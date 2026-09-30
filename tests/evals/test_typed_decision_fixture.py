"""Shape checks for the Mnemo-owned synthetic typed-decision fixture."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mnemo_memory.packages.model_gateway.rule_axes import matched_risk_tags

FIXTURE = Path(__file__).parents[1] / "fixtures/evals/typed-decision-v1.json"


def _fixture() -> dict[str, Any]:
    value = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_fixture_declares_synthetic_provenance() -> None:
    value = _fixture()
    assert value["schema_version"] == 1
    assert value["fixture_kind"] == "synthetic-typed-decision-acceptance"
    assert value["provenance"] == {
        "origin": "Mnemo-owned original synthetic prompts",
        "competing_product_artifacts_used": False,
    }


def test_tier_cases_are_balanced_unique_and_labelled() -> None:
    cases = _fixture()["tier_cases"]
    assert len(cases) == 40
    assert len({case["id"] for case in cases}) == 40
    assert len({case["prompt"] for case in cases}) == 40
    assert sum(case["expected_tier"] == "heavy" for case in cases) == 20
    assert {case["expected_tier"] for case in cases} == {"light", "heavy"}
    assert {case["expected_tool_need"] for case in cases} == {"none", "read_heavy", "edit"}


def test_light_cases_carry_no_risk_terms_and_ten_heavy_cases_do() -> None:
    cases = _fixture()["tier_cases"]
    light = [case for case in cases if case["expected_tier"] == "light"]
    heavy = [case for case in cases if case["expected_tier"] == "heavy"]
    assert all(matched_risk_tags(case["prompt"]) == () for case in light)
    assert sum(bool(matched_risk_tags(case["prompt"])) for case in heavy) == 10


def test_worth_negatives_are_unique_no_op_events() -> None:
    negatives = _fixture()["worth_negatives"]
    assert len(negatives) == 12
    assert len({item["id"] for item in negatives}) == 12
    assert len({item["summary"] for item in negatives}) == 12
    assert all(matched_risk_tags(item["summary"]) == () for item in negatives)
