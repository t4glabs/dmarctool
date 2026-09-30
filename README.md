# DMARCTool

A self-hosted, single-operator platform that watches every domain an organization sends email from —
DMARC/SPF/DKIM authentication, DNS and blocklist health, ESP (Mailgun/SES) reputation, Google Postmaster
data, impersonation and look-alike-domain threats, and Listmonk newsletter quality — and turns all of it
into one prioritized, plain-language action list. It doesn't stop at watching: it also writes and sends
the periodic plain-language report a non-technical domain owner actually reads (email + a real PDF), and
can send a one-click "please clean your mailing list" email with the exact addresses to remove, attached
and explained — no hopping between provider consoles, and no manually drafting client emails by hand.

## What this is, in one line

Everything that affects whether an organization's email reaches the inbox — authentication, reputation,
and newsletter quality — watched from one place, explained in plain English, and reported straight to the
people who need to know, not just the person running the dashboard.

## Description

DMARCTool runs quietly in the background on a single machine. For the operator, it's a dashboard: DMARC
report ingestion and policy-ramp guidance, live DNS/SPF/DKIM checks, blocklist and reputation monitoring
across Mailgun/SES, Google Postmaster's real Gmail-reported spam rate, impersonation and look-alike-domain
detection, and full newsletter engagement/content-quality scoring via Listmonk — all deduplicated into one
action list with concrete next steps, built to be run solo by someone who isn't a deliverability expert.

For the domain owners themselves — often non-technical people running a small nonprofit or community
project — it's something different: a periodic email (with a full, professionally designed PDF report
attached) that explains what happened to their email this month in plain terms, no jargon, and an
on-demand button the operator can press to email them a ready-to-use list of dead addresses to remove
from their own subscriber list, with a clear explanation of why it protects their reputation. Every piece
of client-facing wording — report copy, tips, this notification email — is written and reviewed to read
like plain English to someone non-technical, stay honestly calibrated to what's actually known, and never
just repeat the same sentence verbatim every cycle.

## Core features

### DMARC & authentication health
- Ingests aggregate DMARC reports (mbox, Google Takeout zip, `.xml.gz`, `.xml`), idempotently, and derives
  policy history plus plain-language ramp-up recommendations (when it's safe to raise `pct=` or move
  toward `p=reject`) from a rolling, volume-aware pass rate.
- Live DNS checks for SPF (RFC 7208 lookup-budget), DKIM (key strength, alignment), and DMARC record
  health — catching a misconfiguration before it silently breaks authentication, not just from reports.
- Detects domains passing DMARC almost entirely on SPF, with DKIM barely or never aligning — a real gap
  a simple pass/fail read misses.

### Reputation, deliverability & compliance
- Known sending IPs checked against Spamhaus ZEN and Barracuda BRBL, plus PTR/FCrDNS verification — zero
  API keys required.
- Mailgun and Amazon SES bounce/complaint rates and suppression lists, two-tier (watch/danger)
  thresholds, with a volume floor so a single stray bounce never reads as a 9% bounce rate.
- Google Postmaster Tools integration: the real Gmail-reported spam rate and compliance verdicts, straight
  from Google, not inferred — plus automatic registration of any tracked domain with real Gmail volume
  that isn't in Postmaster Tools yet.
- SES account-wide health (enforcement status, sending quota, suppression settings), per-domain identity
  verification, and previously-invisible SES "Reject" events surfaced directly.
- MTA-STS/TLS-RPT checks (is inbound mail protected against a TLS downgrade), Google Safe Browsing checks
  on the domain's own website, domain-expiry warnings, and verification that configured report addresses
  (`rua=`) are actually authorized to collect reports.

### Impersonation & source intelligence
- Classifies every sending source per domain — legitimate ESP, benign forwarder, or genuinely suspicious
  third party — instead of treating every unfamiliar IP as an attack.
- Watches for look-alike domains registered to impersonate a tracked domain, and surfaces real caught
  impersonation attempts with concrete examples (the fake address used, when, and — via WHOIS — roughly
  where from).
- A cross-domain source view: one sending IP or identity's activity across every tracked domain at once,
  with a plain "what is this and what should I do about it" guide per source.

### Newsletter & list quality
- Per-campaign, real engagement stats (opens/clicks by unique person, not raw event counts, which can
  overstate engagement several times over) built from SES's own event stream, matched to Listmonk
  campaigns via header.
- Inactive-subscriber detection, newsletter content/subject-line spam-trigger and HTML structural scoring,
  display-name guideline checks, and one-click-unsubscribe/RFC 5322 header compliance.
- Bounce reasons categorized into plain-language causes (mailbox full, no such user, blocked, etc.) from
  raw SMTP/ESP diagnostic text, plus detection of addresses that quietly fail for weeks without ever
  formally bouncing — functionally dead, just never confirmed as such.
- A self-hosted SMTP-based email verifier (syntax, MX, disposable-domain, RCPT-TO probe, catch-all
  detection) for single addresses or CSV batches, cross-referenced against known-bad addresses already
  seen across SES/Mailgun/Listmonk.

### Client-facing plain-language reporting
- A periodic, jargon-free email report to each domain's own owner, with a full, minimalist-designed PDF
  attachment (real charts, real typography, generated on the fly) covering their health score, what
  changed, what was fixed, and what's still being watched — written for someone who's never heard of SPF.
- A one-click "notify client to clean their list" email: attaches the confirmed-dead and likely-dead
  address lists as two clearly-labeled, appropriately-hedged files, explains in plain terms why removing
  them protects their sender reputation, and remembers what's already been sent so nothing goes out twice.

### One unified action list
- Every check above feeds one deduplicated, prioritized action list per domain, with plain-language
  explanations and concrete remediation steps — plus a separate radar/watchlist view for domains you're
  aware of but don't formally track yet.

## Docs

- [`MANUAL.md`](MANUAL.md) — plain-language usage guide (what to click, what the terms mean,
  troubleshooting). Point non-technical users here.
- [`CLAUDE.md`](CLAUDE.md) — architecture, module reference, and working conventions for anyone (human or
  AI) developing the tool further.
- [`AWS_SES_ONBOARDING.md`](AWS_SES_ONBOARDING.md) — how to onboard a new domain/identity into the shared
  AWS SES event pipeline.
