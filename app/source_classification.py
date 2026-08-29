"""
What kind of thing is this sending source?

DMARC's own answer to "did this pass" is one bit, which makes every failing
source look alike -- a mailing list that rewrote a message body sits in the
same list as someone forging your domain outright. The commercial tools all
split this out (EasyDMARC labels sources Compliant / Non-compliant / Threat /
Forwarded); we had the data to do it and weren't using it.

The data is `record_auth_results`: alongside the *aligned* SPF/DKIM verdicts in
`report_records`, every report also carries the raw authentication results,
including **which domain** each signature was valid for. That last part is what
distinguishes the cases.

Four kinds, from that evidence:

  aligned      -- passing DMARC. Nothing to look at.
  forwarded    -- some of this source's mail carries a valid signature for YOUR
                  domain, but not all of it passes. That is the signature of a
                  relay or mailing list: the mail started as yours, and a hop
                  altered it enough to break the signature on the way. Benign,
                  and not something you can fix from your end.
  third_party  -- consistently carries a valid signature for a DIFFERENT
                  domain. A real, fixable configuration problem: a shared ESP
                  account verified under one domain but sending with another
                  domain in the From address. Alignment can never pass as-is.
  unverified   -- failing, with no valid signature for anyone, ever. The only
                  bucket where "someone may be forging you" is on the table.

Deliberately hedged in the labels ("looks like", "probably"): these are
inferences from counts, not facts a report states outright. A source can also
change kind as new reports arrive, which is correct -- it is a description of
recent evidence, not a permanent verdict.

Read-only and computed on demand from data already ingested. No DNS, no
network, no new tables.
"""

import datetime

# A source needs a bit of history before its shape means anything -- one
# failing message proves nothing either way.
MIN_MSGS_TO_CLASSIFY = 3

# At or above this aligned-pass rate a source isn't worth flagging at all,
# whatever its mix looks like. Matches the threshold verdicts.senders_verdict
# already uses, so the two can't disagree about who has a problem.
HEALTHY_PASS_RATE = 0.95

KIND_LABELS = {
    "aligned": "Passing",
    "forwarded": "Forwarded mail",
    "third_party": "Third-party sender",
    "unverified": "Unverified",
    "unknown": "Not enough data",
}

KIND_HELP = {
    "aligned": "This source's mail passes authentication. Nothing to do.",
    "forwarded": (
        "Some of this source's mail carries a valid signature for your own domain, but not all of it "
        "passes. That is what forwarding looks like: the message started out as yours, and something "
        "on the way -- a mailing list, an auto-forward rule, a spam filter that rewrites mail -- "
        "changed it enough to break the signature. It is not someone pretending to be you, and there "
        "is nothing you can fix at your end. Worth knowing so it doesn't get mistaken for an attack."
    ),
    "third_party": (
        "This source consistently authenticates as a different domain than the one in the From "
        "address. That usually means a shared sending account (Mailgun, SES and similar) that was "
        "verified under one domain but is being used to send as another. DMARC can never pass for "
        "this mail as it stands, and it will get worse as you tighten enforcement -- so this is a "
        "real configuration problem, and a fixable one."
    ),
    "unverified": (
        "This source's mail fails authentication and carries no valid signature for anyone -- not "
        "your domain, not a third party. This is the only category where forgery is a real "
        "possibility, so it is the one worth actually investigating."
    ),
    "unknown": "Too few messages from this source so far to say anything useful about it.",
}


def _is_own(candidate: str, domain_name: str) -> bool:
    """Whether a signature domain counts as this domain's own -- the domain
    itself, or a subdomain of it (mails.arpo.in signing for arpo.in is still
    arpo.in's own mail)."""
    c = (candidate or "").strip().lower().rstrip(".")
    own = domain_name.strip().lower().rstrip(".")
    return bool(c) and (c == own or c.endswith("." + own) or own.endswith("." + c))


