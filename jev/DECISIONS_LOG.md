# Decisions log

Append-only. One entry per real Jev-informed decision going forward, in chronological order. Each entry:
what was being judged, which criteria, Jev's raw verdict, and what actually changed in code (or why
nothing changed). This is the low-level ledger; see `WORKFLOW.md` for how it relates to the hosted "Jev
findings & improvements" artifact, which carries the narrative/prose version of the substantial rounds.

---

## 2026-09-24 — Workflow build-out and validation call

**Context:** Building the `jev/` directory and `app/jev_context.py` per the user's standing instruction
to formalize the Claude+Jev workflow as real files/code, not memory. This entry documents the validation
call made while building the module, not a report-content decision in its own right.

**What was judged:** A representative spoofing-attempt sentence for `arpo.in` (real domain, used under
the 2026-09-24 authorization to pass real client/domain details to Jev): *"We caught someone trying to
send email pretending to be arpo.in from an address in another country. Because your protection is still
set to watch-only, this attempt was seen and logged but not blocked outright -- turning protection up
further is what will stop these completely."*

**Criteria run:** `audience_fit`, `honesty_calibration`, `emotional_resonance`.

**Jev's verdict:**
- `audience_fit`: `needs_plain_language_pass` (59% confidence) — "watch-only," "blocked outright" register
  as mildly technical.
- `honesty_calibration`: `accurately_calibrated` (96%) — correctly avoided over-claiming "blocked" for a
  detected-but-not-stopped attempt.
- `emotional_resonance`: 2.79/4 (weighted mean; 54% mass on "clearly makes the reader feel looked after")
  — solid but short of the top "wow" band, plausibly because the sentence doesn't name a concrete example
  (fake identity used, country, date) the way `CONTEXT.md`'s wow-factor guidance recommends.

**Outcome:** No code changed — this was a validation call to prove the `ask_with_context()` wrapper works
end-to-end with real domain data under the new authorization, not a live report edit. Confirms two things
worth carrying into the next real audit round: (1) "watch-only" and "blocked outright" are candidates for
further plain-language simplification wherever this pattern appears in `_PROBLEM_STORY`/`_TIP_LIBRARY`;
(2) spoofing-attempt sentences score higher on `emotional_resonance` when they include a concrete example,
matching what `CONTEXT.md` already prescribes — worth checking real current wording against this in the
next chapter.

---

## 2026-09-24 — Chapter 4: first full audit run through the formalized workflow

**Context:** First real audit round using `jev/WORKFLOW.md`'s process end-to-end — pulled real current
report content (live functions against real domain data, and one historical `_build_context()`
reconstruction) for several `USE_CASES.md` entries, ran the relevant `CRITERIA.md` checks, revised where
warranted, re-checked, shipped. All calls used real domain names directly (arpo.in), per the 2026-09-24
authorization.

### Finding 1 — `_impersonation_good_news` (use case B.11/B.12): fixed

Baseline was the real, live sentence for arpo.in (3 real caught attempts): *"We caught 3 attempts to send
email pretending to be your organization, one of them from China. They were caught and reported to us,
though not blocked outright yet -- strengthening your protection to full strength is what stops attempts
like these from reaching anyone at all."*

- `audience_fit`: `needs_plain_language_pass` 59%, `emotional_resonance`: 2.63/4, `honesty_calibration`:
  78% accurately_calibrated (21% overstates) — matches the Chapter 3 validation-call finding almost
  exactly, on the real report sentence this time rather than a synthetic one.

Tried three revisions (all naming the real most-recent attempt's fake address + date from
`caught_impersonation()`'s own `examples`, previously unused): a plain version, a "hoping a funder/donor
wouldn't look twice" version, and a "trick a donor into trusting an email that wasn't really from you"
version. The plain version won on every axis and was shipped:

*"We caught 3 attempts to send email pretending to be your organization, one of them from China. The most
recent, on 2026-07-29, used the made-up address "ghzd.arpo.in" to try to pass as you. We saw and logged
every one, but your protection isn't turned up far enough yet to stop them before they arrive -- turning
it up further is what will."*

- `audience_fit`: `clear_as_is` 53% (was 41%), `honesty_calibration`: 95% accurately_calibrated (was 78%),
  `repetition_risk`: 57% `fresh_or_appropriately_reframed`. `emotional_resonance` barely moved (2.68 vs
  2.63) — naming the example didn't produce the hoped-for wow-factor jump on its own; the bigger win was
  `audience_fit`/`honesty_calibration`. Also dropped "blocked outright" everywhere in the function (all
  three disposition branches), per the same finding applied consistently rather than just where tested.

**Shipped in** `app/domain_report.py::_impersonation_good_news`. Verified against real data two ways: live
against arpo.in's current 400-day window, and via a historical `_build_context()` reconstruction of a
window containing a real July 2026 attempt, to confirm the sentence renders correctly with an actual
example present.

### Finding 2 — sent-volume growth/drop sentence (use case A.9): fixed

Baseline (constructed from the real code path, `domain_window_stats`-driven, both directions tested):
*"...You also sent more email this time -- about 340, up from about 210 last time."* / *"...You sent less
email this time -- about 120, down from about 340 last time."*

- `honesty_calibration` was weak on both: growth 51% accurately_calibrated (46% overstates), drop 56%
  (42% overstates). Not the "blocked vs detected" kind of overstatement — Jev read stating a volume change
  with no framing of whether it matters as implicitly claiming significance it didn't earn. Same trap as
  vague-and-alarming language, from the opposite direction: unexplained "signal" instead of unexplained
  "problem." This is exactly what use case A.9 predicted before any content was tested.

Fix: append an explicit neutral-context line, matching the tool's existing "not something you need to act
on" tip pattern. Landed at: *"...That's just extra context for the number above, not something you need to
do anything about."* — moved `honesty_calibration` to 59%/58% accurately_calibrated on the two variants
tested (growth/drop), `audience_fit` to 69-80% `clear_as_is`. Diminishing returns past this (tried two
further rewordings, no further gain) — shipped at this point rather than over-optimizing a secondary
sentence, consistent with the project's incremental working style.

**Shipped in** `app/domain_report.py`, the `deliverability` growth/drop branch. Verified against real
historical data (arpo.in, 2026-08-21 to 2026-09-22 real report period, sent volume 699 -> 451).

### Checked, no action needed

- **`lookalike_domain` still-open wording** (use case B.13): real sentence scored 90% `clear_as_is`, 90%
  `accurately_calibrated`. Already well-calibrated; the underlying "ignored/benign candidates are excluded
  from the action item entirely" behavior in `app/lookalike.py::_apply_action_item` already matches
  `CONTEXT.md`'s honesty rule (silence = reviewed and harmless, not "stopped looking") — no report wording
  currently claims otherwise, so nothing to fix.
- **`domain_expiring_soon` wording at different distances** (use case I.83): tested the real story+tip
  text at 58 days and 9 days remaining. `honesty_calibration` held at 90% accurately_calibrated at *both*
  distances — the wording doesn't need to escalate with proximity because `_domain_expiry_detail()`
  already bakes the real date and day-count into the sentence. Confirms the 60-day threshold change
  ([[dmarctool_expiry_warn_2months]]) didn't introduce an urgency mismatch. `emotional_resonance` scored
  low (~0.6-0.8/4) at both distances, which is expected and correct — this is a practical/actionability
  category, not a trust-building one, so it shouldn't be judged on that axis.

---

## 2026-09-24 — Chapter 5: peer-comparison removal, still-open repetition fix

**Context:** Continued the D (streak/repetition) and J (portfolio-wide consistency) `USE_CASES.md`
sections. Found one structural issue never flagged in Chapters 1-4 and fixed the general case of a
repetition gap Chapter 2's streak work never reached.

### Finding 1 — the "HOW YOU COMPARE" peer-percentile section (use case J, cross-cutting with
CONTEXT.md's own stated rule): removed entirely

While reading `_health_trend`'s docstring (which already noted, unresolved, "distinct from
`_health_comparison()`, which ranks against other orgs; a peer ranking can't tell you whether YOUR month
went well") — found that `_health_comparison()` was still live, wired into `build_domain_report()`, and
rendered in all three templates (`email_report.txt`/`.html`, `client_report.html`) under a real "HOW YOU
COMPARE" heading. This directly contradicts `CONTEXT.md`'s own stated rule: "historical comparison to
their own past... not a competitor benchmark."

Pulled the real sentence for every tracked domain. Worst real case, **pattic.org**: *"Your domain's
overall email health is better than about 0% of the other organizations aikyam supports right now."*
Typical case, arpo.in: *"...better than about 33%..."*

- pattic.org (0%): `emotional_resonance` 0.17/4 (86% mass on "flat, no resonance either way" — for a
  domain already having a hard time, this reads as demoralizing, not reassuring), `audience_fit` 58%
  needs-plain-language-pass, `contradiction_check` 28% (moderate tension against the report's own
  progress-focused framing elsewhere).
- arpo.in (33%, a "good" real result): still only 0.48/4 `emotional_resonance`, 47% needs-plain-language.
  Even the best-case number doesn't help.

**No rewording could fix this** — the whole point of the section is a peer ranking, which is exactly what
`CONTEXT.md` says not to do, and `_health_trend` already exists as the correct alternative sitting right
next to it. Removed `_health_comparison()` and the "comparison" context key entirely, and the "HOW YOU
COMPARE" block from all three templates. `_health_trend` (own-progress framing) is untouched and remains
the report's only health-trajectory content.

**Shipped in** `app/domain_report.py` (`_health_comparison()` deleted, `comparison` removed from context
dict and docstring), `app/templates/email_report.txt`, `app/templates/email_report.html`,
`app/templates/client_report.html`. Verified: `preview_domain_report()` for pattic.org renders with no
`comparison` key and no "HOW YOU COMPARE" text in the output.

### Finding 2 — still-open items get zero duration framing, unlike resolved streaks (use case D,
generalizes past the specific dkim_alignment_gap use case D was scoped to)

Went in planning to test `dkim_alignment_gap`'s wording (real domains catsofkochi.com/captains.ngo have it
open). The isolated story scored 83% `needs_plain_language_pass` on `audience_fit` — tried 3 rewordings,
best got to 36-46% `clear_as_is`, real but modest gains, diminishing returns.

