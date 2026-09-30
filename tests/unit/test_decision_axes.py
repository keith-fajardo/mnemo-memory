import asyncio

import pytest

from mnemo_memory.packages.domain import EpisodicMemoryKind
from mnemo_memory.packages.model_gateway.cascade_router import (
    AxisKind,
    ClassifierResult,
    PrecomputedClassifier,
)
from mnemo_memory.packages.model_gateway.decision_axes import (
    EPISODIC_KIND,
    FRONT_DOOR_AXES,
    HINT_TEXT,
    TIER_AXES,
    NeedAnswer,
    accepted_choice,
    hint_eligible,
    need_from_result,
    relevance_axis,
    should_drop_candidate,
    tier_committee,
    worth_extracting,
)
from mnemo_memory.packages.model_gateway.rule_axes import RISK_AXIS


def _result(
    name: str, label: str, score: float, confidence: float | None = None
) -> ClassifierResult:
    return ClassifierResult(name, label, -0.1, score, confidence=confidence)


def test_front_door_asks_two_needs_and_two_tier_axes() -> None:
    assert [axis.name for axis in FRONT_DOOR_AXES] == [
        "needs_long_term",
        "needs_structure",
        "complexity",
        "tool_need",
    ]
    assert [axis.kind for axis in FRONT_DOOR_AXES[:2]] == [AxisKind.YES_NO, AxisKind.YES_NO]
    assert [axis.name for axis in TIER_AXES] == ["complexity", "tool_need", "risk"]
    assert TIER_AXES[2] is RISK_AXIS


def test_every_catalogue_axis_fits_the_guard_text_limit() -> None:
    for axis in (*FRONT_DOOR_AXES, EPISODIC_KIND):
        assert len(axis.instructions) <= 400
        assert all(len(text) <= 400 for _, text in axis.criteria)


@pytest.mark.parametrize(
    ("probability", "expected"),
    [
        (0.7, NeedAnswer.YES),
        (0.69, NeedAnswer.UNKNOWN),
        (0.31, NeedAnswer.UNKNOWN),
        (0.3, NeedAnswer.NO),
    ],
)
def test_need_thresholds(probability: float, expected: NeedAnswer) -> None:
    assert need_from_result(_result("needs_long_term", "yes", probability)) is expected


def test_missing_need_answer_is_unknown() -> None:
    assert need_from_result(None) is NeedAnswer.UNKNOWN


def test_relevance_axis_embeds_a_bounded_snippet() -> None:
    axis = relevance_axis(3, "  heading \n\n" + "x" * 500)
    assert axis.name == "helps_3"
    assert axis.kind is AxisKind.YES_NO
    assert axis.instructions.startswith("This stored note would help answer the request: heading x")
    assert len(axis.instructions) <= 400
    for bad_index in (-1, 32):
        with pytest.raises(ValueError):
            relevance_axis(bad_index, "note")
    with pytest.raises(ValueError):
        relevance_axis(0, "   ")


def test_relevance_drops_only_a_confident_no() -> None:
    assert should_drop_candidate(None) is False
    assert should_drop_candidate(_result("helps_0", "no", 0.2)) is True
    assert should_drop_candidate(_result("helps_0", "no", 0.21)) is False


def test_worth_extracting_skips_only_a_confident_no() -> None:
    assert worth_extracting(None) is True
    assert worth_extracting(_result("worth_remembering", "no", 0.3)) is False
    assert worth_extracting(_result("worth_remembering", "no", 0.31)) is True


def test_choice_answers_need_confidence() -> None:
    assert accepted_choice(None) is None
    assert accepted_choice(_result("tool_need", "read_heavy", 0.2)) is None
    assert accepted_choice(_result("tool_need", "read_heavy", 0.2, 0.59)) is None
    assert accepted_choice(_result("tool_need", "read_heavy", 0.2, 0.6)) == "read_heavy"


def test_hint_only_for_light_reading_heavy_tasks() -> None:
    reading = _result("tool_need", "read_heavy", 0.2, 0.7)
    assert hint_eligible("light", reading) is True
    assert hint_eligible("heavy", reading) is False
    assert hint_eligible("light", _result("tool_need", "edit", 0.7, 0.7)) is False
    assert hint_eligible("light", _result("tool_need", "read_heavy", 0.2, 0.5)) is False
    assert HINT_TEXT == "Mnemo: light, reading-heavy task; a Haiku subagent could do the reading."
    assert (len(HINT_TEXT) + 3) // 4 <= 40


def test_tier_committee_uses_the_spec_weights_and_threshold() -> None:
    low_risk = _result("risk", "low", 0.0)
    heavy = PrecomputedClassifier(
        (_result("complexity", "heavy", 0.8), _result("tool_need", "read_heavy", 0.205), low_risk)
    )
    light = PrecomputedClassifier(
        (_result("complexity", "light", 0.2), _result("tool_need", "read_heavy", 0.205), low_risk)
    )
    assert asyncio.run(tier_committee().classify("task", heavy)).route == "heavy"  # 0.562
    assert asyncio.run(tier_committee().classify("task", light)).route == "light"  # 0.202


def test_episodic_kind_labels_match_the_domain() -> None:
    assert EPISODIC_KIND.allowed_labels == tuple(kind.value for kind in EpisodicMemoryKind)
