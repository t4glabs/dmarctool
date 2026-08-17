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

from app.analysis import (current_policy_run, domain_window_stats, ensure_default_settings, epoch_day,
                           recent_campaigns, sending_cadence)
from app.config import get_secret
from app.db import get_connection, init_db
from app.mailgun import send_message

DEFAULT_INTERVAL_DAYS = 30

# Deliberately curated, not exhaustive (same rationale as content_scoring.py's
# phrase list) -- only the categories realistic for a small Ghost+Mailgun
# beneficiary domain, in plain "what was actually going on" language. A
# category not listed here falls back to a generic phrase rather than ever
# leaking a raw internal code into the email. mailgun_new_suppressions/
# ses_new_suppressions are handled separately (they need a real per-report
# count baked into the sentence, not a static phrase).
_PROBLEM_STORY = {
    "dns_drift": "the settings that protect your website's name in emails weren't set up the way they should have been",
    "blocklist": "one of the computers sending your emails ended up on a public \"don't trust this sender\" list, which can send your mail straight to spam",
    "ptr_issue": "one of your sending computers wasn't labeled correctly on the internet, which makes some email services distrust it",
    "mailgun_reputation": "more of your emails than usual were bouncing back or being marked as spam",
    "ses_reputation": "more of your emails than usual were bouncing back or being marked as spam",
    "ses_reputation_watch": "your emails' bounce or spam-complaint rate started creeping up",
    "ses_rejected": "one or more of your emails were blocked before they even went out, usually a sign something needed a closer look",
    # Fallback wording only -- _resolved_items() special-cases these two with
    # a real per-report count instead of this generic phrase; this only shows
    # up if one is still open (these categories currently have no auto-dismiss).
    "mailgun_new_suppressions": "some people's addresses have stopped accepting your mail (often a typo, a full inbox, or an address that no longer exists)",
    "ses_new_suppressions": "some people's addresses have stopped accepting your mail (often a typo, a full inbox, or an address that no longer exists)",
    "new_sender": ("we noticed a computer sending email using your website's name that we hadn't seen before. "
                   "It's worth knowing about, since this can be a sign that someone else is using your "
                   "organization's name without your knowledge"),
    "failure_investigation": ("a computer sending email using your website's name was having trouble passing "
                               "safety checks. It's worth knowing about, since this can be a sign that someone "
                               "else is using your organization's name without your knowledge"),
    "borrowed_sending_identity": ("some of your emails were accidentally being sent through a different website's "
                                   "account. It's worth knowing about, since mix-ups like this can also be a sign "
                                   "that someone else is using your organization's name"),
    "spf_missing": "your website was missing a security setting that helps stop people from faking your emails",
    "dns_missing": "your website didn't have the basic protection that stops people from faking your emails",
    "dkim_missing": "your website was missing a kind of digital signature that proves your emails really came from you",
    "safe_browsing_flagged": "Google flagged your website as possibly unsafe, which can scare visitors away",
    "postmaster_compliance": "Google let us know something about how your emails are being handled needed attention",
    "display_name_issue": "the name your newsletters show as being \"from\" could look confusing or untrustworthy to your readers",
    "content_spam_risk": "the wording in one of your newsletters looked similar to what spam filters watch out for",
    "subject_spam_risk": "the subject line of one of your newsletters looked similar to what spam filters watch out for",
    "sending_cadence_irregular": ("your newsletters haven't been going out on a very consistent schedule lately, "
                                   "and a steady rhythm helps mailbox providers trust your mail more"),
}
_GENERIC_PROBLEM_STORY = "something about your website's email setup needed attention"

# These are operator-facing/internal signals about Aikyam's own workflow or
# data pipeline (e.g. "haven't ingested reports lately", "here's Aikyam's own
# next suggested step", "found an untracked subdomain to add to DMARCTool") --
# never a "problem with your website" a beneficiary org should be told about.
# Excluded outright rather than falling back to a generic phrase, so the
# report doesn't manufacture a vague "something needed attention" out of
# something that was never actually about them.
#
# sending_cadence_irregular is deliberately NOT here (unlike the others) --
# it's real, actionable, consumer-facing guidance ("send on a consistent
# schedule"), not an internal Aikyam/DMARCTool workflow signal.
_OPERATOR_ONLY_CATEGORIES = {
    "ramp_recommendation", "data_stale", "untracked_sending_subdomain",
    "volume_spike",
}

# Still-open categories serious enough to explicitly invite the reader to
# contact Aikyam directly, rather than just listing the problem and moving on.
_URGENT_STILL_OPEN_CATEGORIES = {
    "mailgun_reputation", "ses_reputation", "ses_reputation_watch",
    "ses_rejected", "blocklist", "safe_browsing_flagged",
}

