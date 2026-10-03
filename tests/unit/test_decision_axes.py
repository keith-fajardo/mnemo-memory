import asyncio
import hashlib
import json

import pytest

from mnemo_memory.packages.domain import EpisodicMemoryKind
from mnemo_memory.packages.model_gateway.cascade_router import (
    AxisKind,
    ClassifierResult,
    PrecomputedClassifier,
)
from mnemo_memory.packages.model_gateway.decision_axes import (
    EPISODIC_KIND,
    FILLER_CHECK_BUDGET_SECONDS,
    FILLER_QUESTION_VERSION,
    FRONT_DOOR_AXES,
    HINT_TEXT,
    MEMORY_NEED,
    NOTE_SUBSTANCE,
    NOTE_TEXT_CHARACTERS,
    SKILL_PICK_NAME,
    SKILL_PICK_NONE,
    TIER_AXES,
    NeedAnswer,
    accepted_choice,
    accepted_skill,
    hint_eligible,
    needs_from_memory_choice,
    note_text,
    should_drop_filler,
    should_drop_note,
    skill_pick_axis,
    tier_committee,
    worth_extracting,
)
from mnemo_memory.packages.model_gateway.rule_axes import RISK_AXIS


def _result(
    name: str, label: str, score: float, confidence: float | None = None
) -> ClassifierResult:
    return ClassifierResult(name, label, -0.1, score, confidence=confidence)


def test_front_door_asks_one_memory_choice_and_two_tier_axes() -> None:
    assert [axis.name for axis in FRONT_DOOR_AXES] == ["memory_need", "complexity", "tool_need"]
    assert FRONT_DOOR_AXES[0] is MEMORY_NEED
    assert MEMORY_NEED.kind is AxisKind.CHOICE
    assert [axis.name for axis in TIER_AXES] == ["complexity", "tool_need", "risk"]
    assert TIER_AXES[2] is RISK_AXIS


def test_every_catalogue_axis_fits_the_guard_text_limit() -> None:
    for axis in (*FRONT_DOOR_AXES, NOTE_SUBSTANCE, EPISODIC_KIND):
        assert len(axis.instructions) <= 400
        assert all(len(text) <= 400 for _, text in axis.criteria)


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("past_sessions", (NeedAnswer.YES, NeedAnswer.NO)),
        ("project_docs", (NeedAnswer.YES, NeedAnswer.NO)),
        ("code_structure", (NeedAnswer.NO, NeedAnswer.YES)),
        ("code_and_history", (NeedAnswer.YES, NeedAnswer.YES)),
        ("nothing", (NeedAnswer.NO, NeedAnswer.NO)),
    ],
)
def test_memory_choice_maps_every_label(
    label: str, expected: tuple[NeedAnswer, NeedAnswer]
) -> None:
    assert needs_from_memory_choice(_result("memory_need", label, 0.2, 0.8)) == expected


def test_memory_choice_below_the_confidence_bar_is_unknown() -> None:
    unknown = (NeedAnswer.UNKNOWN, NeedAnswer.UNKNOWN)
    assert needs_from_memory_choice(None) == unknown
    assert needs_from_memory_choice(_result("memory_need", "nothing", 0.0)) == unknown
    assert needs_from_memory_choice(_result("memory_need", "nothing", 0.0, 0.59)) == unknown
    assert needs_from_memory_choice(_result("memory_need", "nothing", 0.0, 0.6)) == (
        NeedAnswer.NO,
        NeedAnswer.NO,
    )


def test_note_text_collapses_whitespace_and_caps_length() -> None:
    text = note_text("  heading \n\n" + "x" * 500)
    assert text.startswith("heading x")
    assert len(text) == NOTE_TEXT_CHARACTERS == 300
    with pytest.raises(ValueError):
        note_text("  \n ")


def test_note_substance_scores_filler_as_the_escalation_side() -> None:
    assert NOTE_SUBSTANCE.allowed_labels == ("task_information", "filler")
    assert NOTE_SUBSTANCE.label_scores == (0.0, 1.0)


def test_filler_is_dropped_only_at_a_confident_score() -> None:
    assert should_drop_note(None) is False
    assert should_drop_note(_result("note_substance", "filler", 0.69)) is False
    assert should_drop_note(_result("note_substance", "filler", 0.7)) is True


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


def test_filler_check_has_a_total_time_budget() -> None:
    assert FILLER_CHECK_BUDGET_SECONDS == 0.8


def test_skill_pick_axis_lists_skills_then_none() -> None:
    axis = skill_pick_axis(("release-notes", "test-plan"))
    assert axis is not None
    assert axis.name == SKILL_PICK_NAME == "skill_pick"
    assert axis.allowed_labels == ("release-notes", "test-plan", SKILL_PICK_NONE)
    assert axis.criteria == ()


@pytest.mark.parametrize(
    "names",
    [
        (),
        tuple(f"skill-{index:02d}" for index in range(33)),
        ("release-notes", "release-notes"),
        ("none",),
        ("Release Notes",),
        ("x" * 65,),
    ],
)
def test_skill_pick_axis_refuses_unusable_skill_lists(names: tuple[str, ...]) -> None:
    assert skill_pick_axis(names) is None


def test_thirty_two_skills_is_the_largest_axis() -> None:
    axis = skill_pick_axis(tuple(f"skill-{index:02d}" for index in range(32)))
    assert axis is not None and len(axis.allowed_labels) == 33


def test_accepted_skill_needs_the_bar_and_the_skill_axis() -> None:
    assert accepted_skill(_result("skill_pick", "test-plan", 0.0, 0.6)) == "test-plan"
    assert accepted_skill(_result("skill_pick", "none", 0.0, 0.9)) == "none"
    assert accepted_skill(_result("skill_pick", "test-plan", 0.0, 0.59)) is None
    assert accepted_skill(_result("memory_need", "nothing", 0.0, 0.9)) is None
    assert accepted_skill(None) is None


# sha256 of the NOTE_SUBSTANCE wording below; bump FILLER_QUESTION_VERSION with it.
NOTE_SUBSTANCE_WORDING_SHA256 = "90a368ed40b38da5197a673f1740058cced0f4b13f1f838356a0b9a3b475724b"


def test_the_filler_question_version_pins_its_wording() -> None:
    """Changing NOTE_SUBSTANCE must bump FILLER_QUESTION_VERSION, so cached verdicts lapse."""

    axis = NOTE_SUBSTANCE
    wording = json.dumps(
        [
            axis.name,
            axis.instructions,
            list(axis.allowed_labels),
            [list(pair) for pair in axis.criteria],
            list(axis.label_scores) if axis.label_scores else None,
        ],
        sort_keys=True,
    )
    digest = hashlib.sha256(wording.encode()).hexdigest()
    assert (FILLER_QUESTION_VERSION, digest) == (1, NOTE_SUBSTANCE_WORDING_SHA256)


def test_a_cached_filler_probability_drops_only_at_the_bar() -> None:
    assert should_drop_filler(None) is False
    assert should_drop_filler(0.69) is False
    assert should_drop_filler(0.7) is True
    assert should_drop_note(_result("note_substance", "filler", 0.7)) is True
