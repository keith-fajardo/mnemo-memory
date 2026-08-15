"""End-to-end deterministic viability evaluation gate."""

from __future__ import annotations

from pathlib import Path

from mnemo_memory.packages.application.evaluation import (
    ConditionId,
    EvaluationRunner,
    aggregate_runs,
    load_corpus,
    load_evaluation_config,
)

ROOT = Path(__file__).parents[2]


def test_full_offline_viability_corpus_is_paired_reproducible_and_honest() -> None:
    corpus = load_corpus(ROOT / "tests/fixtures/evals/viability-corpus-v1.json")
    config = load_evaluation_config(ROOT / "tests/fixtures/evals/viability-config-v1.json")
    runner = EvaluationRunner.offline(config, corpus)
    runs = runner.run("offline-eval-test")
    aggregate = aggregate_runs(runs, config)

    assert len(runs) == 18 * 3 * 7
    assert aggregate["available_run_count"] == 18 * 3 * 6
    assert aggregate["paired_observations_per_available_condition"] == 54
    assert aggregate["primary_independence_unit"] == "scenario_family"
    assert aggregate["independent_scenario_family_count"] == 6
    assert aggregate["verdict"] == "INSUFFICIENT EVIDENCE"
    assert aggregate["market_pull"]["score"] is None
    assert aggregate["operational_portability"]["portability_claim_supported"] is False
    assert ConditionId.PROVIDER_NATIVE.value in aggregate["unavailable_conditions"]
    assert set(aggregate["by_horizon"]) == {"short", "medium", "long"}
    assert len(aggregate["by_category"]) == 6
    assert len(aggregate["economic_scenarios"]) == 3
    assert len(aggregate["thresholds"]) == 8
    assert len(aggregate["token_efficiency_by_horizon_reuse"]) == 9
    comparison = aggregate["paired_comparisons"]["M3_mnemo_adaptive_retrieval_vs_B0_full_history"]
    assert comparison["pair_count"] == 54
    assert comparison["independent_scenario_family_count"] == 6
    assert (
        comparison["token_efficiency_score"]["confidence_interval_method"]
        == "paired cluster bootstrap over scenario families"
    )
    diagnostics = aggregate["lifecycle_tes_diagnostics"]
    assert diagnostics["primary_reported_value"] == "median_of_paired_ratios"
    assert diagnostics["median_of_paired_ratios"] != diagnostics["ratio_of_condition_medians"]
    compact = aggregate["conditions"]["M1_mnemo_compact_200"]
    rolling = aggregate["conditions"]["B2_rolling_summary"]
    proxy_delta = aggregate["paired_comparisons"]["M1_mnemo_compact_200_vs_B2_rolling_summary"][
        "task_success_proxy_delta"
    ]["mean"]
    assert (
        proxy_delta == compact["task_success_proxy"]["mean"] - rolling["task_success_proxy"]["mean"]
    )
    assert (
        proxy_delta
        != compact["continuation_fidelity"]["mean"] - rolling["continuation_fidelity"]["mean"]
    )
    assert aggregate["threshold_summary"] == {
        "PASS": 4,
        "FAIL": 1,
        "NOT EVALUATED": 3,
    }

    for condition in (
        ConditionId.FULL_HISTORY,
        ConditionId.SLIDING_WINDOW,
        ConditionId.ROLLING_SUMMARY,
        ConditionId.MNEMO_COMPACT,
        ConditionId.MNEMO_ADAPTIVE,
        ConditionId.MNEMO_RETRIEVAL,
    ):
        condition_runs = [run for run in runs if run.condition is condition]
        assert len(condition_runs) == 54
        assert all(run.available for run in condition_runs)
        assert len({run.pair_id for run in condition_runs}) == 54

    m3_runs = [run for run in runs if run.condition is ConditionId.MNEMO_RETRIEVAL]
    assert all(run.grade is not None for run in m3_runs)
    assert all(run.grade and not run.grade.critical_violations for run in m3_runs)
    assert all(run.grade and run.grade.false_memory_rate == 0.0 for run in m3_runs)
    assert all(run.grade and run.grade.drift_resistance == 1.0 for run in m3_runs)

    full_pair_ids = {run.pair_id for run in runs if run.condition is ConditionId.FULL_HISTORY}
    for pair_id in full_pair_ids:
        order = [
            run.condition
            for run in sorted(
                (item for item in runs if item.pair_id == pair_id),
                key=lambda item: item.order_index,
            )
        ]
        assert set(order) == set(ConditionId)
