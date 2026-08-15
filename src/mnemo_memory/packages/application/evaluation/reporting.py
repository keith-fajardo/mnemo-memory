"""Append-only result artifacts, dependency-free SVG charts, and decision report."""

# ruff: noqa: E501

from __future__ import annotations

import csv
import hashlib
import html
import json
import statistics
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from .models import ConditionId, EvaluationConfig, EvaluationRun, ScenarioCorpus


def write_evaluation_artifacts(
    *,
    results_root: Path,
    evaluation_run_id: str,
    runs: tuple[EvaluationRun, ...],
    aggregate: dict[str, Any],
    config: EvaluationConfig,
    corpus: ScenarioCorpus,
    environment: dict[str, object],
    corpus_path: Path,
    config_path: Path,
) -> Path:
    run_directory = results_root / evaluation_run_id
    run_directory.mkdir(parents=True, exist_ok=False)
    charts = run_directory / "charts"
    charts.mkdir()
    _write_jsonl(run_directory / "raw-runs.jsonl", runs)
    _write_csv(run_directory / "per-run-metrics.csv", runs)
    _write_json(run_directory / "aggregate.json", aggregate)
    _write_json(run_directory / "evaluation-config.json", _config_dict(config))
    _write_json(run_directory / "environment.json", environment)
    _write_human_review_artifacts(run_directory, runs)
    chart_paths = generate_charts(charts, runs, aggregate)
    report = render_comprehensive_report(
        evaluation_run_id=evaluation_run_id,
        aggregate=aggregate,
        config=config,
        corpus=corpus,
        environment=environment,
        chart_paths=chart_paths,
    )
    _write_exclusive(run_directory / "report.md", report)
    artifacts = sorted(
        path.relative_to(run_directory).as_posix()
        for path in run_directory.rglob("*")
        if path.is_file()
    )
    manifest = {
        "schema_version": "mnemo-viability-reproducibility/1.0",
        "evaluation_run_id": evaluation_run_id,
        "corpus": {
            "path": corpus_path.as_posix(),
            "sha256": _sha256(corpus_path),
            "version": corpus.corpus_version,
        },
        "configuration": {
            "path": config_path.as_posix(),
            "sha256": _sha256(config_path),
            "schema_version": config.schema_version,
        },
        "environment": environment,
        "commands": [
            f"uv run python -m scripts.run_viability_evaluation --run-id {evaluation_run_id}",
            "uv run pytest -q tests/unit/test_viability_evaluation.py "
            "tests/evals/test_lifecycle_viability_evaluation.py",
            "npm run check",
        ],
        "artifact_hashes": {name: _sha256(run_directory / name) for name in artifacts},
        "raw_log_policy": "exclusive-create run directory; an existing run ID is never overwritten",
    }
    _write_json(run_directory / "reproducibility-manifest.json", manifest)
    return run_directory


