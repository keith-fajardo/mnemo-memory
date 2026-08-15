"""Paired profile comparison checks for semantic lifecycle runs."""

from typing import cast

from scripts.compare_semantic_lifecycle_runs import paired_comparison


def _record(sample: int, operation: str, wall: int) -> dict[str, object]:
    return {
        "sample": sample,
        "operation": operation,
        "wall_duration_ns": wall,
        "deterministic_cpu_ns": wall - 1,
        "stage_durations_ns": {"retrieval": wall - 2},
    }


def test_paired_comparison_clusters_repeated_operations_by_profile() -> None:
    before = [
        _record(sample, operation, 10)
        for sample in (1, 2, 3)
        for operation in ("checkpoint_view_save", "automatic_context_assembly")
    ]
    after = [
        _record(sample, operation, 5)
        for sample in (1, 2, 3)
        for operation in ("checkpoint_view_save", "automatic_context_assembly")
    ]
    result = paired_comparison(before, after, seed=7, bootstrap_iterations=100)
    metrics = cast(dict[str, dict[str, object]], result["metrics"])
    metric = metrics["top_level.wall_duration_ns"]
    assert result["paired_profile_count"] == 3
    assert metric["before_mean"] == 20
    assert metric["after_mean"] == 10
    assert metric["paired_mean_relative_change"] == -0.5
    assert metric["paired_mean_relative_change_ci95"] == [-0.5, -0.5]
