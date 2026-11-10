"""
Is DMARC's pct= still honoured by the receivers that matter?

Most of this portfolio ramps enforcement with pct= (10 -> 25 -> 50 -> 100),
which is exactly what app.analysis.recommend() advises. But the May 2026
DMARC standard (RFC 9989) REMOVED pct: Appendix A.6 says it was "usually not
accurately applied, unless the value specified was either 0 or 100", and
section 4.7 says unknown tags MUST be ignored. A receiver that adopts RFC 9989
therefore reads `p=quarantine; pct=10` as plain `p=quarantine` and quarantines
ALL failing mail, overnight, with no change on the domain owner's side. Any of
that domain's genuine mail still failing would start landing in spam.

There's no announcement to wait for, but the aggregate reports already say
what each receiver actually DID: every record carries the policy that was
published (policy_pct) and the disposition applied. So for mail that failed
DMARC under a partial pct, compare the share that got the policy against the
share pct asked for. Measured 2026-11-10 over 120 days: Gmail quarantined 7%
of failing mail at pct=10 and 19% at pct=25; Microsoft 365 11% and 31% -- both
still honour it. This re-runs that measurement every sweep and raises a
per-domain action item the moment a receiver stops.

Only DMARC-failing mail counts (policy_evaluated dkim AND spf both not pass --
these columns are DMARC's own aligned verdicts, see ingest.py). For p=reject,
only an actual reject counts as "policy applied": RFC 7489 s6.6.4 tells a
receiver to QUARANTINE the sampled-out remainder, so counting quarantine too
would read ~100% even when pct is honoured.
"""

import datetime

from app.analysis import ensure_default_settings, upsert_system_action

# Display names for the aggregate-report org_name values seen in this
# portfolio. Anything not listed shows under its own reported name.
REPORTER_LABELS = {
    "google.com": "Gmail",
    "Enterprise Outlook": "Microsoft 365",
    "Outlook.com": "Outlook.com",
    "Yahoo": "Yahoo",
}


def reporter_label(org_name):
    return REPORTER_LABELS.get(org_name, org_name)


def measure_pct_handling(conn, max_days, min_failing):
    """One dict per reporting receiver: how much DMARC-failing mail it saw
    under a partial pct, how much of it actually got the published policy,
    and how much pct asked for.

    Judged on each receiver's MOST RECENT `min_failing` failing messages,
    reaching back only as far as needed (up to `max_days`). A fixed window
    doesn't work here: failing volume under a partial pct is lumpy (Gmail saw
    225 in October 2026 but only 8 in the 30 days to 10 Nov), so a short
    window often can't judge at all, while a long one would take weeks for a
    real change to outweigh the old data. `window_days` in the result is the
    span actually used.

    Verdict is 'ignores' when the applied share is closer to 100% than to
    what pct asked for -- the midpoint keeps ordinary sampling noise (month
    to month, Gmail ran 8-21% applied against 14-27% asked) well clear."""
    since = int((datetime.datetime.utcnow() - datetime.timedelta(days=max_days)).timestamp())
    rows = conn.execute(
        """SELECT r.org_name AS reporter,
                  date(r.date_begin, 'unixepoch') AS day,
                  SUM(rr.count) AS failing,
                  SUM(CASE WHEN r.policy_p = 'reject' AND rr.disposition = 'reject' THEN rr.count
                           WHEN r.policy_p = 'quarantine' AND rr.disposition IN ('quarantine', 'reject') THEN rr.count
                           ELSE 0 END) AS applied,
                  SUM(rr.count * r.policy_pct) / 100.0 AS expected
           FROM report_records rr JOIN reports r ON r.id = rr.report_id
           WHERE r.policy_p IN ('quarantine', 'reject') AND r.policy_pct BETWEEN 1 AND 99
             AND COALESCE(rr.dkim_result, '') != 'pass' AND COALESCE(rr.spf_result, '') != 'pass'
             AND r.date_begin >= ?
           GROUP BY r.org_name, day
           ORDER BY day DESC""",
        (since,),
    ).fetchall()
    acc = {}
    today = datetime.datetime.utcnow().date()
    for r in rows:
        a = acc.setdefault(r["reporter"], {"failing": 0, "applied": 0, "expected": 0.0, "oldest": r["day"]})
        if a["failing"] >= min_failing:
            continue  # already have this receiver's most recent min_failing messages
        a["failing"] += r["failing"] or 0
        a["applied"] += r["applied"] or 0
        a["expected"] += r["expected"] or 0
        a["oldest"] = r["day"]
    out = []
    for reporter, a in acc.items():
        if not a["failing"]:
            continue
        applied_share = a["applied"] / a["failing"]
        expected_share = a["expected"] / a["failing"]
        if a["failing"] < min_failing:
            verdict = "not_enough_data"
        elif applied_share >= (expected_share + 1) / 2:
            verdict = "ignores"
        else:
            verdict = "honours"
        out.append({
            "reporter": reporter, "label": reporter_label(reporter),
            "failing": a["failing"], "applied": a["applied"],
            "applied_share": applied_share, "expected_share": expected_share, "verdict": verdict,
            "window_days": (today - datetime.date.fromisoformat(a["oldest"])).days + 1,
        })
    out.sort(key=lambda r: r["failing"], reverse=True)
    return out


