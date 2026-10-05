"""
On-demand "notify the client to clean their list" emails.

Distinct from app.domain_report's scheduled periodic reports: this is a
manual, button-triggered send for exactly one real workflow the user already
does by hand -- when a domain's bounce rate/pass rate looks off, check which
addresses are genuinely dead (hard bounces, chronic stuck bounces), and email
that list to whoever manages the domain's own mailing list (Ghost members,
Listmonk, a spreadsheet), asking them to remove it. DMARCTool never touches
the client's own list directly -- only tells them what to remove and waits
for them to confirm.

Three real categories, matching the user's own description of what's "very
correct" to hand over:
  - Spam complaints: addresses that marked a message from this domain as
    spam (SES/Mailgun). As certain as a hard bounce (the provider told us
    directly) and arguably more urgent -- continuing to send to them doesn't
    just waste effort, it actively damages reputation with every other
    provider watching this domain. Scoped to what's NEW since the last
    successful notification, same watermark as hard bounces below.
  - Hard bounces: app.bounce_reasons.PERMANENT_CATEGORIES -- addresses that
    no longer exist or are permanently disabled. Scoped to what's NEW since
    the last successful notification (bounce_notification_sends' own
    watermark, independent of the dashboard's own mark-done watermark --
    marking the dashboard reminder done doesn't mean the client has actually
    removed anything yet, so the two must not share one cutoff).
  - Chronic transient bounces (app.chronic_bounces): always the CURRENT full
    list, same "no new/full split" reasoning as that module's own CSV
    export -- it's a standing state, not a discrete event stream.
"""

import csv
import datetime
import email.utils
import io

from app.bounce_reasons import categorize_bounce, PERMANENT_CATEGORIES
from app.chronic_bounces import chronic_transient_bounces


def _clean_email(raw: str) -> str:
    return email.utils.parseaddr(raw or "")[1] or raw


def _last_notified_at(conn, domain_id: int):
    row = conn.execute(
        """SELECT sent_at FROM bounce_notification_sends
           WHERE domain_id=? AND status='sent' ORDER BY sent_at DESC LIMIT 1""",
        (domain_id,),
    ).fetchone()
    return row["sent_at"] if row else None


def _hard_bounce_rows(conn, domain_id: int, since: str = None):
    """New (since the watermark) addresses in a PERMANENT_CATEGORIES bounce
    category, combined across Mailgun and SES -- [{"email", "category",
    "reason", "first_seen"}, ...]. `since=None` means "everything ever" (the
    very first notification for a domain)."""
    rows = []
    for r in conn.execute(
        """SELECT email, reason, first_seen_at FROM mailgun_suppressions
           WHERE domain_id=? AND kind='bounce' AND (? IS NULL OR first_seen_at > ?)""",
        (domain_id, since, since),
    ):
        category = categorize_bounce(r["reason"], None)
        if category in PERMANENT_CATEGORIES:
            rows.append({"email": _clean_email(r["email"]), "category": category,
                         "reason": r["reason"] or "", "first_seen": r["first_seen_at"]})
    for r in conn.execute(
        """SELECT email, reason, bounce_type, first_seen_at FROM ses_suppressions
           WHERE domain_id=? AND kind='bounce' AND (? IS NULL OR first_seen_at > ?)""",
        (domain_id, since, since),
    ):
        category = categorize_bounce(r["reason"], r["bounce_type"])
        if category in PERMANENT_CATEGORIES:
            rows.append({"email": _clean_email(r["email"]), "category": category,
                         "reason": r["reason"] or "", "first_seen": r["first_seen_at"]})
    rows.sort(key=lambda x: x["email"])
    return rows


def _spam_complaint_rows(conn, domain_id: int, since: str = None):
    """New (since the watermark) addresses that marked a message from this
    domain as spam, combined across Mailgun and SES -- [{"email", "reason",
    "first_seen"}, ...]. `since=None` means "everything ever" (the very
    first notification for a domain), same convention as _hard_bounce_rows."""
    rows = []
    for r in conn.execute(
        """SELECT email, reason, first_seen_at FROM mailgun_suppressions
           WHERE domain_id=? AND kind='complaint' AND (? IS NULL OR first_seen_at > ?)""",
        (domain_id, since, since),
    ):
        rows.append({"email": _clean_email(r["email"]), "reason": r["reason"] or "",
                      "first_seen": r["first_seen_at"]})
    for r in conn.execute(
        """SELECT email, reason, first_seen_at FROM ses_suppressions
           WHERE domain_id=? AND kind='complaint' AND (? IS NULL OR first_seen_at > ?)""",
        (domain_id, since, since),
    ):
        rows.append({"email": _clean_email(r["email"]), "reason": r["reason"] or "",
                      "first_seen": r["first_seen_at"]})
    rows.sort(key=lambda x: x["email"])
    return rows