def classify_sources(conn, domain_id: int, domain_name: str,
                     start_epoch: int = None, end_epoch: int = None) -> dict:
    """{source_ip: {"kind", "label", "help", "total", "passed", "pass_rate",
    "signed_by": [domains]}} for every source seen in the window."""
    where = ["r.domain_id = ?"]
    params = [domain_id]
    if start_epoch is not None:
        where.append("r.date_end >= ?")
        params.append(start_epoch)
    if end_epoch is not None:
        where.append("r.date_begin <= ?")
        params.append(end_epoch)
    clause = " AND ".join(where)

    totals = conn.execute(
        f"""SELECT rr.source_ip,
                   SUM(rr.count) AS total,
                   SUM(CASE WHEN rr.dkim_result='pass' OR rr.spf_result='pass' THEN rr.count ELSE 0 END) AS passed
            FROM report_records rr JOIN reports r ON r.id = rr.report_id
            WHERE {clause}
            GROUP BY rr.source_ip""",
        params,
    ).fetchall()

    # Every domain this source has ever produced a *valid* signature for, in
    # this window. One query for the whole domain rather than one per source.
    signed = {}
    for row in conn.execute(
        f"""SELECT rr.source_ip, ar.domain
            FROM report_records rr
            JOIN reports r ON r.id = rr.report_id
            JOIN record_auth_results ar ON ar.record_id = rr.id
            WHERE {clause} AND ar.result = 'pass' AND ar.domain IS NOT NULL
            GROUP BY rr.source_ip, ar.domain""",
        params,
    ):
        signed.setdefault(row["source_ip"], set()).add(row["domain"].strip().lower())

    # An ESP's own signing domain (mailgun.org, amazonses.com) rides along on
    # every message it sends, so it appears against many unrelated domains --
    # whereas "aikyamsolve.org" appears against one or two. Ranking a
    # signature domain by how many *different* tracked domains it signs for
    # therefore separates provider boilerplate from the specific identity the
    # mail is actually borrowing, with no hardcoded list of providers to go
    # stale. Lower breadth = more informative, so it sorts first.
    breadth = {
        r["domain"].strip().lower(): r["n"]
        for r in conn.execute(
            """SELECT LOWER(TRIM(ar.domain)) AS domain, COUNT(DISTINCT r.domain_id) AS n
               FROM record_auth_results ar
               JOIN report_records rr ON rr.id = ar.record_id
               JOIN reports r ON r.id = rr.report_id
               WHERE ar.result='pass' AND ar.domain IS NOT NULL
               GROUP BY LOWER(TRIM(ar.domain))"""
        )
    }

    def _informative(names):
        return sorted(names, key=lambda d: (breadth.get(d, 1), d))

    out = {}
    for row in totals:
        ip, total, passed = row["source_ip"], row["total"] or 0, row["passed"] or 0
        rate = (passed / total) if total else None
        domains = signed.get(ip, set())
        # Signatures for the ESP's own domain (amazonses.com, mailgun.org) are
        # noise here: every message through a provider carries one, so they
        # say nothing about whose mail this is. Only the alignment-relevant
        # split -- ours vs. somebody else's -- matters.
        own = {d for d in domains if _is_own(d, domain_name)}
        others = domains - own

        if total < MIN_MSGS_TO_CLASSIFY:
            kind = "unknown"
        elif rate is not None and rate >= HEALTHY_PASS_RATE:
            kind = "aligned"
        elif own:
            # Carries our own valid signature at least sometimes -> the mail is
            # genuinely ours and something downstream is breaking it.
            kind = "forwarded"
        elif others:
            kind = "third_party"
        else:
            kind = "unverified"

        out[ip] = {
            "kind": kind,
            "label": KIND_LABELS[kind],
            "help": KIND_HELP[kind],
            "total": total,
            "passed": passed,
            "pass_rate": rate,
            "signed_by": _informative(others)[:3] if kind == "third_party" else _informative(own)[:3],
        }
    return out


