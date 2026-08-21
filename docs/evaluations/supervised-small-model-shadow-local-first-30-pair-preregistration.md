# Local-first 30-pair final evaluation preregistration

Status: frozen before any model output from this final run.

Run ID: `final-20260820-ministral8b-gpt56sol-codexcli-localfirst-30pair-001`.

This final evaluation reuses the v5 local-first treatment after the fail-closed trajectory
correction. It changes no prompt, model, route, deterministic check, scorer, quality threshold, or
savings threshold. `SS` remains local-first Ministral execution with frontier review only after a
deterministic failure; `SD` remains direct local execution and `TD` remains direct frontier
execution. Frontier planning and takeover remain disabled. An unresolved `SS` session makes that
condition unavailable before any later session can mask it.

## Frozen population and gates

The run uses all 30 original synthetic variants and all three conditions under `run_role=final`.
The independence unit is one variant. The frozen evidence minimum is 30 complete `SD`/`SS`/`TD`
triplets. Any unavailable condition reduces the paired count; the analysis must remain
`NOT_EVALUATED` when fewer than 30 complete triplets survive.

The quality gates remain: `SS - SD >= +0.10`, `SS - TD >= -0.02`, zero critical false memories,
and at least 30 complete pairs. The subscription-savings gate remains at least 30% fewer `SS`
frontier input-plus-output tokens than `TD`. Local executor tokens do not enter that primary
comparison and remain visible as secondary total-compute and latency evidence.

## Frozen safety bounds

After the fail-closed correction, an `SS` trajectory can make at most two frontier calls because
its first unresolved session stops the condition; `TD` makes three calls. The 30-variant maximum is
therefore 150 frontier calls. The completed v5 engineering variant reported 77,701 frontier tokens
over five calls. A linear 30-variant projection is 2,331,030 tokens; the fixed 3,000,000-token
ceiling provides about 29% headroom. These are cumulative fail-closed ceilings, not targets or a
claim that the ChatGPT subscription will provide that capacity. A response can cross the token
ceiling before reported usage is validated.

## Authorization and artifacts

The canonical fixture keeps `live_calls_authorized` false. Only a system-temporary copy may enable
the authorized run after the complete repository gate passes. The run is not resumable, the result
directory is exclusive, and any retry requires a new immutable ID and explicit approval. Prompts,
responses, critique, repair steps, reasoning, and credentials remain transient. Artifacts retain
only bounded hashes, parsed accepted fields, deterministic reports, closed statuses, calls, token
counters, scores, and latency. This authorization does not include a production route, deploy, or
release.