# Gmail/Postmaster's own calibration points, reused from the same thresholds
# this tool already uses elsewhere (settings.mailgun_complaint_rate_warn and
# the health-score formula in analysis.snapshot_domain_health) rather than
# invented numbers.
GOOD_SPAM_RATE = 0.001   # Google's own recommended ceiling
BAD_SPAM_RATE = 0.003    # existing "this is a real problem" threshold

# A small curated library, not exhaustive -- same rationale as _PROBLEM_STORY:
# only what's realistic for a small Ghost+Mailgun beneficiary domain, keyed
# to whatever's actually open/at-risk for THIS domain rather than a static
# list everyone gets regardless of their situation.
_TIP_LIBRARY = {
    "mailgun_reputation": "Keep an eye on your bounce and spam-complaint numbers over the next few weeks, and remove any addresses that keep bouncing from your own list.",
    "ses_reputation": "Keep an eye on your bounce and spam-complaint numbers over the next few weeks, and remove any addresses that keep bouncing from your own list.",
    "ses_reputation_watch": "Keep an eye on your bounce and spam-complaint numbers over the next few weeks, and remove any addresses that keep bouncing from your own list.",
    "blocklist": "If this happens again, ask whoever manages your sending platform (Ghost/Mailgun) to look into why one of your sending computers landed on a spam list.",
    "safe_browsing_flagged": "Check your website for anything that looks out of place, since this sometimes happens after a plugin or theme gets compromised.",
    "display_name_issue": "Keep your newsletter's \"from\" name consistent and clearly recognizable as your organization across every email you send.",
    "content_spam_risk": "Before your next newsletter, read the subject line and body out loud. If it sounds like a sales pitch or leans on urgency (\"Act now\", \"limited time\"), soften it.",
    "subject_spam_risk": "Before your next newsletter, read the subject line out loud. If it sounds like a sales pitch or leans on urgency (\"Act now\", \"limited time\"), soften it.",
    "postmaster_compliance": "aikyam can walk you through Google's checklist for bulk senders to make sure your setup still matches it.",
    "spf_missing": "This one needs a small change to your website's DNS settings. aikyam can make this change for you if you're not comfortable doing it yourself.",
    "dns_missing": "This one needs a small change to your website's DNS settings. aikyam can make this change for you if you're not comfortable doing it yourself.",
    "dkim_missing": "This one needs a small change to your website's DNS settings. aikyam can make this change for you if you're not comfortable doing it yourself.",
}
_CONSISTENCY_TIP = ("Try sending your newsletter on the same day of the week or month each time. A predictable "
                     "rhythm helps mailbox providers trust your mail more, and helps your readers know when to "
                     "expect you.")
_GENERIC_TIP = ("Keep sending on a predictable schedule, keep your list clean by removing addresses that bounce, "
                 "and reach out to aikyam any time something looks off. We'd rather help early than after it "
                 "becomes a bigger problem.")

# Categories where this domain's own overall pass rate (7 days before the fix
# vs. 7 days after) is a fair, honest measure of whether the fix actually
# helped -- reused across every authentication-adjacent category rather than
# inventing a bespoke metric each one would need its own explanation for.
_PASS_RATE_IMPACT_CATEGORIES = {
    "dns_drift", "ptr_issue", "spf_missing", "dns_missing", "dkim_missing",
    "failure_investigation", "new_sender", "borrowed_sending_identity",
}
# A couple of categories have their own cleaner, more specific rate (the
# ESP's own bounce+complaint rate) instead of the generic pass-rate impact.
_MAILGUN_RATE_IMPACT_CATEGORIES = {"mailgun_reputation"}
_SES_RATE_IMPACT_CATEGORIES = {"ses_reputation", "ses_reputation_watch"}

MATERIAL_PASS_RATE_DELTA = 2   # percentage points -- below this, don't manufacture a "wow factor" from noise
MATERIAL_ESP_RATE_DELTA = 1    # percentage points -- ESP bounce/complaint rates are usually much smaller numbers


def _plain_problem(category: str) -> str:
    return _PROBLEM_STORY.get(category, _GENERIC_PROBLEM_STORY)


