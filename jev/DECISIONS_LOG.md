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

*(Next entry: Chapter 9 — section G (deciding what dashboard-only data should ever cross into a client
report at all) has real candidate data and hasn't been touched; section D's remaining items (streak-bucket
repetition, health-trajectory wording) also have real multi-month data available now.)*
