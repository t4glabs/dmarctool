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
   gets an entry in `DECISIONS_LOG.md`, AND (for anything substantial, i.e. worth a reader/owner seeing)
   a new or updated section in the hosted "Jev findings & improvements" artifact
   (`https://claude.ai/code/artifact/6bf9c5d4-632b-469d-a0e3-b88bf188478c`) — same pattern as Chapters 1
   and 2: problem found, Jev's raw output, the actual fix, kept in sync (never left saying "Proposed"
   once it's shipped).
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

## Relationship to the hosted artifact

The artifact is the reader-facing, narrative record ("book of chapters") — what a human skimming the
project's history would want to see: problem, evidence, fix, in prose. `DECISIONS_LOG.md` is the
lower-level, append-only ledger of individual Jev calls and their raw verdicts, useful for tracing exactly
which criterion caught what. Both get updated together; neither replaces the other.