def _pass_rate_impact(conn, domain_id: int, around: datetime.datetime):
    """This domain's overall pass rate in the 7 days before `around` vs. the
    7 days after -- returns None (no impact clause shown) unless the change
    is real/material; never invents a number from statistical noise."""
    week = datetime.timedelta(days=7)
    _, _, before = domain_window_stats(conn, domain_id, int((around - week).timestamp()), int(around.timestamp()))
    _, _, after = domain_window_stats(conn, domain_id, int(around.timestamp()), int((around + week).timestamp()))
    if not before or not after:
        return None
    before_pct, after_pct = round(before * 100), round(after * 100)
    if after_pct - before_pct < MATERIAL_PASS_RATE_DELTA:
        return None
    return (f"in the week after, the share of your mail passing safety checks went from "
            f"{before_pct} to {after_pct} out of every 100")


def _rate_drop_sentence(before, after):
    if before is None or after is None:
        return None
    before_pct, after_pct = before * 100, after * 100
    if before_pct - after_pct < MATERIAL_ESP_RATE_DELTA:
        return None
    return f"your bounce/spam-complaint rate dropped from about {before_pct:.1f}% to {after_pct:.1f}%"


def _mailgun_rate_impact(conn, mailgun_domain: str, around: datetime.datetime):
    week = datetime.timedelta(days=7)

    def _window(start_day, end_day):
        row = conn.execute(
            """SELECT SUM(accepted) as a, SUM(failed_perm) as f, SUM(complained) as c
               FROM mailgun_daily_stats WHERE mailgun_domain=? AND day BETWEEN ? AND ?""",
            (mailgun_domain, start_day, end_day),
        ).fetchone()
        accepted = row["a"] or 0
        bad = (row["f"] or 0) + (row["c"] or 0)
        return (bad / accepted) if accepted else None

    before = _window((around - week).date().isoformat(), around.date().isoformat())
    after = _window(around.date().isoformat(), (around + week).date().isoformat())
    return _rate_drop_sentence(before, after)


def _ses_rate_impact(conn, config_set: str, around: datetime.datetime):
    week = datetime.timedelta(days=7)

    def _window(start_day, end_day):
        row = conn.execute(
            """SELECT SUM(delivered) as d, SUM(bounced) as b, SUM(complained) as c
               FROM ses_event_counts WHERE configuration_set=? AND day BETWEEN ? AND ?""",
            (config_set, start_day, end_day),
        ).fetchone()
        delivered = row["d"] or 0
        bad = (row["b"] or 0) + (row["c"] or 0)
        return (bad / delivered) if delivered else None

    before = _window((around - week).date().isoformat(), around.date().isoformat())
    after = _window(around.date().isoformat(), (around + week).date().isoformat())
    return _rate_drop_sentence(before, after)


def _suppression_story(count: int) -> str:
    return (f"{count} email address{'es' if count != 1 else ''} stopped accepting your mail during this time "
            f"(usually a typo, a full inbox, or an account that's closed). These are now automatically skipped "
            f"so they don't drag down your other emails. Keeping a clean list like this is exactly what helps "
            f"your future emails land in the inbox instead of getting filtered out, so it's worth removing them "
            f"from your own list too")


def _explain_policy_for_owner(p: str, pct) -> str:
    """The DMARC policy, in plain language with no DMARC/policy/percent words
    at all -- a locked-door analogy instead. Goes a level further than
    app.labels.explain_policy(), which is aimed at an operator, not a
    complete non-technical reader."""
    if not p:
        return ("We haven't yet turned on the protection that stops people from faking your website's "
                "name in emails. That's the next thing we're setting up for you.")
    pct = 100 if pct is None else pct
    if p == "none":
        return ("Right now we're quietly watching for anyone faking your website's name in emails, "
                "without blocking anything yet. This is the safe first step before turning on real "
                "protection, so we don't accidentally block any of your own real mail while we're still watching.")
    verb = "sent straight to spam instead of the inbox" if p == "quarantine" else "blocked completely, never even arriving"
    if pct >= 100:
        return f"Every email we catch pretending to be from you now gets {verb}."
    return (f"Think of it like a lock we're gradually tightening: right now, about {pct} out of every 100 "
            f"suspicious emails pretending to be you get {verb}, and we're keeping a close eye on the rest "
            f"before turning the lock up further. That way we never accidentally block your own real mail.")


# Aikyam's own default CC on every domain's report -- so Aikyam always has
# visibility into what each org is being told, without needing to remember
# to add it per domain. Still a real per-domain column, editable/removable
# from the domain's own Email Updates tab, not a hardcoded send-time constant.
DEFAULT_CC_EMAIL = "jinso@aikyamfellows.org"


def _normalize_email_list(value):
    """Accepts one or more comma-separated addresses, trims stray whitespace
    around each, and drops empty entries -- so "a@x.org,  , b@x.org," saves
    cleanly as "a@x.org, b@x.org" instead of round-tripping the mess."""
    if not value:
        return None
    parts = [p.strip() for p in value.split(",") if p.strip()]
    return ", ".join(parts) if parts else None


