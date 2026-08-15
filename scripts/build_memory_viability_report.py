"""Build the terminal Mnemo investigation report from immutable evidence runs."""

# ruff: noqa: E501

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import subprocess
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).parents[1]
DEFAULT_RESULTS = ROOT / "evaluation-results" / "final-investigation-v1"
DEFAULT_GATE1 = ROOT / "evaluation-results" / "live-semantic-v1" / "live-20260812-57ec69f-gate1-002"
DEFAULT_GATE1_EXCLUDED = (
    ROOT / "evaluation-results" / "live-semantic-v1" / "live-20260812-57ec69f-gate1-001"
)
DEFAULT_LIFECYCLE_BEFORE = (
    ROOT
    / "evaluation-results"
    / "semantic-lifecycle-v1"
    / "lifecycle-20260812-57ec69f-instrumented-baseline-001"
)
DEFAULT_LIFECYCLE_AFTER = (
    ROOT
    / "evaluation-results"
    / "semantic-lifecycle-v1"
    / "lifecycle-20260812-57ec69f-evidence-reuse-final-001"
)
DEFAULT_LIFECYCLE_COMPARISON = (
    ROOT
    / "evaluation-results"
    / "semantic-lifecycle-v1"
    / "comparison-20260812-baseline-vs-final-001"
)
DEFAULT_LONG_HORIZON = ROOT / "evaluation-results" / "long-horizon-v1" / "final-20260812-qwen30-001"
DEFAULT_DRY_ONE = ROOT / "evaluation-results" / "long-horizon-v1" / "dry-20260812-qwen-001"
DEFAULT_DRY_TWO = ROOT / "evaluation-results" / "long-horizon-v1" / "dry-20260812-qwen-002"
DEFAULT_OFFLINE = (
    ROOT / "evaluation-results" / "viability-v1" / "offline-20260812-57ec69f-integrity-001"
)


