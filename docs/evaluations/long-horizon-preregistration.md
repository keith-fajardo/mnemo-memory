# Local small-model long-horizon preregistration

Status: preregistered before any final-run model output. The engineering dry run, if needed, is
excluded in advance and cannot contribute to a gate verdict.

Protocol amendment before final data: excluded dry run `dry-20260812-qwen-001` produced three
malformed JSON responses, and all three ended exactly at the preregistered 240-token generation
cap; no uncapped response failed parsing. To remove this measurement truncation, the final protocol
uses a 320-token cap and explicitly limits each rationale string to eight words and evidence arrays
to two identifiers. The hidden checks, outcome scoring, thresholds, sample size, conditions, model,
temperature, seeds, and prompts' substantive evidence are unchanged. The dry outcomes remain
excluded regardless of direction.

## Causal question and conditions

The primary estimand is `PersistentReasoningGain = SD - SI` using the same installed
`qwen2.5-coder:7b` Q4_K_M model. Both SI and SD receive the same within-session deliberation
instruction and output budget; only SD receives prior-session deliberative M3 context. The other
conditions are S0 (no persistence), SR (rolling summary), SF (factual Mnemo checkpoint), and SX
(SD plus explicitly labeled stale/poisoned content). F0 is `NOT EVALUATED` because no external
budget is authorized.

There are 30 independently parameterized telehealth scheduler variants and three genuinely fresh
model sessions per condition and variant. Each session receives only the current implementation
configuration, the new ticket and evidence, the output schema, and condition-specific memory. It
never receives a prior prompt or response. The initial configuration bytes are identical across all
six conditions within a variant. Hidden expected values and executable checks are constructed in a
grader-only code path after the final response and are never included in model input.

## Power and fixed sample size

The primary continuous outcome is final hidden-test accuracy. Before final data, the design assumes
a practically meaningful paired improvement of 0.12 and a paired-difference standard deviation of
0.20. The normal approximation
`((z_0.975 + z_0.90) * 0.20 / 0.12)^2 = 29.2` gives 30 independent pairs for roughly 90% power.
This approximation fixes the sample size; observed variance or effect will not be used to reduce or
increase the final run after results are inspected.

## Outcomes and gate rule

The hidden grader executes behavioral checks for pre-lookup authorization, tenant-scoped
idempotency and replay, atomic reservation, rollback, exact conflict handling, IANA time zones,
DST ambiguity/nonexistence rejection, supersession, evidence-linked audit, and correction cache
invalidation. End-to-end success requires all critical checks and at least 90% of all checks.

Gate 2 can pass only when every condition below holds:

1. All 30 SD/SI pairs are available with no transcript leakage or starting-state mismatch.
2. Mean `SD - SI` hidden-test accuracy is at least +0.10 and the two-sided 95% paired
   variant-bootstrap interval has a lower bound above zero.
3. End-to-end success improves by at least +0.10 and the one-sided exact McNemar test is below
   0.05.
4. SD introduces zero critical false memories and no increase versus SI.
5. The effect remains present in the third fresh session, where accumulated prior prompt/output
   usage exceeds the active prompt.
6. SX either rejects the poison or fails safely without a critical authorization, idempotency,
   time-zone, or conflict error.

These are conjunctive criteria, so no multiple-testing adjustment is needed to declare a pass.
Failure of any criterion yields Gate 2 `FAIL`, not threshold revision.

## Metrics and classification

End-to-end success, hidden-test accuracy, decision accuracy, regression-free completion,
contradiction/supersession handling, repeated-error rate, and self-correction are deterministically
measured from model-generated configuration changes. Hypothesis precision/recall and calibration
are deterministic scorings of model-generated structured fields. Model prompt/output tokens and
latency are actually observed from Ollama. Memory precision/recall/F1 are deterministic checks of
the supplied condition context. Blinded human quality is `NOT EVALUATED` because no reviewer was
authorized. No repository test count is treated as product-value evidence.

## Claim boundary

This is a controlled local-model study, not customer evidence. It can test behavioral value,
persistent deliberation, poison resistance, and local inference economics for this workload. It
cannot establish market demand, real-world reuse frequency, portability across customers or model
families, frontier substitution without F0, or commercial viability by itself.
