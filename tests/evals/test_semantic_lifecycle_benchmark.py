"""Aggregation checks for actual semantic lifecycle measurements."""

from typing import cast

from scripts.run_semantic_lifecycle_benchmark import aggregate_records


def test_lifecycle_aggregate_preserves_operations_and_stage_distributions() -> None:
    records = [
        {
            "operation": "checkpoint_view_save",
            "stage_durations_ns": {"validation": 2, "serialization": 4},
            "wall_duration_ns": 10,
            "deterministic_cpu_ns": 8,
            "source_event_count": 3,
            "changed_event_count": 2,
            "rendered_tokens": 100,
            "rendered_bytes": 300,
        },
        {
            "operation": "checkpoint_view_save",
            "stage_durations_ns": {"validation": 4, "serialization": 6},
            "wall_duration_ns": 14,
            "deterministic_cpu_ns": 10,
            "source_event_count": 4,
            "changed_event_count": 1,
            "rendered_tokens": 120,
            "rendered_bytes": 360,
        },
    ]
    aggregate = aggregate_records(records)
    operations = cast(dict[str, dict[str, object]], aggregate["operations"])
    operation = operations["checkpoint_view_save"]
    assert operation["observation_count"] == 2
    wall = cast(dict[str, object], operation["wall_duration_ns"])
    stages = cast(dict[str, dict[str, object]], operation["stages"])
    rendered = cast(dict[str, object], operation["rendered_tokens"])
    assert wall["median"] == 12
    assert stages["validation"]["mean"] == 3
    assert rendered["maximum"] == 120