def pending_notify_counts(conn, domain_id: int, min_occurrences: int, min_days: int):
    """(hard_count, chronic_count, spam_count) -- cheap enough to compute on
    every domain page load (small per-domain row counts), used to decide
    whether to show the "Notify client" button at all and what count to put
    on it."""
    since = _last_notified_at(conn, domain_id)
    hard_count = len(_hard_bounce_rows(conn, domain_id, since))
    chronic_count = len(chronic_transient_bounces(conn, domain_id, min_occurrences, min_days))
    spam_count = len(_spam_complaint_rows(conn, domain_id, since))
    return hard_count, chronic_count, spam_count


def _csv_bytes(header: list, rows: list, row_fn) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    for row in rows:
        writer.writerow(row_fn(row))
    return buf.getvalue().encode()


def build_notification(conn, domain_id: int, domain_name: str, recipient_label: str,
                        min_occurrences: int, min_days: int, signoff_name: str = None):
    """Returns (subject, text_body, attachments, hard_count, chronic_count,
    spam_count), or None if there's nothing new/pending to notify about (all
    three counts zero) -- callers must treat None as "don't send", not as an
    error. `attachments` is a list of (filename, bytes, content_type) tuples,
    ready for app.mailgun.send_message()'s list-of-attachments support.
    Hard-bounce/chronic content (subject/body) is Jev-validated wording
    (jev/DECISIONS_LOG.md Ch.40), not invented fresh here without review --
    see that chapter before changing that copy. The spam-complaint category
    (Ch.50) reuses the same confidence level as hard bounces -- a complaint
    is as certain a signal as a bounce, just framed around reputation harm
    instead of non-delivery -- and is listed first since it's the most
    urgent of the three (every day left on the list actively hurts
    reputation, not just wasted send effort). `signoff_name` should be the
    SAME `settings["report_signoff_name"]` the periodic report closes with
    ("With care, {signoff_name}") -- caught missing entirely on the first
    version of this feature (Ch.41): every other outbound email from this
    tool ends the same way, this one just forgot to."""
    since = _last_notified_at(conn, domain_id)
    spam_rows = _spam_complaint_rows(conn, domain_id, since)
    hard_rows = _hard_bounce_rows(conn, domain_id, since)
    chronic_rows = chronic_transient_bounces(conn, domain_id, min_occurrences, min_days)
    spam_count, hard_count, chronic_count = len(spam_rows), len(hard_rows), len(chronic_rows)
    if not spam_count and not hard_count and not chronic_count:
        return None

    greeting = recipient_label or domain_name
    attachments = []
    body_parts = [f"Hi {greeting},", ""]

    if spam_count:
        body_parts += [
            f"{spam_count} address{'es' if spam_count != 1 else ''} on your list marked a recent email from "
            f"{domain_name} as spam. Mailbox providers watch this closely -- the more people do this, the "
            "more of your other emails start landing in spam instead of the inbox for everyone else on your "
            "list too. Removing them is the single best thing you can do to protect your reputation.",
            "",
        ]
    if hard_count or chronic_count:
        body_parts += [
            f"We also found some email addresses that have stopped accepting mail from {domain_name}. "
            "Repeatedly sending to dead addresses is one of the biggest reasons mailbox providers like "
            "Gmail start treating a sender as spammy -- removing them protects your reputation and keeps "
            "your real emails landing in the inbox.",
            "",
        ]

    # Wording for hard/chronic Jev-tested (jev workflow Ch.40): multiple
    # rounds against audience_fit/actionability/natural_voice/
    # honesty_calibration on real pattic.org numbers (327 hard, 36 chronic).
    # Deliberately does NOT treat all files as equally urgent -- user's own
    # real concern, raised after the first draft: recipients are
    # non-technical and may feel like they're "deleting their own members,"
    # so the confirmed files (spam, hard bounce) need to read as confidently
    # safe (no hedging) while the chronic file is explicitly framed as less
    # certain and optional to double-check, not "also just delete these."
    # "no longer real" tested meaningfully clearer than "no longer exist"/
    # "permanently stopped accepting mail" in isolation. audience_fit stayed
    # the weakest criterion throughout (peaked ~43% clear_as_is combined)
    # despite several genuine rewrites -- shipped as the best found, not
    # force-fixed further; flagged honestly rather than claimed clean.
    present = sum(1 for c in (spam_count, hard_count, chronic_count) if c)
    if present > 1:
        word = {2: "two", 3: "three"}[present]
        body_parts.append(f"We've split this into {word} files, since not all of them are equally certain:")
        body_parts.append("")
    n = 0
    if spam_count:
        n += 1
        attachments.append((
            f"{domain_name}-spam-complaints.csv",
            _csv_bytes(
                ["email", "reason", "first_seen"], spam_rows,
                lambda r: [r["email"], r["reason"], r["first_seen"][:10]],
            ),
            "text/csv",
        ))
        label = f"{n}. " if present > 1 else ""
        body_parts.append(
            f"{label}spam-complaints.csv -- {spam_count} address{'es' if spam_count != 1 else ''} that "
            f"marked your mail as spam. Safe and worth removing right away."
        )
    if hard_count:
        n += 1
        attachments.append((
            f"{domain_name}-remove-these-addresses.csv",
            _csv_bytes(
                ["email", "reason", "first_seen"], hard_rows,
                lambda r: [r["email"], r["category"], r["first_seen"][:10]],
            ),
            "text/csv",
        ))
        label = f"{n}. " if present > 1 else ""
        body_parts.append(
            f"{label}remove-these-addresses.csv -- {hard_count} address{'es' if hard_count != 1 else ''} "
            f"that {'are' if hard_count != 1 else 'is'} confirmed gone or permanently broken. Safe to "
            "delete right away."
        )
    if chronic_count:
        n += 1
        attachments.append((
            f"{domain_name}-stuck-addresses.csv",
            _csv_bytes(
                # chronic_transient_bounces() returns the raw stored email --
                # sometimes a full "Display Name <addr>" string straight from
                # the original send's To: header, same as mailgun/ses
                # suppressions -- needs _clean_email() too (the existing
                # suppressions_new.csv export already does this for its own
                # chronic rows; missed here on the first pass, caught by
                # actually reading the generated CSV content, not assumed).
                ["email", "first_seen", "last_seen"], chronic_rows,
                lambda r: [_clean_email(r["email"]), r["first_seen"][:10], r["last_seen"][:10]],
            ),
            "text/csv",
        ))
        label = f"{n}. " if present > 1 else ""
        body_parts.append(
            f"{label}stuck-addresses.csv -- {chronic_count} address{'es' if chronic_count != 1 else ''} that "
            f"{'have' if chronic_count != 1 else 'has'} been failing quietly for weeks, without a final "
            f"bounce to confirm it. These are very likely dead too, but since we cannot be fully sure, "
            "feel free to check them yourself before removing anyone."
        )

    body_parts += [
        "",
        "Once you have removed what you're comfortable with, just reply and let us know -- we will mark "
        "this as done on our end.",
        "",
        "With care,",
        signoff_name or "The aikyam Team",
    ]
    subject = f"A few addresses to remove from your {domain_name} mailing list"
    text_body = "\n".join(body_parts)
    return subject, text_body, attachments, hard_count, chronic_count, spam_count


def record_notification_sent(conn, domain_id: int, recipient_email: str, hard_count: int, chronic_count: int,
                              status: str, error_message: str = None, spam_count: int = 0):
    conn.execute(
        """INSERT INTO bounce_notification_sends
           (domain_id, recipient_email, hard_bounce_count, chronic_count, spam_complaint_count, status, error_message)
           VALUES (?,?,?,?,?,?,?)""",
        (domain_id, recipient_email, hard_count, chronic_count, spam_count, status, error_message),
    )
    conn.commit()


def last_notification(conn, domain_id: int):
    """Most recent send attempt (any status), for display -- or None if
    this domain has never been notified."""
    row = conn.execute(
        """SELECT sent_at, recipient_email, hard_bounce_count, chronic_count, spam_complaint_count, status, error_message
           FROM bounce_notification_sends WHERE domain_id=? ORDER BY sent_at DESC LIMIT 1""",
        (domain_id,),
    ).fetchone()
    return dict(row) if row else None
