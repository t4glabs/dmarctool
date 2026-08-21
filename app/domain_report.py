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

from app.analysis import (cached_whois_org, current_policy_run, domain_window_stats, ensure_default_settings,
                           epoch_day, guess_sender_identity, recent_campaigns, sending_cadence)
from app.config import get_secret
from app.db import get_connection, init_db
from app.domain_expiry import days_until
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
    "dns_policy_weakened": ("your protection against fake emails using your name got weaker recently, not just "
                             "out of date on our end -- someone or something actually changed a setting on your "
                             "website's domain"),
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
    # Deliberately generic ONLY as a last resort -- _postmaster_story() below
    # names the actual requirement Google flagged. The old generic wording
    # ("something ... needed attention") appeared in five of nine real reports
    # and told the reader nothing about what, where, or how, which is exactly
    # the anxious-and-useless combination this report exists to avoid.
    "postmaster_compliance": "Google flagged one of its sender requirements for your domain",
    "display_name_issue": "the name your newsletters show as being \"from\" could look confusing or untrustworthy to your readers",
    "content_spam_risk": "the wording in one of your newsletters looked similar to what spam filters watch out for",
    "subject_spam_risk": "the subject line of one of your newsletters looked similar to what spam filters watch out for",
    "sending_cadence_irregular": ("your newsletters haven't been going out on a very consistent schedule lately, "
                                   "and a steady rhythm helps mailbox providers trust your mail more"),
    "domain_expiring_soon": "your website's domain name registration is coming up for renewal soon",
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
    "ses_rejected", "blocklist", "safe_browsing_flagged", "dns_policy_weakened",
    "domain_expiring_soon",
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
    "spf_missing": "This one needs a small change to your website's DNS settings. aikyam can make this change for you if you're not comfortable doing it yourself.",
    "dns_missing": "This one needs a small change to your website's DNS settings. aikyam can make this change for you if you're not comfortable doing it yourself.",
    "dkim_missing": "This one needs a small change to your website's DNS settings. aikyam can make this change for you if you're not comfortable doing it yourself.",
    "domain_expiring_soon": "Renew your domain name with whoever you registered it through, as soon as you can -- if it lapses, your website and all your email stop working right away, and someone else could register it.",
}
_CONSISTENCY_TIP = ("Try sending your newsletter on the same day of the week or month each time. A predictable "
                     "rhythm helps mailbox providers trust your mail more, and helps your readers know when to "
                     "expect you.")
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


MAX_SAMPLE_EMAILS = 3  # how many actual addresses to name before falling back to "and N more"


def _suppression_story(count: int, sample_emails=None) -> str:
    """Naming a few of the real addresses (this is the domain owner's own
    subscriber list, not a third party's) makes "some people" concrete --
    the reader can picture who actually stopped receiving mail, rather than
    an abstract count."""
    base = (f"{count} email address{'es' if count != 1 else ''} stopped accepting your mail during this time "
            f"(usually a typo, a full inbox, or an account that's closed)")
    if sample_emails:
        shown = sample_emails[:MAX_SAMPLE_EMAILS]
        names = ", ".join(shown)
        remaining = len(sample_emails) - len(shown)
        if remaining > 0:
            names += f", and {remaining} more"
        base += f", including {names}"
    base += (". These are now automatically skipped so they don't drag down your other emails. Keeping a clean "
             "list like this is exactly what helps your future emails land in the inbox instead of getting "
             "filtered out, so it's worth removing them from your own list too")
    return base


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


