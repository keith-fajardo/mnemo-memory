# Subscription-backed Codex CLI supervised shadow preregistration

Status: frozen before any v3 Codex model execution.

This protocol changes the gated-v2 frontier transport from the usage-billed OpenAI Responses API
to a locally installed Codex CLI authenticated through the maintainer's ChatGPT subscription. The
local executor, frontier model, medium reasoning effort, prompts, corpus, three conditions,
deterministic initial-review gate, repair loop, scoring, privacy boundary, and quality and token
thresholds remain unchanged. V1 and v2 remain immutable and keep their API-backed interpretation.

The purpose is to measure frontier-token use under the requested subscription-backed workflow. A
ChatGPT subscription can have product usage limits, but this protocol has no configured per-token
dollar rates and must not translate CLI tokens into API spend. Reports label dollar cost as not
applicable and retain frontier input and output totals. Cached input and reasoning output are
reported as non-additive breakdowns: they are subsets of the corresponding input and output totals,
not extra tokens to add again.

## CLI boundary

The fixture pins the `codex` executable, CLI version `0.147.0`, `gpt-5.6-sol`, medium reasoning,
and `chatgpt_subscription` authentication. Before constructing the callable adapter, the runner
must reject absent live authorization, any non-subscription authentication status, a mismatched CLI
version, and any non-empty `OPENAI_API_KEY` or `CODEX_API_KEY` in the supplied environment. It does
not read or persist an authentication token.

Each advisor call uses a fresh system-temporary directory and sends the synthetic prompt on standard
input. The command must be ephemeral, ignore user configuration and repository rules, use a
read-only sandbox, skip repository discovery, emit JSONL, and enforce the purpose-specific output
schema. The repository is not added as a writable or readable working directory. The environment is
reduced to a bounded operational allowlist and never forwards API-key variables.

The adapter accepts only reasoning and one final schema-constrained agent message. Any shell,
filesystem, MCP, web, plan, or other tool item fails the call. Unknown, malformed, failed, oversized,
or incomplete JSONL also fails closed. Raw JSONL, standard error, prompts, response bodies, reasoning,
thread identifiers, and authentication state remain transient.

## Operational ceilings

One one-variant engineering run permits at most nine frontier calls, 50,000 cumulative reported
frontier tokens, a 32,768-byte final response, and 120 seconds per CLI process. Call count is checked
before execution. Because Codex CLI does not expose a pre-request token cap in this protocol, the
cumulative token ceiling is checked after each reported response and can be crossed by that single
call; a crossing response is rejected and no later call is permitted. This limitation must remain
visible in any result interpretation.

No live call is authorized by this preregistration. `live_calls_authorized` remains `false`. A v3
engineering run requires separate maintainer approval, a fresh immutable run ID, and a temporary
configuration copy with explicit authorization. A 30-variant final run requires its own approval and
revised call and token ceilings.

## Product boundary

This adapter exists only in the evaluation runner. It does not proxy, replace, or modify an agent's
configured model endpoint, add a dependency, or create a production routing path. Codex output is an
untrusted proposal. Existing deterministic consistency checks remain authoritative, and model output
cannot authorize or directly persist a mutation.
