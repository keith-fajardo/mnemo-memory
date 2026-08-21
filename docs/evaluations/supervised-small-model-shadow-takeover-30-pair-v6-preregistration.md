# Takeover v6 30-pair final evaluation preregistration

Status: frozen before any model output from this final run.

Run ID: `final-20260820-ministral8b-gpt56sol-codexcli-takeover-v6-30pair-001`.

This final synthetic evaluation reuses the corrected v6 treatment that completed immutable
engineering triplet
`dry-20260820-ministral8b-gpt56sol-codexcli-takeover-v6-delta-002`. It changes no prompt, model,
route, deterministic check, scorer, or acceptance threshold. `SS` remains local-first Ministral
execution with one direct-frontier takeover only after the local proposal fails deterministic
checks; `SD` remains direct Ministral and `TD` remains direct frontier. A takeover remains an
untrusted proposal and must pass the same deterministic checks before the trajectory continues.

## Frozen objective and population

The run uses all 30 original synthetic variants and all three conditions under `run_role=final`.
One variant is the independence unit. The active router objective is `ACHIEVED` only when all 30
matched `SS`/`SD`/`TD` triplets are available, every `SS` trajectory succeeds, every `SS`
trajectory has zero critical false memories, matched mean `SS` hidden-test accuracy is no more than
0.02 below `TD`, and matched `SS` frontier input-plus-output tokens are at least 30% below `TD`.
Any missing triplet produces `NOT_EVALUATED`; a complete population missing any gate produces
`NOT_ACHIEVED`.

The older supervision research verdict remains separately reported and may impose its additional
`SS - SD >= +0.10` requirement. It does not redefine or override the active router objective.
Local executor tokens remain visible but do not enter the primary ChatGPT-subscription-capacity
comparison.

## Frozen cumulative safety bounds

The corrected engineering triplet used one `SS` takeover and three `TD` calls. It reported 15,376
`SS` and 47,415 `TD` frontier tokens, or 62,791 combined tokens across four calls. The direct linear
30-variant projection is 1,883,730 frontier tokens. The final 3,000,000-token ceiling is fixed about
59.3% above that projection.

Each `SS` session can make at most one takeover and each `TD` session makes one direct call, so the
absolute 30-variant call ceiling is 180. These are cumulative fail-closed ceilings, not usage
targets or a promise of subscription capacity. A response can cross the reported-token ceiling
before its usage is validated. The run is nonresumable so a restart cannot reset process-local
counters.

## Authorization, artifacts, and claim boundary

The canonical fixture keeps `live_calls_authorized` false. Only an ephemeral copy may enable this
specific run after the complete repository gate passes. The result directory is exclusive, and any
retry requires a new immutable ID. Prompts, responses, critique, repair steps, reasoning, and
credentials remain transient. Artifacts retain only bounded hashes, parsed accepted fields,
deterministic reports, route metadata, calls, token counters, scores, and latency.

Even an `ACHIEVED` result establishes only this synthetic shadow objective. It does not establish
production safety, general task routing quality, customer value, or permission to alter any
agent's configured model endpoint. No production route, deployment, or release is included.
