from __future__ import annotations

from scripts.run_wrapper_overhead_benchmark import evaluate, main


def test_wrapper_overhead_fixture_is_deterministic_and_separates_phases() -> None:
    first = evaluate()
    second = evaluate()

    assert first == second
    assert first["passed"] is True
    assert first["hardware_performance_claim"] is False
    assert first["phase_milliseconds"] == {
        "mnemo_pre_hook": 1,
        "dbt_execution": 20,
        "mnemo_post_hook_parse_and_ingestion": 8,
        "total": 29,
    }


def test_wrapper_overhead_command_exits_successfully_for_the_golden_fixture() -> None:
    assert main(["--json"]) == 0