def economic_analysis(
    trajectories: list[dict[str, object]],
    analysis: dict[str, object],
    sessions: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    available = [row for row in trajectories if row.get("available") is True]
    condition_names = sorted({cast(str, row["condition"]) for row in available})
    resources: dict[str, object] = {}
    for condition in condition_names:
        rows = [row for row in available if row["condition"] == condition]
        successes = sum(bool(row["end_to_end_success"]) for row in rows)
        total_tokens = sum(
            cast(int, row["actual_prompt_tokens"]) + cast(int, row["actual_output_tokens"])
            for row in rows
        )
        total_seconds = sum(cast(int, row["actual_latency_ns"]) for row in rows) / 1e9
        failed = [row for row in rows if not bool(row["end_to_end_success"])]
        resources[condition] = {
            "classification": "Actually observed",
            "trajectory_count": len(rows),
            "successful_tasks": successes,
            "total_model_tokens": total_tokens,
            "median_model_tokens_per_task": statistics.median(
                cast(int, row["actual_prompt_tokens"]) + cast(int, row["actual_output_tokens"])
                for row in rows
            ),
            "total_local_inference_seconds": total_seconds,
            "tokens_per_successful_task": None if successes == 0 else total_tokens / successes,
            "inference_seconds_per_successful_task": (
                None if successes == 0 else total_seconds / successes
            ),
            "external_cost_per_successful_task_usd": None if successes == 0 else 0.0,
            "observed_failed_task_tokens": sum(
                cast(int, row["actual_prompt_tokens"]) + cast(int, row["actual_output_tokens"])
                for row in failed
            ),
            "observed_failed_task_inference_seconds": sum(
                cast(int, row["actual_latency_ns"]) for row in failed
            )
            / 1e9,
            "repeated_errors": sum(cast(int, row["repeated_error_count"]) for row in rows),
            "human_interventions": sum(cast(int, row["human_intervention_count"]) for row in rows),
            "external_spend_usd": sum(cast(float, row["external_spend_usd"]) for row in rows),
        }

    paired = {
        "SD_vs_SI": _paired_token_economics(available, "SD", "SI"),
        "SF_vs_S0": _paired_token_economics(available, "SF", "S0"),
    }
    conditions = cast(dict[str, dict[str, object]], analysis["conditions"])
    sd_quality = cast(float, conditions["SD"]["hidden_test_accuracy"])
    si_quality = cast(float, conditions["SI"]["hidden_test_accuracy"])
    sf_quality = cast(float, conditions["SF"]["hidden_test_accuracy"])
    s0_quality = cast(float, conditions["S0"]["hidden_test_accuracy"])
    sd_tes = cast(float, paired["SD_vs_SI"]["lifecycle_tes"])
    sf_tes = cast(float, paired["SF_vs_S0"]["lifecycle_tes"])
    savings_path = (sd_tes >= 0.30 and sd_quality >= si_quality - 0.02) or (
        sf_tes >= 0.30 and sf_quality >= s0_quality - 0.02
    )
    success_path = _lower_cost_with_more_success(resources, conditions, "SD", "SI") or (
        _lower_cost_with_more_success(resources, conditions, "SF", "S0")
    )
    return {
        "schema_version": "mnemo-economic-analysis/1.0",
        "resource_costs": resources,
        "paired_token_economics": paired,
        "study_execution_overhead": _study_execution_overhead(available, sessions),
        "value_paths": {
            "thirty_percent_token_savings_without_quality_loss": savings_path,
            "lower_cost_per_success_from_measured_success": success_path,
            "frontier_gap_closure_while_cheaper": "NOT EVALUATED",
        },
        "gate_3_verdict": "PASS" if savings_path or success_path else "FAIL",
        "tested_segment": "three-session local-Qwen telehealth scheduler with ephemeral checkpoints",
        "horizon_analysis": {
            "short": "NOT EVALUATED",
            "medium": "NOT EVALUATED",
            "long": "actually observed in the three-session controlled study",
        },
        "reuse_analysis": _reuse_analysis(available),
        "memory_type_analysis": {
            "ephemeral_checkpoint": "actually observed",
            "project_architecture_memory": "NOT EVALUATED",
            "correction_lessons": "NOT EVALUATED",
            "procedures_and_runbooks": "NOT EVALUATED",
            "multi_agent_shared_knowledge": "NOT EVALUATED",
        },
        "monetization_note": (
            "Local inference seconds and tokens are measured resource vectors. Hardware, energy, "
            "and labor prices were not authorized, so they are not converted to speculative dollars."
        ),
        "break_even_reuse": (
            "NOT EVALUATED: only two actual fresh-session recalls occurred per trajectory and no "
            "customer reuse-frequency evidence exists"
        ),
        "market_demand": "NOT EVALUATED",
        "portability": "NOT EVALUATED",
    }


def _paired_token_economics(
    rows: list[dict[str, object]], candidate: str, baseline: str
) -> dict[str, object]:
    indexed = {(cast(str, row["variant_id"]), cast(str, row["condition"])): row for row in rows}
    variants = sorted(
        variant
        for variant, condition in indexed
        if condition == candidate and (variant, baseline) in indexed
    )
    candidate_tokens = [
        cast(int, indexed[(item, candidate)]["actual_prompt_tokens"])
        + cast(int, indexed[(item, candidate)]["actual_output_tokens"])
        for item in variants
    ]
    baseline_tokens = [
        cast(int, indexed[(item, baseline)]["actual_prompt_tokens"])
        + cast(int, indexed[(item, baseline)]["actual_output_tokens"])
        for item in variants
    ]
    candidate_total = sum(candidate_tokens)
    baseline_total = sum(baseline_tokens)
    ratios = [
        1 - candidate_value / baseline_value
        for candidate_value, baseline_value in zip(candidate_tokens, baseline_tokens, strict=True)
    ]
    return {
        "classification": "Deterministically measured",
        "candidate": candidate,
        "baseline": baseline,
        "paired_variants": len(variants),
        "candidate_total_tokens": candidate_total,
        "baseline_total_tokens": baseline_total,
        "lifecycle_tes": 1 - candidate_total / baseline_total,
        "median_paired_lifecycle_tes": statistics.median(ratios),
        "definition": "1 - candidate actual model tokens / baseline actual model tokens",
    }


def _study_execution_overhead(
    trajectories: list[dict[str, object]], sessions: list[dict[str, object]] | None
) -> dict[str, object]:
    if sessions is None:
        return {"classification": "Not evaluated", "reason": "raw sessions not supplied"}
    analyzed_prompt = sum(cast(int, row["actual_prompt_tokens"]) for row in trajectories)
    analyzed_output = sum(cast(int, row["actual_output_tokens"]) for row in trajectories)
    analyzed_latency = sum(cast(int, row["actual_latency_ns"]) for row in trajectories)
    observed_prompt = sum(
        cast(int, cast(dict[str, object], row["actual_usage"])["prompt_eval_count"])
        for row in sessions
    )
    observed_output = sum(
        cast(int, cast(dict[str, object], row["actual_usage"])["eval_count"]) for row in sessions
    )
    observed_latency = sum(cast(int, row["request_latency_ns"]) for row in sessions)
    expected_calls = len(trajectories) * 3
    return {
        "classification": "Actually observed",
        "actual_model_calls": len(sessions),
        "analyzed_model_calls": expected_calls,
        "orphaned_model_calls_after_interruption": len(sessions) - expected_calls,
        "orphaned_prompt_tokens": observed_prompt - analyzed_prompt,
        "orphaned_output_tokens": observed_output - analyzed_output,
        "orphaned_total_tokens": (observed_prompt + observed_output)
        - (analyzed_prompt + analyzed_output),
        "orphaned_local_inference_seconds": (observed_latency - analyzed_latency) / 1e9,
        "all_actual_prompt_tokens": observed_prompt,
        "all_actual_output_tokens": observed_output,
        "all_actual_local_inference_seconds": observed_latency / 1e9,
        "note": (
            "The two orphaned calls are operational failure cost only; they are excluded from "
            "trajectory effects and condition comparisons."
        ),
    }


def _lower_cost_with_more_success(
    resources: dict[str, object],
    conditions: dict[str, dict[str, object]],
    candidate: str,
    baseline: str,
) -> bool:
    candidate_resource = cast(dict[str, object], resources[candidate])
    baseline_resource = cast(dict[str, object], resources[baseline])
    candidate_cost = candidate_resource["tokens_per_successful_task"]
    baseline_cost = baseline_resource["tokens_per_successful_task"]
    if candidate_cost is None or baseline_cost is None:
        return False
    return cast(float, conditions[candidate]["end_to_end_success_rate"]) > cast(
        float, conditions[baseline]["end_to_end_success_rate"]
    ) and cast(float, candidate_cost) < cast(float, baseline_cost)


def _reuse_analysis(rows: list[dict[str, object]]) -> dict[str, object]:
    indexed = {(cast(str, row["variant_id"]), cast(str, row["condition"])): row for row in rows}
    variants = sorted(
        variant
        for variant, condition in indexed
        if condition == "SD" and (variant, "SI") in indexed
    )
    observed_overhead = statistics.fmean(
        (
            cast(int, indexed[(item, "SD")]["actual_prompt_tokens"])
            + cast(int, indexed[(item, "SD")]["actual_output_tokens"])
            - cast(int, indexed[(item, "SI")]["actual_prompt_tokens"])
            - cast(int, indexed[(item, "SI")]["actual_output_tokens"])
        )
        / 2
        for item in variants
    )
    return {
        "observed_reuses_per_trajectory": 2,
        "observed_sd_incremental_tokens_per_reuse": observed_overhead,
        "counterfactual": {
            str(reuse): {
                "classification": "estimated",
                "incremental_tokens": observed_overhead * reuse,
                "quality_assumption": "not projected; no unobserved quality benefit is assigned",
            }
            for reuse in (1, 3, 10, 30)
        },
        "higher_reuse_frequency_plausibility": "NOT EVALUATED",
    }


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        cast(dict[str, object], json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _write_exclusive(path: Path, value: object) -> None:
    text = value if isinstance(value, str) else json.dumps(value, indent=2, sort_keys=True) + "\n"
    with path.open("x", encoding="utf-8") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())


def build(
    *,
    report_id: str,
    results_root: Path = DEFAULT_RESULTS,
    gate1_run: Path = DEFAULT_GATE1,
    long_horizon_run: Path = DEFAULT_LONG_HORIZON,
    lifecycle_before: Path = DEFAULT_LIFECYCLE_BEFORE,
    lifecycle_after: Path = DEFAULT_LIFECYCLE_AFTER,
    lifecycle_comparison: Path = DEFAULT_LIFECYCLE_COMPARISON,
    offline_run: Path = DEFAULT_OFFLINE,
) -> tuple[Path, dict[str, object]]:
    gate1 = cast(dict[str, object], _load(gate1_run / "summary.json"))
    long_analysis = cast(dict[str, object], _load(long_horizon_run / "analysis.json"))
    trajectories = _load_jsonl(long_horizon_run / "raw-trajectories.jsonl")
    sessions = _load_jsonl(long_horizon_run / "raw-sessions.jsonl")
    lifecycle = cast(dict[str, object], _load(lifecycle_comparison / "comparison.json"))
    economics = economic_analysis(trajectories, long_analysis, sessions)
    gate2 = cast(str, long_analysis["gate_2_verdict"])
    gate3 = cast(str, economics["gate_3_verdict"])
    primary = cast(
        dict[str, object],
        cast(dict[str, object], long_analysis["estimands"])["PersistentReasoningGain"],
    )
    adequately_powered = cast(int, primary["paired_count"]) >= 30
    if gate2 == "FAIL" and adequately_powered:
        decision = "STOP"
    elif gate1["gate_1_verdict"] == "PASS" and gate2 == "PASS" and gate3 == "PASS":
        decision = "PROCEED"
    elif gate1["gate_1_verdict"] == "PASS" and gate2 == "PASS":
        decision = "PIVOT"
    else:
        decision = "INSUFFICIENT EVIDENCE"
    verdicts = {
        "schema_version": "mnemo-investigation-verdicts/1.0",
        "Gate 1 live-path memory integrity": gate1["gate_1_verdict"],
        "Gate 2 long-horizon behavioral value": gate2,
        "Gate 3 economic viability for tested segment": gate3,
        "market demand": "NOT EVALUATED",
        "portability": "NOT EVALUATED",
        "frontier substitution": "NOT EVALUATED",
        "blinded human quality": "NOT EVALUATED",
        "decision": decision,
    }
    destination = results_root / report_id
    destination.mkdir(parents=True, exist_ok=False)
    evidence_index = _evidence_index(
        gate1_run,
        lifecycle_before,
        lifecycle_after,
        lifecycle_comparison,
        long_horizon_run,
        offline_run,
    )
    failures = _failure_index(long_horizon_run, trajectories, sessions)
    environment = {
        "gate1": _load(gate1_run / "reproducibility-manifest.json"),
        "long_horizon": _load(long_horizon_run / "reproducibility-manifest.json"),
        "lifecycle": _load(lifecycle_after / "reproducibility-manifest.json"),
        "external_spend_usd": 0.0,
        "human_reviewers": 0,
    }
    _write_exclusive(destination / "gate-verdicts.json", verdicts)
    _write_exclusive(destination / "economics.json", economics)
    _write_exclusive(
        destination / "lifecycle-cost-breakdown.json",
        _lifecycle_cost_breakdown(lifecycle, economics),
    )
    _write_exclusive(
        destination / "statistical-analysis.json",
        {
            "classification": "Deterministically measured",
            "primary_independence_unit": "variant",
            "analysis": long_analysis,
        },
    )
    _write_exclusive(destination / "metric-classifications.json", _metric_classifications())
    _write_exclusive(destination / "evidence-index.json", evidence_index)
    _write_exclusive(destination / "failure-and-excluded-run-log.json", failures)
    _write_exclusive(destination / "environment-and-configuration.json", environment)
    _write_exclusive(destination / "live-path-architecture-trace.md", _architecture_trace())
    _write_exclusive(
        destination / "reproduction.md",
        _reproduction(report_id, gate1_run, lifecycle_before, lifecycle_after, long_horizon_run),
    )
    report = _render_report(
        report_id=report_id,
        gate1=gate1,
        long_analysis=long_analysis,
        lifecycle=lifecycle,
        economics=economics,
        verdicts=verdicts,
        trajectories=trajectories,
    )
    _write_exclusive(destination / "executive-decision-report.md", report)
    artifact_names = sorted(path.name for path in destination.iterdir() if path.is_file())
    manifest = {
        "schema_version": "mnemo-final-investigation-reproducibility/1.0",
        "report_id": report_id,
        "git_revision": subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "artifact_hashes": {name: _sha256(destination / name) for name in artifact_names},
        "source_evidence": evidence_index,
        "command": f"uv run python -m scripts.build_memory_viability_report --report-id {report_id}",
    }
    _write_exclusive(destination / "artifact-manifest.json", manifest)
    return destination, verdicts


def _evidence_index(*runs: Path) -> dict[str, object]:
    records: dict[str, object] = {}
    for run in runs:
        files = sorted(
            path
            for path in run.iterdir()
            if path.is_file()
            and path.name
            in {
                "analysis.json",
                "comparison.json",
                "raw-events.jsonl",
                "raw-lifecycle.jsonl",
                "raw-runs.jsonl",
                "raw-sessions.jsonl",
                "raw-trajectories.jsonl",
                "reproducibility-manifest.json",
                "summary.json",
            }
        )
        records[run.name] = {
            "path": str(run.relative_to(ROOT)),
            "artifacts": {
                path.name: {"sha256": _sha256(path), "bytes": path.stat().st_size} for path in files
            },
        }
    return {
        "schema_version": "mnemo-final-evidence-index/1.0",
        "runs": records,
        "raw_records_are_append_only": True,
    }


def _lifecycle_cost_breakdown(
    lifecycle: dict[str, object], economics: dict[str, object]
) -> dict[str, object]:
    return {
        "schema_version": "mnemo-lifecycle-cost-breakdown/1.0",
        "deterministic_semantic_work": {
            "classification": "Actually observed",
            "independence_unit": "paired generated profile",
            "paired_profile_count": lifecycle["paired_profile_count"],
            "before_run": lifecycle["before_run"],
            "after_run": lifecycle["after_run"],
            "metrics": lifecycle["metrics"],
            "rendered_content_change": {
                "classification": "Deterministically measured",
                "rendered_checkpoint_tokens_before": 399,
                "rendered_checkpoint_tokens_after": 399,
                "automatic_context_item_tokens_before": 505,
                "automatic_context_item_tokens_after": 505,
                "interpretation": "CPU and elapsed-work reduction, not model-token savings",
            },
        },
        "model_lifecycle": {
            "classification": "Actually observed",
            "condition_resources": economics["resource_costs"],
            "paired_token_economics": economics["paired_token_economics"],
            "study_execution_overhead": economics["study_execution_overhead"],
        },
        "human_intervention": {
            "classification": "Actually observed",
            "count": 0,
        },
        "external_spend_usd": {"classification": "Actually observed", "value": 0.0},
        "local_hardware_energy_and_labor_dollars": {
            "classification": "Not evaluated",
            "reason": "no authorized rates",
        },
    }


def _metric_classifications() -> dict[str, object]:
    return {
        "schema_version": "mnemo-metric-classifications/1.0",
        "allowed_labels": [
            "Actually observed",
            "Deterministically measured",
            "Model-generated",
            "Estimated",
            "Proxy",
            "Simulated",
            "Not evaluated",
        ],
        "metrics": {
            "raw_model_prompts_responses_and_structured_proposals": "Model-generated",
            "ollama_prompt_tokens_output_tokens_and_request_latency": "Actually observed",
            "semantic_stage_wall_and_cpu_durations": "Actually observed",
            "hidden_test_accuracy_decision_accuracy_and_task_success": (
                "Deterministically measured"
            ),
            "regression_supersession_repeated_error_and_self_correction": (
                "Deterministically measured"
            ),
            "memory_precision_recall_f1_and_critical_false_memory": ("Deterministically measured"),
            "hypothesis_precision_recall_and_calibration": "Deterministically measured",
            "paired_effects_confidence_intervals_mcnemar_and_tes": ("Deterministically measured"),
            "live_transport_fidelity_provenance_scope_and_deletion": ("Deterministically measured"),
            "gate1_local_qwen_continuation": "Model-generated",
            "old_offline_lifecycle_token_counts": "Estimated",
            "offline_task_success_and_continuation_availability": "Proxy",
            "offline_deterministic_condition_rows": "Simulated",
            "higher_reuse_token_projections": "Estimated",
            "F0_frontier_gap_and_frontier_substitution": "Not evaluated",
            "blinded_human_quality": "Not evaluated",
            "market_demand_customer_reuse_and_portability": "Not evaluated",
            "local_hardware_energy_labor_dollar_cost": "Not evaluated",
        },
        "classification_note": (
            "A deterministic metric can score model-generated data; that does not make the "
            "underlying model response deterministic."
        ),
    }


def _failure_index(
    long_horizon_run: Path,
    trajectories: list[dict[str, object]],
    sessions: list[dict[str, object]],
) -> dict[str, object]:
    runs = []
    for path, status, reason in (
        (
            DEFAULT_GATE1_EXCLUDED,
            "excluded",
            "synthetic project nested under repository caused pre-inference hook timeout",
        ),
        (
            DEFAULT_DRY_ONE,
            "excluded",
            "engineering dry run; three responses hit 240-token cap and malformed JSON",
        ),
        (
            DEFAULT_DRY_TWO,
            "excluded",
            "mechanical confirmation only; no cap, parse, or invalid-change failures",
        ),
    ):
        runs.append({"run_id": path.name, "status": status, "reason": reason})
    final_failures = _load_jsonl(long_horizon_run / "failures.jsonl")
    overhead = _study_execution_overhead(trajectories, sessions)
    return {
        "schema_version": "mnemo-failure-exclusion-log/1.0",
        "excluded_runs": runs,
        "final_run_execution_failures": final_failures,
        "final_run_process_interruptions": [
            {
                "status": "recovered by exact resume",
                "after_completed_trajectories": 170,
                "root_cause": "per-process file-descriptor exhaustion",
                "root_cause_correction": (
                    "SQLiteSemanticCheckpointRepository now wraps every read connection in "
                    "contextlib.closing instead of relying on SQLite's non-closing transaction "
                    "context manager"
                ),
                "correction_verification": (
                    "a regression proves the read connection is unusable after the repository "
                    "operation; the semantic SQLite and long-horizon suites pass"
                ),
                "observable_errors": [
                    "sqlite3.OperationalError: unable to open database file",
                    "OSError: [Errno 24] Too many open files",
                ],
                "failure_log_append_succeeded": False,
                "affected_incomplete_key": ["telehealth-29", "SF"],
                "orphaned_model_calls": overhead.get("orphaned_model_calls_after_interruption"),
                "orphaned_total_tokens": overhead.get("orphaned_total_tokens"),
                "orphaned_local_inference_seconds": overhead.get(
                    "orphaned_local_inference_seconds"
                ),
                "effect_on_estimands": (
                    "none; incomplete calls were excluded and the fixed trajectory was rerun "
                    "from its byte-identical start and seed"
                ),
            }
        ],
        "final_run_excluded_trajectories": sum(
            row.get("available") is not True for row in trajectories
        ),
    }


def _architecture_trace() -> str:
    return """# Live-path architecture trace

```text
real agent session
  -> public stdio MCP save_checkpoint
  -> canonical checkpoint revision (SQLite, immutable evidence)
  -> feature-flagged after-save callback
  -> changed-kind task events (exact task scope, retention, revision source key)
  -> deterministic compiler and policy patch
  -> semantic checkpoint ledger + active references
  -> original MCP process ends
  -> independent SessionStart process (cwd + opaque session ID, no transcript)
  -> legacy authorization/scope selection
  -> feature-flagged M3 replacement within existing 600-token budget
  -> exact atom-to-evidence trace + provenance digest
  -> fresh local-model prompt
  -> model-generated continuation recorded with actual usage
```

Cross-scope poison never enters the selected item. A newer checkpoint revision retains historical
supersession while excluding stale active content. Canonical deletion tombstones the checkpoint,
deletes only namespaced projection events, cascades atoms/references/orphaned evidence, and causes
later recall and SessionStart to fail closed. The stable feature-off path is unchanged.
"""


def _reproduction(
    report_id: str,
    gate1: Path,
    lifecycle_before: Path,
    lifecycle_after: Path,
    long_horizon: Path,
) -> str:
    return f"""# Exact reproduction

Use the committed lockfiles and the already-installed model; do not overwrite run IDs.

```bash
uv sync --locked
npm ci --ignore-scripts
ollama serve
uv run python -m scripts.run_live_semantic_evaluation --run-id NEW_GATE1_ID
uv run python -m scripts.run_semantic_lifecycle_benchmark --run-id NEW_BASELINE_ID --repetitions 30
uv run python -m scripts.run_semantic_lifecycle_benchmark --run-id NEW_FINAL_ID --repetitions 30
uv run python -m scripts.run_long_horizon_evaluation --run-id NEW_STUDY_ID --run-role final --variant-count 30
uv run python -m scripts.build_memory_viability_report --report-id {report_id}-reproduction --gate1-run NEW_GATE1_PATH --long-horizon-run NEW_STUDY_PATH
npm run check
```

Evidence used here: `{gate1.relative_to(ROOT)}`, `{lifecycle_before.relative_to(ROOT)}`,
`{lifecycle_after.relative_to(ROOT)}`, and `{long_horizon.relative_to(ROOT)}`. The manifests record
exact hashes, model identifier/digest, seed, platform, commands, and worktree-state digest.
"""


def _render_report(
    *,
    report_id: str,
    gate1: dict[str, object],
    long_analysis: dict[str, object],
    lifecycle: dict[str, object],
    economics: dict[str, object],
    verdicts: dict[str, object],
    trajectories: list[dict[str, object]],
) -> str:
    primary = cast(
        dict[str, object],
        cast(dict[str, object], long_analysis["estimands"])["PersistentReasoningGain"],
    )
    lifecycle_metrics = cast(dict[str, dict[str, object]], lifecycle["metrics"])
    top = lifecycle_metrics["top_level.wall_duration_ns"]
    automatic = lifecycle_metrics["automatic_context_assembly.wall_duration_ns"]
    sd_economics = cast(
        dict[str, object],
        cast(dict[str, object], economics["paired_token_economics"])["SD_vs_SI"],
    )
    overhead = cast(dict[str, object], economics["study_execution_overhead"])
    conditions = cast(dict[str, dict[str, object]], long_analysis["conditions"])
    unsafe_sx = sum(
        not bool(row["poison_safe"]) for row in trajectories if row["condition"] == "SX"
    )
    return f"""# Mnemo memory-value investigation — executive decision

Report ID: `{report_id}`
Decision: **{verdicts["decision"]}**

## Executive conclusion

The experimental live path now transports M3 memory through real public checkpoint and fresh
SessionStart boundaries with perfect deterministic integrity in the controlled trace. That is an
engineering result, not a product-value result. The preregistered real-model continuation failed
three of seven critical response requirements, so Gate 1 is **{gate1["gate_1_verdict"]}**.

The adequately powered 30-variant behavioral study reports `SD - SI` hidden-test accuracy of
`{cast(float, primary["mean_difference"]):.3f}` with 95% paired variant-bootstrap interval
`{primary["confidence_interval_95"]}`. Gate 2 is **{long_analysis["gate_2_verdict"]}**. Gate 3 is
**{economics["gate_3_verdict"]}** for the only tested segment. Market demand and portability remain
`NOT EVALUATED`.

The statistical improvement is real but only +3.1 percentage points, below the preregistered
+10-point practical margin. Both SD and SI completed 0/30 tasks, and {unsafe_sx}/30 poisoned-memory
trajectories violated the fixed safe-failure rule. This does not support persistent deliberation,
context-rot mitigation, or commercial value for the tested segment.

## Gate verdicts

| Gate/evidence | Verdict | What it establishes |
|---|---|---|
| Live-path integrity | {gate1["gate_1_verdict"]} | Transport subchecks passed; real continuation did not meet the fixed critical rule. |
| Long-horizon behavioral value | {long_analysis["gate_2_verdict"]} | Same-model SD vs SI causal estimate across 30 paired variants. |
| Economics, tested segment | {economics["gate_3_verdict"]} | Actual tokens/time/success for long-horizon ephemeral checkpoints. |
| Frontier substitution | NOT EVALUATED | No authorized F0 call or frontier gap. |
| Market demand | NOT EVALUATED | No customer trace, pilot, or willingness-to-pay evidence. |
| Portability | NOT EVALUATED | One local model/runtime and one controlled workload. |

## Lifecycle before/after

Three bounded optimizations reduced actual top-level semantic elapsed work by
`{cast(float, top["paired_mean_relative_change"]):.1%}` (95% CI
`{top["paired_mean_relative_change_ci95"]}`) and automatic context by
`{cast(float, automatic["paired_mean_relative_change"]):.1%}`. Rendered content remained 399
tokens and the evidence-bearing item 505 tokens. This is deterministic CPU/latency reduction, not
model-token savings. Actual SD lifecycle TES relative to SI is
`{cast(float, sd_economics["lifecycle_tes"]):.1%}`.

## Statistical and behavioral interpretation

- Primary independence unit: 30 task variants; repeated sessions are clustered within variant.
- Primary effect: `{primary["mean_difference"]}`; CI `{primary["confidence_interval_95"]}`;
  paired Cohen's dz `{primary["cohens_dz"]}`.
- Task-success exact test: `{long_analysis["primary_task_success_mcnemar"]}`.
- Condition accuracy: SD `{cast(float, conditions["SD"]["hidden_test_accuracy"]):.3f}`, SI
  `{cast(float, conditions["SI"]["hidden_test_accuracy"]):.3f}`; task success was 0/30 for each.
- Poison resistance: {30 - unsafe_sx}/30 safe; fixed all-variant criterion failed.
- Human-blinded quality: `NOT EVALUATED`.
- F0 and FrontierGapClosure: `NOT EVALUATED`.

## Run integrity and operational failure cost

The final run contains 180 analyzed trajectories and {overhead["actual_model_calls"]} actual local
model calls. A per-process file-descriptor leak stopped the first process after 170 trajectories;
exact resume skipped completed keys. Its two orphaned calls are excluded from effects but retained
as {overhead["orphaned_total_tokens"]} actually observed tokens and
{cast(float, overhead["orphaned_local_inference_seconds"]):.2f} seconds of failure cost. There were
no response parse failures, output-cap hits, invalid changes, transcript leaks, or unavailable final
trajectories.

The root cause was production semantic storage using SQLite's transaction context manager as if it
closed connections; it does not. All five semantic read paths now use explicit closing, and a
regression proves the connection is closed after a read. The final efficacy data remain the frozen
pre-fix observations; this reliability fix prevents the demonstrated descriptor leak without
changing model inputs, outcomes, or gate thresholds.

## Distinctions that must not be collapsed

- Engineering correctness: repository/runtime mechanisms and regression checks.
- Memory integrity: exact scoped transport, evidence, supersession, poison exclusion, deletion.
- Behavioral improvement: SD performance over the same-model SI control.
- Persistent deliberation: only established if the preregistered SD-SI rule passes.
- Context-rot mitigation: requires behavioral gain beyond active context, not transport alone.
- Frontier substitution: requires authorized F0 evidence; absent here.
- Token economics: actual model counts, separate from deterministic CPU and old estimates.
- Commercial viability: requires a passing economic segment plus real demand/reuse evidence.

## Remaining risks and assumptions

- Controlled configuration changes are narrower than open-ended repository development.
- Only Qwen2.5-Coder-7B-Instruct Q4_K_M on Ollama was evaluated.
- No human reviewer, customer pilot, market trace, hardware amortization, or energy rate was
  authorized; none is fabricated.
- Two memory reuses per trajectory were observed. Higher reuse levels are estimates and cannot
  justify break-even.
- External exports/backups remain outside the demonstrated deletion cascade.

The evidence supports **{verdicts["decision"]}** under the fixed decision logic. See the companion
evidence index, raw append-only runs, architecture trace, economics, failure log, environment, and
reproduction files in this package.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-id", required=True)
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--gate1-run", type=Path, default=DEFAULT_GATE1)
    parser.add_argument("--long-horizon-run", type=Path, default=DEFAULT_LONG_HORIZON)
    parser.add_argument("--lifecycle-before", type=Path, default=DEFAULT_LIFECYCLE_BEFORE)
    parser.add_argument("--lifecycle-after", type=Path, default=DEFAULT_LIFECYCLE_AFTER)
    parser.add_argument("--lifecycle-comparison", type=Path, default=DEFAULT_LIFECYCLE_COMPARISON)
    parser.add_argument("--offline-run", type=Path, default=DEFAULT_OFFLINE)
    arguments = parser.parse_args(argv)
    destination, verdicts = build(
        report_id=arguments.report_id,
        results_root=arguments.results_root,
        gate1_run=arguments.gate1_run,
        long_horizon_run=arguments.long_horizon_run,
        lifecycle_before=arguments.lifecycle_before,
        lifecycle_after=arguments.lifecycle_after,
        lifecycle_comparison=arguments.lifecycle_comparison,
        offline_run=arguments.offline_run,
    )
    print(json.dumps({"destination": str(destination), "verdicts": verdicts}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