def caught_impersonation(conn, domain_id: int, domain_name: str,
                          start_epoch: int, end_epoch: int, example_limit: int = 3) -> dict:
    """The 'someone tried to impersonate you and it didn't work' story, told
    as the good news it is rather than a chore.

    A caught attempt is a report record whose From address is this domain (or a
    subdomain of it), that failed authentication and carries NO valid signature
    for anyone -- the 'unverified' shape above, the only bucket where forgery
    is actually on the table. We additionally require that neither the sending
    IP nor the From identity EVER appears in a passing record for this domain,
    so one of your real senders having an occasional bad day is never
    mislabelled an attacker. (This is what a spoofer looks like: a made-up
    subdomain like munh.arpo.in, one message, from an IP that never sends your
    real mail.)

    Split honestly by what DMARC actually did, because the wording depends on
    it and we must never claim mail was blocked when the policy didn't act:
      blocked  -- disposition quarantine/reject: the receiver stopped it.
      detected -- disposition none: seen and reported to us, but not stopped,
                  because enforcement isn't at full strength yet.

    `country`/`org` on each example are read from the WHOIS cache only (never a
    live lookup -- this runs on page render); a separate background pass fills
    that cache in. Returns None when there were no caught attempts in the
    window, so the section simply doesn't render."""
    # cached_whois_org/country are read-only here (allow_live=False); imported
    # locally to avoid a heavier import at module load, matching the codebase's
    # circular-import-avoidance style elsewhere.
    from app.analysis import cached_whois_org, cached_whois_country

    rows = conn.execute(
        """WITH passing_ips AS (
             SELECT DISTINCT rr.source_ip FROM report_records rr
               JOIN reports r ON r.id = rr.report_id
              WHERE r.domain_id = ? AND (rr.dkim_result='pass' OR rr.spf_result='pass')
           ), passing_from AS (
             SELECT DISTINCT rr.header_from FROM report_records rr
               JOIN reports r ON r.id = rr.report_id
              WHERE r.domain_id = ? AND (rr.dkim_result='pass' OR rr.spf_result='pass')
           )
           SELECT rr.source_ip, rr.header_from,
                  SUM(CASE WHEN rr.disposition IN ('quarantine','reject') THEN rr.count ELSE 0 END) AS blocked,
                  SUM(CASE WHEN rr.disposition NOT IN ('quarantine','reject') THEN rr.count ELSE 0 END) AS detected,
                  SUM(rr.count) AS vol, MAX(r.date_end) AS last_ts
             FROM report_records rr
             JOIN reports r ON r.id = rr.report_id
            WHERE r.domain_id = ? AND r.date_end >= ? AND r.date_begin <= ?
              AND rr.header_from IS NOT NULL
              AND (rr.header_from = ? OR rr.header_from LIKE '%.' || ?)
              AND rr.dkim_result != 'pass' AND (rr.spf_result IS NULL OR rr.spf_result != 'pass')
              AND NOT EXISTS (SELECT 1 FROM record_auth_results ar
                                WHERE ar.record_id = rr.id AND ar.result = 'pass')
              AND rr.source_ip NOT IN (SELECT source_ip FROM passing_ips)
              AND rr.header_from NOT IN (SELECT header_from FROM passing_from)
            GROUP BY rr.source_ip, rr.header_from
            ORDER BY last_ts DESC""",
        (domain_id, domain_id, domain_id, start_epoch, end_epoch, domain_name, domain_name),
    ).fetchall()

    if not rows:
        return None

    attempts = len(rows)
    blocked = sum(r["blocked"] for r in rows)
    detected = sum(r["detected"] for r in rows)
    examples = []
    for r in rows[:example_limit]:
        date = datetime.datetime.utcfromtimestamp(r["last_ts"]).date().isoformat() if r["last_ts"] else None
        examples.append({
            "identity": r["header_from"],
            "date": date,
            "country": cached_whois_country(conn, r["source_ip"]),
            "org": cached_whois_org(conn, r["source_ip"], allow_live=False),
        })
    return {
        "attempts": attempts,
        "blocked": blocked,
        "detected": detected,
        "all_blocked": detected == 0 and blocked > 0,
        "examples": examples,
    }