def get_report_settings(conn, domain_id: int):
    return conn.execute("SELECT * FROM domain_report_settings WHERE domain_id=?", (domain_id,)).fetchone()


def save_report_settings(conn, domain_id: int, recipient_email: str, recipient_label: str,
                          interval_days: int, enabled: bool, cc_email: str = None) -> None:
    recipient_email = _normalize_email_list(recipient_email)
    cc_email = _normalize_email_list(cc_email) or DEFAULT_CC_EMAIL
    conn.execute(
        """INSERT INTO domain_report_settings (domain_id, recipient_email, recipient_label, interval_days, enabled, cc_email)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(domain_id) DO UPDATE SET
             recipient_email=excluded.recipient_email, recipient_label=excluded.recipient_label,
             interval_days=excluded.interval_days, enabled=excluded.enabled, cc_email=excluded.cc_email""",
        (domain_id, recipient_email, recipient_label, interval_days, int(enabled), cc_email),
    )
    conn.commit()


def _resolved_items(conn, domain_id: int, start_str: str, end_str: str):
    """Resolved action items in this window as {"story", "impact"} dicts,
    one per category (matching how they're deduplicated in the dashboard's
    own action-items list) -- excludes manual_log rows (status='logged', not
    a system-detected problem) and operator-only categories. `impact` is a
    real, computed before/after clause, or None when no material change was
    found -- never a fabricated number."""
    rows = conn.execute(
        """SELECT category, MIN(resolved_at) as resolved_at, MAX(ref_key) as ref_key
           FROM action_items
           WHERE domain_id=? AND status IN ('done','dismissed') AND resolved_at BETWEEN ? AND ?
             AND category IS NOT NULL
           GROUP BY category""",
        (domain_id, start_str, end_str),
    ).fetchall()
    items = []
    for r in rows:
        category = r["category"]
        if category in _OPERATOR_ONLY_CATEGORIES:
            continue
        resolved_at = datetime.datetime.strptime(r["resolved_at"], "%Y-%m-%d %H:%M:%S")

        if category in ("mailgun_new_suppressions", "ses_new_suppressions"):
            table = "mailgun_suppressions" if category == "mailgun_new_suppressions" else "ses_suppressions"
            count = conn.execute(
                f"SELECT COUNT(*) as n FROM {table} WHERE domain_id=? AND first_seen_at BETWEEN ? AND ?",
                (domain_id, start_str, end_str),
            ).fetchone()["n"] or 0
            if count <= 0:
                continue
            items.append({"story": _suppression_story(count), "impact": None})
            continue

        impact = None
        if category in _PASS_RATE_IMPACT_CATEGORIES:
            impact = _pass_rate_impact(conn, domain_id, resolved_at)
        elif category in _MAILGUN_RATE_IMPACT_CATEGORIES and r["ref_key"]:
            impact = _mailgun_rate_impact(conn, r["ref_key"], resolved_at)
        elif category in _SES_RATE_IMPACT_CATEGORIES and r["ref_key"]:
            impact = _ses_rate_impact(conn, r["ref_key"], resolved_at)
        items.append({"story": _plain_problem(category), "impact": impact})
    return items


def _still_open_categories(conn, domain_id: int) -> set:
    rows = conn.execute(
        """SELECT DISTINCT category FROM action_items
           WHERE domain_id=? AND status='open' AND category IS NOT NULL""",
        (domain_id,),
    ).fetchall()
    return {r["category"] for r in rows if r["category"] not in _OPERATOR_ONLY_CATEGORIES}


def _still_open_problems(conn, domain_id: int):
    return [_plain_problem(c) for c in _still_open_categories(conn, domain_id)]


def _contact_aikyam_cta(still_open_categories: set):
    """An explicit, direct invitation to reach out -- shown only when
    something still open is serious enough (a reputation/blocklist/safety
    issue) that a non-technical reader shouldn't just sit with it."""
    if not (still_open_categories & _URGENT_STILL_OPEN_CATEGORIES):
        return None
    return ("If you're not sure how to fix what's above, reach out to aikyam directly. This is worth getting "
            "right, and we'll make sure it's solved properly.")


