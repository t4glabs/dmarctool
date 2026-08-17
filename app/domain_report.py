"""
Periodic, plain-language email reports for non-technical domain owners.

DMARCTool's entire value to the small, non-technical grassroots orgs Aikyam
supports is otherwise invisible to them -- they never see this dashboard.
This module builds a short, warm, jargon-free summary of what changed for
their domain since the last report (what got fixed, what's still being
watched, how their mail is doing) and sends it via Mailgun to a recipient
address they configure themselves, on their own schedule.

Deliberately NOT the same voice as the rest of this tool: app.labels already
avoids saying "quarantine"/"reject", but still uses words like "policy",
"authentication", and raw percentages. This module goes a level simpler --
no DMARC/SPF/DKIM/policy/percent at all, analogies instead -- because the
audience here isn't an operator who chose to run this tool, it's someone who
may never have heard of any of this and shouldn't need to.

Settings are per-domain (`domain_report_settings`, one row per domain) rather
than the tool-wide `settings` table, since recipient/cadence/greeting are
individual to each org, not a shared threshold. `enabled` defaults to 0 --
nothing is ever sent until a domain is explicitly opted in, and always after
a "send test now" review (see run_report_emails() and the domain page's
Manual Log tab).

Uses stdlib only plus app.mailgun.send_message() (already stdlib urllib) --
no new dependency.
"""

import argparse
import datetime

from app.analysis import current_policy_run, domain_window_stats, epoch_day, recent_campaigns
from app.config import get_secret
from app.db import get_connection, init_db
from app.mailgun import send_message

DEFAULT_INTERVAL_DAYS = 30

# Deliberately curated, not exhaustive (same rationale as content_scoring.py's
# phrase list) -- only the categories realistic for a small Ghost+Mailgun
# beneficiary domain, in plain "what was actually going on" language. A
# category not listed here falls back to a generic phrase rather than ever
# leaking a raw internal code into the email.
_PROBLEM_STORY = {
    "dns_drift": "the settings that protect your website's name in emails changed unexpectedly",
    "blocklist": "one of the computers sending your emails ended up on a public \"don't trust this sender\" list, which can send your mail straight to spam",
    "ptr_issue": "one of your sending computers wasn't labeled correctly on the internet, which makes some email services distrust it",
    "mailgun_reputation": "more of your emails than usual were bouncing back or being marked as spam",
    "mailgun_new_suppressions": "some people's addresses stopped accepting your mail (often a typo, a full inbox, or an address that no longer exists)",
    "ses_new_suppressions": "some people's addresses stopped accepting your mail (often a typo, a full inbox, or an address that no longer exists)",
    "new_sender": "we noticed a computer sending email using your website's name that we hadn't seen before",
    "failure_investigation": "a computer sending email using your website's name was having trouble passing safety checks",
    "spf_missing": "your website was missing a security setting that helps stop people from faking your emails",
    "dns_missing": "your website didn't have the basic protection that stops people from faking your emails",
    "dkim_missing": "your website was missing a kind of digital signature that proves your emails really came from you",
    "safe_browsing_flagged": "Google flagged your website as possibly unsafe, which can scare visitors away",
    "postmaster_compliance": "Google let us know something about how your emails are being handled needed attention",
    "borrowed_sending_identity": "some of your emails were accidentally being sent through a different website's account",
    "display_name_issue": "the name your newsletters show as being \"from\" could look confusing or untrustworthy to your readers",
    "content_spam_risk": "the wording in one of your newsletters looked similar to what spam filters watch out for",
    "subject_spam_risk": "the subject line of one of your newsletters looked similar to what spam filters watch out for",
}
_GENERIC_PROBLEM_STORY = "something about your website's email setup needed attention"

# These are operator-facing/internal signals about Aikyam's own workflow or
# data pipeline (e.g. "haven't ingested reports lately", "here's Aikyam's own
# next suggested step", "found an untracked subdomain to add to DMARCTool") --
# never a "problem with your website" a beneficiary org should be told about.
# Excluded outright rather than falling back to a generic phrase, so the
# report doesn't manufacture a vague "something needed attention" out of
# something that was never actually about them.
_OPERATOR_ONLY_CATEGORIES = {
    "ramp_recommendation", "data_stale", "untracked_sending_subdomain",
    "volume_spike", "sending_cadence_irregular",
}


