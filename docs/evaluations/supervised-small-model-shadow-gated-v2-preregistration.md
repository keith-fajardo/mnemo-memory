# Deterministic-gated supervised small-model shadow preregistration

Status: frozen before any gated-v2 model output.

This evaluation changes one variable from the frozen v1 supervised protocol: whether the initial
frontier review is called. The local executor, frontier model, reasoning effort, prompts, corpus,
conditions, repair limit, final review after frontier intervention, scoring, privacy boundary, and
quality and token thresholds remain unchanged. This isolates whether deterministic call gating can
reduce paid frontier use without reducing measured quality or safety.

The v1 one-variant engineering run is motivation, not proof. Two of its three supervised sessions
received a frontier `pass` even though their local candidates already had no deterministic mismatch
and every changed field had structured support. V2 tests whether those initial reviews can be
avoided. It does not rewrite or reinterpret the immutable v1 result.

## Gating rule

For each `SS` session, Ministral produces one local candidate. The initial GPT-5.6 Sol review is
skipped only when all of these deterministic conditions hold:

1. the local response is parse-valid;
2. it contains no invalid field or value;
3. the accumulated candidate has no mismatch with active structured memory; and
4. every changed field has structured support in the active verifier evidence.

When all four hold, the candidate may proceed under the existing deterministic acceptance rule and
the session records zero frontier calls. This is not model self-approval: the gate is standard
library and Mnemo verifier logic over explicitly structured evidence.

If any condition fails, the initial frontier review runs. A `repair` still permits only one local
repair, followed by the existing mandatory final frontier review. A malformed review, `escalate`, a
second `repair`, a remaining mismatch, or an unsupported change still produces `NEEDS_ESCALATION`
and applies no candidate. Once the frontier participates, v2 never skips its final check.

## Frozen comparison and limits

The same `SD`, `SS`, and `TD` definitions and 30 paired variants apply. The primary quality
comparison remains `SS - SD`, the quality ceiling remains `SS - TD`, and the economic gate remains
at least 30% fewer `SS` frontier tokens than `TD`. Executor and frontier tokens, calls, and latency
remain separately reported. Local tokens are not treated as provider-billed dollars.

The provider/model remains user-selectable through the secret-free configuration before a run.
This v2 fixture pins Ministral 3 8B Instruct Q4_K_M and OpenAI `gpt-5.6-sol` at medium reasoning so
that review gating is the only changed treatment. The worst-case ceiling remains nine frontier
calls for a one-variant engineering run because every supervised session can still require two
reviews and every direct-frontier session one call. The configured $2 conservative ceiling is also
unchanged.

No live call is authorized by this preregistration. `live_calls_authorized` remains `false`. A
one-variant v2 engineering run requires separate maintainer approval, a fresh immutable run ID, and
temporary explicit authorization. A later 30-variant final run requires its own approval and
revised operational ceilings.

## Privacy and product boundary

Prompts, response bodies, critique prose, repair plans, reasoning, and credentials remain transient.
Artifacts retain only the existing bounded hashes, parsed accepted fields, closed statuses,
deterministic reports, scores, tokens, calls, and latency. Mnemo remains a local-first context
platform: this evaluation does not proxy, replace, or modify an agent's configured model endpoint,
and no model output can authorize or directly persist a mutation.