The bigger finding came from testing the REAL combined render (story + why + "we're on it" trailer, exactly
as `email_report.txt` assembles it): `repetition_risk` came back at **89% "reads as boilerplate."** Not
because story and why duplicate each other (they do, slightly, but that wasn't the driver) — Jev reads this
as boilerplate because a still-open, unchanged, multi-cycle problem repeats the *exact same sentence* every
report cycle until it's fixed, and `CRITERIA.md`'s `repetition_risk` question is explicitly about
cross-cycle repetition. Chapter 2's streak-framing work fixed this for `whats_working`'s POSITIVE facts
(real streak numbers, phrase rotation, "no change since last time") but never touched the still-open
section's problem descriptions, which had no equivalent mechanism at all.

Confirmed this isn't dkim_alignment_gap-specific: real data shows `dns_drift` open 58 days straight on
prerna.aikyam.school and 50 days on chingaritrustbhopal.org with zero duration framing either.

Fix: extended `_incident_recurrence()` (previously only fired at 2+ distinct re-raise days) to also handle
a single continuously-open item — once open at least 30 days (one full report cycle) as of the report's
own `period_end` (never wall-clock time, per [[dmarctool_streak_framing]]'s earlier lesson), it now returns
*"This has been open since {month} -- still on our list, not forgotten."* Tested on the real
dkim_alignment_gap combined text: `repetition_risk` dropped from 89% to 57% `reads_as_boilerplate` (real
improvement; can't fully eliminate it, since "still not fixed after 2 months" is inherently less exciting
than a positive streak, but a duration note beats silent identical repetition).

**Shipped in** `app/domain_report.py::_incident_recurrence` (new `as_of_str` param) and its still-open call
site in `_still_open_items` (now passes `end_str`). Verified against real data: `_still_open_items()` for
prerna.aikyam.school (domain_id 6) now returns "This has been open since July -- still on our list, not
forgotten." / "...since August..." for its two real long-open categories.

---

## 2026-09-24 — Chapter 6: section C (deliverability/reputation signals)

**Context:** First chapter to deliberately work a specific `USE_CASES.md` section (C) rather than follow
up on a prior chapter's queue, per the user asking directly how chapters get picked and what's left — C
was untouched and has real live data (Postmaster spam-rate stats) to test against right now.

### Finding 1 — `_risk_warning` (use case C.21-adjacent, found via the same "read the real shipped
sentence" method as Chapter 5): fixed, most significant finding this chapter

Never fired for a real domain yet (dormant — same shape as other not-yet-real features shipped this
session), but this is real, live copy that runs the moment a real trend crosses its thresholds, so tested
it directly with real reason-clauses the code actually generates:

*"You are in danger of emails landing in spam folders soon if this continues: Google's own numbers show
more people than recommended are marking your mail as spam right now; your bounce rate has been climbing
over the last few weeks. If you're not sure how to fix this yourself, reach out to aikyam directly. This
is really important for your organization."*

- `honesty_calibration`: **91% `overstates_beyond_the_evidence`** — the largest single miscalibration
  found in any chapter so far. A trend-based early-warning signal (real, but probabilistic) was phrased as
  a near-certain prediction ("You are in danger... soon").
- `emotional_resonance`: 0.41/4 (70% "no resonance") — surprisingly low for an urgent-sounding message.
  Root cause: it broke the report's own established "aikyam already handles this" pattern (the
  mailgun_reputation/blocklist tips) by asking the READER to act instead, which reads as being handed a
  problem rather than being looked after.

Rewrote to match the trend's actual certainty and put aikyam back in the driver's seat: *"We're keeping a
close watch on your email health because a few signs have started trending the wrong way: [same real
reasons]. Nothing urgent yet, but if this keeps going it could start affecting where your mail lands.
aikyam is already looking into it -- if anything changed on your end recently (a new sending tool, a big
one-off email blast), let us know so we can factor that in."*

- `honesty_calibration`: 91% overstates → **90% accurately_calibrated**. `emotional_resonance`: 0.41 →
  **1.74/4**. `audience_fit` moved the wrong way slightly (71% → 80% needs-plain-language-pass) — the
  individual `reasons` clauses (bounce rate, spam numbers) weren't touched and remain the harder part;
  noted as a residual for a future pass rather than chased further this round.

**Shipped in** `app/domain_report.py::_risk_warning`.

### Finding 2 — `_spam_rate_trend`'s good-case sentence (use case C.21, the repetition angle): fixed

Every real tracked domain currently renders the identical base sentence — *"Google's own numbers show very
few people are marking your mail as spam, averaging about 0.00% over the last few months, well under the
0.10% Google recommends staying under."* — with **no domain having enough Postmaster history yet for the
`prior`-comparison branch to fire**, meaning this sentence will repeat verbatim, unchanged, for as long as
the domain stays healthy (which is the goal!) unless the rate crosses a 0.01pp band. Tested the real
sentence: `repetition_risk` 67% `reads_as_boilerplate`. A third surface for the same trap Chapter 2 fixed
for `whats_working` and Chapter 5 fixed for still-open items — this time on a good-news STATUS line rather
than a positive streak or an open problem.

Fix: added an `else` branch (previously nonexistent — flat/unchanged silently added nothing) appending
*"That's steady, same as a few months ago."* once real `prior` history exists. Tested combined:
`repetition_risk` 67% boilerplate → 44% `borderline_needs_variation` (real improvement — a status sentence
that repeats "very few, well under 0.10%" every cycle will always have some inherent repetition risk no
matter how it's dressed, but silence made it worse).

**Shipped in** `app/domain_report.py::_spam_rate_trend`. Dormant like Finding 1 above — no domain has 6
months of Postmaster history yet, so this branch hasn't fired for real data, but the sentence it will
eventually produce is now validated.

---

## 2026-09-24 — Chapter 7: a real key-mismatch bug in Postmaster requirement wording (use case C.22)

**Context:** Continuing section C. Went in to Jev-test the real `_postmaster_story()` sentence for real
open `postmaster_compliance` items (aikyamjobs.org, climatekhoj.com both have real ones). Before running
any Jev call, checking which real requirement types were live turned up a straightforward code bug, not a
wording issue — same discovery method as Chapter 5's `_health_comparison` find (read the real code path
before testing content, not just the eventual sentence).

**The bug:** `_POSTMASTER_REQUIREMENT_STORY` (the dict `_postmaster_story()` looks curated wording up in)
had a key `"SPAM_RATE"`. Google's Postmaster API's real requirement value is `"USER_REPORTED_SPAM_RATE"` —
confirmed from `postmaster.py`'s own logic, which checks that exact string (`requirement ==
"USER_REPORTED_SPAM_RATE"`). The dict key could never match, so any domain whose relevant open item was a
spam-rate compliance flag silently fell back to the generic `"Google flagged one of its sender
requirements for your domain"` filler — precisely the "anxious and useless" pattern the 2026-09-23 fix for
this exact function was built to prevent, regressed by one wrong string. The dict was also completely
missing `"DMARC_POLICY"`, a second real, currently-live requirement value distinct from `"DMARC_ALIGNMENT"`
(alignment is about one message's "from" address; policy is about the protective setting's own strength).

**Real, live impact, checked before writing any fix:** of 8 real domains with an open
`postmaster_compliance` item, **4 (aikyam.space, captains.ngo, climatekhoj.com, makestories.space) were
hitting the generic fallback right now**, in the report they'd actually receive next — not a hypothetical
edge case or a dormant code path like Chapters 5-6's findings, a live regression affecting half the
domains with any open Postmaster issue.

**Jev-validated the replacement wording** (draft 1 used the word "DMARC" directly, which the report's own
voice rule bars — a real self-inflicted miss, caught on the first test):
- Generic fallback baseline: `usefulness` 0.74/4, `audience_fit` 90% needs-plain-language-pass.
- Draft 1 (used "DMARC policy" literally): `usefulness` 1.29/4 (better), `audience_fit` 81% needs-pass,
  19% `too_technical_reader_will_skip` — the jargon violation shows up as real audience_fit cost.
- Draft 2 (rewritten using the same "protective setting" analogy `dns_drift` already established, no
  DMARC/policy words): `usefulness` 1.44/4, `audience_fit` 73% needs-pass, 2%
  `too_technical_reader_will_skip`. Shipped.

**Shipped in** `app/domain_report.py::_POSTMASTER_REQUIREMENT_STORY` — renamed `"SPAM_RATE"` →
`"USER_REPORTED_SPAM_RATE"` (existing, already-fine wording — the text itself wasn't the problem), added
`"DMARC_POLICY"` (draft-2 wording). Verified live: all 4 previously-affected domains now return a real
story instead of `None`/the generic fallback; `preview_domain_report()` for climatekhoj.com confirmed the
generic filler text no longer appears anywhere in its real rendered report.

**Worth flagging for a future chapter, not chased today:** `DMARC_POLICY`'s meaning was inferred from
context (Google's exact distinction from `DMARC_ALIGNMENT` isn't confirmed against their API docs, just
against what's safely inferable and consistent with the observed `reason`/`status` data), and it may
overlap with the dashboard's own independent `dns_missing`/`dns_drift` findings for the same domain — worth
a `contradiction_check` pass once a domain has both open at once in real data.

---

## 2026-09-24 — Chapter 8: the flagged overlap check, and dkim_alignment_gap as a rollout case study

**Context:** No real domain currently has `DMARC_POLICY` open alongside `dns_missing`/`dns_drift` as
flagged in Chapter 7, but real data turned up a closer case: **captains.ngo has both `DMARC_POLICY` (the
Chapter 7 fix) and `dkim_alignment_gap` open simultaneously right now** — a better test than the
originally-planned one, since both are genuinely about authentication weakness from two independent
detection paths (DMARCTool's own report analysis vs. Google Postmaster's verdict).

### Finding 1 — real co-occurrence check: no contradiction, but confirms Chapter 5's repetition finding
compounds when items pair up

Pulled both items' real, fully-assembled still-open text for captains.ngo exactly as `_still_open_items()`
renders them (story + why + duration note + trailer) and tested them together as they'd actually appear as
consecutive bullets in the same report:

- `contradiction_check`: **19% risk** — low. The two stories are different enough (one about DKIM rarely
  aligning, one about Google wanting a stronger DMARC policy) that a reader wouldn't read them as
  duplicating or contradicting each other. **Resolves the question Chapter 7 flagged**: no fix needed here.
- `repetition_risk` on the pair: 80% `reads_as_boilerplate` — high, but not a NEW finding: this reflects
  `dkim_alignment_gap`'s own already-known repetition profile (57% after Chapter 5's duration-note fix,
  tested alone) compounding when a second still-open item sits next to it. Not actioned separately —
  tracked under the existing `dkim_alignment_gap` residual from Chapters 5/7.

**No code change** — checked clean on the specific question asked (contradiction), with the repetition
signal correctly attributed to already-tracked work rather than treated as a new bug.

### Finding 2 — dkim_alignment_gap retroactively audited against section F's own rollout checklist

Section F (`USE_CASES.md` #51-60) describes what a NEW detector category's rollout should look like.
`dkim_alignment_gap` (added 2026-09-23, before this Jev workflow existed) is the one real category built
from scratch this session — a natural case study for whether the checklist would have caught its known
problems earlier if it had existed at the time.

- **F.51/F.58 (audience_fit before shipping, full checklist before first real report):** No — confirmed
  by history. It shipped with a silently-missing `_PROBLEM_STORY` entry (Chapter 2 found and fixed that),
  then its story wording itself wasn't Jev-tested until Chapter 5, which found 83%
  `needs_plain_language_pass`. Had F.51/F.58 existed and been followed at rollout time, both gaps would
  have been caught before ever reaching a real report instead of after.
- **F.52 (dashboard label, separate audience):** `labels.py`'s `"DKIM rarely/never aligns"` label uses
  real jargon deliberately — correct, since the dashboard is Aikyam's own operator-facing tool, not the
  client report. Confirms this use case needs the audience-scope caveat added to `CRITERIA.md` this
  chapter (see below) — testing this label against client-audience criteria would have wrongly flagged it.
- **F.53 (operator remediation tooltip actionability):** Tested the real `CATEGORY_REMEDIATION` text
  (exact click-path: "Google Workspace -> Admin Console -> Apps -> ... -> Authenticate email") —
  `actionability` came back **99% `concrete_next_step`**, the highest score of any check this session.
  Already excellent; no fix needed.
- **F.57 (min-volume threshold to avoid low-volume false alarms):** Already built correctly from the
  start (`dkim_alignment_min_volume=20`, 30-day window, 0.5 min rate) — this is the one F-checklist item
  the original rollout got right without any formal checklist existing yet.

**Net effect:** confirms the value of the workflow existing going forward (2 of 4 checked items were real,
now-fixed gaps that predated the checklist) without needing any NEW code change this round — the fixes
were already made in Chapters 2 and 5.

### Process fix — `CRITERIA.md` audience scope, clarified

Running F.53's test surfaced a real gap in the workflow documentation itself: nothing previously said
`AUDIENCE_CONTEXT`/the 7 criteria are calibrated for the CLIENT report's non-technical reader specifically,
not for operator-only dashboard tooltips. Applying `audience_fit`/`emotional_resonance` to correctly
technical operator copy would produce a misleading "needs plain language" verdict for content that's
already right for its real audience. Added an explicit scope note to `jev/CRITERIA.md`: `actionability`,
`contradiction_check`, and `honesty_calibration` generalize to operator content; `audience_fit`,
`emotional_resonance`, and `repetition_risk` don't and should be skipped for that audience.

---

## 2026-09-24 — Chapter 9: a real SPF miscategorization found investigating section G

**Context:** Went in to test use case G.63 (should the SPF-lookup-budget detail — which `include:` spends
the DNS-lookup budget — ever cross into a client report). Checking real domains with an open
`spf_lookup_limit` item to pull real detail first (same discipline as Chapters 5, 7, 8: check what the real
data actually says before testing any wording) turned up a different, more fundamental bug before G.63's
actual question was ever reached.

**Correction to the Chapter 8 queue note:** section D's "remaining items now have real multi-month data" was
wrong — checked before starting, and no domain has a real 3-point `_health_timeline` yet (the tool is
~2 months old), and no domain has crossed `_pass_rate_streak_days`' 30-day threshold yet (max real streak:
18 days, on 6 different domains). D stays dormant for now; the queue note should have been verified before
being written, not assumed.

**The bug:** `_run_spf()` in `app/compliance.py` raised category `spf_lookup_limit` for BOTH `status ==
"over_limit"` (a real SPF record with too many DNS lookups) AND `status == "missing"` (no SPF record at
all) — two completely different real-world problems sharing one category, and therefore one client-facing
story: *"the settings that prove your emails really come from you had grown too complicated for some mail
systems to finish checking."* That sentence is simply false for a domain with no SPF record at all — there
is nothing "too complicated," there's nothing there.

**Confirmed live on real data:** tinkerhub.org and olimalarfoundation.org both have `spf_lookup_limit` open
right now, and both are genuinely missing SPF entirely (`spf_checks.status='missing'`,
`note='no SPF (v=spf1) record found at...'`) — not a hypothetical edge case, the wrong story for both real
domains that currently have this category open.

**A second discovery while fixing it:** `spf_missing` — the correct category for this case — already
exists, fully built (`_PROBLEM_STORY`, `_TIP_LIBRARY`, `_WHY_IT_MATTERS` in `domain_report.py`,
`CATEGORY_REMEDIATION` in `labels.py`), but **no code anywhere actually raised it**. Dead infrastructure,
presumably built in anticipation of exactly this case and then never wired up.

**Fix:** split `_run_spf()`'s branch by status — `"missing"` now raises `spf_missing` (dismissing any stale
`spf_lookup_limit` for the same target), `"over_limit"` keeps raising `spf_lookup_limit` (dismissing any
stale `spf_missing`), and the catch-all cleanup on recovery now dismisses both categories. Added the
missing `CATEGORY_LABELS`/`CATEGORY_HELP`/`CATEGORY_PRIORITY_ORDER` entries for `spf_missing` in
`labels.py` (previously only `CATEGORY_REMEDIATION` existed — the rest of the dashboard-facing
infrastructure was as unfinished as the code path that would have raised it).

**Jev's role here was secondary, not primary** — this is a logic/categorization bug, not a wording problem,
and a `honesty_calibration` check on the wrong sentence against a stated "genuinely has no SPF record"
fact came back 79% `accurately_calibrated`, missing it: the criterion is built to catch claims that
overstate/understate real evidence in tone (e.g. "blocked" vs. "detected"), not to catch "this is a
category-level factual mismatch, the wrong explanation entirely." Worth remembering going forward:
structural/categorization bugs are found by checking real code and data directly (same method as Chapters
5, 7, and now 9), not by running content through the checklist — Jev judges wording quality once the
underlying fact is already right, it doesn't independently verify the fact.

**Shipped in** `app/compliance.py::_run_spf`, `app/labels.py`. Verified live: forced a re-check
(`recheck_hours=0`) against both real domains — old `spf_lookup_limit` items auto-dismissed, new
`spf_missing` items raised with correct titles; `preview_domain_report()` for tinkerhub.org confirmed the
real report now shows "your website was missing a security setting that helps stop people from faking your
emails" instead of the "too complicated" text. Service restarted, confirmed healthy.

**G.63 itself, still open:** whether the specific SPF-lookup-budget detail (which include spends it) should
ever cross into a client report wasn't reached this chapter — queued for Chapter 10.

---

## 2026-09-24 — Scope correction, before Chapter 10

The user gave explicit, durable scoping guidance: the EMAIL report (`email_report.txt`/`.html`, sent via
`report_sends`) is the real deliverable — the interactive `client_report.html` view "was never shown or
used," not a separate target worth its own attention going forward. Also: **length is explicitly not a
constraint** for the email report — it will eventually become a PDF, so don't hold back genuinely useful
content out of a fear of making it too long; the only real bar is usefulness/emotional resonance.

Updated `jev/CONTEXT.md` and `app/jev_context.py::AUDIENCE_CONTEXT` (the thing actually transmitted to
every future Jev call) to say both explicitly, so this reframes every future G-section judgment call: the
question is "is this genuinely useful," never "will this make the email too long."

## 2026-09-24 — Chapter 10: section G, tested against the new "don't self-limit" framing

**G.62 (source-classification breakdown — own/forwarded/third-party/unverified) — real value found,
shipping deferred pending a better-targeted fix.**

Pulled real `classify_sources()` output for every tracked domain. aikyamfellows.org has real, substantial
variety: 61 aligned, 4 forwarded, **18 third-party** (mail authenticating as a domain that isn't
aikyamfellows.org's own — "a shared ESP account verified under one domain but sending with another domain
in the From address," per the module's own docstring), 131 too-low-volume to classify.

Drafted a summary paragraph for the email report and tested it twice:
- v1 (inventory-style, raw counts per kind): `usefulness` **2.48/4** (53% "clearly useful") — real,
  meaningful value, genuinely surprising given this exact kind of content was previously assumed too
  much/too long to include. `audience_fit` 84% needs-plain-language-pass (raw counts read as a data dump).
- v2 (narrative, led with the one real finding instead of an inventory): `usefulness` improved to
  **2.91/4** (71% "clearly useful"), `audience_fit` improved to 67% needs-pass (down from 84%), but
  `honesty_calibration` dropped to 67% accurately_calibrated (32% overstates) — a vaguer claim ("the large
  majority... clearly, verifiably yours") without v1's grounding numbers cost real accuracy.

**Why this isn't shipping yet, despite real usefulness:** `classify_sources()` is deliberately hedged
dashboard-only language ("looks like," "probably," "a description of recent evidence, not a permanent
verdict" — its own docstring). The tool already has a STRICTER, already-built, already-tested client-facing
detector for exactly this pattern: `detect_borrowed_sending_identity` (`analysis.py`), which requires
sustained volume/span/message-share evidence before raising `borrowed_sending_identity`. **Checked: it has
NOT fired for aikyamfellows.org**, despite 18 real third-party-classified sources. Before writing new
ad-hoc report content around the looser dashboard classification, the real open question is whether the
stricter detector's thresholds are correctly calibrated against this much real third-party volume, or
whether there's a genuine gap there — telling the client about a looser, hedged classification that
contradicts the report's own stricter, already-vetted detector would be inconsistent. Flagged for a
dedicated chapter rather than chased today.

**G.63 (SPF-lookup-budget detail) — still can't be tested, now with proof why.**

Checked every domain's `spf_checks` table directly: **zero rows anywhere in the portfolio currently have
`status='over_limit'`.** Every real `spf_lookup_limit` item that existed turned out to be a miscategorized
"missing" case (Chapter 9's fix). This isn't just "no data yet" — it's live confirmation that Chapter 9's
fix was complete: not one domain in the whole portfolio is actually over the real SPF DNS-lookup budget
right now. G.63 stays genuinely untestable against real data until a domain's SPF record actually grows
too complex.

**G.68 (known-bad cross-reference, "we already know these addresses bounce") — already covered.**

`_list_hygiene()` (built and Jev-tested in Chapters 1-2) already tells the reader about addresses that
stopped accepting mail, framed as routine housekeeping rather than an alarm. This substantially answers
G.68's intent already; no new work needed.

**Shipped this chapter:** the scope/context updates only (`jev/CONTEXT.md`, `app/jev_context.py`). No
`app/` report-logic changes — this was a research chapter, not a content-fix chapter, and that's a
legitimate shape: real evidence gathered, a genuinely promising idea found NOT ready to ship responsibly,
and two items resolved by checking what already exists rather than building something new.

---

## 2026-09-24 — Chapter 11: two real bugs found investigating the G.62 follow-up

**Context:** Chapter 10 flagged that `detect_borrowed_sending_identity` hadn't fired for aikyamfellows.org
despite 18 sources `classify_sources()` marked `third_party`. Investigated all 18 directly against real
`known_senders` data (user: "yeah aikyamfellows corrections are alright do whatever is right").

**What the 18 sources actually were, once inspected individually — two very different real patterns
hiding under one number:**

- **16 of 18: Google's own IP range (209.85.x.x), each low-volume (3-13 msgs), 100% signed by
  `googlegroups.com`, span 0-1 day each.** This is Google Groups mailing-list relay traffic — someone
  posted/forwarded aikyamfellows.org mail through a Google Group. Correctly stays unflagged: no individual
  IP has real volume or span, and this is closer in spirit to benign forwarding than a borrowed-identity
  misconfiguration Aikyam would need to fix. Not touched.
- **2 of 18 (161.38.204.238, 185.250.239.7): 13 msgs each, 0% pass rate, span 144 DAYS, 100% signed by
  `aikyam.space`** (a real domain in Aikyam's own tracked portfolio) via `eu.mailgun.org`. A genuinely
  sustained, real cross-domain identity pattern — running for **144 real days** — invisible to every
  existing qualifying path: too low volume for path (a) (13 < `high_vol`=20), no cross-domain
  corroboration for path (b) (this exact (IP, auth_domain) pair appears on no other domain), nothing acted
  on for path (c).

**Bug 1 — the detector required high volume no matter how long a pattern had persisted.** Path (a) requires
`total >= high_vol AND span_days >= MIN_BORROWED_IDENTITY_DAYS(5)` — both together. A pattern running 144
days is arguably STRONGER evidence than a volume spike (the same reasoning path (c) already applies to
measurable harm: real evidence lowers the volume bar rather than removing it). Added path (d): `span_days
>= LONG_SPAN_DAYS(90) AND total >= MIN_CORROBORATED_MSGS(3)`. **Simulated against the WHOLE portfolio
before shipping** at 60/90/120-day thresholds — stable, identical 3 clean real findings at every threshold
tested (no fragility, no new noise anywhere outside aikyamfellows.org): the 2 aikyam.space IPs, plus a
third real case (143.55.232.5 -> aikyamjobs.org, 3 msgs, 156 days) that had been sitting invisible on
aikyamfellows.org too.

**Bug 2, found while confirming Bug 1's fix wouldn't double-report — `_ESP_DEFAULT_AUTH_DOMAINS` was
missing `eu.mailgun.org`.** The same 2 real IPs/messages also validly sign as `eu.mailgun.org` (Mailgun's
EU-region default signing domain, dual-signed on every relayed message alongside the customer's own —
confirmed real: `eu.mailgun.org` signs for exactly 1 domain across the whole portfolio, vs. `mailgun.org`'s
16, and the identical IPs/messages carry both signatures). Without this, path (d) would have raised TWO
near-duplicate findings for the same 13 messages (one for `aikyam.space`, one for `eu.mailgun.org`) —
confusing rather than clarifying. Added `eu.mailgun.org` to the existing ESP-boilerplate exclusion set,
same reasoning already applied to base `mailgun.org`.

**Shipped in** `app/analysis.py::detect_borrowed_sending_identity` (`LONG_SPAN_DAYS` constant + path (d))
and `_ESP_DEFAULT_AUTH_DOMAINS`. `borrowed_sending_identity` already had full client-facing infrastructure
(story, tip, why-it-matters, dashboard label/help/remediation) from before this session — no new report
wording needed, just a detection gap.

**Verified live:** ran `detect_borrowed_sending_identity` directly against aikyamfellows.org — 2 real,
correctly-bucketed findings, no `eu.mailgun.org` duplicate. Ran the full `run_analysis()` for real (per the
established validation pattern) across the whole portfolio — 4 domains total now carry a real
`borrowed_sending_identity` finding (aikyamfellows.org ×2 collapsed to one client-facing mention, plus the
2 pre-existing ones on aikyamhq.com/tinybridge.in/catsofkochi.com, unaffected). Confirmed via
`preview_domain_report()` that aikyamfellows.org's real email report now correctly surfaces this. Service
restarted, healthy.

**Not chased this round:** the client-facing story text is deliberately generic ("a different website's
account," never naming which one) and correctly collapses aikyamfellows.org's 2 findings into one mention
via the existing dedup-by-rendered-text rule — working as designed, not a bug. Whether a MORE specific
client-facing version (naming which/how-many identities, now that length isn't a constraint per the
Chapter 10 scope correction) would score higher on usefulness is a real, separate question worth a future
chapter, not bundled into this one.

---

## 2026-09-24 — Chapter 12: naming specifics in borrowed_sending_identity, now that length is free

**Context:** Chapter 11 left this open: the client story for `borrowed_sending_identity` is entirely
generic (no culprit domain, count, or duration ever surfaces), even though the real detector already
computes all of it. Tested whether naming specifics scores higher, per the Chapter 10 scope correction
(email report is the deliverable, length is not a constraint).

**Baseline vs. with real detail**, tested on aikyamfellows.org's real, live data (2 open findings:
aikyam.space, 26 msgs/144 days; aikyamjobs.org, 3 msgs/156 days):

| | audience_fit (clear_as_is) | honesty_calibration (accurate) | usefulness |
|---|---|---|---|
| baseline (fully generic) | 79% | 68% | 1.63/4 |
| + real detail (list format) | 61% | 84% | 2.53/4 |
| + real detail (prose, shipped) | 66% | 87% | 2.50/4 |

**A genuine surprise**: the fully-generic baseline was WORSE on `honesty_calibration` (68%, 31%
overstates) than the version with real specifics (87%) — an unsubstantiated claim ("some of your emails")
reads as less trustworthy than the same claim backed by real numbers, not more. `usefulness` more than
doubled once real detail was added. `audience_fit` dipped moderately (naming domains/durations adds real
complexity) but stayed majority-clear.

**The complication, found while building this**: `_still_open_items`' own query (`GROUP BY category` +
`MAX(ref_key)`) only ever surfaces ONE ref_key per category — meaning a naive fix using just that one
ref_key would have silently omitted aikyamfellows.org's second real finding (aikyamjobs.org), understating
the real picture. New function `_borrowed_identity_detail()` queries ALL open ref_keys for the category
directly, independent of that GROUP BY limitation, and parses the real msg-count/day-span already stored
in each action item's `detail` text (same regex-on-stored-text approach as
`_chronic_bounce_count_in_period`, so it can never drift from what was actually raised). Handles 1 vs. 2+
accounts with correct prose joining, and a day-phrase helper fixing a grammar edge case caught while
verifying ("over the last 0 days" / "over the last 1 days" → "just today" / "the last day").

**Shipped in** `app/domain_report.py::_borrowed_identity_detail`, wired into `_still_open_items`. Verified
against all 4 real domains currently carrying this finding (aikyamfellows.org ×2, aikyamhq.com,
tinybridge.in, catsofkochi.com) — correct singular/plural and day-phrase grammar on every real case.
Confirmed via `preview_domain_report()` that aikyamfellows.org's real report now shows: *"Right now, 2
other accounts are involved: aikyam.space, which has carried 26 of your emails over the last 144 days, and
aikyamjobs.org, which has carried 3 of your emails over the last 156 days."* Service restarted, healthy.

---

## 2026-09-24 — Chapter 13: Listmonk unblocked, and a real ~1.3-4x engagement overstatement found and fixed

**Context:** Went in to check section H (blocked on Listmonk per prior-session memory). **Checked first:
it isn't blocked anymore** — `fetch_all_campaigns()` returned 150 real campaigns with no error, and all 21
of `ses_campaigns`' tracked rows already have real `body_html` populated. The prior "blocked on a
token-permission grant" note in memory was stale; corrected.

**What that unblocked, immediately: real per-campaign engagement data for aikyamjobs.org (17 real
campaigns) and pattic.org (4).** Pulling it via `recent_campaigns()` surfaced fields
`_newsletter_reach()` (the client-facing function) never used: `unique_openers`/`unique_clickers` (real
distinct people) alongside the raw `opened`/`clicked` (event counts) it actually reads.

**The bug, confirmed with real portfolio numbers before touching any code:** aggregated across all 17 real
aikyamjobs.org campaigns (21,565 delivered) — raw open rate 46.2% vs. the correct unique-people rate 35.6%
(a ~1.3x overstatement: some people reopen a message); raw click rate 6.3% vs. unique 1.55% (a **~4x**
overstatement: click-tracking events multiply per link/repeat-click far more than opens do). This is
exactly the "counted ~7x overstated" trap `dmarctool_deliverability_model.md` already documented and
already fixed in the dashboard's own `campaign_score.py` months before this session — but `_newsletter_
reach()`, the separate client-facing narrative function, was never updated to match. Confirmed this
memory's OWN standing guidance directly: "Always benchmark on unique people... Don't benchmark the
automation-filtered 'genuine' counts either: they're deliberately conservative... unfairly harsh" — so the
fix uses `unique_openers`/`unique_clickers`, not the separately-available bot-filtered "genuine" counts,
per that established research rather than re-deciding it from scratch.

**Shipped:** `_newsletter_reach()` now sums `unique_openers`/`unique_clickers` instead of `opened`/
`clicked`. Also surfaces clicks in the client report for the first time — previously never mentioned at
all, despite the same research explicitly ranking clicks as the MORE trustworthy signal (immune to Apple
Mail Privacy Protection's pixel pre-fetch, which opens are not). Added a rounding guard so a sub-1% click
rate is omitted rather than rounded up to "about 1" — the exact overstatement trap this whole fix exists
to close.

**Jev's role here, explicitly noted:** ran both the old (raw) and new (unique) real sentences through
`honesty_calibration` — both scored 99% accurately_calibrated, unable to distinguish them, because Jev
judges a claim's tone against evidence stated in the same `state`, not against ground truth it has no way
to independently verify. Same class of "not a Jev finding" as Chapters 9 and 11 — found and verified by
computing real aggregate numbers directly and checking them against the project's own established research,
not via the checklist.

**Verified live:** aikyamjobs.org's real Aug-Sep period now renders *"You sent 17 newsletters this time.
Out of every 100 people who received one, about 36 opened it and about 2 clicked through to read more."*
— matching the corrected unique rates exactly. pattic.org's real 2-newsletter period also renders
correctly, including the improved/concern comparison logic now covering clicks too. Service restarted,
healthy.

---

## 2026-09-24 — Chapter 14: section F audit using Chapters 9/11 as real rollout case studies

**Context:** Worked section F's checklist (F.51/56/58) against `spf_missing` (revived in Chapter 9) and
`borrowed_sending_identity` (extended in Chapter 11) as real, fresh rollout examples.

**F.51/F.58 (story passes audience_fit before shipping):** Retroactively tested `spf_missing`'s real
story+why (tinkerhub.org): 72% `clear_as_is`, 78% `accurately_calibrated` — passes reasonably, no fix
needed. Also tested the shared generic-DNS tip (`spf_missing` and 5 other categories) against
`actionability`: scored 94% `vague_needs_specifics` even though this exact tip was already Jev-validated
and shipped in Chapter 1 specifically for being reassuring/reader-passive by design ("aikyam will take
care of it for you"). **Not a content bug** — a real limitation in the `actionability` criterion itself,
which doesn't cleanly credit intentional reader-passivity as `not_applicable`. Documented as a known
limitation in `jev/CRITERIA.md` rather than rewriting an already-good, already-validated tip based on a
score that doesn't mean what it looks like it means.

**F.56 (urgency framing justified by real severity) — a real asymmetry found and fixed.**
`_URGENT_STILL_OPEN_CATEGORIES` already includes `spf_lookup_limit` and `dkim_weak_key` with the stated
reasoning "undermine the proof that mail is genuinely theirs." Checked the set directly against
Chapters 9/11's two real categories:
- `spf_missing` (literally NO SPF record) is a strictly WORSE state than `spf_lookup_limit` ("SPF exists,
  but too complex") — yet only the milder sibling was in the urgent set.
- `borrowed_sending_identity` was never in the set either, despite the exact same "can never pass DMARC
  alignment" reasoning, and Chapter 11 confirmed real, sustained (144/156-day) cases exist right now.

Added both to `_URGENT_STILL_OPEN_CATEGORIES`. Verified the combined real text (story + the
"reach out to aikyam directly" CTA) on both tinkerhub.org and aikyamfellows.org's real open items:
`contradiction_check` 13% (low — the conditional, low-pressure CTA phrasing ["if you're not sure how to
fix what's above..."] doesn't clash with the existing "we're on it" framing), `emotional_resonance`
2.27/4 (reads as protective, not confusing), `audience_fit` 66% clear.

**Shipped in** `app/domain_report.py::_URGENT_STILL_OPEN_CATEGORIES` (2 additions) and `jev/CRITERIA.md`
(actionability limitation note). Verified live via `preview_domain_report()` for both real domains.
Service restarted, healthy.

**J deferred to a future chapter** — F's real findings (the urgent-set asymmetry, the actionability
limitation) were substantial enough on their own; didn't want to dilute either by rushing J in the same
round.

---

## 2026-09-24 — Chapter 15: section J, and a self-audit closing out the original 100

**J.91 (same category, two real domains, same cycle — consistency check):** `spf_missing` fires on both
tinkerhub.org and olimalarfoundation.org right now (from Chapter 9). Pulled both real rendered still-open
items directly: byte-for-byte identical story/why, no `detail` on either (consistent — `spf_missing` has no
dedicated detail generator, unlike `borrowed_sending_identity` now does). Checked clean, no fix needed.

**J.98 (quiet-cycle report shape):** Found 5 real domains with a genuinely empty cycle (nothing open,
nothing resolved) and read one full real report front to back (aikyamsolve.org). The existing
"omit empty sections rather than pad them" discipline already produces a coherent, non-awkward shape —
no "TIPS FOR THE NEXT FEW WEEKS" heading over nothing, no still-open/resolved headers with nothing under
them. Checked clean; this use case's underlying worry ("does the full template read badly when nothing's
there") turned out to already be handled correctly by rules built in earlier sessions.

**J.100 — the real work of this chapter: a self-audit of `USE_CASES.md` against 14 real chapters.**
Went through every chapter's actual findings looking for RECURRING techniques that produced real value but
were never written down as their own checklist item. Found six, added as a new addendum section (101-106,
`jev/USE_CASES.md`) rather than renumbering into the original 100 (kept as a stable historical reference):

101. Check the real code path directly before testing wording — the single most valuable technique of
     the whole audit (Chapters 5, 7, 9, 11, 13).
102. Audit a category's dashboard-facing completeness (label/help/priority-order), not just its report
     story, whenever wiring up a category (Chapters 7, 9).
103. Audit gating SETS for internal consistency against their own stated reasoning, not just whether one
     addition is individually justified (Chapter 14).
104. Simulate a detection-logic/threshold change against the WHOLE portfolio before shipping (Chapters 9,
     11) — distinct from F/J's wording-focused checks.
105. Before trusting a low Jev score, check whether the criterion can even meaningfully judge this content
     (Chapters 8, 14's scope caveats).
106. Verify a memory note that says something is "blocked" before working around or skipping it — an
     entire section (H) sat unaudited for 12 chapters on a stale assumption (Chapter 13).

**Shipped this chapter:** `jev/USE_CASES.md` only (the addendum). No `app/` changes — every content check
came back clean, and this chapter's real value was closing the loop on the workflow's own documentation
rather than fixing report content. A legitimate chapter shape, same as Chapter 10's research round.

---

*(15 chapters in. Tally against the now-106-item checklist: roughly 20 items explicitly closed out
(fixed or checked-clean) across A/B/C/E/F/G/I/J, D and most of H still real but low-priority given current
real data availability, plus 6 process/methodology items (101-106) now codified.)*

## 2026-09-24 — the general `USE_CASES.md` sweep is paused; two new priorities

User: "just keep this as a plan for us to do later, do not forget when we discuss pending items please."
The sequential section-by-section sweep (D, most of H, whatever's left of A/B/C/E/F/G/I/J) is paused, not
abandoned — resume it when asked. Two new, specific priorities take over from here: (1) audit and improve
the criteria behind DMARCTool's domain health/"vibe" score, the same Jev-assisted way; (2) audit and fix
the client email report's prose for AI-generated writing tells (em-dashes, repetitive phrasing), plus
whatever new sections/details Jev validation surfaces along the way.

## 2026-09-24 — Chapter 16: the domain health/"vibe" score contradicted the project's own research

**Context:** Priority 1. `domain_vibe_verdict()` (`app/verdicts.py`) is literally named "vibe" — the
one-glance dashboard read of `snapshot_domain_health()`'s composite 0-100 score
(`app/analysis.py`), which also feeds the client-facing `_health_trend()`/`_health_timeline()` sections of
the email report ("Your overall email health is X out of 100").

**The real problem, found by reading the formula against already-established project research (no Jev
needed for this part — a direct internal-consistency check, same method as Chapters 5/7/9/11/13):**
`dmarctool_deliverability_model.md` already ranks what actually drives inbox placement: (1) spam
complaint rate — the only hard published limit, (2) bounce rate/list hygiene, (3) engagement — "decides
inbox vs. Promotions vs. Spam once authentication passes", (4) authentication — explicitly described as
"cheap, usually one-time ESP config". The actual health-score formula weighted authentication (pass_rate)
**highest at 40/100**, had Postmaster spam rate at 25, bounce at 20, ESP complaint rate at 15 — and **no
engagement component at all**. A score shown directly to clients ("your health is X/100") never reflected
whether anyone actually opened or clicked their mail, and weighted the cheapest, least-informative signal
heaviest.

**Proposed reweighting**, following the existing research directly rather than inventing new priorities:
pass_rate 40→20, postmaster spam rate unchanged at 25, bounce rate 20→25, ESP complaint rate 15→10, new
engagement component (real unique-click rate, using the same fix Chapter 13 applied to `_newsletter_reach`,
benchmarked against the existing nonprofit-sector `campaign_click_benchmark` setting — clicks over opens
per that same research: MPP-immune, opens aren't) at weight 20.

**Validated against the real portfolio before shipping** (simulated old vs. new for every domain): most
domains barely move (many at 100 either way, no engagement data to change them). Two real domains with
real newsletter data shift substantially: aikyamjobs.org 95.5→83.7, pattic.org 74.3→62.4 — both driven by
real click-through rates (1.5-1.6%) sitting well below the 3.3% sector benchmark, previously invisible to
the score entirely. **Presented this exact swing to the user before shipping** (a client-facing number
changing meaningfully for real domains is a real judgment call, not a bug fix) — user confirmed: "Ship as
designed."

**A second real problem found while verifying the fix**: `domain_health_snapshots` is idempotent per
domain per day, so simply shipping the new formula would have produced a next report literally saying
"your health has slipped from 95 to 84" — comparing a new-formula score against an old-formula prior
snapshot, which is not a real change in the domain's behavior at all, just a change in how it's measured.
Confirmed this would have shipped a real, misleading client-facing sentence. Fixed by clearing
`domain_health_snapshots` entirely (its own docstring already calls it "pure derived history, safe to
recompute" — every reader in the codebase already handles zero rows gracefully) and letting it rebuild
fresh under the new formula. Verified: the same two real domains now correctly show the "not enough
history yet to compare" framing instead of a false regression.

**Shipped in** `app/analysis.py::snapshot_domain_health` (new weights + click_rate component),
`app/db.py` (new `click_rate` column via the existing `_ensure_columns` migration pattern),
`app/verdicts.py::domain_vibe_verdict` (tier text now names engagement alongside the signals it already
named). Verified live against the real portfolio (31 domains), confirmed via `preview_domain_report()`
that both real shifted domains render correctly. Service restarted, healthy.

**Secondary finding, not chased this round**: ran the new `_health_trend()` sentence through Jev anyway —
`honesty_calibration` came back only 56% accurately_calibrated (43% overstates) and `usefulness` only
1.21/4, independent of the reweighting itself. `_health_trend`'s wording ("Overall, your email health is
{band} -- we score it {score} out of 100") predates this session and wasn't part of what was asked for
here — folding a rewrite of it into Priority 2 (the report-prose pass) rather than patching it ad hoc.

---

## 2026-09-24 — Chapter 17: report prose, first pass — the worst repetition tell, plus 4 em-dash fixes

**Context:** Priority 2. First real bug found immediately while reading a live report for this (unrelated
to style): `_borrowed_identity_detail()` (Chapter 12) returns a string already ending in a period, and the
template also appends one, producing "...156 days..". Fixed in `app/domain_report.py`.

**New criterion added**: `natural_voice` (`jev/CRITERIA.md` #8, `app/jev_context.py`) — Choice,
`reads_like_a_person` / `reads_like_generated_boilerplate`. Distinct from `audience_fit` (jargon/complexity,
not style) and `repetition_risk` (same fact across cycles, not the same phrase within one read).

**The single worst finding: "We're on it, and we'll tell you when it's done." appeared 4 times, verbatim,
back to back**, in a real aikyamfellows.org report — once per still-open item. Tested: `natural_voice` 87%
`reads_like_generated_boilerplate`, `repetition_risk` 98% `reads_as_boilerplate`. The single most
noticeable "templated" tell found in the whole report. **Fixed in the two EMAIL report templates**
(`email_report.txt`/`.html`, the actual deliverable per the user's standing scope instruction): removed the
per-item trailer, replaced with one consolidated sentence after the whole list ("We're already working
through all of this, and we'll let you know as each one clears."). Also removed a second, now-redundant
hardcoded reassurance paragraph ("Whatever needs fixing here, aikyam will make sure it gets sorted
properly...") that repeated the same sentiment again near the closing. Verified: `repetition_risk` dropped
from 98% to 23% on the real fixed section. `client_report.html` (the interactive view) has its own
milder version of the same pattern ("We're on it.") — left untouched, per the user's standing instruction
not to invest further effort there.

**Four individual em-dash rewrites, each tested old vs. new before shipping:**
- `dns_drift`'s story ("...currently different from what we expect it to say -- either X, or it's Y"):
  74%→52% `reads_like_generated_boilerplate`. Split into two plain sentences.
- The still-open duration note ("...open since {month} -- still on our list, not forgotten"): tested 3
  variants; "We've known about this since {month}, and it hasn't slipped off our list" won (90% vs. 80%
  `reads_like_a_person`) — also drops "not forgotten," a phrase that defends against an accusation nobody
  made.
- The policy-unchanged line ("Same level as last time -- still watching before raising it further"): a
  naive split (just replacing the dash with a comma) scored WORSE (61% vs. 53% boilerplate) — the fix that
  actually worked wove it into one sentence instead: "It has stayed at that level since last time, while we
  keep watching before turning it up further" (65% `reads_like_a_person`). Real lesson: removing the
  em-dash mechanically isn't the fix by itself; the sentence needs to actually flow.
- `_health_trend()`'s 4 branches (all had an em-dash): dashes removed from all 4. The "steady" branch
  moved 97%→69% `reads_like_generated_boilerplate`, the clearest single win. `usefulness` stayed low
  (~1/4) regardless of the dash — a deeper rewrite of this sentence's whole framing is flagged as a
  follow-up, not solved this round (already noted as a Chapter 16 secondary finding).

**Scope, stated plainly:** `app/domain_report.py` has ~149 occurrences of "--" across its story/tip/why
text. This chapter fixed the highest-impact ones (everything that rendered in the one real report used for
testing, plus the worst repetition tell found in it). ~145 remain across dozens of other strings not yet
tested — this is genuinely a multi-chapter effort, not something to claim finished after one pass.

**Shipped in** `app/domain_report.py`, `app/templates/email_report.txt`, `app/templates/email_report.html`,
`app/templates/client_report.html` (only for the shared trailer-repetition bug fix — not otherwise touched,
per the user's standing instruction to focus on the email report and not invest further in the interactive
view), `jev/CRITERIA.md`, `app/jev_context.py`. Verified live against aikyamfellows.org's real report,
full before/after read. Service restarted, healthy.

---

## 2026-09-24 — Chapter 18: em-dash sweep, round 2 — prioritized by real domain count

**Method:** parsed `_PROBLEM_STORY`/`_TIP_LIBRARY`/`_WHY_IT_MATTERS`/`_POSTMASTER_REQUIREMENT_STORY` for
every entry still containing "--" (13 entries found), then cross-referenced against real currently-open
categories portfolio-wide, prioritizing by how many real domains each one actually affects right now.

**Fixed — `_POSTMASTER_REQUIREMENT_STORY`, affects the 8-domain `postmaster_compliance` category:**
- `SPF_AND_DKIM`: 75%→48% `reads_like_generated_boilerplate`. Split "...you -- the proof that..." into two
  sentences.
- `USER_REPORTED_SPAM_RATE`: 91%→60%. Same split pattern.
- Neither is currently the MOST RECENTLY UPDATED requirement for any of the 8 real domains (checked before
  claiming impact — `DMARC_POLICY`/`DELIVERABILITY` currently win that slot for the domains tested), so
  these specific fixes aren't live yet for any real send, but are real content that will render once
  those requirements are the most recent again.

**Fixed — `dkim_alignment_gap`'s all 3 texts (story/tip/why), affects 2 real domains
(catsofkochi.com, captains.ngo), and resolves a gap flagged back in Chapter 5:**
- Story: 82%→31% `reads_like_generated_boilerplate`. Chapter 5 had already flagged this story's
  `audience_fit` as still needing work (best prior attempt: 36-46% clear); this round's rewrite trades a
  few points of `audience_fit` (78%→85% needs-pass — a real, acknowledged cost) for a much larger
  `natural_voice` gain. `repetition_risk` on the story alone also improved slightly (81%→79%).
- Tip: dropped the em-dash, matches the established "one-time setting, aikyam handles it" tip pattern.
- `_WHY_IT_MATTERS`: rewritten to add NEW information (funder/donor trust consequence) instead of
  restating the story's own "one way, not two" mechanism, which is what made the combined story+why
  redundant in the first place.
- Note on the combined story+why+trailer test: still scored 97% `repetition_risk` — this is the
  already-documented, already-mostly-solved cross-cycle repetition problem from Chapter 5 (the duration
  note only kicks in once an item's been open 30+ days; this real item opened 2 days ago, so it hasn't
  triggered yet). Not a new failure of this round's wording fix.

**Checked, no action taken (2 items):**
- `lookalike_domain`'s story (7 real domains, the highest domain-count of anything tested this round):
  already scores 68% `reads_like_a_person` as-is. Two rewrite attempts tested — neither improved
  `natural_voice` meaningfully, and one measurably hurt `honesty_calibration` (78% vs. the ~90%+ the
  original scored in Chapter 4). Left unchanged rather than trade a real win for an uncertain one.
- The shared generic-DNS tip ("...aikyam will take care of it for you", affects `spf_missing`'s 3 domains
  among 6 total categories): scores a borderline 56% boilerplate. One rewrite attempt tested WORSE (61%).
  Already extensively validated for `actionability`/clarity in Chapter 1 — left unchanged rather than
  degrade something that's already working well on other axes for an unproven style gain.

**Shipped in** `app/domain_report.py` only this round (no template changes). Verified live against
catsofkochi.com and captains.ngo's real rendered reports. Service restarted, healthy.

**Running tally**: ~143 of the original ~149 "--" occurrences remain. Two more real "checked, no action"
results this round are still valuable signal — not every em-dash is actually hurting the sentence it's in.

---

## 2026-09-24 — Chapter 19: past the category dicts — every currently-open real category was already
handled, so this round swept the structural functions that render regardless of which categories are open

**Context:** checked first (per use case 101/106's own discipline): every real currently-open category's
em-dash text had already been fixed or checked in Chapters 17-18. Rather than force-fix dormant categories
next, broadened the search past `_PROBLEM_STORY`/`_TIP_LIBRARY`/`_WHY_IT_MATTERS` to the structural
functions that fire in most/all real reports regardless of which specific categories happen to be open:
`_incident_recurrence`, `_risk_warning`, `_list_hygiene`, `_ALL_CLEAR_PHRASES`. Confirmed via grep across
the whole file that essentially every remaining real return-value f-string/string-literal em-dash has now
been surfaced (the ~139 remaining "--" occurrences left after this chapter are overwhelmingly in
docstrings/comments, not user-facing text).

**Fixed — 4 real strings:**
- `_incident_recurrence`'s RESOLVED branch (recurring problems that got fixed again): 80%→87%
  `reads_like_a_person`. Real applicability confirmed broadly — many real domains have 2+ distinct
  recurrence days on reader-facing categories (dns_drift, new_sender, postmaster_compliance,
  mailgun_reputation, failure_investigation, lookalike_domain).
- `_risk_warning`'s trailing clause — a real dash missed during the Chapter 6 rewrite of this same
  function: 69%→76%.
- `_list_hygiene`'s chronic-transient-bounce addition: **89%→54%, the single biggest natural_voice gain
  of this round.** Confirmed real: pattic.org has real chronic-bounce items resolved on 2026-09-17.
  Verified via a historical `_build_context()` reconstruction of that real period.
- `domain_expiring_soon`'s tip: 68%→78%.

**Checked, no action (2 items) — the sweep's discipline holding up again:**
- `_ALL_CLEAR_PHRASES`'s 2 dashed variants: a plain period-split produced **zero movement** (69%→69%,
  identically) on both. Tried two further, more substantial rewrites — best only reached 63%. Left
  unchanged: these exist specifically as a deliberate 3-way rotation for variety (Chapter 2), and the
  modest gains available didn't justify disrupting that designed set for an unproven edge.
- `blocklist`'s tip: a period-split rewrite scored WORSE (63%→55%) than the original. Left unchanged.

**Shipped in** `app/domain_report.py` only. Verified live: pattic.org's real chronic-bounce text via
historical reconstruction, service restarted, healthy.

**Running tally**: ~139 "--" occurrences remain in the file, but a full-file grep for real return-value
strings suggests the vast majority of what's left is docstrings/comments rather than user-facing text.
Worth re-confirming this with a more rigorous pass before declaring the sweep done, but the highest-value
work is very likely complete.

---

## 2026-09-24 — a new standing priority supersedes the em-dash sweep: comprehensive report rebuild

User: "before 20, i would like to make the email report much better... do not think about restricting
space and content... we will slowly move this report into a pdf format later so whatever we need that time
can start from now also in the content point... run it with criteria and rules and feed everything to jev
ai." Standing constraint reaffirmed: no real/test emails, ever.

Went through a full plan-mode research + design cycle (2 Explore agents surveying all 14 dashboard-only
modules + the current report structure/PDF-readiness, 1 Plan agent designing the build) before touching
code, given the scale of the ask. Full plan approved by the user and saved at
`/Users/jinsoraj/.claude/plans/splendid-tumbling-summit.md`. Key conclusions:

- **Keep the foundation, expand don't rebuild.** The current 21-section report structure and its voice/
  honesty/anti-repetition machinery are mature and validated against 9 real domains across 19 prior
  chapters — the ask is genuine expansion, not a teardown.
- **Verdict on all 14 hidden-data modules**: 5 YES (campaign_score reframed, source_classification gated,
  safe_browsing/display_name/content_scoring detail — all dormant, zero real rows yet but confirmed wired
  into the real pipeline), 1 DEFER (mta_sts, no real broken record exists), 8 NO (operator-only jargon,
  already covered elsewhere, shared-infrastructure misattribution risk, or explicitly a PDF-phase-only
  concern like `charts.py`'s SVG rendering).
- **Build sequence re-prioritized by real data availability** (not code-pattern simplicity, correcting
  the Plan agent's initial ordering after checking the live DB directly): `campaign_score.py` and
  `source_classification.py` have real, substantial data right now; the other three have real, working
  detectors that simply haven't found anything yet (checked: zero rows, ever, portfolio-wide) — real but
  dormant, ship later.

## 2026-09-24 — Chapter 20: Round 1 — a real per-campaign standout, not a hidden grade

**Checked real data before designing anything** (per use case 101's own discipline): computed
`score_campaign()` for all 17 of aikyamjobs.org's real campaigns. Found the one real, differentiating
signal (engagement vs. the sector benchmark) is **already** what `_newsletter_reach()` reports in
aggregate — the 6-pillar scorecard's real content substantially overlaps with either `_newsletter_reach`
itself (complaints/hygiene/engagement) or two already-existing, already-wired categories
(`campaign_compliance_issue` covers the "technical" pillar; `content_spam_risk`/`subject_spam_risk` cover
"structure"/"wording"). This reshaped the round: rather than surface a "worst pillar" (redundant), surface
a **specific, real, named standout send** — something `_newsletter_reach`'s own aggregate blend can never
show, since it scores PER campaign while the aggregate blends the whole period into one number.

**Real, confirmed spread**: aikyamjobs.org's 17 campaigns scored 83-95 (a real 12-point range, all
`confidence="high"`) — genuine variation, not noise.

**Design, deliberately conservative**: `_standout_campaign_note()` only speaks up with 2+ high-confidence
scores AND an 8+ point spread, so a normal, consistent period stays silent rather than manufacturing a
"standout" out of nothing (same "no filler" discipline as every prior chapter). Never exposes the
score/grade itself — CONTEXT.md's audience "fears technology" and needs to feel safe, not graded; a
literal "B" or "83/100" would read as a report card, the opposite of the intended effect.

**Jev-tested before shipping**: baseline (no standout note) scored usefulness 0.48/4; adding the named
standout moved it to 0.67-0.72/4 depending on phrasing. `audience_fit` stayed strong both ways (83-89%
clear). `emotional_resonance` stayed near-zero both ways — expected and correct, this is a status update,
not a trust-building moment.

**Shipped in** `app/domain_report.py::_standout_campaign_note` (new) + `_newsletter_reach` (wired in).
Verified live: aikyamjobs.org's real report now correctly names 2026-08-11 (its real highest-scoring
send) as the standout; pattic.org's real 4-campaign period correctly names its own real standout
(2026-09-12); a 1-campaign period correctly stays silent (the `count >= 2` gate). Service restarted,
healthy.

---

## 2026-09-24 — Chapter 21: Rounds 2-4 in one pass

**Round 2 — source_classification summary: tested twice, real reason not to ship.** Found the cleanest
real test case first: pattic.org has 99.5% aligned mail (345,599 of 347,461 real messages) and real
forwarded volume (1,208 messages) — crucially, **zero current `borrowed_sending_identity` findings**, so
no reconciliation risk for this specific domain. Drafted a positive reassurance sentence ("...about 99
clearly check out as genuinely yours...") and tested 3 real variants, iterating on wording each time.
`honesty_calibration` never cleared ~54% accurately_calibrated on any of them (42-47% overstates on every
attempt). Root cause, not a wording problem: `source_classification.py`'s own docstring already says these
are "inferences from counts, not facts a report states outright" — any confident-sounding reassurance
built on a hedged classifier reads as overstating it, no matter the phrasing. This matches Chapter 10's
original finding on this same content area (a narrative version there also lost accuracy vs. a
numbers-grounded one). **Not shipped.** Real, honest limitation of this content, documented rather than
forced through on a 4th attempt.

**Round 3 — safe_browsing threat-type detail: shipped, dormant.** New
`_safe_browsing_detail(conn, domain_id)`, mapping Google's real 4 threat-type values (confirmed from
`app/safe_browsing.py::THREAT_TYPES`) to plain English. Tested 3 phrasings: a bare "flagged for X" scored
worst on `honesty_calibration` (25-45% accurately_calibrated) — stating Google's classification as settled
fact overstated a probabilistic signal that can misfire after a compromised plugin/theme. Naming that
possibility explicitly (matching what the existing tip already tells the reader) moved it to 56%. Wired
into `_still_open_items`. Verified logic against single/double/triple-threat-type cases via an in-memory
test DB (never touching real production data) — all joined and rendered correctly. Zero real domains have
ever been flagged (checked live) — dormant, same precedent as `risk_warning`/`mta_sts_broken`.

**Round 4 — display_name detail(s): shipped, dormant, strong scores on the first real attempt.** Traced
exactly where `check_display_name()`'s output is persisted (`app/ses_events.py` lines ~557-570) — found
the stored `action_items.detail` text joins multiple issues with a single space and no reliable delimiter,
too fragile to re-parse. New `_display_name_detail()` instead re-derives the issue list by calling
`check_display_name()` directly against the domain's real, currently-stored `from_display_name`/
`from_address` — exact by construction, no parsing risk. Tested on a constructed real-shaped example
("ACT NOW UPDATES" — ALL CAPS): **94% clear_as_is, 93% accurately_calibrated on the first draft**, no
iteration needed. Also fixed `display_name_inconsistent`'s already-real stored detail ("Names seen: X, Y,
Z.") which reads like a log line, not a sentence — reworded to "Recently it's gone out under a few
different names: X, Y, and Z," tested old vs. new: usefulness 1.95→2.12/4, audience_fit 64%→86% clear,
natural_voice 71%→82% reads_like_a_person. Both wired into `_still_open_items`. Verified via an in-memory
test DB — real multi-issue joining logic confirmed correct (3 issues joined with commas + "and"). Zero
real domains have ever triggered either category — dormant, real and wired-in.

**Shipped in** `app/domain_report.py` only: `_safe_browsing_detail`, `_SAFE_BROWSING_THREAT_STORY`,
`_display_name_detail`, `_DISPLAY_NAME_ISSUE_STORY`, `_display_name_inconsistency_detail`, all wired into
`_still_open_items`'s elif-chain (no template changes needed — same `item.detail` slot already used by
prior detail generators). Verified live against real domains (`preview_domain_report()` returns `None`
correctly for the currently-clean real cases, no crash). Service restarted, healthy.

**Rounds shipped this session: 3 of 5** (Round 1 Ch.20, Rounds 3-4 this chapter). Round 2 tested and
honestly not shipped. **Round 5 remains** (content_scoring specific-phrase detail — the one flagged for
needing the most Jev iteration given real accusation-risk).

---

## 2026-09-24 — Chapter 22: Round 5, the last of the five — content_scoring specific-phrase detail

**Flagged from the start (the plan) as needing the most care**: naming a specific flagged phrase risks
reading as an accusation rather than a helpful explanation, more than any of the other 4 rounds.

**Design**: `_content_risk_phrase()` translates one real `app.content_scoring.score_text()` flag into a
plain clause, and `_content_risk_detail()` re-derives the flag list by calling `score_text()` directly
against the domain's most recent real campaign subject/body — same "re-derive, don't re-parse the
already-joined stored detail text" pattern Round 4 established for display names (the real stored detail
here has the identical `" ".join(flags)` fragility). Only names ONE concrete example (the first real
flag), not an exhaustive dump — one clear, real example over a list, matching this audience's needs.

**Jev-tested, 2 iterations**: draft 1 ("...used the phrase 'act now', which spam filters are trained to
watch for -- not a judgment...") scored `honesty_calibration` 87%, `audience_fit` 56% clear. Draft 2
(simplified the "trained to watch for" clause) moved `audience_fit` to 60% and `honesty_calibration` to
89% — the explicit "not a judgment on your newsletter, just something that can make spam filters more
suspicious" hedge is what keeps this category's honesty score high, the same lesson Round 3's
`safe_browsing` detail already taught (stating a filter's pattern-match as settled fact overstates it).

**Verified via an in-memory test DB** (never touching real production data): "act now" (a real high-risk
phrase), "ALL CAPS SHOUTING HEADLINE" both correctly detected and translated. Verified against real data:
aikyamjobs.org's real, currently-clean newsletters correctly return `None` — no crash, no false positive.

**Shipped in** `app/domain_report.py::_content_risk_phrase` + `_content_risk_detail`, wired into
`_still_open_items` for both `content_spam_risk` (body) and `subject_spam_risk` (subject). Service
restarted, healthy, confirmed live at the user's request so the change is visible in the running tool.

## All 5 rounds of the comprehensive report expansion — summary

| Round | What | Outcome |
|---|---|---|
| 1 | `campaign_score.py` → named standout send | Shipped (Ch.20) — real data reshaped the design mid-build |
| 2 | `source_classification.py` → source-mix reassurance | **Not shipped** (Ch.21) — real, honest limitation: any confident claim on hedged inference data overstates it |
| 3 | `safe_browsing.py` → threat-type detail | Shipped (Ch.21), dormant |
| 4 | `display_name_checks.py` → specific-issue detail (2 categories) | Shipped (Ch.21), dormant, best first-draft scores of the session |
| 5 | `content_scoring.py` → specific-phrase detail (2 categories) | Shipped (Ch.22), dormant, most-scrutinized per the plan |

4 of 5 rounds shipped real content; the 1 that didn't ship has a documented, real reason rather than being
forced through. The comprehensive-rebuild priority is now substantially complete for this pass — remaining
open threads are the paused general `USE_CASES.md` sweep and the paused em-dash/AI-voice prose sweep
(`jev/DECISIONS_LOG.md` Chapters 17-19), both explicitly kept as "a plan for later, don't forget."

---

*(Next entry: resume the paused em-dash sweep, or the paused general USE_CASES.md chapter cadence —
whichever the user asks for next.)*

## Chapter 23 — Email report visual/UI redesign, Round 1: foundation

New priority, superseding the paused em-dash/USE_CASES threads for now (user: "colors, fonts all
decision run it through jev ai... dashboard elements, chart elements... should really make my audience
feel it important and readable"). This is the visual counterpart to the Chapters 20-22 content rebuild —
same email_report.html/.txt files, tied to the same eventual PDF migration.

**Full plan-mode cycle run first** given the scale: 2 Explore agents (rendering-pipeline/asset inventory +
real per-domain chartable-data depth) + 1 Plan agent (engineering design), plus live web research on
email-client rendering constraints. Plan saved at
`/Users/jinsoraj/.claude/plans/splendid-tumbling-summit.md` (overwritten from the completed Chapter 20-22
plan — a different task, not a continuation).

**Key finding that shaped everything**: DMARCTool has no public web exposure and Mailgun's send API has
no `cid:` inline-attachment path, so any real chart must be inline `<svg>` markup, never an `<img>`. This
is exactly what `app/charts.py`'s 9 existing SVG functions already produce, just wired for the dashboard's
live CSS (`currentColor`/`var(--ok)`) — which won't survive an emailed HTML file. Real per-domain data is
uneven: richest is `mailgun_daily_stats` (76-105 daily points, 5-7 domains) and current-period DMARC
totals (broadest — works for any domain with any report data this period); narrowest is `ses_campaigns`
newsletter engagement (only aikyamjobs.org and pattic.org have any). `domain_health_snapshots` (the
0-100 score) is dormant for trend purposes right now — only 1 row/domain, the table was cleared during
the recent scoring-formula change (Chapter 16) — so no health-score trend chart this round.

**An honest scope-setting note stated plainly to the user before building**: Jev is a text-only judgment
model, it cannot see pixels/colors/rendered layout. It validates wording (new labels/captions) and
higher-level emotional-framing questions posed in words — color/type/layout craft itself is applied
design judgment informed by the existing brand system + accessibility research, not something Jev
directly reviews.

**Round 1 shipped**: full palette/font token swap (literal hex reusing the *original* `style.css` brand
tokens, not `client_report.html`'s already-drifted copy — warm cream `#FBF7F4`, ink `#1F2421`, brand
purple `#7358B3` accent, flattened `rgba()` tints to solid hex for Outlook.com/Android-Gmail
compatibility); web-safe font stack (`Helvetica Neue`/Arial body, one Georgia-serif display moment on the
greeting line only — custom webfonts render in only ~51% of opens per live research, so no `@font-face`
anywhere); table-based scaffolding replacing the old flat `<div>` card (`<table role="presentation">` +
`bgcolor` attributes, not just CSS `background`, since Outlook honors the attribute more reliably);
`<meta name="color-scheme"/"supported-color-schemes" content="light">` to force light mode, matching
`client_report.html`'s own precedent of not following system dark mode; a new KPI stat-tile strip
(delivery-rate %, health-score /100, both fed from the same locals the existing prose already computes —
`delivery_rate_pct`/`health_score_value` — never parsed back out of that prose); a 5-zone reorg merging
the 18 existing context keys with 2 real moves (`risk_warning` now sits after `still_open` as an
escalation of the action ledger, not orphaned between `list_hygiene`/`deliverability`; `list_hygiene`
moved into the new merged "Getting through, and staying protected" zone alongside `deliverability`,
`protection`, and `spam_trend`, replacing 3 separately-emoji-headed sections with 1); all emoji section
markers dropped in favor of small uppercase eyebrow-style labels (matching `client_report.html`'s own
brand pattern).

**Jev-tested (the one new sentence this round introduces)**: the merged Zone-D heading. 3 candidates
tested with `audience_fit`+`natural_voice`: "How your emails are arriving & staying protected" (91%
clear/84% person), "Delivery and protection, together" (85%/83%), and the winner, **"Getting through, and
staying protected"** (92% clear_as_is, 88% reads_like_a_person) — shipped. Nothing else in this round
changes wording, so nothing else needed a Jev pass, per the plan's explicit per-round scoping (stated
rather than defaulting to "run everything through Jev" when there's no new prose).

**Code changes**: `app/domain_report.py` gained `_latest_health_score()` and `_health_score_trail()`, both
extracted from `_health_trend()`/`_health_timeline()`'s own inline queries (re-derive, don't duplicate —
the same discipline as every `_xxx_detail()` helper) so the KPI tiles and the existing prose sentences can
never disagree about the same underlying fact. `build_domain_report()` now also returns
`delivery_rate_pct`, `health_score_value`, `health_timeline_delta`, `health_timeline_since` — the last
two stay `None` portfolio-wide right now (same dormant-but-wired precedent as the Chapter 20-22 rounds),
lighting up a 3rd KPI tile automatically once `domain_health_snapshots` accumulates 3+ months.

**Verified against real data**: all 33 real domains in the portfolio rendered both `email_report.html` and
`email_report.txt` with zero Jinja errors. Live HTTP checks (200 OK) via `/domain/{name}/report_preview`
on aikyamjobs.org (richest — confirmed 100% delivered / 84/100 health tiles, correct 2-tile 50/50 width
since the 3rd tile is absent, merged Zone D correctly shows all 3 sub-labels, `still_open` correctly
precedes the now-absent-for-this-domain `risk_warning` slot), pattic.org, aikyamfellows.org (longest
history), and aikyamsolve.org (the "nothing needed fixing" fallback path, confirmed still renders).
No real domain currently has `risk_warning` active, so the reordered position couldn't be visually
confirmed live this round — structurally verified in the template instead (correctly the last block in
Zone C, before Zone D opens).

Service restarted, healthy. No email sent or triggered.

---

*(Next entry: Round 2 — the delivered-safely ring, the first real chart in the email report.)*

## Chapter 24 — Email report visual/UI redesign, Round 2: the delivered-safely ring

First real chart in the email report. Proved the email-safe SVG mechanism on exactly one function
(`disposition_donut_chart`) before touching the other 3 planned for later rounds, per the plan's own
sequencing.

**`app/charts.py`**: added an optional `colors: dict | None = None` param to `disposition_donut_chart`.
Default (`None`) is byte-identical to the prior behavior (`currentColor`/`var(--ok)` etc, CSS-class-based
text labels) — every existing dashboard/`client_report.html` call site is untouched, confirmed by leaving
`app/web.py`'s `/domain/{name}/client_view` route's own `disposition_donut_chart(...)` call unmodified.
When `colors` is passed, arcs use the literal hex from the dict and text labels switch from CSS classes
(`donut-center-label`/`-sublabel`, which have no effect without `client_report.html`'s loaded stylesheet)
to inline `style=` attributes — a real detail the plan's Plan-agent pass hadn't fully spelled out until
implementation: the SVG's *text* theming needed the same treatment as its *arc* theming, not just the
strokes.

**`app/domain_report.py`**: new `_EMAIL_CHART_COLORS` constant (hex matching `app/static/style.css`'s
`:root` tokens exactly — ink `#1F2421`, ok `#1A7F37`, warn `#9A6400`, bad `#C0392B`, accent `#7358B3`,
muted `#7A746B`) and `_email_charts(conn, domain_id, period_start, period_end)`, called from
`_build_context()` so `send_report_now()`/`preview_domain_report()` always agree, exactly like every text
section already does. Sums `analysis.provider_breakdown()`'s `disp_none`/`disp_quarantine`/`disp_reject`
across all providers for the current period (broader-reach data than any history-based chart — works for
any domain with any report data this period, not just 3+ points, which is why this ships before the
trend charts) and builds two sizes from the same counts: a 110×110 for Zone D beside the `deliverability`
paragraph, and a 64×64 for KPI Tile 1 (replacing the plain percentage number — the donut already renders
the percentage in its own center text, so showing both would restate the same fact twice, the same trap
`build_domain_report()`'s own impersonation/blocklist mutual-exclusion logic already guards against
elsewhere).

**No Jev call this round** — no new prose, the chart pairs with `deliverability`'s already-approved
sentence and doesn't add a caption of its own.

**Verified against the full real portfolio**: all 33 domains render with zero Jinja errors. 22 domains
have real current-period disposition data (donut renders); 11 have none and fall back cleanly to
`deliverability`'s existing "we didn't get enough information" text with no leftover chart markup. Live
HTTP 200 checks confirm the real rendered SVG on aikyamjobs.org (100% pass, single green arc, hex colors
confirmed in the raw output, not `currentColor`) and the clean text-only fallback on catsofkochi.com (a
real zero-report domain). Service restarted, healthy. No email sent.

---

*(Next entry: Round 3 — spam-rate and bounce/complaint trend charts, the richest real historical data in
the whole portfolio.)*

## Chapter 25 — Email report visual/UI redesign, Round 3: trend charts

Second and third real charts. `colors` param (default `None`, byte-identical to prior behavior) added to
`spam_rate_sparkline` and `metric_trend_chart` in `app/charts.py` — both needed the full ink/warn/bad hex
treatment across every internal `currentColor`/`var(--x)` (axis text, gridlines, raw dots, the
no-threshold-crossed line color), not just the arc-level swap `disposition_donut_chart` needed in
Chapter 24. `metric_trend_chart`'s `thresholds` list was already fully caller-supplied (value, label,
color) tuples, so no chart-level change was needed there beyond the function's own internal defaults —
the email caller just passes literal hex in the threshold tuples directly.

**A real scope decision, not in the original plan text**: only ONE `metric_trend_chart` ships (bounce
rate), not two (bounce + complaint, which is what the dashboard shows). Reasoning: complaint volume is
thin enough portfolio-wide that a real chart would mostly be an uninteresting flat line, while bounce rate
is the metric `_list_hygiene()` already narrates *counts* for without ever showing the *rate* those counts
sit inside — genuinely new information, not a duplicate. Paired with `list_hygiene` visually but gated
independently on its own data (bounce-rate history is broader than `list_hygiene`'s own gate, which only
fires when there were suppression/chronic-bounce events specifically this period) so the chart doesn't
hide behind a narrower condition than its real data supports.

**`_email_charts()` extended**: `spam_rate_chart_svg` (`analysis.postmaster_daily_series`, 60 days, paired
with the existing `spam_trend` paragraph) and `bounce_rate_chart_svg` (`analysis.mailgun_daily_series`,
60 days, threshold from the real `mailgun_bounce_rate_warn` setting, same one the dashboard's own alert
uses). Both independently `{% if %}`-gated on real series length, matching `charts.py`'s own "not enough
history yet" convention.

**No Jev call this round** — both charts pair with already-approved existing sentences, no new captions.

**Verified against the full real portfolio**: zero Jinja errors on all 33 domains. Spam-rate chart renders
on 16 domains, bounce-rate chart on 18. Live-checked on aikyamjobs.org (real hex confirmed throughout the
raw SVG — no `currentColor`/`var()` leftover anywhere, a real red-threshold-crossing dot correctly colored
`#C0392B`, a real 7-day rolling average called out at 0.31%) and ilabindia.org (thin postmaster history —
11 real points, confirmed the chart renders sensibly rather than looking broken at low point-count, per
the plan's explicit validation target for this case). Service restarted, healthy. No email sent.

---

*(Next entry: Round 4 — the campaign-engagement table bars, the one genuinely new visual surface in this
redesign.)*

## Chapter 26 — Email report visual/UI redesign, Round 4: campaign-engagement bars

The one genuinely new visual surface in this redesign -- deliberately table-`<td bgcolor>` bars, not a new
untested SVG shape, per the plan's own reasoning: this is new content with real rendering risk, and
table-bars are the decades-proven ESP-standard technique for in-email progress bars, degrading to "a
table with a colored cell and a number" in the most hostile clients rather than a silent blank box.

**Re-derive, don't duplicate, applied one more time**: `_newsletter_reach()`'s rate math used to live in a
nested `_rates()` closure, invisible outside that function. Extracted to a module-level `_newsletter_rates()`
(pure computation, no I/O) and a new `_newsletter_engagement_bars()` that calls it against the same
`this_period` campaign rows `_newsletter_reach()` already filters -- so the bars and the prose sentence
share the literal same math and can never drift apart, same discipline as every prior round's KPI/chart
work. Returns `None` in exactly the same conditions `_newsletter_reach()` does (verified: 0 mismatches
across all 33 real domains), so the two stay gated together in the template with no separate condition to
maintain.

**A deliberate wording choice, not left implicit**: bar labels are "Opened"/"Clicked" -- exact one-word
echoes of `_newsletter_reach()`'s already-Jev-approved prose ("opened it"/"clicked through to read more"),
not a reword. Per the plan's own scoping for this round, an exact echo needs no separate Jev pass; a
reworded label would have. Flagged here as the judgment call it is, not silently assumed.

**Verified against the full real portfolio**: zero Jinja errors on all 33 domains. Only aikyamjobs.org has
real bars in the CURRENT live reporting period (pattic.org's historical campaigns from the data-inventory
check don't fall inside its current window) -- confirmed the rendered bars match the existing prose
exactly (20% opened, 1% clicked, both numbers identical to the sentence above them). aikyamfellows.org
(zero campaigns ever) confirmed a completely clean disappearance -- zero occurrences of the newsletter
heading in its rendered output, no leftover markup. Service restarted, healthy. No email sent.

---

*(Next entry: Round 5 — the pass-rate sparkline and a whole-report Jev checkpoint, closing out the
5-round visual redesign.)*

## Chapter 27 — Email report visual/UI redesign, Round 5: pass-rate trend + whole-report checkpoint

The 4th and final `colors` param, then the honesty check this whole 5-round redesign was building toward:
does the finished report actually read well as ONE document, not just as individually-approved pieces.

**`colors` param added to `pass_rate_sparkline`** (the simplest of the 4 -- only `currentColor`, no
`var()` at all). All 4 email-used chart functions in `charts.py` now share the same pattern; every default
stays byte-identical to pre-redesign behavior. Wired into a new `_email_charts()` key,
`pass_rate_trend_svg`, from `analysis.daily_pass_series(days=60)`.

**A real implementation deviation from the plan text, made and stated rather than silently decided**: the
plan suggested folding this into KPI Tile 3 (the same slot reserved for the still-dormant health-timeline
delta). Built instead as its own full-width chart in Zone D, beside the delivery-rate donut. Reasoning: a
trend line with real axis labels and date ticks needs more room than a 33%-width KPI tile can give without
becoming unreadable -- a legitimate design tradeoff, not a shortcut. Renders on 27 of 33 real domains (only
needs 2+ daily points within 60 days, broader than the plan's cited "5 domains with 3+ history points"
figure, which was policy_history's stricter bar, not daily_pass_series').

**Named validation target confirmed exactly as specified**: aikyamfellows.org shows the pass-rate trend
chart (real hex colors, real dates) while showing zero occurrences of the newsletter section -- the
graceful-absence pairing this round's plan called out by name.

**Full visual-consistency pass**: found and fixed one real leftover -- the `risk_warning` callout's border
was still the pre-redesign `#f3c6c1`, never updated to the new token set in Round 1. Fixed to `#EAC6C1`
(consistent with the new `bad`/`bad-bg` hue family). Confirmed zero emoji, zero stray old-palette hex,
zero `Georgia` usage outside the one intentional greeting-line accent, and zero `currentColor`/`var()`
leftovers in any of the 4 email-used chart functions.

**`.txt` zone-order parity**: confirmed still matches the `.html` zone order exactly (Round 1's reorder
holds; nothing in Rounds 2-4 touched section order, only added chart-adjacent content to `.html` alone).

**Full-portfolio sweep**: all 33 real domains render both templates with zero errors (re-confirmed twice
after two transient `database is locked` errors turned out to be the live background scheduler holding a
write lock on a separate script-opened connection, not a code bug -- resolved with a short retry, the live
app itself never errored, confirmed via its own request log). Found the real emptiest domain
(mail.folktaler.com, zero chart/KPI data anywhere) and confirmed it degrades completely cleanly -- the
whole KPI strip omits itself, no leftover markup, no crash.

**The whole-report Jev checkpoint -- a real, honestly-reported finding, not a clean pass.** Ran the
complete real `aikyamjobs.org` report (not an isolated sentence) through `audience_fit`, `usefulness`,
`emotional_resonance`, `honesty_calibration`, `natural_voice`:

| Criterion | Result |
|---|---|
| `usefulness` | 3.28/4 -- clearly useful |
| `emotional_resonance` | 3.05/4 -- clearly makes the reader feel looked after |
| `audience_fit` | 60% needs_plain_language_pass (borderline, not a clean pass) |
| `honesty_calibration` | 65% overstates_beyond_the_evidence |
| `natural_voice` | 95% reads_like_generated_boilerplate |

The two content-quality axes this redesign was actually aiming at (does it feel useful, does it feel
looked-after) score well. The two prose-voice axes don't. **Investigated rather than shipped blind**: two
follow-up tests isolated whether this round's own new content was the cause. Removing the new bare
"Delivered safely: 100%" / "Health score: 84/100" label:value lines from the `.txt` file barely moved
`honesty_calibration` (65%->62%) and `natural_voice` (95%->96%, no real improvement). Converting the
ALL-CAPS section headers to title case moved nothing either (95%->97% boilerplate, slightly worse).
**Conclusion: this is not something Round 5's visual work introduced or can fix.** It reads as a
whole-document-level property -- individually-approved sentences (each validated in isolation across
Chapters 1-22) compounding into a templated rhythm only visible when read start-to-finish in one pass,
which no prior chapter's per-sentence testing methodology could have caught. Per the plan's own framing,
this is grounds to flag a follow-up chapter, not to abandon the KPI-strip/chart structure this round
shipped (which the strong `usefulness`/`emotional_resonance` scores validate on its own terms). **This
finding should be the entry point for resuming the already-paused em-dash/AI-voice prose sweep
(Chapters 17-19, jev/DECISIONS_LOG.md) the next time general report-prose work is prioritized** -- it's
the same thread, now with a concrete whole-document reproduction instead of isolated examples.

Service restarted, healthy. No email sent.

## All 5 rounds of the visual/UI redesign — summary

| Round | What | Outcome |
|---|---|---|
| 1 | Palette/fonts/scaffolding/5-zone reorg/KPI strip | Shipped (Ch.23) |
| 2 | `disposition_donut_chart` colors param, delivered-safely ring | Shipped (Ch.24) |
| 3 | `spam_rate_sparkline`/`metric_trend_chart` colors, 2 trend charts | Shipped (Ch.25) |
| 4 | Campaign-engagement table bars (new surface, not SVG) | Shipped (Ch.26) |
| 5 | `pass_rate_sparkline` colors, polish, whole-report checkpoint | Shipped (Ch.27), real finding not fixed |

All 5 rounds shipped real, working content, verified against the full 33-domain real portfolio at every
step. The one honest gap: the whole-report checkpoint surfaced a real prose-voice issue this round's
scope couldn't fix, flagged clearly for the next general-content-quality pass rather than ignored or
force-patched.

---

*(Next entry: whatever the user prioritizes next -- the flagged whole-document natural_voice/
honesty_calibration finding above, the still-paused general USE_CASES.md sweep, or something new.)*

## Chapter 28 — Post-ship bug fix: the mini delivered-safely ring collided with its own text

User caught this live, in a real sent-eligible report, right after Chapter 27 shipped: "that circle just
covers the text and number written under it and also not aligned with the health score card."

**Root cause**: `disposition_donut_chart`'s 64x64 KPI-tile version (Chapter 24) put a 16px bold percentage
and a 10px sublabel INSIDE the ring's own hole, same as the working 110px Zone D version -- but at 64px
the donut's inner hole (computed from the same fixed stroke-fraction geometry) isn't wide enough for that
fixed-size text, so "100%" visually overran the ring's stroke on both sides. The 110px version never had
this problem because its larger hole comfortably fits the same fixed-size text -- the bug only existed at
the smaller size, which is exactly why the full-portfolio Jinja-error sweeps in Chapters 24-27 never
caught it (nothing crashes; it's a pure visual collision, invisible to an automated render check that
doesn't look at the actual pixels).

**Fix, matching an existing codebase precedent**: `health_score_sparkline` already has a `compact = height
< 90` mode that drops axis text entirely rather than shrinking it (documented reasoning: fixed-size text
doesn't scale gracefully, dropping it is more reliable than resizing it). Added the identical pattern to
`disposition_donut_chart` -- below 90px tall, both center-text elements are omitted entirely, leaving a
clean ring with no risk of text collision at any size. The KPI tile's ring shrunk from 64x64 to 40x40
(now a pure icon, no embedded text) and the percentage moved to sit beside it as separate HTML text at
the exact same 24px/700-weight style KPI Tile 2's health-score number already uses -- which also directly
fixes the alignment complaint, since both tiles now share identical typography and vertical structure
(one label line, one content line) rather than one tile being an SVG-with-embedded-text and the other
being plain HTML text at a different implicit size.

Verified against the full real portfolio (zero Jinja errors), confirmed live on aikyamjobs.org: the ring
renders with no center text at all, "100%" renders as a properly-sized number beside it. Service
restarted, healthy. No email sent.

---

*(Next entry: whatever the user prioritizes next.)*

## Chapter 29 — Investigating Chapter 27's whole-document finding: a real bug, a real fix, a real partial result

User asked directly: "is there a fix possible to sort all issues mentioned by jev" (re: Chapter 27's
weak whole-document `honesty_calibration`/`natural_voice` scores). Investigated properly rather than
repeating "not fixable" -- found one genuine logic bug and one real prose improvement, both shipped, both
measured. Honest result: real, meaningful improvement, not a full fix.

**Localized the problem by zone first**: split the real aikyamjobs.org report into its 5 zones and tested
each independently. Every zone scored 36-66% boilerplate/overstate on its own -- none anywhere near the
whole document's 95%/65% -- confirming the low whole-document score is a genuine compounding effect
across many individually-approved sentences, not one bad section.

**A real bug, not just prose**: rereading `_headline_verdict()`, found the branch for "non-urgent open
items exist" returned an `_ALL_CLEAR_PHRASES` rotation ("Nothing on your domain needs your attention this
time.") -- while the branch for "genuinely nothing open" returned `None`. Backwards. Confirmed live on
aikyamjobs.org: the real report said "Nothing on your domain needs your attention" directly above a real
"WHAT WE'RE STILL WORKING ON" section naming an actual open item (Google's low-confidence-mail flag).
Tested the combined headline+still-open block against Jev's `contradiction_check` +
`honesty_calibration`: current behavior scored **0.84 contradiction, 81% overstates**; removing the false
headline (return `None` instead, letting the still-open section speak for itself) dropped that to **0.45
contradiction, 25% overstates** -- more than a 35-point swing on the single clearest test in this whole
investigation.

**Fixed**: swapped `_headline_verdict()`'s branches so `_ALL_CLEAR_PHRASES` only fires when
`still_open_categories` is genuinely empty (the case it's actually true), and the non-urgent-open-item
case returns `None` (matching the docstring's own original design principle -- the still-open section
already states it accurately, a headline can only repeat or contradict it). **Found and fixed a new
duplication risk this created**: for the truly-empty case, the headline now says an all-clear phrase
*and* the existing "not resolved and not still_open" fallback box would ALSO fire its own near-identical
message -- confirmed live on aikyamsolve.org before the fix would have shipped two "nothing to worry
about" lines back to back. Fixed by adding `and not headline` to that fallback box's condition in both
`email_report.html` and `email_report.txt`, so exactly one home for that message exists per report.
Verified live: aikyamsolve.org now shows the headline once, the fallback box correctly stays silent
(1 occurrence of "Nothing" in the rendered text, not 2).

**Real portfolio impact**: 8 real domains had this exact false-contradiction live right now --
aikyamjobs.org, arpo.in, climatekhoj.com, folktaler.com, mail.aikyamhq.com, makestories.space,
pattic.org, send.mail.folktaler.com. All 8 now correctly show no headline (letting their real open item
speak accurately for itself) instead of a false all-clear claim.

**One more real, clearly-measured prose fix**: `_suppression_story()`'s tail claimed "Keeping a clean
list like this is **exactly** what helps your future emails land in the inbox" -- unwarranted causal
certainty (list hygiene is one factor among many, not "exactly" the thing that does it), plus a
"so...so" double-clause structure. Reworded to "Worth removing them from your own list too, for the same
reason." -- tested cleanly better on both axes (`honesty_calibration` 47%->34% overstates,
`natural_voice` crossed from 72% boilerplate to reads_like_a_person).

**One tested change NOT shipped, documented honestly**: tried 2 rewordings of `_explain_policy_for_owner`'s
"blocked completely, never even arriving" verb phrase. Results were noisy/inconsistent across repeated
Jev calls (one run showed improvement, a second showed the rewording scoring worse than the original) --
not a reliable enough signal to justify a code change. Left as-is rather than churn on an unclear result.

**The honest overall result -- re-ran the full whole-document checkpoint on the real, now-fixed
aikyamjobs.org report:**

| Criterion | Chapter 27 (before) | Chapter 29 (after) |
|---|---|---|
| `audience_fit` | 40% clear | 45% clear |
| `usefulness` | 3.28/4 | 3.24/4 (noise, not a regression) |
| `emotional_resonance` | 3.05/4 | 3.02/4 (noise, not a regression) |
| `honesty_calibration` | 65% overstates | 61% overstates |
| `natural_voice` | 95% boilerplate | 84% boilerplate |

**Not a clean pass.** `natural_voice` moved a real 11 points, `honesty_calibration` a more modest 4 --
genuine improvement, especially given the isolated headline fix alone was a 35+ point swing, showing the
whole-document score really is diluted across many contributing sentences, each individually small. The
two real fixes shipped here (the headline contradiction bug, the suppression-story overclaim) were the
highest-confidence, most clearly-isolatable issues found. What remains is the same distributed,
whole-document-level pattern Chapter 27 already named -- fixing it further means going sentence by
sentence through the rest of the report (the reassurance-tail "X, so/rather-than Y" construction repeats
in `_health_trend`, `_still_open_items`'s generic wrapper, and elsewhere), which is genuinely the scope of
the already-paused em-dash/AI-voice sweep (Chapters 17-19), not a single follow-up chapter. Reported
honestly rather than claimed as fully resolved.

Verified against the full real portfolio (zero Jinja errors, 33/33 domains). Service restarted, healthy.
No email sent.

---

*(Next entry: whatever the user prioritizes next -- continuing the sentence-by-sentence prose sweep this
chapter's numbers point toward, or something new.)*

## Chapter 30 — The email report becomes a PDF (Typst), Round A: pipeline infrastructure

New initiative, directly resolving the accepted-but-unresolved gap from [[dmarctool_email_svg_gmail_gap]]:
real Gmail testing proved inline SVG charts actively leak garbled text rather than degrading gracefully.
The user's own plan all along was to wait for a Typst-based PDF export; that work starts now. The PDF
becomes the real rich report (unconstrained by any email-client limit); the email body becomes a short
teaser pointing to the attachment (Round E).

**Full plan-mode cycle first**, given the scale (new toolchain, new attachment mechanism, a full second
design pass): one Explore-agent technical spike (not codebase research -- a hands-on validation of real
Typst 0.15.1 syntax, actually compiling test `.typ` files rather than trusting remembered syntax) plus
direct verification of Mailgun's real attachment API. Plan saved at
`/Users/jinsoraj/.claude/plans/splendid-tumbling-summit.md` (overwritten from the completed visual-
redesign plan -- a different task).

**Real findings from the spike, not guessed**: Typst's native primitives (no cetz, no external package,
no network dependency at compile time -- matching `app/charts.py`'s own "no charting library" precedent)
fully support horizontal progress-bars and connected-point line charts (grid/axis/markers via
`place()`/`line()`/`circle()`), but **cannot do true arc/donut charts** without an external package.
Adopted fallback: horizontal segment gauges for any percentage that was a donut in the email/dashboard
version -- a deliberate substitution, arguably more minimalist, and sidesteps the exact "text crammed
inside a small ring" bug class fixed in Chapter 28. PNG export (`typst compile file.typ file.png`) gives
a real way to visually inspect generated output -- something the email work never had (could only read
raw HTML/text).

**Shipped**: new `app/pdf_report.py` (`_typst_str()` escaping helper -- guards every dynamic string
against Typst's markup-mode special characters; `_compile_typst()`, a `subprocess`/`tempfile` wrapper
around the real `typst` CLI, stdlib only; a design-system `_PREAMBLE` -- New York serif for headings/
wordmark, Helvetica Neue sans for body/data, a white/near-white print page carrying the established brand
hue forward, `stat-tile()`/`segment-gauge()`/`eyebrow()`/`section-title()` helper functions; `_masthead_
and_kpi()` + `render_domain_report_pdf()`, reusing `_build_context()` directly -- zero new content
decisions, matching the user's explicit instruction not to re-run the same logic). `app/mailgun.py::
send_message()` gained an optional `attachment` param (hand-rolled multipart/form-data, stdlib `urllib`,
Mailgun's real `attachment` field name confirmed against their docs) -- default `None` keeps the one real
call site (`send_report_now()`) byte-identical to before.

**Two real bugs caught via actual visual inspection, not just "did it compile"**: (1) the footer showed
literal quote marks around the domain name (`"aikyamsolve.org"`) -- a real Typst gotcha: a bare string
literal placed directly in markup content (`[...]`) is NOT parsed as code, the quote characters just
render as text; fixed by prefixing with `#` to force one code-mode expression. (2) the page counter
showed `1 / (1,)` -- `#counter(page).final()` returns an array/tuple, needs `.first()` to get the actual
number; the same untested pattern was in the spike's own reference doc, caught here by actually looking
at the rendered PNG rather than trusting "no compile error" as sufficient. Also fixed the same
raw-string-in-markup risk in the greeting/domain-name lines (was using a quote-slicing hack instead of
the correct `#{...}` pattern) before it could bite on a name containing a markup-special character.

**Verified against the full real portfolio**: all 33 real domains compile with zero Typst errors.
Visually confirmed via PNG on aikyamjobs.org (headline correctly omitted -- the still-open item isn't
urgent, inheriting Chapter 29's contradiction fix automatically since this reuses `_build_context()`
directly, no re-implementation) and aikyamsolve.org (headline shown correctly, real "Hi Jinso," real
92/86 health scores, real smart-quote typography). Service restarted, healthy. No email sent.

---

*(Next entry: Round B -- the standing narrative and action ledger zones, ported into the PDF template.)*

## Chapter 31 — PDF report, Round B: standing narrative + action ledger

Ports `care_ledger`/`health_trend`/`health_timeline`/`whats_working`/`resolved`/`still_open`/
`contact_cta`/`risk_warning` into the Typst template. Zero new content or wording decisions -- every
sentence rendered here is the exact same Jev-validated prose from Chapters 1-22, just given real print
typography and room to breathe.

**Shipped**: `_bullet_list()` (native Typst `#list()` with an accent-colored bullet marker),
`_resolved_item()`/`_still_open_item()` (rebuild each item's rich inline formatting -- bold "we've taken
care of it," muted why/impact/history clauses -- matching the email template's own structure exactly),
`_standing_narrative()` and `_action_ledger()`.

**A real design decision carried forward deliberately, not re-derived**: the "nothing needed fixing"
fallback box is gated on `not context.get("headline")`, exactly mirroring the Chapter 29 fix in
`email_report.html`/`.txt` -- a true all-clear domain would otherwise show the same "nothing to worry
about" message twice (once as the masthead's green headline callout, once again here). Confirmed this
matters for real: since `_masthead_and_kpi()` and `_action_ledger()` both read the identical `headline`
key from the same `_build_context()` call, this gating is automatically correct with no extra plumbing.

**Verified against the full real portfolio**: zero Typst errors on all 33 domains. Visually confirmed on
aikyamfellows.org (4 real still-open items across 2 full pages, correct pagination, correct muted-clause
styling) and aikyam.school (2 real resolved items, the whats_working green box, and the single
correctly-non-duplicated "no action needed" callout). Service restarted, healthy. No email sent.

---

*(Next entry: Round C -- protection/deliverability + the 3 real Typst-native trend charts.)*

## Chapter 32 — PDF report, Round C: protection/deliverability + the 3 real trend charts

The core visual payoff of the whole PDF initiative: real vector line charts, something the email report
could never safely show (Chapter 28/[[dmarctool_email_svg_gmail_gap]]).

**Real architectural refactor first**: `app/domain_report.py::_email_charts()` used to fetch data AND
render SVG in one step. Extracted the data-fetching half into a new public `chart_data()` function
(disposition totals, the 3 real daily series) that both `_email_charts()` (SVG, unchanged behavior --
re-verified against the full email-path portfolio sweep, zero regressions) and the new PDF path call --
one database query each, never two independent ones that could silently drift apart. This is the
"re-derive, don't duplicate" discipline applied at the architecture level, planned explicitly in Round A's
own spec rather than discovered as an afterthought.

**A real Typst line-chart function, hand-validated before use** (not assumed from the Round A spike's
6-point toy example): built and test-compiled directly via the `typst` CLI before writing any Python
around it -- grid lines, an optional dashed threshold line (folded into the y-scale exactly like
`charts.py::metric_trend_chart`'s own reference lines), an end-point accent dot, and first/last date
labels. No cetz, no external package.

**A real bug found immediately on first real-data test**: `_short_date()` crashed on
`daily_pass_series()`'s real output -- `analysis.daily_pass_series()` returns real `date` objects for its
date field (matching how `charts.py::pass_rate_sparkline` already calls `.strftime()` directly on it),
while `postmaster_daily_series()`/`mailgun_daily_series()` return ISO strings for theirs. Same
inconsistency `charts.py` already quietly works around per-function; fixed by handling both shapes in one
helper rather than assuming a single format.

**Shipped**: `_protection_and_deliverability()` -- deliverability + a segment gauge for the current-period
delivered-safely percentage, protection prose, spam_trend prose + a real Google spam-rate line chart,
list_hygiene prose, a real bounce-rate line chart (thresholded against the real
`mailgun_bounce_rate_warn` setting), a real pass-rate history line chart, and the
impersonation/blocklist good-news callouts closing the zone -- same section order as the email version.

**Verified against the full real portfolio**: zero Typst errors on all 33 domains, email path re-verified
unaffected by the `chart_data()` refactor. Visually confirmed on aikyamjobs.org: a real 100% delivered-
safely gauge, a real spam-rate chart showing an honest late-period spike above the dashed threshold (not
smoothed over), a real bounce-rate chart with a real above-threshold spike, and a near-flat 100%
delivery-rate history line -- all three charts rendering cleanly on real, sometimes-messy data, not just
a clean toy example. Service restarted, healthy. No email sent.

---

*(Next entry: Round D -- newsletter zone, tips, closing, the new /report_pdf preview route, and full
single-PDF assembly.)*

## Chapter 33 — PDF report, Round D: newsletter, tips, closing, and full single-document assembly

Completes the content -- every section from `build_domain_report()` now has a home in the PDF. Ships
`_newsletter_and_closing()` (newsletter prose + real open/click segment gauges re-derived from the exact
same `newsletter_bars` data Chapter 26's email table-bars use -- same number, different render; tips as a
green bulleted box matching whats_working's treatment; the closing paragraph/signoff, exact established
wording, zero new content) and the new `GET /domain/{name}/report_pdf` route (`app/web.py`, same
read-only/no-side-effects contract as `/report_preview` -- this is how the PDF gets verified, by me and by
the user, without ever triggering a real or test send).

**A real, load-bearing bug found only by testing the actual live route, not my own shell scripts**: every
prior round's verification ran `render_domain_report_pdf()` directly via `./venv/bin/python3`, where the
interactive shell's own `PATH` includes Homebrew's prefix. The new `/report_pdf` route, run through the
real launchd-managed service, hit `FileNotFoundError: typst` -- launchd's PATH is minimal (no Homebrew),
and `_compile_typst()` was calling bare `"typst"`. `dig` (`app/compliance.py`) gets away with this because
it lives at `/usr/bin/dig`, a default system path always on launchd's PATH; Typst is Homebrew-installed
at `/opt/homebrew/bin/typst`, which isn't. Fixed by hardcoding the absolute path. **A real lesson for the
rest of this session and beyond**: a script run from my own shell is not the same execution environment
as the real service -- the very last verification step (hitting the actual live route) is not optional,
even after 3 rounds of "zero errors" from direct Python calls.

**Verified against the full real portfolio**: zero errors, all 33 domains, via both direct calls and the
real live route. Visually confirmed the complete 3-page assembly on aikyamjobs.org (real newsletter
segment gauges matching the prose numbers exactly -- 30% opened, 4% clicked -- the real standout-campaign
note, correct closing/signoff) and the graceful-degradation case on solidaritycir.com (the real emptiest
domain in the portfolio -- no KPI tiles, no charts, no newsletter, a single clean page, no crash, no ugly
gaps). Service restarted, healthy. No email sent.

---

*(Next entry: Round E -- the Jev-validated teaser email, wired into send_report_now(), closing out the
5-round PDF initiative.)*

## Chapter 34 — PDF report, Round E: the teaser email, and the initiative closes out

The user's central requirement for this whole initiative: a short email whose only job is making the
reader eager to open the attached PDF, without duplicating its content -- "they should 100% feel eager to
read pdf attached." Per the standing [[dmarctool_jev_every_decision]] instruction, iterated through Jev
rather than shipping a first draft.

**13 candidates tested, a real pattern found and then fixed**: early drafts (generic reassurance +
bulleted "Inside:" list, matching common marketing-email structure) scored `natural_voice` as
`reads_like_generated_boilerplate` every single time, `emotional_resonance` as low as 0.7/4. Anchoring
the opening sentence on real, concrete numbers (this period's real resolved/still-open counts, the real
health score -- re-derived, never invented) plus one sentence naming *why* it matters (protecting the
reader's relationship with their own donors/supporters, not a technical checkbox) was what first crossed
to `reads_like_a_person`. **A real gap found mid-iteration**: that winning version never actually said
"PDF" or "attached" anywhere -- "explained in full inside" is genuinely ambiguous about whether "inside"
means the email body or an attachment, which directly violates the user's own explicit requirement.
Adding an explicit PDF mention initially cost the `natural_voice` gain back (dropped to boilerplate again)
until reframed as personal ("we made you a proper PDF instead of just an email") rather than transactional
("the report is attached") -- recovered `reads_like_a_person` while keeping the explicit, unambiguous
mention. Final scores on the real generated text: `usefulness` 3.06/4, `emotional_resonance` 2.14/4,
`audience_fit` clear, `natural_voice` reads_like_a_person -- the best of every candidate tested.

**Shipped**: new `_teaser_hook()` (`app/domain_report.py`) building the opening sentence from real,
re-derived numbers (health score, this period's resolved/still-open counts) with a genuine fallback for
domains with none of that data. `email_report.html`/`.txt` rewritten entirely as the short teaser (hook +
an explicit visually-highlighted "Your full report is attached (PDF)" callout, separately Jev-tested +
the mission-framing paragraph + signoff) -- the old rich 21-section content these files carried since
Chapter 1 now lives exclusively in the PDF. `send_report_now()` wired to generate the real PDF and attach
it via `app/mailgun.py`'s Round A attachment support.

**Verified against the full real portfolio**: zero errors on all 33 domains for the teaser
`.html`/`.txt` render AND the PDF generation together. Live-confirmed via the real service on
aikyamsolve.org: both `/report_preview` (the real generated teaser, matching the tested wording exactly)
and `/report_pdf` return 200. **`send_report_now()` itself was never called** -- per the standing rule,
verification stops at generating what a send would produce, never triggering one. Service restarted,
healthy. No email sent.

## All 5 rounds of the PDF initiative -- summary

| Round | What | Outcome |
|---|---|---|
| A | Typst pipeline, Mailgun attachment support, design system | Shipped (Ch.30) |
| B | Standing narrative + action ledger, zero new content | Shipped (Ch.31) |
| C | Protection/deliverability + 3 real trend charts (the core visual payoff) | Shipped (Ch.32) |
| D | Newsletter + tips + closing + `/report_pdf` route + full assembly | Shipped (Ch.33) |
| E | The teaser email, 13 Jev-tested candidates | Shipped (Ch.34) |

All 5 rounds shipped real, working content, verified against the full 33-domain real portfolio at every
step, with two genuinely load-bearing bugs caught only by actually looking at rendered output or hitting
the real live route rather than trusting "did it compile"/"did it import": the Typst markup-vs-code-mode
string-escaping bug (Ch.30) and the launchd-PATH-has-no-Homebrew bug (Ch.33). The email report and its
PDF attachment are now a genuinely different medium from what this whole session started with --
real charts, real fonts, no email-client rendering ceiling -- while never re-litigating a single
Chapters-1-22 content or voice decision along the way.

---

## Chapter 35 — Fixing the teaser's "this time" repetition/honesty bug, and a real answer to use case #35

User, reviewing pending work after the PDF initiative shipped, asked me to audit "anything Jev can be
used for" across the project. Found this myself while re-reading the just-shipped teaser: "**This time**
we made you a proper PDF instead of just an email" is a static string that would repeat verbatim every
month forever -- "this time" only honestly describes the very first PDF a domain ever gets. Exactly what
`jev/USE_CASES.md` use case #40 warns about: "before adding ANY new recurring status line, does a
projected 6-cycle simulation of its wording pass repetition_risk before it ships" -- a check that didn't
happen when Chapter 34 shipped.

**Fixed with a real distinguishing signal, not a guess**: new `domain_report_settings.pdf_intro_shown`
column (`app/db.py`), set only after a real successful send (`mark_sent=True`, mirroring `last_sent_at`'s
own gating exactly). Deliberately NOT reusing `last_sent_at IS NULL` as the signal -- many real domains
already have a non-null `last_sent_at` from their old plain-email sends, before the PDF existed, so that
alone can't distinguish "first report ever" from "first PDF ever." New `_pdf_intro_line(pdf_intro_shown)`
returns the original tested "this time" wording only when `pdf_intro_shown` is falsy, and a real
non-novelty-claiming alternative otherwise.

**A genuinely important negative finding, not forced past**: tried to make the repeat-case wording
resistant to becoming stale over many months, first with a single well-worded alternative, then --
suspecting the issue was really about repetition, not wording -- with a version EXPLICITLY told to Jev
that it was one of several deterministic monthly-rotating variants (the exact technique
`_ALL_CLEAR_PHRASES` already uses elsewhere in this file). **Both still scored `repetition_risk` as
reads_as_boilerplate and `natural_voice` as reads_like_generated_boilerplate.** This is real evidence
answering `jev/USE_CASES.md` use case #35, open since it was written: "Does the `_ALL_CLEAR_PHRASES`
rotation actually reduce repetition_risk in practice, or do the 3 variants still read as interchangeable
to Jev?" -- for a short, structurally-fixed template like this teaser, rotation alone does not fix it.
Shipped the single honest fix (no false novelty claim) rather than force further rotation the evidence
says doesn't work, matching this project's own "don't force a fix that doesn't work" precedent (Round 2,
Chapters 20-22).

**A second, smaller negative finding, confirming an older lesson rather than contradicting it**: tried
swapping the shipped wording's "--" for a colon, expecting no real effect (pure punctuation, same
words) -- and it REGRESSED `natural_voice` from reads_like_a_person to reads_like_generated_boilerplate
on the first-time line specifically. Same lesson Chapter 17 already found once: a mechanical
dash-removal isn't the fix by itself, only a genuine sentence-restructure is. Shipped the empirically-best
wording (with its "--") rather than a worse version just to avoid a dash; flagged both new lines in code
comments for the paused em-dash sweep to properly rewrite later, not swap.

**Verified against the full real portfolio**: zero errors on all 33 domains for both template renders.
Confirmed live via `/report_preview` on aikyamsolve.org (correctly shows the "first time" framing, since
no real domain has actually received a PDF-attached send yet -- `pdf_intro_shown` is genuinely 0
everywhere right now, an honest, accurate state, not a bug). Directly verified the repeat-case function
branch returns the correct alternate wording. Service restarted, healthy. No email sent.

---

## Chapter 36 — Resuming the whole-document natural_voice/honesty_calibration finding

Chapter 29 left a real, un-fixed finding on the table: the full `still_open` block for a domain with
several action items simultaneously open scores `natural_voice: reads_like_generated_boilerplate` and
`repetition_risk: reads_as_boilerplate`, even though every individual item's wording had already been
Jev-validated in isolation over many earlier chapters. Resumed it as the first of the "already known
ones" the user asked for, in the order they specified.

**Real baseline, established first**: pulled the live `still_open` list for `tinybridge.in` -- a real
domain with 4 categories open at once (`borrowed_sending_identity`, `lookalike_domain`, `new_sender`,
`postmaster_compliance`), the richest real overlap case in the portfolio. Ran it through Jev exactly as
it renders today: `natural_voice: reads_like_generated_boilerplate` (98% confidence),
`repetition_risk: reads_as_boilerplate`.

**Root cause, narrowed to two real code locations**: `_PROBLEM_STORY`'s `new_sender`/
`failure_investigation`/`borrowed_sending_identity` entries all shared the identical tail phrase "It's
worth knowing about, since [X] can be a sign that someone else is using your organization's name..."; and
`_WHY_IT_MATTERS`'s `postmaster_compliance`/`lookalike_domain`/`new_sender`/`failure_investigation`/
`borrowed_sending_identity` entries all shared the same "[fact], so [consequence]" grammatical shape.
Four different categories, same two templates -- exactly what reads as generated once several show up in
the same document.

**Fix 1 -- broke both shared templates with genuinely restructured sentences** (not rotation, not a
punctuation swap -- both already-proven non-fixes from Chapters 17/35): each of the affected entries now
uses a different real grammatical shape (contrast clauses with "but"/"though", causal "Because..."
openers, gerund-subject constructions, consequence-first framing) rather than a shared connector word.
Caught and fixed a fresh bug of my own making mid-edit: the first `lookalike_domain` rewrite duplicated
"watching what it does" against its own story line (a new intra-item echo, not the cross-item one being
fixed) -- rewritten again before testing.

**Fix 2 -- a second, previously-undiagnosed repetition source, found while re-rendering the real
combined block**: `_incident_recurrence()`'s single-day/30+-days-old branch returns one fixed sentence
("We've known about this since {month}, and it hasn't slipped off our list.") with no per-item
variation. When several items share the same first-known month (as all 4 of tinybridge.in's do -- all
opened in August), the exact same sentence appeared 4 times in a row in one document, undiluted by
Fix 1. Fixed with the same "never render the same sentence twice" discipline `_still_open_items()`
already applies to `story` (via `seen_stories`) -- added a matching `seen_histories` set; a repeat
history sentence is now suppressed (set to `None`) rather than duplicated.

**Re-tested the real combined block with both fixes applied**: `repetition_risk` moved from
`reads_as_boilerplate` to `borderline_needs_variation` -- a genuine, measured improvement.
`natural_voice` did NOT move (still `reads_like_generated_boilerplate`, 95% confidence) -- word-level
and duplicate-sentence fixes alone don't reach it.

**The real finding that explains why, confirmed with an isolated A/B test (not assumed)**: took one real
item (`borrowed_sending_identity`) and tested two versions with the SAME underlying facts. Version A --
today's actual mechanical assembly (capitalized story sentence, then a separate detail sentence, then a
separate capitalized why sentence, then history) -- scored `natural_voice` 56% boilerplate even in
isolation (worse once compounded across 4 items in one document, per the baseline above). Version B --
the same facts hand-fused into ONE flowing sentence with reordered, concrete-detail-first structure and
varied subordination ("It's usually just a shared-vendor mix-up rather than anything malicious, but
until it's sorted...") -- scored 80% `reads_like_a_person`. **Conclusion: the dominant driver of the
whole-document boilerplate read is the fixed story+detail+why+history sentence-concatenation STRUCTURE
itself, not primarily the individual phrases inside each slot.** This generalizes Chapters 17/35's
"mechanical swaps don't work, genuine restructuring does" finding one level up: it applies to how items
are ASSEMBLED, not just how each sentence is WORDED.

**Scoped, not force-fixed further this round**: reaching `reads_like_a_person` on the full document
would mean redesigning `_still_open_item()`/`_resolved_item()` (`app/pdf_report.py`) to fuse fields into
one flowing sentence per item, plus rewriting every category's `_WHY_IT_MATTERS` entry to be a fusable
subordinate clause rather than a standalone capitalized sentence (~20 categories) -- real content work at
the scale of the earlier 5-round PDF effort, not a same-sitting patch. Documented as the concrete next
target rather than forced through partially; matches this project's standing discipline of shipping a
real, measured improvement and naming the next real gap honestly rather than overclaiming a full fix.

**Verified against the full real portfolio**: `_build_context()` succeeds with zero errors across all 33
real domains. Re-compiled the real PDF for `tinybridge.in` (the test case), `aikyamjobs.org` (richest),
and `aikyamsolve.org` (the standing test domain) -- all 3 compile clean. Service restarted, `/` and
`/domain/tinybridge.in/report_pdf` both return 200. No email sent.

---

*(Next entry: the em-dash/AI-voice sweep, then the general USE_CASES.md D/H sweep, then the PDF-era
content expansion. The item-assembly-structure fusion redesign found this chapter is a new, real
candidate for its own future round.)*

---

## Chapter 37 — Line charts had no y-axis, so a real 100% pass rate looked like a broken chart

User caught this from a real generated PDF screenshot: "Your delivery rate over time" showed a flat line
with dates on the x-axis but no numbers anywhere -- no way to tell if it meant 100% (great) or a broken/
empty chart. Asked directly: "is this normal? does ppl get idea after seeing it?"

**Confirmed real, on both counts.** Checked `analysis.daily_pass_series()` across the real portfolio:
several domains (aikyamfellows.org, aikyamsolve.org, others) genuinely hold a flat 100% DMARC pass rate
for their whole window -- the flat line was real, honest data, not a bug. But `line-chart()`
(`app/pdf_report.py`) truly had zero y-axis value labels -- only x-axis start/end dates -- confirmed by
reading the function, not assumed. A reader has no way to distinguish "100%, every day, genuinely
perfect" from "this chart isn't rendering anything." Answer to "do people get an idea": no, not as
shipped.

**Fixed**: `line-chart()` now takes `scale`/`unit` (default 100/"%", matching all 3 real callers --
spam/bounce/pass-rate, all stored as 0..1 fractions) and reserves a left axis gutter with a value label
at each of the 4 gridlines, plus a bold endpoint label next to the current-value dot so the single most
important number never depends on a reader eyeballing an unlabeled line against a gridline.

**A real bug caught while visually verifying, not shipped blind**: the first version's number formatter
rounded to 1 decimal place, which collapsed the spam-rate chart's sub-1% gridlines (0.04%/0.08%/0.12%)
into duplicate "0.1%, 0.1%, 0%" labels -- the exact confusing thing this axis exists to fix, just moved
one level down. Fixed with 2-decimal precision specifically for sub-1% values (where Google's own 0.1%
threshold lives), 1-decimal/whole-number for everything else.

**Verified visually, not just compiled**: re-rendered `aikyamfellows.org`'s real PDF (the flat-100%
case) and PNG-exported it before and after -- confirmed the exact reported problem (unlabeled flat line)
and the fix (now reads "100%" clearly, endpoint bold-labeled). Also confirmed the bounce-rate chart's
real endpoint value (14.3%) now reads directly off the chart instead of requiring an eyeball estimate
against the gridlines.

**Full real portfolio**: all 33 domains recompile with zero errors. Service restarted, `/report_pdf`
returns 200. No email sent.

---

## Chapter 38 — Em-dash/AI-voice sweep, resumed and finished

Resumed exactly where Chapter 19 left off: confirm which of the file's remaining "--" occurrences are
real user-facing text vs. docstrings/comments, then fix the real ones. Used a proper AST-based scan
(not just grep) to separate docstring lines and pure code comments from actual string-literal content,
since a plain grep can't tell the difference reliably.

**Result: 180 total "--" occurrences in `app/domain_report.py`, only 23 were real user-facing strings.**
The other ~157 are docstrings and inline code comments -- confirms Chapter 19's suspicion was right.
Went through all 23:

**New real fixes (5), each Jev-tested old vs. new before shipping:**
- The 6-way shared "small technical change behind the scenes..." tip (`spf_missing`/`dns_missing`/
  `dkim_missing`/`spf_lookup_limit`/`dkim_weak_key`/`mta_sts_broken`) was one identical sentence
  copy-pasted across 6 categories -- a real duplication risk (Chapter 36's exact bug class, just found
  in the tips library instead of story/why-it-matters) and 64% boilerplate on its own. Rewrote all 6
  with genuinely different structure; all 6 improved (50-77% reads_like_a_person).
- `dns_policy_weakened`'s story: 64%->75% (currently 0 domains open, but real and now fixed regardless).
- `safe_browsing_flagged`'s detail: 88%->78% boilerplate, and a bonus win -- `honesty_calibration` moved
  44%->66% accurately_calibrated too (that criterion didn't exist when this was last tuned, so this is
  the first time it's been checked against it).
- `content_spam_risk`/`subject_spam_risk`'s detail: 85%->70% boilerplate -- a real, partial improvement;
  two further restructures scored worse or flat, left as the best found rather than forced further.

**Confirmed fine, left unchanged (7)**: `lookalike_domain`'s story (74%, re-confirms Chapter 18's finding
that 2 earlier rewrite attempts didn't help), `blocklist`'s why (62%), `campaign_compliance_issue`'s tip
(69%), the impersonation risk-warning's both branches (69%/84%), the volume-comparison sentence (57%,
already deliberately tuned per its own code comment), the health-trend delta tail (74%), and the
`_ALL_CLEAR_PHRASES` rotation (already checked Chapter 19, zero movement). Not every remaining "--" was
hurting its sentence -- same lesson as Chapter 19, re-confirmed rather than assumed.

**Already fixed by earlier chapters, re-confirmed present and correct**: the 3 `_WHY_IT_MATTERS` entries
and 1 `_PROBLEM_STORY` entry from Chapter 36, and `_pdf_intro_line()`'s deliberately-kept em-dash version
from Chapter 35.

**The sweep is now genuinely complete**, not just paused again: every real user-facing "--" occurrence
in the file has either been fixed or explicitly Jev-checked and confirmed fine. Future new content
should still be written without em-dash by default (matching Chapters 17-19's original standard), but
there is no longer a backlog of untested existing text.

Verified against all 33 real domains (zero errors), PDF re-compiled clean for 3 real domains (including
`pattic.org`, which exercises the newly-touched bounce/DNS categories), service restarted and healthy.
No email sent.

---

## Chapter 39 — PDF-era content expansion, 3 real additions

Surveyed what real, already-computed data has no reader-facing surface yet, given the PDF's print space
freedom versus even the "no space limit" email rebuild ([[dmarctool_comprehensive_report_rebuild]]).
Ruled out a health-score history chart -- checked live, zero real domains have 3+ months of score
history yet (the scoring formula was only reworked the week before), so it would ship empty everywhere.
Presented 3 real, data-backed candidates to the user; all 3 approved.

**1. Per-newsletter table.** `_campaign_table()` (`app/domain_report.py`) + `_campaign_table_typst()`
(`app/pdf_report.py`): subject/sent-date/delivered/opened%/clicked% for each real newsletter sent this
period, below the existing aggregate open/click bars. Real data: 25 real campaigns across 2 domains
(aikyamjobs.org, pattic.org). Uses `unique_open_rate`/`unique_click_rate` (real people), never the raw
event-count rates `recent_campaigns()` also returns -- those are documented as overstating engagement,
sometimes past 100%. Verified visually: aikyamjobs.org's 4-campaign table and pattic.org's 1-campaign
table both render correctly, real numbers match the underlying query.

**2. Bounce-reason breakdown.** `_bounce_reason_breakdown()` + a new `category-bars()` Typst primitive
(ranked horizontal bars, not a 0..1 fraction like `segment-gauge` -- there's no natural "100%" for a
ranked category list). Reuses `bounce_reasons.categorize_bounce()`, already built and tuned for the
chronic-bounce detector, with no reader-facing surface until now. Gated on a 5-bounce minimum (same
"don't manufacture signal from noise" floor as every other volume-gated number in this report). Real
data: 762 SES + 706 Mailgun categorized suppressions; pattic.org alone has 117 in-period, breaking down
into 5 real categories (mailbox-full 67, provider-fault 26, unknown 7, no-such-user 5, no-answer 5).
Verified visually on pattic.org's real breakdown.

**A real, confirmed Typst bug caught before it could ship silently**: `((label, value))` in Typst is NOT
a 1-element array of one pair -- parens are pure grouping, so it flattens to the 2-element array
`(label, value)` itself, and `.at(0)` returns `label` (a string), not a pair. Confirmed with a real
minimal compile test (`.len()` returned 2, not 1). This was a LATENT bug in `_typst_series()` since
Round A (Chapter 30) -- dormant only because every existing caller happens to gate on `len >= 2` before
calling it -- but immediately live for `category-bars()`, which has no such guard (a domain could
plausibly have all its bounces fall into one category). Fixed `_typst_series()` with a trailing comma
for the single-element case (Typst's own single-element-array syntax, same as Python's `(x,)`), and
reused it for `category-bars()`'s input instead of hand-rolling a second array-literal builder.

**3. Compact still-open reference table.** `_still_open_short_label()` + `_first_seen_date()`
(`app/domain_report.py`) + `_still_open_table_typst()` (`app/pdf_report.py`): a scannable issue/since
table above the existing prose bullets, gated on 2+ open items. New short-label dict deliberately does
NOT reuse `app.labels.category_label()` -- that's written for the dashboard's more technical operator
audience (raw terms like "PTR", "SPF", "MTA-STS"), while this report's whole voice is jargon-free by its
own module docstring, so the table needed its own plain-language phrasing at the same register as
`_PROBLEM_STORY`. Verified on `tinybridge.in`'s real 4-item case -- table and prose bullets both show
consistent real dates, no duplication introduced.

**Verified against the full real portfolio, both paths**: `render_domain_report_pdf()` for all 33 real
domains (zero errors) and `preview_domain_report()` (the email path, confirming the 3 new item-dict keys
don't disturb Jinja rendering) for all 33 (zero errors). Service restarted, `/report_pdf` returns 200 on
3 real domains. No email sent.

---

## Chapter 40 — New feature: on-demand "notify client to clean their list" email

**The real workflow this replaces**: the user's own words -- "when the pass rate percentage goes down i
usually check the card and see the increased bounces and download the new bounces, especially very
correct ones like hard bounces, chronologically appearing bounces which are safe to delete, non existing
email ids etc and send to them to remove from their email lists... i dont want to intervine in their
lists... i just realised this can be sent from the tool itself." A real, previously-manual workflow
(check → download CSV → paste into an email → send → remember to mark done later) automated into one
button, without DMARCTool ever touching the client's own list directly.

**New module `app/bounce_notify.py`**: reuses `app.bounce_reasons.categorize_bounce`/
`PERMANENT_CATEGORIES` (the "very correct, hard bounce" bucket) and `app.chronic_bounces.
chronic_transient_bounces()` (already-built, already-tuned detectors, no new categorization logic
invented). Two real categories, each with its own certainty level:
- **Hard bounces** (`_hard_bounce_rows`): scoped to what's NEW since the last successful notification --
  its own watermark (`bounce_notification_sends.sent_at`), deliberately independent from the dashboard's
  own mark-done watermark (`app.mailgun._suppression_watermark`). The two must not share one cutoff:
  marking the dashboard reminder done doesn't mean the client has actually removed anything yet.
- **Chronic transient bounces**: always the CURRENT full list, same "no new/full split" reasoning as
  `chronic_transient_bounces()`'s own existing CSV export -- a standing state, not a discrete event.

**New table `bounce_notification_sends`** (append-only, same audit-log shape as the existing
`report_sends` table for the periodic report) -- `sent_at` doubles as both the audit trail and the
watermark for "new since we last asked."

**Mailgun multi-attachment support**: `app.mailgun.send_message()`'s `attachment` param now accepts
either a single tuple (existing PDF-report call site, unchanged) or a list of tuples (this feature's 2
separate CSVs) -- `_encode_multipart()` loops once per file. Mailgun's own API repeats the `attachment`
form field per file, confirmed against their docs.

**Real bug caught while reading actual generated output, not assumed correct**: the chronic-bounce CSV
initially included raw stored email values, which are sometimes a full `"Display Name <addr>"` string
straight from the original send's To: header (same shape `app.web.download_new_suppressions` already
handles for its own chronic rows via `_clean_email()`) -- missed on the first pass here, caught by
actually decoding and reading the generated CSV bytes before wiring up the route.

**Wording, multiple real Jev rounds against real pattic.org numbers (327 hard, 36 chronic)**, criteria
`audience_fit`/`actionability`/`natural_voice`/`honesty_calibration`:
- First draft's "these are safe to remove" as a bare opening claim scored 92% `overstates_beyond_the_
  evidence` in isolation -- the assertion appeared before its own justification. Restructured so each
  file's safety claim is stated alongside its specific reason, not as a blanket opener.
- "no longer real" tested meaningfully clearer than "no longer exist"/"permanently stopped accepting
  mail" (`audience_fit` `clear_as_is` 0.35→0.73 in isolation).
- **A real, user-raised concern mid-build changed the design**: after the first working draft, the user
  pointed out the recipients are non-technical and may feel like they're "deleting their own members" --
  the two files should NOT read as equally urgent. Restructured so the hard-bounce file states its safety
  plainly (no hedging: "confirmed gone or permanently broken... safe to delete right away") while the
  chronic file is explicitly framed as less certain and optional to double-check ("very likely dead too,
  but since we cannot be fully sure, feel free to check them yourself before removing anyone"). This
  version scored best-balanced across all 4 criteria: `actionability` 100%, `natural_voice` 83%,
  `honesty_calibration` 69%, `audience_fit` 43%.
- **`audience_fit` stayed the weakest criterion throughout every round** (peaked ~43% `clear_as_is`
  combined, despite several genuine restructures, not mechanical swaps) -- shipped as the best found and
  documented honestly in code comments, not force-fixed further or claimed clean. Matches this project's
  own established discipline (Chapters 18-19, 35, 38) of reporting a real ceiling rather than overclaiming.

**New route** `POST /domain/{name}/notify_bounce_cleanup` (`app/web.py`) -- same recipient/cc as the
domain's existing report settings, reply-to from the same global `report_reply_to` setting
(`jinso@aikyamfellows.org`) already used by the periodic report, per the user's explicit instruction to
reuse both. Same secrets-check/flash-message pattern as the existing `test_domain_report` route.

**New UI**: a "📧 Notify client about list cleanup" section on the Deliverability & Spam tab, right next
to the existing "Download suppressions" section (where this exact manual workflow already lived) --
button shows live pending counts ("327 confirmed + 36 likely"), and once notified, shows "Last notified:
{date} ({counts}, to {recipient})" or the error if the send failed, so a future visit can't accidentally
re-notify without seeing that context first.

**Verified without ever sending a real email** (standing rule unchanged): unit-tested `build_notification()`
directly against real pattic.org/aikyamjobs.org data (single-file and both-file cases), verified the
watermark behavior end-to-end by inserting and then removing a real test row in `bounce_notification_sends`
and confirming the dashboard's pending-count and "last notified" display both update correctly. Full
33-domain page-render sweep against the real live service (zero errors). Service restarted, healthy.

**Also this chapter**: the user retired the hosted-artifact-sync step of this whole workflow (see
`jev/WORKFLOW.md`'s "Relationship to the hosted artifact" section) -- `DECISIONS_LOG.md` is now the sole
record, no more parallel artifact updates.

---

## Chapter 41 — Ch.40 consistency fixes + a "why it matters" reasoning gap

User caught 2 real consistency gaps right after Ch.40 shipped: the notify email had no "With care, {signoff}"
closing (every other outbound email from this tool has one) and used the bare sender email as the From
address instead of the configured display name ("Domain Health", `settings["report_sender_name"]") --
both simply missed on the first pass, not a design decision. Fixed: `build_notification()` now takes
`signoff_name` (the same `report_signoff_name` the periodic report uses) and closes with it; the route now
builds `from_header` the same way `send_report_now()` does.

**A second, real content gap, also user-raised**: the opening paragraph said removing dead addresses
"helps keep your emails out of spam" but never explained the actual mechanism (repeated sends to dead
addresses erode sender reputation, which is what triggers spam-folder treatment) -- the "why" a
non-technical reader needs to actually feel the urgency. Rewrote and Jev-tested against real pattic.org
numbers: **this became the best-scoring wording of the entire feature** -- `audience_fit` 43%->55%
`clear_as_is` (the highest this feature has ever reached), `honesty_calibration` 65%, `natural_voice` 78%,
`actionability` 100%. Explaining the real mechanism (reputation/spam-folder cause-and-effect) made the
copy clearer AND better calibrated at the same time, not a trade-off between them.

**Important operational note, not a bug**: while building this chapter's fixes, found 2 REAL notification
sends already logged in `bounce_notification_sends` -- pattic.org (2026-09-30 10:39, 327+36 addresses, to
pooja@aikyamfellows.org/shemeer@aikyamhq.com) and ilabindia.org (2026-09-30 10:41, 54+0, to
info@ilabindia.org). These went out via the real button on the live dashboard, most likely the user
testing the just-shipped Chapter 40 feature themselves -- not triggered by Claude (the standing
never-send-a-real-email-during-development rule was not broken; these predate this chapter's fixes).
Correctly reflects that BOTH real sends used the pre-fix wording (no signoff, bare From address, no
reputation reasoning) -- flagged to the user directly rather than silently reconciled, since the two
recipients already received the older version and the watermark now correctly shows 0 new hard bounces
for pattic.org as a result.

Verified: full 33-domain `build_notification()` sweep (zero errors) with the final wording, live page
re-checked for ilabindia.org showing its real prior send correctly. Service restarted, healthy. No new
email sent by Claude.

---

## Chapter 42 — Two more manual notify buttons: inactive subscribers, domain expiry

Same pattern as bounce-notify (Ch.40-41), at the user's request to extend it to other actions. Explicit
scope constraint this time: "remember only manual buttons and a log if i already send. thats it" -- no
extra automation, no auto-triggering, just a button and a send-history log, matching the existing shape
exactly.

**Inactive subscribers** (`app/subscriber_notify.py`, new `subscriber_notification_sends` table): reuses
the existing `subscriber_engagement_summary()` detection (already built for the dashboard's own CSV
export), with its own independent watermark -- separate from the dashboard's `subscriber_review_watermarks`,
same reasoning as bounce-notify's independent watermark (reviewing on the dashboard and telling the client
are two different real actions). Real data: aikyamjobs.org has 518 subscribers who've received 9+
newsletters with zero opens, out of 4,120 total (12.6%).

Wording deliberately softer than bounce-notify's "confirmed dead, delete" register -- these are still real
people who may have simply lost interest, not broken addresses, so the email never tells the client to
remove anyone, only to decide. Jev-tested (audience_fit/actionability/natural_voice/honesty_calibration)
against real aikyamjobs.org numbers: landed at actionability 98%, natural_voice 74%, honesty_calibration
97%, audience_fit 43% (same real ceiling as every prior notify-email wording round).

**Domain expiry** (`app/domain_expiry_notify.py`, new `domain_expiry_notification_sends` table): simpler
than the other two -- a domain's expiry date is one point-in-time fact, not a "new since" list, so the
send-log exists purely for the operator's own "already told them" visibility, not to scope content. Real
data: aikyamsolve.org expires 2026-11-22 (52 days), registered through Cloudflare, Inc.; captains.ngo
similarly open. Jev-tested, landed strong across all 4 criteria: audience_fit 82%, actionability 100%,
natural_voice 80%, honesty_calibration 93% -- the best-scoring wording of any notify email built so far.

**Refactor**: extracted `_send_manual_notification()` (`app/web.py`) -- the secrets-check/From-header/
HTML-body/send_message block was about to be duplicated a third time; now shared by all 3 notify routes.

**A real bug caught and fixed before shipping**: a `replace_all` edit meant to fix 4 of my own new routes'
tab anchors accidentally also changed an unrelated, pre-existing route (`classify_sender`, the sending-IP
classification form) from `#senders` to `#deliverability` -- caught by grepping all occurrences before
moving on, not assumed safe. A second, separate bug: the domain-expiry button's confirm() dialog used an
HTML entity (`&quot;`) inside a Jinja `{{ }}` expression, which Jinja can't parse (entities aren't Jinja
string syntax) -- caused a real `TemplateSyntaxError` on every domain page load, caught immediately via the
live error log, fixed by matching the bounce-notify button's already-working plain-nested-quotes pattern.

**UI placement, verified against the real tab structure, not assumed**: inactive-subscriber button in the
existing "Inactive subscribers" section (Deliverability & Spam tab); domain-expiry button inline next to
the existing expiry badge (Auth & DNS tab, not Overview as first guessed -- corrected by actually checking
the template's tab-panel boundaries).

Verified: full 33-domain page-render sweep against the real live service (zero errors), both buttons
spot-checked rendering real data (518 inactive subscribers; 52-days-left expiry badge). Direct builder-
function tests confirm correct content/attachment row counts. Service restarted, healthy. No email sent.

---

## Chapter 43 — Reworked campaign_score.py's tips, first step toward a newsletter report card in the PDF

User's framing on why this matters: "there is no point of i tracking their newsletter numbers for them,
but they are not knowing it, then what is the use?" -- the per-campaign scorecard (A-F grade, 6 pillars)
is fully computed for every real send but has zero reader-facing surface (per Ch.39's finding). Before
surfacing any of it, the user asked to rework the scorecard's own internal tip/suggestion wording with
Jev first, since that's the actual content that would reach a client.

**Checked real fire data before testing anything** (same discipline as every other chapter): of the 6
pillars, only `engagement` has ever scored warn/bad on real data (4 times across aikyamjobs.org/
pattic.org's last 50 campaigns). The other 5 are currently dormant -- still worth checking (they'll fire
eventually and this content will need to be solid when they do), but engagement is where the real,
live-firing content lives.

**A real architectural bug found in 2 places, not just a wording issue**: both the engagement pillar's
"inactive subscribers" tip and the hygiene pillar's bounce-cleanup tip referenced "the top/bottom of this
tab" -- a dashboard-layout self-reference baked into content that this exact text is meant to be reused
for (the PDF, this task's whole point). Fixed both to presentation-agnostic phrasing ("ask us any time for
the exact list") that works regardless of where the text ends up.

**Engagement pillar fully reworked**: the original fix text bundled 2-3 unrelated tips into one dense,
run-on paragraph, scoring 54-69% `reads_like_generated_boilerplate` depending on which combination fired.
Split into separate, focused sentences and Jev-tested individually: the clicks tip landed near-even
(52%/48%), the opens/subject-line tip improved to 63% `reads_like_a_person`, and the inactive-subscriber
tip (now free of the dashboard reference) improved to 67% `reads_like_a_person`.

**Structure pillar's fix also reworked**: real `audience_fit` problem found (78% `needs_plain_language_pass`)
even though this is dormant -- the original used "image-to-text ratio" framing implicitly through jargon
like "bit.ly/tinyurl wrapper" without explaining why it matters in plain terms. Rewrote to explain the
actual reader-facing consequence (images-off readers see an empty email) before the technical ask; moved
to 61% `clear_as_is`, natural_voice 40%->71%.

**2 real "tested, no improvement, kept as-is" results**: the complaints pillar's fix (53% boilerplate,
78% audience_fit) -- a rewrite attempt scored worse on every axis (68% boilerplate, 67% audience_fit),
reverted. The technical pillar's fix has a real, documented, OUT-OF-SCOPE issue -- its dynamic `problems`
list (`unsubscribe_issues`/`header_issues`/`display_name_issues`) is generated by `header_compliance.py`/
`display_name_checks.py`, not this module, and contains raw technical phrases ("missing List-Unsubscribe
header") driving a real 61% `too_technical_reader_will_skip` score. Flagged for a future round that
touches those modules directly, not force-fixed here by rewording only the static wrapper text around it.

Verified against all real campaigns for both domains with newsletter data (zero errors). No service
restart needed (pure Python logic change, no template/route touched).

---

## Chapter 44 — Newsletter quality box in the PDF, the middle path between "never show it" and "a raw grade"

User's framing: "there is no point of i tracking their newsletter numbers for them, but they are not
knowing it, then what is the use?" -- directly follows Ch.43's rework. Explicit scope: never a raw
letter grade or 0-100 score (that's still correctly off the table per `jev/CONTEXT.md`'s "audience fears
technology" principle), but SOME real, plain-language verdict plus understandable numbers plus concrete
tips, since the reader here is specifically "someone who writes newsletters" for the org, closer to an
operator of their own tool than the fully non-technical donor-facing reader the rest of the report is
written for.

**New `_newsletter_quality_summary()`** (`app/domain_report.py`): aggregates `campaign_score.py`'s
already-computed, already-reworked (Ch.43) report cards across the report period, using the exact same
period-filtering convention as `_campaign_table()` (Ch.39) for consistency. Three status bands (ok/warn/
bad, matching this project's own established status vocabulary) driven by average score, with a real
"clean count out of total" number instead of the raw average -- "3 of your 4 newsletters came back clean"
is the "understandable numbers metric" the user asked for, not a percentage or score. Tips are the real,
already-Jev-tested `fix` text from each campaign's worst-first `improvements`, deduplicated by pillar,
capped at 2.

**Verdict wording, several Jev rounds** (audience_fit/natural_voice/honesty_calibration), landing with the
same real ceiling pattern seen throughout this whole project -- best combined results around 54-60% on
each axis, not higher despite multiple genuine rewrites.

**A real grammar bug caught by actually rendering a 1-campaign domain, not assumed fine from the
multi-campaign case**: "All 1 of your newsletters this period came back clean" is a real wart when
`total == 1` (pattic.org's real case this period). Added singular/plural branching -- now "Your
newsletter this period came back clean."

**New PDF rendering** (`_newsletter_quality_typst()`, `app/pdf_report.py`): a colored verdict box (ok/
warn/bad, same color tokens every other status box in this module already uses) plus the tip bullets
below it, placed right after the per-campaign table, gated on having newsletter content at all (same
`_campaign_table` gating).

**Standing note for future tip-writing, from the user mid-session**: "when it comes to suggestions on
tech side of things... they might feel a correct what to do kind of tip more useful than some broad
language which makes them confuse... something like opinionated method." Applies going forward to any
improvement-tip wording, newsletter or otherwise -- prefer a single clear, concrete, opinionated
recommendation over hedged/vague "consider..." framing.

Verified against all 33 real domains on both render paths (PDF + email context, zero errors either way).
Visually confirmed on aikyamjobs.org (4 campaigns, 1 improvement) and pattic.org (1 campaign, fully
clean, singular-phrasing fix). Service restarted, healthy, dev-send guard confirmed still paused through
the restart. No email sent.

---

## Chapter 45 — Newsletter engagement trend chart, closing the last Ch.39 survey gap

User approved building the one concrete gap found while answering a question about PDF newsletter
coverage: bounce rate and pass rate both get a real trend chart across time, but newsletter engagement
never did -- only this period's 2 aggregate bars and a per-send table. Checked real data first (same
discipline as every chart added this session): 18 qualifying real campaigns for aikyamjobs.org, 4 for
pattic.org (volume-floor filtered at `MIN_VOLUME_FOR_RATES`, the same constant `campaign_score.py` already
uses), confirming genuine variation worth charting, not noise -- aikyamjobs.org's real data showed a
striking recent spike to 11.2% click rate against a steady ~1-2% baseline, exactly the kind of signal a
single-period aggregate would hide entirely.

**New `chart_data()` key** (`app/domain_report.py`): `newsletter_engagement_series`, built from
`recent_campaigns()` (not a new daily-snapshot table -- sends aren't daily, so per-send is the natural
unit, and this needed zero new data collection). Oldest-first to match every other trend series'
left-to-right convention (`recent_campaigns()` itself returns most-recent-first).

**Two new charts** (`app/pdf_report.py`, `_newsletter_and_closing()`): open-rate-over-time and
click-rate-over-time, reusing the existing `line-chart()` Typst primitive as two separate single-line
charts rather than building multi-series support into that function -- matches this module's own
one-metric-per-chart convention (spam/bounce/pass-rate are each their own chart too). Placed between the
per-send table and the quality verdict box, so the narrative reads: here's each individual send, here's
the trend across sends, here's the overall verdict.

Verified against all 33 real domains (zero errors), visually confirmed on aikyamjobs.org's real data --
both charts render correctly, including the real click-rate spike. Service restarted, healthy, dev-send
guard confirmed still paused. No email sent.

---

## Chapter 46 — The item-assembly-structure redesign, round 1 (real progress, not a full fix)

User asked to start the redesign Ch.36 flagged but didn't do: `_still_open_item()` mechanically
concatenates story/detail/why/history as up to 4 separate, always-present-in-the-same-order sentences,
and that fixed STRUCTURE (not word choice) was identified as the real remaining driver of a
whole-document `natural_voice`/`repetition_risk` boilerplate read.

**Scope for this round**: fuse `why` + `history` into ONE sentence (history is already a short fragment
like "since August", not its own sentence, since Ch.36 -- see `_incident_recurrence`'s new `fragment=True`
mode) instead of rendering them as 2 separate sentences. Prioritized by real currently-open domain count:
`postmaster_compliance` (9), `lookalike_domain` (8), `dns_drift` (7, not yet touched this round --
queued), `spf_missing` (5, queued), `new_sender` (5, already fine), `borrowed_sending_identity` (4).

**A real, previously-uncaught finding, bigger than the structural issue itself**: testing each why-text
in ISOLATION (not just the combined document) surfaced 2 severe honesty_calibration bugs that had shipped
since Ch.18/36 without this specific per-line check ever running: `borrowed_sending_identity`'s "your
mail IS unmistakably yours again" stated the POST-FIX state as present-tense fact for an item that's
still open (91% `overstates_beyond_the_evidence`), and `lookalike_domain`'s "This IS the exact tactic
used to scam donors" asserted an unconfirmed look-alike as an active threat (93%). Both rewritten as
conditional/ongoing, matching what's actually true right now. Real lesson: whole-document testing alone
doesn't catch everything -- isolated per-line checks on already-shipped text found bugs the combined-
document score never flagged on its own.

**Connector testing, not assumed**: tried a "poetic" duration phrase ("a pattern that has held since
August") first -- worked well on `borrowed_sending_identity` (71% `reads_like_a_person`) but caused a
NEW overclaim on `postmaster_compliance` (66% `overstates_beyond_the_evidence`) by implying an
escalating risk had already materialized. Settled on a neutral, risk-agnostic connector ("-- this one has
been open {history}") that tested well on both real, differently-shaped why-texts (65%/62% and 59%/73%)
without the overclaim risk.

**A second intra-item repetition caught by re-reading the real combined output before shipping, not
after**: the first `lookalike_domain` rewrite ended "...which is why we keep an eye on it" -- but that
category's own `story` text already ends "...and we're keeping an eye on what it does." Same phrase,
same item, caught and fixed before testing the combined block (same lesson Ch.36 already learned once
for this exact category).

**Real measured result on tinybridge.in's real 4-category combined block** (the same baseline used since
Ch.36): `honesty_calibration` 79% accurately_calibrated -- a substantial, real improvement (2 of the 4
items had tested at 91-93% overstating individually). `natural_voice` improved modestly, 95%->89%
boilerplate -- real but not a flip. `repetition_risk` essentially unchanged (54%->57% borderline).

**Honest accounting of what's NOT yet fixed**: fusing why+history into one sentence per item didn't
change the fact that every item still follows roughly the same 2-3-sentence shape (opening fact, then a
consequence/reason), repeated 4 times in one document -- that compounding, not any individual sentence's
wording, is very likely the real remaining ceiling. 2 of the 6 priority categories (`dns_drift`,
`spf_missing`) weren't touched this round. Queued for a future round, not forced further here.

Verified against all 33 real domains (zero errors), visually confirmed on tinybridge.in's real PDF --
merged sentence renders correctly, no duplicate phrases. Service restarted, healthy, scheduled sends
confirmed active (user resumed them, verified safe, earlier this session). No email sent.

---

## Chapter 47 — Report Groups: one combined PDF/email for a parent domain + its subdomains

User raised a real duplication problem: `aikyamjobs.org` and its real subdomain `ats.aikyamjobs.org` were
each configured separately for email reports, but share the same real recipients
(`sentinaro@aikyamfellows.org`, `jinso@aikyamfellows.org`) -- so the same person got two near-identical
monthly PDFs, and every future subdomain would repeat the problem. Confirmed with the user before
designing anything: one combined PDF for the whole group, not N separate ones.

**Design constraint, checked against real data first**: `ats.aikyamjobs.org` genuinely has a different
DMARC posture than its parent (different open action items, far fewer ingested reports, earlier `pct=`
ramp stage, as later confirmed live: 94/100 health with 4 open items for the parent vs. 100/100 with
nothing open for the subdomain). Blending or averaging their metrics would misrepresent both -- so
grouping is deliberately an *optional, delivery-only* concept. A grouped domain's own technical analysis
(dashboard, action items, bounce tracking) stays fully independent forever; only the recipient/schedule/
PDF/email layer is shared. A domain not in any group behaves with zero change. A grouped domain's own
`domain_report_settings` row is left in place but unused, so ungrouping later loses nothing.

**What shipped**: `domain_report_groups` + `group_report_sends` tables, `domains.report_group_id`
(nullable FK). `app/pdf_report.py`'s old `_masthead_and_kpi()`/`_newsletter_and_closing()` were split into
reusable zone functions (`_shared_masthead`, `_domain_kpi_header`, `_newsletter_and_tips`, `_closing`) so
`render_domain_report_pdf()` (single-domain, unchanged output) and the new `render_group_report_pdf()`
(one shared masthead/closing, one `#pagebreak()`-separated section per member domain, each built from that
domain's own real, unmodified `_build_context()`/`chart_data()`) share one code path per zone instead of
two. `_build_group_context()` is the single shared context-builder behind both `preview_group_report()`
and `send_group_report_now()` -- same "preview and send must compute identically" discipline as the
existing single-domain pair. New `/report_groups` page + 7 routes (list/create/settings/members/test/
preview/pdf/delete), all mirroring the existing per-domain routes' shape and access level exactly.

**The one new piece of client-facing wording** (`_group_teaser_hook()`, the line that replaces a single
domain's name in the teaser email with an aggregate across the group) was Jev-tested on the real combined
aikyamjobs.org + ats.aikyamjobs.org case before shipping: `honesty_calibration` 95% accurate,
`natural_voice` 30% reads_like_a_person -- a real, modest, documented ceiling, not oversold. The new
group-intro sentence in `_shared_masthead` (is_group=True) tested `audience_fit` 99%, `honesty` 95%,
`natural_voice` 51%.

**Verified on real data, no email ever sent**: created a real test group from `aikyamjobs.org` (id 1) +
`ats.aikyamjobs.org` (id 22) via the new routes, confirmed the member list, settings save, and read-only
`/report_groups/{id}/preview` + `/report_groups/{id}/pdf` routes all work -- visually PNG-checked the
8-page combined PDF: one shared masthead, each domain's real (and genuinely different) KPI/health/action-
item/newsletter sections on its own page(s), one shared closing at the very end. Deleted the test group
afterward (`group_report_sends`/`domain_report_groups` back to 0 rows, both domains back to
`report_group_id = NULL`). Re-rendered all 35 real domains' standalone pages and PDFs after the
`pdf_report.py` zone-builder refactor -- zero errors, confirming the single-domain path is unchanged.
Paused `dev_pause_scheduled_sends` before the one real service restart used to confirm `/report_groups`
and a real domain page both load live, then resumed it immediately after (log showed a clean restart, 0
group sends). No group has ever been enabled with a real recipient outside this already-deleted test.

---

## Chapter 48 — Found and fixed the real sweep-hang: SES drain held one write lock for up to 10 minutes

Follow-up to the unresolved incident in [[dmarctool_sweep_hang_incident]]: the app went fully
unresponsive for 10+ minutes right after the Report Groups work, confirmed via a real
`sqlite3.OperationalError: database is locked` at restart. Root-caused this session instead of guessing.

**Method**: ran the real 20-step periodic sweep against a throwaway *copy* of the real database
(`sqlite3 .backup`) so a repeat hang couldn't touch the live app -- deliberately excluding
`run_ses_event_ingest` from that test since it deletes messages from the real shared SQS queue, and
running it against a disposable DB copy would have permanently lost real events. All 19 other steps
completed cleanly in under a minute. That left `run_ses_event_ingest` as the only untested step, and its
own code comment already documented the exact mechanism: a background drain is allowed to run for up to
`ses_drain_seconds` (600s, a deliberate earlier fix for backlog draining) but the function only called
`conn.commit()` once, at the very end -- holding one open SQLite write transaction for the whole window.
Every single page view writes to `access_log` on its own connection (`audit_middleware`), so any slow
drain blocked the entire dashboard, not just that one check.

**Fix**: moved the commit inside the per-batch loop (`conn.commit()` after each ~10-message batch, which
already happens every ~1-2 seconds) instead of only after the whole drain finishes. The post-loop
aggregate writes (`ses_event_counts`, suppression-notification tallies) are untouched -- they only read
from in-memory dicts accumulated across the loop, so committing mid-loop doesn't affect their correctness.

**Verified live, not just in theory**: ran the real drain against the real database and real SQS queue
(334 real messages waiting) while polling the live dashboard throughout -- drained cleanly in 6.1s, queue
confirmed empty afterward (`ApproximateNumberOfMessages: 0`), dashboard stayed responsive. Today's real
backlog wasn't large enough to reproduce the full multi-minute version of the original hang, but the root
mechanism (one commit per up-to-600s drain) was fixed regardless of today's queue size. Service restarted
once to load the fix, confirmed responsive. `dev_pause_scheduled_sends` was kept PAUSED throughout this
whole investigation and the restart (see [[dmarctool_dev_pause_discipline]] -- the standing rule from this
same incident), not resumed.
