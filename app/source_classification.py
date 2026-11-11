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

Six kinds, from that evidence:

  aligned      -- passing DMARC. Nothing to look at.
  forwarded    -- some of this source's mail carries a valid signature for YOUR
                  domain, but not all of it passes. That is the signature of a
                  relay or mailing list: the mail started as yours, and a hop
                  altered it enough to break the signature on the way. Benign,
                  and not something you can fix from your end.
  altered_own  -- failing, no valid signature of ours, but a BROKEN one made
                  with our real key (a selector that passes on our normal
                  mail), and the provider's own signature broken too: signed
                  by us, changed afterwards, typically by a recipient's
                  email-security filter. See altered_own_evidence.
  own_key_failing -- the provider's signature passed but ours failed: the
                  message arrived unchanged, so our DKIM key/DNS record is
                  what's broken. Needs fixing. See own_key_failing_evidence.
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
import ipaddress

# A source needs a bit of history before its shape means anything -- one
# failing message proves nothing either way.
MIN_MSGS_TO_CLASSIFY = 3

# At or above this aligned-pass rate a source isn't worth flagging at all,
# whatever its mix looks like. Matches the threshold verdicts.senders_verdict
# already uses, so the two can't disagree about who has a problem.
HEALTHY_PASS_RATE = 0.95

# Networks Gmail and Outlook.com/Microsoft 365 auto-forward from (Gmail's
# forwarding hosts are mail-sor-*.google.com, e.g. 209.85.220.69). Only used to
# name a tiny, all-passing source as forwarding -- never to excuse a failing one.
FORWARDER_NETWORKS = [ipaddress.ip_network(n) for n in (
    "209.85.128.0/17", "74.125.0.0/16", "64.233.160.0/19", "66.102.0.0/20", "108.177.0.0/17",
    "172.217.0.0/16", "173.194.0.0/16", "2607:f8b0::/32", "2a00:1450::/32", "2404:6800::/32",
    "40.92.0.0/15", "40.107.0.0/16", "52.100.0.0/14", "104.47.0.0/17", "2a01:111:f400::/48",
)]


def _in_forwarder_network(ip):
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in FORWARDER_NETWORKS if net.version == addr.version)


KIND_LABELS = {
    "aligned": "Passing",
    "forwarded_ok": "Forwarded by a recipient",
    "forwarded": "Forwarded mail",
    "altered_own": "Your mail, changed on the way",
    "own_key_failing": "Your signature is failing",
    "third_party": "Third-party sender",
    "unverified": "Unverified",
    "unknown": "Not enough data",
}

