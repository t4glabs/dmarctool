"""
Analysis / recommendation engine.

Run with: python3 -m app.analysis

For each tracked domain this:
  1. Rebuilds policy_history from ingested reports (majority-vote per day, run-length
     encoded, so a single lagging reporter doesn't fake a policy change).
  2. Refreshes known_senders (per-IP totals + a best-effort classification) and flags
     genuinely new source IPs seen in the last `new_sender_window_days`.
  3. Flags known senders with high volume + high failure rate as worth investigating.
  4. Flags domains with no newly-ingested reports in a while (data staleness --
     distinct from DNS drift, which is a later module).
  5. Computes a rolling, volume-aware pass rate and produces a plain-language
     next-suggested-action + reasoning for ramping p/pct, anchored to configurable
     thresholds in the `settings` table.

All "system_suggested" findings are upserted into action_items, keyed on
(domain_id, category, ref_key) among currently-open items, so re-running this
doesn't pile up duplicates -- it just keeps the same item current.

Rolling pass-rate windows are anchored to the latest *ingested* report per domain,
not wall-clock time -- so recommendations reflect data quality, not how recently you
ran the ingester. Staleness (point 7: "have I even ingested recently?") is checked
separately against wall-clock time.
"""

import argparse
import datetime
import socket
from collections import Counter
from pathlib import Path

from app.db import get_connection, init_db
from app.labels import classification_label

DEFAULT_SETTINGS = {
    "min_pass_rate": "0.99",           # rolling pass rate required to consider ramping
    "low_pass_rate": "0.95",           # below this, recommend investigating instead of ramping
    "min_days_stable": "14",           # days at current p/pct required before ramping
    "rolling_window_days": "21",       # analysis window, capped by days at current policy
    "min_volume_for_recommendation": "50",  # total msgs in window needed to trust the rate
    "ramp_steps": "10,25,50,100",      # pct ramp ladder
    "new_sender_window_days": "14",    # a sender first seen within this window is "new"
    "high_volume_fail_threshold": "20",  # msgs in window to flag a failing sender
    "high_fail_rate_threshold": "0.5",   # pass rate below this + high volume => flag
    "stale_days_threshold": "3",        # days since last ingested report => flag staleness
    "blocklist_min_volume": "50",       # total msgs (all-time) needed before an IP is worth a DNSBL check
    "blocklist_recent_days": "30",      # skip IPs not seen sending in this many days
    "blocklist_recheck_hours": "4",     # don't re-query an IP against DNSBL zones more often than this
    "compliance_recheck_hours": "24",   # PTR/SPF/DKIM change far less often than blocklist status
    "spf_lookup_warn_threshold": "8",   # warn before hitting SPF's hard 10-DNS-lookup limit (RFC 7208)
    "dkim_min_bits": "1024",            # Gmail's hard minimum RSA DKIM key length (2048 recommended)
    "mailgun_recheck_hours": "6",        # don't re-poll the Mailgun API more often than this
    "mailgun_stats_window_days": "30",   # lookback window for Mailgun delivered/bounced/complained stats
    "mailgun_bounce_rate_warn": "0.05",  # bounce rate (of accepted) that triggers a flag
    "mailgun_complaint_rate_warn": "0.001",  # complaint rate (of accepted) that triggers a flag
    "postmaster_recheck_hours": "24",     # Postmaster Tools data itself lags/aggregates daily
    "postmaster_stats_window_days": "30", # lookback window for the SPAM_RATE / delivery-error metrics
    "ses_stats_window_days": "30",        # lookback window for SES bounce/complaint rate (from our own accumulated counts)
    "ses_bounce_rate_watch": "0.02",       # bounce rate (of delivered) that triggers an early "watch" flag
    "ses_bounce_rate_warn": "0.05",       # bounce rate (of delivered) that triggers a flag
    "ses_complaint_rate_watch": "0.0008", # complaint rate (of delivered) that triggers an early "watch" flag
    "ses_complaint_rate_warn": "0.001",   # complaint rate (of delivered) that triggers a flag
    "ses_max_messages_per_run": "3000",   # cap SQS messages drained per check so a big backlog can't block a request; the rest drain on the next run
    "ses_account_recheck_hours": "24",    # don't re-poll SES account health/identity verification more often than this
    "newsletter_inactive_campaigns": "9",  # campaigns received with zero opens across all of them => flagged inactive
    "volume_spike_recent_days": "3",       # "recent" window averaged for the spike comparison
    "volume_spike_baseline_days": "7",     # "before" window averaged as the baseline
    "volume_spike_min_baseline_avg": "10", # baseline must average at least this many msgs/day to count
    "volume_spike_multiplier": "2.0",      # recent avg must be at least this many times the baseline to flag
    "safe_browsing_recheck_hours": "24",   # Safe Browsing status doesn't change fast; daily is plenty
}


def ensure_default_settings(conn) -> dict:
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value)
        )
    conn.commit()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {row["key"]: row["value"] for row in rows}


def epoch_day(ts: int) -> int:
    return ts // 86400


def day_to_date(day: int) -> datetime.date:
    return datetime.date(1970, 1, 1) + datetime.timedelta(days=day)


