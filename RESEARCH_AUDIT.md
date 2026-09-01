# Research Audit

## Verdict

**Claim present but evidence insufficient.** The repository states a separable scientific claim—that the benefit of injected memory is an inverted-U function of model capability—but the only completed behavioral evidence uses one quantized 7.6B Qwen model, one synthetic telehealth-configuration template, and three one-turn sessions per trajectory. The completed result supports the narrow statement that this model/serving/prompt/task combination obtained a small SD-over-SI hidden-field gain and zero end-to-end successes; it does not identify a capability boundary, an inverted U, or a general limit on small models. The result called “near-perfect memory” is not an oracle-memory experiment: its F1 is computed from four literal string probes, and no oracle condition exists. (`docs/superpowers/plans/2026-08-15-small-model-long-horizon.md:200-219`; `docs/evaluations/long-horizon-preregistration.md:14-28`; `evaluation-results/long-horizon-v1/final-20260812-qwen30-001/reproducibility-manifest.json:24-38`; `evaluation-results/long-horizon-v1/final-20260812-qwen30-001/analysis.json:25-43,65-83,174-209`; `scripts/run_long_horizon_evaluation.py:677-693`)

## Inventory (Phase 1)

### Repository map

| Path | Apparent purpose | Evidence |
|---|---|---|
| `src/mnemo_memory/packages/domain` | Pure scope, evidence, memory, checkpoint, and context contracts. | `AGENTS.md:14-20` |
| `src/mnemo_memory/packages/application` | Lifecycle services, local configuration, semantic-memory orchestration, and composition. | `AGENTS.md:18-20`; `docs/semantic-checkpoints.md:92-102` |
| `src/mnemo_memory/packages/policy`, `storage` | Deterministic authorization/retention/mutation rules and repository interfaces. | `AGENTS.md:20-22,39-44` |
| `src/mnemo_memory/packages/episodic`, `knowledge`, `project_index`, `skills_registry` | Episodic memory, project/personal knowledge, rebuildable dbt/source projections, and procedural-memory registry. | `AGENTS.md:22-25,41-43` |
| `src/mnemo_memory/packages/model_gateway`, `telemetry`, `context_engine` | Optional model boundary, content-free observability contracts, and authorization-first context selection/ranking/budgeting. | `AGENTS.md:26-28,39-40` |
| `src/mnemo_memory/connectors`, `apps` | External/client adapters and CLI/MCP composition/transport roots. | `AGENTS.md:29-30` |
| `src/mnemo_memory/resources` | SQLite/PostgreSQL migrations and bundled web/static resources; the semantic schema is described as personal-SQLite-only and forward-only. | `docs/semantic-checkpoints.md:147-159` |
| `tests`, `tests/fixtures/evals` | Unit, integration, security, architecture, and synthetic evaluation fixtures. The aggregate test command is `uv run pytest`. | `package.json:10-28`; `tests/evals/test_golden_workflows.py:5-17` |
| `scripts` | Deterministic benchmarks, live Ollama evaluation runners, viability/report aggregation, dependency/architecture checks, and PostgreSQL load tooling. | `package.json:16-28` |
| `docs` | Product contract, architecture, security, ADRs, benchmark descriptions, evaluation plans, and implementation status. | `README.md:331-348` |
| `evaluation-results` | Local raw/results packages for viability, live semantic transport, lifecycle timing, long-horizon behavior, and terminal decision aggregation. It is ignored by Git. | `.gitignore:19-31`; `docs/evaluations/memory-value-investigation.md:6-10` |
| `deploy` | Team deployment material included in the source distribution. | `pyproject.toml:73-80` |

### Memory-system architecture

The stored system has three layers: immutable exact-task event envelopes with bounded summaries and evidence references; a typed semantic-atom ledger containing goal/fact/state/decision/constraint/preference/question/action/result/failure/inference atoms and their attribution, confidence, lifecycle, sources, and supersession; and checkpoint metadata/renderings that select active atoms and expose compact, portable, or audit views. (`docs/semantic-checkpoints.md:9-27`)

Writes enter as evidence-backed task events, are compiled by a deterministic prefix-aware compiler into closed patch operations, and are validated for exact scope, source existence, lifecycle transitions, and immutable meaning before a transactional SQLite write. A later explicit goal or decision supersedes the previous active atom without deleting its audit record. (`docs/semantic-checkpoints.md:29-47`)

Retrieval requires exact scope plus query/task text and token targets. It ranks active atoms inside safety bands, marks goals/constraints/decisions/questions/actions/authority/critical uncertainty mandatory, and admits or omits optional atoms only as whole units. The retrieval unit is therefore an active semantic atom, not a token span or transcript chunk. (`docs/semantic-checkpoints.md:67-90`)

The public experimental path projects an accepted checkpoint revision into semantic events, then a fresh-session exact-scope read may replace one legacy checkpoint item with a bounded semantic rendering; errors or over-budget state fall back to the legacy item. (`docs/semantic-checkpoints.md:98-118`)

### Evaluation and benchmark inventory

The evidence-state labels below mean exactly: **implemented and executed** = code plus a saved report/result asserting execution; **implemented, not evidenced** = code/fixture but no saved execution result; **absent** = no implementation found in the named search scope.

