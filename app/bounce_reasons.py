"""
Best-effort plain-language categorization of raw SMTP/ESP bounce reason text.

Designed directly against this project's own stored bounce reasons, which
turned out far messier than a simple "contains the words 'mailbox full'"
check can handle:

  - Amazon SES wraps the real reason inside a generic "Message expired:
    unable to deliver in 840 minutes.<...>" envelope, with the receiving
    server's actual reply nested inside the angle brackets -- e.g.
    "smtp; 550 4.4.7 Message expired...<452-4.2.2 The recipient's inbox
    is out of storage space...>" is really a mailbox-full bounce, not a
    generic timeout, even though the outer code (4.4.7) says "expired".
  - Some SES bounces aren't real SMTP bounces from the recipient's server
    at all -- they're SES's own pre-emptive suppression-list or
    address-validation blocks, worth calling out as a distinct category
    since they say nothing about the recipient's actual mailbox.
  - Yahoo/AOL use their own "NNN.NN" style codes (e.g. "554.30") instead
    of RFC 3463's dotted x.y.z enhanced status codes, and Gmail sometimes
    just reports "recipient rejected your message" in a specific-enough
    phrase that trusting the words is more reliable than trusting a coded
    status a particular mail server may be using loosely (e.g. one real
    STARTTLS-related rejection was coded 5.1.1, which technically means
    "bad destination mailbox" -- the code would have mis-categorized it).

Given that, phrase matching runs before code matching, and code matching
prefers the *last* enhanced-status code found in the text (the nested,
specific one) over the first (SES's own generic wrapper code).
"""

import re

_ENHANCED_STATUS_RE = re.compile(r"(?<!\d)([245])\.(\d{1,3})\.(\d{1,3})(?!\d)")

# RFC 3463 (subject, detail) -> plain-language category. The class digit
# (2/4/5) distinguishes success/transient/permanent but doesn't change the
# category itself, so it isn't part of the lookup key.
_SUBJECT_DETAIL_CATEGORY = {
    (1, 1): "No such user / invalid address",
    (1, 2): "No such domain",
    (1, 3): "Bad address syntax",
    (1, 6): "Mailbox has moved, no forwarding address",
    (1, 10): "No such user / invalid address",
    (2, 0): "Mailbox disabled/unavailable",
    (2, 1): "Mailbox disabled/unavailable",
    (2, 2): "Mailbox full or over quota",
    (2, 3): "Message too large for mailbox",
    (2, 4): "Mailing list expansion problem",
    (3, 0): "Receiving mail system problem",
    (4, 0): "Network or routing problem",
    (4, 1): "No answer from recipient's mail server",
    (4, 4): "Temporary delivery failure / timed out",
    (4, 7): "Delivery time expired (retries exhausted)",
    (5, 0): "SMTP protocol error",
    (5, 1): "STARTTLS/TLS required by recipient server",
    (6, 0): "Message content problem",
    (7, 1): "Rejected/blocked by recipient's mail server",
    (7, 5): "Message integrity/authentication failure",
    (7, 7): "Message loop detected",
}

_SES_INTERNAL_PATTERNS = [
    ("suppression list for your account", "Suppressed pre-emptively by SES (not a real mailbox bounce)"),
    ("email validation", "Suppressed pre-emptively by SES (address-quality check)"),
]

# Mailgun's own equivalent of the SES-internal patterns above -- these mean
# Mailgun already knew the address was dead and refused to even attempt
# delivery, which is a materially different, more actionable situation than
# a fresh bounce: it means the *sending list itself* still has this address
# on it somewhere upstream (Listmonk, a CRM, whatever generates the send),
# not that the recipient's mail server just rejected this one message.
# Surfaced directly by a real case: a recipient kept reappearing across
# multiple check cycles under "Other permanent failure" -- once given its
# own category, it read immediately as "this address needs removing from
# wherever the list lives," not "investigate this bounce."
_MAILGUN_INTERNAL_PATTERNS = [
    ("previously bounced address", "Already suppressed by Mailgun (remove from your sending list, not a fresh bounce)"),
    ("not delivering to unsubscribed address", "Already suppressed by Mailgun (recipient unsubscribed)"),
]

# Checked before coded-status matching -- more reliable when a mail server
# reports something specific in plain text, and needed for formats (Yahoo/AOL's
# "NNN.NN", Gmail's plain English) that don't carry a proper RFC 3463 code.
_PHRASE_CATEGORIES = [
    (("no such user", "no mailbox here", "mailbox not found", "does not exist", "user unknown", "unknown user",
      "recipientnotfound", "not found by smtp address lookup", "doesn't have a"),
     "No such user / invalid address"),
    (("account closed",),
     "Mailbox disabled/unavailable"),
    (("does not support starttls", "starttls"),
     "STARTTLS/TLS required by recipient server"),
    (("mailbox is disabled", "account has been disabled", "account disabled"),
     "Mailbox disabled/unavailable"),
    (("mailbox unavailable", "mailbox not available"),
     "Mailbox disabled/unavailable"),
    (("over quota", "inbox is out of storage", "mailbox is full", "quota exceeded"),
     "Mailbox full or over quota"),
    (("invalid address", "invalid recipient"),
     "Bad address syntax"),
    (("recipient rejected", "blocked", "blacklist", "access denied", "recipient address rejected"),
     "Rejected/blocked by recipient's mail server"),
]


def categorize_bounce(reason: str, bounce_type: str = None) -> str:
    """Plain-language category for a raw bounce diagnostic string. Never
    discards the raw text -- callers should show this alongside it, not
    instead of it, since the category is a best-effort read, not a fact."""
    if not reason:
        return "Unknown (no diagnostic text captured)"

    lower = reason.lower()

    for needle, category in _SES_INTERNAL_PATTERNS:
        if needle in lower:
            return category

    for needle, category in _MAILGUN_INTERNAL_PATTERNS:
        if needle in lower:
            return category

    for phrases, category in _PHRASE_CATEGORIES:
        if any(p in lower for p in phrases):
            return category

    matches = _ENHANCED_STATUS_RE.findall(reason)
    if matches:
        _klass, subject, detail = matches[-1]  # prefer the nested/specific code over SES's generic outer wrapper
        category = _SUBJECT_DETAIL_CATEGORY.get((int(subject), int(detail)))
        if category:
            return category

    if bounce_type == "Permanent":
        return "Other permanent failure (see raw reason)"
    if bounce_type == "Transient":
        return "Other temporary failure (see raw reason)"
    return "Other / unrecognized"