def generate_charts(
    directory: Path,
    runs: tuple[EvaluationRun, ...],
    aggregate: dict[str, Any],
) -> tuple[str, ...]:
    conditions = cast(dict[str, dict[str, Any]], aggregate["conditions"])
    baseline_tokens = _metric(
        conditions[ConditionId.FULL_HISTORY.value], "lifecycle_tokens", "mean"
    )
    labels = list(conditions)
    tes = [
        0.0
        if baseline_tokens == 0
        else (baseline_tokens - _metric(conditions[name], "lifecycle_tokens", "mean"))
        / baseline_tokens
        for name in labels
    ]
    lme = [_metric(conditions[name], "lme_gated", "mean") for name in labels]
    paths: list[Path] = []
    paths.append(
        _write_chart(
            directory / "01-token-efficiency-vs-lme.svg",
            _scatter_svg(
                "Lifecycle token efficiency vs gated LME",
                labels,
                tes,
                lme,
                "TES vs full history",
                "Gated LME",
            ),
        )
    )
    points = cast(dict[str, dict[str, float]], aggregate["pareto_points"])
    frontier = set(cast(list[str], aggregate["pareto_frontier"]))
    paths.append(
        _write_chart(
            directory / "02-pareto-frontier.svg",
            _scatter_svg(
                "Pareto frontier: lifecycle tokens vs LME",
                list(points),
                [points[name]["tokens"] for name in points],
                [points[name]["lme"] for name in points],
                "Lifecycle token estimate (lower is better)",
                "Gated LME (higher is better)",
                highlights=frontier,
            ),
        )
    )
    token_components = _mean_token_components(runs)
    paths.append(
        _write_chart(
            directory / "03-lifecycle-token-breakdown.svg",
            _stacked_bar_svg("Mean lifecycle token breakdown", token_components),
        )
    )
    by_horizon = cast(dict[str, dict[str, dict[str, float]]], aggregate["by_horizon"])
    horizon_values = {
        f"{horizon}:{condition}": value["task_success_proxy_mean"]
        for horizon, condition_values in by_horizon.items()
        for condition, value in condition_values.items()
    }
    paths.append(
        _write_chart(
            directory / "04-task-success-by-horizon.svg",
            _bar_svg("Offline task-success proxy by condition and horizon", horizon_values),
        )
    )
    retention = {
        f"{name}:required": _metric(value, "required_knowledge_retention", "mean")
        for name, value in conditions.items()
    }
    retention.update(
        {
            f"{name}:protected": _metric(value, "protected_span_fidelity", "mean")
            for name, value in conditions.items()
        }
    )
    paths.append(
        _write_chart(
            directory / "05-required-and-protected-retention.svg",
            _bar_svg("Required-fact and protected-span retention", retention, maximum=1.0),
        )
    )
    paths.append(
        _write_chart(
            directory / "06-supersession-temporal-accuracy.svg",
            _bar_svg(
                "Supersession and temporal accuracy",
                {
                    name: _metric(value, "temporal_supersession_accuracy", "mean")
                    for name, value in conditions.items()
                },
                maximum=1.0,
            ),
        )
    )
    failure_values: dict[str, float] = {}
    for name, value in conditions.items():
        failure_values[f"{name}:critical"] = float(value["critical_violation_runs"])
        failure_values[f"{name}:false-memory"] = _metric(value, "false_memory_rate", "mean")
    paths.append(
        _write_chart(
            directory / "07-false-memory-critical-failures.svg",
            _bar_svg("False-memory rates and critical-failure run counts", failure_values),
        )
    )
    break_even = cast(dict[str, dict[str, Any]], aggregate["break_even"])
    paths.append(
        _write_chart(
            directory / "08-break-even-reuse.svg",
            _bar_svg(
                "Median checkpoint reuse required for token break-even",
                {name: float(value["median_reuses"] or 0.0) for name, value in break_even.items()},
            ),
        )
    )
    portability = cast(dict[str, Any], aggregate["operational_portability"])
    paths.append(
        _write_chart(
            directory / "09-portability-status.svg",
            _bar_svg(
                "Portability evidence status (missing, not zero performance)",
                {
                    "live model families tested": float(portability["live_model_families"]),
                    "exact provider tokenizers": float(portability["provider_tokenizers"]),
                },
                maximum=2.0,
            ),
        )
    )
    economics = cast(list[dict[str, Any]], aggregate["economic_scenarios"])
    paths.append(
        _write_chart(
            directory / "10-economic-scenarios.svg",
            _bar_svg(
                "Estimated annual net value sensitivity (USD)",
                {str(item["name"]): float(item["annual_net_value_usd"]) for item in economics},
                allow_negative=True,
            ),
        )
    )
    return tuple(path.relative_to(directory.parent).as_posix() for path in paths)