| Harness | Task and data | Metric and effective N | Evidence state |
|---|---|---|---|
| Golden-workflow specification | At least 10 synthetic workflows and 50 questions across episodic, knowledge, procedural, and project categories. | Fixture schema/coverage only; it explicitly does not run retrieval or a model. | **Implemented and executed as a specification check**, not as a performance evaluation. `tests/evals/test_golden_workflows.py:24-33`; `docs/evaluation-baseline.md:3-13,97-118` |
| Fresh-session resumption | One synthetic coding handoff under no memory, full transcript, and Mnemo checkpoint. | Exact fact/provenance availability and heuristic `ceil(chars/3)` tokens; one fixture. | **Implemented and executed**; the saved documentation reports 2,948 full-transcript vs 499 Mnemo estimated context tokens and 100% required facts. `docs/fresh-session-resumption-benchmark.md:18-59`; `README.md:61-79` |
| Cross-client transport | Isolated Codex-to-Claude, Claude-to-Codex, and revised Claude-to-Codex checkpoint transport using the same synthetic handoff. | Required-fact/provenance availability, scope exclusion, stale revision and launcher/profile failure behavior; no model. | **Implemented and executed** according to the saved completion audit. Standalone result artifact: **NOT FOUND** under `evaluation-results` or elsewhere in `docs`; only the saved audit description exists. `docs/fresh-session-resumption-benchmark.md:77-102` |
| Unified context | One checkpoint fixture plus one dbt manifest fixture. | Heuristic token counts; exact historical recall, dbt precision/recall, provenance, scope, currentness, and budgets. | **Implemented and executed**; 6,379 full synthetic context vs 1,426 Mnemo estimated tokens is saved in documentation. `docs/unified-context-benchmark.md:15-68`; `docs/implementation-status.md:382-390` |
| Automatic-context routing | 60 original synthetic prompts, balanced 15 each across prior memory, knowledge, structure, and none. | Accuracy >=0.80, prior-memory recall >=0.90, structure recall >=0.80, none precision 1.0. | **Implemented, not evidenced**: assertions and fixture exist. Persisted result artifact: **NOT FOUND** under `evaluation-results`, `docs`, or the test tree. `tests/evals/test_automatic_context_routing.py:15-57`; `tests/fixtures/evals/automatic-context-routing-v1.json:1-8,57-72` |
| Semantic-checkpoint held-out evaluation | 12 synthetic event cases and three local deterministic token counters. | Exact fidelity, protected spans, provenance, omissions, false memories, supersession, drift, and compression. | **Implemented and executed** by the repository's saved status; it reports 100% selected integrity metrics and zero false-memory probes. Raw per-case result artifact: **NOT FOUND** under `evaluation-results`, `docs`, or the test tree. `tests/evals/test_semantic_checkpoint_evaluation.py:105-210`; `docs/implementation-status.md:5234-5245` |
| Wrapper phase accounting | One fake dbt invocation with injected timestamps. | Pre-hook/child/post-hook milliseconds and four boolean gates. | **Implemented, not independently evidenced**: the values are generated from an injected clock and explicitly are not hardware performance. Persisted execution artifact: **NOT FOUND** under `evaluation-results` or `docs`. `scripts/run_wrapper_overhead_benchmark.py:1-6,84-130`; `docs/command-wrapper.md:65-75` |
| Offline viability | Six synthetic scenario families expanded to 15/75/225 events and three reuse levels; 54 deterministic rows per available condition, 324 rows total. | Availability/fidelity/integrity, estimated lifecycle tokens/TES, proxies, cluster-bootstrap intervals over six families, and economic sensitivity. No model calls. | **Implemented and executed** in five saved run directories; the corrected run labels its verdict `INSUFFICIENT EVIDENCE`. `docs/evaluations/viability-evaluation.md:57-85,118-151`; `evaluation-results/viability-v1/offline-20260812-57ec69f-integrity-001/report.md:8-29,48-65` |
| Live semantic Gate 1 | One checkpoint/revision/deletion trace and one fresh Qwen continuation. | Exact transport fidelity, false-memory count, seven continuation requirements, and actual Ollama token/time counts. | **Implemented and executed once successfully**, plus one excluded timeout run. The successful continuation scored 4/7 (`0.571`). `tests/fixtures/evals/live-semantic-gate-v1.json:11-107`; `evaluation-results/live-semantic-v1/live-20260812-57ec69f-gate1-002/report.md:1-31` |
| Semantic lifecycle timing | 30 fresh SQLite profiles, each with create, revise, and two recalls; four implementation stages plus one paired comparison. | Actual local elapsed/process CPU by operation; 10,000 paired profile bootstraps. Model counts are reused from the single Gate 1 call, not 30 model executions. | **Implemented and executed**. `scripts/run_semantic_lifecycle_benchmark.py:245-317`; `evaluation-results/semantic-lifecycle-v1/comparison-20260812-baseline-vs-final-001/report.md:1-16` |
| Long-horizon behavior | 30 parameterized variants of one telehealth-scheduler template, six conditions, and three stateless model calls per trajectory. | Final equality-check accuracy over 15 fields, end-to-end all-critical/>=0.9 success, memory literal scores, actual Ollama usage, paired bootstrap, Cohen's dz, and exact McNemar. | **Implemented and executed** for Qwen2.5-Coder-7B Q4_K_M: 180 analyzed trajectories/540 calls plus two orphan calls. `docs/evaluations/long-horizon-preregistration.md:14-70`; `docs/implementation-status.md:5562-5581` |
| Phase 2 capability ladder | The same 30 telehealth variants, now nine conditions (`S0`, `SI`, `SR`, `SF`, `SD`, `SX`, `SF-fixed`, `SFp`, `SV`) for Qwen2.5-Coder-7B and Qwen3-14B with non-thinking and two-phase-thinking modes. | Same metrics plus exact-value integrity, verifier gain, and deterministic ceiling diagnostics; planned N is 270 trajectories per model/mode. | **7B final run in progress; 14B implemented, not evidenced.** No completed Phase 2 final analysis/report/manifest and no 14B result directory were found. `evaluation-results/long-horizon-v1/final-20260816-qwen25coder7b-phase2-001/evaluation-config.json:59-110,182-183`; `tests/fixtures/evals/telehealth-long-horizon-phase2-qwen3-14b.json:1-24`; `tests/fixtures/evals/telehealth-long-horizon-phase2-qwen3-14b-thinking.json:1-24`; `docs/implementation-status.md:5753-5769` |
| PostgreSQL team load | 160 authenticated scoped `get_context` reads with eight workers after eight warm-ups. | p95 latency, throughput, and errors. | **Implemented and executed** on a documented local PostgreSQL 17.10 reference environment. `tests/integration/test_postgres_team_control_plane.py:2366-2459`; `docs/team-load-slo.md:18-39` |
| Terminal investigation package | Aggregates Gate 1, lifecycle, long-horizon, economics, classifications, exclusions, and manifests. | Decision gates and evidence bookkeeping; no new independent examples. | **Implemented and executed** twice; `final-002` is the terminal package. `docs/evaluations/memory-value-investigation.md:1-21`; `evaluation-results/final-investigation-v1/investigation-20260812-final-002/executive-decision-report.md:1-33` |

### Result artifacts and dates

Dates below are the dates embedded in run IDs unless a report provides an explicit evidence timestamp.