def _tips_for_domain(still_open_categories: set, consistency_irregular: bool) -> list:
    """2-4 concrete, plain-language tips keyed to whatever's actually open or
    at-risk for THIS domain -- never a static list every domain gets
    regardless of their real situation."""
    tips = []
    for cat in still_open_categories:
        tip = _TIP_LIBRARY.get(cat)
        if tip and tip not in tips:
            tips.append(tip)
    if consistency_irregular and _CONSISTENCY_TIP not in tips:
        tips.append(_CONSISTENCY_TIP)
    if not tips:
        tips.append(_GENERIC_TIP)
    return tips[:4]


_POLICY_STRENGTH = {"none": 0, "quarantine": 1, "reject": 2}


def _policy_as_of(conn, domain_id: int, as_of: datetime.datetime):
    return conn.execute(
        """SELECT * FROM policy_history WHERE domain_id=? AND source='report' AND observed_from <= ?
           ORDER BY observed_from DESC LIMIT 1""",
        (domain_id, int(as_of.timestamp())),
    ).fetchone()


def _protection_tightened(conn, domain_id: int, period_start: datetime.datetime):
    """Whether this domain's protection got measurably stronger since the
    last report -- either the lock level itself moved up (none -> quarantine
    -> reject) or the percentage enforced increased materially. Returns None
    rather than manufacturing a delta from noise, from a policy that simply
    hasn't changed, or -- critically -- from a missing "before" data point:
    policy_history often doesn't reach back to period_start (e.g. a domain
    whose current policy row has been in effect since before this report's
    window even started), and treating "no record that far back" as "the
    policy was `none`" would falsely claim a strengthening that never
    happened. Only ever compares against a real, known prior state."""
    after = current_policy_run(conn, domain_id)
    if not after:
        return None
    before = _policy_as_of(conn, domain_id, period_start)
    if not before:
        return None
    after_p = after["p"]
    after_pct = 100 if after["pct"] is None else after["pct"]
    before_p = before["p"]
    before_pct = 100 if before["pct"] is None else before["pct"]
    before_strength = _POLICY_STRENGTH.get(before_p, -1)
    after_strength = _POLICY_STRENGTH.get(after_p, -1)

    if after_strength > before_strength and after_strength >= 0:
        verb = "sent straight to spam instead of the inbox" if after_p == "quarantine" else "blocked completely, never even arriving"
        return (f"Since last time, we've strengthened your protection. Suspicious emails pretending to be from "
                f"you now get {verb}, instead of just being watched. That makes it much harder for someone to "
                f"send fake mail that looks like it's from you.")
    if after_strength == before_strength and after_strength >= 0 and after_pct > before_pct + MATERIAL_PASS_RATE_DELTA:
        return (f"Since last time, we've tightened your protection from catching about {before_pct} out of 100 "
                f"suspicious emails to about {after_pct} out of 100. That makes it much harder for someone to "
                f"send fake mail that looks like it's from you.")
    return None


def _spam_rate_trend(conn, domain_id: int, now: datetime.datetime):
    """Gmail's own reported spam-complaint trend over the last ~3 months vs.
    the 3 months before that, from postmaster_daily_stats. Returns None for
    any domain not verified in Google Postmaster Tools, rather than a
    misleading gap."""
    def _avg(start_date, end_date):
        row = conn.execute(
            "SELECT AVG(spam_rate) as avg FROM postmaster_daily_stats WHERE domain_id=? AND day BETWEEN ? AND ?",
            (domain_id, start_date.isoformat(), end_date.isoformat()),
        ).fetchone()
        return row["avg"]

    recent = _avg((now - datetime.timedelta(days=90)).date(), now.date())
    if recent is None:
        return None
    prior = _avg((now - datetime.timedelta(days=180)).date(), (now - datetime.timedelta(days=90)).date())
    recent_pct = recent * 100

    if recent <= GOOD_SPAM_RATE:
        story = (f"Google's own numbers show very few people are marking your mail as spam, averaging about "
                 f"{recent_pct:.2f}% over the last few months, well under the {GOOD_SPAM_RATE * 100:.2f}% Google "
                 f"recommends staying under.")
    elif recent >= BAD_SPAM_RATE:
        story = (f"Google's own numbers show about {recent_pct:.2f}% of people are marking your mail as spam "
                 f"over the last few months, which is above the {BAD_SPAM_RATE * 100:.2f}% Google treats as a "
                 f"real problem.")
    else:
        story = (f"Google's own numbers show about {recent_pct:.2f}% of people are marking your mail as spam "
                 f"over the last few months, within a normal range but worth keeping an eye on.")
    if prior is not None:
        prior_pct = prior * 100
        if recent_pct < prior_pct - 0.01:
            story += " That's an improvement from a few months ago."
        elif recent_pct > prior_pct + 0.01:
            story += " That's a bit higher than it was a few months ago."
    return story


