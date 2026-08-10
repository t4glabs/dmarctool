# DMARCTool

A self-hosted, single-operator dashboard that consolidates DMARC authentication, domain/IP reputation, ESP (Mailgun/SES) bounce and complaint tracking, Google Postmaster data, and Listmonk newsletter engagement and content quality into one plain-language view — so email deliverability problems get caught and explained before they hurt inbox placement, without hopping between five different provider consoles.

## What this is, in one line

Everything that affects whether your organization's email actually reaches the inbox — authentication, reputation, and newsletter content — watched from one place, in plain English.

## Description

DMARCTool runs quietly in the background on a single machine and watches over every domain an organization sends email from: DMARC/SPF/DKIM setup and DNS health, blocklist status, Mailgun/SES bounce and complaint reputation, Google Postmaster's real Gmail-reported spam rate, and the actual newsletters sent through Listmonk — their engagement, their content, and whether they follow Gmail's own sender guidelines. Instead of raw logs or a dozen open browser tabs, it produces one prioritized, plain-language action list with concrete next steps, built for a non-technical operator to run solo.

## Core features

1. **DMARC report analysis** — ingests aggregate reports (mbox, Google Takeout zip, xml.gz, xml), derives policy history, and gives plain-language ramp-up recommendations (when it's safe to raise `pct=` or move toward `p=reject`) based on a rolling, volume-aware pass rate.
2. **Live DNS & authentication checks** — SPF (RFC 7208 lookup-budget), DKIM (key strength), and DMARC record health checked directly, catching misconfigurations before they silently break authentication.
3. **Blocklist & infrastructure monitoring** — known sending IPs checked against Spamhaus and Barracuda, plus PTR/reverse-DNS verification, with zero API keys required.
4. **ESP reputation tracking** — Mailgun and Amazon SES bounce/complaint rates and suppression lists, with two-tier (watch/danger) thresholds and bounce reasons automatically categorized into plain-language causes instead of raw SMTP codes.
5. **Google Postmaster Tools integration** — the actual Gmail-reported spam rate and compliance verdicts, straight from Google, not inferred from DMARC reports.
6. **SES account health** — enforcement status, sending quota, domain identity verification, and previously-invisible SES "Reject" events (SES refusing to even attempt sending) all surfaced directly.
7. **Per-newsletter engagement stats** — opens, clicks, and bounces for every Listmonk campaign sent via SES, built from SES's own event data rather than Listmonk's own less-reliable analytics.
8. **Inactive-subscriber detection** — flags subscribers who've received many newsletters but never opened one, with a downloadable CSV for list cleanup.
9. **Newsletter content & spam-trigger scoring** — subject lines and full body content (fetched from Listmonk) scored for spam-trigger language, formatting issues, image-to-text ratio, and link shorteners, with a per-newsletter report card showing exactly what's good and what to fix.
10. **One unified action list** — every check across every integration feeds a single prioritized, deduplicated list of action items with plain-language explanations and concrete remediation steps, plus a separate radar view for domains you manage but don't formally track.

## Docs

- [`MANUAL.md`](MANUAL.md) — plain-language usage guide (what to click, what the terms mean, troubleshooting). Point non-technical users here.
- [`CLAUDE.md`](CLAUDE.md) — architecture, module reference, and working conventions for anyone (human or AI) developing the tool further.