- **2026-08-06 PostgreSQL reference:** the result is embedded in `docs/team-load-slo.md`, not under `evaluation-results`; it records four accepted local runs and the full canonical JSON for one run. (`docs/team-load-slo.md:26-39`)
- **2026-08-11 evidence timestamp / 2026-08-12 run IDs, viability:** five directories exist: `offline-20260812-bef8bb3-001`, `-002`, `-003`, `-004`, and `offline-20260812-57ec69f-integrity-001`. Each contains `aggregate.json`, `environment.json`, `evaluation-config.json`, `human-review.csv`, `per-run-metrics.csv`, `raw-runs.jsonl`, `report.md`, `reproducibility-manifest.json`, and ten SVG charts; `-003`, `-004`, and `integrity-001` also contain blind-review packets and keys. The corrected report gives evidence time `2026-08-11T20:16:15.725590+00:00`. (`docs/evaluations/viability-evaluation.md:118-129`; `evaluation-results/viability-v1/offline-20260812-57ec69f-integrity-001/report.md:1-6`)
- **2026-08-12, live semantic:** `live-...-gate1-001` contains a partial profile, failures, and raw events from the excluded timeout; `live-...-gate1-002` contains profile state/SQLite, `failures.jsonl`, `raw-events.jsonl`, `summary.json`, `report.md`, and a reproducibility manifest. (`docs/implementation-status.md:5513-5528`; `evaluation-results/live-semantic-v1/live-20260812-57ec69f-gate1-002/reproducibility-manifest.json:1-45`)
- **2026-08-12, semantic lifecycle:** four run directories—instrumented baseline, incremental delta, reuse-history delta, and evidence-reuse final—each contain failures, append-only lifecycle rows, summary, report, and manifest; one comparison directory contains `comparison.json`, `report.md`, and a manifest. (`evaluation-results/semantic-lifecycle-v1/lifecycle-20260812-57ec69f-evidence-reuse-final-001/reproducibility-manifest.json:1-33`; `evaluation-results/semantic-lifecycle-v1/comparison-20260812-baseline-vs-final-001/report.md:1-16`)
- **2026-08-12, long horizon:** two engineering dry-run directories and `final-20260812-qwen30-001` each contain config, raw session/trajectory JSONL, failures, analysis, report, and manifest. Despite its run ID, the final run used Qwen2.5-Coder-7B, not a 30B model. (`evaluation-results/long-horizon-v1/final-20260812-qwen30-001/report.md:1-12`; `evaluation-results/long-horizon-v1/final-20260812-qwen30-001/reproducibility-manifest.json:20-48`)
- **2026-08-12, terminal packages:** `investigation-20260812-final-001` and `-002` each contain 12 files: artifact manifest, economics, environment/config, evidence index, executive report, exclusion log, gate verdicts, lifecycle cost breakdown, live-path trace, metric classifications, reproduction instructions, and statistical analysis. `-002` is identified as the complete terminal package. (`docs/evaluations/memory-value-investigation.md:1-10`; `evaluation-results/final-investigation-v1/investigation-20260812-final-002/executive-decision-report.md:1-22`)
- **2026-08-16, Phase 2:** two excluded 7B dry-run directories contain completed analysis/report artifacts; the active 7B final directory contains only config, failures JSONL, and append-only session/trajectory JSONL at the audit snapshot. `analysis.json`, `report.md`, and `reproducibility-manifest.json` are **NOT FOUND** there. A search of `evaluation-results/long-horizon-v1` found no Qwen3-14B run directory. (`docs/implementation-status.md:5753-5777`; `evaluation-results/long-horizon-v1/final-20260816-qwen25coder7b-phase2-001/evaluation-config.json:59-110,182-183`)
- **Notebooks:** **NOT FOUND.** I searched repository file names with `rg --files -g '*.ipynb'`; no notebook exists. No notebook-cell evidence is therefore available.

All of `evaluation-results` is ignored, and `git ls-files evaluation-results` returns no tracked files. The raw local result packages therefore exist in this working directory but are not part of a fresh clone. (`.gitignore:19-31`; Git evidence: `git ls-files evaluation-results`, empty output)

### Models and serving configurations

- The only model with completed behavioral artifacts is `qwen2.5-coder:7b`, identified by Ollama as Qwen2 family, 7.6B parameters, Q4_K_M quantization, digest `dae161...f4364`, served on loopback by Ollama 0.32.7 with temperature 0, `num_ctx=4096`, and `num_predict=320` for the long-horizon run. (`evaluation-results/long-horizon-v1/final-20260812-qwen30-001/reproducibility-manifest.json:20-41`)
- Gate 1 used the same model/digest family and quantization, with seed 8122026, `num_ctx=4096`, and `num_predict=350`. (`tests/fixtures/evals/live-semantic-gate-v1.json:1-10`; `evaluation-results/live-semantic-v1/live-20260812-57ec69f-gate1-002/reproducibility-manifest.json:27-45`)
- Qwen3-14B is configured but not evidenced in two modes: non-thinking/single-JSON and thinking/two-phase-JSON, both temperature 0, context 4096, output cap 320, nine conditions, and 30 variants. (`tests/fixtures/evals/telehealth-long-horizon-phase2-qwen3-14b.json:1-24`; `tests/fixtures/evals/telehealth-long-horizon-phase2-qwen3-14b-thinking.json:1-24`)
- Qwen3-30B-A3B and Qwen2.5-Coder-32B/frontier are proposed ladder rungs only; the authorized local execution explicitly excludes them. (`docs/superpowers/plans/2026-08-15-small-model-long-horizon.md:200-213,236-243`)
- No OpenAI, Anthropic, Claude, frontier, or second model family has a completed behavior result. The semantic evaluation explicitly says external OpenAI/Anthropic-family validation was not run, and the terminal report says only Qwen2.5-Coder-7B was evaluated. (`docs/semantic-checkpoints.md:141-145`; `evaluation-results/final-investigation-v1/investigation-20260812-final-002/executive-decision-report.md:84-89`)

### Git history and project arc

The history contains 335 commits from 2026-08-02 through 2026-08-16. It begins at `d94ea2c` with the checkpoint/runtime baseline and proceeds through deterministic resumption/dbt/context work, client and package hardening, semantic checkpoints and offline viability, then the live 7B investigation and Phase 2 verifier/capability-ladder work; the audit-snapshot tip is `d172b20`. (Git evidence: `git rev-list --count HEAD`; `git log --reverse --format='%h %aI %s'`; `git log -1 --format='%h %aI %s'`; the implemented arc is also summarized at `docs/implementation-status.md:382-400,5234-5258,5554-5610,5686-5777`.)

