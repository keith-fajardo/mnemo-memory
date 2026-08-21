# Deterministic hybrid executor routing preregistration

Status: frozen before implementation and before any v4 model output.

This evaluation-only protocol adds a deterministic preflight branch to the frozen v3
subscription-backed executor/advisor protocol. It does not create a production model route or
proxy an agent's configured endpoint. The same synthetic corpus, local Ministral executor,
subscription-backed GPT-5.6 Sol advisor, `SD`/`SS`/`TD` conditions, scoring, privacy rules, and
quality and token thresholds remain fixed.

## Selected treatment

The `SS` condition receives one of two routes before generation. The route uses only the frozen
session risk tags and strict user-selected mode in `hybrid_routing`; neither model chooses its own
route and model confidence has no routing authority.

- `local_first` always uses the existing v3 local-first deterministic-failure gate.
- `frontier_plan_first` always requests a bounded frontier plan before local execution.
- `hybrid` requests a plan only when a frozen session tag intersects `plan_first_tags`; otherwise
  it uses the existing local-first path.

The frozen hybrid treatment tags session 1 as `authorization`, session 2 as
`bounded_mechanical`, and session 3 as `migration`. It sends sessions 1 and 3 through plan-first
and session 2 through local-first. These tags are trusted evaluator controls, not classifications
inferred from untrusted ticket text.

## Plan-first loop

The frontier plan contains one to eight concise steps, one to eight acceptance checks, and bounded
uncertainty. It is an untrusted proposal. A malformed plan fails before the local executor runs.
For a valid plan, Ministral receives the original task plus the plan, then deterministic schema,
value, memory-consistency, and structured-support checks run. A frontier review is mandatory even
when those checks pass. A `repair` permits one local repair and one final frontier review; only a
final `pass` plus the deterministic checks may accept the candidate. Malformed, `escalate`, second
`repair`, mismatched, or unverifiable output fails closed. Frontier takeover is frozen off.

Each plan-first session therefore uses two frontier calls when it passes immediately and at most
three when one repair occurs. The complete one-variant v4 ceiling is twelve frontier calls: nine
for three worst-case plan-first `SS` sessions plus three direct-frontier `TD` sessions. The
cumulative reported-token ceiling is 75,000. These are safety ceilings, not expected usage.

## Privacy, accounting, and claims

Plan text, critique, repair prose, prompts, responses, reasoning, and credentials remain transient.
Artifacts may retain only fixed routing decisions and reason codes, prompt hashes, parsed accepted
fields, closed review statuses, deterministic reports, tokens, calls, scores, and latency. Only
verified field-name correction markers may reach Mnemo. Subscription usage is reported as frontier
tokens and calls, not API-dollar spend.

The existing 30-pair thresholds remain unchanged. A one-variant engineering run cannot pass them
and is `NOT_EVALUATED`. No live call is authorized by this preregistration:
`live_calls_authorized` remains `false`. Any engineering or final run requires separate explicit
approval, a fresh immutable run ID, and temporary authorization.
