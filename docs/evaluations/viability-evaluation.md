# Lifecycle viability evaluation

This evaluation asks whether Mnemo improves long-horizon continuation while reducing total
lifecycle token consumption. It evaluates semantic checkpointing as a system, not checkpoint size
as an isolated output metric.

## Evaluated architecture and revision

The first run evaluates the worktree based on commit
`bef8bb312aab386bb17ab0698af399e0b69584b1` plus the uncommitted semantic checkpoint vertical slice.
That slice reuses immutable task-event envelopes and canonical evidence references, compiles typed
semantic atoms through deterministic patches, persists personal SQLite deltas and snapshots, and
renders compact, portable, and audit views. The viability harness does not alter the MCP surface,
model endpoint, persistence schema, installation, or deployed release.

## Conditions

| ID | Condition | Offline implementation |
|---|---|---|
| B0 | Full usable history | Every exact-scope event, chronologically rendered |
| B1 | Sliding window | Newest whole events within 600 estimated tokens |
| B2 | Rolling summary | Whole natural-language bullets within 200 estimated tokens |
| B3 | Provider-native compaction | Unavailable unless a real provider adapter and budget exist |
| M1 | Mnemo compact | Semantic rendering with a 200-token target and ceiling |
| M2 | Mnemo adaptive | 200-token first pass; explicit evidence tasks may expand to 600 |
| M3 | Mnemo plus retrieval | M2 plus bounded lexical expansion from canonical events |

B2 is an original, transparent deterministic proxy. It is not presented as model-generated or
provider-native compaction. B3 remains unavailable rather than being simulated.

## Evidence classes

- **Actually observed:** executed wall-clock/process bookkeeping such as local latency, run counts,
  call counts, exclusions, and incurred external spend.
- **Deterministically measured:** byte-, fixture-, evidence-, or artifact-derived fidelity and
  integrity results.
- **Model-generated:** actual input/output from an executed model condition; the offline baseline
  has none.
- **Estimated:** named lexical token counts, lifecycle TES, break-even reuse, and disclosed economic
  sensitivity assumptions.
- **Proxy:** information-availability and composite metrics such as task-success proxy, Task Impact,
  LME, and MVS.
- **Simulated:** counterfactual retry/repair token equivalents, reuse scaling, and synthetic scenario
  repetition.
- **Not evaluated:** live task success, provider billing, blinded quality, market demand, and
  production portability when no authorized observation exists.

The raw token account retains its lower-level source value (`provider_reported`,
`tokenizer_estimate`, `offline_proxy`, or `not_available`). The aggregate metric catalog applies the
seven report classifications above without upgrading an estimate or proxy into an observation.

The default run uses `mnemo/conservative-lexical-v1`. It records local text-processing equivalents
for save input/output and validation even though deterministic local code is not provider-billed.
This conservative accounting prevents local overhead from disappearing from TES. Monetary
efficiency remains separate.

## Corpus and experimental controls

`viability-corpus-v1` contains six original synthetic realistic workflow templates expanded across
15-, 75-, and 225-event horizons. The six templates are the primary independence units. Three reuse
counts produce 54 paired deterministic rows per available condition, but those rows are repeated
measurements within the six scenario-family clusters. Paired and condition-level confidence
intervals resample whole scenario families. A category containing only one template reports its
interval as not estimable. Condition order rotates deterministically. Each adapter receives a fresh
exact-scope store, identical task prompt, counter, and grader rubric. Ground-truth keys, expected
answers, and fixture filenames are checked for leakage.

The deterministic continuation grader is condition-blind: its interface receives context,
evidence associations, ground truth, token counts, and integrity metadata, but no condition ID. It
performs byte-exact checks and yields an explicitly labelled task-success availability proxy. An
authorized external grader can implement the same structured interface; exact checks remain
authoritative.

## Formulas

The harness implements the specified lifecycle token totals, TES, compression diagnostic,
break-even reuse, gated and ungated LME, TI, economic sensitivity, and MVS. TE is normalized as
`clamp(TES, 0, 1)`: no or negative token saving has zero token-efficiency utility. MVS reports only
an observed-dimension geometric score when EV or MP is missing; it never imputes a favorable score.
Any critical memory violation zeros gated LME for that run and closes the production gate.

Paired means, medians, sample standard deviation, P10/P90, deterministic scenario-family-clustered
bootstrap 95% confidence intervals, failure counts, paired deltas, five-point non-inferiority proxy,
and a multi-objective Pareto frontier are emitted. Offline proxy non-inferiority cannot pass the
production live-quality threshold.

## Evidence-integrity correction

The corrected append-only run `offline-20260812-57ec69f-integrity-001` resolves two presentation
ambiguities without changing fixtures or thresholds:

- The reported `+0.701` is the paired mean **task-success availability proxy** delta: M1 `1.000`
  minus B2 `0.299`. The separately displayed **continuation-fidelity** means are M1 `0.810` and B2
  `0.299`, whose difference is `0.511`.
- The reported `-209.6%` is the median of 54 paired lifecycle-TES ratios. The ratio of the displayed
  marginal medians is separately `1 - 42,681 / 13,615.5 = -213.5%`. Neither summary is substituted
  for the other.

The threshold table now emits only `PASS`, `FAIL`, or `NOT EVALUATED`. Its offline result is four
passes, one failure, and three not-evaluated thresholds; unmeasured live quality, cost per successful
task, and market demand are not counted as empirical failures.

## External authorization

The checked-in configuration allows zero external calls and zero external cost. A credential or
installed CLI alone is not authorization. Live execution requires all of:

1. `live_evaluation_enabled=true`;
2. a positive suite, run, and call-count budget;
3. a real provider adapter and model configuration;
4. an estimated maximum cost within every budget boundary.

The budget ledger reserves before a call and stops safely on exhaustion. The first desired live
study needs at least 180 generation calls: B0, B2, and M3 × 30 paired runs × two model families,
before optional grader duplication. No dollar estimate is safe until model-specific pricing and
per-call limits are approved.

## Run

```bash
npm run eval:viability -- --run-id offline-20260812-bef8bb3-001
uv run pytest -q tests/unit/test_viability_evaluation.py \
  tests/evals/test_lifecycle_viability_evaluation.py
```

Every run gets a new directory under `evaluation-results/viability-v1/`. The writer uses exclusive
creation and refuses to overwrite an earlier identity. Raw JSONL, flattened CSV, aggregate JSON,
configuration, environment, human-review template, charts, report, and a content-hashed
reproducibility manifest are retained.

## Human and model semantic rubric

Reviewers score continuation on the existing 0–4 rubric:

- 0: unsafe, out of scope, or materially wrong;
- 1: starts but misses essential state;
- 2: material omissions or repeated work;
- 3: correct with minor inefficiency;
- 4: correct, scoped, evidence-backed, with no material rework.

Every score requires cited output evidence, a critical-error flag, reviewer ID, and time. A blind
sample must be independently double-rated before using a model grader for primary claims. Report
exact agreement and a chance-corrected agreement statistic where sample size permits.

## Known limitations

Synthetic explicit prefixes are cleaner than real agent traces. The evaluation measures
information availability rather than generated task completion. It has no exact provider token
counts, cached/reasoning usage, invoices, cross-model portability, human grader agreement, design
partners, pilots, retention, or willingness-to-pay evidence. Its correct initial verdict is
therefore `INSUFFICIENT EVIDENCE`, regardless of compression.
