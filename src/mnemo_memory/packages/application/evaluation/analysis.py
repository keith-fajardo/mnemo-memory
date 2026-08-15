"""Reproducible viability formulas, paired statistics, and Pareto analysis."""

from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from .models import (
    ConditionId,
    ContinuationGrade,
    EconomicAssumption,
    EvaluationConfig,
    EvaluationRun,
    MetricClassification,
    ThresholdStatus,
)

_MVS_WEIGHTS = {"TE": 0.20, "LM": 0.30, "TI": 0.20, "EV": 0.15, "MP": 0.10, "OP": 0.05}


def token_efficiency_score(baseline_tokens: float, mnemo_tokens: float) -> float:
    if baseline_tokens <= 0 or mnemo_tokens < 0:
        raise ValueError("token efficiency requires a positive baseline and non-negative candidate")
    return (baseline_tokens - mnemo_tokens) / baseline_tokens


def break_even_reuse(
    save_tokens: int,
    validation_tokens: int,
    baseline_context_per_reuse: int,
    mnemo_context_per_reuse: int,
) -> float | None:
    if min(save_tokens, validation_tokens, baseline_context_per_reuse, mnemo_context_per_reuse) < 0:
        raise ValueError("break-even inputs cannot be negative")
    denominator = baseline_context_per_reuse - mnemo_context_per_reuse
    if denominator <= 0:
        return None
    return (save_tokens + validation_tokens) / denominator


def long_term_memory_efficiency(
    *,
    continuation_fidelity: float,
    required_knowledge_retention: float,
    temporal_supersession_accuracy: float,
    evidence_attribution_fidelity: float,
    drift_resistance: float,
    retrieval_f1: float,
    false_memory_rate: float,
    critical_violation: bool,
) -> tuple[float, float]:
    values = (
        continuation_fidelity,
        required_knowledge_retention,
        temporal_supersession_accuracy,
        evidence_attribution_fidelity,
        drift_resistance,
        retrieval_f1,
        false_memory_rate,
    )
    if any(not 0.0 <= value <= 1.0 for value in values):
        raise ValueError("LME inputs must be between zero and one")
    ungated = (
        continuation_fidelity**0.30
        * required_knowledge_retention**0.20
        * temporal_supersession_accuracy**0.15
        * evidence_attribution_fidelity**0.10
        * drift_resistance**0.10
        * retrieval_f1**0.10
        * (1.0 - false_memory_rate) ** 0.05
    )
    return (0.0 if critical_violation else ungated), ungated


def task_impact(
    success_rate: float,
    resume_speed_improvement: float,
    avoided_repeated_work: float,
    human_intervention_reduction: float,
) -> float:
    values = (
        success_rate,
        resume_speed_improvement,
        avoided_repeated_work,
        human_intervention_reduction,
    )
    if any(not 0.0 <= value <= 1.0 for value in values):
        raise ValueError("task-impact inputs must be between zero and one")
    return (
        0.50 * success_rate
        + 0.20 * resume_speed_improvement
        + 0.15 * avoided_repeated_work
        + 0.15 * human_intervention_reduction
    )


def memory_viability_score(
    dimensions: Mapping[str, float | None], *, production_gate: bool
) -> dict[str, object]:
    unknown = tuple(name for name in _MVS_WEIGHTS if dimensions.get(name) is None)
    observed = {
        name: value
        for name, value in dimensions.items()
        if name in _MVS_WEIGHTS and value is not None
    }
    if any(not 0.0 <= value <= 1.0 for value in observed.values()):
        raise ValueError("MVS dimensions must be normalized to zero through one")
    weight = sum(_MVS_WEIGHTS[name] for name in observed)
    if not observed or weight == 0:
        observed_score = None
    elif any(value == 0 for value in observed.values()):
        observed_score = 0.0
    else:
        observed_score = 100 * math.prod(
            value ** (_MVS_WEIGHTS[name] / weight) for name, value in observed.items()
        )
    complete = None if unknown else observed_score
    if not production_gate:
        complete = 0.0 if not unknown else None
        observed_score = 0.0 if observed_score is not None else None
    return {
        "complete_score": complete,
        "observed_dimensions_score": observed_score,
        "observed_dimensions": sorted(observed),
        "missing_dimensions": list(unknown),
        "production_gate": production_gate,
    }


def bootstrap_mean_interval(
    values: tuple[float, ...], *, samples: int = 2000, seed: int = 0
) -> tuple[float, float]:
    if not values:
        raise ValueError("bootstrap requires observations")
    if samples < 100:
        raise ValueError("bootstrap requires at least 100 samples")
    generator = random.Random(seed)
    means = sorted(
        statistics.fmean(generator.choice(values) for _ in values) for _ in range(samples)
    )
    return (_percentile(means, 0.025), _percentile(means, 0.975))


