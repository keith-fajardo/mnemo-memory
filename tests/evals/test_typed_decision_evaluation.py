"""Scoring and provenance checks for the phase-1 typed-decision evaluation (no network)."""

from __future__ import annotations

from typing import Any

import pytest

from mnemo_memory.packages.model_gateway.decision_axes import NeedAnswer
from scripts.typed_decision_evaluation import (
    ROUTING_FIXTURE,
    TELEHEALTH_FIXTURE,
    TYPED_DECISION_FIXTURE,
    VIABILITY_FIXTURE,
    ExtractionRow,
    FixtureProvenanceError,
    FrontDoorRow,
    RelevanceRow,
    TierRow,
    derived_route,
    load_synthetic_fixture,
    phase_one_complete,
    score_extraction,
    score_front_door,
    score_latency,
    score_relevance,
    score_tier,
)

Y, N, U = NeedAnswer.YES, NeedAnswer.NO, NeedAnswer.UNKNOWN


def test_only_fixtures_with_synthetic_provenance_are_accepted() -> None:
    for path in (ROUTING_FIXTURE, VIABILITY_FIXTURE, TYPED_DECISION_FIXTURE):
        assert isinstance(load_synthetic_fixture(path), dict)
    with pytest.raises(FixtureProvenanceError):
        load_synthetic_fixture(TELEHEALTH_FIXTURE)


def test_derived_route_prefers_structure_then_long_term() -> None:
    assert derived_route(Y, Y) == "structure"
    assert derived_route(N, Y) == "long_term"
    assert derived_route(N, N) == "none"
    assert derived_route(U, N) == "unknown"


def _front(
    expected: str, structure: NeedAnswer, long_term: NeedAnswer, n: int
) -> list[FrontDoorRow]:
    return [
        FrontDoorRow(f"{expected}-{i}", expected, structure, long_term, 100, None) for i in range(n)
    ]


def test_front_door_scoring_applies_the_existing_routing_gates() -> None:
    perfect = (
        _front("prior_memory", N, Y, 15)
        + _front("knowledge", N, Y, 15)
        + _front("structure", Y, N, 15)
        + _front("none", N, N, 15)
    )
    score = score_front_door(perfect)
    assert score["accuracy"] == 1.0 and all(score["gates"].values())

    leaky = [*perfect[:-1], FrontDoorRow("prior-x", "prior_memory", N, N, 100, None)]
    assert score_front_door(leaky)["gates"]["none_precision"] is False

    cautious = _front("prior_memory", N, Y, 15) + _front("none", U, U, 15)
    assert score_front_door(cautious)["none_precision"] == 1.0  # nothing predicted none


def test_latency_uses_nearest_rank_and_the_600ms_share() -> None:
    passing = score_latency([100] * 57 + [700] * 3)
    assert (passing["p95_ms"], passing["share_over_deadline"]) == (100, 0.05)
    assert all(passing["gates"].values())
    assert score_latency([100] * 56 + [700] * 4)["gates"]["share_over_deadline"] is False
    assert score_latency([100] * 49)["gates"]["samples"] is False


def test_relevance_gate_fails_if_any_relevant_candidate_is_dropped() -> None:
    rows = [
        RelevanceRow("t", "a", "relevant", False, True),
        RelevanceRow("t", "n", "noise", True, True),
    ]
    assert score_relevance(rows)["gates"]["relevant_dropped"] is True
    rows.append(RelevanceRow("t", "b", "relevant", True, True))
    assert score_relevance(rows)["gates"]["relevant_dropped"] is False


def test_relevance_gate_fails_when_any_row_is_unanswered() -> None:
    rows = [
        RelevanceRow("t", "a", "relevant", False, True),
        RelevanceRow("t", "n", "noise", False, False),
    ]
    score = score_relevance(rows)
    assert score["unanswered"] == 1
    assert score["gates"]["answered"] is False
    assert score["gates"]["relevant_dropped"] is True
    assert score_relevance([])["gates"]["answered"] is False


def test_extraction_gate_needs_the_ollama_baseline() -> None:
    jev = [ExtractionRow("jev", "e1", True, "decision", True, "decision", True)]
    assert score_extraction(jev)["gates"] == {"baseline": "not_evaluated"}
    ollama = [ExtractionRow("ollama", "e1", True, "decision", False, None, True)]
    assert all(value is True for value in score_extraction(jev + ollama)["gates"].values())


def test_extraction_gates_fail_when_an_arm_has_unanswered_rows() -> None:
    jev = ExtractionRow("jev", "e1", True, "decision", True, "decision", True)
    ollama = ExtractionRow("ollama", "e1", True, "decision", False, None, True)
    lost_jev = ExtractionRow("jev", "e2", True, "decision", True, None, False)
    lost_ollama = ExtractionRow("ollama", "e2", True, "decision", False, None, False)

    jev_down = score_extraction([jev, lost_jev, ollama])
    assert jev_down["arms"]["jev"]["unanswered"] == 1
    assert jev_down["gates"]["jev_answered"] is False
    assert jev_down["gates"]["baseline_answered"] is True

    baseline_down = score_extraction([jev, ollama, lost_ollama])
    assert baseline_down["arms"]["ollama"]["unanswered"] == 1
    assert baseline_down["gates"]["baseline_answered"] is False
    assert baseline_down["gates"]["jev_answered"] is True


def test_tier_gate_requires_heavy_recall_of_095() -> None:
    rows = [
        TierRow(f"h{i}", "heavy", "edit", "heavy", "threshold", "edit", False) for i in range(19)
    ]
    rows.append(TierRow("h19", "heavy", "edit", "light", "light", "edit", False))
    assert score_tier(rows)["gates"]["heavy_recall"] is True  # 19/20 = 0.95
    rows.append(TierRow("h20", "heavy", "edit", "light", "light", "edit", False))
    assert score_tier(rows)["gates"]["heavy_recall"] is False


def test_tier_gate_fails_when_any_case_was_unavailable() -> None:
    rows = [TierRow("h0", "heavy", "edit", "heavy", "threshold", "edit", False)]
    assert score_tier(rows)["gates"]["answered"] is True
    rows.append(TierRow("h1", "heavy", "edit", "heavy", "unavailable:timeout", None, False))
    assert score_tier(rows)["gates"]["answered"] is False
    assert score_tier([])["gates"]["answered"] is False


def test_phase_one_completion_requires_every_gate_true() -> None:
    report: dict[str, Any] = {
        section: {"gates": {"g": True}}
        for section in ("front_door", "latency", "relevance", "extraction", "tier")
    }
    assert phase_one_complete(report) is True
    report["extraction"]["gates"] = {"baseline": "not_evaluated"}
    assert phase_one_complete(report) is False
