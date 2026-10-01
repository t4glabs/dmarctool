"""
On-demand "notify the client about inactive newsletter subscribers" email.

Same shape as app.bounce_notify (manual button, not scheduled; its own
independent send-history log/watermark, separate from the dashboard's own
"mark reviewed" state in subscriber_review_watermarks -- reviewing the
dashboard reminder and telling the client are two different real actions).

Deliberately a softer register than bounce_notify's "these are confirmed
dead, delete them" framing: an inactive subscriber is still a real person
who may simply have lost interest, not a broken address -- the email never
tells the client to remove anyone, only to decide.
"""

import csv
import io

from app.analysis import subscriber_engagement_summary


def _last_notified_at(conn, domain_id: int):
    row = conn.execute(
        """SELECT sent_at FROM subscriber_notification_sends
           WHERE domain_id=? AND status='sent' ORDER BY sent_at DESC LIMIT 1""",
        (domain_id,),
    ).fetchone()
    return row["sent_at"] if row else None


def pending_notify_count(conn, domain_id: int) -> int:
    """How many inactive subscribers are new since the last successful
    notification -- cheap enough to compute on every domain page load, used
    to decide whether to show the button at all."""
    since = _last_notified_at(conn, domain_id)
    engagement = subscriber_engagement_summary(conn, domain_id)
    if not since:
        return len(engagement["inactive_all"])
    return len([s for s in engagement["inactive_all"] if s["last_received"] > since])


def build_notification(conn, domain_id: int, domain_name: str, recipient_label: str, signoff_name: str = None):
    """Returns (subject, text_body, attachments, count), or None if nothing
    new to notify about. Wording Jev-tested (jev/DECISIONS_LOG.md Ch.42)
    against real aikyamjobs.org numbers (515 inactive of 4,120 subscribers)."""
    since = _last_notified_at(conn, domain_id)
    engagement = subscriber_engagement_summary(conn, domain_id)
    rows = engagement["inactive_all"] if not since else [
        s for s in engagement["inactive_all"] if s["last_received"] > since
    ]
    count = len(rows)
    if not count:
        return None

    threshold = engagement["threshold"]
    greeting = recipient_label or domain_name
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["email", "newsletters_received", "first_received", "last_received"])
    for s in rows:
        writer.writerow([s["email"], s["received"], s["first_received"][:10], s["last_received"][:10]])
    attachments = [(f"{domain_name}-inactive-subscribers.csv", buf.getvalue().encode(), "text/csv")]

    body = (
        f"Hi {greeting},\n\n"
        f"{count} people on your newsletter list have received {threshold} or more issues in a row "
        f"without ever opening one. There is no way to know for sure why -- maybe they moved on, maybe "
        f"they just skim it somewhere else -- so we are not removing anyone automatically, just letting "
        f"you know.\n\n"
        f"A list where a large share of people never engage can itself affect how mailbox providers "
        f"treat your sending; consistent low engagement is one of the signals they use to decide where "
        f"your mail lands.\n\n"
        f"The full list is attached. Take a look and decide what you would like to do -- try winning "
        f"some of them back with a re-engagement email, or remove them from your list. Either way, reply "
        f"once you have decided and we will mark this as handled on our end.\n\n"
        f"With care,\n"
        f"{signoff_name or 'The aikyam Team'}"
    )
    subject = f"Some newsletter subscribers who may have lost interest — {domain_name}"
    return subject, body, attachments, count


def record_notification_sent(conn, domain_id: int, recipient_email: str, count: int, status: str, error_message: str = None):
    conn.execute(
        """INSERT INTO subscriber_notification_sends (domain_id, recipient_email, inactive_count, status, error_message)
           VALUES (?,?,?,?,?)""",
        (domain_id, recipient_email, count, status, error_message),
    )
    conn.commit()


def last_notification(conn, domain_id: int):
    row = conn.execute(
        """SELECT sent_at, recipient_email, inactive_count, status, error_message
           FROM subscriber_notification_sends WHERE domain_id=? ORDER BY sent_at DESC LIMIT 1""",
        (domain_id,),
    ).fetchone()
    return dict(row) if row else None
