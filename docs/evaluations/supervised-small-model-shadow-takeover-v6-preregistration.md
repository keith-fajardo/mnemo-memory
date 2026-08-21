# Direct frontier takeover engineering evaluation

Status: frozen before any v6 model output. No live run is authorized by this document.

This evaluation-only protocol follows the incomplete 30-variant local-first run. It tests whether
one direct frontier execution is a better fallback than asking the same local executor to repair a
deterministic failure after frontier critique. It does not create a production route, proxy an
agent endpoint, or change the configured local or frontier models.

## Treatment route

Every `SS` session starts with one Ministral execution. A parse-valid candidate is accepted with
zero frontier calls only when it contains no invalid value, no deterministic remembered-literal
mismatch, and no changed field without structured support. When that gate fails and
`frontier_takeover` is enabled, the original session task is sent once to the direct frontier
executor. The route makes no advisor-review call, creates no frontier plan, and makes no local
repair attempt.

The direct frontier result remains an untrusted proposal. The same deterministic checks validate
it before any change is accepted. A malformed, invalid, mismatched, or unsupported result fails the
session closed and stops the trajectory. Takeover output cannot override memory-backed constraints.
The `frontier_takeover_call_count` separately identifies direct fallback calls in session,
trajectory, and aggregate records.

`SD` remains direct local execution and `TD` remains direct frontier execution. Existing schema
4.0 and 5.0 fixtures must keep takeover disabled; only schema 6.0 may opt into it.

## Bounds and claims

The first permitted engineering run, if separately approved, is one synthetic variant. `SS` can
make at most one frontier call in each of three sessions and `TD` makes three, so the combined
maximum is six frontier calls. The cumulative reported-frontier-token ceiling remains 180,000.
These limits are fail-closed ceilings, not expected usage, and a provider response can cross the
token ceiling before its usage is validated.

One variant can verify routing order, deterministic acceptance, fail-closed behavior, accounting,
and artifact privacy. It cannot establish quality or savings. The analyzer now makes
`frontier_token_savings_vs_direct` and `total_token_savings_vs_direct` unavailable and keeps the
token gate false whenever the preregistered complete paired population is missing. A later
30-variant run requires separate approval and a new frozen cumulative ceiling.

The canonical fixture keeps `live_calls_authorized` false. Prompts, responses, plans, critique,
reasoning, and credentials remain transient. Artifacts may retain only bounded hashes, accepted
schema fields, deterministic reports, fixed route metadata, calls, tokens, scores, and latency.
