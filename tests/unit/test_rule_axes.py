import asyncio

import pytest

from mnemo_memory.packages.model_gateway.cascade_router import (
    AxisRoutedClassifier,
    CascadeCommittee,
    CascadeRouterError,
    ClassifierAxis,
    ClassifierResult,
    PrecomputedClassifier,
)
from mnemo_memory.packages.model_gateway.rule_axes import (
    RISK_AXIS,
    RiskTermClassifier,
    matched_risk_tags,
)


@pytest.mark.parametrize(
    ("prompt", "tag"),
    [
        ("Write the schema migration for the events table", "migration"),
        ("Add authorization checks to the export endpoint", "authorization"),
        ("Delete the stale cache rows", "deletion"),
        ("Rotate the leaked credentials", "security"),
        ("Deploy the build to production", "external_write"),
    ],
)
def test_risk_terms_map_to_the_harness_tags(prompt: str, tag: str) -> None:
    assert tag in matched_risk_tags(prompt)


def test_everyday_prompts_are_low_risk() -> None:
    for prompt in (
        "Rename the helper parse_row to parse_record in utils.py",
        "Summarize the README",
        "What did we decide about the checkpoint token budget?",
    ):
        assert matched_risk_tags(prompt) == ()
        result = asyncio.run(RiskTermClassifier().classify(RISK_AXIS, prompt))
        assert (result.label, result.escalation_score) == ("low", 0.0)


def test_risk_veto_forces_heavy_even_when_every_other_axis_is_light() -> None:
    complexity = ClassifierAxis(
        "complexity", "how hard", ("light", "heavy"), 0.6, label_scores=(0.0, 1.0)
    )
    easy = ClassifierResult("complexity", "light", -0.01, 0.0)
    classifier = AxisRoutedClassifier(
        {"complexity": PrecomputedClassifier((easy,)), "risk": RiskTermClassifier()}
    )
    committee = CascadeCommittee((complexity, RISK_AXIS), escalation_threshold=0.5)
    decision = asyncio.run(committee.classify("Delete the old audit rows", classifier))
    assert (decision.route, decision.reason) == ("heavy", "veto:risk")
    assert asyncio.run(committee.classify("Rename a local variable", classifier)).route == "light"


def test_rule_classifier_answers_only_the_risk_axis() -> None:
    other = ClassifierAxis("complexity", "how hard", ("light", "heavy"), 0.6)
    with pytest.raises(CascadeRouterError, match="RULE_AXIS_UNSUPPORTED"):
        asyncio.run(RiskTermClassifier().classify(other, "anything"))
