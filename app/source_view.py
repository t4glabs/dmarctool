"""
One sending source, across every domain it touches.

Everything else in this tool is organised per-domain, which is the right
default and also a blind spot: a single misconfigured relay shows up as an
unrelated-looking problem on each domain separately, and nothing says "these
are the same thing".

The case that prompted this: Mailgun IPs 69.72.42.1 and 69.72.42.14
authenticate as aikyamsolve.org while sending as aikyamhq.com AND
tinybridge.in AND aikyamfellows.org. On aikyamhq.com there was enough volume
to raise a borrowed-identity item; on tinybridge.in the same
misconfiguration, from the same IP, with the same borrowed identity, sat at 4
messages and raised nothing. One fix addresses all three, and no per-domain
page could say so.

Two things live here:

  `source_overview()` -- the read-only view behind /source/<ip>, answering
  "what is this machine, whose mail does it send, and is it doing the same
  thing everywhere".

  `corroborated_identity_pairs()` -- the same cross-domain evidence, fed back
  into app.analysis's borrowed-identity detector so it can recognise a pattern
  it has already proven elsewhere instead of demanding independent proof on
  every domain. See detect_borrowed_sending_identity for how it is applied.
"""

from app.source_classification import HEALTHY_PASS_RATE, classify_sources


def _rows(conn, sql, params=()):
    return conn.execute(sql, params).fetchall()


def source_overview(conn, source_ip: str) -> dict:
    """Everything known about one sending IP, across all tracked domains."""
    per_domain = _rows(
        conn,
        """SELECT d.id AS domain_id, d.name AS domain_name,
                  SUM(rr.count) AS total,
                  SUM(CASE WHEN rr.dkim_result='pass' OR rr.spf_result='pass' THEN rr.count ELSE 0 END) AS passed,
                  SUM(CASE WHEN rr.disposition IN ('quarantine','reject') THEN rr.count ELSE 0 END) AS acted,
                  MIN(r.date_begin) AS first_seen, MAX(r.date_end) AS last_seen
           FROM report_records rr
           JOIN reports r ON r.id = rr.report_id
           JOIN domains d ON d.id = r.domain_id
           WHERE rr.source_ip = ?
           GROUP BY d.id, d.name
           ORDER BY total DESC""",
        (source_ip,),
    )
    if not per_domain:
        return {}

    # Which identities this IP produces valid signatures for, and on which
    # domains -- the fact that makes a shared root cause visible.
    identities = {}
    for row in _rows(
        conn,
        """SELECT LOWER(TRIM(ar.domain)) AS auth_domain, d.name AS domain_name, SUM(rr.count) AS msgs
           FROM record_auth_results ar
           JOIN report_records rr ON rr.id = ar.record_id
           JOIN reports r ON r.id = rr.report_id
           JOIN domains d ON d.id = r.domain_id
           WHERE rr.source_ip = ? AND ar.result = 'pass' AND ar.domain IS NOT NULL
           GROUP BY auth_domain, d.name""",
        (source_ip,),
    ):
        entry = identities.setdefault(row["auth_domain"], {"domains": [], "msgs": 0})
        entry["domains"].append(row["domain_name"])
        entry["msgs"] += row["msgs"] or 0

    tracked = {r["name"].lower() for r in _rows(conn, "SELECT name FROM domains")}

    # Per-domain classification, reusing the same logic the Senders tab shows
    # so the two can never disagree about what this source is.
    domains = []
    for row in per_domain:
        cls = classify_sources(conn, row["domain_id"], row["domain_name"]).get(source_ip) or {}
        total, passed = row["total"] or 0, row["passed"] or 0
        domains.append({
            "domain_name": row["domain_name"],
            "total": total,
            "passed": passed,
            "acted": row["acted"] or 0,
            "pass_rate": (passed / total) if total else None,
            "kind": cls.get("kind"),
            "label": cls.get("label"),
            "help": cls.get("help"),
            "first_seen": row["first_seen"],
            "last_seen": row["last_seen"],
        })

    total_msgs = sum(d["total"] for d in domains)
    total_passed = sum(d["passed"] for d in domains)
    failing = [d for d in domains
               if d["pass_rate"] is not None and d["pass_rate"] < HEALTHY_PASS_RATE]

    return {
        "source_ip": source_ip,
        "domains": domains,
        "identities": sorted(
            ({"auth_domain": k, "msgs": v["msgs"], "domains": sorted(set(v["domains"])),
              "is_tracked": k in tracked} for k, v in identities.items()),
            key=lambda i: -i["msgs"],
        ),
        "total_msgs": total_msgs,
        "total_passed": total_passed,
        "pass_rate": (total_passed / total_msgs) if total_msgs else None,
        "acted": sum(d["acted"] for d in domains),
        "domain_count": len(domains),
        "failing_domain_count": len(failing),
        "ptr": conn.execute(
            "SELECT status, ptr_hostname, note FROM ptr_checks WHERE source_ip=? ORDER BY checked_at DESC LIMIT 1",
            (source_ip,),
        ).fetchone(),
        "blocklist": conn.execute(
            "SELECT status, note FROM blocklist_checks WHERE source_ip=? ORDER BY checked_at DESC LIMIT 1",
            (source_ip,),
        ).fetchone(),
    }