def _risk_warning(conn, domain_id: int, now: datetime.datetime):
    """A forward-looking "heads up" -- distinct from "still working on" --
    that fires only on a real trend in this domain's OWN history (never a
    one-off blip) or a hard threshold breach, reusing domain_health_snapshots
    so it's the same signal already behind the composite health score."""
    latest = conn.execute(
        "SELECT * FROM domain_health_snapshots WHERE domain_id=? ORDER BY snapshot_date DESC LIMIT 1",
        (domain_id,),
    ).fetchone()
    if not latest:
        return None
    earlier_cutoff = (now - datetime.timedelta(days=21)).date().isoformat()
    earlier = conn.execute(
        """SELECT * FROM domain_health_snapshots WHERE domain_id=? AND snapshot_date <= ?
           ORDER BY snapshot_date DESC LIMIT 1""",
        (domain_id, earlier_cutoff),
    ).fetchone()

    reasons = []
    if latest["postmaster_spam_rate"] is not None and latest["postmaster_spam_rate"] >= BAD_SPAM_RATE:
        reasons.append("Google's own numbers show more people than recommended are marking your mail as spam right now")
    if earlier:
        if (latest["bounce_rate"] is not None and earlier["bounce_rate"] is not None
                and latest["bounce_rate"] > earlier["bounce_rate"] + 0.02):
            reasons.append("your bounce rate has been climbing over the last few weeks")
        if (latest["complaint_rate"] is not None and earlier["complaint_rate"] is not None
                and latest["complaint_rate"] > earlier["complaint_rate"] + 0.001):
            reasons.append("more people have been marking your mail as spam over the last few weeks")
        if (latest["pass_rate"] is not None and earlier["pass_rate"] is not None
                and latest["pass_rate"] < earlier["pass_rate"] - 0.05):
            reasons.append("the share of your mail passing safety checks has been dropping over the last few weeks")
    if not reasons:
        return None
    return ("You are in danger of emails landing in spam folders soon if this continues: " + "; ".join(reasons)
            + ". If you're not sure how to fix this yourself, reach out to aikyam directly. This is really "
            "important for your organization.")


def _health_comparison(conn, domain_id: int):
    """This domain's latest composite health_score vs. every other tracked
    domain's latest score. Only shown once a handful of other domains
    actually have snapshots, so this never claims "better than 100%" off a
    single, meaningless comparison -- and always frames it as "the other
    organizations Aikyam supports," never a geographic claim we can't back."""
    row = conn.execute(
        "SELECT health_score FROM domain_health_snapshots WHERE domain_id=? ORDER BY snapshot_date DESC LIMIT 1",
        (domain_id,),
    ).fetchone()
    if not row or row["health_score"] is None:
        return None
    my_score = row["health_score"]

    latest_per_domain = conn.execute(
        """SELECT domain_id, MAX(snapshot_date) as latest FROM domain_health_snapshots
           WHERE domain_id != ? GROUP BY domain_id""",
        (domain_id,),
    ).fetchall()
    other_scores = []
    for r in latest_per_domain:
        s = conn.execute(
            "SELECT health_score FROM domain_health_snapshots WHERE domain_id=? AND snapshot_date=?",
            (r["domain_id"], r["latest"]),
        ).fetchone()
        if s and s["health_score"] is not None:
            other_scores.append(s["health_score"])
    if len(other_scores) < 3:
        return None

    better_than = sum(1 for s in other_scores if my_score > s)
    percentile = round(100 * better_than / len(other_scores))
    return (f"Your domain's overall email health is better than about {percentile}% of the other organizations "
            f"aikyam supports right now.")


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
    """Opens/bounces/complaints for newsletters sent in this window vs. the
    equal-length window before it, phrased relatively and naming the
    specific factors that changed rather than a single vague "reach" number.
    Returns None if no newsletters were sent in this window (most
    beneficiary domains won't have Listmonk/SES campaign data at all, or
    won't send every period)."""
    campaigns = recent_campaigns(conn, domain_id, limit=200)
    this_period = [c for c in campaigns if c["send_day"] and start_date <= c["send_day"] <= end_date]
    prev_period = [c for c in campaigns if c["send_day"] and prev_start_date <= c["send_day"] < start_date]
    if not this_period:
        return None

    def _rates(items):
        delivered = sum(c["delivered"] for c in items)
        if not delivered:
            return None, None, None
        opened = sum(c["opened"] for c in items)
        bounced = sum(c["bounced"] for c in items)
        complained = sum(c["complained"] for c in items)
        return opened / delivered, bounced / delivered, complained / delivered

    this_open, this_bounce, this_complaint = _rates(this_period)
    prev_open, prev_bounce, prev_complaint = _rates(prev_period)
    count = len(this_period)
    story = f"You sent {count} newsletter{'s' if count != 1 else ''} this time."

    if this_open is not None:
        story += f" Out of every 100 people who received one, about {round(this_open * 100)} opened it."

    improvements, concerns = [], []
    if this_open is not None and prev_open is not None and prev_open > 0:
        change = (this_open - prev_open) / prev_open
        if change > 0.1:
            improvements.append("more people are opening your emails")
        elif change < -0.1:
            concerns.append("fewer people opened them than usual")
    if this_bounce is not None and prev_bounce is not None and prev_bounce - this_bounce >= 0.01:
        improvements.append("fewer of them bounced back")
    elif this_bounce is not None and prev_bounce is not None and this_bounce - prev_bounce >= 0.01:
        concerns.append("more of them bounced back than usual")
    if this_complaint is not None and prev_complaint is not None and prev_complaint - this_complaint >= 0.001:
        improvements.append("fewer people marked them as spam")
    elif this_complaint is not None and prev_complaint is not None and this_complaint - prev_complaint >= 0.001:
        concerns.append("more people marked them as spam than usual")

    if improvements:
        story += " Your newsletters are improving: " + ", and ".join(improvements) + " compared to last time."
    elif concerns:
        story += " Worth a look: " + ", and ".join(concerns) + " compared to last time."
    return story


