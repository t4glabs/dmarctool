"""
Is the background machinery actually running? Two answers shown on every
dashboard page (base.html's sidebar status panel + stale-checks banner):

- Background checks: when the last full sweep (web.py::run_all_checks)
  finished. The sweep is meant to run every 6 hours; if none has finished in
  STALE_AFTER_HOURS, something is wrong (hung step, app down, machine asleep)
  and nothing else in the dashboard would say so -- every other page just
  quietly shows older data.
- Scheduled report emails: the two independent switches that both have to be
  on for run_report_emails() to send (report_emails_enabled, the user's
  standing Settings choice, and dev_pause_scheduled_sends, the temporary
  development pause), plus how many reports are due right now -- so resuming
  never surprises anyone with a batch of client emails.
"""

import datetime

from app.analysis import ensure_default_settings
from app.domain_report import due_report_targets

# 6-hour interval + 2 hours of slack for a long sweep (a full SES drain alone
# can take 10 minutes) before calling it stopped.
STALE_AFTER_HOURS = 8

_TS = "%Y-%m-%d %H:%M:%S"


def record_sweep_start(conn, trigger: str) -> int:
    cur = conn.execute("INSERT INTO sweep_runs (trigger) VALUES (?)", (trigger,))
    conn.commit()
    return cur.lastrowid


def record_sweep_finish(conn, run_id: int) -> None:
    conn.execute("UPDATE sweep_runs SET finished_at=datetime('now') WHERE id=?", (run_id,))
    conn.commit()


def _parse(ts):
    return datetime.datetime.strptime(ts, _TS) if ts else None


def sweep_status(conn, app_started_at: datetime.datetime, now: datetime.datetime = None) -> dict:
    """All times naive UTC. With no finished sweep on record yet (fresh
    install, or the first few minutes after this table was added), the app's
    own start time stands in as the reference point -- so a sweep that hangs
    on the very first run still trips the banner STALE_AFTER_HOURS later."""
    now = now or datetime.datetime.utcnow()
    last = conn.execute(
        "SELECT finished_at FROM sweep_runs WHERE finished_at IS NOT NULL ORDER BY finished_at DESC LIMIT 1"
    ).fetchone()
    running = conn.execute(
        "SELECT started_at FROM sweep_runs WHERE finished_at IS NULL ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    last_finished = _parse(last["finished_at"]) if last else None
    running_since = _parse(running["started_at"]) if running else None
    # A stale unfinished row from before a restart isn't "running" -- the
    # process that started it is gone. Only count one newer than this process.
    if running_since and running_since < app_started_at:
        running_since = None

    reference = last_finished or app_started_at
    hours_since = (now - reference).total_seconds() / 3600
    return {
        "last_finished": last_finished,
        "running_since": running_since,
        "hours_since": hours_since,
        "stale": hours_since > STALE_AFTER_HOURS,
    }


def email_status(conn, now: datetime.datetime = None) -> dict:
    now = now or datetime.datetime.utcnow()
    settings = ensure_default_settings(conn)
    due_rows, due_groups = due_report_targets(conn, now)
    master_on = settings.get("report_emails_enabled", "0") == "1"
    paused = settings.get("dev_pause_scheduled_sends", "0") == "1"
    return {
        "master_on": master_on,
        "paused": paused,
        "sending": master_on and not paused,
        "due_count": len(due_rows) + len(due_groups),
    }


def humanize_hours(hours: float) -> str:
    if hours < 1:
        minutes = max(1, int(hours * 60))
        return f"{minutes} min ago"
    if hours < 48:
        return f"{int(hours)} h ago"
    return f"{int(hours // 24)} days ago"