def _resolved_items(conn, domain_id: int, start_str: str, end_str: str, blocklist_real_ips: set = frozenset(),
                     exclude_categories: set = frozenset()):
    """Resolved action items in this window as {"story", "impact"} dicts,
    one per category (matching how they're deduplicated in the dashboard's
    own action-items list) -- excludes manual_log rows (status='logged', not
    a system-detected problem) and operator-only categories. `impact` is a
    real, computed before/after clause, or None when no material change was
    found -- never a fabricated number. `blocklist_real_ips` -- computed once
    by build_domain_report() via _blocklist_split() -- skips the generic
    "blocklist" line here when the representative IP for this window turns
    out to be a spoofing attempt rather than a real concern; that case gets
    its own good-news story elsewhere instead."""
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
        # Never claim we fixed something that is ALSO still open. Action items
        # are per-(category, ref_key), so one IP's failure can be resolved
        # while another's is live -- both then rendered the SAME sentence,
        # producing "we fixed X" immediately followed by "we're still working
        # on X" in three of nine real reports. Nothing erodes trust faster
        # than a report contradicting itself, so still-open wins: the honest
        # framing is that the problem isn't finished yet.
        if category in exclude_categories:
            continue
        if category == "blocklist" and r["ref_key"] not in blocklist_real_ips:
            continue
        resolved_at = datetime.datetime.strptime(r["resolved_at"], "%Y-%m-%d %H:%M:%S")

        # Bounced/unsubscribed addresses are neither "we fixed it" nor "still
        # pending" -- they're routine list hygiene, partly automatic and partly
        # the org's own to action. Reported in their own section instead, since
        # putting them under "what we fixed" produced the odd combination of
        # asking the reader to prune their list and telling them we'd already
        # handled it in the same sentence.
        if category in ("mailgun_new_suppressions", "ses_new_suppressions"):
            continue

        impact = None
        if category in _PASS_RATE_IMPACT_CATEGORIES:
            impact = _pass_rate_impact(conn, domain_id, resolved_at)
        elif category in _MAILGUN_RATE_IMPACT_CATEGORIES and r["ref_key"]:
            impact = _mailgun_rate_impact(conn, r["ref_key"], resolved_at)
        elif category in _SES_RATE_IMPACT_CATEGORIES and r["ref_key"]:
            impact = _ses_rate_impact(conn, r["ref_key"], resolved_at)
        items.append({"story": _plain_problem(category), "impact": impact, "why": _why_it_matters(category)})
    return items


def _still_open_categories(conn, domain_id: int, blocklist_real_ips: set = frozenset()) -> set:
    """Distinct open, reader-facing categories -- used to gate the contact-
    Aikyam CTA and the tips library. A currently-open "blocklist" item only
    counts here if at least one of its IPs is a real concern (not a spoofing
    attempt already handled elsewhere), otherwise there's nothing left to
    act on for that category."""
    rows = conn.execute(
        """SELECT DISTINCT category, ref_key FROM action_items
           WHERE domain_id=? AND status='open' AND category IS NOT NULL""",
        (domain_id,),
    ).fetchall()
    categories = set()
    for r in rows:
        category = r["category"]
        if category in _OPERATOR_ONLY_CATEGORIES:
            continue
        if category == "blocklist" and r["ref_key"] not in blocklist_real_ips:
            continue
        categories.add(category)
    return categories


def _reputation_rate_detail(conn, category: str, ref_key: str, start_str: str, end_str: str):
    """Real bounce/complaint numbers for THIS report's own window on a still-
    open reputation issue -- turns "more than usual" into an actual count
    and rate the reader can picture. Returns None rather than a fabricated
    figure when there's no volume to compute one from."""
    start_day, end_day = start_str[:10], end_str[:10]
    if category == "mailgun_reputation":
        row = conn.execute(
            """SELECT SUM(accepted) as total, SUM(failed_perm) as bounced, SUM(complained) as complained
               FROM mailgun_daily_stats WHERE mailgun_domain=? AND day BETWEEN ? AND ?""",
            (ref_key, start_day, end_day),
        ).fetchone()
    else:
        row = conn.execute(
            """SELECT SUM(delivered) as total, SUM(bounced) as bounced, SUM(complained) as complained
               FROM ses_event_counts WHERE configuration_set=? AND day BETWEEN ? AND ?""",
            (ref_key, start_day, end_day),
        ).fetchone()
    total = row["total"] or 0
    bad = (row["bounced"] or 0) + (row["complained"] or 0)
    if not total or not bad:
        return None
    days = max(1, (datetime.datetime.strptime(end_day, "%Y-%m-%d") - datetime.datetime.strptime(start_day, "%Y-%m-%d")).days)
    rate = bad * 100 / total
    return (f"That's about {bad} out of the {total} emails you sent over the last {days} days, "
            f"roughly {rate:.1f}%")


