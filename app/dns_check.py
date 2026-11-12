"""
Live DNS drift check.

Runs `dig TXT _dmarc.<domain>` for every tracked domain, parses the DMARC tags,
and compares them against whichever is more authoritative:
  - a manual_log policy_history entry (ground truth you logged yourself), if one
    is open-ended for this domain, else
  - the report-derived current policy_history run.

A mismatch against manual_log data is a real signal (DNS doesn't match what you
say you set). A mismatch against report-derived data alone is *ambiguous* -- it's
exactly as likely to mean "reports haven't caught up to a recent change yet" as
"DNS is wrong" -- so that case is worded as such rather than as an alarm.

Also flags: no DMARC TXT record found, more than one found (a spec violation --
receivers may disregard all of them), and DNS lookup failures.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor

from app import dns_lookup
from app.analysis import all_domains, current_policy_run, ensure_default_settings, upsert_system_action
from app.db import get_connection, init_db
from app.public_suffix import registrable_domain

MAX_WORKERS = 10

# Used only to compare *direction* of a live-vs-expected mismatch -- a
# regression (protection got weaker) is a materially more urgent event than
# an advancement reports haven't caught up to yet (which is routine lag,
# already worded calmly below). Local to this module since the comparison
# here is simpler than domain_report.py's own health-scoring use of the
# same concept -- not worth a cross-module dependency for a 3-entry map.
_POLICY_STRENGTH = {"none": 0, "quarantine": 1, "reject": 2}


def dig_txt(domain: str, timeout: float = 6.0):
    """Raw TXT record strings for _dmarc.<domain>, [] if there are none, or
    None if the lookup failed (SERVFAIL/timeout -- never "no record")."""
    return dns_lookup.txt(f"_dmarc.{domain}", timeout)


def parse_dmarc_tags(txt: str) -> dict:
    tags = {}
    for chunk in txt.split(";"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        k, v = chunk.split("=", 1)
        tags[k.strip().lower()] = v.strip()
    return tags


def _record(conn, domain_id, status, txt_value, parsed_p, parsed_pct, matches_expected, note):
    conn.execute(
        """INSERT INTO dns_checks (domain_id, status, txt_value, parsed_p, parsed_pct, matches_expected, note)
           VALUES (?,?,?,?,?,?,?)""",
        (domain_id, status, txt_value, parsed_p, parsed_pct, matches_expected, note),
    )


def _dismiss(conn, domain_id, categories, ref_key=None):
    marks = ",".join("?" * len(categories))
    sql = (f"UPDATE action_items SET status='dismissed', resolved_at=datetime('now') "
           f"WHERE domain_id=? AND category IN ({marks}) AND status='open'")
    args = [domain_id, *categories]
    if ref_key is not None:
        sql += " AND ref_key=?"
        args.append(ref_key)
    conn.execute(sql, args)


def _lookup_failed(conn, domain_id, name):
    """A failed lookup (SERVFAIL, timeout) is "couldn't check", never "no
    record": it's logged on the dashboard's DNS history, raises nothing and
    clears nothing. It used to open a dns_drift item, which subscribers read
    as "your protective setting doesn't match what we expect"."""
    note = f"Couldn't check _dmarc.{name} this time (DNS lookup failed or timed out) -- will retry next run"
    _record(conn, domain_id, "lookup_failed", None, None, None, None, note)
    return {"status": "lookup_failed", "note": note}


def record_problems(tags: dict) -> dict:
    """{problem_key: explanation} for one DMARC record's tags -- the checks a
    receiver applies before it ever uses the policy, plus the two gaps that
    quietly undo an enforced one. Keys double as action-item ref_keys.

    `pct` is deliberately not judged here: RFC 9989 (DMARCbis) removed it --
    receivers apply all or nothing, `t=y` is the testing flag now -- and
    pct_watch.py already measures whether Gmail and Microsoft still honour it."""
    problems = {}
    p = (tags.get("p") or "").lower()
    if p not in _POLICY_STRENGTH:
        problems["invalid_p"] = (
            f"The record's policy is p={tags.get('p')!r}, which isn't one of none / quarantine / reject"
            if "p" in tags else "The record has no p= policy tag at all")
        problems["invalid_p"] += (", so receivers treat it as asking for no protection (p=none, RFC 9989 "
                                  "section 4.7). Forged mail using this domain is not stopped.")
    elif p in ("quarantine", "reject") and (tags.get("sp") or "").lower() == "none":
        problems["sp_none"] = (
            f"p={p} protects the domain itself, but sp=none tells receivers to let forged mail from any "
            f"subdomain (anything.<domain>) through. Remove sp=none, or set it to quarantine or reject.")
    if not tags.get("rua"):
        problems["no_rua"] = ("The record has no rua= reporting address, so no aggregate reports are sent "
                              "for it and Vigil can't see who sends mail as this domain.")
    return problems


def _apply_record_problems(conn, domain_id, domain_name, problems):
    categories = {"invalid_p": "dmarc_record_invalid", "sp_none": "dmarc_subdomains_open", "no_rua": "dmarc_no_rua"}
    titles = {
        "invalid_p": f"{domain_name}: DMARC record has an invalid policy, so it protects nothing",
        "sp_none": f"{domain_name}: DMARC record leaves subdomains unprotected (sp=none)",
        "no_rua": f"{domain_name}: DMARC record has no reporting address (rua=)",
    }
    for key, category in categories.items():
        if key in problems:
            upsert_system_action(conn, domain_id, category, None, titles[key], problems[key])
        else:
            _dismiss(conn, domain_id, [category])


def check_domain(conn, domain_id: int, domain_name: str, records=None) -> dict:
    if records is None:
        records = dig_txt(domain_name)

    if records is None:
        return _lookup_failed(conn, domain_id, domain_name)

    dmarc_records = [r for r in records if r.lower().startswith("v=dmarc1")]
    inherited_from = None

    if not dmarc_records:
        # A subdomain with no record of its own is covered by its organizational
        # domain's record (RFC 9989 section 4.10): sp= if set, else p=. Calling
        # that "no DMARC record" contradicted any public check of the same DNS
        # (mails.catsofkochi.com, 12 Nov 2026: raised as missing while
        # catsofkochi.com's p=quarantine covered it).
        org = registrable_domain(domain_name)
        if org != domain_name:
            parent = dig_txt(org)
            if parent is None:
                return _lookup_failed(conn, domain_id, domain_name)
            parent_records = [r for r in parent if r.lower().startswith("v=dmarc1")]
            if len(parent_records) == 1:
                inherited_from = org
                dmarc_records = parent_records

    if not dmarc_records:
        note = f"No DMARC TXT record found at _dmarc.{domain_name}"
        _record(conn, domain_id, "missing", None, None, None, 0, note)
        upsert_system_action(conn, domain_id, "dns_drift", None, f"{domain_name}: no DMARC record found", note)
        return {"status": "missing", "note": note}

    if len(dmarc_records) > 1:
        txt_value = " | ".join(dmarc_records)
        note = f"{len(dmarc_records)} DMARC TXT records found (spec violation -- receivers may ignore all of them)"
        _record(conn, domain_id, "multiple", txt_value, None, None, 0, note)
        upsert_system_action(conn, domain_id, "dns_drift", None,
                              f"{domain_name}: multiple DMARC TXT records (ambiguous)", note)
        return {"status": "multiple", "note": note, "records": dmarc_records}

    txt_value = dmarc_records[0]
    tags = parse_dmarc_tags(txt_value)
    if inherited_from:
        # What a receiver applies to this subdomain: the parent's sp=, else p=.
        # Reporting-address and syntax problems belong to the parent's own
        # check, so they aren't raised a second time here.
        tags = {**tags, "p": tags.get("sp") or tags.get("p")}
        problems = {k: v for k, v in record_problems(tags).items() if k == "invalid_p"}
    else:
        problems = record_problems(tags)
    _apply_record_problems(conn, domain_id, domain_name, problems)
    if "invalid_p" in problems:
        note = problems["invalid_p"]
        _record(conn, domain_id, "invalid", txt_value, tags.get("p"), None, 0, note)
        return {"status": "invalid", "note": note}

    parsed_p = tags.get("p").lower()
    parsed_pct = int(tags["pct"]) if tags.get("pct", "").isdigit() else 100  # RFC 7489 default

    manual_run = conn.execute(
        """SELECT * FROM policy_history WHERE domain_id=? AND source='manual_log' AND observed_to IS NULL
           ORDER BY observed_from DESC LIMIT 1""",
        (domain_id,),
    ).fetchone()
    report_run = current_policy_run(conn, domain_id)

    expected = manual_run or report_run
    expected_source = "manual_log" if manual_run else ("report" if report_run else None)

    matches = None
    weakened = False
    note = "matches expected policy"
    if expected is not None:
        expected_pct = 100 if expected["pct"] is None else expected["pct"]  # RFC 7489 default
        matches = (expected["p"] == parsed_p and expected_pct == parsed_pct)
        if not matches:
            # Direction matters more than the mismatch itself: reports lag behind
            # a recent *improvement* by design (routine, already worded calmly),
            # but a live policy that's *weaker* than the last known-good state
            # means protection just dropped -- worth flagging as its own, more
            # urgent thing regardless of whether the baseline is report-derived
            # or manually logged, since a weakening is exactly the kind of event
            # reports haven't had time to reflect yet either way.
            live_strength = (_POLICY_STRENGTH.get(parsed_p, -1), parsed_pct)
            expected_strength = (_POLICY_STRENGTH.get(expected["p"], -1), expected_pct)
            weakened = live_strength < expected_strength
            note = (f"live DNS shows p={parsed_p}/pct={parsed_pct}, {expected_source} data shows "
                    f"p={expected['p']}/pct={expected_pct}")
            if weakened:
                note += (". This looks like your protection just got weaker, not report lag -- worth "
                         "confirming this was an intentional change.")
            elif expected_source == "report":
                note += " -- if you changed DNS recently this may just be report lag, not a real problem"
    else:
        note = "no prior report or manual-log data to compare against yet"
    if inherited_from:
        note = f"no record of its own; covered by {inherited_from}'s record (p={parsed_p} applies here). {note}"

    _record(conn, domain_id, "inherited" if inherited_from else "ok", txt_value, parsed_p, parsed_pct,
            (1 if matches else 0) if matches is not None else None, note)

    if matches is False and weakened:
        upsert_system_action(conn, domain_id, "dns_policy_weakened", None,
                              f"{domain_name}: DMARC protection appears to have weakened", note)
        conn.execute(
            """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
               WHERE domain_id=? AND category='dns_drift' AND status='open'""",
            (domain_id,),
        )
    elif matches is False:
        upsert_system_action(conn, domain_id, "dns_drift", None,
                              f"{domain_name}: live DNS disagrees with {expected_source} data", note)
        conn.execute(
            """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
               WHERE domain_id=? AND category='dns_policy_weakened' AND status='open'""",
            (domain_id,),
        )
    else:
        conn.execute(
            """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
               WHERE domain_id=? AND category IN ('dns_drift', 'dns_policy_weakened') AND status='open'""",
            (domain_id,),
        )

    return {"status": "inherited" if inherited_from else "ok", "txt_value": txt_value, "parsed_p": parsed_p,
            "parsed_pct": parsed_pct, "matches": matches, "weakened": weakened,
            "expected_source": expected_source, "note": note, "problems": problems}


def run_dns_checks(conn, verbose: bool = True) -> None:
    ensure_default_settings(conn)
    domains = all_domains(conn)
    names = [d["name"] for d in domains]

    # dig_txt is pure network I/O with no DB access, so it's safe to fan out --
    # the actual DB reads/writes in check_domain() below stay sequential on
    # this one connection.
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(names) or 1)) as ex:
        records_by_name = dict(zip(names, ex.map(dig_txt, names))) if names else {}

    for domain in domains:
        result = check_domain(conn, domain["id"], domain["name"], records=records_by_name[domain["name"]])
        conn.commit()
        if verbose:
            print(f"=== {domain['name']} ===")
            if result["status"] in ("ok", "inherited"):
                flag = "OK" if result["matches"] else ("MISMATCH" if result["matches"] is False else "no baseline yet")
                print(f"  live: p={result['parsed_p']} pct={result['parsed_pct']}  [{flag}]")
                if result["matches"] is not True:
                    print(f"  {result['note']}")
            else:
                print(f"  {result['status']}: {result['note']}")


# Common prefixes for a dedicated bulk/transactional sending subdomain --
# trimmed to the ones actually used across these domains (confirmed with the
# user) rather than a generic guess-every-prefix list.
SENDING_SUBDOMAIN_PREFIXES = ("mail", "mails", "news", "campaigns", "updates", "newsletter")


def discover_untracked_subdomains(conn, verbose: bool = True) -> None:
    """For every tracked domain, checks whether a handful of common
    sending-subdomain prefixes (mail.<domain>, bulk.<domain>, etc.) have their
    own DMARC record that isn't itself tracked as a separate domain here.

    This closes a real blind spot: a subdomain's DMARC record and reports are
    completely independent of its parent's, and DMARCTool only ever tracks
    domains explicitly present in the `domains` table (which only ever grows
    via ingesting a report) -- so a sending subdomain nobody told DMARCTool
    about stays invisible indefinitely, even if it has its own DMARC policy
    with real sending traffic. It surfaced here once, via a raw email header
    someone pasted in by hand; this check means that shouldn't need to
    happen again.
    """
    tracked = {row["name"] for row in conn.execute("SELECT name FROM domains")}
    domains = all_domains(conn)

    # A subdomain we ALREADY monitor as a sending source -- a matched Mailgun
    # sending domain, or an SES configuration set (dots->hyphens, the same
    # naming convention ses_events.py uses) -- is not an unknown blind spot.
    # This check exists for subdomains "nobody told DMARCTool about"; flagging
    # one the operator deliberately set up and that the tool is already
    # tracking the sending for contradicts its own premise ("zero visibility
    # until now") and just reads as noise. Only genuinely-unseen subdomains
    # with their own DMARC record are worth surfacing.
    mailgun_senders = {row["mailgun_domain"] for row in conn.execute("SELECT DISTINCT mailgun_domain FROM mailgun_stats")}
    ses_config_sets = {row["configuration_set"] for row in conn.execute("SELECT DISTINCT configuration_set FROM ses_event_counts")}
    # SES configuration sets are commonly named after the APEX domain
    # (aikyamjobs.org -> "aikyamjobs-org"), even though the mail is sent from a
    # subdomain (mails.aikyamjobs.org). So an exact subdomain->config-set match
    # misses it. If a domain already has SES sending data, we ARE monitoring its
    # SES bounces/complaints/deliveries -- so its sending subdomains aren't a
    # blind spot either. (Safe: a _dmarc record under this domain can only be
    # published by whoever controls the domain's DNS, i.e. the operator, so a
    # sending-prefix subdomain here is theirs, not an attacker's.)
    ses_domain_ids = {row["domain_id"] for row in conn.execute("SELECT DISTINCT domain_id FROM ses_event_counts")}

    def _covered(name, parent_id):
        return (name in mailgun_senders
                or name.replace(".", "-") in ses_config_sets
                or parent_id in ses_domain_ids)

    # Clear any previously-flagged subdomain that's since been added as its own
    # tracked domain, or that we've now recognised as a known sending source --
    # either way the gap this check exists for is closed.
    for item in conn.execute(
        "SELECT id, ref_key, domain_id FROM action_items WHERE category='untracked_sending_subdomain' AND status='open'"
    ).fetchall():
        if item["ref_key"] in tracked or _covered(item["ref_key"], item["domain_id"]):
            conn.execute(
                "UPDATE action_items SET status='dismissed', resolved_at=datetime('now') WHERE id=?",
                (item["id"],),
            )

    # Once the operator has seen and acknowledged (done/dismissed) a candidate,
    # don't raise it again just because it's still neither a tracked domain nor
    # a covered sender -- those are the ONLY two conditions the dismiss loop
    # above clears on, so without this, marking an item done only clears that
    # one row: upsert_system_action's own dedup only matches an OPEN item, so
    # the very next run (still "untracked", still "uncovered") inserts a brand
    # new one for the identical subdomain. Confirmed live: the same subdomain
    # got raised, marked done, and re-raised again the same day, twice, on two
    # real domains (aikyamjobs.org, arpo.in) -- exactly this loop.
    already_acknowledged = {
        (row["domain_id"], row["ref_key"])
        for row in conn.execute(
            "SELECT domain_id, ref_key FROM action_items "
            "WHERE category='untracked_sending_subdomain' AND status IN ('done', 'dismissed')"
        )
    }
    candidates = [
        (domain["id"], domain["name"], f"{prefix}.{domain['name']}")
        for domain in domains
        for prefix in SENDING_SUBDOMAIN_PREFIXES
        if f"{prefix}.{domain['name']}" not in tracked
        and not _covered(f"{prefix}.{domain['name']}", domain["id"])
        and (domain["id"], f"{prefix}.{domain['name']}") not in already_acknowledged
    ]
    if not candidates:
        conn.commit()
        if verbose:
            print("[dns] no candidate sending subdomains to check (all already tracked)")
        return

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        results = list(ex.map(lambda c: dig_txt(c[2]), candidates))

    found = 0
    for (domain_id, domain_name, candidate), records in zip(candidates, results):
        if not records:
            continue
        dmarc_records = [r for r in records if r.lower().startswith("v=dmarc1")]
        if not dmarc_records:
            continue
        found += 1
        tags = parse_dmarc_tags(dmarc_records[0])
        rua_note = ("" if "rua" in tags else
                    " It has no rua= reporting address, so no aggregate reports will ever be generated for "
                    "it until one is added.")
        upsert_system_action(
            conn, domain_id, "untracked_sending_subdomain", candidate,
            f"{domain_name}: found an untracked sending subdomain ({candidate})",
            f"_dmarc.{candidate} publishes its own DMARC record ({dmarc_records[0]}), separate from "
            f"{domain_name}'s own record.{rua_note} If this subdomain is used for sending, DMARCTool can "
            f"track it like any other domain once reports start arriving for it.",
        )
    conn.commit()
    if verbose:
        print(f"[dns] checked {len(candidates)} candidate sending subdomain(s) across {len(domains)} "
              f"tracked domain(s), found {found} untracked one(s) with their own DMARC record")


def main() -> None:
    parser = argparse.ArgumentParser(description="Check live DMARC DNS TXT records against report/manual-log data")
    parser.parse_args()
    conn = get_connection()
    init_db(conn)
    run_dns_checks(conn)
    discover_untracked_subdomains(conn)


if __name__ == "__main__":
    main()
