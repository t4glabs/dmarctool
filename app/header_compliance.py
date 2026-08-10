"""
Checks against the RFC 5322 / message-formatting requirements and the
mandatory one-click-unsubscribe requirement from Gmail's sender guidelines
(https://support.google.com/mail/answer/81126, "Requirements for sending
5,000 or more messages per day" and "Message formatting guidelines").

Built from the same raw message headers already captured per-campaign via
the SES event pipeline -- DMARC reports carry none of this.
"""


def check_unsubscribe_compliance(list_unsubscribe: str, list_unsubscribe_post: str) -> list:
    """Google requires (for 5,000+/day senders) a working one-click
    unsubscribe: a List-Unsubscribe header with a usable link, plus
    List-Unsubscribe-Post: List-Unsubscribe=One-Click exactly."""
    issues = []
    if not list_unsubscribe:
        issues.append("Missing List-Unsubscribe header -- required for bulk senders (5,000+/day)")
    elif "https://" not in list_unsubscribe and "mailto:" not in list_unsubscribe:
        issues.append("List-Unsubscribe header doesn't contain a usable https:// or mailto: link")

    if not list_unsubscribe_post:
        issues.append("Missing List-Unsubscribe-Post header -- required for one-click unsubscribe")
    elif list_unsubscribe_post.strip() != "List-Unsubscribe=One-Click":
        issues.append(f'List-Unsubscribe-Post value looks wrong: "{list_unsubscribe_post}" (should be exactly "List-Unsubscribe=One-Click")')

    return issues


def check_header_hygiene(message_id: str, subject: str) -> list:
    """RFC 5322 / Gmail formatting basics: a Message-ID must exist, and a
    newsletter shouldn't masquerade as a reply/forward."""
    issues = []
    if not message_id:
        issues.append("Missing Message-ID header")
    if subject and subject.strip().lower().startswith(("re:", "fwd:", "fw:")):
        issues.append('Subject starts with "Re:"/"Fwd:" but this is a newsletter, not an actual reply/forward -- Gmail explicitly flags this as misleading')
    return issues