The long-horizon chronology is not a clean preregistration chronology. Commit `da7f174` added the small-model plan on 2026-08-15 at 11:53 +08:00, but that plan already states that memory F1 was 0.93 and the 7B scored 0/30. Commit `21f1eb1` at 12:24 added the “preregistration,” the long-horizon runner, and the result write-up together. (`docs/superpowers/plans/2026-08-15-small-model-long-horizon.md:11-17`; Git evidence: `git log --diff-filter=A -- ...`; `21f1eb1:docs/evaluations/long-horizon-preregistration.md:1-12`; `21f1eb1:docs/evaluations/memory-value-investigation.md:1-21`)

There is also a reproducibility inconsistency: the final manifest says the run used Git revision `57ec69f` and a runner at `scripts/run_long_horizon_evaluation.py` with hash `28d970...`, but that path is **NOT FOUND** in Git tree `57ec69f`; its first tracked addition is `21f1eb1`. (`evaluation-results/long-horizon-v1/final-20260812-qwen30-001/reproducibility-manifest.json:43-48`; Git evidence: `git cat-file -e 57ec69f:scripts/run_long_horizon_evaluation.py`, not found; `git log --diff-filter=A -- scripts/run_long_horizon_evaluation.py`)

## Q1. Is there a claim, and is it separable from the system?

Yes. The strongest general claim is: **the benefit of injected memory is a non-monotonic, inverted-U function of model capability: very small models cannot use it, mid-capability models benefit most, and frontier models benefit less.** That statement does not require the word “Mnemo.” (`docs/superpowers/plans/2026-08-15-small-model-long-horizon.md:200-219`)

Two narrower separable claims also exist: semantic checkpoints reduce total lifecycle tokens without reducing long-horizon continuation quality, and accurate memory transport may fail to convert into reliable task completion for the tested 7B configuration. (`evaluation-results/viability-v1/offline-20260812-57ec69f-integrity-001/report.md:41-46`; `docs/evaluations/memory-value-investigation.md:37-43`)

The first general claim is not a README-level result and has no completed multi-scale evidence; it appears in an implementation plan as a rationale for future validation. (`README.md:61-79`; `docs/superpowers/plans/2026-08-15-small-model-long-horizon.md:200-243`)

**Verdict: SUPPORTED** for the existence and separability of a claim, not for the truth of the claim.

## Q2. Is the headline finding a tested hypothesis or a post-hoc observation?

The original 7B observation is **post hoc**. The first tracked plan already knew both the memory F1 and the 0/30 outcome, and the tracked preregistration, runner, and result write-up were introduced together afterward. (`docs/superpowers/plans/2026-08-15-small-model-long-horizon.md:11-17`; Git evidence: additions in `da7f174` and `21f1eb1` described in the inventory chronology)

The saved preregistration asserts that it preceded “final-run model output,” but repository history cannot verify that assertion. The result manifest points to a Git tree that did not contain the named runner, and the ignored result artifacts are not independently timestamped by version control. (`docs/evaluations/long-horizon-preregistration.md:1-12`; `evaluation-results/long-horizon-v1/final-20260812-qwen30-001/reproducibility-manifest.json:43-51`; `.gitignore:19-31`)

The hypothesis has since been converted into a designed Phase 2 capability-ladder test with fixed 7B and 14B configurations, nine conditions, 30 variants, exact-value metrics, and thinking/non-thinking 14B modes. That designed follow-up has no completed 14B result and no completed 7B final analysis at the audit snapshot. (`docs/superpowers/plans/2026-08-15-small-model-long-horizon.md:200-243`; `tests/fixtures/evals/telehealth-long-horizon-phase2-qwen3-14b.json:1-24`; `tests/fixtures/evals/telehealth-long-horizon-phase2-qwen3-14b-thinking.json:1-24`; `docs/implementation-status.md:5753-5769`)

**Verdict: PARTIAL.** The finding began as a post-hoc observation. A prospective follow-up is implemented and in progress, not completed.

## Q3. Statistical power: seeds, tasks, and variance

| Headline result | Independent runs/seeds and examples | Variance/inference |
|---|---|---|
| README resumption/unified token reductions | One deterministic coding-handoff fixture and one dbt manifest; no stochastic seed. | No variance, CI, error bar, or significance test; these are exact fixture values from a heuristic counter. `README.md:61-79`; `docs/unified-context-benchmark.md:15-37` |
| Routing gates | 60 fixed prompts, 15 per class; no stochastic repeats. | Threshold assertions only; no variance, CI, or significance test. `tests/evals/test_automatic_context_routing.py:28-57` |
| Semantic checkpoint fidelity/compression | 12 fixed cases and three deterministic token-counter adapters; no external model seed. | No variance/CI/significance test for fidelity; totals and ratios are deterministic fixture summaries. `scripts/semantic_checkpoint_eval.py:107-227`; `docs/implementation-status.md:5234-5245` |
| Offline viability | 324 deterministic rows, but only six scenario families are independence units. Seed 20260812; no model sampling. | Sample SD, percentiles, and scenario-family cluster-bootstrap CIs exist. A category with one family reports its CI as not estimable. `evaluation-results/viability-v1/offline-20260812-57ec69f-integrity-001/evaluation-config.json:1-10,49-63`; `docs/evaluations/viability-evaluation.md:57-85` |
| Live Gate 1 continuation | One model continuation with seed 8122026. The seven exact requirements are scored on that one response. | No repeated seed, variance, CI, error bar, or significance test. `tests/fixtures/evals/live-semantic-gate-v1.json:1-10,80-107`; `evaluation-results/live-semantic-v1/live-20260812-57ec69f-gate1-002/summary.json:27-43` |
| Semantic lifecycle timing | 30 fresh SQLite profiles; model-token fields reuse one Gate 1 call. | Paired 10,000-resample profile-bootstrap intervals with seed 8122026 are reported for elapsed time. `evaluation-results/semantic-lifecycle-v1/comparison-20260812-baseline-vs-final-001/report.md:1-16`; `scripts/run_semantic_lifecycle_benchmark.py:302-315` |
| Completed 7B long-horizon result | One task template with 30 parameter substitutions. A base seed of 8122026 is offset by variant number, then reused for all sessions/conditions of that variant at temperature 0; there is no repeated stochastic run of the same variant. Each condition has 30 trajectories and three calls per trajectory. | The SD-SI paired bootstrap uses 10,000 resamples over variants, reports a 95% CI and Cohen's dz, and end-to-end success uses exact one-sided McNemar. Per-condition standard deviations/error bars: **NOT FOUND** in `analysis.json` or `report.md`. `scripts/run_long_horizon_evaluation.py:803-816,1030-1066,1095-1116`; `evaluation-results/long-horizon-v1/final-20260812-qwen30-001/analysis.json:25-43,65-83,174-205` |
| PostgreSQL load | One documented 160-operation run plus a statement that four consecutive accepted runs occurred; no seed. | No confidence interval or significance test; only reference p95/throughput extrema across the four runs are reported. `docs/team-load-slo.md:26-39` |
| Phase 2 model-scale claim | No completed 14B run and no 30B/32B run. | No cross-scale variance, interval, or hypothesis test exists. `docs/implementation-status.md:5753-5769`; `docs/superpowers/plans/2026-08-15-small-model-long-horizon.md:204-243` |

