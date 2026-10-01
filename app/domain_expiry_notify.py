"""
On-demand "notify the client their domain is expiring soon" email.

Same shape as app.bounce_notify/app.subscriber_notify (manual button, not
scheduled, own send-history log) but simpler: a domain's expiry date is one
point-in-time fact, not a "new since last time" list, so there's nothing to
scope -- the log here exists purely so the operator can see "already told
them, on this date" rather than to decide what content to include.
"""

import datetime

from app.domain_expiry import days_until


def latest_expiry_check(conn, domain_id: int):
    row = conn.execute(
        """SELECT expires_at, registrar, checked_at FROM domain_expiry_checks
           WHERE domain_id=? AND status='ok' ORDER BY checked_at DESC LIMIT 1""",
        (domain_id,),
    ).fetchone()
    return dict(row) if row else None


def build_notification(conn, domain_id: int, domain_name: str, recipient_label: str, signoff_name: str = None):
    """Returns (subject, text_body, days_left, expires_at), or None if no
    real expiry data is on file yet. Wording Jev-tested (jev/DECISIONS_LOG.md
    Ch.42) against real aikyamsolve.org data (expires 2026-11-22, registered
    through Cloudflare, Inc.)."""
    check = latest_expiry_check(conn, domain_id)
    if not check or not check["expires_at"]:
        return None

    days_left = days_until(check["expires_at"])
    greeting = recipient_label or domain_name
    registrar_line = (
        f"It is registered through {check['registrar']}, so renewing it there should just take a few "
        f"minutes and a small payment.\n\n"
    ) if check["registrar"] else ""

    body = (
        f"Hi {greeting},\n\n"
        f"A quick but important one: {domain_name}'s domain registration expires on "
        f"{check['expires_at']}, which is {days_left} days away. If it lapses, your website and every "
        f"email address on it stop working that same day -- and someone else could register the name "
        f"out from under you.\n\n"
        f"{registrar_line}"
        f"Please take care of it when you get a chance. Reply once it is done and we will mark this as "
        f"handled on our end.\n\n"
        f"With care,\n"
        f"{signoff_name or 'The aikyam Team'}"
    )
    subject = f"{domain_name}'s domain registration is expiring soon"
    return subject, body, days_left, check["expires_at"]


def record_notification_sent(conn, domain_id: int, recipient_email: str, expires_at: str, status: str, error_message: str = None):
    conn.execute(
        """INSERT INTO domain_expiry_notification_sends (domain_id, recipient_email, expires_at, status, error_message)
           VALUES (?,?,?,?,?)""",
        (domain_id, recipient_email, expires_at, status, error_message),
    )
    conn.commit()


def last_notification(conn, domain_id: int):
    row = conn.execute(
        """SELECT sent_at, recipient_email, expires_at, status, error_message
           FROM domain_expiry_notification_sends WHERE domain_id=? ORDER BY sent_at DESC LIMIT 1""",
        (domain_id,),
    ).fetchone()
    return dict(row) if row else None
