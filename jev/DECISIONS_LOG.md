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

*(Next entry: Chapter 5 — remaining unaudited `USE_CASES.md` sections, starting with D (streak/repetition
across real long-running domains) and H (newsletter engagement, once Listmonk sync is unblocked).)*