def _plain_problem(category: str) -> str:
    return _PROBLEM_STORY.get(category, _GENERIC_PROBLEM_STORY)


def _explain_policy_for_owner(p: str, pct) -> str:
    """The DMARC policy, in plain language with no DMARC/policy/percent words
    at all -- a locked-door analogy instead. Goes a level further than
    app.labels.explain_policy(), which is aimed at an operator, not a
    complete non-technical reader."""
    if not p:
        return ("We haven't yet turned on the protection that stops people from faking your website's "
                "name in emails -- that's the next thing we're setting up for you.")
    pct = 100 if pct is None else pct
    if p == "none":
        return ("Right now we're quietly watching for anyone faking your website's name in emails, "
                "without blocking anything yet -- this is the safe first step before turning on real "
                "protection, so we don't accidentally block any of your own real mail while we're still watching.")
    verb = "sent straight to spam instead of the inbox" if p == "quarantine" else "blocked completely, never even arriving"
    if pct >= 100:
        return f"Every email we catch pretending to be from you now gets {verb}."
    return (f"Think of it like a lock we're gradually tightening: right now, about {pct} out of every 100 "
            f"suspicious emails pretending to be you get {verb}, and we're keeping a close eye on the rest "
            f"before turning the lock up further -- that way we never accidentally block your own real mail.")


def get_report_settings(conn, domain_id: int):
    return conn.execute("SELECT * FROM domain_report_settings WHERE domain_id=?", (domain_id,)).fetchone()


def save_report_settings(conn, domain_id: int, recipient_email: str, recipient_label: str,
                          interval_days: int, enabled: bool) -> None:
    conn.execute(
        """INSERT INTO domain_report_settings (domain_id, recipient_email, recipient_label, interval_days, enabled)
           VALUES (?,?,?,?,?)
           ON CONFLICT(domain_id) DO UPDATE SET
             recipient_email=excluded.recipient_email, recipient_label=excluded.recipient_label,
             interval_days=excluded.interval_days, enabled=excluded.enabled""",
        (domain_id, recipient_email, recipient_label, interval_days, int(enabled)),
    )
    conn.commit()


def _resolved_problems(conn, domain_id: int, start_str: str, end_str: str):
    """Action items resolved in this window -- excludes manual_log rows
    (status='logged', not a system-detected problem) since only ever
    reflecting DMARCTool's own findings here, not the operator's private notes."""
    rows = conn.execute(
        """SELECT DISTINCT category FROM action_items
           WHERE domain_id=? AND status IN ('done','dismissed') AND resolved_at BETWEEN ? AND ?
             AND category IS NOT NULL""",
        (domain_id, start_str, end_str),
    ).fetchall()
    return [_plain_problem(r["category"]) for r in rows if r["category"] not in _OPERATOR_ONLY_CATEGORIES]


def _still_open_problems(conn, domain_id: int):
    rows = conn.execute(
        """SELECT DISTINCT category FROM action_items
           WHERE domain_id=? AND status='open' AND category IS NOT NULL""",
        (domain_id,),
    ).fetchall()
    return [_plain_problem(r["category"]) for r in rows if r["category"] not in _OPERATOR_ONLY_CATEGORIES]


def _blocklist_events(conn, domain_id: int, start_str: str, end_str: str):
    """Any blocklist listing among this domain's known sending IPs during the
    window -- known_senders is only used here for the *set of IPs*, which is
    stable identity data, not the cumulative counters (see the module-level
    caution elsewhere in this codebase about known_senders not being safe for
    period-diffing counts)."""
    ips = [r["source_ip"] for r in conn.execute(
        "SELECT DISTINCT source_ip FROM known_senders WHERE domain_id=?", (domain_id,)
    ).fetchall()]
    if not ips:
        return 0
    placeholders = ",".join("?" for _ in ips)
    row = conn.execute(
        f"""SELECT COUNT(*) as n FROM blocklist_checks
            WHERE source_ip IN ({placeholders}) AND status='listed' AND checked_at BETWEEN ? AND ?""",
        (*ips, start_str, end_str),
    ).fetchone()
    return row["n"] or 0