def _domain_expiry_detail(conn, domain_id: int):
    """Real expiry date + days-left for the still-open domain_expiring_soon
    item -- same "make it concrete, not just a generic phrase" discipline as
    _reputation_rate_detail. Returns None if the latest check somehow has no
    expires_at (shouldn't happen if the item is open, but never guess)."""
    row = conn.execute(
        "SELECT expires_at FROM domain_expiry_checks WHERE domain_id=? ORDER BY checked_at DESC LIMIT 1",
        (domain_id,),
    ).fetchone()
    if not row or not row["expires_at"]:
        return None
    days_left = days_until(row["expires_at"])
    return f"It's due to expire on {row['expires_at']}, which is {days_left} day{'s' if days_left != 1 else ''} away"


def _still_open_items(conn, domain_id: int, start_str: str, end_str: str, blocklist_real_ips: set = frozenset()):
    """Still-open action items, one per category (same dedup as the
    dashboard's own list), each carrying as concrete a "detail" clause as we
    can honestly compute for THIS report's window -- real sample addresses
    for suppressions, a real count/rate for reputation issues. `detail` is
    None when we don't have enough to say something concrete without
    guessing (matches _resolved_items' "impact" discipline). `blocklist_real_ips`
    -- see _resolved_items() for why the "blocklist" category is filtered."""
    rows = conn.execute(
        """SELECT category, MAX(ref_key) as ref_key FROM action_items
           WHERE domain_id=? AND status='open' AND category IS NOT NULL
           GROUP BY category""",
        (domain_id,),
    ).fetchall()
    items = []
    for r in rows:
        category, ref_key = r["category"], r["ref_key"]
        if category in _OPERATOR_ONLY_CATEGORIES:
            continue
        if category == "blocklist" and ref_key not in blocklist_real_ips:
            continue

        if category in ("mailgun_new_suppressions", "ses_new_suppressions") and ref_key:
            table = "mailgun_suppressions" if category == "mailgun_new_suppressions" else "ses_suppressions"
            key_col = "mailgun_domain" if category == "mailgun_new_suppressions" else "configuration_set"
            emails = [x["email"] for x in conn.execute(
                f"""SELECT email FROM {table} WHERE domain_id=? AND {key_col}=? AND first_seen_at BETWEEN ? AND ?
                    ORDER BY first_seen_at""",
                (domain_id, ref_key, start_str, end_str),
            ).fetchall()]
            if emails:
                continue

        detail = None
        if category in (_MAILGUN_RATE_IMPACT_CATEGORIES | _SES_RATE_IMPACT_CATEGORIES) and ref_key:
            detail = _reputation_rate_detail(conn, category, ref_key, start_str, end_str)
        elif category == "domain_expiring_soon":
            detail = _domain_expiry_detail(conn, domain_id)
        story = _plain_problem(category)
        if category == "postmaster_compliance":
            story = _postmaster_story(conn, domain_id) or story
        items.append({"story": story, "detail": detail, "why": _why_it_matters(category)})
    return items


def _contact_aikyam_cta(still_open_categories: set):
    """An explicit, direct invitation to reach out -- shown only when
    something still open is serious enough (a reputation/blocklist/safety
    issue) that a non-technical reader shouldn't just sit with it."""
    if not (still_open_categories & _URGENT_STILL_OPEN_CATEGORIES):
        return None
    return ("If you're not sure how to fix what's above, reach out to aikyam directly. This is worth getting "
            "right, and we'll make sure it's solved properly.")


def _tips_for_domain(still_open_categories: set, consistency_irregular: bool) -> list:
    """0-4 concrete, plain-language tips keyed to whatever's actually open or
    at-risk for THIS domain -- never a static list every domain gets
    regardless of their real situation.

    Returns an empty list (and the templates then omit the whole section)
    when there's nothing specific to say. There used to be a _GENERIC_TIP
    fallback so the section always rendered, which contradicted the rule
    above: across the 9 live domains it produced a "tips for the next few
    weeks" heading over advice nobody had asked for and nothing had
    prompted. An empty section is more honest than filler, and it makes the
    times a tip DOES appear mean something."""
    tips = []
    for cat in still_open_categories:
        tip = _TIP_LIBRARY.get(cat)
        if tip and tip not in tips:
            tips.append(tip)
    if consistency_irregular and _CONSISTENCY_TIP not in tips:
        tips.append(_CONSISTENCY_TIP)
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


