# Lifecycle token break-even preregistration

Status: preregistered before implementation and before any lifecycle result. Thresholds, conditions,
fixture identities, horizons, and exclusions below must not change after observing an offline or live
result. An engineering dry run is excluded in advance and cannot contribute to a verdict.

## Question and claim boundary

The primary question is whether lifecycle-routed Mnemo replaces enough usable growing history to
reduce cumulative downstream-agent model tokens without losing prior-only required facts. This is a
local synthetic engineering evaluation. It cannot establish customer value, market demand,
cross-provider generality, or task-quality improvement. Mnemo makes zero model calls; all model-token
measurements belong to the downstream agent condition.

This evaluation does not alter the agent's model endpoint. Prompts and responses exist transiently
only for an authorized provider request. Mnemo and result artifacts never persist prompts, responses,
tool bodies, or model reasoning.

## Frozen conditions

- FH — full usable growing history. The request receives all prior public task facts and bounded
  deterministic result summaries needed to expose the protected prior fact. No hidden grader data is
  available.
- RS — deterministic rolling summary. This transparent Mnemo-owned baseline is not provider-native
  compaction and is never labelled as such.
- NM — current-session input only. It receives no durable history and no current state, filename,
  enum, hint, or metadata that carries the protected prior fact.
- MR — lifecycle-routed Mnemo. It invokes the real SessionStart and UserPromptSubmit hook composition,
  including exact redelivery suppression, and receives only the context the hook emits.

The four conditions receive identical current public task facts, instructions, lifecycle schedules,
and starting state. Only prior-history transport differs. Condition labels are absent from model
prompts and deterministic grader inputs.

## Corpus, horizons, and lifecycle

The schedule references the six original synthetic scenario families in
`tests/fixtures/evals/viability-corpus-v1.json` by template and event key. It copies no scenario truth.
The scenario family is the independence unit. Sessions and horizon observations are repeated measures
inside a family.

Frozen cumulative horizons are 1, 10, and 30 sessions. Each opaque client session begins with
SessionStart and contains at most four UserPromptSubmit events: one self-contained request, one
prior-memory request, an exact repeat, and at selected horizons one changed-memory request.
PreCompact occurs after sessions 10 and 20. Session 1 seeds the prior fact and is not a scored reuse;
later scored requests must obtain that fact from FH or MR history transport.

## Memory-necessity and contamination gate

Before token accounting, a deterministic validator resolves the referenced source event and proves
that its protected meaning is absent from the reuse prompt, current implementation state, allowed
values, filenames, hashes, lifecycle labels, and grader metadata. It also proves that FH and MR expose
the same required prior-fact set. Deterministic availability must be FH = 1.0, MR = 1.0, and NM = 0.0.
Any mismatch or current-input leakage makes the scenario or run `INVALID`; it is never scored as
savings evidence.

## Frozen estimands and gates

The primary long-horizon estimand is cumulative total downstream-model token savings:

`1 - MR_total_model_tokens / FH_total_model_tokens`

`total_model_tokens` is the sum of actual provider-reported input and output tokens for included,
paired calls. The analysis also reports model-input-only savings, delivered context tokens, duplicate
tokens avoided, and the earliest sampled horizon with non-negative cumulative savings. It never
subtracts local work or memory tokens after the fact.

A final `PASS` requires all of:

1. At the 30-session horizon, paired cumulative MR-versus-FH total-model-token savings are at least 30%.
2. Required-knowledge retention and protected-literal fidelity are 1.0 in FH and MR; deterministic
   NM availability is 0.0.
3. Critical false-memory count and cross-scope disclosure count are both zero.
4. Actual provider token counts exist for every included FH/MR pair, with explicit orphan/exclusion
   accounting.
5. If a separately authorized task-generating comparison runs, MR hidden-test accuracy is no worse
   than FH by more than 0.02. Without that comparison, task-quality impact is `NOT EVALUATED` and
   cannot be inferred from deterministic availability.

Tokenizer-only evidence can yield at most `PROVISIONAL`. Missing offline and live evidence yields
`NOT EVALUATED`. A failed gate remains a failure; thresholds are not revised after results.

## Statistical interpretation

All comparisons are paired by scenario family and horizon. Six scenario-family clusters are too few
for reliable population inference. Paired family bootstrap intervals, when estimable, are descriptive
sensitivity summaries only. The report makes no significance claim and does not generalize beyond
the six preregistered families. Sessions, prompts, and repeated retrievals are never treated as
independent observations.

## Artifact and privacy contract

Every run directory uses exclusive immutable creation. Raw rows are append-and-fsync records keyed by
condition, scenario family, horizon, and attempt; completed keys support resume without rewriting.
The reproducibility manifest hashes the fixture, source corpus, runner, configuration, and artifacts
and records included, excluded, failed, and orphaned provider calls.

Raw artifacts may contain only:

- schema/run/condition/scenario/horizon identifiers;
- lifecycle boundary and content-free prompt SHA-256;
- actual or estimated input/output token counts and latency;
- route action, delivered/suppressed counts, and duplicate flag;
- deterministic availability, fidelity, false-memory, disclosure, and quality grades;
- model/runtime/environment provenance and artifact hashes.

Unique sentinel tests must prove that transient prompts, responses, protected source text, source
summaries, tool input/output, and reasoning occur nowhere in the run directory, logs, exceptions, or
manifest.

## Offline and live authorization boundary

Offline execution uses the repository's conservative lexical token counter and zero external calls.
It measures construction, lifecycle behavior, leakage, fidelity, and estimated break-even. It cannot
produce a final provider-token verdict.

Live calibration requires separate explicit authorization. The bounded primary configuration uses
loopback Ollama with exact `qwen3:14b` identity, non-thinking mode, temperature 0, and at most eight
generated tokens. It makes 36 primary calls plus one preflight smoke call: six families multiplied by
FH/MR and the three frozen horizons. It stops after 90 minutes. The 30B/32B rungs are excluded on the
24 GB M4 Air. A model download or task-generating quality run requires separate authorization.

## Originality

This schedule and preregistration are original Mnemo evaluation artifacts. They reference existing
Mnemo-owned synthetic event keys and introduce no competing-product prompts, schemas, fixtures, code,
or dependencies. Contributors remain responsible for originality, privacy, licensing, and review.