KIND_HELP = {
    "aligned": "This source's mail passes authentication. Nothing to do.",
    # Jev-tested 2026-11-10: plainer than the first draft (68% vs 81% too-technical), same calibration.
    "forwarded_ok": (
        "Someone who received your email has their Gmail or Outlook set to forward it to another inbox. "
        "Forwarding makes one check (SPF) fail, but your email's signature (DKIM) came through intact, so it "
        "still passed and was delivered normally. Nothing to do."
    ),
    "forwarded": (
        "Some of this source's mail carries a valid signature for your own domain, but not all of it "
        "passes. That is what forwarding looks like: the message started out as yours, and something "
        "on the way -- a mailing list, an auto-forward rule, a spam filter that rewrites mail -- "
        "changed it enough to break the signature. It is not someone pretending to be you, and there "
        "is nothing you can fix at your end. Worth knowing so it doesn't get mistaken for an attack."
    ),
    "altered_own": (
        "This mail carries your own signature, made with the same key your normal mail passes with, "
        "but it arrived broken. That happens when something on the recipient's side (an email-security "
        "filter, a forwarder, a mailing list) changes your message and then hands it on from its own "
        "server. It is your mail, not someone pretending to be you, and there is nothing to fix in your "
        "own settings. Open the source page to see who it was going to and whether they got it."
    ),
    "own_key_failing": (
        "This is your own email provider sending your mail, and the provider's own signature on it checks out, so "
        "nothing changed the messages on the way. Your domain's signature failed anyway, which means the key it "
        "was signed with doesn't match what's published in your DNS, usually because the DKIM record was deleted or "
        "changed. Mail like this can be sent to spam. Open the source page for the exact record to check."
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

    # Messages that passed DMARC on our own DKIM signature while SPF *for our
    # own domain* failed -- the envelope was ours, the server wasn't. From a
    # mailbox provider's network that's auto-forwarding (thudippu.in,
    # 2026-11-10: 209.85.220.69 = mail-sor-f69.google.com, 2 msgs, DKIM pass,
    # SPF softfail). Own-domain SPF only: an ESP that just isn't using a
    # custom bounce domain fails SPF for amazonses.com etc., not for us.
    forwarded_msgs = {
        r["source_ip"]: r["n"] for r in conn.execute(
            f"""SELECT rr.source_ip, SUM(rr.count) AS n
                FROM report_records rr JOIN reports r ON r.id = rr.report_id
                WHERE {clause} AND rr.dkim_result = 'pass'
                  AND EXISTS (SELECT 1 FROM record_auth_results ar
                              WHERE ar.record_id = rr.id AND ar.mechanism = 'spf'
                                AND ar.result IN ('fail', 'softfail')
                                AND (lower(ar.domain) = lower(?) OR lower(ar.domain) LIKE '%.' || lower(?)))
                GROUP BY rr.source_ip""",
            params + [domain_name, domain_name],
        ).fetchall()
    }

    # Failing messages that still carry OUR signature, made with a key that
    # passes on our normal mail, but broken -- the message was signed by us and
    # changed afterwards (aikyamjobs.org, 2026-11-11: 54.227.64.76 =
    # nat.perception-point.io, a recipient's security filter handing our SES
    # mail back to their Microsoft 365 with the signature broken).
    from app.analysis import _ESP_DEFAULT_AUTH_DOMAINS
    esp = ",".join(f"'{d}'" for d in sorted(_ESP_DEFAULT_AUTH_DOMAINS))  # fixed constants, not user input
    altered_msgs = {
        r["source_ip"]: r["n"] for r in conn.execute(
            f"""WITH proven AS (
                   SELECT DISTINCT lower(ar.domain) AS d, lower(ar.selector) AS s
                   FROM record_auth_results ar JOIN report_records rr ON rr.id = ar.record_id
                   JOIN reports r ON r.id = rr.report_id
                   WHERE r.domain_id = ? AND ar.mechanism = 'dkim' AND ar.result = 'pass' AND ar.selector IS NOT NULL)
                SELECT rr.source_ip, SUM(rr.count) AS n
                FROM report_records rr JOIN reports r ON r.id = rr.report_id
                WHERE {clause} AND rr.dkim_result != 'pass' AND COALESCE(rr.spf_result, '') != 'pass'
                  -- the provider's own signature (mailgun.org, amazonses.com),
                  -- added at sending alongside ours, broke too: the message
                  -- changed after sending. If it still passed, the message
                  -- arrived intact and it's OUR key that's failing -- see
                  -- own_key_failing (aikyam.school 69.72.42.2). A signature a
                  -- forwarder adds afterwards (x.onmicrosoft.com) proves nothing.
                  AND NOT EXISTS (SELECT 1 FROM record_auth_results ok
                                  WHERE ok.record_id = rr.id AND ok.mechanism = 'dkim' AND ok.result = 'pass'
                                    AND lower(ok.domain) IN ({esp}))
                  AND EXISTS (SELECT 1 FROM record_auth_results ar JOIN proven p
                                ON p.d = lower(ar.domain) AND p.s = lower(ar.selector)
                              WHERE ar.record_id = rr.id AND ar.mechanism = 'dkim' AND ar.result = 'fail'
                                AND (lower(ar.domain) = lower(?) OR lower(ar.domain) LIKE '%.' || lower(?)))
                GROUP BY rr.source_ip""",
            [domain_id] + params + [domain_name, domain_name],
        ).fetchall()
    }

    # Failing messages where the provider's own signature PASSED but ours
    # FAILED: they reached the receiver unchanged, from our own provider, so
    # our key is what's broken -- typically its DNS record deleted or changed
    # (news.aikyam.school, 8-11 Oct 2026: mailo._domainkey gone, 202 failed).
    key_failing_msgs = {
        r["source_ip"]: r["n"] for r in conn.execute(
            f"""SELECT rr.source_ip, SUM(rr.count) AS n
                FROM report_records rr JOIN reports r ON r.id = rr.report_id
                WHERE {clause} AND rr.dkim_result != 'pass' AND COALESCE(rr.spf_result, '') != 'pass'
                  AND EXISTS (SELECT 1 FROM record_auth_results ok WHERE ok.record_id = rr.id
                              AND ok.mechanism = 'dkim' AND ok.result = 'pass' AND lower(ok.domain) IN ({esp}))
                  AND EXISTS (SELECT 1 FROM record_auth_results ar WHERE ar.record_id = rr.id
                              AND ar.mechanism = 'dkim' AND ar.result IN ('fail', 'permerror')
                              AND (lower(ar.domain) = lower(?) OR lower(ar.domain) LIKE '%.' || lower(?)))
                GROUP BY rr.source_ip""",
            params + [domain_name, domain_name],
        ).fetchall()
    }

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

        if (total < MIN_MSGS_TO_CLASSIFY and total and forwarded_msgs.get(ip, 0) == total
                and _in_forwarder_network(ip)):
            # Too few to judge a pattern, but every one of them has the exact
            # shape of a recipient's auto-forward -- say so instead of a bare
            # "Not enough data", which left the operator wondering whether a
            # Google IP on a domain without Google Workspace was an attack.
            kind = "forwarded_ok"
        elif total < MIN_MSGS_TO_CLASSIFY:
            kind = "unknown"
        elif rate is not None and rate >= HEALTHY_PASS_RATE:
            kind = "aligned"
        elif key_failing_msgs.get(ip, 0) >= 0.5 * (total - passed) > 0:
            # Ahead of the "changed on the way" kinds: this one needs fixing.
            kind = "own_key_failing"
        elif altered_msgs.get(ip, 0) >= 0.8 * (total - passed) > 0:
            # Ahead of "forwarded": a security filter that lets 2 of 142
            # through intact (35.174.145.124, Avanan) is still the story of
            # the other 140, and this kind can say who they were for.
            kind = "altered_own"
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


def low_volume_failing_rollup(conn, domain_id: int, domain_name: str, start_epoch: int, end_epoch: int,
                               min_volume: int) -> list:
    """Groups this domain's one-off/low-volume FAILING senders (below
    `min_volume` -- the same floor eligible_known_senders() uses to decide
    what's worth a DNSBL/WHOIS lookup) by shared sending infrastructure
    instead of listing them as individually-meaningless rows.

    Built from a real case: catsofkochi.com had 8 failing senders this period,
    each sent exactly once, each individually reporting "not enough data" --
    useless on their own. Reverse-DNS showed 6 of them were actually the same
    thing: Google Cloud generic compute (a teammate's Better Auth integration,
    never added to SPF). Jev-tested this rollup framing at 96%
    clear_and_actionable vs. 46% for a raw per-IP PTR line -- the grouping is
    what makes it a finding instead of noise.

    Returns a list of {"pattern", "kind", "count", "example_ips", "all_spf_fail",
    "all_dkim_fail"} sorted by count descending. `kind` is "cloud_compute" |
    "esp" | "ptr_other" | "no_ptr" -- cloud_compute is the one most worth a
    second look (a known ESP match is usually benign; a PTR match with no
    recognized pattern or no PTR at all are grouped too, so nothing silently
    disappears, but they carry a softer framing)."""
    from app.analysis import _guess_cloud_host, _guess_provider

    rows = conn.execute(
        """SELECT rr.source_ip, SUM(rr.count) as total,
                  SUM(CASE WHEN rr.spf_result='fail' THEN rr.count ELSE 0 END) as spf_fail,
                  SUM(CASE WHEN rr.dkim_result='fail' THEN rr.count ELSE 0 END) as dkim_fail
           FROM report_records rr JOIN reports r ON r.id = rr.report_id
           WHERE r.domain_id=? AND r.date_end >= ? AND r.date_begin <= ?
           GROUP BY rr.source_ip
           HAVING SUM(rr.count) < ? AND SUM(CASE WHEN rr.dkim_result='pass' OR rr.spf_result='pass'
                                                   THEN rr.count ELSE 0 END) = 0""",
        (domain_id, start_epoch, end_epoch, min_volume),
    ).fetchall()
    if not rows:
        return []

    groups = {}
    for row in rows:
        ip, total = row["source_ip"], row["total"]
        ptr_row = conn.execute(
            "SELECT ptr_hostname FROM ptr_checks WHERE source_ip=? ORDER BY checked_at DESC LIMIT 1", (ip,)
        ).fetchone()
        ptr = ptr_row["ptr_hostname"] if ptr_row else None

        cloud_host = _guess_cloud_host(ptr)
        esp = _guess_provider(ptr) if not cloud_host else None
        if cloud_host:
            kind, pattern = "cloud_compute", cloud_host
        elif esp:
            kind, pattern = "esp", esp
        elif ptr:
            # Loose fallback: the PTR's last two labels, so e.g. three
            # different one-off IPs all ending in ".example-host.net" still
            # cluster together even with no recognized provider pattern.
            labels = ptr.rstrip(".").split(".")
            pattern = ".".join(labels[-2:]) if len(labels) >= 2 else ptr
            kind = "ptr_other"
        else:
            # Covers both "genuinely has no PTR record" and "not checked yet"
            # (recency windows for the rollup vs. the PTR-eligibility sweep
            # aren't perfectly aligned) -- either way, there's nothing to go
            # on from reverse-DNS yet, so one honestly-hedged bucket for both.
            kind, pattern = "no_ptr", "No reverse-DNS record on file"

        key = (kind, pattern)
        g = groups.setdefault(key, {"pattern": pattern, "kind": kind, "count": 0, "example_ips": [],
                                     "all_spf_fail": True, "all_dkim_fail": True})
        g["count"] += total
        if len(g["example_ips"]) < 3:
            g["example_ips"].append(ip)
        g["all_spf_fail"] = g["all_spf_fail"] and (row["spf_fail"] == total)
        g["all_dkim_fail"] = g["all_dkim_fail"] and (row["dkim_fail"] == total)

    # cloud_compute first (most actionable), then esp, then everything else,
    # each tier sorted by volume -- so the highest-signal group always leads.
    kind_rank = {"cloud_compute": 0, "esp": 1, "ptr_other": 2, "no_ptr": 3}
    return sorted(groups.values(), key=lambda g: (kind_rank[g["kind"]], -g["count"]))


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


def altered_own_evidence(conn, domain_id: int, domain_name: str, source_ip: str, ptr: str = None):
    """The proof behind an "altered_own" source, or None if its failing mail
    doesn't mostly carry our own (proven, but broken) signature.

    Answers the operator's real question -- "is this mine?" -- from what the
    mail carried, not from whose network the IP is on (the IP only says who
    handed it over last). Facts gathered, each one checkable:
      - the signing key (DKIM selector) it carried, and that the same key
        passes on our normal mail from other servers;
      - the bounce domain (envelope from), and that it passes SPF elsewhere;
      - who it was going to (envelope_to, when the reporter includes it), and
        whether our own SES send log has mail to that domain;
      - the reverse-DNS name, when it's a known email-security service;
      - what the receiver did with it (blocked or let through).
    `certain` is only set when the key is proven AND either our own send log
    or a known security service backs it up -- otherwise a copied signature
    header could look the same, and the caller must stay hedged."""
    from app.analysis import _guess_security_gateway

    own_like = "(lower({c}) = lower(:dom) OR lower({c}) LIKE '%.' || lower(:dom))"
    args = {"did": domain_id, "dom": domain_name, "ip": source_ip}
    proven_keys = {
        (r["d"], r["s"]): r["n"] for r in conn.execute(
            f"""SELECT lower(ar.domain) d, lower(ar.selector) s, SUM(rr.count) n
                FROM record_auth_results ar JOIN report_records rr ON rr.id = ar.record_id
                JOIN reports r ON r.id = rr.report_id
                WHERE r.domain_id = :did AND rr.source_ip != :ip AND ar.mechanism = 'dkim'
                  AND ar.result = 'pass' AND ar.selector IS NOT NULL AND {own_like.format(c='ar.domain')}
                GROUP BY 1, 2""", args)
    }
    proven_bounce = {
        r["d"] for r in conn.execute(
            f"""SELECT DISTINCT lower(ar.domain) d
                FROM record_auth_results ar JOIN report_records rr ON rr.id = ar.record_id
                JOIN reports r ON r.id = rr.report_id
                WHERE r.domain_id = :did AND rr.source_ip != :ip AND ar.mechanism = 'spf'
                  AND ar.result = 'pass' AND {own_like.format(c='ar.domain')}""", args)
    }
    records = conn.execute(
        """SELECT rr.id, rr.count, rr.disposition, rr.envelope_from, rr.envelope_to, r.date_begin, r.date_end,
                  EXISTS (SELECT 1 FROM record_auth_results ok WHERE ok.record_id = rr.id
                          AND ok.mechanism = 'dkim' AND ok.result = 'pass'
                          AND lower(ok.domain) IN ('mailgun.org', 'eu.mailgun.org', 'amazonses.com', 'sendgrid.net')) AS intact
           FROM report_records rr JOIN reports r ON r.id = rr.report_id
           WHERE r.domain_id = :did AND rr.source_ip = :ip
             AND rr.dkim_result != 'pass' AND COALESCE(rr.spf_result, '') != 'pass'""", args).fetchall()
    if not records:
        return None

    failing = sum(r["count"] for r in records)
    broken, keys, bounce, recipients, blocked, quarantined = 0, set(), set(), {}, 0, 0
    first = last = None
    for r in records:
        sigs = conn.execute(
            "SELECT lower(domain) d, lower(selector) s FROM record_auth_results "
            "WHERE record_id = ? AND mechanism = 'dkim' AND result = 'fail'", (r["id"],)).fetchall()
        hit = [(s["d"], s["s"]) for s in sigs if (s["d"], s["s"]) in proven_keys]
        if not hit or r["intact"]:  # provider's signature passed: not altered, our key failed (own_key_failing)
            continue
        broken += r["count"]
        keys.update(hit)
        if r["envelope_from"] and _is_own(r["envelope_from"], domain_name):
            bounce.add(r["envelope_from"].lower())
        if r["envelope_to"]:
            recipients[r["envelope_to"]] = recipients.get(r["envelope_to"], 0) + r["count"]
        if r["disposition"] == "reject":
            blocked += r["count"]
        elif r["disposition"] == "quarantine":
            quarantined += r["count"]
        first = min(first or r["date_begin"], r["date_begin"])
        last = max(last or r["date_end"], r["date_end"])
    if not broken or broken < 0.8 * failing:
        return None

    # Our own record of sending there: SES keeps one row per recipient per
    # newsletter. Only the recipient domains the reporter named are looked up.
    own_sends = []
    for rdom in recipients:
        own_sends += [dict(row) for row in conn.execute(
            """SELECT email, COUNT(*) AS sends, MIN(first_seen_at) AS first_sent, MAX(first_seen_at) AS last_sent,
                      SUM(delivered) AS delivered, SUM(opened) AS opened, SUM(clicked) AS clicked,
                      SUM(bounced) AS bounced
               FROM ses_campaign_recipients WHERE domain_id = ? AND lower(email) LIKE '%@' || ?
               GROUP BY email ORDER BY sends DESC""", (domain_id, rdom))]

    if ptr is None:
        row = conn.execute("SELECT ptr_hostname FROM ptr_checks WHERE source_ip=? AND ptr_hostname IS NOT NULL "
                           "ORDER BY checked_at DESC LIMIT 1", (source_ip,)).fetchone()
        ptr = row["ptr_hostname"] if row else None
    gateway = _guess_security_gateway(ptr)

    return {
        "failing": failing,
        "broken": broken,
        "keys": sorted(keys),
        "key_passes": sum(proven_keys[k] for k in keys),
        "bounce_domains": sorted(bounce),
        "bounce_proven": bool(bounce) and bounce <= proven_bounce,
        "recipients": sorted(recipients.items(), key=lambda kv: -kv[1]),
        "own_sends": own_sends,
        "ptr": ptr,
        "gateway": gateway,
        "blocked": blocked,
        "quarantined": quarantined,
        "first_seen": first,
        "last_seen": last,
        "certain": bool(own_sends or gateway),
    }


def blocked_at_recipient(conn, days: int = 90) -> list:
    """Every source where our own mail was blocked or spam-foldered after a
    recipient's mail system changed it (altered_own, `certain` only), across
    the portfolio -- one row per (domain, source) for the overview panel.
    Recent window only: an old one-off isn't worth a reminder."""
    start = int((datetime.datetime.utcnow() - datetime.timedelta(days=days)).timestamp())
    out = []
    for d in conn.execute("SELECT id, name FROM domains ORDER BY name").fetchall():
        for ip, info in classify_sources(conn, d["id"], d["name"], start_epoch=start).items():
            if info["kind"] != "altered_own":
                continue
            ev = altered_own_evidence(conn, d["id"], d["name"], ip)
            if not ev or not ev["certain"] or not (ev["blocked"] + ev["quarantined"]):
                continue
            out.append({"domain": d["name"], "ip": ip, "gateway": ev["gateway"],
                        "recipients": [r for r, _ in ev["recipients"]],
                        "hit": ev["blocked"] + ev["quarantined"]})
    return out


def own_key_failing_evidence(conn, domain_id: int, domain_name: str, source_ip: str):
    """Facts for an own_key_failing source: which of our keys failed on
    messages whose provider signature passed, how many and when, and what
    our own DKIM check currently says about that key's DNS record."""
    from app.analysis import _ESP_DEFAULT_AUTH_DOMAINS
    esp = ",".join(f"'{d}'" for d in sorted(_ESP_DEFAULT_AUTH_DOMAINS))
    rows = conn.execute(
        f"""SELECT lower(ar.domain) d, ar.selector s, SUM(rr.count) n,
                   MIN(r.date_begin) first, MAX(r.date_end) last,
                   SUM(CASE WHEN rr.disposition IN ('quarantine', 'reject') THEN rr.count ELSE 0 END) acted,
                   (SELECT lower(ok.domain) FROM record_auth_results ok WHERE ok.record_id = rr.id
                      AND ok.mechanism = 'dkim' AND ok.result = 'pass' AND lower(ok.domain) IN ({esp}) LIMIT 1) provider
            FROM report_records rr JOIN reports r ON r.id = rr.report_id
            JOIN record_auth_results ar ON ar.record_id = rr.id
            WHERE r.domain_id = ? AND rr.source_ip = ? AND rr.dkim_result != 'pass'
              AND ar.mechanism = 'dkim' AND ar.result IN ('fail', 'permerror')
              AND (lower(ar.domain) = lower(?) OR lower(ar.domain) LIKE '%.' || lower(?))
              AND EXISTS (SELECT 1 FROM record_auth_results ok WHERE ok.record_id = rr.id
                          AND ok.mechanism = 'dkim' AND ok.result = 'pass' AND lower(ok.domain) IN ({esp}))
            GROUP BY 1, 2 ORDER BY n DESC""",
        (domain_id, source_ip, domain_name, domain_name),
    ).fetchall()
    if not rows:
        return None
    top = rows[0]
    check = conn.execute(
        "SELECT status FROM dkim_checks WHERE signing_domain=? AND selector=? ORDER BY checked_at DESC LIMIT 1",
        (top["d"], top["s"]),
    ).fetchone()
    return {
        "signing_domain": top["d"], "selector": top["s"], "record": f"{top['s']}._domainkey.{top['d']}",
        "failed": sum(r["n"] for r in rows), "acted": sum(r["acted"] for r in rows),
        "first_seen": min(r["first"] for r in rows), "last_seen": max(r["last"] for r in rows),
        "provider_domain": top["provider"], "dns_status": check["status"] if check else None,
    }