_POSTMASTER_REQUIREMENT_STORY = {
    # Phrased as clauses that read naturally after "Google told us ..." --
    # so none of them may start with "Google" or the sentence doubles it up.
    "SPF_AND_DKIM": ("it couldn't always confirm your emails were signed by you -- the proof that a message really "
                      "came from your domain"),
    "DMARC_ALIGNMENT": ("the \"from\" address on some of your mail didn't line up with the domain that actually "
                         "sent it, which makes it look less trustworthy"),
    "ENCRYPTION": "some of your mail travelled without encryption, which it now expects for every message",
    "DNS_RECORDS": ("one of the computers sending your mail isn't properly labelled on the internet, so it can't "
                     "confirm the sender is legitimate"),
    "ONE_CLICK_UNSUBSCRIBE": ("your newsletters were missing the one-click unsubscribe button it now requires of "
                               "anyone sending in volume"),
    "HONOR_UNSUBSCRIBE": "it wasn't satisfied that unsubscribe requests were being acted on quickly enough",
    "SPAM_RATE": ("more of your recipients marked your mail as spam than its guidance allows -- the one number "
                   "that most directly decides whether you reach the inbox"),
    "DELIVERABILITY": ("it isn't yet confident about your mail overall, most often because it simply hasn't seen "
                        "enough steady sending from your domain to judge it yet"),
}


def _list_hygiene(conn, domain_id: int, start_str: str, end_str: str):
    """Addresses that stopped accepting mail in this window, across both
    Mailgun and SES, as one plain story. Its own section (see _resolved_items
    for why) -- routine housekeeping, framed as such rather than as a
    problem or a fix."""
    emails = []
    for table in ("mailgun_suppressions", "ses_suppressions"):
        emails += [x["email"] for x in conn.execute(
            f"""SELECT email FROM {table} WHERE domain_id=? AND first_seen_at BETWEEN ? AND ?
                ORDER BY first_seen_at""",
            (domain_id, start_str, end_str),
        ).fetchall()]
    if not emails:
        return None
    return _suppression_story(len(emails), emails)


def _postmaster_story(conn, domain_id: int):
    """Names the specific Google requirement rather than saying "something
    needed attention". The requirement is in the action item's ref_key as
    "<postmaster_domain>:<REQUIREMENT>"."""
    row = conn.execute(
        """SELECT ref_key FROM action_items WHERE domain_id=? AND category='postmaster_compliance'
           AND status='open' ORDER BY updated_at DESC LIMIT 1""",
        (domain_id,),
    ).fetchone()
    if not row or not row["ref_key"] or ":" not in row["ref_key"]:
        return None
    requirement = row["ref_key"].split(":", 1)[1]
    story = _POSTMASTER_REQUIREMENT_STORY.get(requirement)
    return f"Google told us {story}" if story else None


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


