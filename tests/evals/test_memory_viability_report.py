"""Economic classifications and terminal resource accounting."""

from typing import cast

from scripts.build_memory_viability_report import economic_analysis


def _row(variant: str, condition: str, tokens: int, success: bool) -> dict[str, object]:
    return {
        "variant_id": variant,
        "condition": condition,
        "available": True,
        "end_to_end_success": success,
        "actual_prompt_tokens": tokens - 10,
        "actual_output_tokens": 10,
        "actual_latency_ns": tokens * 1_000_000,
        "repeated_error_count": 0,
        "human_intervention_count": 0,
        "external_spend_usd": 0.0,
    }


def test_economics_uses_actual_tokens_and_does_not_invent_cost_per_success() -> None:
    rows: list[dict[str, object]] = []
    for variant in ("v1", "v2"):
        rows.extend(
            (
                _row(variant, "S0", 100, False),
                _row(variant, "SI", 110, False),
                _row(variant, "SF", 80, False),
                _row(variant, "SD", 90, False),
            )
        )
    analysis: dict[str, object] = {
        "conditions": {
            "S0": {"hidden_test_accuracy": 0.5, "end_to_end_success_rate": 0.0},
            "SI": {"hidden_test_accuracy": 0.5, "end_to_end_success_rate": 0.0},
            "SF": {"hidden_test_accuracy": 0.5, "end_to_end_success_rate": 0.0},
            "SD": {"hidden_test_accuracy": 0.5, "end_to_end_success_rate": 0.0},
        }
    }
    result = economic_analysis(rows, analysis)
    paired = cast(dict[str, dict[str, object]], result["paired_token_economics"])
    resources = cast(dict[str, dict[str, object]], result["resource_costs"])
    sd = paired["SD_vs_SI"]
    assert sd["lifecycle_tes"] == 1 - 180 / 220
    assert resources["SD"]["tokens_per_successful_task"] is None
    assert result["gate_3_verdict"] == "FAIL"
    assert result["market_demand"] == "NOT EVALUATED"


def test_economics_counts_orphaned_model_calls_as_operational_overhead() -> None:
    rows = [
        _row("v1", "S0", 100, False),
        _row("v1", "SI", 110, False),
        _row("v1", "SF", 80, False),
        _row("v1", "SD", 90, False),
    ]
    analysis: dict[str, object] = {
        "conditions": {
            condition: {"hidden_test_accuracy": 0.5, "end_to_end_success_rate": 0.0}
            for condition in ("S0", "SI", "SF", "SD")
        }
    }
    sessions: list[dict[str, object]] = []
    for row in rows:
        prompt_tokens = cast(int, row["actual_prompt_tokens"])
        latency_ns = cast(int, row["actual_latency_ns"])
        for _ in range(3):
            sessions.append(
                {
                    "actual_usage": {
                        "prompt_eval_count": prompt_tokens // 3,
                        "eval_count": 10 // 3,
                    },
                    "request_latency_ns": latency_ns // 3,
                }
            )
    sessions.append(
        {
            "actual_usage": {"prompt_eval_count": 50, "eval_count": 5},
            "request_latency_ns": 7_000_000_000,
        }
    )
    result = economic_analysis(rows, analysis, sessions)
    overhead = cast(dict[str, object], result["study_execution_overhead"])
    assert overhead["actual_model_calls"] == 13
    assert overhead["orphaned_model_calls_after_interruption"] == 1
    assert overhead["orphaned_total_tokens"] == 47
