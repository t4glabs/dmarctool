# DMARCTool

A self-hosted, single-user DMARC management dashboard for the user's domains (Aikyam / Tiny Bridge LLP / Muziris Bazaar). Runs locally on the user's MacBook Air, no public exposure, no team/multi-user concerns.

## Running service

- Dashboard: `http://127.0.0.1:8787`
- Managed by launchd: `~/Library/LaunchAgents/com.aikyam.dmarctool.plist`, label `com.aikyam.dmarctool`
- `RunAtLoad` + `KeepAlive` — starts at login, auto-restarts on crash
- Logs: `~/dmarctool/logs/dmarctool.out.log` / `dmarctool.err.log`
- Reload after code changes: `launchctl kickstart -k gui/$(id -u)/com.aikyam.dmarctool`

## Stack

Python, FastAPI + Jinja2 (server-rendered, no JS build step), raw `sqlite3` (no ORM, `app/schema.sql` is the source of truth), APScheduler (background 6-hourly analysis+DNS refresh). venv at `./venv` — recreate it (don't just move the directory) if the project ever relocates again, since venv shebang lines are absolute paths.

## Modules (`app/`)

- `db.py` — connection + schema init
- `ingest.py` — parses DMARC aggregate reports (mbox/Takeout zip/xml.gz/xml), idempotent, sniffs zip/gzip by magic bytes not filename
- `analysis.py` — policy history derivation, rolling pass rates, ramp recommendations, known-sender tracking, provider/sending-stream breakdowns, action-item upserts (deduped)
- `dns_check.py` — live `dig TXT _dmarc.<domain>` vs. reports/manual log (parallelized across domains)
- `blocklist.py` — DNSBL checks (Spamhaus ZEN, Barracuda BRBL) on known sending IPs, volume/recency-filtered, cached
- `compliance.py` — zero-credential Gmail sender-guideline checks: PTR/FCrDNS, SPF DNS-lookup budget (RFC 7208), DKIM key length — all via `dig`, no API keys
- `mailgun.py` — Mailgun API integration (stats + suppression lists) for whichever tracked domain has a matching Mailgun domain, dynamically re-matched each run; needs `MAILGUN_API_KEY` in `secrets.env`
- `postmaster.py` — Google Postmaster Tools v2: real Gmail-reported spam rate + `complianceStatus` verdicts (SPF/DKIM, DMARC, TLS, PTR, unsubscribe, overall deliverability), dynamically matched against verified domains; needs `GOOGLE_POSTMASTER_CLIENT_ID/SECRET/REFRESH_TOKEN` in `secrets.env`
- `postmaster_auth.py` — one-time interactive OAuth flow (`python -m app.postmaster_auth`) that produces the refresh token above; only needs re-running if access is revoked
- `ses_events.py` — Amazon SES bounce/complaint/delivery events, drained from an SQS queue fed by one SNS topic that each domain's dedicated SES configuration set publishes to (SES has no per-domain read API otherwise). Matches configuration-set name back to a domain via a naming convention: dots replaced with hyphens (`pattic.org` → `pattic-org`) -- any new domain's config set must follow this to be picked up. Needs `AWS_SES_ACCESS_KEY_ID/SECRET_ACCESS_KEY/REGION` + `SES_EVENTS_QUEUE_URL` in `secrets.env`. Uses `boto3` (the one non-stdlib dependency in the project, justified by how error-prone hand-rolled AWS SigV4 signing would be)
- `config.py` — loads `secrets.env` (project root, chmod 600, never committed/DB-stored) for API keys
- `actions.py` — manual action log + action-item resolve (CLI: `python -m app.actions log/resolve/list`)
- `web.py` — the dashboard app
- `charts.py` — hand-rolled SVG sparkline (no charting library)
- `labels.py` — plain-language labels/tooltips/settings help text, kept separate so the dashboard stays understandable to a non-technical reader without cluttering the logic modules

See `MANUAL.md` for the plain-language usage guide (what to click, what the terms mean, troubleshooting) — that's the doc to point the user to, not this file.

## Working style for this project

- Build incrementally, validate each piece against real ingested data before moving on — don't batch multiple features into one delivery.
- Minimal tooling: prefer stdlib and what's already installed over new dependencies/frameworks.
- This is a personal ops tool, not a product — simple and functional over polished, but the dashboard should stay understandable to a non-technical reader (plain-language labels/tooltips over raw internal codes).

## Known constraint

Never place this project (or its launchd-managed venv) under `~/Desktop`, `~/Documents`, or `~/Downloads` — macOS TCC blocks background/non-Terminal processes from reading files there, which is why this project lives at `~/dmarctool` instead of its original `~/Desktop/DMARCTool` location.

## More context

Full history, real findings from the first data load, and hosting decisions are in this session's memory files (see `MEMORY.md` in the memory directory for this project path). If you're a fresh session picking this up, check the dashboard at http://127.0.0.1:8787 for current state rather than assuming anything here is still accurate.