def _blocklist_split(conn, domain_id: int, domain_name: str, start_str: str, end_str: str):
    """Every distinct IP flagged on a public blocklist for this domain that's
    either still open or was resolved during this window, split by
    guess_sender_identity()'s verdict. A one-off spoofing attempt that was
    already on a public blocklist is good news worth celebrating -- DMARC
    protection working exactly as intended, with nothing to do with this
    domain's own sending setup -- so it's treated completely differently
    from an IP that might genuinely be this domain's own infrastructure,
    which still needs the reader's attention as a real problem. Returns
    (good_news_story_or_None, {ref_keys that are real concerns, not spoofers}).
    skip_lookup=True throughout -- this only ever runs from a report render
    (background job or live preview/test-send), never triggering a fresh
    WHOIS call; it just reads whatever the background blocklist check has
    already cached."""
    rows = conn.execute(
        """SELECT DISTINCT ref_key FROM action_items
           WHERE domain_id=? AND category='blocklist' AND ref_key IS NOT NULL
             AND (status='open' OR (status IN ('done','dismissed') AND resolved_at BETWEEN ? AND ?))""",
        (domain_id, start_str, end_str),
    ).fetchall()
    good_ips, real_ips = [], []
    for r in rows:
        ip = r["ref_key"]
        verdict = guess_sender_identity(conn, domain_id, domain_name, ip, skip_lookup=True)["verdict"]
        (good_ips if verdict == "not_yours" else real_ips).append(ip)

    good_story = None
    if good_ips:
        n = len(good_ips)
        sample_orgs = [org for ip in good_ips[:2] if (org := cached_whois_org(conn, ip, allow_live=False))]
        example = f", for example one came from \"{sample_orgs[0]}\"" if sample_orgs else ""
        good_story = (
            f"We caught {n} attempt{'s' if n != 1 else ''} to send fake email pretending to be your "
            f"organization{example}. Each one was already on a public \"known troublemaker\" list and came from "
            f"an ordinary home/business internet connection, not any platform you actually use. It was never "
            f"a real risk to your reputation. This is exactly what your email protection is for."
        )
    return good_story, set(real_ips)


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
    """Returns {"whats_working": [str, ...], "resolved": [{"story","impact"}, ...],
    "still_open": [{"story","detail"}, ...],
    "deliverability": str, "protection": str, "newsletter": str|None,
    "blocklist_good_news": str|None, "protection_tightened": str|None,
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

    blocklist_good_news, blocklist_real_ips = _blocklist_split(conn, domain_id, domain_name, start_str, end_str)
    still_open_categories = _still_open_categories(conn, domain_id, blocklist_real_ips)
    still_open = _still_open_items(conn, domain_id, start_str, end_str, blocklist_real_ips)
    resolved = _resolved_items(conn, domain_id, start_str, end_str, blocklist_real_ips,
                                exclude_categories=still_open_categories)

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

    headline = _headline_verdict(conn, domain_id, still_open_categories, _risk_warning(conn, domain_id, period_end))
    whats_working = _whats_working(conn, domain_id, still_open_categories, rate, total, period_end)
    health_trend = _health_trend(conn, domain_id, period_start)
    list_hygiene = _list_hygiene(conn, domain_id, start_str, end_str)
    protection_tightened = _protection_tightened(conn, domain_id, period_start)
    spam_trend = _spam_rate_trend(conn, domain_id, period_end)
    risk_warning = _risk_warning(conn, domain_id, period_end)
    comparison = _health_comparison(conn, domain_id)
    contact_cta = _contact_aikyam_cta(still_open_categories)
    cadence = sending_cadence(conn, domain_id)
    tips = _tips_for_domain(still_open_categories, cadence["irregular"])

    return {
        "headline": headline,
        "whats_working": whats_working,
        "health_trend": health_trend,
        "list_hygiene": list_hygiene,
        "resolved": resolved,
        "still_open": still_open,
        "deliverability": deliverability,
        "protection": protection,
        "newsletter": newsletter,
        "blocklist_good_news": blocklist_good_news,
        "protection_tightened": protection_tightened,
        "spam_trend": spam_trend,
        "risk_warning": risk_warning,
        "comparison": comparison,
        "contact_cta": contact_cta,
        "tips": tips,
    }


def report_period_for_domain(conn, domain_id: int):
    """Public wrapper around _report_period for callers outside this module
    (e.g. the interactive client-view route in web.py) that need the exact
    same period a real/preview send would use, without reaching into a
    private helper."""
    row = get_report_settings(conn, domain_id)
    return _report_period(row, datetime.datetime.utcnow())


# What each kind of problem actually threatens, in the terms this audience
# cares about: their donors, their funders, and whether their impact stories
# get read. The whole reason a grassroots org needs to care about domain
# health is that a custom email address is what makes a funder trust them --
# and an unmaintained one is what lets someone scam a donor in their name, or
# quietly drops their newsletter into a funder's spam folder. Stating the
# stake in those words is what turns a technical finding into something worth
# reading. Kept short: one clause, appended to the story, never a lecture.
_WHY_IT_MATTERS = {
    "dns_drift": "Left alone, this is what lets someone send a donation appeal that looks exactly like it came from you.",
    "dns_policy_weakened": "This is the protection that stops a stranger emailing your donors in your name, so we don't let it slip.",
    "dns_missing": "Without it, anyone can put your organization's name on an email asking your supporters for money.",
    "spf_missing": "This is part of what proves an email really came from you and not from someone imitating you.",
    "dkim_missing": "This is the signature that lets a funder's mail system confirm your message is genuinely yours.",
    "blocklist": "While that lasts, your updates can go straight to a supporter's spam folder instead of their inbox.",
    "ptr_issue": "Some mail systems quietly distrust mail from an unlabelled computer, so messages can be filtered out.",
    "mailgun_reputation": "If it keeps climbing, mailbox providers start sending your newsletter to spam instead of the inbox.",
    "ses_reputation": "If it keeps climbing, mailbox providers start sending your newsletter to spam instead of the inbox.",
    "ses_reputation_watch": "Worth catching early, because once providers lose confidence it takes a while to earn back.",
    "ses_rejected": "A blocked message never reaches anyone at all, so it's worth understanding why.",
    "new_sender": "We check these so nobody can quietly use your organization's name to email your supporters.",
    "failure_investigation": "We check these so nobody can quietly use your organization's name to email your supporters.",
    "borrowed_sending_identity": "Worth sorting out so your mail is clearly recognisable as yours.",
    "safe_browsing_flagged": "Visitors get a red warning screen before they reach your site, which costs you trust and donations.",
    "postmaster_compliance": "This comes straight from Google, so it is worth settling before it affects the inbox.",
    "display_name_issue": "The \"from\" name is the first thing a reader sees, and it decides whether they trust the email.",
    "content_spam_risk": "Wording alone can be enough to land a newsletter in spam rather than the inbox.",
    "subject_spam_risk": "Wording alone can be enough to land a newsletter in spam rather than the inbox.",
    "sending_cadence_irregular": "A steady rhythm is part of how mailbox providers decide to trust your mail.",
    "domain_expiring_soon": "If a domain lapses, your website and every email address on it stop working the same day.",
}


def _why_it_matters(category):
    return _WHY_IT_MATTERS.get(category)


def _health_trend(conn, domain_id: int, period_start):
    """This domain's OWN health score now vs. around the last report -- the
    single most reassuring number available, and the thing a "did it get
    better?" question actually needs. Distinct from _health_comparison(),
    which ranks against other orgs; a peer ranking can't tell you whether
    YOUR month went well. Returns None rather than inventing a trend when
    there isn't enough history yet."""
    latest = conn.execute(
        """SELECT snapshot_date, health_score FROM domain_health_snapshots
           WHERE domain_id=? AND health_score IS NOT NULL
           ORDER BY snapshot_date DESC LIMIT 1""",
        (domain_id,),
    ).fetchone()
    if not latest:
        return None
    prior = conn.execute(
        """SELECT health_score FROM domain_health_snapshots
           WHERE domain_id=? AND health_score IS NOT NULL AND snapshot_date <= ?
           ORDER BY snapshot_date DESC LIMIT 1""",
        (domain_id, period_start.date().isoformat()),
    ).fetchone()

    score = round(latest["health_score"])
    band = ("in good shape" if score >= 80 else
            "holding steady, with room to improve" if score >= 50 else
            "needs some work, and we're on it")
    if not prior or prior["health_score"] is None:
        return (f"Overall, your email health is {band} -- we score it {score} out of 100. "
                f"We'll show you how this moves each time, so you can see progress rather than take our word for it.")

    before = round(prior["health_score"])
    delta = score - before
    if delta >= 3:
        return (f"Your overall email health has improved since the last update -- from {before} to {score} out of "
                f"100. That's real progress, and it's the direct result of the fixes below.")
    if delta <= -3:
        return (f"Your overall email health has slipped a little since the last update -- from {before} to {score} "
                f"out of 100. Nothing here is alarming, and the items below are what we're working through.")
    return (f"Your overall email health is steady at {score} out of 100, about the same as last time -- which is "
            f"exactly what you want between updates.")