def build_domain_report(conn, domain_id: int, domain_name: str,
                         period_start: datetime.datetime, period_end: datetime.datetime) -> dict:
    """Returns {"resolved": [{"story","impact"}, ...], "still_open": [...],
    "deliverability": str, "protection": str, "newsletter": str|None,
    "blocklist_events": int, "protection_tightened": str|None,
    "spam_trend": str|None, "risk_warning": str|None, "comparison": str|None,
    "contact_cta": str|None, "tips": [str, ...]} -- plain-language sections
    ready to drop into the email templates. `resolved` items carry a real,
    computed "impact" clause when a material before/after change was found,
    else None."""
    start_str = period_start.strftime("%Y-%m-%d %H:%M:%S")
    end_str = period_end.strftime("%Y-%m-%d %H:%M:%S")
    start_epoch = int(period_start.timestamp())
    end_epoch = int(period_end.timestamp())
    prev_start_epoch = start_epoch - (end_epoch - start_epoch)

    resolved = _resolved_items(conn, domain_id, start_str, end_str)
    still_open_categories = _still_open_categories(conn, domain_id)
    still_open = [_plain_problem(c) for c in still_open_categories]

    total, passed, rate = domain_window_stats(conn, domain_id, start_epoch, end_epoch)
    _, _, prev_rate = domain_window_stats(conn, domain_id, prev_start_epoch, start_epoch)
    if total:
        deliverability = f"Out of every 100 emails sent using your website's name, about {round(rate * 100)} arrived safely."
        if prev_rate:
            prev_pct, cur_pct = round(prev_rate * 100), round(rate * 100)
            if cur_pct > prev_pct + 2:
                deliverability += f" That's better than last time (about {prev_pct} out of 100)."
            elif cur_pct < prev_pct - 2:
                deliverability += f" That's a bit lower than last time (about {prev_pct} out of 100). We're looking into why."
            else:
                deliverability += " That's about the same as last time."
    else:
        deliverability = "We didn't get enough information about your emails this time to say how they're doing. Nothing to worry about, we'll know more next time."

    policy_run = current_policy_run(conn, domain_id)
    protection = _explain_policy_for_owner(policy_run["p"] if policy_run else None, policy_run["pct"] if policy_run else None)

    newsletter = _newsletter_reach(
        conn, domain_id, domain_name,
        period_start.date().isoformat(), period_end.date().isoformat(),
        (period_start - (period_end - period_start)).date().isoformat(),
    )

    blocklist_events = _blocklist_events(conn, domain_id, start_str, end_str)

    protection_tightened = _protection_tightened(conn, domain_id, period_start)
    spam_trend = _spam_rate_trend(conn, domain_id, period_end)
    risk_warning = _risk_warning(conn, domain_id, period_end)
    comparison = _health_comparison(conn, domain_id)
    contact_cta = _contact_aikyam_cta(still_open_categories)
    cadence = sending_cadence(conn, domain_id)
    tips = _tips_for_domain(still_open_categories, cadence["irregular"])

    return {
        "resolved": resolved,
        "still_open": still_open,
        "deliverability": deliverability,
        "protection": protection,
        "newsletter": newsletter,
        "blocklist_events": blocklist_events,
        "protection_tightened": protection_tightened,
        "spam_trend": spam_trend,
        "risk_warning": risk_warning,
        "comparison": comparison,
        "contact_cta": contact_cta,
        "tips": tips,
    }


