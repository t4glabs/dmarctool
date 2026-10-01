"""
Manual action log + action-item management (point 6: ground truth for what you
actually changed and when, independent of what the reports show).

Logging a policy change here also opens a new policy_history run
(source='manual_log'), which dns_check.py prefers over report-derived data when
comparing live DNS -- so a logged change gives you an unambiguous "did it take
effect" signal instead of "reports disagree, who knows why".
"""

import argparse
import datetime

from app.analysis import ensure_default_settings
from app.db import get_connection, get_or_create_domain, init_db


def set_scheduled_sends_paused(conn, paused: bool) -> None:
    """Toggles app.domain_report.run_report_emails' dev-only guard (see
    DEFAULT_SETTINGS' dev_pause_scheduled_sends for why this exists
    separately from the user's own report_emails_enabled setting). Only
    affects the SCHEDULED job -- the dashboard's manual "send test now"
    button and the bounce/subscriber/domain-expiry notify buttons are
    unaffected, since none of those can ever fire from a service restart."""
    ensure_default_settings(conn)
    conn.execute(
        "UPDATE settings SET value=? WHERE key='dev_pause_scheduled_sends'",
        ("1" if paused else "0",),
    )
    conn.commit()


def scheduled_sends_paused(conn) -> bool:
    settings = ensure_default_settings(conn)
    return settings.get("dev_pause_scheduled_sends", "0") == "1"


def log_action(conn, domain_name: str, message: str, p: str = None, pct: int = None, when: str = None) -> None:
    domain_id = get_or_create_domain(conn, domain_name)
    when_dt = datetime.datetime.strptime(when, "%Y-%m-%d") if when else datetime.datetime.utcnow()
    when_epoch = int(when_dt.timestamp())

    conn.execute(
        """INSERT INTO action_items (domain_id, kind, category, ref_key, title, detail, status, resolved_at)
           VALUES (?, 'manual_log', NULL, NULL, ?, ?, 'logged', datetime('now'))""",
        (domain_id, message, f"p={p} pct={pct}" if (p or pct is not None) else None),
    )

    if p is not None or pct is not None:
        conn.execute(
            """UPDATE policy_history SET observed_to = ?
               WHERE domain_id = ? AND source = 'manual_log' AND observed_to IS NULL""",
            (when_epoch, domain_id),
        )
        conn.execute(
            """INSERT INTO policy_history (domain_id, p, pct, observed_from, observed_to, source, notes)
               VALUES (?, ?, ?, ?, NULL, 'manual_log', ?)""",
            (domain_id, p, pct, when_epoch, message),
        )
    conn.commit()


def resolve_action(conn, item_id: int, status: str = "done") -> bool:
    cur = conn.execute(
        "UPDATE action_items SET status = ?, resolved_at = datetime('now') WHERE id = ? AND status = 'open'",
        (status, item_id),
    )
    conn.commit()
    return cur.rowcount > 0


def list_open_items(conn, domain_name: str = None):
    if domain_name:
        return conn.execute(
            """SELECT ai.id, d.name as domain, ai.category, ai.title, ai.detail, ai.created_at
               FROM action_items ai JOIN domains d ON d.id = ai.domain_id
               WHERE ai.status = 'open' AND d.name = ? ORDER BY ai.created_at""",
            (domain_name,),
        ).fetchall()
    return conn.execute(
        """SELECT ai.id, d.name as domain, ai.category, ai.title, ai.detail, ai.created_at
           FROM action_items ai JOIN domains d ON d.id = ai.domain_id
           WHERE ai.status = 'open' ORDER BY d.name, ai.created_at"""
    ).fetchall()


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage DMARC action items and manual change log")
    sub = parser.add_subparsers(dest="command", required=True)

    log_p = sub.add_parser("log", help="Log a manual action you took")
    log_p.add_argument("domain")
    log_p.add_argument("message")
    log_p.add_argument("--p", choices=["none", "quarantine", "reject"], default=None)
    log_p.add_argument("--pct", type=int, default=None)
    log_p.add_argument("--date", default=None, help="YYYY-MM-DD, defaults to today")

    resolve_p = sub.add_parser("resolve", help="Mark an open action item resolved")
    resolve_p.add_argument("item_id", type=int)
    resolve_p.add_argument("--status", choices=["done", "dismissed"], default="done")

    list_p = sub.add_parser("list", help="List open action items")
    list_p.add_argument("domain", nargs="?", default=None)

    sub.add_parser("pause-sends", help="Stop the scheduled job from sending real report emails (dev guard)")
    sub.add_parser("resume-sends", help="Let the scheduled job send real report emails again")
    sub.add_parser("sends-status", help="Show whether scheduled report emails are currently paused")

    args = parser.parse_args()
    conn = get_connection()
    init_db(conn)

    if args.command == "pause-sends":
        set_scheduled_sends_paused(conn, True)
        print("Scheduled report emails PAUSED -- the periodic job will not send real mail until you run "
              "`python -m app.actions resume-sends`. Manual send/notify buttons are unaffected.")
    elif args.command == "resume-sends":
        set_scheduled_sends_paused(conn, False)
        print("Scheduled report emails RESUMED -- normal periodic sending is back on.")
    elif args.command == "sends-status":
        print("PAUSED" if scheduled_sends_paused(conn) else "active (not paused)")
    elif args.command == "log":
        log_action(conn, args.domain, args.message, p=args.p, pct=args.pct, when=args.date)
        print(f"Logged for {args.domain}: {args.message}")
    elif args.command == "resolve":
        ok = resolve_action(conn, args.item_id, args.status)
        print("OK" if ok else "No open item with that id")
    elif args.command == "list":
        rows = list_open_items(conn, args.domain)
        for r in rows:
            print(f"[{r['id']:>4}] {r['domain']:<28} ({r['category'] or 'manual'}) {r['title']}")
            if r["detail"]:
                print(f"        {r['detail']}")


if __name__ == "__main__":
    main()