def all_domains(conn):
    return conn.execute("SELECT id, name FROM domains ORDER BY name").fetchall()


# ---------------------------------------------------------------------------
# 1. Policy history
# ---------------------------------------------------------------------------

def derive_policy_history(conn, domain_id: int) -> None:
    rows = conn.execute(
        """SELECT date_begin, date_end, policy_p, policy_sp, policy_pct, policy_adkim, policy_aspf
           FROM reports WHERE domain_id = ? ORDER BY date_begin""",
        (domain_id,),
    ).fetchall()
    if not rows:
        return

    by_day = {}
    for r in rows:
        day = epoch_day(r["date_begin"])
        by_day.setdefault(day, []).append(r)

    daily_key = {}
    daily_row = {}
    for day, day_rows in by_day.items():
        counts = Counter((r["policy_p"], r["policy_pct"]) for r in day_rows)
        winning_key = counts.most_common(1)[0][0]
        daily_key[day] = winning_key
        # keep a representative full row (sp/adkim/aspf) matching the winning key
        daily_row[day] = next(r for r in day_rows if (r["policy_p"], r["policy_pct"]) == winning_key)

    days_sorted = sorted(daily_key)
    runs = []
    for day in days_sorted:
        key = daily_key[day]
        row = daily_row[day]
        if runs and runs[-1]["key"] == key:
            runs[-1]["last_day"] = day
            runs[-1]["observed_to"] = max(runs[-1]["observed_to"], row["date_end"])
        else:
            runs.append({
                "key": key, "first_day": day, "last_day": day,
                "p": row["policy_p"], "pct": row["policy_pct"],
                "sp": row["policy_sp"], "adkim": row["policy_adkim"], "aspf": row["policy_aspf"],
                "observed_from": row["date_begin"], "observed_to": row["date_end"],
            })

    conn.execute("DELETE FROM policy_history WHERE domain_id = ? AND source = 'report'", (domain_id,))
    for run in runs:
        conn.execute(
            """INSERT INTO policy_history (domain_id, p, sp, pct, adkim, aspf, observed_from, observed_to, source)
               VALUES (?,?,?,?,?,?,?,?, 'report')""",
            (domain_id, run["p"], run["sp"], run["pct"], run["adkim"], run["aspf"],
             run["observed_from"], run["observed_to"]),
        )
    conn.commit()


def current_policy_run(conn, domain_id: int):
    """Latest policy_history run for this domain (source='report'), or None."""
    return conn.execute(
        """SELECT * FROM policy_history WHERE domain_id = ? AND source = 'report'
           ORDER BY observed_to DESC LIMIT 1""",
        (domain_id,),
    ).fetchone()


# ---------------------------------------------------------------------------
# 2/3. Known senders + new/failing sender flags
# ---------------------------------------------------------------------------

def _reverse_dns(ip: str, timeout: float = 1.5):
    try:
        socket.setdefaulttimeout(timeout)
        host, _, _ = socket.gethostbyaddr(ip)
        return host
    except Exception:
        return None


def _suggest_classification(conn, domain_id: int, domain_name: str, source_ip: str) -> str:
    row = conn.execute(
        """SELECT DISTINCT ar.domain FROM record_auth_results ar
           JOIN report_records rr ON rr.id = ar.record_id
           JOIN reports r ON r.id = rr.report_id
           WHERE r.domain_id = ? AND rr.source_ip = ? AND ar.domain IS NOT NULL""",
        (domain_id, source_ip),
    ).fetchall()
    auth_domains = {r["domain"] for r in row}
    if f"mails.{domain_name}" in auth_domains:
        return "ses_newsletter"
    if domain_name in auth_domains:
        return "primary_domain"
    return "unclassified"