def _report_period(row, now: datetime.datetime):
    """The period a report for this domain_report_settings row would cover
    right now -- shared by the real scheduler, the test-send route, and the
    in-tool preview, so "what you preview" and "what would actually send"
    are always computed the same way."""
    interval = (row["interval_days"] if row and row["interval_days"] else None) or DEFAULT_INTERVAL_DAYS
    if row and row["last_sent_at"]:
        period_start = datetime.datetime.strptime(row["last_sent_at"], "%Y-%m-%d %H:%M:%S")
    else:
        period_start = now - datetime.timedelta(days=interval)
    return period_start, now


def run_report_emails(conn, verbose: bool = True) -> None:
    """Self-throttling, same shape as every other _stale()-gated check in
    this codebase: one function, called only from the existing 6-hourly job
    (deliberately not the manual "Refresh now" button -- see web.py's
    run_checks() -- since sending mail to a third party is a different class
    of action than every other check there, which only reads/updates
    internal state), that decides per-domain whether it's due rather than
    needing its own scheduler job."""
    sender_email = get_secret("REPORT_SENDER_EMAIL")
    sender_domain = get_secret("REPORT_SENDER_MAILGUN_DOMAIN")
    api_key = get_secret("MAILGUN_SEND_API_KEY")
    if not (sender_email and sender_domain and api_key):
        if verbose:
            print("[domain_report] missing REPORT_SENDER_EMAIL/REPORT_SENDER_MAILGUN_DOMAIN/MAILGUN_SEND_API_KEY -- skipping")
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
        period_start, period_end = _report_period(row, now)

        send_report_now(conn, row["domain_id"], row["domain_name"], row["recipient_email"],
                         row["recipient_label"], period_start, period_end,
                         sender_email, sender_domain, api_key, mark_sent=True, verbose=verbose,
                         cc_email=row["cc_email"])


def _build_context(conn, domain_id: int, domain_name: str, recipient_label: str,
                    period_start: datetime.datetime, period_end: datetime.datetime) -> dict:
    """The full template context for one report -- shared by send_report_now()
    and preview_domain_report(), so a preview is guaranteed to show exactly
    what a real send would render, including the current branding settings."""
    settings = ensure_default_settings(conn)
    sections = build_domain_report(conn, domain_id, domain_name, period_start, period_end)
    return {
        "domain_name": domain_name,
        "recipient_label": recipient_label or domain_name,
        "period_start": period_start.date().isoformat(),
        "period_end": period_end.date().isoformat(),
        "signoff_name": settings["report_signoff_name"],
        **sections,
    }


def preview_domain_report(conn, domain_id: int, domain_name: str):
    """Read-only: renders the plain-text version of the report that would go
    out right now for this domain (same period logic a real/test send would
    use), without sending anything or touching last_sent_at. Returns
    (subject, text, context) -- context is reused by the "view as HTML" route."""
    from app.web import templates  # local import: avoids a circular import at module load time

    settings = ensure_default_settings(conn)
    row = get_report_settings(conn, domain_id)
    period_start, period_end = _report_period(row, datetime.datetime.utcnow())
    recipient_label = row["recipient_label"] if row else None
    context = _build_context(conn, domain_id, domain_name, recipient_label, period_start, period_end)
    subject = settings["report_subject_template"].replace("{domain}", domain_name)
    text = templates.env.get_template("email_report.txt").render(**context)
    return subject, text, context


def send_report_now(conn, domain_id: int, domain_name: str, recipient_email: str, recipient_label: str,
                     period_start: datetime.datetime, period_end: datetime.datetime,
                     sender_email: str, sender_domain: str, api_key: str,
                     mark_sent: bool, verbose: bool = True, cc_email: str = None):
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

    settings = ensure_default_settings(conn)
    context = _build_context(conn, domain_id, domain_name, recipient_label, period_start, period_end)
    sender_name = settings["report_sender_name"]
    from_header = f"{sender_name} <{sender_email}>" if sender_name else sender_email
    subject = settings["report_subject_template"].replace("{domain}", domain_name)
    html = templates.env.get_template("email_report.html").render(**context)
    text = templates.env.get_template("email_report.txt").render(**context)

    message_id, err = send_message(sender_domain, api_key, from_header, recipient_email, subject, text, html,
                                    cc_addr=cc_email)
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
