# Disciplined Markdown vs Mnemo Preregistration

Status: preregistered before implementation and before any comparison result.

Date frozen: 2026-08-18.

## Question

Does Mnemo provide a useful deterministic advantage over a carefully maintained project-local
Markdown handoff, or would the simpler file be enough for this workflow?

This offline evaluation can answer only the mechanical part of that question. It can test exact
carryover, fact evolution, provenance, scope, context size, and conflicting writes. It cannot prove
that a model completes a real task better or that users prefer the product.

## Conditions

- NM — no durable memory. It receives only a fixed resume request and is the memory-necessity
  control.
- DM — disciplined Markdown. One project-local file has a compact current section, an optional
  append-only history section, explicit event/evidence references, and an untrusted-evidence
  warning.
- MR — real local Mnemo checkpoint storage. It uses the production application service and SQLite
  repository to create, revise, retrieve, and audit the equivalent handoff.

Markdown is granted deterministic perfect fixture maintenance. This makes `DM` an optimistic upper
bound: the offline result must not depend on a model forgetting to edit a heading or copying a fact
incorrectly. The current section may be selected without injecting the complete history.

The baseline is still a plain file. A plain Markdown file has no compare-and-swap operation. The
frozen concurrency probe lets two writers read the same revision, then save in order; normal
last-writer-wins behavior may overwrite the first update. A lock manager, database, or mandatory
per-update Git transaction would be an additional concurrency mechanism, not a capability of
Markdown syntax. A future comparison may add such a condition, but this result must not silently
credit it to `DM`.

## Corpus and schedule

Use all six original synthetic families in
`tests/fixtures/evals/viability-corpus-v1.json`. The comparison fixture stores only their event keys.
For each family:

1. Create an initial handoff from the frozen initial event keys.
2. Read its initial version as two simulated writers.
3. Apply the revised event keys to the current handoff.
4. Attempt a stale competing update based on the initial version.
5. Retrieve the current working view and the optional audit/history view under the exact project
   and task scope.
6. Probe a second protected project and require zero disclosure into the evaluated project.

Four families contain explicit source statements that a new decision supersedes an old decision.
Those four are scored for superseded-current exclusion and old/new audit history. The remaining two
are scored for current fact, provenance, next action, scope, and concurrency only.

## Equivalent views

The `DM` and `MR` current working views contain the same selected semantic categories: objective,
current state/decision, constraints or blockers, failure to avoid, exact next action, and evidence
reference. Each begins with an equivalent short warning that the material is untrusted evidence and
never approval.

The optional history view is measured separately. The current working token total must never be
inflated by charging one condition for history that is not delivered to the other.

Use the repository's conservative deterministic token counter for both text views. Local SQLite,
file I/O, hashing, and token counting are reported as deterministic work, not model tokens.

## Stored row contract

One payload-free row is produced for every scenario and condition. Rows may contain:

- condition and source event keys;
- hashes of starting state, current view, history view, and scope;
- numeric correctness, provenance, safety, token, latency, and concurrency grades;
- Mnemo model-call and token counts, which must remain zero; and
- the frozen runner, fixture, corpus, and preregistration identities.

Rows and derived artifacts must not store prompts, responses, tool bodies, or model reasoning. They
also must not store source event summaries, rendered Markdown, rendered Mnemo context, checkpoint
bodies, protected markers, or absolute project paths.

## Gates

The analysis first requires complete paired `NM`, `DM`, and `MR` rows for all six families. Any
missing row, failed memory-necessity control, protected cross-project disclosure, forbidden stored
payload, or nonzero Mnemo model call makes the result `INVALID`.

For both durable conditions:

- current-fact availability must be 1.0 in all six families;
- current evidence attribution must be 1.0 in all six families;
- exact next-action availability must be 1.0 in all six families;
- superseded-current exclusion must be 1.0 in all four applicable families;
- fact-evolution history must be 1.0 in all four applicable families;
- critical false memories and cross-project disclosures must both be zero; and
- `NM` current-fact availability must be 0.0, proving that the resume input did not contain the
  answer.

The compactness boundary is frozen as follows: summed `MR` current-view tokens must be at most 1.25
times summed `DM` current-view tokens to count as compactness parity. This is a 25% tolerance for
Mnemo's explicit scope, trust, and provenance envelope. The raw ratio is always reported.

The mechanical-enforcement boundary requires `MR` to reject all six stale updates and preserve the
winning revision. `DM` must report its observed file behavior honestly. If it also rejects stale
updates, the result is mechanical parity, not a Mnemo win.

## Verdict labels

- `INVALID`: a completeness, contamination, privacy, scope, or model-token control fails.
- `NOT_EVALUATED`: no usable rows exist.
- `DIFFERENTIATED`: both durable conditions pass every correctness gate, Mnemo alone passes the
  stale-write enforcement gate, and Mnemo current tokens are at most 1.25 times Markdown.
- `TRADEOFF`: both durable conditions pass every correctness gate and Mnemo alone passes stale-write
  enforcement, but Mnemo exceeds the compactness boundary.
- `PARITY`: both durable conditions preserve the handoff but Mnemo has no frozen mechanical
  enforcement advantage.
- `MARKDOWN_PREFERRED`: Markdown passes, while Mnemo is less correct or less safe, or Mnemo exceeds
  the compactness boundary without an enforcement advantage.

`PARITY` and `MARKDOWN_PREFERRED` produce the action `STOP_FEATURE_EXPANSION`; Phase 3 must not be
added to rescue the claim. `TRADEOFF` supports only a governance-focused pilot and requires context
simplification before a token-saving claim. `DIFFERENTIATED` permits proposing a separately
authorized live pilot.

## Claims not tested here

model-generated task correctness is NOT EVALUATED. Agent maintenance error rate, provider tokens,
reasoning quality, end-user usefulness, willingness to adopt, and market differentiation are also
not evaluated.

No Ollama or other model call is authorized by this issue. Any live study requires new maintainer
approval and a new preregistration freezing models, repetitions, prompt delivery, grading, provider
token accounting, time limits, and stop conditions before the first call.