The +0.031 SD-SI accuracy difference is not indistinguishable from zero **under the repository's own variant-resampling model** because its saved interval is `[0.0156, 0.0489]`; it is nevertheless below the fixed +0.10 practical margin and produced 0/30 success in both arms. (`evaluation-results/long-horizon-v1/final-20260812-qwen30-001/analysis.json:174-205`)

That interval does not establish model-level or task-family generalization because the 30 “independent variants” are substitutions of role, status, timezone, and idempotency key inside one three-ticket template, and there is no repeated model seed per variant. (`evaluation-results/long-horizon-v1/final-20260816-qwen25coder7b-phase2-001/evaluation-config.json:70-98,110-182`; `scripts/run_long_horizon_evaluation.py:135-167,803-816`)

The single Gate 1 continuation result is currently indistinguishable from seed-to-seed or generation-to-generation noise because N=1 and variance is unreported. (`evaluation-results/live-semantic-v1/live-20260812-57ec69f-gate1-002/summary.json:27-43`; `tests/fixtures/evals/live-semantic-gate-v1.json:4-10`)

The inverted-U/model-threshold claim is not a noisy estimate; it is **unestimated**, because only one completed model size exists. (`evaluation-results/final-investigation-v1/investigation-20260812-final-002/executive-decision-report.md:84-89`; `docs/implementation-status.md:5753-5769`)

**Verdict: PARTIAL.** The narrow 30-variant paired contrast has an interval and exact success test. The headline capability claim has no cross-model sample, and the single-response Gate 1 number is indistinguishable from run noise.

## Q4. Controls and ablations: compared to what?

| Required condition | State | Evidence |
|---|---|---|
| Same model, no memory | **Implemented and executed.** `S0` has neither memory nor added deliberation; `SI` has the same deliberation instruction as `SD` but no persistent memory. | `docs/evaluations/long-horizon-preregistration.md:14-21`; `evaluation-results/long-horizon-v1/final-20260812-qwen30-001/analysis.json:5-24,65-83` |
| Same model, full/naive context stuffing | **Absent.** `SR` is a constructed rolling summary, not full prior prompts/responses or full usable history. No full-context condition appears in the condition set or prompt builder. | `scripts/run_long_horizon_evaluation.py:60-65,662-674,719-798`; **NOT FOUND** in `scripts/run_long_horizon_evaluation.py`, all telehealth corpus files, and `docs/evaluations/long-horizon-preregistration.md`. |
| Same model, oracle/perfect memory | **Absent.** No oracle condition exists. | `docs/evaluations/long-horizon-preregistration.md:14-21`; `scripts/run_long_horizon_evaluation.py:60-65`; **NOT FOUND** for `oracle` in the long-horizon runner/corpora/results. |
| Same model, deliberately degraded memory | **Implemented and executed.** `SX` appends explicit stale poison; 14/30 trajectories failed the fixed poison safety rule. | `scripts/run_long_horizon_evaluation.py:784-788`; `docs/implementation-status.md:5571-5574` |
| Larger model, no memory | **Implemented, not evidenced.** Qwen3-14B corpora include `S0` and `SI`; no result directory exists. | `tests/fixtures/evals/telehealth-long-horizon-phase2-qwen3-14b.json:1-24`; `docs/implementation-status.md:5753-5769` |
| Larger model, same memory | **Implemented, not evidenced.** Qwen3-14B corpora include `SF`, `SD`, `SFp`, and `SV`; no result directory exists. | `tests/fixtures/evals/telehealth-long-horizon-phase2-qwen3-14b.json:13-24`; `tests/fixtures/evals/telehealth-long-horizon-phase2-qwen3-14b-thinking.json:13-24`; `docs/implementation-status.md:5753-5769` |
| Randomized/shuffled memory contents | **Absent.** The only adversarial arm appends semantically harmful poison; it does not shuffle correct memory or randomize unrelated memory while preserving format/length. | `scripts/run_long_horizon_evaluation.py:784-788`; **NOT FOUND** in the runner, telehealth corpora, preregistration, or saved analysis. |

### The “oracle memory” premise fails

**The completed experiment has no oracle arm, and the reported 0.933 memory F1 is not evidence of oracle memory.** `_memory_score` checks only whether two literals are present in early sessions and four literals—authorization role, idempotency key, conflict status, and the substring `atomic`—are present in session 3; it treats three poison substrings as false positives. It does not verify that all 15 final hidden-check values are present, correct, current, attributable, or usable. (`scripts/run_long_horizon_evaluation.py:677-693`; `scripts/run_long_horizon_evaluation.py:79-95`)

For `SD` and `SV`, the harness constructs memory events directly from `_expected(...)` for fields listed as revealed by the current public ticket, saves those events before retrieval, and may therefore duplicate current-ticket ground truth into memory. (`scripts/run_long_horizon_evaluation.py:142-189,599-659,753-783`)

The final report's “memory precision 1.0, recall 0.875, F1 0.933” is consequently a four-literal transport score, not a validated upper bound on task-relevant memory. (`docs/evaluations/memory-value-investigation.md:37-43`; `scripts/run_long_horizon_evaluation.py:677-693`)