def _newsletter_reach(conn, domain_id: int, domain_name: str, start_date: str, end_date: str, prev_start_date: str):
    """Opens for newsletters sent in this window vs. the equal-length window
    before it, phrased relatively rather than as raw numbers. Returns None if
    no newsletters were sent in this window (most beneficiary domains won't
    have Listmonk/SES campaign data at all, or won't send every period)."""
    campaigns = recent_campaigns(conn, domain_id, limit=200)
    this_period = [c for c in campaigns if c["send_day"] and start_date <= c["send_day"] <= end_date]
    prev_period = [c for c in campaigns if c["send_day"] and prev_start_date <= c["send_day"] < start_date]
    if not this_period:
        return None

    def _open_rate(items):
        delivered = sum(c["delivered"] for c in items)
        opened = sum(c["opened"] for c in items)
        return (opened / delivered) if delivered else None

    this_rate = _open_rate(this_period)
    prev_rate = _open_rate(prev_period)
    count = len(this_period)
    story = f"You sent {count} newsletter{'s' if count != 1 else ''} this time."
    if this_rate is not None:
        if prev_rate is not None and prev_rate > 0:
            change = (this_rate - prev_rate) / prev_rate
            if change > 0.1:
                trend = "more people opened them than usual -- nice work!"
            elif change < -0.1:
                trend = "fewer people opened them than usual -- worth thinking about what might have changed."
            else:
                trend = "about as many people opened them as usual."
            story += f" Out of every 100 people who received one, about {round(this_rate * 100)} opened it -- {trend}"
        else:
            story += f" Out of every 100 people who received one, about {round(this_rate * 100)} opened it."
    return story


def build_domain_report(conn, domain_id: int, domain_name: str,
                         period_start: datetime.datetime, period_end: datetime.datetime) -> dict:
    """Returns {"resolved": [...], "still_open": [...], "deliverability": str,
    "protection": str, "newsletter": str|None, "blocklist_events": int} --
    plain-language sections ready to drop into the email templates."""
    start_str = period_start.strftime("%Y-%m-%d %H:%M:%S")
    end_str = period_end.strftime("%Y-%m-%d %H:%M:%S")
    start_epoch = int(period_start.timestamp())
    end_epoch = int(period_end.timestamp())
    prev_start_epoch = start_epoch - (end_epoch - start_epoch)

    resolved = _resolved_problems(conn, domain_id, start_str, end_str)
    still_open = _still_open_problems(conn, domain_id)

    total, passed, rate = domain_window_stats(conn, domain_id, start_epoch, end_epoch)
    _, _, prev_rate = domain_window_stats(conn, domain_id, prev_start_epoch, start_epoch)
    if total:
        deliverability = f"Out of every 100 emails sent using your website's name, about {round(rate * 100)} arrived safely."
        if prev_rate:
            prev_pct, cur_pct = round(prev_rate * 100), round(rate * 100)
            if cur_pct > prev_pct + 2:
                deliverability += f" That's better than last time (about {prev_pct} out of 100)."
            elif cur_pct < prev_pct - 2:
                deliverability += f" That's a bit lower than last time (about {prev_pct} out of 100) -- we're looking into why."
            else:
                deliverability += " That's about the same as last time."
    else:
        deliverability = "We didn't get enough information about your emails this time to say how they're doing -- nothing to worry about, we'll know more next time."

    policy_run = current_policy_run(conn, domain_id)
    protection = _explain_policy_for_owner(policy_run["p"] if policy_run else None, policy_run["pct"] if policy_run else None)

    newsletter = _newsletter_reach(
        conn, domain_id, domain_name,
        period_start.date().isoformat(), period_end.date().isoformat(),
        (period_start - (period_end - period_start)).date().isoformat(),
    )

    blocklist_events = _blocklist_events(conn, domain_id, start_str, end_str)

    return {
        "resolved": resolved,
        "still_open": still_open,
        "deliverability": deliverability,
        "protection": protection,
        "newsletter": newsletter,
        "blocklist_events": blocklist_events,
    }