def _whats_working(conn, domain_id: int, still_open_categories: set, rate, total, now: datetime.datetime) -> list:
    """The report's opening section: the things that are demonstrably going
    RIGHT for this domain, each stated as a fact plus what it buys them.

    This replaces an opening paragraph that told the reader their domain was
    "being looked after" and "in good hands". That was reassurance asserted
    rather than shown -- and, as the user put it, the rest of the email
    already makes them feel it, so saying it outright read as padding. The
    stakes this audience actually has (see the module docstring: a funder
    trusting their address, a donor not being scammed in their name, an
    impact story not landing in spam) are far better served by "here is
    what's protecting you right now" than by "don't worry".

    Every item is gated on real stored data AND on the matching problem not
    being open, so this section can never congratulate the domain on
    something a later section is simultaneously reporting as broken -- the
    same self-contradiction trap that _resolved_items/exclude_categories
    exists to prevent. Returns [] when nothing qualifies (a brand-new domain
    with no data yet), and the templates then omit the heading entirely.
    """
    working = []

    # 1. Impersonation protection. The single most valuable thing this tool
    #    does for them, and the one with the most concrete stake.
    policy_run = current_policy_run(conn, domain_id)
    protection_broken = still_open_categories & {
        "dns_policy_weakened", "dns_missing", "dns_drift", "spf_missing", "dkim_missing",
    }
    if policy_run and policy_run["p"] and policy_run["p"] != "none" and not protection_broken:
        working.append(
            "The protection that stops anyone sending email pretending to be your organization is switched on "
            "and working. If someone tried to email your donors using your name, mailbox providers would "
            "catch it rather than deliver it."
        )

    # 2. Their own mail being recognised as genuinely theirs. Deliberately
    #    framed as recognition/trust, not arrival -- the deliverability
    #    section further down already gives the arrival number, and repeating
    #    it here would just read as the same fact twice.
    auth_broken = still_open_categories & {
        "failure_investigation", "borrowed_sending_identity", "ptr_issue",
        # Reputation problems belong here too. On a live domain this claim
        # rendered two sections above "9 of the 31 emails you sent bounced,
        # roughly 29%". Technically about different mechanisms; to the reader,
        # a flat contradiction.
        "mailgun_reputation", "ses_reputation", "ses_reputation_watch", "ses_rejected",
    }
    if total and total >= 50 and rate is not None and rate >= 0.98 and not auth_broken:
        working.append(
            "Your own emails are being recognised as genuinely yours, so the messages you send to funders and "
            "supporters arrive looking trustworthy rather than suspicious."
        )

    # 3. Registration paid up. Unglamorous, but it's the one failure that
    #    takes the website and every email address down at the same moment,
    #    and the one thing on this list only they can act on.
    if "domain_expiring_soon" not in still_open_categories:
        exp = conn.execute(
            "SELECT expires_at FROM domain_expiry_checks WHERE domain_id=? ORDER BY checked_at DESC LIMIT 1",
            (domain_id,),
        ).fetchone()
        if exp and exp["expires_at"]:
            days_left = days_until(exp["expires_at"])
            # Needs real headroom, not just "hasn't tripped the warning yet".
            # At 38 days out (one live domain, today) "your domain is paid up"
            # is technically true and practically misleading -- renewal is a
            # month away. Twice the warning threshold means this only ever
            # reads as genuine good news, and a domain in the gap between the
            # two simply doesn't get the line.
            try:
                warn_days = int(ensure_default_settings(conn).get("domain_expiry_warn_days", 30))
            except (TypeError, ValueError):
                warn_days = 30
            comfortable = warn_days * 2
            if days_left is not None and days_left > comfortable:
                working.append(
                    f"Your domain name is paid up until {exp['expires_at']}, so your website and every email "
                    f"address on it keep working. We're watching the date and will remind you in good time."
                )

    return working


def _headline_verdict(conn, domain_id: int, still_open_categories: set, risk_warning):
    """One short, factual line orienting the reader before the detail: is
    there something here they need to know about, or not.

    Deliberately terse. This used to be a three-clause paragraph asserting
    the domain was "being looked after" / "in good hands" -- reassurance
    stated outright, which the user found read as padding on top of an email
    whose whole tone already conveys it. Everything worth saying about how
    safe they are is now shown as evidence in _whats_working() instead.
    Returns None where a sentence would add nothing at all: with no problems
    to flag, the "what's already working" list is a better opening than any
    summary of it could be."""
    if still_open_categories & _URGENT_STILL_OPEN_CATEGORIES or risk_warning:
        return "There's one thing on this update we want to flag for you, and it's explained below."
    if still_open_categories:
        return "Nothing on your domain needs your attention this time."
    return None


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
    settings = ensure_default_settings(conn)
    if settings.get("report_emails_enabled", "0") != "1":
        if verbose:
            print("[domain_report] report_emails_enabled is off in Settings -- not sending "
                  "(the 'send test now' button still works)")
        return

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