Because no oracle-by-construction condition exists, the claim “small model + perfect/oracle memory still underperforms” is unsupported by this experiment. The defensible wording is narrower: “Qwen2.5-Coder-7B under this prompt and task did not achieve end-to-end success when given the repository's SD memory rendering.” (`evaluation-results/long-horizon-v1/final-20260812-qwen30-001/analysis.json:25-43,65-83`; `evaluation-results/long-horizon-v1/final-20260812-qwen30-001/report.md:3-12`)

**Verdict: UNSUPPORTED.** Some useful same-model controls were executed, but the load-bearing oracle, full-context, larger-model, and shuffled-memory controls are absent or unexecuted.

## Q5. Evaluation integrity

### Contamination and leakage

The model is given the current implementation config, the current ticket, all allowed enum values, and an instruction that the ticket supplies the exact timezone, idempotency key, and conflict status. The three ticket templates state nearly every expected update directly. (`scripts/run_long_horizon_evaluation.py:308-343`; `evaluation-results/long-horizon-v1/final-20260816-qwen25coder7b-phase2-001/evaluation-config.json:111-148`)

Across the three “fresh” calls, the harness carries the model's accepted changes forward in `config` and renders that entire current config into the next prompt for every condition, including `S0` and `SI`. The no-memory arms therefore retain prior outputs through environment state even though they receive no separate memory block. (`scripts/run_long_horizon_evaluation.py:728-745,791-798,840-861`)

For `SD`/`SV`, current revealed expected values are additionally materialized as trusted memory events before memory retrieval. This does not leak the grader's boolean map, but it means the task is primarily literal extraction and state update, not recovery of facts unavailable from current input. (`scripts/run_long_horizon_evaluation.py:599-659,753-783`; `docs/evaluations/long-horizon-preregistration.md:23-28`)

The runner checks that prior response text is not copied verbatim into later prompts, but it does not test semantic leakage through the carried configuration because that carry-forward is part of the design. (`scripts/run_long_horizon_evaluation.py:791-801,840-897`)

### Metric validity

The “hidden executable checks” do not execute a scheduler, database transaction, authorization lookup, idempotent replay, rollback, cache invalidation, or timezone operation. They are 15 equality comparisons between final config fields and expected literals. (`scripts/run_long_horizon_evaluation.py:170-189`)

End-to-end success is defined as equality on all critical fields and at least 90% of those field checks. This is a deterministic structured-output metric, not task execution. (`scripts/run_long_horizon_evaluation.py:933-975`)

No LLM judge is used in the long-horizon scorer, so same-family judge bias is absent. No human validation of the equality metric against actual scheduler-task success exists; blinded human quality is recorded as `NOT EVALUATED`. (`docs/evaluations/long-horizon-preregistration.md:62-70`; `evaluation-results/long-horizon-v1/final-20260812-qwen30-001/analysis.json:1-3`)

The offline viability “task success” number is explicitly an information-availability proxy, not generated task completion. (`evaluation-results/viability-v1/offline-20260812-57ec69f-integrity-001/report.md:67-80`; `docs/evaluations/viability-evaluation.md:145-151`)

### Horizon validity

Each long-horizon trajectory consists of exactly three stateless model calls. (`docs/evaluations/long-horizon-preregistration.md:23-28`; `scripts/run_long_horizon_evaluation.py:753-753,963-964`)

The repository's `beyond_active_context` flag means cumulative prior prompt/output tokens exceed the current prompt token count; it does not mean the current prompt overflowed the model's context window. (`scripts/run_long_horizon_evaluation.py:865-896`)

The largest `active_prompt_tokens` value found by scanning the completed raw session log was 1,209, while the configured model context was 4,096. The completed experiment therefore does not exercise context-window overflow or context-window saturation. (`evaluation-results/long-horizon-v1/final-20260812-qwen30-001/raw-sessions.jsonl:518`; `evaluation-results/long-horizon-v1/final-20260812-qwen30-001/reproducibility-manifest.json:24-28`)

The evidence supports a three-boundary state-carrying task; it does not support a general “long-horizon” claim in the sense of dozens of turns, long tool trajectories, or near-window-limit context. (`docs/evaluations/long-horizon-preregistration.md:23-28`; `evaluation-results/final-investigation-v1/investigation-20260812-final-002/executive-decision-report.md:84-91`)

### Prompt fairness

`SI` and `SD` receive the same added deliberation text and output budget, which isolates the presence of the SD memory block more cleanly than an `S0` comparison. (`docs/evaluations/long-horizon-preregistration.md:14-21`; `scripts/run_long_horizon_evaluation.py:318-343`)

The prompts still expose the condition label, and memory conditions receive a long structured packet while no-memory conditions receive `PERSISTENT CONTEXT: NONE`; no blinded prompt-label ablation or independently optimized no-memory/full-context baseline is present. (`scripts/run_long_horizon_evaluation.py:324-343`; **NOT FOUND** in the preregistration, runner, telehealth corpora, or results.)

### Silent failures

Transport/timeouts/exceptions produce an unavailable trajectory and a separate failure record, rather than silently becoming an ordinary incorrect result. (`scripts/run_long_horizon_evaluation.py:1323-1351`)

A parse failure, however, becomes an empty `changes` object with `invalid_change_count=1`, so it contributes as task failure unless separately inspected in session records. (`scripts/run_long_horizon_evaluation.py:395-435`)

The completed final run reports zero parse failures, cap hits, invalid changes, transcript leaks, or unavailable trajectories, but it also contains two orphan model calls from a file-descriptor interruption that are excluded from efficacy estimates. (`docs/implementation-status.md:5576-5587`; `evaluation-results/final-investigation-v1/investigation-20260812-final-002/executive-decision-report.md:57-71`)

The old completed raw session artifacts contain full prompts and response bodies; the current Phase 2 runner was changed to retain hashes and accepted fields instead. This does not change the old efficacy scores, but it means the old raw artifact format and current runner are not identical. (`evaluation-results/long-horizon-v1/final-20260812-qwen30-001/raw-sessions.jsonl:518`; `docs/implementation-status.md:5762-5768`; `scripts/run_long_horizon_evaluation.py:869-893`)

### Reproducibility

Dependencies are pinned in `pyproject.toml`, and completed live manifests record the corpus hash, runner hash, model digest/family/size/quantization, runtime version, platform, and commands. (`pyproject.toml:18-33,47-71`; `evaluation-results/long-horizon-v1/final-20260812-qwen30-001/reproducibility-manifest.json:1-48`)