def render_comprehensive_report(
    *,
    evaluation_run_id: str,
    aggregate: dict[str, Any],
    config: EvaluationConfig,
    corpus: ScenarioCorpus,
    environment: dict[str, object],
    chart_paths: tuple[str, ...],
) -> str:
    conditions = cast(dict[str, dict[str, Any]], aggregate["conditions"])
    comparisons = cast(dict[str, dict[str, Any]], aggregate["paired_comparisons"])
    candidate_name = ConditionId.MNEMO_RETRIEVAL.value
    candidate = conditions[candidate_name]
    compact = conditions[ConditionId.MNEMO_COMPACT.value]
    rolling_summary = conditions[ConditionId.ROLLING_SUMMARY.value]
    full_comparison = comparisons[f"{candidate_name}_vs_{ConditionId.FULL_HISTORY.value}"]
    rolling_same_budget_comparison = comparisons[
        f"{ConditionId.MNEMO_COMPACT.value}_vs_{ConditionId.ROLLING_SUMMARY.value}"
    ]
    rolling_retrieval_comparison = comparisons[
        f"{candidate_name}_vs_{ConditionId.ROLLING_SUMMARY.value}"
    ]
    tes = _metric(full_comparison, "token_efficiency_score", "median")
    tes_diagnostics = cast(dict[str, Any], aggregate["lifecycle_tes_diagnostics"])
    ratio_of_medians = float(tes_diagnostics["ratio_of_condition_medians"])
    baseline_median_tokens = float(tes_diagnostics["baseline_condition_median_tokens"])
    candidate_median_tokens = float(tes_diagnostics["candidate_condition_median_tokens"])
    rolling_same_budget_delta = _metric(
        rolling_same_budget_comparison, "task_success_proxy_delta", "mean"
    )
    rolling_retrieval_delta = _metric(
        rolling_retrieval_comparison, "task_success_proxy_delta", "mean"
    )
    lme = _metric(candidate, "lme_gated", "mean")
    success = _metric(candidate, "task_success_proxy", "mean")
    break_even = cast(dict[str, dict[str, Any]], aggregate["break_even"])[candidate_name]
    thresholds = cast(list[dict[str, Any]], aggregate["thresholds"])
    threshold_summary = cast(dict[str, int], aggregate["threshold_summary"])
    failure_counts = cast(dict[str, int], candidate["failure_categories"])
    category = cast(dict[str, dict[str, Any]], aggregate["by_category"])
    best_category = max(
        category,
        key=lambda name: float(
            cast(dict[str, Any], category[name]["token_efficiency_score"])["median"] or -1.0
        ),
    )
    artifact_lines = "\n".join(f"- [{Path(path).name}]({path})" for path in chart_paths)
    threshold_rows = "\n".join(
        f"| {item['threshold']} | {_fmt(item['value'])} | {item['status']} | {item['classification']} | {item['evidence']} |"
        for item in thresholds
    )
    classification_rows = "\n".join(
        f"| {item['metric_or_family']} | {item['classification']} | {item['basis']} |"
        for item in cast(list[dict[str, str]], aggregate["metric_classification_catalog"])
    )
    condition_rows = "\n".join(
        "| {name} | {tokens} | {cf} | {success_proxy} | {lme_value} | {violations} |".format(
            name=name,
            tokens=_fmt(_metric(value, "lifecycle_tokens", "median")),
            cf=_fmt(_metric(value, "continuation_fidelity", "mean")),
            success_proxy=_fmt(_metric(value, "task_success_proxy", "mean")),
            lme_value=_fmt(_metric(value, "lme_gated", "mean")),
            violations=value["critical_violation_runs"],
        )
        for name, value in conditions.items()
    )
    economic_rows = "\n".join(
        f"| {item['name']} | {_money(item['economic_saving_per_eligible_run_usd'])} | {_money(item['annual_net_value_usd'])} | {_fmt(item['roi'])} | {_fmt(item['volume_break_even_runs'])} |"
        for item in cast(list[dict[str, Any]], aggregate["economic_scenarios"])
    )
    horizon_reuse_rows = "\n".join(
        f"| {name} | {_fmt(value['median'])} | {_fmt(value['mean'])} | "
        f"{_fmt(value['confidence_interval_95'])} |"
        for name, value in cast(
            dict[str, dict[str, Any]], aggregate["token_efficiency_by_horizon_reuse"]
        ).items()
    )
    return f"""# Mnemo lifecycle viability evaluation

Run ID: `{evaluation_run_id}`<br>
Evidence date: `{environment.get("evaluated_at")}`<br>
Evaluated revision: `{environment.get("evaluated_revision")}`<br>
Worktree state: `{environment.get("worktree_state")}`

## 1. Executive summary

**Verdict: {aggregate["verdict"]}.** The harness executed {aggregate["available_run_count"]}
available condition rows across {aggregate["independent_scenario_family_count"]} independent
scenario families, {len(corpus.scenarios)} materialized scenarios, and three reuse levels. For M3,
the median of paired estimated lifecycle TES ratios versus full history was **{tes:.1%}**, gated LME was
**{lme:.3f}**, and the offline continuation-success proxy was **{success:.3f}**. These are direct
measurements of deterministic synthetic context availability and estimated token-equivalent
processing, not live model task performance or provider billing.

## 2. Verdict: Proceed, Pivot, Stop, or Insufficient Evidence

**INSUFFICIENT EVIDENCE.** The provisional table contains {threshold_summary["PASS"]} `PASS`,
{threshold_summary["FAIL"]} `FAIL`, and {threshold_summary["NOT EVALUATED"]} `NOT EVALUATED`
verdicts. Compression and deterministic retention can justify the next
validation experiment, but cannot justify continued product investment by themselves.

## 3. Confidence level and strongest evidence

Confidence is **high for harness behavior and synthetic integrity**, **moderate for relative local
token estimates**, and **low for product viability**. Strongest evidence: paired execution,
byte-exact protected checks, scope canaries, append-only run data, and reproducible formulas.

## 4. Current Mnemo architecture evaluated

The worktree implements immutable task-event envelopes with canonical evidence references, a typed
semantic atom ledger, deterministic patch application, personal SQLite delta/snapshot checkpoints,
and separate compact/portable/audit renderings. The M3 condition adds lexical selective expansion
from canonical events. It is not wired into the public automatic MCP path. Architecture metadata is
recorded in `environment.json`; the dirty-worktree source identity is
`{cast(dict[str, object], environment.get("evaluated_source_tree", {})).get("sha256")}` across
`{cast(dict[str, object], environment.get("evaluated_source_tree", {})).get("file_count")}` files.

## 5. Research questions and hypotheses

The primary hypothesis is that semantic checkpoints reduce **total lifecycle** tokens without
reducing long-horizon continuation quality. Secondary hypotheses cover superiority to rolling
summaries, positive reuse break-even, safe supersession, evidence fidelity, and workload-specific
value. Live task and market hypotheses remain untested.

## 6. Experimental design

Each scenario/reuse pair ran every condition with a counterbalanced order and a fresh in-memory
store. The deterministic grader never receives the condition ID. Shared task prompts, tokenizer,
tools, seed, and rubric are fixed by fairness digest `{environment.get("fairness_control_digest")}`.
There are {aggregate["paired_observations_per_available_condition"]} paired observations per
available condition, but the six scenario templates—not those deterministic rows—are the primary
independence unit. Confidence intervals for paired and condition-level metrics use a cluster
bootstrap over whole scenario families. Repetition is across scenario/horizon/reuse strata, not
stochastic model sampling.

## 7. Models, providers, configurations, budgets, and limitations

No live provider call was authorized: `live_evaluation_enabled={config.live_evaluation_enabled}`,
maximum external calls `{config.budget.maximum_external_calls}`, suite budget
`${config.budget.maximum_suite_cost_usd:.2f}`. B3 provider-native compaction and the model semantic
grader are unavailable, not simulated. All token values use `{config.token_counter_id}` and are
estimates. External cost incurred: **$0.00**.

### 7.1 Metric evidence classification

The classifications below apply to the named raw and aggregate metric families. A metric is never
upgraded merely because it appears in a deterministic report.

| Metric or family | Classification | Basis |
|---|---|---|
{classification_rows}

## 8. Scenario corpus and ground truth

Corpus `{corpus.corpus_version}` contains six realistic workflow templates expanded to
{len(corpus.scenarios)} scenarios at 15, 75, and 225 events. It covers software delivery, incident
operations, customer cases, research, multi-agent coordination, and dbt analytics engineering,
plus all controlled integrity challenges. Ground truth is never inserted into tested context.

## 9. Baselines and Mnemo conditions

- B0: full usable event history.
- B1: whole-event 600-token sliding window.
- B2: deterministic conventional natural-language rolling-summary proxy at 200 tokens.
- B3: unavailable because no real provider-native adapter and budget are configured.
- M1: semantic checkpoint with a 200-token target/ceiling.
- M2: 200-token preference with a 600-token ceiling.
- M3: M2 plus bounded lexical evidence expansion from canonical events.

## 10. Token Efficiency results

| Condition | Median lifecycle token estimate | Continuation fidelity | Task-success availability proxy | Gated LME proxy | Critical-violation rows |
|---|---:|---:|---:|---:|---:|
{condition_rows}

M3's primary descriptive TES is the **median of paired ratios: {tes:.1%}** after save, validation,
recall, retrieval, and simulated retry equivalents. The separate **ratio of displayed condition
medians is {ratio_of_medians:.1%}**: `1 - {_fmt(candidate_median_tokens)} /
{_fmt(baseline_median_tokens)}`. These summaries answer different
questions and are labelled separately. Median positive token break-even is
**{_fmt(break_even["median_reuses"])} reuses**;
{break_even["no_positive_break_even_count"]} scenarios had no positive break-even. Compression
ratio is retained only as a diagnostic in per-run artifacts.

| Horizon and reuse | Median M3 TES | Mean M3 TES | Clustered mean 95% CI |
|---|---:|---:|---:|
{horizon_reuse_rows}

![Token efficiency versus LME]({chart_paths[0]})

## 11. Long-Term Memory Efficiency results

M3 gated/ungated LME means are **{_fmt(_metric(candidate, "lme_gated", "mean"))}** and
**{_fmt(_metric(candidate, "lme_ungated", "mean"))}**. Required knowledge, temporal accuracy,
evidence attribution, drift, retrieval F1, and false-memory probes are separately recorded; a
critical violation zeros a run's gated LME.

## 12. Two-axis analysis and Pareto frontier

Nondominated offline conditions: `{", ".join(cast(list[str], aggregate["pareto_frontier"]))}`.
No single condition is called best: B0 maximizes available history but costs tokens; M1 minimizes
context but may omit useful evidence; M3 spends retrieval tokens for higher evidence recall.

![Pareto frontier]({chart_paths[1]})

## 13. Task Impact results

Task Impact is an **offline availability proxy**, not generated-agent performance. M3 mean proxy is
**{_fmt(_metric(candidate, "task_impact_proxy", "mean"))}**. The same-policy 200-token M1-versus-B2
task-success proxy delta is **{rolling_same_budget_delta:+.3f}**, computed from M1 task-success
proxy **{_fmt(_metric(compact, "task_success_proxy", "mean"))}** minus B2
**{_fmt(_metric(rolling_summary, "task_success_proxy", "mean"))}**. The separate
continuation-fidelity values displayed in Section 10 are M1
**{_fmt(_metric(compact, "continuation_fidelity", "mean"))}** and B2
**{_fmt(_metric(rolling_summary, "continuation_fidelity", "mean"))}**, whose difference is
**{_fmt(_metric(compact, "continuation_fidelity", "mean") - _metric(rolling_summary, "continuation_fidelity", "mean"))}**;
they are not inputs to the
reported task-success delta. M3-versus-B2 with adaptive context
and retrieval is **{rolling_retrieval_delta:+.3f}**. Resume speed is estimated from context size;
avoided work from failed-approach retention; human intervention from blocker/next-action availability.

## 14. Economic and break-even analysis

These values are sensitivity estimates, not market evidence or provider invoices.

| Scenario | Saving/run | Annual net value | ROI | Volume break-even |
|---|---:|---:|---:|---:|
{economic_rows}

Token savings alone do not recover realistic development cost in the low case. High-case value is
dominated by assumed human time and avoided failures and therefore requires pilot validation.

## 15. Reliability, safety, and failure analysis

M3 critical-violation runs: `{candidate["critical_violation_runs"]}`. Failure categories:
`{json.dumps(failure_counts, sort_keys=True)}`. Determinism and eight-cycle regeneration are exact
checks. Scope canaries, forbidden facts, evidence associations, omission notices, and protected
spans are graded independently.

## 16. Portability and generalization

Portability is **NOT EVALUATED**: zero live model families and zero exact provider tokenizers were
tested. CLI availability or an environment credential does not constitute evaluation authorization.
Synthetic performance cannot establish cross-provider or small-model generalization.

## 17. Market validation status

Market pull is **NOT EVALUATED**. There are no repository-backed design-partner interviews, pilots,
retention cohorts, willingness-to-pay responses, or verified cost-avoidance records. The supplied
pilot schema, usage schema, interview guide, and questionnaire define how to collect them without
inventing MP.

## 18. Go/no-go threshold table

| Threshold | Value | Result | Classification | Evidence |
|---|---:|---|---|---|
{threshold_rows}

## 19. Failure examples and root causes

Sliding windows predictably lose early hard constraints at medium and long horizons. A 200-token
rolling summary and M1 can omit optional failed-attempt or evidence units. Full history retains
facts but includes large obsolete/noise sets, lowering selective evidence precision. M3 mitigates
optional evidence loss but pays save, validation, query, and evidence-expansion overhead.

## 20. Sensitivity analysis

Token value changes with reuse count and horizon; see `aggregate.json` by horizon/category and the
break-even chart. Economic results range from low to high assumptions. The highest offline marginal
token value appears in `{best_category}`, but one synthetic template per category is not a market
segment estimate.

## 21. Threats to validity

- Deterministic information availability is not actual model continuation quality.
- The rolling summary is a transparent offline proxy, not provider-generated prose.
- Local lexical counts are not billed provider tokens, cached tokens, or reasoning tokens.
- Synthetic events are cleaner than real conversations and may favor explicit semantic labels.
- Latencies are local single-process measurements and not service SLO evidence.
- No blinded human ratings or inter-rater agreement were collected.
- Market and production usage evidence is absent.

## 22. Recommended next experiments

Run a preregistered, explicitly budgeted fresh-session experiment with at least 30 paired runs for
B0, B2, and M3 on one OpenAI and one Anthropic model, using hidden held-out traces and blinded human
review. Capture provider usage, latency, retry behavior, and grader agreement.

## 23. Product and architectural recommendations

Do not integrate M3 into automatic production context yet. Preserve semantic checkpoints as a
candidate for high-noise, multi-session, evidence-sensitive workflows; deprioritize short or
single-use tasks where save overhead cannot amortize. A critical integrity failure in live testing,
negative lifecycle TES after three reuses, or task-quality decline beyond five points would falsify
the current hypothesis.

## 24. Reproduction instructions

```bash
uv run python -m scripts.run_viability_evaluation --run-id <new-run-id>
uv run pytest -q tests/unit/test_viability_evaluation.py \
  tests/evals/test_lifecycle_viability_evaluation.py
npm run check
```

Use a new run ID; the writer refuses to overwrite an existing result directory.

## 25. Complete artifact index

- [Raw append-only run log](raw-runs.jsonl)
- [Per-run metrics](per-run-metrics.csv)
- [Aggregate metrics](aggregate.json)
- [Evaluation configuration](evaluation-config.json)
- [Environment metadata](environment.json)
- [Reproducibility manifest](reproducibility-manifest.json)
- [Human-review score sheet](human-review.csv)
- [Blind human-review packets](blind-review-packets.jsonl)
- [Human-review condition key](human-review-key.jsonl)
{artifact_lines}

### Final decision answers

- **High-cost, frequent problem?** Missing market and usage evidence.
- **Better than rolling summary?** At the same 200-token memory policy, M1's offline availability delta is {rolling_same_budget_delta:+.3f}; M3's expanded-context delta is {rolling_retrieval_delta:+.3f}. Live task quality is unmeasured.
- **Lower total tokens?** The estimated paired-ratio median TES versus B0 is {tes:.1%}; the ratio of condition medians is {ratio_of_medians:.1%}.
- **Preserves task completion?** Deterministic context proxy is {success:.3f}; actual completion is unmeasured.
- **Break-even?** Median {_fmt(break_even["median_reuses"])} reuses in scenarios with a positive denominator.
- **Benefiting workloads?** Long, noisy, reused, evidence-sensitive handoffs are the hypothesis; short/single-use work may not amortize saves.
- **Largest risk?** Explicit synthetic compilation may not transfer to ambiguous real traces or live-model behavior.
- **Falsification evidence?** Critical integrity errors, >5-point live quality loss, or nonpositive TES after realistic reuse.
- **Highest-value next action?** The budgeted cross-provider paired fresh-session experiment above.
- **Recommendation:** INSUFFICIENT EVIDENCE; validate before further product integration.
"""


