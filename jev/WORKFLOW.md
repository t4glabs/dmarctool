# The Claude + Jev workflow

This is a standing process, not a one-time audit. From 2026-09-24 onward, per the user's explicit
instruction, every future decision about report/dashboard content in DMARCTool follows this. It's
committed to the repo (not Claude's memory) specifically so it survives across sessions.

## When this workflow applies

Any time Claude is about to:
- Write or rewrite a client-facing report sentence, section, or tip (`app/domain_report.py`).
- Write or rewrite a dashboard label, tooltip, or help text meant for the non-technical reader
  (`app/labels.py`).
- Add a brand-new action-item category and decide whether/how it should ever reach a client report.
- Decide whether a piece of internal/dashboard data should be promoted into a client report at all.
- Run a periodic audit (a new "chapter") of existing report content.

It does **not** apply to: internal code structure, SQL, DNS/API integration logic, or anything that
never renders as text a client reads. Jev judges copy; it has no opinion on implementation.

## Step by step

1. **Write the content first**, as a real, specific draft — never ask Jev to write it (see `CRITERIA.md`,
   "What Jev is never asked to do").
2. **Pick the relevant criteria** from `CRITERIA.md` — usually 2-4 of the 7, not all 7 every time. Use
   `USE_CASES.md` to find the closest matching use case if unsure which criteria apply; each use case
   names its relevant checks.
3. **Call `app/jev_context.py::ask_with_context(state, criteria_names)`** — this automatically prepends
   the full `CONTEXT.md` audience/voice/history context to whatever specific content is being judged, so
   Jev is never evaluating a sentence in isolation.
4. **Read the result honestly.** A low `usefulness`/`emotional_resonance` score, a `needs_plain_language_pass`
   / `reads_as_boilerplate` / `overstates_beyond_the_evidence` verdict, or a `contradiction_check` flag
   above ~30% is a real signal to revise, not a score to explain away.
5. **Revise and re-check** if the first result was negative. Don't ship on a bad Jev read without either
   fixing it or explicitly deciding (and noting why, in `DECISIONS_LOG.md`) that the content should ship
   anyway.
6. **Log it.** Every real Jev-informed decision — finding, verdict, and what actually changed in code —
   gets an entry in `DECISIONS_LOG.md`. That's the sole record as of 2026-09-30 (see below) — no hosted
   artifact update needed anymore.
7. **For a full audit round** (a new "chapter," not a single decision): work through `USE_CASES.md`
   systematically by section, pull real current report content for each check (never synthetic examples
   — this project's own working style requires validating against real data), and produce one new chapter
   in the artifact summarizing everything found and fixed that round.

## Data-handling rule (hard, never relaxed further without the user saying so explicitly)

Real client details, domain names, and email addresses **may** be sent to Jev as part of `state` —
explicit user authorization, 2026-09-24. This extends to the hosted "Jev findings & improvements" artifact
too — the user confirmed the same day that real domains/incident detail are also fine on that published
page, not just inside the Jev API call itself (Chapters 1-2 anonymized there; Chapter 3 onward does not).
**Nothing from `secrets.env`** (API keys, passwords, tokens, OAuth credentials, AWS keys, IMAP passwords,
Listmonk tokens) is ever part of a Jev call OR the hosted artifact, under any circumstance.
`app/jev_context.py` never imports or reads `app/config.py::get_secret()` for anything except the
`JEV_API_KEY` itself (needed to authenticate the call), and that key is passed only as the `Authorization`
header, never inside `state` or `questions`.

## Cost/latency shape (why this is cheap enough to run routinely)

Jev responds in 70-500ms at $0.042/M input tokens with free output — a single report-content decision
costs a fraction of a cent and adds well under a second. There's no practical reason to skip this
workflow for being "too slow or expensive" for routine use; the actual cost of skipping it is the kind of
repetition/boredom/contradiction bugs this workflow was built to catch (see `CONTEXT.md`, "Traps already
learned the hard way").

## Relationship to the hosted artifact (retired 2026-09-30)

Chapters 1-39 kept a hosted "Jev findings & improvements" artifact in sync with `DECISIONS_LOG.md` as a
reader-facing narrative version of the same record. The user retired this step 2026-09-30: "since you
already log jev decision log in github md file we can skip the artifcats updations from now onwards i
guess, i can check github and that log file when required." `DECISIONS_LOG.md` (committed, pushed to
GitHub) is now the single source of truth — no hosted artifact to keep in sync, no risk of the two
drifting apart. The old artifact URL is left as historical reference only; do not update it further
unless the user explicitly asks for it again.