Exact reproduction of the headline artifact is not possible from a fresh clone as recorded: `evaluation-results` is ignored/untracked, and the manifest's claimed Git revision does not contain the claimed runner. (`.gitignore:19-31`; `evaluation-results/long-horizon-v1/final-20260812-qwen30-001/reproducibility-manifest.json:43-48`; Git evidence: `git cat-file -e 57ec69f:scripts/run_long_horizon_evaluation.py`, not found)

A stranger can run the **current** runner with the checked-in corpus and an Ollama model matching the identifier, but that is not byte-identical reproduction of the saved result because the current runner's source hash differs from the manifest and its artifact schema has changed. (`evaluation-results/long-horizon-v1/final-20260812-qwen30-001/reproducibility-manifest.json:46-48`; `docs/implementation-status.md:5762-5768`; current `scripts/run_long_horizon_evaluation.py:869-893`)

**Verdict: UNSUPPORTED** for the broad scientific claim. The exact structured scorer and explicit failure bookkeeping are inspectable, but the task leaks its required values in current inputs, the “behavioral” checks are field equality, the horizon is three short calls, and the exact historical runner/result package is not reproducible from the tracked tree.

## Q6. Literature positioning

There is no systematic related-work artifact. **NOT FOUND:** `.bib`/`.bibtex` files, a `papers/` directory, a related-work document, or a literature-review table after searching `README.md`, `docs`, `src`, `scripts`, and `tests` by file name and for headings/terms including `Related`, `References`, `Literature`, `arXiv`, `LongMemEval`, and `FaithfulRAG`.

The model-selection plan contains four inline arXiv identifiers and names FaithfulRAG, but gives no bibliography entries, study-design comparison, dataset overlap analysis, or explicit novelty matrix. (`docs/superpowers/plans/2026-08-15-small-model-long-horizon.md:200-219`)

The plan's “Evidence base” is three external Claude artifact links plus internal diagnosis/preregistration/ADR files, not a scholarly related-work record. (`docs/superpowers/plans/2026-08-15-small-model-long-horizon.md:20-27`)

The repository's ADR references are mainly implementation/security/product sources; the implementation plan also links vendor documentation. Neither is a systematic account of memory-agent or model-scale research. (`docs/implementation-plan.md:738-754`; examples: `docs/adr/0003-dbt-manifest-lineage.md:108-114`)

Novelty therefore cannot be assessed from this repository alone. The only nearby works I can name without adding external claims are the repository's own unverified pointers: arXiv `2603.11513`, FaithfulRAG/arXiv `2506.08938`, input-distrust work at `2505.17225`, and format-tax work at `2604.03616`. Their relevance and the plan's descriptions were not verified in this audit. (`docs/superpowers/plans/2026-08-15-small-model-long-horizon.md:202-219`)

**Verdict: PARTIAL.** Inline pointers exist; systematic literature positioning and assessable novelty do not.

## Q7. Load-bearing-ness

If the completed 7B numbers are taken as rigorously true, they change one concrete decision: do not deploy or justify investment in the tested M3 path on Qwen2.5-Coder-7B for this three-session telehealth-config workflow on the expectation of task-success or token-economic gain. The repository itself applies exactly that `STOP` decision to the tested segment. (`docs/evaluations/memory-value-investigation.md:78-85`; `evaluation-results/final-investigation-v1/investigation-20260812-final-002/executive-decision-report.md:94-96`)

They do not identify which larger model to use, where a capability threshold lies, whether benefit later declines, or whether open-ended coding tasks behave similarly, because no completed cross-scale or second-task-family result exists. (`docs/superpowers/plans/2026-08-15-small-model-long-horizon.md:200-243`; `evaluation-results/final-investigation-v1/investigation-20260812-final-002/executive-decision-report.md:84-91`)

Thus the narrow product decision is load-bearing, while the general inverted-U claim is currently **true but inert** even if assumed true: the repository has not estimated the curve or a deployment threshold. (`docs/superpowers/plans/2026-08-15-small-model-long-horizon.md:200-213`; `docs/implementation-status.md:5753-5769`)

**Verdict: PARTIAL.** There is a concrete stop decision for one tested segment; there is no supported general model-selection rule.

## Adversarial pass (Phase 3)

### 1. Prompt-format sensitivity at small scale

The model is forced into strict JSON with seven fields, exact enum restrictions, short rationale fields, and a 320-token cap. The first dry run had three malformed outputs at the earlier 240-token cap, which caused a protocol amendment. (`scripts/run_long_horizon_evaluation.py:308-343`; `docs/evaluations/long-horizon-preregistration.md:6-12`)

**What rules it out:** nothing in the completed result. No same-content free-form-vs-JSON ablation was run. Two-phase generation exists only in the unexecuted 14B-thinking configuration. (`tests/fixtures/evals/telehealth-long-horizon-phase2-qwen3-14b-thinking.json:1-24`; `docs/implementation-status.md:5753-5769`)

### 2. Instruction-following/config-copying failure rather than memory-utilization failure

The task requires copying explicit ticket values into a JSON change object and preserving prior fields in a supplied current-config object; the scorer then checks field equality. (`scripts/run_long_horizon_evaluation.py:170-189,308-343,933-975`)

**What rules it out:** nothing. No independent instruction-following baseline, direct copy task, or executable tool-use task is present. The same model's 0/30 success across both SI and SD is compatible with a task-format/field-update ceiling unrelated to memory. (`evaluation-results/long-horizon-v1/final-20260812-qwen30-001/analysis.json:25-43,65-83`; **NOT FOUND** in the long-horizon runner/corpora/results.)

### 3. Context-window overflow or truncation

**What rules it out:** this alternative is ruled out for the completed final run. The largest scanned active prompt was 1,209 tokens against `num_ctx=4096`, and the final run reports no cap hits. (`evaluation-results/long-horizon-v1/final-20260812-qwen30-001/raw-sessions.jsonl:518`; `evaluation-results/long-horizon-v1/final-20260812-qwen30-001/reproducibility-manifest.json:24-28`; `docs/implementation-status.md:5576-5581`)

This does not validate the “long-horizon” construct; it shows that context overflow is not the reason for the observed failure. (`scripts/run_long_horizon_evaluation.py:886-888`; `docs/evaluations/long-horizon-preregistration.md:23-28`)

### 4. Retrieval rendering or prompt placement confuses the model