def verify_saved_run_log(path: Path, aggregate: dict[str, Any]) -> bool:
    """Recompute primary counts and token means from a saved append-only JSONL log."""

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    if len(records) != aggregate["run_count"]:
        return False
    available = [record for record in records if record["available"]]
    if len(available) != aggregate["available_run_count"]:
        return False
    grouped: defaultdict[str, list[int]] = defaultdict(list)
    for record in available:
        account = cast(dict[str, Any], record["token_account"])
        grouped[str(record["condition"])].append(int(account["total"]))
    conditions = cast(dict[str, dict[str, Any]], aggregate["conditions"])
    for condition, values in grouped.items():
        expected = _metric(conditions[condition], "lifecycle_tokens", "mean")
        if abs(statistics.fmean(values) - expected) > 1e-9:
            return False
    return True


def _write_jsonl(path: Path, runs: tuple[EvaluationRun, ...]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        for run in runs:
            handle.write(json.dumps(_run_dict(run), sort_keys=True, separators=(",", ":")))
            handle.write("\n")


def _write_csv(path: Path, runs: tuple[EvaluationRun, ...]) -> None:
    rows = [_flat_run(run) for run in runs]
    fieldnames = list(rows[0]) if rows else []
    with path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _write_human_review_artifacts(directory: Path, runs: tuple[EvaluationRun, ...]) -> None:
    available = sorted(
        (run for run in runs if run.available and run.context is not None),
        key=lambda run: hashlib.sha256(run.run_id.encode("utf-8")).hexdigest(),
    )
    with (
        (directory / "blind-review-packets.jsonl").open("x", encoding="utf-8") as packets,
        (directory / "human-review-key.jsonl").open("x", encoding="utf-8") as key,
    ):
        for index, run in enumerate(available, start=1):
            blind_id = f"review-{index:04d}"
            packets.write(
                json.dumps(
                    {
                        "blind_review_id": blind_id,
                        "task_prompt": run.task_prompt,
                        "candidate_context": run.context,
                        "rubric": "score 0-4; cite evidence; flag critical error",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
            key.write(
                json.dumps(
                    {
                        "blind_review_id": blind_id,
                        "run_id": run.run_id,
                        "condition": run.condition.value,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
    with (directory / "human-review.csv").open("x", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            (
                "blind_review_id",
                "scenario_id",
                "reuse_count",
                "rubric_score_0_to_4",
                "critical_error_y_n",
                "evidence",
                "reviewer_id",
                "reviewed_at",
            )
        )
        for index, run in enumerate(available, start=1):
            writer.writerow(
                (f"review-{index:04d}", run.scenario_id, run.reuse_count, "", "", "", "", "")
            )


def _run_dict(run: EvaluationRun) -> dict[str, object]:
    grade: dict[str, object] | None = None
    if run.grade is not None:
        grade = asdict(run.grade)
    return {
        "run_id": run.run_id,
        "pair_id": run.pair_id,
        "order_index": run.order_index,
        "scenario_id": run.scenario_id,
        "template_id": run.template_id,
        "category": run.category,
        "horizon": run.horizon.value,
        "reuse_count": run.reuse_count,
        "condition": run.condition.value,
        "available": run.available,
        "unavailable_reason": run.unavailable_reason,
        "execution_failure": run.execution_failure,
        "task_prompt": run.task_prompt,
        "context": run.context,
        "token_account": run.token_account.to_dict(),
        "context_tokens_per_reuse": run.context_tokens_per_reuse,
        "save_tokens": run.save_tokens,
        "validation_tokens": run.validation_tokens,
        "latency_ms": run.latency_ms,
        "grade": grade,
        "lme_gated": run.lme_gated,
        "lme_ungated": run.lme_ungated,
        "task_impact_proxy": run.task_impact_proxy,
        "provider": run.provider,
        "model": run.model,
        "estimated_cost_usd": run.estimated_cost_usd,
        "provider_reported_cost_usd": run.provider_reported_cost_usd,
    }


def _flat_run(run: EvaluationRun) -> dict[str, object]:
    grade = run.grade
    return {
        "run_id": run.run_id,
        "pair_id": run.pair_id,
        "order_index": run.order_index,
        "scenario_id": run.scenario_id,
        "category": run.category,
        "horizon": run.horizon.value,
        "reuse_count": run.reuse_count,
        "condition": run.condition.value,
        "available": run.available,
        "execution_failure": run.execution_failure or "",
        "lifecycle_tokens": run.token_account.total,
        "input_tokens": run.token_account.input_total,
        "output_tokens": run.token_account.output_total,
        "context_tokens_per_reuse": run.context_tokens_per_reuse,
        "latency_ms": run.latency_ms,
        "continuation_fidelity": "" if grade is None else grade.continuation_fidelity,
        "required_knowledge_retention": "" if grade is None else grade.required_knowledge_retention,
        "temporal_supersession_accuracy": ""
        if grade is None
        else grade.temporal_supersession_accuracy,
        "evidence_attribution_fidelity": ""
        if grade is None
        else grade.evidence_attribution_fidelity,
        "retrieval_f1": "" if grade is None else grade.retrieval_f1,
        "false_memory_rate": "" if grade is None else grade.false_memory_rate,
        "critical_violation_count": "" if grade is None else len(grade.critical_violations),
        "lme_gated": "" if run.lme_gated is None else run.lme_gated,
        "lme_ungated": "" if run.lme_ungated is None else run.lme_ungated,
        "task_impact_proxy": "" if run.task_impact_proxy is None else run.task_impact_proxy,
        "token_measurement_source": run.token_account.source.value,
        "tokenizer_id": run.token_account.tokenizer_id,
    }


def _config_dict(config: EvaluationConfig) -> dict[str, object]:
    raw = asdict(config)
    return cast(dict[str, object], raw)


def _write_json(path: Path, value: object) -> None:
    _write_exclusive(path, json.dumps(value, sort_keys=True, indent=2) + "\n")


def _write_exclusive(path: Path, value: str) -> None:
    with path.open("x", encoding="utf-8") as handle:
        handle.write(value)


def _write_chart(path: Path, value: str) -> Path:
    _write_exclusive(path, value)
    return path


def _bar_svg(
    title: str,
    values: dict[str, float],
    *,
    maximum: float | None = None,
    allow_negative: bool = False,
) -> str:
    width, height = 1100, max(420, 70 + 34 * len(values))
    left, right = 350, 40
    usable = width - left - right
    observed_max = max((abs(value) for value in values.values()), default=1.0)
    scale_max = maximum or observed_max or 1.0
    zero_x = left + (usable / 2 if allow_negative else 0)
    body: list[str] = []
    for index, (label, value) in enumerate(values.items()):
        y = 55 + index * 34
        available = usable / 2 if allow_negative else usable
        bar_width = min(available, abs(value) / scale_max * available)
        x = zero_x - bar_width if allow_negative and value < 0 else zero_x
        color = "#b42318" if value < 0 else "#1570ef"
        body.append(f'<text x="12" y="{y + 16}" font-size="12">{html.escape(label)}</text>')
        body.append(
            f'<rect x="{x:.1f}" y="{y}" width="{bar_width:.1f}" height="22" fill="{color}"/>'
        )
        body.append(
            f'<text x="{x + bar_width + 6:.1f}" y="{y + 16}" font-size="12">{value:.3g}</text>'
        )
    return _svg(title, width, height, "".join(body))


def _stacked_bar_svg(title: str, values: dict[str, dict[str, float]]) -> str:
    totals = {name: sum(parts.values()) for name, parts in values.items()}
    maximum = max(totals.values(), default=1.0) or 1.0
    width, height = 1100, max(420, 80 + 42 * len(values))
    colors = ("#1570ef", "#12b76a", "#f79009", "#7a5af8", "#ee46bc", "#667085")
    body: list[str] = []
    for row, (name, parts) in enumerate(values.items()):
        y = 60 + row * 42
        x = 300.0
        body.append(f'<text x="12" y="{y + 17}" font-size="12">{html.escape(name)}</text>')
        for index, (_, value) in enumerate(parts.items()):
            segment = value / maximum * 740
            body.append(
                f'<rect x="{x:.1f}" y="{y}" width="{segment:.1f}" height="24" fill="{colors[index % len(colors)]}"/>'
            )
            x += segment
        body.append(f'<text x="{x + 6:.1f}" y="{y + 17}" font-size="12">{totals[name]:.0f}</text>')
    return _svg(title, width, height, "".join(body))


def _scatter_svg(
    title: str,
    labels: list[str],
    xs: list[float],
    ys: list[float],
    x_label: str,
    y_label: str,
    *,
    highlights: set[str] | None = None,
) -> str:
    width, height = 1000, 620
    left, top, plot_width, plot_height = 100, 70, 800, 460
    minimum_x, maximum_x = min(xs, default=0.0), max(xs, default=1.0)
    minimum_y, maximum_y = min(ys, default=0.0), max(ys, default=1.0)
    if minimum_x == maximum_x:
        maximum_x += 1.0
    if minimum_y == maximum_y:
        maximum_y += 1.0
    body = [
        f'<line x1="{left}" y1="{top + plot_height}" x2="{left + plot_width}" y2="{top + plot_height}" stroke="#344054"/>',
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_height}" stroke="#344054"/>',
        f'<text x="{left + plot_width / 2}" y="590" text-anchor="middle" font-size="13">{html.escape(x_label)}</text>',
        f'<text x="18" y="{top + plot_height / 2}" transform="rotate(-90 18 {top + plot_height / 2})" text-anchor="middle" font-size="13">{html.escape(y_label)}</text>',
    ]
    highlight_values = highlights or set()
    for label, x_value, y_value in zip(labels, xs, ys, strict=True):
        x = left + (x_value - minimum_x) / (maximum_x - minimum_x) * plot_width
        y = top + plot_height - (y_value - minimum_y) / (maximum_y - minimum_y) * plot_height
        color = "#12b76a" if label in highlight_values else "#1570ef"
        radius = 8 if label in highlight_values else 6
        body.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius}" fill="{color}"/>')
        body.append(
            f'<text x="{x + 9:.1f}" y="{y - 7:.1f}" font-size="10">{html.escape(label)}</text>'
        )
    return _svg(title, width, height, "".join(body))


def _svg(title: str, width: int, height: int, body: str) -> str:
    escaped = html.escape(title)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        f'<title>{escaped}</title><rect width="100%" height="100%" fill="white"/>'
        f'<text x="20" y="30" font-size="18" font-weight="600">{escaped}</text>{body}</svg>\n'
    )


def _mean_token_components(runs: tuple[EvaluationRun, ...]) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[EvaluationRun]] = defaultdict(list)
    for run in runs:
        if run.available:
            grouped[run.condition.value].append(run)
    result: dict[str, dict[str, float]] = {}
    for condition, items in sorted(grouped.items()):
        components: defaultdict[str, float] = defaultdict(float)
        for run in items:
            for key, value in run.token_account.to_dict(include_metadata=False).items():
                components[key] += float(cast(int, value))
        result[condition] = {
            key: value / len(items) for key, value in components.items() if value > 0
        }
    return result


def _metric(value: dict[str, Any], *keys: str) -> float:
    current: Any = value
    for key in keys:
        current = current[key]
    return float(current or 0.0)


def _fmt(value: object) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.3f}"
    return str(value)


def _money(value: object) -> str:
    return f"${float(cast(float, value)):,.2f}"


def _sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
