"""Cost-weighted deterministic acceptance evaluation for automatic context routing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from mnemo_memory.packages.application.context_routing import (
    AutomaticContextNeed,
    choose_automatic_context_route,
    plan_automatic_context_needs,
)

FIXTURE = Path(__file__).parents[1] / "fixtures/evals/automatic-context-routing-v1.json"


def _cases() -> list[dict[str, str]]:
    value = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert value["fixture_kind"] == "synthetic-routing-acceptance"
    assert value["provenance"] == {
        "origin": "Mnemo-owned original synthetic prompts",
        "competing_product_artifacts_used": False,
    }
    return cast(list[dict[str, str]], value["cases"])


def test_router_fixture_is_balanced_unique_and_content_free() -> None:
    cases = _cases()

    assert len(cases) == 60
    assert len({case["id"] for case in cases}) == len(cases)
    assert len({case["prompt"] for case in cases}) == len(cases)
    assert {
        route: sum(case["expected_route"] == route for case in cases)
        for route in {"prior_memory", "knowledge", "structure", "none"}
    } == {"prior_memory": 15, "knowledge": 15, "structure": 15, "none": 15}


def test_router_meets_cost_weighted_recall_and_no_memory_precision_gates() -> None:
    rows = [
        (case["expected_route"], choose_automatic_context_route(case["prompt"]).route.value)
        for case in _cases()
    ]
    predicted_none = [row for row in rows if row[1] == "none"]
    correct = sum(expected == actual for expected, actual in rows)
    prior_recall = sum(expected == actual == "prior_memory" for expected, actual in rows) / 15
    structure_recall = sum(expected == actual == "structure" for expected, actual in rows) / 15
    none_precision = sum(expected == actual == "none" for expected, actual in predicted_none) / len(
        predicted_none
    )

    assert correct / len(rows) >= 0.80
    assert prior_recall >= 0.90
    assert structure_recall >= 0.80
    assert none_precision == 1.0
    assert all(not (actual == "none" and expected != "none") for expected, actual in rows)


def test_shadow_planner_meets_combined_axis_and_shared_cost_gates() -> None:
    cases = (
        (
            "Use the previous architecture decision and show which modules depend on this service.",
            AutomaticContextNeed.YES,
            AutomaticContextNeed.YES,
        ),
        (
            "Check the documented policy and trace every caller of the affected function.",
            AutomaticContextNeed.YES,
            AutomaticContextNeed.YES,
        ),
        (
            "Resume the prior task and inspect downstream files for the chosen adapter.",
            AutomaticContextNeed.YES,
            AutomaticContextNeed.YES,
        ),
        (
            "What did we decide in the earlier session?",
            AutomaticContextNeed.UNKNOWN,
            AutomaticContextNeed.YES,
        ),
        (
            "Show the dependency graph around this package.",
            AutomaticContextNeed.YES,
            AutomaticContextNeed.UNKNOWN,
        ),
        (
            "Translate this complete sentence into French.",
            AutomaticContextNeed.NO,
            AutomaticContextNeed.NO,
        ),
    )

    for prompt, expected_structure, expected_long_term in cases:
        live_before = choose_automatic_context_route(prompt)
        shadow = plan_automatic_context_needs(prompt)
        live_after = choose_automatic_context_route(prompt)
        assert shadow.structural_need is expected_structure
        assert shadow.long_term_need is expected_long_term
        assert shadow.structural_tokens + shadow.long_term_tokens <= 1_300
        assert live_after == live_before
