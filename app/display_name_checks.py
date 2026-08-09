"""
Checks against Gmail's "Guidelines for email display names"
(https://support.google.com/mail/answer/81126): a display name should be a
consistent, accurate statement of the sender's identity -- not subject-line
content, not an attempt to imitate a verification badge, thread reply, or
another domain's identity.

DMARC aggregate reports carry none of this -- they're domain-level only, no
display name, subject, or content. This only became checkable once real
message headers (mail.commonHeaders.from) started flowing through the SES
event pipeline built for per-newsletter engagement stats.
"""

import re

_URGENCY_PHRASES = (
    "urgent", "last chance", "act now", "final notice", "final warning",
    "immediate action", "final reminder", "expires today", "don't miss",
)
_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF❤✅✔]"
)
_REPLY_COUNT_RE = re.compile(r"\(\d+\)\s*$")


def check_display_name(display_name: str, from_address: str = None) -> list:
    """Returns a list of plain-language issue strings for one display name;
    empty if nothing looks wrong. Best-effort heuristics, not a certainty --
    always let a human glance at the actual name."""
    if not display_name:
        return []

    issues = []
    lower = display_name.lower()

    if any(p in lower for p in _URGENCY_PHRASES):
        issues.append("Reads like subject-line/urgency content, not a sender name (Gmail: \"don't include subject or message content in display names\")")
    if display_name.isupper() and " " in display_name.strip() and len(display_name) > 3:
        # a single all-caps word is a perfectly normal short brand name/acronym
        # (e.g. "PATTIC", "IBM") -- only a multi-word all-caps *phrase* reads
        # as shouting/subject content.
        issues.append("ALL CAPS phrase -- looks like shouting/subject content rather than a sender name")
    if _EMOJI_RE.search(display_name):
        issues.append("Contains emoji -- can read as an attempt to imitate a verification badge")
    if _REPLY_COUNT_RE.search(display_name):
        issues.append("Ends in a \"(N)\" pattern -- can look like a threaded-reply count")
    if "gmail.com" in lower and from_address and not from_address.lower().endswith("@gmail.com"):
        issues.append("Display name references gmail.com but the sending address doesn't -- looks like impersonation")

    return issues


def display_name_consistency(campaigns: list) -> list:
    """campaigns: list of dicts with a 'from_display_name' key (as returned by
    analysis.recent_campaigns). Returns the distinct non-None display names
    used, most-recent-first order preserved -- more than one means the
    sender identity isn't consistent across newsletters, which Gmail's
    guidelines call out explicitly ("a consistent, clear, and accurate
    statement of the sender's identity")."""
    seen = []
    for c in campaigns:
        name = c.get("from_display_name")
        if name and name not in seen:
            seen.append(name)
    return seen
