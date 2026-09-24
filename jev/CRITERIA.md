# The standard Jev checklist

Every future report-content or dashboard-copy decision runs through this checklist before shipping,
per the user's standing instruction: "every decision you wanted to take... make all these as checklist
in your workflow and run it through jev every single time." Each entry below is a reusable Jev question
template, implemented in code at `app/jev_context.py::CRITERIA`. `state` is always the specific piece of
content being judged (a sentence, a section, a whole report); `CONTEXT.md` content is prepended to every
`state` automatically by `app/jev_context.py::ask_with_context()` so Jev always has the full picture, not
just the isolated snippet.

Not every criterion applies to every decision — pick the ones relevant to what's being judged (see
`WORKFLOW.md` for how to choose). Running all seven on trivial copy is wasteful; running zero on a new
report section is a process failure.

**Audience scope, clarified in Chapter 8:** `AUDIENCE_CONTEXT` (and therefore every criterion here) is
calibrated for the non-technical NGO reader the CLIENT REPORT is written for. It does not apply the same
way to operator-only dashboard content (`labels.py`'s `CATEGORY_HELP`/`CATEGORY_REMEDIATION` tooltips,
aimed at Aikyam itself) — that content is *supposed* to use real technical terms (DKIM, DMARC, DNS
records), the same way the dashboard already does more broadly per `CONTEXT.md`. Running `audience_fit` or
`emotional_resonance` against operator copy will produce a misleading "needs plain language" verdict for
content that's already correctly calibrated for its real audience. `actionability` and
`contradiction_check` generalize fine to either audience (concreteness and internal consistency matter
regardless of who's reading); `honesty_calibration` does too. When judging operator-only copy, pick from
that subset and skip `audience_fit`/`emotional_resonance`/`repetition_risk` (the last is specifically about
what a non-technical reader would recognize as boilerplate across monthly reports — operator tooltips
aren't read that way).

## 1. `audience_fit` (Choice)
**Question:** Is this understandable to a non-technical NGO staffer with zero DMARC/email-infrastructure
background, with no jargon requiring a definition?
**Options:** `clear_as_is` / `needs_plain_language_pass` / `too_technical_reader_will_skip`
**Ties back to:** CONTEXT.md "Voice rules" — no DMARC/SPF/DKIM/policy/percent words at all.

## 2. `usefulness` (Score, 1-5)
**Question:** How much does this help the reader feel safer about their email, or take a concrete next
action — versus being noise that doesn't change what they understand or do?
**Rubric:** 1 = pure noise/filler -> 5 = directly changes what the reader understands or does next.
**Ties back to:** CONTEXT.md "What this tool deliberately does NOT chase" — judged against this specific
audience's real needs, not generic completeness.

## 3. `repetition_risk` (Choice)
**Question:** Given this exact wording has plausibly appeared in a prior report to the same reader, will
it read as fresh information or as boilerplate they'll skip?
**Options:** `fresh_or_appropriately_reframed` / `borderline_needs_variation` / `reads_as_boilerplate`
**Ties back to:** CONTEXT.md "Repetition/boredom — the newest and most significant lesson."

## 4. `emotional_resonance` (Score, 1-5)
**Question:** Does this content help the reader feel that trusting Aikyam with their email was the right
call — safety, protection, a threat that was caught, a improvement visible over their own past?
**Rubric:** 1 = flat/procedural, no feeling either way -> 5 = a genuine "wow, glad someone's watching this"
moment.
**Ties back to:** CONTEXT.md "What the report must make them feel" and "The wow factor."

## 5. `honesty_calibration` (Choice)
**Question:** Does the strength of the claim ("blocked" vs. "seen and logged", "safe" vs. "reviewed and
currently harmless") match exactly what the underlying data actually shows, with nothing overstated?
**Options:** `accurately_calibrated` / `understates_a_real_win` / `overstates_beyond_the_evidence`
**Ties back to:** CONTEXT.md "Honesty is non-negotiable in this framing" — an overstatement is worse than
an understatement, because it's the kind of claim a technical reader could later catch Aikyam in.

## 6. `contradiction_check` (Noul)
**Question:** Considered next to the rest of the same report, does this content ever imply the opposite
of something else stated in the same document (e.g. praised as reliable in one section, flagged as a live
problem in another)?
**Ties back to:** CONTEXT.md "Traps already learned the hard way" — self-contradiction and mis-gated
praise.

## 7. `actionability` (Choice) — only for tips/recommendations, not status updates
**Question:** If this is advice rather than a status update, is there a specific, concrete next step the
reader (or aikyam on their behalf) can take — or is it vague enough that no one would know what to
actually do?
**Options:** `concrete_next_step` / `vague_needs_specifics` / `not_applicable_this_is_status_not_advice`
**Ties back to:** CONTEXT.md "Vague-and-alarming language" and "Filler tips."

## How these map to Jev's three primitives

- **Noul** — binary risk flags, cheapest/fastest: `contradiction_check`.
- **Choice** — categorical judgment with calibrated confidence: `audience_fit`, `repetition_risk`,
  `honesty_calibration`, `actionability`.
- **Score** — where a continuum genuinely exists: `usefulness`, `emotional_resonance`.

## What Jev is never asked to do

Per its own structural limits (`app/jev_client.py` docstring) — Jev returns zero free text, so it is never
asked to draft, rewrite, or generate report copy. It only ever judges copy Claude has already written.