def latest_pct_watch(conn):
    """Most recent stored measurement per receiver, for the domain page."""
    return [dict(r) for r in conn.execute(
        """SELECT * FROM pct_watch_checks
           WHERE id IN (SELECT MAX(id) FROM pct_watch_checks GROUP BY reporter)
           ORDER BY failing_msgs DESC"""
    ).fetchall()]


def _partial_pct_domains(conn):
    """Tracked domains whose LIVE record enforces with a partial pct -- the
    ones a receiver ignoring pct actually changes anything for."""
    from app.dns_check import parse_dmarc_tags
    out = []
    for d in conn.execute("SELECT id, name FROM domains").fetchall():
        row = conn.execute(
            "SELECT txt_value FROM dns_checks WHERE domain_id=? ORDER BY checked_at DESC LIMIT 1", (d["id"],)
        ).fetchone()
        if not row or not row["txt_value"]:
            continue
        tags = parse_dmarc_tags(row["txt_value"])
        try:
            pct = int(tags.get("pct", "100"))
        except ValueError:
            continue
        if tags.get("p") in ("quarantine", "reject") and 1 <= pct <= 99:
            out.append((d["id"], d["name"], tags["p"], pct, tags.get("t") == "y"))
    return out


def run_pct_watch(conn, verbose: bool = True) -> None:
    settings = ensure_default_settings(conn)
    max_days = int(settings["pct_watch_max_days"])
    min_failing = int(settings["pct_watch_min_failing"])

    results = measure_pct_handling(conn, max_days, min_failing)
    for r in results:
        conn.execute(
            """INSERT INTO pct_watch_checks
               (reporter, window_days, failing_msgs, applied_msgs, expected_share, applied_share, verdict)
               VALUES (?,?,?,?,?,?,?)""",
            (r["reporter"], r["window_days"], r["failing"], r["applied"], r["expected_share"], r["applied_share"],
             r["verdict"]),
        )

    ignoring = [r for r in results if r["verdict"] == "ignores"]
    partial = _partial_pct_domains(conn)
    flagged = set()
    if ignoring:
        names = ", ".join(r["label"] for r in ignoring)
        evidence = "; ".join(
            f"{r['label']} applied the policy to {r['applied_share']:.0%} of the failing mail it reported across "
            f"all your domains using a partial pct, where pct asked for {r['expected_share']:.0%} on average "
            f"(its most recent {r['failing']} failing messages, over {r['window_days']} days)"
            for r in ignoring
        )
        for domain_id, name, p, pct, has_t in partial:
            flagged.add(domain_id)
            upsert_system_action(
                conn, domain_id, "pct_not_honoured", None,
                f"{name}: {names} now appl{'ies' if len(ignoring) == 1 else 'y'} your full DMARC policy "
                f"-- pct={pct} no longer holds it back",
                f"Your record says p={p}; pct={pct}. But {evidence}. The new DMARC standard (RFC 9989) removed "
                f"pct, so receivers that adopt it apply p={p} to all failing mail. Any of {name}'s own mail that "
                f"still fails DMARC is now getting that treatment at {names}."
                + (" This record already has t=y." if has_t else ""),
            )
    for domain_id, *_ in partial:
        if domain_id not in flagged:
            conn.execute(
                """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
                   WHERE domain_id=? AND category='pct_not_honoured' AND status='open'""",
                (domain_id,),
            )
    # A domain that has since moved to pct=100 / p=none drops out of `partial`
    # entirely; its item is moot either way.
    partial_ids = {p[0] for p in partial}
    for row in conn.execute(
        "SELECT id, domain_id FROM action_items WHERE category='pct_not_honoured' AND status='open'"
    ).fetchall():
        if row["domain_id"] not in partial_ids:
            conn.execute(
                "UPDATE action_items SET status='dismissed', resolved_at=datetime('now') WHERE id=?", (row["id"],)
            )
    conn.commit()
    if verbose:
        for r in results:
            print(f"[pct-watch] {r['label']}: {r['verdict']} -- {r['applied_share']:.0%} applied vs "
                  f"{r['expected_share']:.0%} asked ({r['failing']} failing msgs)")


def main() -> None:
    from app.db import get_connection, init_db
    conn = get_connection()
    init_db(conn)
    run_pct_watch(conn)


if __name__ == "__main__":
    main()
