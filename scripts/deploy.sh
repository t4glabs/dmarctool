#!/bin/bash
# The ONLY way code changes should go live: pause scheduled client emails,
# push, restart, wait out the post-restart send check, and only then resume.
#
# Why: the scheduler runs a full check + scheduled report emails ~1 minute
# after every restart (app/web.py::_startup), so a restart during development
# with sends live can email real clients (real incident, 2026-10-01 -- see
# app/analysis.py's dev_pause_scheduled_sends). Every failure path below
# leaves sends PAUSED (fail closed) and says so.
#
# Usage:  scripts/deploy.sh                 # commit first; this pushes, restarts, resumes
#         scripts/deploy.sh --keep-paused   # same, but leave sends paused afterwards
set -euo pipefail

cd "$(dirname "$0")/.."
PY=venv/bin/python
DB=data/dmarc.db
LABEL="gui/$(id -u)/com.aikyam.dmarctool"
KEEP_PAUSED=0
[ "${1:-}" = "--keep-paused" ] && KEEP_PAUSED=1

fail() { echo; echo "STOPPED: $1"; echo "Scheduled report emails are still PAUSED. Resume from the dashboard sidebar or with: $PY -m app.actions resume-sends"; exit 1; }

echo "1/5  Pausing scheduled report emails"
$PY -m app.actions pause-sends >/dev/null
[ "$($PY -m app.actions sends-status)" = "PAUSED" ] || fail "could not confirm the pause."

echo "2/5  Checking everything is committed"
[ -z "$(git status --porcelain)" ] || { git status --short; fail "uncommitted changes -- commit them first."; }

echo "3/5  Pushing to GitHub"
git push origin main || fail "git push failed."
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] || fail "local main and origin/main still differ after push."

echo "4/5  Restarting the app"
RESTART_TS=$(date -u +"%Y-%m-%d %H:%M:%S")
launchctl kickstart -k "$LABEL" || fail "launchctl kickstart failed."
for _ in $(seq 1 30); do curl -fsS -m 5 -o /dev/null http://127.0.0.1:8787 2>/dev/null && break; sleep 2; done
curl -fsS -m 5 -o /dev/null http://127.0.0.1:8787 || fail "app did not come back up -- check logs/dmarctool.err.log."

echo "5/5  Waiting for the post-restart check to finish (its email step is skipped while paused)"
# A sweep row started after this restart with finished_at set means the
# scheduler's first run is past its check step; run_report_emails() follows
# immediately and returns at once while paused. Up to 30 min (a full SES
# drain alone can take 10).
for _ in $(seq 1 360); do
  DONE=$(sqlite3 "$DB" "SELECT count(*) FROM sweep_runs WHERE started_at >= '$RESTART_TS' AND finished_at IS NOT NULL")
  [ "$DONE" -gt 0 ] && break
  sleep 5
done
[ "$DONE" -gt 0 ] || fail "the post-restart check didn't finish within 30 minutes."
sleep 15  # let run_report_emails() return before the switch flips

if [ "$KEEP_PAUSED" = 1 ]; then
  echo; echo "Deployed. Scheduled report emails left PAUSED (--keep-paused)."
  exit 0
fi
$PY -m app.actions resume-sends >/dev/null
echo; echo "Deployed. Scheduled report emails RESUMED -- due reports go out at the next 6-hourly check, not now."