def update_known_senders(conn, domain_id: int, domain_name: str, settings: dict) -> None:
    rows = conn.execute(
        """SELECT rr.source_ip,
                  MIN(r.date_begin) as first_seen, MAX(r.date_end) as last_seen,
                  SUM(rr.count) as total_msgs,
                  SUM(CASE WHEN rr.dkim_result='pass' OR rr.spf_result='pass' THEN rr.count ELSE 0 END) as pass_msgs
           FROM report_records rr JOIN reports r ON r.id = rr.report_id
           WHERE r.domain_id = ?
           GROUP BY rr.source_ip""",
        (domain_id,),
    ).fetchall()

    existing = {
        row["source_ip"]
        for row in conn.execute("SELECT source_ip FROM known_senders WHERE domain_id = ?", (domain_id,))
    }

    for row in rows:
        ip = row["source_ip"]
        fail_msgs = row["total_msgs"] - row["pass_msgs"]
        if ip not in existing:
            classification = _suggest_classification(conn, domain_id, domain_name, ip)
            conn.execute(
                """INSERT INTO known_senders
                   (domain_id, source_ip, first_seen, last_seen, total_msgs, pass_msgs, fail_msgs, classification)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (domain_id, ip, row["first_seen"], row["last_seen"], row["total_msgs"], row["pass_msgs"],
                 fail_msgs, classification),
            )
        else:
            conn.execute(
                """UPDATE known_senders
                   SET first_seen = MIN(first_seen, ?), last_seen = MAX(last_seen, ?),
                       total_msgs = ?, pass_msgs = ?, fail_msgs = ?
                   WHERE domain_id = ? AND source_ip = ?""",
                (row["first_seen"], row["last_seen"], row["total_msgs"], row["pass_msgs"], fail_msgs,
                 domain_id, ip),
            )
    conn.commit()


def flag_new_and_failing_senders(conn, domain_id: int, settings: dict, now_day: int) -> list:
    new_window = int(settings["new_sender_window_days"])
    high_vol = int(settings["high_volume_fail_threshold"])
    high_fail_rate = float(settings["high_fail_rate_threshold"])

    findings = []
    senders = conn.execute("SELECT * FROM known_senders WHERE domain_id = ?", (domain_id,)).fetchall()
    for s in senders:
        total = s["total_msgs"]
        pass_rate = s["pass_msgs"] / total if total else 0
        first_seen_day = epoch_day(s["first_seen"])
        is_new = (now_day - first_seen_day) <= new_window

        if is_new and s["classification"] == "unclassified" and total >= 3:
            ptr = _reverse_dns(s["source_ip"])
            detail = (f"First seen {day_to_date(first_seen_day)}, {total} msgs, "
                      f"{pass_rate:.0%} pass" + (f", PTR: {ptr}" if ptr else ", no PTR record"))
            findings.append({
                "category": "new_sender", "ref_key": s["source_ip"],
                "title": f"New unrecognized sender {s['source_ip']} on this domain",
                "detail": detail,
            })

        if total >= high_vol and pass_rate < high_fail_rate:
            ptr = _reverse_dns(s["source_ip"]) if s["classification"] == "unclassified" else None
            label = classification_label(s["classification"])
            detail = (f"{total} msgs, {pass_rate:.0%} pass ({s['fail_msgs']} failing), "
                      f"labeled as: {label}" + (f", PTR: {ptr}" if ptr else ""))
            findings.append({
                "category": "failure_investigation", "ref_key": s["source_ip"],
                "title": f"Investigate failing sender {s['source_ip']}",
                "detail": detail,
            })
    return findings


# ---------------------------------------------------------------------------
# 4. Staleness
# ---------------------------------------------------------------------------

def check_staleness(conn, domain_id: int, domain_name: str, settings: dict, wall_now: datetime.datetime) -> list:
    row = conn.execute(
        "SELECT MAX(date_end) as latest FROM reports WHERE domain_id = ?", (domain_id,)
    ).fetchone()
    if not row or row["latest"] is None:
        return []
    latest_dt = datetime.datetime.utcfromtimestamp(row["latest"])
    stale_days = (wall_now - latest_dt).days
    threshold = int(settings["stale_days_threshold"])
    if stale_days >= threshold:
        return [{
            "category": "data_stale", "ref_key": None,
            "title": f"No new DMARC reports ingested for {domain_name} in {stale_days} days",
            "detail": f"Latest ingested report ends {latest_dt.date()}. Re-run ingestion or check reporting inbox routing.",
        }]
    return []


# ---------------------------------------------------------------------------
# 5. Rolling stats + recommendation
# ---------------------------------------------------------------------------

def daily_pass_series(conn, domain_id: int, days: int = 60):
    """List of (date, total, passed, rate) for the last `days` days of ingested data,
    anchored to the latest ingested report for this domain (not wall-clock)."""
    latest_row = conn.execute(
        "SELECT MAX(date_end) as latest FROM reports WHERE domain_id = ?", (domain_id,)
    ).fetchone()
    if not latest_row or latest_row["latest"] is None:
        return []
    latest_day = epoch_day(latest_row["latest"])
    start_day = latest_day - days + 1

    rows = conn.execute(
        """SELECT r.date_begin, rr.count, rr.dkim_result, rr.spf_result
           FROM report_records rr JOIN reports r ON r.id = rr.report_id
           WHERE r.domain_id = ? AND r.date_begin >= ?""",
        (domain_id, start_day * 86400),
    ).fetchall()

    per_day = {d: [0, 0] for d in range(start_day, latest_day + 1)}
    for r in rows:
        day = epoch_day(r["date_begin"])
        if day not in per_day:
            continue
        per_day[day][0] += r["count"]
        if r["dkim_result"] == "pass" or r["spf_result"] == "pass":
            per_day[day][1] += r["count"]

    series = []
    for day in sorted(per_day):
        total, passed = per_day[day]
        rate = passed / total if total else None
        series.append((day_to_date(day), total, passed, rate))
    return series


def check_volume_spike(conn, domain_id: int, domain_name: str, settings: dict) -> list:
    """Gmail's guidance is explicit: increase sending volume gradually, since a
    sudden jump can trigger rate limiting or hurt reputation even if the mail
    itself is fine. This is distinct from the DMARC enforcement pct ramp (that's
    about how much of your *failing* mail gets penalized) -- this compares your
    recent total daily volume against your own trailing baseline, purely from
    report_records counts already ingested, no new data source needed.
    """
    recent_days = int(settings["volume_spike_recent_days"])
    baseline_days = int(settings["volume_spike_baseline_days"])
    min_baseline_avg = float(settings["volume_spike_min_baseline_avg"])
    multiplier = float(settings["volume_spike_multiplier"])

    series = daily_pass_series(conn, domain_id, days=recent_days + baseline_days)
    if len(series) < recent_days + baseline_days:
        return []

    baseline_window = series[:baseline_days]
    recent_window = series[baseline_days:]
    baseline_avg = sum(d[1] for d in baseline_window) / len(baseline_window)
    recent_avg = sum(d[1] for d in recent_window) / len(recent_window)

    if baseline_avg < min_baseline_avg or recent_avg < baseline_avg * multiplier:
        return []

    return [{
        "category": "volume_spike", "ref_key": None,
        "title": f"{domain_name}: sending volume jumped -- ~{recent_avg:.0f}/day recently vs ~{baseline_avg:.0f}/day before",
        "detail": (f"Average volume over the last {recent_days}d is {recent_avg / baseline_avg:.1f}x the prior "
                   f"{baseline_days}d average. Gmail's guidance: ramp sending volume up gradually rather than "
                   f"suddenly -- a sharp jump can trigger rate limiting or hurt reputation even when the mail "
                   f"itself is fine."),
    }]


def domain_window_stats(conn, domain_id: int, window_start: int, window_end: int):
    row = conn.execute(
        """SELECT SUM(rr.count) as total, SUM(CASE WHEN rr.dkim_result='pass' OR rr.spf_result='pass' THEN rr.count ELSE 0 END) as passed
           FROM report_records rr JOIN reports r ON r.id = rr.report_id
           WHERE r.domain_id = ? AND r.date_begin >= ? AND r.date_end <= ?""",
        (domain_id, window_start, window_end),
    ).fetchone()
    total = row["total"] or 0
    passed = row["passed"] or 0
    rate = passed / total if total else 0.0
    return total, passed, rate


def provider_breakdown(conn, domain_id: int, window_start: int, window_end: int):
    """Per-reporting-provider (org_name) volume, pass rate, and disposition mix.

    A per-domain proxy for "how does Gmail/Yahoo/Microsoft see this mail" --
    the closest thing to per-provider reputation that's derivable from DMARC
    aggregate reports alone (no Postmaster Tools / SNDS access needed).
    """
    rows = conn.execute(
        """SELECT r.org_name as org_name,
                  SUM(rr.count) as total,
                  SUM(CASE WHEN rr.dkim_result='pass' OR rr.spf_result='pass' THEN rr.count ELSE 0 END) as passed,
                  SUM(CASE WHEN rr.disposition='none' THEN rr.count ELSE 0 END) as disp_none,
                  SUM(CASE WHEN rr.disposition='quarantine' THEN rr.count ELSE 0 END) as disp_quarantine,
                  SUM(CASE WHEN rr.disposition='reject' THEN rr.count ELSE 0 END) as disp_reject
           FROM report_records rr JOIN reports r ON r.id = rr.report_id
           WHERE r.domain_id = ? AND r.date_begin >= ? AND r.date_end <= ?
           GROUP BY r.org_name
           ORDER BY total DESC""",
        (domain_id, window_start, window_end),
    ).fetchall()
    out = []
    for row in rows:
        total = row["total"] or 0
        out.append({
            "org_name": row["org_name"] or "Unknown reporter",
            "total": total,
            "rate": (row["passed"] or 0) / total if total else None,
            "disp_none": row["disp_none"] or 0,
            "disp_quarantine": row["disp_quarantine"] or 0,
            "disp_reject": row["disp_reject"] or 0,
        })
    return out


def sending_stream_breakdown(conn, domain_id: int, window_start: int, window_end: int):
    """Volume and pass rate per sending stream (the classification you set on
    known senders -- Workspace, SES bulk sender, primary domain, etc.).

    Complements provider_breakdown: that one groups by *receiving* provider,
    this one groups by *your* sending infrastructure, so a domain that mixes
    e.g. Google Workspace mail with an SES/Mailgun bulk stream can see each
    stream's health separately instead of one blended pass rate that hides
    a problem in the smaller stream.
    """
    rows = conn.execute(
        """SELECT ks.classification as classification,
                  SUM(rr.count) as total,
                  SUM(CASE WHEN rr.dkim_result='pass' OR rr.spf_result='pass' THEN rr.count ELSE 0 END) as passed
           FROM report_records rr
           JOIN reports r ON r.id = rr.report_id
           JOIN known_senders ks ON ks.domain_id = r.domain_id AND ks.source_ip = rr.source_ip
           WHERE r.domain_id = ? AND r.date_begin >= ? AND r.date_end <= ?
           GROUP BY ks.classification
           ORDER BY total DESC""",
        (domain_id, window_start, window_end),
    ).fetchall()
    out = []
    for row in rows:
        total = row["total"] or 0
        out.append({
            "classification": row["classification"],
            "total": total,
            "rate": (row["passed"] or 0) / total if total else None,
        })
    return out


def ses_daily_series(conn, domain_id: int, days: int = 60):
    """List of (date_str, delivered, bounce_rate, complaint_rate) from our own
    accumulated SES event counts -- there's no external API to backfill from,
    so this only shows history from whenever the SNS/SQS pipeline went live."""
    since = (datetime.datetime.utcnow().date() - datetime.timedelta(days=days)).isoformat()
    rows = conn.execute(
        """SELECT day, SUM(delivered) as delivered, SUM(bounced) as bounced, SUM(complained) as complained
           FROM ses_event_counts WHERE domain_id=? AND day >= ?
           GROUP BY day ORDER BY day""",
        (domain_id, since),
    ).fetchall()
    series = []
    for r in rows:
        delivered = r["delivered"] or 0
        bounce_rate = (r["bounced"] or 0) / delivered if delivered else None
        complaint_rate = (r["complained"] or 0) / delivered if delivered else None
        series.append((r["day"], delivered, bounce_rate, complaint_rate))
    return series


def _campaign_report_card(c: dict, structure: dict) -> dict:
    """Turns the various per-campaign checks already computed (subject/body
    scoring, display name, unsubscribe/header compliance, this campaign's
    own bounce/complaint rate, and now image/link structure) into a single
    "here's what's good, here's what's not, here's the score" report --
    always says *something*, even when everything is clean, rather than
    only ever speaking up about problems."""
    from app.content_scoring import score_html_structure

    good, issues = [], []

    if c["subject_score"]["score"] == 0:
        good.append(("Subject line", "No spam-trigger words or formatting issues found"))
    else:
        issues.append(("Subject line", "; ".join(c["subject_score"]["flags"]), c["subject_score"]["score"]))

    if c["body_score"] is None:
        good.append(("Newsletter content", "Not yet fetched from Listmonk -- will be scored once content sync catches up"))
    elif c["body_score"]["score"] == 0:
        good.append(("Newsletter content", "No spam-trigger words or formatting issues found in the body text"))
    else:
        issues.append(("Newsletter content", "; ".join(c["body_score"]["flags"]), c["body_score"]["score"]))

    structure_result = score_html_structure(structure["image_count"], structure["word_count"], structure["shortener_links"]) if structure else {"score": 0, "flags": []}
    if structure and structure["word_count"]:
        if structure_result["score"] == 0:
            good.append(("Images & links", f"{structure['image_count']} image(s), {structure['link_count']} link(s) -- healthy balance, no shorteners used"))
        else:
            issues.append(("Images & links", "; ".join(structure_result["flags"]), structure_result["score"]))

    if not c["display_name_issues"]:
        good.append(("Sender display name", f'"{c["from_display_name"] or "-"}" follows Gmail\'s display-name guidelines'))
    else:
        issues.append(("Sender display name", "; ".join(c["display_name_issues"]), 2 * len(c["display_name_issues"])))

    if not c["unsubscribe_issues"]:
        good.append(("Unsubscribe compliance", "One-click unsubscribe headers present and correctly formatted"))
    else:
        issues.append(("Unsubscribe compliance", "; ".join(c["unsubscribe_issues"]), 3 * len(c["unsubscribe_issues"])))

    if not c["header_issues"]:
        good.append(("Message formatting", "Message-ID present, subject isn't a misleading \"Re:\"/\"Fwd:\""))
    else:
        issues.append(("Message formatting", "; ".join(c["header_issues"]), 2 * len(c["header_issues"])))

    if c["delivered"]:
        if c["complaint_rate"] and c["complaint_rate"] >= 0.001:
            issues.append(("Spam complaints", f"{c['complaint_rate']:.2%} of recipients marked this as spam", 5))
        else:
            good.append(("Spam complaints", f"{(c['complaint_rate'] or 0):.2%} -- negligible"))
        if c["bounce_rate"] and c["bounce_rate"] >= 0.05:
            issues.append(("Bounce rate", f"{c['bounce_rate']:.2%} bounced -- check list hygiene", 3))
        else:
            good.append(("Bounce rate", f"{(c['bounce_rate'] or 0):.2%} bounced -- healthy"))

    overall_score = sum(score for _, _, score in issues)
    return {"good": good, "issues": issues, "overall_score": overall_score}


def recent_campaigns(conn, domain_id: int, limit: int = 10):
    """List of dicts, most recent newsletter first -- built entirely from SES's
    own Open/Click/Bounce/Complaint/Delivery events for messages that carry a
    Listmonk X-Listmonk-Campaign header. This is SES's own numbers, not
    Listmonk's -- the two can disagree since Listmonk tracks opens/clicks
    itself via pixel/link rewriting, while this reads what SES actually saw."""
    from app.bounce_reasons import categorize_bounce
    from app.content_scoring import score_text
    from app.display_name_checks import check_display_name
    from app.header_compliance import check_header_hygiene, check_unsubscribe_compliance
    from app.listmonk import analyze_html

    rows = conn.execute(
        """SELECT * FROM ses_campaigns WHERE domain_id=?
           ORDER BY send_day DESC, updated_at DESC LIMIT ?""",
        (domain_id, limit),
    ).fetchall()
    out = []
    for r in rows:
        delivered = r["delivered"] or 0
        bounce_breakdown = Counter()
        if r["bounced"]:
            for br in conn.execute(
                """SELECT bounce_reason FROM ses_campaign_recipients
                   WHERE configuration_set=? AND campaign_id=? AND bounced=1""",
                (r["configuration_set"], r["campaign_id"]),
            ):
                bounce_breakdown[categorize_bounce(br["bounce_reason"])] += 1
        out.append({
            "campaign_id": r["campaign_id"],
            "subject": r["subject"] or "(no subject captured)",
            "from_display_name": r["from_display_name"],
            "send_day": r["send_day"],
            "delivered": delivered,
            "opened": r["opened"] or 0,
            "clicked": r["clicked"] or 0,
            "bounced": r["bounced"] or 0,
            "complained": r["complained"] or 0,
            "open_rate": (r["opened"] or 0) / delivered if delivered else None,
            "click_rate": (r["clicked"] or 0) / delivered if delivered else None,
            "bounce_rate": (r["bounced"] or 0) / delivered if delivered else None,
            "complaint_rate": (r["complained"] or 0) / delivered if delivered else None,
            "bounce_breakdown": bounce_breakdown.most_common(),
            "display_name_issues": check_display_name(r["from_display_name"]),
            "rejected": r["rejected"] or 0,
            "unsubscribe_issues": check_unsubscribe_compliance(r["list_unsubscribe"], r["list_unsubscribe_post"]),
            "header_issues": check_header_hygiene(r["message_id"], r["subject"]),
            "subject_score": score_text(r["subject"]),
            "body_score": score_text(r["body_text"]) if r["body_text"] else None,
        })
        structure = analyze_html(r["body_html"]) if r["body_html"] else None
        out[-1]["structure"] = structure
        out[-1]["report_card"] = _campaign_report_card(out[-1], structure)
    return out


def sending_cadence(conn, domain_id: int):
    """Gaps between consecutive newsletter send days -- Gmail's guidance is
    explicit: "send email at a consistent rate. Avoid sending email in
    bursts" and "avoid introducing sudden volume spikes if you do not have a
    history". Needs at least 3 distinct send days to say anything meaningful
    about a pattern, not just report noise from a single gap."""
    rows = conn.execute(
        "SELECT DISTINCT send_day FROM ses_campaigns WHERE domain_id=? AND send_day IS NOT NULL ORDER BY send_day",
        (domain_id,),
    ).fetchall()
    days = [datetime.datetime.strptime(r["send_day"], "%Y-%m-%d").date() for r in rows]
    if len(days) < 3:
        return {"days": [d.isoformat() for d in days], "average_gap_days": None, "latest_gap_days": None, "irregular": False}

    gaps = [(days[i] - days[i - 1]).days for i in range(1, len(days))]
    avg_gap = sum(gaps[:-1]) / len(gaps[:-1]) if len(gaps) > 1 else gaps[0]
    latest_gap = gaps[-1]
    # Flag only a clear departure from the domain's own history -- e.g. a
    # newsletter that normally goes out every ~7 days suddenly going out
    # next-day, or after a 2+ month silence.
    irregular = avg_gap > 0 and (latest_gap < avg_gap * 0.3 or latest_gap > avg_gap * 3)
    return {
        "days": [d.isoformat() for d in days],
        "average_gap_days": round(avg_gap, 1),
        "latest_gap_days": latest_gap,
        "irregular": irregular,
    }


def display_name_summary(conn, domain_id: int):
    """Distinct sender display names seen across this domain's newsletters,
    plus a consistency flag -- Gmail's guidelines call for "a consistent,
    clear, and accurate statement of the sender's identity", which is only
    checkable in aggregate across campaigns, not from a single one."""
    from app.display_name_checks import display_name_consistency

    campaigns = recent_campaigns(conn, domain_id, limit=50)
    names = display_name_consistency(campaigns)
    return {"names": names, "consistent": len(names) <= 1}


def subscriber_engagement_summary(conn, domain_id: int, threshold: int = None):
    """Replaces the manual monthly SQL query the team runs directly against
    Listmonk's DB ('received 9+ campaigns, opened zero') -- built from the
    same per-recipient SES event data as recent_campaigns, so it's the same
    trusted source, just aggregated differently."""
    if threshold is None:
        settings = ensure_default_settings(conn)
        threshold = int(settings["newsletter_inactive_campaigns"])
    per_email = conn.execute(
        """SELECT email, COUNT(DISTINCT campaign_id) as received, MAX(opened) as ever_opened,
                  MIN(first_seen_at) as first_received, MAX(last_seen_at) as last_received
           FROM ses_campaign_recipients WHERE domain_id=? AND delivered=1
           GROUP BY email""",
        (domain_id,),
    ).fetchall()
    inactive = sorted(
        (
            {
                "email": r["email"], "received": r["received"],
                "first_received": r["first_received"], "last_received": r["last_received"],
                "reason": f"{r['received']} newsletters received, 0 opened",
            }
            for r in per_email if r["received"] >= threshold and not r["ever_opened"]
        ),
        key=lambda x: -x["received"],
    )
    return {"total_subscribers": len(per_email), "inactive": inactive, "threshold": threshold}


def bounce_category_breakdown(conn, domain_id: int):
    """Bounce counts by plain-language category (see app/bounce_reasons.py),
    combined across Mailgun and SES -- turns a bare bounce count into an
    actual reason, using real diagnostic text already captured for
    suppression purposes."""
    from app.bounce_reasons import categorize_bounce

    counts = Counter()
    for r in conn.execute(
        "SELECT reason, bounce_type FROM ses_suppressions WHERE domain_id=? AND kind='bounce'",
        (domain_id,),
    ):
        counts[categorize_bounce(r["reason"], r["bounce_type"])] += 1
    for r in conn.execute(
        "SELECT reason FROM mailgun_suppressions WHERE domain_id=? AND kind='bounce'",
        (domain_id,),
    ):
        counts[categorize_bounce(r["reason"], None)] += 1
    return counts.most_common()


def mailgun_daily_series(conn, domain_id: int, days: int = 60):
    """List of (date_str, delivered, bounce_rate, complaint_rate) -- same shape
    as ses_daily_series so both can share the same chart function. Built from
    our own accumulated history; Mailgun's own API only ever returns a rolling
    window per query."""
    since = (datetime.datetime.utcnow().date() - datetime.timedelta(days=days)).isoformat()
    rows = conn.execute(
        """SELECT day, SUM(delivered) as delivered, SUM(failed_perm) as failed_perm, SUM(complained) as complained
           FROM mailgun_daily_stats WHERE domain_id=? AND day >= ?
           GROUP BY day ORDER BY day""",
        (domain_id, since),
    ).fetchall()
    series = []
    for r in rows:
        delivered = r["delivered"] or 0
        bounce_rate = (r["failed_perm"] or 0) / delivered if delivered else None
        complaint_rate = (r["complained"] or 0) / delivered if delivered else None
        series.append((r["day"], delivered, bounce_rate, complaint_rate))
    return series


def postmaster_daily_series(conn, domain_id: int, days: int = 60):
    """List of (date_str, spam_rate) from our own accumulated daily Postmaster
    pulls -- Postmaster Tools itself only exposes a rolling window per query,
    so history here only goes back to whenever this domain was first checked."""
    since = (datetime.datetime.utcnow().date() - datetime.timedelta(days=days)).isoformat()
    rows = conn.execute(
        """SELECT day, spam_rate FROM postmaster_daily_stats
           WHERE domain_id=? AND day >= ? ORDER BY day""",
        (domain_id, since),
    ).fetchall()
    return [(r["day"], r["spam_rate"]) for r in rows]


def recommend(conn, domain_id: int, domain_name: str, settings: dict, latest_report_end: int):
    run = current_policy_run(conn, domain_id)
    if run is None:
        return None

    p, pct = run["p"], run["pct"]
    days_stable = (epoch_day(latest_report_end) - epoch_day(run["observed_from"]))
    min_days_stable = int(settings["min_days_stable"])
    min_pass_rate = float(settings["min_pass_rate"])
    low_pass_rate = float(settings["low_pass_rate"])
    min_volume = int(settings["min_volume_for_recommendation"])
    rolling_window_days = int(settings["rolling_window_days"])
    ramp_steps = [int(x) for x in settings["ramp_steps"].split(",")]

    window_days = min(rolling_window_days, max(days_stable, 1))
    window_start = latest_report_end - window_days * 86400
    total, passed, rate = domain_window_stats(conn, domain_id, window_start, latest_report_end)

    if total < min_volume:
        return {
            "title": f"{domain_name}: insufficient recent volume for a recommendation",
            "detail": f"Only {total} msgs in the last {window_days}d at p={p}/pct={pct}. "
                      f"Need >= {min_volume} to trust the rate; will reassess as more reports arrive.",
        }

    if p == "none":
        if days_stable >= min_days_stable and rate >= min_pass_rate:
            return {
                "title": f"{domain_name}: ready to start enforcement (move p=none -> p=quarantine, pct=10)",
                "detail": f"{rate:.1%} pass over last {window_days}d ({total} msgs) at p=none for {days_stable}d "
                          f"(>= {min_days_stable}d threshold). Safe to begin the ramp.",
            }
        return {
            "title": f"{domain_name}: hold at p=none, monitoring",
            "detail": f"{rate:.1%} pass over last {window_days}d ({total} msgs); {days_stable}/{min_days_stable}d "
                      f"stability elapsed. Not yet ready to begin enforcement.",
        }

    if pct < 100:
        if rate < low_pass_rate:
            return {
                "title": f"{domain_name}: pass rate dropped at p={p}/pct={pct} -- investigate before ramping",
                "detail": f"{rate:.1%} pass over last {window_days}d ({total} msgs), below the {low_pass_rate:.0%} "
                          f"floor. See failure_investigation action items for likely culprits.",
            }
        if rate >= min_pass_rate and days_stable >= min_days_stable:
            next_steps = [s for s in ramp_steps if s > pct]
            next_pct = next_steps[0] if next_steps else 100
            return {
                "title": f"{domain_name}: safe to raise pct={pct} -> pct={next_pct}",
                "detail": f"Sustained {rate:.1%} pass over last {window_days}d ({total} msgs) at p={p}/pct={pct} "
                          f"for {days_stable}d (>= {min_days_stable}d threshold).",
            }
        return {
            "title": f"{domain_name}: hold at p={p}/pct={pct}",
            "detail": f"{rate:.1%} pass over last {window_days}d ({total} msgs); {days_stable}/{min_days_stable}d "
                      f"stability elapsed at this pct. Keep monitoring before ramping further.",
        }

    # pct == 100
    if p == "quarantine":
        if rate >= min_pass_rate and days_stable >= min_days_stable:
            return {
                "title": f"{domain_name}: ready to move p=quarantine -> p=reject",
                "detail": f"Sustained {rate:.1%} pass over last {window_days}d ({total} msgs) at "
                          f"p=quarantine/pct=100 for {days_stable}d.",
            }
        return {
            "title": f"{domain_name}: hold at p=quarantine/pct=100",
            "detail": f"{rate:.1%} pass over last {window_days}d ({total} msgs); {days_stable}/{min_days_stable}d "
                      f"stability elapsed. Not yet ready to move to p=reject.",
        }

    # p == 'reject', pct == 100 -- fully enforced
    return None


def eligible_known_senders(conn, settings: dict):
    """known_senders rows worth spending outbound-lookup budget on: excludes
    one-off/stray IPs and senders that stopped sending long ago. Shared by
    blocklist.py and compliance.py so both check the same real infrastructure.
    """
    min_volume = int(settings["blocklist_min_volume"])
    recent_cutoff = int(datetime.datetime.utcnow().timestamp()) - int(settings["blocklist_recent_days"]) * 86400
    return conn.execute(
        """SELECT DISTINCT ks.source_ip, ks.domain_id, d.name as domain_name
           FROM known_senders ks JOIN domains d ON d.id = ks.domain_id
           WHERE ks.classification != 'ignored'
             AND ks.total_msgs >= ?
             AND ks.last_seen >= ?""",
        (min_volume, recent_cutoff),
    ).fetchall()


def upsert_system_action(conn, domain_id: int, category: str, ref_key, title: str, detail: str) -> None:
    row = conn.execute(
        """SELECT id FROM action_items
           WHERE domain_id = ? AND category = ? AND (ref_key IS ? OR ref_key = ?) AND status = 'open' AND kind = 'system_suggested'""",
        (domain_id, category, ref_key, ref_key),
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE action_items SET title = ?, detail = ?, updated_at = datetime('now') WHERE id = ?",
            (title, detail, row["id"]),
        )
    else:
        conn.execute(
            """INSERT INTO action_items (domain_id, kind, category, ref_key, title, detail)
               VALUES (?, 'system_suggested', ?, ?, ?, ?)""",
            (domain_id, category, ref_key, title, detail),
        )


def run_analysis(conn, verbose: bool = True) -> None:
    settings = ensure_default_settings(conn)
    wall_now = datetime.datetime.utcnow()

    for domain in all_domains(conn):
        domain_id, domain_name = domain["id"], domain["name"]
        derive_policy_history(conn, domain_id)
        update_known_senders(conn, domain_id, domain_name, settings)

        latest_row = conn.execute(
            "SELECT MAX(date_end) as latest FROM reports WHERE domain_id = ?", (domain_id,)
        ).fetchone()
        latest_report_end = latest_row["latest"]

        findings = []
        if latest_report_end is not None:
            now_day = epoch_day(latest_report_end)
            findings += flag_new_and_failing_senders(conn, domain_id, settings, now_day)
            findings += check_volume_spike(conn, domain_id, domain_name, settings)
        findings += check_staleness(conn, domain_id, domain_name, settings, wall_now)

        for f in findings:
            upsert_system_action(conn, domain_id, f["category"], f["ref_key"], f["title"], f["detail"])

        rec = None
        if latest_report_end is not None:
            rec = recommend(conn, domain_id, domain_name, settings, latest_report_end)
        if rec:
            upsert_system_action(conn, domain_id, "ramp_recommendation", None, rec["title"], rec["detail"])
        else:
            conn.execute(
                """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
                   WHERE domain_id=? AND category='ramp_recommendation' AND status='open'""",
                (domain_id,),
            )

        conn.commit()

        if verbose:
            print(f"\n=== {domain_name} ===")
            if latest_report_end is None:
                print("  no reports ingested yet")
                continue
            run = current_policy_run(conn, domain_id)
            print(f"  current policy (from reports): p={run['p']} pct={run['pct']} "
                  f"since {day_to_date(epoch_day(run['observed_from']))}")
            if rec:
                print(f"  -> {rec['title']}")
                print(f"     {rec['detail']}")
            else:
                print("  -> fully enforced (p=reject, pct=100) and no active recommendation")
            open_items = conn.execute(
                "SELECT category, title FROM action_items WHERE domain_id=? AND status='open' AND category != 'ramp_recommendation'",
                (domain_id,),
            ).fetchall()
            for item in open_items:
                print(f"  [{item['category']}] {item['title']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the DMARC analysis/recommendation engine")
    parser.parse_args()
    conn = get_connection()
    init_db(conn)
    run_analysis(conn)


if __name__ == "__main__":
    main()
