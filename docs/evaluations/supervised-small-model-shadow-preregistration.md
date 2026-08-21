# Supervised small-model shadow-loop preregistration

Status: frozen before any supervised-loop model output.

This evaluation tests whether a bounded larger-model review loop can improve a small local
executor while Mnemo carries only verified correction metadata into later fresh sessions. It is
an evaluation-only extension of the active small-model capability ladder. It does not add a
production model router, proxy an agent endpoint, or authorize model output to apply changes.

The maintainer selected the Apache-2.0 `Ministral-3-8B-Instruct-2512` executor, pinned for Ollama as
`ministral-3:8b-instruct-2512-q4_K_M`, and selected OpenAI `gpt-5.6-sol` at medium reasoning as the
frontier advisor. No local model stands in for that role. The evaluation uses the Responses API
with tools disabled, `store: false`, current-turn reasoning context, and strict JSON Schema output.
Live calls remain unauthorized.

## User-selectable advisor configuration

The protocol's `frontier_advisor` object is the secret-free configuration file boundary. Before a
new protocol produces any model output, a user may select another supported provider/model and must
record its source URL, reasoning effort, input/output prices, maximum output tokens, request timeout,
call ceiling, and dollar ceiling. Changing any of these fields creates a distinct evaluation
configuration; it cannot silently alter or resume an existing run. The built-in adapter currently
supports `openai`; other providers still require an injected adapter.

Supervised runs are non-resumable because call and cost ceilings are enforced in process memory.
After an interruption, the user must choose a new immutable run ID and separately authorize the
replacement attempt; this prevents a restart from resetting a prior run's external-spend limits.

The default one-variant engineering configuration permits at most nine frontier calls and $2 of
conservative cost at the configured uncached rates. `OPENAI_API_KEY` is read only from the process
environment and must never appear in the configuration, artifacts, logs, or Mnemo. Configuration
does not grant permission: `live_calls_authorized` remains `false` until a separate approval.

## Conditions and causal comparisons

The frozen conditions use the same original 30-variant, three-fresh-session telehealth corpus and
hidden grader as the Phase 2 capability ladder:

- `SD`: Ministral 3 8B receives the current task and Mnemo context and acts directly.
- `SS`: Ministral drafts; the frontier advisor reviews the parsed candidate; after at most one
  repair, the frontier advisor reviews again. Deterministic consistency checks remain authoritative.
- `TD`: the same frontier advisor performs the same task directly with the same visible context.

The primary quality estimand is `SS - SD` hidden-test accuracy. The quality-ceiling comparison is
`SS - TD`. Token and latency comparisons count every executor and frontier-advisor call. They do
not pretend that local tokens are provider-billed dollars.

## Bounded loop and escalation

The loop permits at most two executor calls and two frontier-advisor reviews per task session. The
advisor receives the original task plus only the executor's parsed, schema-valid candidate; raw
executor text and reasoning are not forwarded. It returns one closed review object containing a
status, bounded failed-field names, bounded repair steps, and bounded uncertainty.

`pass` is advisory. Before changes are accepted, deterministic verification rejects every
remembered-literal mismatch and every changed field that lacks structured support. A malformed
review, `escalate`, a second `repair`, a mismatch, or an unsupported changed field produces
`NEEDS_ESCALATION`; the shadow arm applies no candidate for that session. The harness never loops
again and never lets a model verdict authorize execution.

## Mnemo correction memory

Prompts, response bodies, review prose, repair plans, and model reasoning remain transient. The
OpenAI request is stateless and does not expose tools. Raw
artifacts may retain only hashes, parsed accepted candidate fields, closed status values, field
names from the preregistered schema, deterministic reports, scores, token counts, call counts, and
latency.

After one repaired candidate receives a final advisor `pass` and deterministic consistency
verification, Mnemo may store one compact, evidence-backed task-scoped lesson naming only the
repaired fields. The corrected values already originate from explicit structured user evidence and
remain in normal constraint/decision atoms. A review plan or model assertion is never stored as
truth. The existing bounded lesson carry-forward then makes the marker available in later fresh
sessions.

## Frozen outcomes and verdict

The final run requires all 30 paired variants. Report:

1. hidden-test accuracy and end-to-end success for all three conditions;
2. critical false memories, exact-value integrity, repeated errors, and escalations;
3. executor and frontier-advisor input/output tokens, calls, and latency separately;
4. verified correction markers created and their later retrieval count;
5. total-token change for `SS` versus both `SD` and `TD`.

The result is `PROMISING` only if all 30 pairs are complete, `SS - SD` hidden-test accuracy is at
least +0.10 with a paired 95% interval above zero, `SS` is no more than 0.02 below `TD`, `SS` has
zero critical false memories, and `SS` uses at least 30% fewer frontier-model tokens than `TD`.
It is `QUALITY_ONLY` when the quality and safety gates pass but the token gate fails, `REJECT` when
a quality or safety gate fails, and `NOT_EVALUATED` for an incomplete or engineering run. These
labels apply only to this synthetic workload and these installed models.

## Execution boundary

The first one-variant supervised run is an excluded engineering dry run. Although the advisor is
pinned, the run cannot begin until live calls are separately authorized and the environment-only
credential is available. It cannot change thresholds or contribute to the final verdict. A final
30-variant run requires another explicit approval and revised operational call/cost ceilings after
the harness, privacy checks, and dry-run artifacts have been reviewed.
