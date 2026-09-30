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
- `charts.py` — hand-rolled SVG sparkline/bar charts (no charting library)
- `labels.py` — plain-language labels/tooltips/settings help text, kept separate so the dashboard stays understandable to a non-technical reader without cluttering the logic modules
- `safe_browsing.py` — Google Safe Browsing Lookup API v4 check on each domain's own website; needs `SAFE_BROWSING_API_KEY` in `secrets.env`
- `mta_sts.py` — zero-credential MTA-STS (RFC 8461) and TLS-RPT (RFC 8460) checks: whether inbound mail to a domain is protected against SMTP TLS downgrade/interception, via `dig` for the DNS records plus a plain HTTPS GET (stdlib `urllib`) of the MTA-STS policy file itself. Informational when a domain simply hasn't set this up (optional, newer than SPF/DKIM/DMARC); only raises an action item when a DNS record exists but the policy is actually broken/unreachable
- `ses_account.py` — SES account-wide health (`sesv2 GetAccount`: enforcement status, sending quota, account-level suppression settings) and per-domain identity verification (`ListEmailIdentities`) — separate from `ses_events.py` since it hits the SES API directly instead of draining the event queue
- `display_name_checks.py` — checks a newsletter's "From" display name against Gmail's display-name guidelines (subject-line content, ALL CAPS, emoji, reply-count patterns, gmail.com spoofing) plus cross-campaign consistency
- `header_compliance.py` — one-click-unsubscribe header compliance (`List-Unsubscribe`/`List-Unsubscribe-Post`) and RFC 5322 hygiene (Message-ID, misleading `Re:`/`Fwd:` subjects) for bulk senders
- `content_scoring.py` — heuristic spam-trigger scoring for subject lines and newsletter body text (financial/urgency bait phrases, ALL-CAPS phrases, excessive punctuation/emoji) plus HTML structural scoring (image-to-text ratio, link shorteners)
- `listmonk.py` — read-only Listmonk API integration (stdlib `urllib`, HTTP Basic-style `token user:key` auth) that fetches real newsletter body HTML for campaigns already tracked via the SES event pipeline (matched by Listmonk campaign UUID), strips it to plain text, and feeds `content_scoring`; needs `LISTMONK_URL`/`LISTMONK_API_USERNAME`/`LISTMONK_API_TOKEN` in `secrets.env`
- `jev_client.py` — thin stdlib-only HTTP client for TypeSafe AI's "Jev" structured-decision model (schema-constrained yes/no, choice, and score judgments — never free text); needs `JEV_API_KEY` in `secrets.env`
- `jev_context.py` — wraps `jev_client.ask()` with DMARCTool's standing audience/voice context and a reusable criteria checklist (audience fit, usefulness, repetition risk, emotional resonance, honesty calibration, contradiction check, actionability) for judging client-facing report/dashboard copy before it ships; see `jev/README.md` before touching report or dashboard wording. Internal review tooling only — never mention this workflow in anything user/client-facing (README, MANUAL.md, report copy itself).
- `domain_report.py` — periodic, plain-language email reports for non-technical domain owners (deliberately a level simpler in voice than the operator dashboard — no DMARC/SPF/DKIM/policy/percent, analogies instead), sent via Mailgun on each domain's own configured schedule; also builds the shared context `pdf_report.py` renders from, so the email and PDF can never disagree about a fact
- `pdf_report.py` — generates the report's PDF attachment via Typst (external CLI, absolute path — launchd's PATH lacks Homebrew's prefix), hand-rolled native-primitive charts (no cetz/external packages), reusing `domain_report.py`'s content/context layer as a presentation-only layer
- `bounce_notify.py` — on-demand "notify client to clean their list" email (a manual button, not scheduled): confirmed-dead (`bounce_reasons.PERMANENT_CATEGORIES`) and chronic-transient addresses as two separately-hedged CSV attachments, with its own send-history watermark (`bounce_notification_sends`) independent from the dashboard's own suppression mark-done watermark
- `chronic_bounces.py` — detects addresses stuck "temporarily" failing for months without ever formally bouncing (functionally dead, just never escalated by the ESP) — surfaced from SES/Mailgun data already collected, no new integration
- `bounce_reasons.py` — categorizes raw SMTP/ESP bounce diagnostic text into plain-language causes (no-such-user, mailbox-full, blocked, etc.), including `PERMANENT_CATEGORIES` (the "safe to delete" bucket `bounce_notify.py` uses)
- `source_classification.py` — classifies each DMARC-failing sending source as forwarder / third-party / borrowed-identity / genuine-threat from the raw per-record auth results (which domain each SPF/DKIM signature was actually valid for), not just DMARC's own pass/fail bit
- `source_view.py` — one sending source's activity across every tracked domain at once (a cross-domain blind spot the rest of the per-domain UI can't see) — verdict/owner/remediation guide per source
- `lookalike.py` — generates realistic look-alike domain permutations of each tracked domain, checks which are registered and mail-configured; reports a watchlist, not per-permutation alerts
- `verdicts.py` — deterministic (status, text) one-line verdicts for every data-heavy dashboard section, from thresholds already used elsewhere — no judgment calls, no LLM
- `watchlist.py` — lightweight, computed-on-page-load radar for domains visible in Mailgun/Postmaster but not formally tracked (no DMARC report ever ingested for them)
- `domain_expiry.py` — zero-credential domain-registration-expiry check via RDAP (stdlib `urllib`/`json`, IANA's own TLD→RDAP-server bootstrap registry, no whois binary/dependency)
- `report_authorization.py` — checks external-destination `rua=` report addresses actually publish the RFC 7489 §7.1 authorization record; a receiver silently enforcing this makes reports just stop with no error, indistinguishable from "no problems"
- `email_verifier.py` — self-hosted syntax/MX/disposable-domain/real-SMTP-RCPT-TO/catch-all email verifier (single address or CSV batch) — same pipeline commercial verifiers run, same honest ceiling (rules out "definitely dead", can't promise zero future bounces)
- `known_bad.py` — cross-references an address against every "already caused a real problem" source already collected (SES/Mailgun suppressions, Listmonk's own blocklist) — read-only, distinct from `email_verifier.py`'s live SMTP snapshot
- `imap_ingest.py` — pulls DMARC aggregate reports straight from a mailbox over IMAP (strictly read-only: `EXAMINE`+`BODY.PEEK`, one label folder only), replacing manual Google Takeout export/import; shares `ingest.py`'s own blob-processing/dedup so nothing double-counts
- `mailgun_campaigns.py` — newsletter detection + per-campaign stats for mail sent through Mailgun directly rather than SES+Listmonk (in practice, Ghost blogs with Mailgun integrated) — campaigns reconstructed by grouping on (from_address, subject) since Mailgun's Events API has no campaign identifier
- `campaign_score.py` — evidence-weighted (not additive-demerit) newsletter deliverability scorecard: a real 0-N denominator, engagement (open/click/genuine-rate) as a scored dimension, spam-trigger wordlists de-emphasized to match their actual (low) predictive value
- `click_quality.py` / `open_quality.py` — tell a genuine subscriber click/open apart from an automated corporate email-security-gateway pre-fetch/scan, from raw per-event SES data (`ses_campaign_clicks`/`ses_campaign_opens`)
- `access_log.py` — records the Cloudflare-Access-verified requester email on every request this app serves (who did what, when), complementing (not replacing) Cloudflare's own Access logs

Per-newsletter engagement (opens/clicks/bounces/complaints/rejects, per-campaign-recipient tracking for inactive-subscriber detection) is built inside `ses_events.py` itself, not a separate module — Listmonk stamps every campaign email with an `X-Listmonk-Campaign` header (plus Subject and From), which SES echoes back on every event notification, so campaign-level stats come from the same trusted SES event stream rather than a second integration.

See `MANUAL.md` for the plain-language usage guide (what to click, what the terms mean, troubleshooting) — that's the doc to point the user to, not this file.

See `AWS_SES_ONBOARDING.md` before onboarding any new domain/identity into AWS SES — the shared SNS topic + SQS queue architecture, the exact configuration-set naming convention `ses_events.py` depends on, and the AWS console steps (event destination, default configuration set). Getting the naming wrong silently drops that domain's events with no error.

See `jev/README.md` before writing or auditing any client-facing report/dashboard copy — the standing Claude+Jev review workflow (audience/voice context, the 7-question criteria checklist, 100 grounded use cases, the data-handling rule for what can/can't be sent to Jev). This is a persistent, standing process, not a one-off audit — every future report/dashboard-copy decision is meant to run through it.

## Working style for this project

- Build incrementally, validate each piece against real ingested data before moving on — don't batch multiple features into one delivery.
- Minimal tooling: prefer stdlib and what's already installed over new dependencies/frameworks.
- This is a personal ops tool, not a product — simple and functional over polished, but the dashboard should stay understandable to a non-technical reader (plain-language labels/tooltips over raw internal codes).

## Known constraint

Never place this project (or its launchd-managed venv) under `~/Desktop`, `~/Documents`, or `~/Downloads` — macOS TCC blocks background/non-Terminal processes from reading files there, which is why this project lives at `~/dmarctool` instead of its original `~/Desktop/DMARCTool` location.

## More context

Full history, real findings from the first data load, and hosting decisions are in this session's memory files (see `MEMORY.md` in the memory directory for this project path). If you're a fresh session picking this up, check the dashboard at http://127.0.0.1:8787 for current state rather than assuming anything here is still accurate.