def run_report_emails(conn, verbose: bool = True) -> None:
    """Self-throttling, same shape as every other _stale()-gated check in
    this codebase: one function, called from the existing 6-hourly job (and
    the manual "Refresh now" button), that decides per-domain whether it's
    due rather than needing its own scheduler job."""
    sender_email = get_secret("REPORT_SENDER_EMAIL")
    sender_domain = get_secret("REPORT_SENDER_MAILGUN_DOMAIN")
    api_key = get_secret("MAILGUN_API_KEY")
    if not (sender_email and sender_domain and api_key):
        if verbose:
            print("[domain_report] missing REPORT_SENDER_EMAIL/REPORT_SENDER_MAILGUN_DOMAIN/MAILGUN_API_KEY -- skipping")
        return

    now = datetime.datetime.utcnow()
    rows = conn.execute(
        """SELECT drs.*, d.name as domain_name FROM domain_report_settings drs
           JOIN domains d ON d.id = drs.domain_id WHERE drs.enabled=1"""
    ).fetchall()

    for row in rows:
        if not row["recipient_email"]:
            continue
        interval = row["interval_days"] or DEFAULT_INTERVAL_DAYS
        if row["last_sent_at"]:
            last_sent = datetime.datetime.strptime(row["last_sent_at"], "%Y-%m-%d %H:%M:%S")
            if now - last_sent < datetime.timedelta(days=interval):
                continue
            period_start = last_sent
        else:
            period_start = now - datetime.timedelta(days=interval)

        send_report_now(conn, row["domain_id"], row["domain_name"], row["recipient_email"],
                         row["recipient_label"], period_start, now,
                         sender_email, sender_domain, api_key, mark_sent=True, verbose=verbose)


def send_report_now(conn, domain_id: int, domain_name: str, recipient_email: str, recipient_label: str,
                     period_start: datetime.datetime, period_end: datetime.datetime,
                     sender_email: str, sender_domain: str, api_key: str,
                     mark_sent: bool, verbose: bool = True):
    """Builds, renders, and sends one report -- shared by the background
    scheduler (run_report_emails, mark_sent=True) and the "send test now"
    button (mark_sent=False, so a test never disturbs the real schedule).
    Returns (status, error) where status is 'sent' | 'failed' | 'skipped_no_data'
    -- callers must not treat "no error" as "an email actually went out",
    since a skip is also error-free."""
    from app.web import templates  # local import: avoids a circular import at module load time

    has_reports = conn.execute(
        "SELECT 1 FROM reports WHERE domain_id=? AND date_end BETWEEN ? AND ? LIMIT 1",
        (domain_id, int(period_start.timestamp()), int(period_end.timestamp())),
    ).fetchone()
    if not has_reports:
        _log_send(conn, domain_id, period_start, period_end, recipient_email, "skipped_no_data", None)
        if verbose:
            print(f"[domain_report] {domain_name}: no data in this period, skipping")
        return "skipped_no_data", None

    sections = build_domain_report(conn, domain_id, domain_name, period_start, period_end)
    context = {
        "domain_name": domain_name,
        "recipient_label": recipient_label or domain_name,
        "period_start": period_start.date().isoformat(),
        "period_end": period_end.date().isoformat(),
        **sections,
    }
    subject = f"Your email health update from Aikyam -- {domain_name}"
    html = templates.env.get_template("email_report.html").render(**context)
    text = templates.env.get_template("email_report.txt").render(**context)

    message_id, err = send_message(sender_domain, api_key, sender_email, recipient_email, subject, text, html)
    status = "failed" if err else "sent"
    _log_send(conn, domain_id, period_start, period_end, recipient_email, status, err)
    if err:
        if verbose:
            print(f"[domain_report] {domain_name}: send failed -- {err}")
        return "failed", err

    if mark_sent:
        conn.execute(
            "UPDATE domain_report_settings SET last_sent_at=datetime('now') WHERE domain_id=?", (domain_id,)
        )
        conn.commit()
    if verbose:
        print(f"[domain_report] {domain_name}: sent to {recipient_email}")
    return "sent", None


def _log_send(conn, domain_id, period_start, period_end, recipient_email, status, error_message):
    conn.execute(
        """INSERT INTO report_sends (domain_id, period_start, period_end, recipient_email, status, error_message)
           VALUES (?,?,?,?,?,?)""",
        (domain_id, period_start.isoformat(), period_end.isoformat(), recipient_email, status, error_message),
    )
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Send due plain-language domain report emails")
    parser.parse_args()
    conn = get_connection()
    init_db(conn)
    run_report_emails(conn)


if __name__ == "__main__":
    main()
