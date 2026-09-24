# Jev context pack — who this tool is for, and why every decision was made

This is the canonical context fed to Jev (TypeSafe AI's structured-decision model) alongside every
future evaluation this workflow runs. It is assembled from the user's own words and the real decisions
made across this project's history — not paraphrased into something generic. Real domain names and
client details are included deliberately (explicit user authorization, 2026-09-24): "I am okay with
passing all client details, domains, emails etc to jev but not my secrets and env security keys etc."
**Hard rule, never crossed: nothing from `secrets.env` (API keys, passwords, tokens, credentials of any
kind) is ever part of a Jev call.** This file, and `app/jev_context.py` which encodes it in code, contain
zero secrets by construction — they're business/audience context, not credentials.

## Who aikyam is

aikyam is a nonprofit that helps *other* nonprofits, grassroots NGOs and movements with accessible
technology for social good. It self-hosts tools, shares them, trains people, and builds their capacity.
DMARCTool ("vigil") is one such tool: it manages email authentication and deliverability on behalf of a
portfolio of real client domains (aikyamjobs.org, aikyamfellows.org, pattic.org, arpo.in, aikyam.school,
aikyamhq.com, catsofkochi.com, captains.ngo, ilabindia.org, and others) so those orgs never have to
understand DMARC/SPF/DKIM themselves.

## Who the audience is, and what they actually fear

The people who read the email reports this tool generates are staff at small NGOs with **no technical
background**. Most don't know what tools exist, which to use, or how — and are especially unaware that
free/open-source options exist at all. **They fear technology.** They are not DMARCTool's operator; they
are aikyam's beneficiaries, trusting aikyam to handle something they don't understand and can't verify
themselves.

## The stakes they don't know to worry about, but need to feel

The report has to carry this understanding *implicitly*, because the audience doesn't have it going in:
- Owning a domain does not give you working, trustworthy email.
- A well-configured domain is what makes a **funder trust** an email is really from the org.
- An unmaintained one is exactly how a **donor gets scammed** by someone spoofing the org's name.
- And it's exactly how the org's own **impact stories and newsletters quietly land in a funder's spam
  folder** — invisible failure, no one tells them why replies stopped coming.

So: "having a domain is not enough — domain health matters, and they need to feel that without needing
to understand DMARC."

## What the report must make them feel

**Safe** — and that trusting aikyam with this was the right call. The user's own words: "okay we dealt
with these these things, now because of this it is safe now." Concretely, every report should answer, in
this order: what happened → where → how → what aikyam did about it → what's still being worked on →
what's now clear → what's still pending → the best next tip, if any — on a schedule, with **historical
comparison to their own past** so improvement is visible (not a competitor benchmark — "did *my* month go
well," not "how do I rank").

## Voice rules — stricter than the rest of the tool

The internal dashboard (built for Aikyam's own operator) already avoids saying "quarantine"/"reject" but
still uses "policy," "authentication," and raw percentages. **The client report goes a full level below
that: no DMARC/SPF/DKIM/policy/percent words at all.** Analogies instead — e.g. describing enforcement
strength as "a lock we're gradually tightening: right now, about 50 out of every 100 suspicious emails
pretending to be you get sent straight to spam, and we're keeping a close eye on the rest before turning
the lock up further." No raw category code (`dns_drift`, `chronic_transient_bounce`, etc.) is ever
allowed to leak into the reader-facing text — every internal category must map to real plain-language
prose, or be marked as internal/operator-only and omitted entirely. There's no generic fallback: an
unclassified new detector is silently omitted from the report rather than ever showing a vague filler
line.

## The "wow factor" — real events framed as trust, not tech

The single most valuable thing DMARCTool does for these orgs is catching people trying to impersonate
them. The user's own framing: catching a spoofing attempt (e.g., a random-looking subdomain from an
overseas source forging `arpo.in`'s name) is a **wow-factor / value signal**, not a chore — it should be
surfaced as good news ("we caught someone trying to do this, and here's what our protection did"), with
count + a few concrete examples (the fake identity used, the country, the date). This is the same
instinct as showing "your sending IP came off a public spam blocklist" as a celebrated fact, not a
buried log line.

**Honesty is non-negotiable in this framing.** A caught attempt must be described exactly as
strongly as what actually happened, never stronger:
- If the org's enforcement was strong enough to actually stop the message (quarantine/reject), say
  "blocked" / "never got through."
- If enforcement wasn't yet at full strength (`disposition=none` — the attempt was *seen and logged*
  but not stopped), say that honestly and pivot to "turning protection up further is what stops these
  completely" — never claim "blocked" for something that was only detected.

The same honesty applies to **look-alike domain monitoring**: a domain registered to closely resemble
theirs (e.g. `a1kyam.school` next to `aikyam.school`) is currently reviewed and, if it looks like an
unrelated/benign registration, marked known and goes quiet. But the reader needs to be told plainly if a
look-alike ever looks like an actual copying/impersonation threat rather than coincidence — silence
should mean "reviewed, looks harmless," never "we stopped looking."

## Historical / incident-relative framing (the reports were becoming repetitive without it)

Three real features exist because the user said reports felt repetitive and asked for "historical and
comparing data relative to their incidents":
- A cumulative ledger: "since Aikyam began looking after this domain in {month}, we've resolved N issues
  that could have hurt your reputation or kept your emails from reaching people, and we're on M more right
  now" — lifetime scope, distinct from the per-period sections below it.
- A recurrence note on an item, but ONLY when that exact problem has genuinely come back more than once
  (never on a first-time item — a "first time!" note on every line would just be new clutter).
- A multi-month health-score trajectory, dormant until enough real months of history exist (this is
  correct behavior, not a bug, while the tool is young).

## Traps already learned the hard way — do not reintroduce these

- **Self-contradiction.** The same underlying problem can have one instance resolved and another still
  open; both used to render the identical sentence, producing "we fixed X" and "we're still working on X"
  in the same report. Still-open must always win, and resolved must exclude anything still open in the
  same category.
- **Restating a summary fact and its detail as two separate, near-identical sentences** in different
  sections. A summary-then-detail relationship is fine; verbatim repetition of the same claim is not.
- **Praise gated on only one signal when the real claim needs several.** "Your mail arrives looking
  trustworthy" was once gated only on authentication passing, and rendered two sections above a real,
  elevated bounce rate for the same domain — a flat contradiction to the reader even though the two
  mechanisms are technically different.
- **Filler tips/sections that exist only so a heading doesn't look empty.** If there's nothing specific
  to advise, the whole section is omitted — never a generic "keep monitoring your email health" non-tip.
- **Vague-and-alarming language.** Anything that can't name what/where/how doesn't belong in the report at
  all — "Google told us something needed attention" tells the reader nothing and just produces anxiety
  with no path to resolution.
- **Miscategorized routine housekeeping.** Pruning dead addresses from a sending list is neither "a
  problem" nor "something we fixed for you" — it's ongoing maintenance, and mixing that framing with
  either extreme misrepresents what actually happened.
- **Repetition/boredom — the newest and most significant lesson.** A real audit (Jev-assisted, this
  session) found that content restating an unchanged positive fact verbatim, cycle after cycle, scores as
  boring/skippable to a reader almost unanimously (measured boredom risk ~2/2 at 93-99% confidence across
  every pattern tested), even though the underlying fact still matters. The fix that works: reference the
  real STREAK/duration something has held ("working steadily for 3 months in a row") instead of
  re-explaining it fresh each time; rotate equivalent phrasing where no real streak exists (a boolean
  all-clear headline); or explicitly acknowledge "no change since last time" instead of silently repeating
  the identical paragraph. The goal is never to delete the reassurance — it's to stop it reading as
  boilerplate.
- **Low-volume false alarms.** A rate computed from too little real data (one bad event out of very few
  total events; a percentage with a near-zero denominator) can look like a dramatic, real problem while
  being statistically meaningless — e.g. a domain that simply stopped sending real mail for a while once
  scored as if 100% of its (nonexistent) mail was failing, purely because a "no data" case was silently
  computed as "0% pass" instead of "not enough data to say." Any rate-based judgment needs a real minimum
  sample size before it's trusted, and "not enough data yet" always beats a fabricated number.

## What this tool deliberately does NOT chase, and why

DMARCTool is judged against real vendors (EasyDMARC, PowerDMARC, dmarcian, URIports) and is ahead on
depth of integration (bounce reasons, newsletter engagement, live compliance checks) precisely because it
stays scoped to what THIS audience needs, not "industry-standard checkbox" features:
- **A paid trust-mark certification** (BIMI) was explicitly declined — "since bimi is paid and not
  relevant to ngos maybe we can skip them." Wrong audience: costs real money and needs a registered
  trademark, neither of which grassroots NGOs have.
- **Same-day/real-time alerting** was explicitly declined by the user ("i dont need 2") even though it's
  cheap to build — not what this audience needs from a monthly-cadence relationship.
- **Fine-grained DNS-change diffing** was explicitly declined ("3 is not needed i think it is too much") —
  more technical surface than this audience or its operator wants.
- **Multi-tenant/RBAC/SSO/API platform features** were refused outright — this serves one operator
  managing real client relationships directly, not a platform selling itself to many mutually-invisible
  customers.

**The lesson for judging any new idea:** usefulness is judged against what THIS specific, non-technical,
under-resourced audience actually needs to feel safe and understood — not against what a generic B2B SaaS
competitor would ship to look complete.

## How decisions actually get made here (the working relationship)

- **Build one thing at a time, validate against real data before moving to the next** — never batch
  multiple unrelated features into one delivery. Every claim made about "what's broken" or "what's fixed"
  gets checked against the real, live dashboard/database before being asserted, not assumed from a prior
  session's memory.
- **When challenged ("are you sure? what else are you missing?"), the right response is re-derivation
  across the WHOLE real portfolio with evidence, never just reassurance about the one case asked about.**
  This has directly surfaced real bugs more than once (a DKIM-redundancy blind spot found on one domain
  turned out to be far worse on a second domain that looked "fully healthy" the whole time).
- **Lean, minimal tooling** — prefer what's already available (stdlib, `dig`, raw SQL) over new
  dependencies or platforms, unless something is doing real, otherwise-error-prone work.