def shared_cause_verdict(overview: dict):
    """(status, text) for the top of the source page: is this one machine
    doing the same wrong thing on several domains, which is the whole reason
    this page exists."""
    if not overview:
        return "muted", "No mail recorded from this source."

    failing = overview["failing_domain_count"]
    borrowed = [i for i in overview["identities"]
                if i["is_tracked"] and len(i["domains"]) > 1]

    if failing > 1 and borrowed:
        i = borrowed[0]
        return "bad", (
            f"⚠️ This one source is failing authentication on {failing} of your domains, and on each of "
            f"them it signs as {i['auth_domain']}. That is a single misconfiguration showing up "
            f"{failing} times -- fixing it where this source is configured fixes all of them at once."
        )
    if failing > 1:
        return "warn", (f"⚠️ This source is failing authentication on {failing} of your domains, so whatever "
                        f"is wrong is likely in how the source itself is set up rather than in any one "
                        f"domain's records.")
    if overview["acted"]:
        return "warn", (f"⚠️ {overview['acted']} message(s) from this source have already been sent to spam or "
                        f"blocked outright by the receiving providers.")
    if failing == 1:
        return "warn", "⚠️ This source is failing authentication on one of your domains."
    return "ok", f"✅ This source's mail is passing on all {overview['domain_count']} domain(s) it sends for."


def corroborated_identity_pairs(conn, high_vol: int, high_fail_rate: float, min_days: int) -> dict:
    """{(source_ip, auth_domain): [domain names where this is independently proven]}

    A pair earns a place here by clearing the borrowed-identity detector's own
    full evidence bar -- enough volume, mostly failing, sustained over days --
    on at least one domain. The detector then treats the same pair on another
    domain as the same already-established fact rather than a fresh hypothesis
    needing its own weeks of evidence.

    Deliberately built from the same thresholds the caller uses, passed in
    rather than re-read here, so corroboration can never be looser than the
    primary rule it is derived from.
    """
    rows = _rows(
        conn,
        """SELECT d.name AS domain_name, ks.source_ip,
                  ks.total_msgs, ks.pass_msgs, ks.first_seen, ks.last_seen
           FROM known_senders ks
           JOIN domains d ON d.id = ks.domain_id""",
    )

    from app.analysis import _ESP_DEFAULT_AUTH_DOMAINS, _passing_auth_domains, epoch_day

    pairs = {}
    for row in rows:
        total = row["total_msgs"] or 0
        if total < high_vol:
            continue
        if total and (row["pass_msgs"] / total) >= high_fail_rate:
            continue
        if (epoch_day(row["last_seen"]) - epoch_day(row["first_seen"])) < min_days:
            continue

        domain_id = conn.execute(
            "SELECT id FROM domains WHERE name = ?", (row["domain_name"],)
        ).fetchone()["id"]
        for auth_domain in _passing_auth_domains(conn, domain_id, row["source_ip"]):
            if auth_domain == row["domain_name"] or auth_domain in _ESP_DEFAULT_AUTH_DOMAINS:
                continue
            pairs.setdefault((row["source_ip"], auth_domain), []).append(row["domain_name"])
    return pairs
