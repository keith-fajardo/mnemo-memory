# Local-first frontier-subscription savings preregistration

Status: frozen before implementation and before any v5 model output.

This evaluation-only protocol follows the incomplete v4 engineering run and changes only the
selected route and frontier safety ceilings. It does not create a production model route or proxy
an agent's configured endpoint. The synthetic corpus, local Ministral executor, subscription-backed
GPT-5.6 Sol advisor, `SD`/`SS`/`TD` conditions, deterministic checks, privacy rules, scoring, and
quality and savings thresholds remain fixed.

## Selected treatment and comparison

Every `SS` session uses `local_first`, regardless of its frozen risk tags. Ministral produces the
initial candidate. A parse-valid candidate is accepted without a frontier call only when it has no
invalid values, no deterministic remembered-literal mismatch, and structured support for every
changed field. A failed gate invokes one frontier review. A `repair` permits one local repair and
one final frontier review. Malformed, unsupported, mismatched, or unresolved output fails closed.
Frontier planning and direct frontier takeover are disabled for `SS`.

`SD` remains direct local execution and `TD` remains direct frontier execution. The primary
economic metric is `frontier_token_savings_vs_direct`: only frontier input plus frontier output
tokens in `SS` and `TD` enter that comparison. Local executor tokens remain reported as a secondary
latency and total-compute metric but do not reduce subscription-token savings.

## Frozen bounds

One `SS` session can make zero frontier calls on a deterministic pass or at most two after a failed
gate. Across three sessions plus three `TD` calls, the one-variant maximum is nine frontier calls.
The cumulative reported-token ceiling is 180,000. This ceiling was selected after v4 observed
62,249 reported tokens over four successful calls; a linear nine-call projection is about 140,061,
with the remainder reserved for bounded variation. It is a fail-closed safety ceiling, not expected
usage or permission for an additional run. A provider response can cross the ceiling before its
reported usage is validated, as documented in the threat model.

## Claims and authorization

The existing 30-pair quality and 30% frontier-token-savings gates remain unchanged. A one-variant
engineering run is operational evidence only and remains `NOT_EVALUATED`; it cannot establish
general savings or quality. Prompts, responses, critique, reasoning, and credentials remain
transient. Artifacts retain only bounded hashes, parsed accepted fields, closed statuses,
deterministic reports, fixed route metadata, calls, tokens, scores, and latency.

The canonical fixture leaves `live_calls_authorized` false and frontier takeover false. The
maintainer approved exactly one live synthetic engineering variant after implementation and the
complete repository gate. No larger run, production route, deployment, or release is authorized.