def impersonation_source_ips(conn, domain_id: int, start_epoch: int, end_epoch: int) -> list:
    """The distinct sending IPs behind caught-impersonation records in a window
    -- the input the background WHOIS-enrichment pass uses to fill in each
    example's country/org, so caught_impersonation() (which never does a live
    lookup) can show them. Same detection predicate as caught_impersonation()."""
    domain = conn.execute("SELECT name FROM domains WHERE id=?", (domain_id,)).fetchone()
    if not domain:
        return []
    name = domain["name"]
    return [r["source_ip"] for r in conn.execute(
        """SELECT DISTINCT rr.source_ip FROM report_records rr
             JOIN reports r ON r.id = rr.report_id
            WHERE r.domain_id = ? AND r.date_end >= ? AND r.date_begin <= ?
              AND rr.header_from IS NOT NULL
              AND (rr.header_from = ? OR rr.header_from LIKE '%.' || ?)
              AND rr.dkim_result != 'pass' AND (rr.spf_result IS NULL OR rr.spf_result != 'pass')
              AND NOT EXISTS (SELECT 1 FROM record_auth_results ar
                                WHERE ar.record_id = rr.id AND ar.result = 'pass')
              AND rr.source_ip NOT IN (
                    SELECT DISTINCT rr2.source_ip FROM report_records rr2 JOIN reports r2 ON r2.id=rr2.report_id
                     WHERE r2.domain_id=? AND (rr2.dkim_result='pass' OR rr2.spf_result='pass'))
              AND rr.header_from NOT IN (
                    SELECT DISTINCT rr3.header_from FROM report_records rr3 JOIN reports r3 ON r3.id=rr3.report_id
                     WHERE r3.domain_id=? AND (rr3.dkim_result='pass' OR rr3.spf_result='pass'))""",
        (domain_id, start_epoch, end_epoch, name, name, domain_id, domain_id),
    ).fetchall()]


def enrich_impersonation_whois(conn, lookback_days: int = 180, max_lookups: int = 20, verbose: bool = True) -> None:
    """Background pass: make sure every recent caught-impersonation source IP
    has a WHOIS org/country cached, so caught_impersonation() (which never does
    a live lookup on a page render) can show it. Bounded per run because the
    whois CLI is slow and rate-limited -- an uncached IP just shows no country
    until a later run reaches it, which is fine. Same "background-job only"
    discipline as guess_sender_identity's live lookups."""
    import datetime as _dt
    from app.analysis import _whois_lookup  # (org, country) from the whois CLI

    end = int(_dt.datetime.utcnow().timestamp())
    start = end - lookback_days * 86400
    wanted = []
    seen = set()
    for d in conn.execute("SELECT id FROM domains").fetchall():
        for ip in impersonation_source_ips(conn, d["id"], start, end):
            if ip not in seen:
                seen.add(ip)
                wanted.append(ip)

    # Only look up IPs with no country cached yet -- either never checked, or
    # cached back when the org column existed but country didn't. Written
    # directly (not via cached_whois_org, which short-circuits on a fresh org
    # row and would never backfill the missing country). Capped per run since
    # the whois CLI is slow/rate-limited.
    to_do = [
        ip for ip in wanted
        if (conn.execute("SELECT country FROM ip_whois_cache WHERE source_ip=?", (ip,)).fetchone() or {"country": None})["country"] is None
    ]
    done = 0
    for ip in to_do[:max_lookups]:
        org, country = _whois_lookup(ip)
        conn.execute(
            """INSERT INTO ip_whois_cache (source_ip, org, country, checked_at) VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT(source_ip) DO UPDATE SET org=excluded.org, country=excluded.country, checked_at=excluded.checked_at""",
            (ip, org, country),
        )
        done += 1
    conn.commit()
    if verbose:
        print(f"[impersonation_whois] {done} IP(s) enriched, {len(to_do) - done} still pending")


def classification_summary(classifications: dict) -> dict:
    """Counts per kind, plus how many sources actually warrant attention --
    which is the whole point: 'forwarded' is not a problem, and lumping it in
    with the rest is what made the sender list unreadable."""
    counts = {}
    for info in classifications.values():
        counts[info["kind"]] = counts.get(info["kind"], 0) + 1
    return {
        "counts": counts,
        "needs_attention": counts.get("third_party", 0) + counts.get("unverified", 0),
        "benign_failing": counts.get("forwarded", 0),
        "total": len(classifications),
    }