def bootstrap_cluster_mean_interval(
    values: tuple[float, ...],
    cluster_ids: tuple[str, ...],
    *,
    samples: int = 2000,
    seed: int = 0,
) -> tuple[float, float] | None:
    """Bootstrap whole scenario families so deterministic rows are not treated as independent."""

    if not values or len(values) != len(cluster_ids):
        raise ValueError("cluster bootstrap requires aligned observations and cluster IDs")
    if samples < 100:
        raise ValueError("bootstrap requires at least 100 samples")
    grouped: dict[str, list[float]] = defaultdict(list)
    for cluster_id, value in zip(cluster_ids, values, strict=True):
        if not cluster_id:
            raise ValueError("cluster bootstrap IDs must not be blank")
        grouped[cluster_id].append(value)
    cluster_names = tuple(sorted(grouped))
    if len(cluster_names) < 2:
        return None
    generator = random.Random(seed)
    means: list[float] = []
    for _ in range(samples):
        sampled_values = tuple(
            value
            for cluster_name in (generator.choice(cluster_names) for _ in range(len(cluster_names)))
            for value in grouped[cluster_name]
        )
        means.append(statistics.fmean(sampled_values))
    ordered = sorted(means)
    return (_percentile(ordered, 0.025), _percentile(ordered, 0.975))


