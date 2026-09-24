# jev/ — the Claude + Jev report-quality workflow

This directory is the persistent, coded-not-remembered record of how Claude uses Jev (TypeSafe AI's
structured-decision model) to evaluate DMARCTool's client-facing content, per the user's explicit
2026-09-24 instruction: "do your best to implement this jev linking workflow in best way... do not ever
forget about this workflow so code it in your workflow in configs etc not as a memory."

**Read `CONTEXT.md` first**, then `CRITERIA.md`, then `WORKFLOW.md` before touching any report or
dashboard copy. `USE_CASES.md` is the reference list of where this applies inside the real codebase.
`DECISIONS_LOG.md` is the running ledger of actual calls made and what changed as a result.

| File | What it is |
|---|---|
| `CONTEXT.md` | Who Aikyam is, who the audience is, the voice rules, the wow-factor philosophy, the traps already learned the hard way. Mirrored in code as `app/jev_context.py::AUDIENCE_CONTEXT`. |
| `CRITERIA.md` | The 7-question standard checklist (audience fit, usefulness, repetition risk, emotional resonance, honesty calibration, contradiction check, actionability). Mirrored in code as `app/jev_context.py::CRITERIA`. |
| `USE_CASES.md` | 100 concrete, codebase-grounded decision points where this checklist applies. |
| `WORKFLOW.md` | The step-by-step process — when this applies, how to run it, the data-handling rule, how it relates to the hosted artifact. |
| `DECISIONS_LOG.md` | Append-only ledger of every real Jev call: what was judged, the verdict, what changed. |

## The code half

`app/jev_context.py::ask_with_context(state, criteria_names)` is the one function this whole workflow
runs through — it prepends `AUDIENCE_CONTEXT` automatically and looks up the named criteria from
`CRITERIA`, so a caller never has to hand-assemble a Jev request. It wraps `app/jev_client.py::ask()`,
which is the thin stdlib-only HTTP client for the real Jev API (unrelated to this workflow's content —
that file is pure transport).

## The one hard rule that never changes without the user saying so

Real client/domain/email details may be sent to Jev (explicit authorization, 2026-09-24). Nothing from
`secrets.env` ever is. See `WORKFLOW.md`, "Data-handling rule."

## The hosted artifact

The narrative record of actual audit rounds ("chapters" — problems found, Jev's raw output, the fix
shipped) lives at `https://claude.ai/code/artifact/6bf9c5d4-632b-469d-a0e3-b88bf188478c`, not in this
directory — this directory is the process and the ledger; the artifact is the readable story.