SD receives a dense `MNEMO_CP_V1` rendering plus evidence aliases and metadata, while SI receives `NONE`. SF, SR, and SD change both content type and format, so none is a clean same-information/different-render comparison. (`scripts/run_long_horizon_evaluation.py:662-674,719-798`; one actual rendering is visible at `evaluation-results/long-horizon-v1/final-20260812-qwen30-001/raw-sessions.jsonl:518`.)

**What rules it out:** nothing. There is no arm that presents byte-equivalent correct memory in plain text, full context, shuffled format, or alternate prompt position. (`scripts/run_long_horizon_evaluation.py:60-65`; **NOT FOUND** in the runner/corpora/results.)

### 5. Task difficulty ceiling unrelated to memory

Every completed condition—including no memory, rolling summary, factual memory, deliberative memory, and poisoned memory—has 0.0 end-to-end success, while field accuracy clusters between 0.64 and 0.79. (`evaluation-results/long-horizon-v1/final-20260812-qwen30-001/analysis.json:5-124`)

**What rules it out:** nothing. No larger completed model, human ceiling, executable reference agent, or easier/harder task ladder exists. (`evaluation-results/final-investigation-v1/investigation-20260812-final-002/executive-decision-report.md:84-91`; `docs/implementation-status.md:5753-5769`)

### 6. Tokenizer, quantization, or serving configuration ceiling

The completed model is a 7.6B Q4_K_M Ollama artifact at temperature 0 with a fixed digest and runtime version. (`evaluation-results/long-horizon-v1/final-20260812-qwen30-001/reproducibility-manifest.json:24-41`)

**What rules it out:** model identity is recorded, which rules out uncertainty about what artifact was used, but no alternate quantization, serving runtime, context configuration, or unquantized checkpoint was run. The performance ceiling could therefore be specific to this deployment. (**NOT FOUND** in completed result manifests; proposed alternatives are only future rungs at `docs/superpowers/plans/2026-08-15-small-model-long-horizon.md:204-243`.)

### 7. Judge bias or metric construct bias

There is no LLM judge, so model-family favoritism by a learned judge is ruled out. (`scripts/run_long_horizon_evaluation.py:170-189,933-975`)

**What is not ruled out:** construct bias. The equality scorer may reward literal field copying without measuring the claimed authorization, concurrency, rollback, DST, audit, or cache behaviors, and it has no human/executable validation. (`scripts/run_long_horizon_evaluation.py:170-189`; `docs/evaluations/long-horizon-preregistration.md:62-70`)

## The three things that would most change the verdict

1. **Complete a genuine model-size-by-memory factorial.** Run at least three same-family model sizes spanning the proposed curve (7B, 14B, and 30B/32B), with `no memory`, `full prior context`, `Mnemo memory`, `oracle-by-construction`, `degraded memory`, and `shuffled-memory` arms. Use at least five independent generation seeds on 100 examples drawn from at least five task families per model/condition. Pre-register the interaction contrast and the quadratic/inverted-U test before any new output. Rough effort: 1–3 GPU-weeks plus 1–2 engineer-weeks for corpus and analysis. This directly addresses the currently unexecuted ladder and missing controls. (`docs/superpowers/plans/2026-08-15-small-model-long-horizon.md:200-243`; Q4 evidence above)

2. **Replace “oracle” assertion with an oracle-by-construction artifact and matched rendering ablation.** For 100 held-out tasks, publish a machine-checkable oracle packet containing every fact sufficient for success, an automated completeness proof against the executable grader, and equal-length plain/Mnemo/shuffled/degraded renderings. Run five seeds on 7B and 14B. Rough effort: 1 engineer-week plus 3–7 GPU-days. This addresses the current four-literal F1 score and rendering confound. (`scripts/run_long_horizon_evaluation.py:677-693`; Q4 oracle finding above)

3. **Use actual long-horizon executable tasks.** Evaluate at least 100 tasks across five repositories or task families, each requiring 20–50 model/tool steps and a real test suite or simulator as the primary outcome; compare no memory, full context, and Mnemo under three seeds, with failure types and context overflow separated. Rough effort: 1–2 engineer-months plus 1–2 GPU-weeks. This addresses the current three-call horizon and equality-only outcome. (`scripts/run_long_horizon_evaluation.py:170-189,753-753,933-975`; Q5 horizon/metric evidence above)

## What is genuinely solid

- The completed 7B artifact records the exact model identifier, digest, parameter size, quantization, runtime, generation settings, platform, corpus hash, and runner hash. (`evaluation-results/long-horizon-v1/final-20260812-qwen30-001/reproducibility-manifest.json:1-51`)
- The completed SD-SI comparison uses paired variants, a fixed practical threshold, a saved bootstrap interval/effect size, and an exact McNemar calculation; the gate is reported as `FAIL` rather than redefined after observing +0.031. (`docs/evaluations/long-horizon-preregistration.md:30-60`; `evaluation-results/long-horizon-v1/final-20260812-qwen30-001/analysis.json:174-209`)
- The single Gate 1 trace establishes exact transport for its fixture—critical fidelity 1.0, zero listed false memories, exact-scope poison exclusion, and deletion cleanup—while explicitly not claiming behavioral improvement. (`evaluation-results/live-semantic-v1/live-20260812-57ec69f-gate1-002/summary.json:10-24,42-69`; `evaluation-results/live-semantic-v1/live-20260812-57ec69f-gate1-002/report.md:25-31`)
- The lifecycle timing result keeps deterministic local elapsed work separate from the one reused model call and from estimated counterfactual tokens. (`evaluation-results/semantic-lifecycle-v1/lifecycle-20260812-57ec69f-evidence-reuse-final-001/report.md:1-18`; `evaluation-results/semantic-lifecycle-v1/comparison-20260812-baseline-vs-final-001/report.md:9-16`)
- The offline viability report labels its task-success values as proxies, identifies six—not 324—independent scenario families, and records `INSUFFICIENT EVIDENCE`. (`evaluation-results/viability-v1/offline-20260812-57ec69f-integrity-001/report.md:8-29,48-65`; `docs/evaluations/viability-evaluation.md:145-151`)
- The terminal report confines its stop decision to the tested three-session local-Qwen telehealth segment and explicitly lists absent portability, human, customer, and frontier evidence. (`docs/evaluations/memory-value-investigation.md:78-85`; `evaluation-results/final-investigation-v1/investigation-20260812-final-002/executive-decision-report.md:84-92`)