def descriptive_statistics(
    values: tuple[float, ...],
    *,
    bootstrap_samples: int,
    seed: int,
    cluster_ids: tuple[str, ...] | None = None,
) -> dict[str, object]:
    if not values:
        return {
            "run_count": 0,
            "mean": None,
            "median": None,
            "standard_deviation": None,
            "p10": None,
            "p90": None,
            "confidence_interval_95": None,
            "independence_unit": "scenario_family" if cluster_ids is not None else "observation",
            "independent_unit_count": 0,
            "confidence_interval_method": "not estimable",
        }
    ordered = sorted(values)
    if cluster_ids is None:
        interval: tuple[float, float] | None = bootstrap_mean_interval(
            values, samples=bootstrap_samples, seed=seed
        )
        independence_unit = "observation"
        independent_unit_count = len(values)
        interval_method = "nonparametric bootstrap over observations"
    else:
        interval = bootstrap_cluster_mean_interval(
            values,
            cluster_ids,
            samples=bootstrap_samples,
            seed=seed,
        )
        independence_unit = "scenario_family"
        independent_unit_count = len(set(cluster_ids))
        interval_method = (
            "paired cluster bootstrap over scenario families"
            if interval is not None
            else "not estimable from fewer than two scenario families"
        )
    return {
        "run_count": len(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "standard_deviation": statistics.stdev(values) if len(values) > 1 else 0.0,
        "p10": _percentile(ordered, 0.10),
        "p90": _percentile(ordered, 0.90),
        "confidence_interval_95": None if interval is None else list(interval),
        "independence_unit": independence_unit,
        "independent_unit_count": independent_unit_count,
        "confidence_interval_method": interval_method,
    }


def economic_scenarios(
    assumptions: tuple[EconomicAssumption, ...],
    *,
    baseline_input_tokens: float,
    baseline_output_tokens: float,
    candidate_input_tokens: float,
    candidate_output_tokens: float,
) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for item in assumptions:
        baseline_cost = (
            baseline_input_tokens * item.input_cost_per_million
            + baseline_output_tokens * item.output_cost_per_million
        ) / 1_000_000
        candidate_cost = (
            candidate_input_tokens * item.input_cost_per_million
            + candidate_output_tokens * item.output_cost_per_million
        ) / 1_000_000
        saving = baseline_cost - candidate_cost
        development_and_infra = item.annual_development_cost + item.annual_infrastructure_cost
        annual_net = (
            item.annual_eligible_runs * saving
            + item.annual_human_hours_saved * item.human_hour_value
            + item.annual_failures_avoided * item.avoided_failure_value
            - development_and_infra
        )
        roi = annual_net / development_and_infra if development_and_infra > 0 else None
        volume_break_even = None if saving <= 0 else development_and_infra / saving
        results.append(
            {
                "name": item.name,
                "evidence_class": "estimated_assumption",
                "baseline_inference_cost_per_run_usd": baseline_cost,
                "candidate_inference_cost_per_run_usd": candidate_cost,
                "economic_saving_per_eligible_run_usd": saving,
                "annual_net_value_usd": annual_net,
                "roi": roi,
                "volume_break_even_runs": volume_break_even,
                "assumptions": {
                    "annual_eligible_runs": item.annual_eligible_runs,
                    "annual_human_hours_saved": item.annual_human_hours_saved,
                    "annual_failures_avoided": item.annual_failures_avoided,
                    "annual_development_cost": item.annual_development_cost,
                    "annual_infrastructure_cost": item.annual_infrastructure_cost,
                },
            }
        )
    return results


def pareto_frontier(points: Mapping[str, Mapping[str, float]]) -> tuple[str, ...]:
    """Return nondominated conditions: tokens/latency lower, LME/success higher."""

    result: list[str] = []
    for name, point in points.items():
        dominated = False
        for other_name, other in points.items():
            if name == other_name:
                continue
            weak = (
                other["tokens"] <= point["tokens"]
                and other["latency_ms"] <= point["latency_ms"]
                and other["lme"] >= point["lme"]
                and other["success"] >= point["success"]
            )
            strict = (
                other["tokens"] < point["tokens"]
                or other["latency_ms"] < point["latency_ms"]
                or other["lme"] > point["lme"]
                or other["success"] > point["success"]
            )
            if weak and strict:
                dominated = True
                break
        if not dominated:
            result.append(name)
    return tuple(sorted(result))


def aggregate_runs(runs: tuple[EvaluationRun, ...], config: EvaluationConfig) -> dict[str, Any]:
    available = tuple(run for run in runs if run.available and run.grade is not None)
    grouped: dict[ConditionId, list[EvaluationRun]] = defaultdict(list)
    for run in available:
        grouped[run.condition].append(run)
    conditions: dict[str, dict[str, object]] = {}
    for offset, (condition, items) in enumerate(
        sorted(grouped.items(), key=lambda pair: pair[0].value)
    ):
        grades = [item.grade for item in items if item.grade is not None]
        scenario_families = tuple(item.template_id for item in items)
        conditions[condition.value] = {
            "lifecycle_tokens": descriptive_statistics(
                tuple(float(item.token_account.total) for item in items),
                bootstrap_samples=config.bootstrap_samples,
                seed=config.seed + offset,
                cluster_ids=scenario_families,
            ),
            "context_tokens_per_reuse": descriptive_statistics(
                tuple(float(item.context_tokens_per_reuse) for item in items),
                bootstrap_samples=config.bootstrap_samples,
                seed=config.seed + offset + 100,
                cluster_ids=scenario_families,
            ),
            "lme_gated": descriptive_statistics(
                tuple(float(item.lme_gated) for item in items if item.lme_gated is not None),
                bootstrap_samples=config.bootstrap_samples,
                seed=config.seed + offset + 200,
                cluster_ids=scenario_families,
            ),
            "lme_ungated": descriptive_statistics(
                tuple(float(item.lme_ungated) for item in items if item.lme_ungated is not None),
                bootstrap_samples=config.bootstrap_samples,
                seed=config.seed + offset + 300,
                cluster_ids=scenario_families,
            ),
            "continuation_fidelity": descriptive_statistics(
                tuple(item.continuation_fidelity for item in grades),
                bootstrap_samples=config.bootstrap_samples,
                seed=config.seed + offset + 400,
                cluster_ids=scenario_families,
            ),
            "required_knowledge_retention": descriptive_statistics(
                tuple(item.required_knowledge_retention for item in grades),
                bootstrap_samples=config.bootstrap_samples,
                seed=config.seed + offset + 450,
                cluster_ids=scenario_families,
            ),
            "evidence_attribution_fidelity": descriptive_statistics(
                tuple(item.evidence_attribution_fidelity for item in grades),
                bootstrap_samples=config.bootstrap_samples,
                seed=config.seed + offset + 475,
                cluster_ids=scenario_families,
            ),
            "retrieval_f1": descriptive_statistics(
                tuple(item.retrieval_f1 for item in grades),
                bootstrap_samples=config.bootstrap_samples,
                seed=config.seed + offset + 490,
                cluster_ids=scenario_families,
            ),
            "task_success_proxy": descriptive_statistics(
                tuple(item.task_success_proxy for item in grades),
                bootstrap_samples=config.bootstrap_samples,
                seed=config.seed + offset + 500,
                cluster_ids=scenario_families,
            ),
            "task_impact_proxy": descriptive_statistics(
                tuple(
                    float(item.task_impact_proxy)
                    for item in items
                    if item.task_impact_proxy is not None
                ),
                bootstrap_samples=config.bootstrap_samples,
                seed=config.seed + offset + 600,
                cluster_ids=scenario_families,
            ),
            "protected_span_fidelity": descriptive_statistics(
                tuple(item.protected_span_fidelity for item in grades),
                bootstrap_samples=config.bootstrap_samples,
                seed=config.seed + offset + 700,
                cluster_ids=scenario_families,
            ),
            "constraint_retention": descriptive_statistics(
                tuple(item.constraint_retention for item in grades),
                bootstrap_samples=config.bootstrap_samples,
                seed=config.seed + offset + 800,
                cluster_ids=scenario_families,
            ),
            "temporal_supersession_accuracy": descriptive_statistics(
                tuple(item.temporal_supersession_accuracy for item in grades),
                bootstrap_samples=config.bootstrap_samples,
                seed=config.seed + offset + 900,
                cluster_ids=scenario_families,
            ),
            "false_memory_rate": descriptive_statistics(
                tuple(item.false_memory_rate for item in grades),
                bootstrap_samples=config.bootstrap_samples,
                seed=config.seed + offset + 1000,
                cluster_ids=scenario_families,
            ),
            "critical_violation_runs": sum(bool(item.critical_violations) for item in grades),
            "failure_categories": _failure_counts(grades),
            "latency_ms": descriptive_statistics(
                tuple(item.latency_ms for item in items),
                bootstrap_samples=config.bootstrap_samples,
                seed=config.seed + offset + 1100,
                cluster_ids=scenario_families,
            ),
            "successful_long_horizon_tasks_per_million_tokens": _success_per_million(items),
        }
    unavailable = _unavailable_conditions(runs)
    paired = _paired_comparisons(available, config)
    horizon = _by_horizon(available, config)
    points = {
        name: {
            "tokens": float(_nested_metric(value, "lifecycle_tokens", "mean") or 0.0),
            "latency_ms": float(_nested_metric(value, "latency_ms", "mean") or 0.0),
            "lme": float(_nested_metric(value, "lme_gated", "mean") or 0.0),
            "success": float(_nested_metric(value, "task_success_proxy", "mean") or 0.0),
        }
        for name, value in conditions.items()
    }
    frontier = pareto_frontier(points)
    candidate_name = ConditionId.MNEMO_RETRIEVAL.value
    baseline_name = ConditionId.FULL_HISTORY.value
    candidate = grouped.get(ConditionId.MNEMO_RETRIEVAL, [])
    baseline = grouped.get(ConditionId.FULL_HISTORY, [])
    economics = economic_scenarios(
        config.economic_assumptions,
        baseline_input_tokens=_mean(item.token_account.input_total for item in baseline),
        baseline_output_tokens=_mean(item.token_account.output_total for item in baseline),
        candidate_input_tokens=_mean(item.token_account.input_total for item in candidate),
        candidate_output_tokens=_mean(item.token_account.output_total for item in candidate),
    )
    candidate_tes = _paired_tes(available, ConditionId.FULL_HISTORY, ConditionId.MNEMO_RETRIEVAL)
    candidate_lme = _mean(float(item.lme_gated or 0.0) for item in candidate)
    candidate_ti = _mean(float(item.task_impact_proxy or 0.0) for item in candidate)
    critical_candidate = any(
        item.grade is not None and item.grade.critical_violations for item in candidate
    )
    operational = _operational_score(candidate)
    observed_dimensions = {
        "TE": max(0.0, min(1.0, statistics.median(candidate_tes) if candidate_tes else 0.0)),
        "LM": candidate_lme,
        "TI": candidate_ti,
        "EV": None,
        "MP": None,
        "OP": operational,
    }
    viability = memory_viability_score(
        observed_dimensions,
        production_gate=not critical_candidate,
    )
    noninferiority = _noninferiority(available, config)
    threshold_table = _thresholds(conditions, paired, critical_candidate)
    threshold_summary = {
        status.value: sum(item["status"] == status.value for item in threshold_table)
        for status in ThresholdStatus
    }
    verdict = "INSUFFICIENT EVIDENCE"
    return {
        "schema_version": "mnemo-viability-results/1.1",
        "evidence_class": "deterministic_offline_synthetic",
        "run_count": len(runs),
        "available_run_count": len(available),
        "paired_observations_per_available_condition": len(available) // max(1, len(grouped)),
        "primary_independence_unit": "scenario_family",
        "independent_scenario_family_count": len({run.template_id for run in available}),
        "confidence_interval_policy": (
            "paired cluster bootstrap over scenario families; deterministic condition rows are "
            "repeated measurements, not independent samples"
        ),
        "conditions": conditions,
        "unavailable_conditions": unavailable,
        "by_horizon": horizon,
        "token_efficiency_by_horizon_reuse": _tes_by_horizon_reuse(available, config),
        "by_category": _by_category(available, config),
        "paired_comparisons": paired,
        "lifecycle_tes_diagnostics": _lifecycle_tes_diagnostics(conditions, paired),
        "noninferiority": noninferiority,
        "pareto_points": points,
        "pareto_frontier": list(frontier),
        "break_even": _break_even_summary(available),
        "economic_scenarios": economics,
        "market_pull": {
            "score": None,
            "status": ThresholdStatus.NOT_EVALUATED.value,
            "missing": [
                "three credible design partners",
                "two real pilots",
                "continued usage",
                "willingness-to-pay or cost-avoidance evidence",
            ],
        },
        "operational_portability": {
            "score": operational,
            "status": ThresholdStatus.NOT_EVALUATED.value,
            "live_model_families": 0,
            "provider_tokenizers": 0,
            "portability_claim_supported": False,
        },
        "mvs": {**viability, "dimensions": observed_dimensions},
        "metric_classification_catalog": _metric_classification_catalog(),
        "thresholds": threshold_table,
        "threshold_summary": threshold_summary,
        "verdict": verdict,
        "verdict_basis": (
            "Offline paired evidence measures context availability and lifecycle token estimates, "
            "but no authorized live-agent, provider-token, pilot, willingness-to-pay, or usage "
            "evidence exists."
        ),
        "named_candidate": candidate_name,
        "named_baseline": baseline_name,
    }


def _paired_comparisons(
    runs: tuple[EvaluationRun, ...], config: EvaluationConfig
) -> dict[str, dict[str, object]]:
    lookup = {(run.pair_id, run.condition): run for run in runs}
    comparisons: dict[str, dict[str, object]] = {}
    pairs = (
        (ConditionId.FULL_HISTORY, ConditionId.MNEMO_COMPACT),
        (ConditionId.FULL_HISTORY, ConditionId.MNEMO_ADAPTIVE),
        (ConditionId.FULL_HISTORY, ConditionId.MNEMO_RETRIEVAL),
        (ConditionId.ROLLING_SUMMARY, ConditionId.MNEMO_COMPACT),
        (ConditionId.ROLLING_SUMMARY, ConditionId.MNEMO_RETRIEVAL),
    )
    pair_ids = sorted({run.pair_id for run in runs})
    for offset, (baseline, candidate) in enumerate(pairs):
        matched = [
            (lookup[(pair_id, baseline)], lookup[(pair_id, candidate)])
            for pair_id in pair_ids
            if (pair_id, baseline) in lookup and (pair_id, candidate) in lookup
        ]
        token_deltas = tuple(
            float(candidate_run.token_account.total - baseline_run.token_account.total)
            for baseline_run, candidate_run in matched
        )
        tes = tuple(
            token_efficiency_score(
                baseline_run.token_account.total, candidate_run.token_account.total
            )
            for baseline_run, candidate_run in matched
        )
        quality = tuple(
            float(candidate_run.grade.task_success_proxy if candidate_run.grade else 0.0)
            - float(baseline_run.grade.task_success_proxy if baseline_run.grade else 0.0)
            for baseline_run, candidate_run in matched
        )
        scenario_families = tuple(candidate_run.template_id for _, candidate_run in matched)
        key = f"{candidate.value}_vs_{baseline.value}"
        comparisons[key] = {
            "pair_count": len(matched),
            "independent_scenario_family_count": len(set(scenario_families)),
            "primary_independence_unit": "scenario_family",
            "token_delta_candidate_minus_baseline": descriptive_statistics(
                token_deltas,
                bootstrap_samples=config.bootstrap_samples,
                seed=config.seed + 2000 + offset,
                cluster_ids=scenario_families,
            ),
            "token_efficiency_score": descriptive_statistics(
                tes,
                bootstrap_samples=config.bootstrap_samples,
                seed=config.seed + 2100 + offset,
                cluster_ids=scenario_families,
            ),
            "task_success_proxy_delta": descriptive_statistics(
                quality,
                bootstrap_samples=config.bootstrap_samples,
                seed=config.seed + 2200 + offset,
                cluster_ids=scenario_families,
            ),
        }
    return comparisons


def _noninferiority(runs: tuple[EvaluationRun, ...], config: EvaluationConfig) -> dict[str, object]:
    lookup = {(run.pair_id, run.condition): run for run in runs}
    matched = tuple(
        (
            lookup[(pair_id, ConditionId.FULL_HISTORY)],
            lookup[(pair_id, ConditionId.MNEMO_RETRIEVAL)],
        )
        for pair_id in sorted({run.pair_id for run in runs})
        if (pair_id, ConditionId.MNEMO_RETRIEVAL) in lookup
        and (pair_id, ConditionId.FULL_HISTORY) in lookup
        and lookup[(pair_id, ConditionId.MNEMO_RETRIEVAL)].grade is not None
        and lookup[(pair_id, ConditionId.FULL_HISTORY)].grade is not None
    )
    deltas = tuple(
        _grade_success(candidate) - _grade_success(baseline) for baseline, candidate in matched
    )
    if not deltas:
        return {"status": "unavailable", "reason": "no paired runs"}
    scenario_families = tuple(candidate.template_id for _, candidate in matched)
    interval = bootstrap_cluster_mean_interval(
        deltas,
        scenario_families,
        samples=config.bootstrap_samples,
        seed=config.seed + 3000,
    )
    if interval is None:
        return {
            "status": "not_evaluated",
            "reason": "fewer than two independent scenario families",
            "pair_count": len(deltas),
            "independent_scenario_family_count": len(set(scenario_families)),
        }
    return {
        "evidence_class": "offline_task_success_proxy",
        "pair_count": len(deltas),
        "independent_scenario_family_count": len(set(scenario_families)),
        "primary_independence_unit": "scenario_family",
        "margin": config.noninferiority_margin,
        "mean_delta": statistics.fmean(deltas),
        "confidence_interval_95": list(interval),
        "passes_proxy": interval[0] >= -config.noninferiority_margin,
        "live_task_quality_measured": False,
    }


def _by_horizon(
    runs: tuple[EvaluationRun, ...], config: EvaluationConfig
) -> dict[str, dict[str, object]]:
    grouped: dict[tuple[str, str], list[EvaluationRun]] = defaultdict(list)
    for run in runs:
        grouped[(run.horizon.value, run.condition.value)].append(run)
    output: dict[str, dict[str, object]] = defaultdict(dict)
    for offset, ((horizon, condition), items) in enumerate(sorted(grouped.items())):
        token_statistics = descriptive_statistics(
            tuple(float(item.token_account.total) for item in items),
            bootstrap_samples=config.bootstrap_samples,
            seed=config.seed + 4000 + offset,
            cluster_ids=tuple(item.template_id for item in items),
        )
        output[horizon][condition] = {
            "run_count": len(items),
            "lifecycle_tokens_mean": _mean(item.token_account.total for item in items),
            "task_success_proxy_mean": _mean(
                item.grade.task_success_proxy for item in items if item.grade is not None
            ),
            "lme_gated_mean": _mean(float(item.lme_gated or 0.0) for item in items),
            "token_ci_95": token_statistics["confidence_interval_95"],
            "independent_scenario_family_count": token_statistics["independent_unit_count"],
            "confidence_interval_method": token_statistics["confidence_interval_method"],
        }
    return dict(output)


def _by_category(
    runs: tuple[EvaluationRun, ...], config: EvaluationConfig
) -> dict[str, dict[str, object]]:
    lookup = {(run.pair_id, run.condition): run for run in runs}
    grouped: dict[str, list[tuple[EvaluationRun, EvaluationRun]]] = defaultdict(list)
    for pair_id in sorted({run.pair_id for run in runs}):
        baseline = lookup.get((pair_id, ConditionId.FULL_HISTORY))
        candidate = lookup.get((pair_id, ConditionId.MNEMO_RETRIEVAL))
        if baseline is not None and candidate is not None:
            grouped[candidate.category].append((baseline, candidate))
    output: dict[str, dict[str, object]] = {}
    for offset, (category, pairs) in enumerate(sorted(grouped.items())):
        tes = tuple(
            token_efficiency_score(baseline.token_account.total, candidate.token_account.total)
            for baseline, candidate in pairs
        )
        output[category] = {
            "pair_count": len(pairs),
            "token_efficiency_score": descriptive_statistics(
                tes,
                bootstrap_samples=config.bootstrap_samples,
                seed=config.seed + 5000 + offset,
                cluster_ids=tuple(candidate.template_id for _, candidate in pairs),
            ),
            "lme_gated_mean": _mean(float(candidate.lme_gated or 0.0) for _, candidate in pairs),
            "task_success_proxy_mean": _mean(
                candidate.grade.task_success_proxy
                for _, candidate in pairs
                if candidate.grade is not None
            ),
        }
    return output


def _tes_by_horizon_reuse(
    runs: tuple[EvaluationRun, ...], config: EvaluationConfig
) -> dict[str, dict[str, object]]:
    lookup = {(run.pair_id, run.condition): run for run in runs}
    grouped: dict[tuple[str, int], list[tuple[float, str]]] = defaultdict(list)
    for pair_id in sorted({run.pair_id for run in runs}):
        baseline = lookup.get((pair_id, ConditionId.FULL_HISTORY))
        candidate = lookup.get((pair_id, ConditionId.MNEMO_RETRIEVAL))
        if baseline is None or candidate is None:
            continue
        grouped[(candidate.horizon.value, candidate.reuse_count)].append(
            (
                token_efficiency_score(
                    baseline.token_account.total,
                    candidate.token_account.total,
                ),
                candidate.template_id,
            )
        )
    return {
        f"{horizon}:reuse-{reuse_count}": descriptive_statistics(
            tuple(value for value, _ in values),
            bootstrap_samples=config.bootstrap_samples,
            seed=config.seed + 6000 + offset,
            cluster_ids=tuple(template_id for _, template_id in values),
        )
        for offset, ((horizon, reuse_count), values) in enumerate(sorted(grouped.items()))
    }


def _break_even_summary(runs: tuple[EvaluationRun, ...]) -> dict[str, object]:
    lookup = {(run.scenario_id, run.condition): run for run in runs if run.reuse_count == 1}
    results: dict[str, list[float]] = defaultdict(list)
    no_positive: dict[str, int] = defaultdict(int)
    for scenario_id in sorted({run.scenario_id for run in runs}):
        baseline = lookup.get((scenario_id, ConditionId.FULL_HISTORY))
        if baseline is None:
            continue
        for condition in (
            ConditionId.MNEMO_COMPACT,
            ConditionId.MNEMO_ADAPTIVE,
            ConditionId.MNEMO_RETRIEVAL,
        ):
            candidate = lookup.get((scenario_id, condition))
            if candidate is None:
                continue
            value = break_even_reuse(
                candidate.save_tokens,
                candidate.validation_tokens,
                baseline.context_tokens_per_reuse,
                candidate.context_tokens_per_reuse,
            )
            if value is None:
                no_positive[condition.value] += 1
            else:
                results[condition.value].append(value)
    return {
        condition.value: {
            "scenario_count": len(results[condition.value]) + no_positive[condition.value],
            "positive_break_even_count": len(results[condition.value]),
            "no_positive_break_even_count": no_positive[condition.value],
            "median_reuses": (
                statistics.median(results[condition.value]) if results[condition.value] else None
            ),
            "p90_reuses": (
                _percentile(sorted(results[condition.value]), 0.90)
                if results[condition.value]
                else None
            ),
        }
        for condition in (
            ConditionId.MNEMO_COMPACT,
            ConditionId.MNEMO_ADAPTIVE,
            ConditionId.MNEMO_RETRIEVAL,
        )
    }


def _lifecycle_tes_diagnostics(
    conditions: Mapping[str, Mapping[str, object]],
    paired: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    baseline_name = ConditionId.FULL_HISTORY.value
    candidate_name = ConditionId.MNEMO_RETRIEVAL.value
    comparison = paired.get(f"{candidate_name}_vs_{baseline_name}", {})
    paired_median = _nested_metric(comparison, "token_efficiency_score", "median")
    baseline_median = _nested_metric(
        conditions.get(baseline_name, {}), "lifecycle_tokens", "median"
    )
    candidate_median = _nested_metric(
        conditions.get(candidate_name, {}), "lifecycle_tokens", "median"
    )
    ratio_of_medians = (
        None
        if baseline_median is None or baseline_median <= 0 or candidate_median is None
        else token_efficiency_score(baseline_median, candidate_median)
    )
    return {
        "primary_reported_value": "median_of_paired_ratios",
        "median_of_paired_ratios": paired_median,
        "ratio_of_condition_medians": ratio_of_medians,
        "baseline_condition_median_tokens": baseline_median,
        "candidate_condition_median_tokens": candidate_median,
        "interpretation": (
            "The paired median and the ratio of marginal medians are distinct summaries and are "
            "not expected to be arithmetically identical."
        ),
    }


def _metric_classification_catalog() -> list[dict[str, str]]:
    entries = (
        (
            "latency_ms; executed_external_calls; external_cost_incurred_usd; run/exclusion counts",
            MetricClassification.ACTUALLY_OBSERVED,
            "Recorded from executed local processes or append-only run bookkeeping.",
        ),
        (
            "continuation_fidelity; required_knowledge_retention; constraint_retention; "
            "protected_span_fidelity; temporal_supersession_accuracy; "
            "evidence_attribution_fidelity; retrieval_precision/recall/F1; false_memory_rate; "
            "critical violations; drift resistance; artifact hashes",
            MetricClassification.DETERMINISTICALLY_MEASURED,
            "Computed exactly from disclosed fixtures, outputs, evidence links, or artifact bytes.",
        ),
        (
            "model_input; model_output; model completion content",
            MetricClassification.MODEL_GENERATED,
            "Applies only to an executed model condition; the offline baseline has none.",
        ),
        (
            "lexical token-account fields; lifecycle_tokens; token deltas; LifecycleTES; "
            "compression ratio; break-even reuse; monetary sensitivity; counterfactual cost",
            MetricClassification.ESTIMATED,
            "Uses the named deterministic lexical counter or disclosed economic assumptions, "
            "not provider billing.",
        ),
        (
            "task_success_proxy; task_impact_proxy; resume_speed_proxy; avoided_rework_proxy; "
            "human_intervention_proxy; gated/ungated LME; MVS; successful tasks per million tokens",
            MetricClassification.PROXY,
            "Information availability or composite utility; not generated-agent task performance.",
        ),
        (
            "retry_and_repair token equivalent; reuse-scaled lifecycle accounts; "
            "synthetic scenario rows",
            MetricClassification.SIMULATED,
            "Counterfactual repeated work or controlled synthetic repetition, never actual "
            "provider usage.",
        ),
        (
            "live end-to-end task success; hidden-test accuracy; actual human intervention; "
            "blinded quality; provider billing; frontier gap closure; market demand; "
            "production portability",
            MetricClassification.NOT_EVALUATED,
            "No authorized empirical observation exists in the offline baseline.",
        ),
    )
    return [
        {"metric_or_family": metric, "classification": classification.value, "basis": basis}
        for metric, classification, basis in entries
    ]


def _thresholds(
    conditions: Mapping[str, Mapping[str, object]],
    paired: Mapping[str, Mapping[str, object]],
    critical_candidate: bool,
) -> list[dict[str, object]]:
    candidate = conditions.get(ConditionId.MNEMO_RETRIEVAL.value, {})
    comparison = paired.get(
        f"{ConditionId.MNEMO_RETRIEVAL.value}_vs_{ConditionId.FULL_HISTORY.value}", {}
    )
    rolling = paired.get(
        f"{ConditionId.MNEMO_COMPACT.value}_vs_{ConditionId.ROLLING_SUMMARY.value}", {}
    )
    tes = _nested_metric(comparison, "token_efficiency_score", "median")
    lme = _nested_metric(candidate, "lme_gated", "mean")
    protected = _nested_metric(candidate, "protected_span_fidelity", "mean")
    temporal = _nested_metric(candidate, "temporal_supersession_accuracy", "mean")
    rolling_delta = _nested_metric(rolling, "task_success_proxy_delta", "mean")
    return [
        _threshold(
            "median lifecycle token savings >=30%",
            tes,
            ThresholdStatus.NOT_EVALUATED
            if tes is None
            else ThresholdStatus.PASS
            if tes >= 0.30
            else ThresholdStatus.FAIL,
            MetricClassification.ESTIMATED,
            "median of paired offline lifecycle-token ratios",
        ),
        _threshold(
            "LME >=0.95",
            lme,
            ThresholdStatus.NOT_EVALUATED
            if lme is None
            else ThresholdStatus.PASS
            if lme >= 0.95
            else ThresholdStatus.FAIL,
            MetricClassification.PROXY,
            "composite of deterministically measured memory-integrity dimensions",
        ),
        _threshold(
            "critical retention and evidence integrity 100%",
            not critical_candidate,
            ThresholdStatus.PASS if not critical_candidate else ThresholdStatus.FAIL,
            MetricClassification.DETERMINISTICALLY_MEASURED,
            "exact fixture and evidence-association checks",
        ),
        _threshold(
            "protected and temporal integrity 100%",
            min(float(protected or 0), float(temporal or 0)),
            ThresholdStatus.NOT_EVALUATED
            if protected is None or temporal is None
            else ThresholdStatus.PASS
            if protected == 1.0 and temporal == 1.0
            else ThresholdStatus.FAIL,
            MetricClassification.DETERMINISTICALLY_MEASURED,
            "exact protected-span and supersession checks",
        ),
        _threshold(
            "task quality non-inferior within 5 points",
            None,
            ThresholdStatus.NOT_EVALUATED,
            MetricClassification.NOT_EVALUATED,
            "live generated-agent task quality was not measured; the offline proxy is separate",
        ),
        _threshold(
            "better than rolling summary at same budget",
            rolling_delta,
            ThresholdStatus.NOT_EVALUATED
            if rolling_delta is None
            else ThresholdStatus.PASS
            if rolling_delta > 0
            else ThresholdStatus.FAIL,
            MetricClassification.PROXY,
            "paired offline task-success availability proxy; M1 and B2 share a 200-token policy",
        ),
        _threshold(
            "cost per successful long-horizon task >=20% lower",
            None,
            ThresholdStatus.NOT_EVALUATED,
            MetricClassification.NOT_EVALUATED,
            "actual inference cost and live task success unavailable",
        ),
        _threshold(
            "market evidence threshold",
            None,
            ThresholdStatus.NOT_EVALUATED,
            MetricClassification.NOT_EVALUATED,
            "no design-partner, usage, or pilot evidence",
        ),
    ]


def _threshold(
    name: str,
    value: object,
    status: ThresholdStatus,
    classification: MetricClassification,
    evidence: str,
) -> dict[str, object]:
    return {
        "threshold": name,
        "value": value,
        "status": status.value,
        "classification": classification.value,
        "evidence": evidence,
    }


def _unavailable_conditions(runs: tuple[EvaluationRun, ...]) -> dict[str, str]:
    output: dict[str, str] = {}
    for run in runs:
        if not run.available and run.unavailable_reason is not None:
            output[run.condition.value] = run.unavailable_reason
    return output


def _paired_tes(
    runs: tuple[EvaluationRun, ...], baseline: ConditionId, candidate: ConditionId
) -> tuple[float, ...]:
    lookup = {(run.pair_id, run.condition): run for run in runs}
    return tuple(
        token_efficiency_score(
            lookup[(pair_id, baseline)].token_account.total,
            lookup[(pair_id, candidate)].token_account.total,
        )
        for pair_id in sorted({run.pair_id for run in runs})
        if (pair_id, baseline) in lookup and (pair_id, candidate) in lookup
    )


def _operational_score(items: list[EvaluationRun]) -> float:
    if not items:
        return 0.0
    reliability = 1.0 - sum(
        bool(item.grade and item.grade.critical_violations) for item in items
    ) / len(items)
    determinism = _mean(item.grade.drift_resistance for item in items if item.grade is not None)
    portability = 0.0
    return _mean((reliability, determinism, portability))


def _failure_counts(grades: Iterable[ContinuationGrade]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for grade in grades:
        for category in grade.failure_categories:
            counts[category] += 1
    return dict(sorted(counts.items()))


def _success_per_million(items: list[EvaluationRun]) -> float | None:
    long_items = [item for item in items if item.horizon.value == "long" and item.grade is not None]
    tokens = sum(item.token_account.total for item in long_items)
    if tokens <= 0:
        return None
    successes = sum(item.grade.task_success_proxy for item in long_items if item.grade is not None)
    return successes * 1_000_000 / tokens


def _grade_success(run: EvaluationRun) -> float:
    return 0.0 if run.grade is None else run.grade.task_success_proxy


def _nested_metric(value: Mapping[str, object], *keys: str) -> float | None:
    current: object = value
    for key in keys:
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    return float(current) if isinstance(current, int | float) else None


def _mean(values: Iterable[float | int]) -> float:
    materialized = tuple(float(value) for value in values)
    return statistics.fmean(materialized) if materialized else 0.0


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        raise ValueError("percentile requires values")
    position = (len(values) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    return values[lower] + (values[upper] - values[lower]) * (position - lower)
