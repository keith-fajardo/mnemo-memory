# Mnemo live semantic-memory value investigation

Status: complete; terminal evidence and repository gate verified.
Decision under the preregistered rules: **STOP**.

The complete immutable evidence package is
`evaluation-results/final-investigation-v1/investigation-20260812-final-002`. It contains the
executive report, gate table, architecture trace, before/after lifecycle breakdown, statistical
analysis, raw-run evidence index and hashes, environment/model/configuration, failure and exclusion
log, metric classifications, economics, and exact reproduction instructions.

## Gate verdicts

| Gate | Verdict | Evidence boundary |
|---|---|---|
| Gate 1: live-path memory integrity | FAIL | The real public-save/fresh-SessionStart transport had 100% deterministic critical fidelity and zero false memories, but the real Qwen continuation satisfied only 4/7 fixed critical response requirements. |
| Gate 2: long-horizon behavioral value | FAIL | Thirty paired variants completed. SD minus SI accuracy was +0.0311 (95% paired bootstrap CI +0.0156 to +0.0489), below the preregistered +0.10 margin. Both arms achieved 0/30 end-to-end successes; McNemar p=1.0. Poison resistance also failed. |
| Gate 3: tested-segment economics | FAIL | SD used 99,237 actual model tokens versus SI's 69,068, for `LifecycleTES = -43.7%`; neither arm succeeded, so cost per successful task is not estimable. |
| Frontier substitution | NOT EVALUATED | F0 calls were not authorized. |
| Blinded human quality | NOT EVALUATED | No reviewer was authorized. |
| Market demand and portability | NOT EVALUATED | No pilots, customer traces, demand evidence, or second model family were available. |

## What the evidence establishes

Engineering correctness and deterministic memory transport materially improved. The experimental
feature-off default remains unchanged. With the flag enabled, real public checkpoints project into
the semantic ledger, exact scoped evidence reaches a genuinely fresh SessionStart, cross-scope and
superseded content is excluded, and deletion propagates through Mnemo-controlled projections. The
live trace achieved 100% protected-span, hard-constraint, evidence, supersession, and scope
fidelity, with zero critical false memories.

Three bounded lifecycle optimizations reduced actually observed deterministic semantic wall work
by 27.0% (95% CI 26.0% to 28.0%), save work by 15.1%, and automatic-context work by 49.0%.
Rendered checkpoint content stayed at 399 tokens and the evidence-bearing automatic item at 505
tokens. These are CPU/latency reductions, not model-token savings.

The behavioral result is narrower and negative against the product claim. SD's statistically
supported +3.1-point hidden-check improvement over SI was not practically meaningful under the
fixed +10-point rule and produced no end-to-end successes. SD memory had precision 1.0, recall
0.875, F1 0.933, and zero critical false memories, but accurate transport did not become reliable
task completion. Fourteen of 30 SX trajectories violated the fixed poison safe-failure rule. The
study therefore does not establish persistent deliberation, context-rot mitigation, or frontier
substitution.

## Actual lifecycle resources

The six conditions produced 540 analyzed local-model calls. A per-process file-descriptor leak
interrupted the runner after 170 trajectories; exact resume skipped completed keys and finished the
fixed sample. The two orphaned pre-interruption calls are excluded from causal effects but retained
as actually observed operational failure cost: 1,934 tokens and 53.59 seconds. In total, the raw
session log contains 542 calls, 440,728 input tokens, 70,170 output tokens, and 18,533.92 seconds of
local request latency. External spend and human interventions were actually observed as zero.
Hardware, energy, and labor dollar rates were not authorized and are `NOT EVALUATED`.

The interruption identified a production storage root cause: Python's SQLite connection context
manager commits or rolls back but does not close the connection. Mnemo's five semantic read paths
now use explicit closing, and a regression proves the descriptor is closed after a read. This
reliability fix does not change or regenerate the frozen model results.

Higher reuse levels are estimates only. The study observed two recalls per trajectory; it supplies
no customer evidence that three, ten, or thirty reuses are plausible. Project architecture memory,
procedures, runbooks, multi-agent shared knowledge, short/medium horizons, and real-world reuse
frequency remain `NOT EVALUATED`.

## Evidence interpretation

Model responses are model-generated. Ollama token counts and latency are actually observed.
Hidden checks, memory metrics, effect sizes, confidence intervals, McNemar results, and TES are
deterministically measured. Old offline lifecycle token totals are estimated counterfactuals;
offline availability results are proxies over simulated condition rows. Unmeasured evidence is
`NOT EVALUATED`, never counted as a pass or empirical failure.

The earlier report discrepancy is resolved: `+0.701` is the M1-minus-B2 task-success availability
proxy, whereas displayed continuation-fidelity means differ by `+0.511`. Likewise, `-209.6%` is
the median of 54 paired TES ratios, while the ratio of displayed condition medians is `-213.5%`.
The six scenario families, not repeated deterministic rows, are the offline independence units.

## Decision

The preregistered decision rule requires `STOP` after an adequately powered live experiment shows
no practically meaningful behavioral gain. That result applies to the tested three-session local
Qwen telehealth-scheduler segment. It does not prove that every memory type or model will fail, but
it does reject investment claims based on this M3 path as persistent reasoning, context-rot
mitigation, economic substitution, or commercial viability without new, independently justified
evidence.

Repository verification completed with 1,097 tests passed and 27 expected skips; the dedicated
PostgreSQL gate passed 26 tests with one opt-in load test skipped. Formatting, lint, strict typing,
schema, dependency/provenance, architecture, and installed-package checks also passed.
