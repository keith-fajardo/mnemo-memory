# Disciplined Markdown vs Mnemo Proof-or-Stop Plan

> **Planning status:** approved for preregistration and offline deterministic implementation on
> 2026-08-18. Live agents, Ollama, Phase 3, deployment, and release remain outside this approval.

**Goal:** Test whether Mnemo provides a useful, measurable advantage over a carefully maintained
project-local Markdown handoff. Give Markdown every simple capability it can honestly provide, and
attribute a Mnemo advantage only to behavior that its storage and policy enforce mechanically.

**Architecture:** Add an evaluation-only driver with three conditions: no durable memory (`NM`), a
disciplined Markdown handoff (`DM`), and the real local Mnemo checkpoint service (`MR`). Reuse the
six original synthetic scenario families. Both durable-memory conditions receive the same selected
facts and evidence references and expose equivalent untrusted current and history views. The
offline driver records only event keys, hashes, counts, grades, and timings.

**Scope boundary:** This is an evaluation issue, not a product-feature issue. It adds no model
proxy, model call, embedding, dependency, migration, retention behavior, semantic mutation,
decomposition, connector, deployment, or release behavior.

## Why this comparison is needed

The existing evaluations compare Mnemo with no memory, full usable history, rolling summary, and
other Mnemo conditions. They do not answer the simpler product question: “Why not ask Codex or
Claude to maintain a good Markdown handoff?”

A fair answer cannot make Markdown deliberately weak. The `DM` baseline therefore gets:

- one project-local file with a compact current section and append-only evolution history;
- explicit evidence/event references;
- a visible untrusted-evidence warning;
- deterministic, perfectly applied fixture updates in the offline test; and
- selective delivery of the current section rather than forced replay of the full file.

This is an optimistic upper bound for a disciplined Markdown workflow. It removes model writing
mistakes from the offline comparison. A later live pilot may separately test whether agents can
maintain that discipline, but it is not authorized here.

Plain Markdown does not, by itself, provide an atomic compare-and-swap update. The concurrency
probe therefore uses two writers that read the same file revision and save normally; the last write
wins. Adding a lock service, database, or mandatory per-update Git workflow would be an additional
concurrency mechanism and must be named as such rather than credited to the file format.

## Frozen conditions

- `NM` — no durable memory; receives only the fixed resume request.
- `DM` — disciplined Markdown; receives the selected current section and can separately inspect
  the append-only history section.
- `MR` — real local Mnemo checkpoint storage; receives the equivalent current checkpoint and can
  separately inspect bounded checkpoint lifecycle history.

All conditions use the same six source families and the same initial/revised event-key schedule.
The source bodies are loaded only in process from
`tests/fixtures/evals/viability-corpus-v1.json`.

## Frozen measurements

For every scenario and condition, record:

- required current fact available;
- superseded fact absent from the current view when the source explicitly supersedes it;
- current fact and prior fact present in the audit/history view when applicable;
- current evidence reference available;
- exact next action available;
- critical false-memory count;
- cross-project disclosure count;
- conservative tokens in the equivalent current working view;
- conservative tokens in the optional audit/history view;
- zero Mnemo model calls and tokens; and
- stale concurrent write outcome and whether the first writer's update survives.

The Markdown and Mnemo current views must both include a short equivalent warning that their
contents are untrusted evidence and never approval. Audit/history tokens are reported separately
because a working agent need not receive them on every turn.

## Preregistered verdicts

The runner must decide one of these labels without changing the thresholds after results exist:

- `INVALID`: a contamination control fails, a source family is missing, a protected cross-project
  marker is disclosed, or a row is incomplete.
- `NOT_EVALUATED`: no usable rows exist.
- `DIFFERENTIATED`: `DM` and `MR` both achieve perfect current-fact, supersession, provenance, and
  next-action grades; `MR` rejects every stale concurrent write while preserving the winning
  revision; `DM` does not; and total `MR` current-view tokens are at most 1.25 times `DM`.
- `TRADEOFF`: the same correctness and mechanical-enforcement requirements pass, but `MR` current
  tokens exceed 1.25 times `DM`. This means Mnemo demonstrates enforced safety but not compactness
  against Markdown.
- `PARITY`: both preserve the handoff, but Mnemo demonstrates no frozen enforcement advantage.
- `MARKDOWN_PREFERRED`: Markdown meets the correctness gates and Mnemo is less correct, leaks
  scope, or adds more than 25% current-view tokens without the enforcement advantage.

`DIFFERENTIATED` supports a later, separately approved live pilot. `TRADEOFF` supports only a
governance-focused pilot and requires token simplification before a token-saving claim. `PARITY` or
`MARKDOWN_PREFERRED` triggers `STOP_FEATURE_EXPANSION`: do not add Phase 3 to rescue the claim.

Offline results cannot prove model-generated task correctness, user adoption, or market value.
Those remain `NOT EVALUATED` until a separately approved live agent study is run.

## Privacy and trust requirements

- No transcript, prompt, response, reasoning, tool body/result, source event summary, or rendered
  memory body is stored in result artifacts.
- Temporary Markdown and SQLite state remain outside immutable result artifacts.
- Result artifacts contain only fixture event keys, hashes, numeric measurements, verdicts, and
  provenance paths.
- Retrieved records remain `untrusted_evidence` and never authorize an action.
- Exact task/project scope is required before either durable condition is read.
- No competitor artifact, dependency, prompt, schema, or runtime is used.

## Task 0: Freeze the contract

**Files:**

- Add `docs/evaluations/markdown-handoff-proof-preregistration.md`.
- Add `tests/fixtures/evals/markdown-handoff-proof-v1.json`.
- Add `tests/evals/test_markdown_handoff_proof.py`.

**TDD:** Write contract tests first, confirm they fail because the preregistration and fixture are
absent, add the two frozen artifacts, confirm the contract tests pass, and commit.

## Task 1: Implement the disciplined Markdown baseline

**Files:**

- Modify `tests/evals/test_markdown_handoff_proof.py`.
- Add `scripts/run_markdown_handoff_proof.py`.

**TDD:** First require deterministic rendering, current/history separation, evidence references,
scope-bound file selection, conservative token counts, and the frozen last-writer-wins concurrency
probe. Confirm failure because the runner is absent. Add the smallest standard-library-only
implementation, confirm pass, and commit.

The Markdown helper belongs only to the evaluation. It is not a new Mnemo product API.

## Task 2: Add the real Mnemo comparison and verdict

**Files:**

- Modify `tests/evals/test_markdown_handoff_proof.py`.
- Modify `scripts/run_markdown_handoff_proof.py`.

**TDD:** First require real SQLite checkpoint create/revise/get calls, revision history, stale-write
rejection, exact-scope isolation, paired rows, payload-free artifacts, deterministic analysis, and
the frozen verdict. Confirm failure, add the minimal driver and analysis, confirm pass, and commit.

Do not change product code unless a failing real-path contract exposes a product defect. If that
happens, stop and report the newly discovered scope before editing product behavior.

## Task 3: Validate and stop at the approved boundary

Run the focused evaluation tests, then `npm run check`. Produce one immutable offline result only
after both gates pass. Verify artifact hashes and scan artifacts for forbidden source bodies. Update
`docs/implementation-status.md`, commit the result and status, save a compact Mnemo checkpoint, and
stop.

Do not run Ollama or any other model. A live comparison needs a new approval and a separate frozen
protocol defining agent models, repetitions, task-correctness scoring, provider-token accounting,
and time budget.
