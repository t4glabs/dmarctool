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

*(Next entry: Priority 2 — the report's prose for AI-generated writing tells. A real bug was already found
while reading a live report for this: `_borrowed_identity_detail()`'s trailing period plus the template's
own trailing period produced "...156 days.." — fixed immediately, unrelated to the style audit itself.)*
