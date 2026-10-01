"""Scoring and provenance checks for the phase-1 typed-decision evaluation (no network)."""

from __future__ import annotations

import asyncio
import json
import threading
import time
import urllib.error
from typing import Any
from uuid import UUID

import pytest

from mnemo_memory.packages.domain import (
    ModelBudgetReservation,
    ModelTaskType,
    TypedDecisionDataRoute,
    TypedDecisionSource,
    WorkspaceId,
)
from mnemo_memory.packages.model_gateway.cascade_router import ClassifierAxis, ClassifierResult
from mnemo_memory.packages.model_gateway.decision_axes import NeedAnswer
from mnemo_memory.packages.model_gateway.typed_decisions import (
    AdapterAnswer,
    GuardedTypedDecisionClassifier,
)
from scripts.typed_decision_evaluation import (
    HOLDOUT_FIXTURE,
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
    evaluate_extraction_ollama,
    evaluate_relevance_holdout,
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
    for path in (ROUTING_FIXTURE, VIABILITY_FIXTURE, TYPED_DECISION_FIXTURE, HOLDOUT_FIXTURE):
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

    # Nothing predicted "none": precision is undefined, so it must not pass the gate by default.
    cautious = score_front_door(_front("prior_memory", N, Y, 15) + _front("none", U, U, 15))
    assert (cautious["none_predicted"], cautious["none_precision"]) == (0, 0.0)
    assert cautious["gates"]["none_precision"] is False


def test_latency_uses_nearest_rank_and_the_600ms_share() -> None:
    passing = score_latency([100] * 57 + [700] * 3)
    assert (passing["p95_ms"], passing["share_over_deadline"]) == (100, 0.05)
    assert all(passing["gates"].values())
    assert score_latency([100] * 56 + [700] * 4)["gates"]["share_over_deadline"] is False
    assert score_latency([100] * 49)["gates"]["samples"] is False


def test_relevance_gate_fails_if_any_relevant_candidate_is_dropped() -> None:
    rows = [
        RelevanceRow("t", "a", "relevant", False, True, 100),
        RelevanceRow("t", "n", "noise", True, True, 100),
    ]
    assert score_relevance(rows)["gates"]["relevant_dropped"] is True
    rows.append(RelevanceRow("t", "b", "relevant", True, True, 100))
    assert score_relevance(rows)["gates"]["relevant_dropped"] is False


def test_relevance_gate_fails_when_any_row_is_unanswered() -> None:
    rows = [
        RelevanceRow("t", "a", "relevant", False, True, 100),
        RelevanceRow("t", "n", "noise", True, False, 100),
    ]
    score = score_relevance(rows)
    assert score["unanswered"] == 1
    assert score["gates"]["answered"] is False
    assert score["gates"]["relevant_dropped"] is True
    assert score_relevance([])["gates"]["answered"] is False


def test_relevance_gate_fails_when_no_noise_is_dropped() -> None:
    rows = [
        RelevanceRow("t", "a", "relevant", False, True, 100),
        RelevanceRow("t", "n", "noise", False, True, 100),
    ]
    score = score_relevance(rows)
    assert score["gates"]["filler_removed"] is False
    assert score["noise_dropped_share"] is None  # nothing dropped: the share is undefined
    assert score["gates"]["relevant_dropped"] is True
    assert score["gates"]["answered"] is True
    assert score_relevance(rows[:1])["gates"]["filler_removed"] is False  # no noise rows


def test_relevance_filler_gate_needs_ninety_percent_of_noise_dropped() -> None:
    relevant = RelevanceRow("t", "a", "relevant", False, True, 100)
    noise = [RelevanceRow("t", f"n{i}", "noise", i != 0, True, 100) for i in range(10)]
    score = score_relevance([relevant, *noise])
    assert score["noise_drop_rate"] == 0.9
    assert score["noise_dropped_share"] == 1.0
    assert score["gates"]["filler_removed"] is True
    noise[1] = RelevanceRow("t", "n1", "noise", False, True, 100)
    assert score_relevance([relevant, *noise])["gates"]["filler_removed"] is False


class _FakeProvider:
    def __init__(self, response: object = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error

    def generate(self, request: object) -> object:
        if self.error is not None:
            raise self.error
        return self.response


def test_relevance_gate_fails_when_filler_checks_run_over_the_budget() -> None:
    def rows(over: int) -> list[RelevanceRow]:
        return [
            RelevanceRow("t", f"n{i}", "noise", True, True, 801 if i < over else 800)
            for i in range(20)
        ]

    ok = score_relevance(rows(1))
    assert ok["over_budget_share"] == 0.05  # 800 ms is within the 0.8 s budget, 801 is not
    assert ok["gates"]["within_budget"] is True
    slow = score_relevance(rows(2))
    assert slow["over_budget_share"] == 0.1
    assert slow["gates"]["within_budget"] is False
    empty = score_relevance([])
    assert (empty["over_budget_share"], empty["gates"]["within_budget"]) == (0.0, False)


class _ConcurrencyProbe:
    """A slow adapter that records how many requests were in flight at once."""

    provider_id = "probe"
    model_id = "probe-1"

    def __init__(self, pause: float) -> None:
        self.pause = pause
        self.lock = threading.Lock()
        self.in_flight = 0
        self.most_in_flight = 0

    def answer(
        self, axes: tuple[ClassifierAxis, ...], text: str, *, timeout_seconds: float
    ) -> AdapterAnswer:
        with self.lock:
            self.in_flight += 1
            self.most_in_flight = max(self.most_in_flight, self.in_flight)
        time.sleep(self.pause)
        with self.lock:
            self.in_flight -= 1
        return AdapterAnswer(
            tuple(ClassifierResult(axis.name, axis.allowed_labels[0], 0.0, 0.9) for axis in axes),
            "probe-1",
            1,
        )


class _OpenBudget:
    def reserve(
        self,
        workspace_id: WorkspaceId,
        task_type: ModelTaskType,
        reservation: ModelBudgetReservation,
    ) -> None:
        return None


def test_note_checks_run_concurrently_in_chunks_of_sixteen() -> None:
    probe = _ConcurrencyProbe(pause=0.1)
    guard = GuardedTypedDecisionClassifier(
        probe,
        data_route=TypedDecisionDataRoute.SYNTHETIC_ONLY,
        source=TypedDecisionSource.SYNTHETIC_FIXTURE,
        budget=_OpenBudget(),
        workspace_id=WorkspaceId(UUID(int=1)),
        reservation=ModelBudgetReservation(input_tokens=1, output_tokens=1, cost_microusd=0),
        deadline_seconds=5.0,
    )
    notes = load_synthetic_fixture(HOLDOUT_FIXTURE)["notes"]
    assert len(notes) == 24  # one full chunk of 16, then 8
    started = time.perf_counter()
    rows = asyncio.run(evaluate_relevance_holdout(guard))
    elapsed = time.perf_counter() - started
    assert probe.most_in_flight == 16
    assert elapsed < 1.0  # two chunks of ~0.1 s, not 24 sequential 0.1 s calls
    assert [row.candidate_id for row in rows] == [note["id"] for note in notes]
    assert all(row.answered for row in rows)


def test_relevance_reports_request_latency_and_count() -> None:
    rows = [RelevanceRow("t", f"n{i}", "noise", True, True, 100 + i) for i in range(20)]
    score = score_relevance(rows)
    assert (score["requests"], score["request_p50_ms"], score["request_p95_ms"]) == (20, 109, 118)
    empty = score_relevance([])
    assert (empty["requests"], empty["request_p50_ms"], empty["request_p95_ms"]) == (0, None, None)


def test_ollama_wrong_shape_reply_is_answered_with_a_format_failure() -> None:
    replies: tuple[object, ...] = ({"bad": 1}, [{"candidates": []}])
    for reply in replies:
        rows = evaluate_extraction_ollama(_FakeProvider(reply))
        assert rows and all(row.answered and row.format_failure for row in rows)
        assert all(row.predicted_worth is False and row.predicted_kind is None for row in rows)
        assert score_extraction(rows)["arms"]["ollama"]["unanswered"] == 0


def test_ollama_non_json_reply_is_answered_with_a_format_failure() -> None:
    error = json.JSONDecodeError("Expecting value", "not json", 0)
    rows = evaluate_extraction_ollama(_FakeProvider(error=error))
    assert rows and all(row.answered and row.format_failure for row in rows)


def test_ollama_connection_and_unknown_failures_are_unanswered() -> None:
    for error in (urllib.error.URLError("down"), TimeoutError("slow"), ConnectionRefusedError()):
        rows = evaluate_extraction_ollama(_FakeProvider(error=error))
        assert rows and not any(row.answered or row.format_failure for row in rows)
        assert score_extraction(rows)["arms"]["ollama"]["unanswered"] == len(rows)
    rows = evaluate_extraction_ollama(_FakeProvider(error=RuntimeError("odd")))
    assert not any(row.answered for row in rows)


class _ScriptedProvider:
    """Raises the scripted errors on the first calls, then returns a valid empty reply."""

    def __init__(self, errors: list[Exception]) -> None:
        self.errors = errors
        self.calls = 0

    def generate(self, request: object) -> object:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return {"candidates": []}


def test_ollama_transport_error_is_retried_once_and_can_recover() -> None:
    provider = _ScriptedProvider([urllib.error.URLError("blip")])
    rows = evaluate_extraction_ollama(provider)
    assert provider.calls == len(rows) + 1
    assert rows[0].answered and rows[0].retried and not rows[0].format_failure
    assert not any(row.retried for row in rows[1:])
    arm = score_extraction(rows)["arms"]["ollama"]
    assert (arm["retries"], arm["unanswered"]) == (1, 0)


def test_ollama_row_is_unanswered_when_the_retry_also_fails() -> None:
    provider = _ScriptedProvider([urllib.error.URLError("down"), urllib.error.URLError("down")])
    rows = evaluate_extraction_ollama(provider)
    assert not rows[0].answered and rows[0].retried
    assert all(row.answered for row in rows[1:])


def test_ollama_format_failure_is_not_retried() -> None:
    provider = _ScriptedProvider([ValueError("bad json")])
    rows = evaluate_extraction_ollama(provider)
    assert provider.calls == len(rows)
    assert rows[0].answered and rows[0].format_failure and not rows[0].retried


def test_baseline_valid_gate_needs_ninety_percent_well_formed_output() -> None:
    jev = ExtractionRow("jev", "e0", True, "decision", True, "decision", True)

    def ollama(bad: int) -> list[ExtractionRow]:
        return [
            ExtractionRow("ollama", f"e{i}", True, "decision", False, None, True, i < bad)
            for i in range(20)
        ]

    ok = score_extraction([jev, *ollama(2)])
    assert ok["arms"]["ollama"]["format_failures"] == 2
    assert ok["gates"]["baseline_valid"] is True
    assert score_extraction([jev, *ollama(3)])["gates"]["baseline_valid"] is False
    assert score_extraction([jev, *ollama(20)])["gates"]["baseline_valid"] is False
    assert score_extraction([jev])["gates"] == {"baseline": "not_evaluated"}


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
        for section in (
            "front_door",
            "front_door_holdout",
            "latency",
            "relevance",
            "relevance_holdout",
            "extraction",
            "tier",
        )
    }
    assert phase_one_complete(report) is True
    report["extraction"]["gates"] = {"baseline": "not_evaluated"}
    assert phase_one_complete(report) is False
    report["extraction"]["gates"] = {"g": True}
    report["relevance_holdout"]["gates"] = {"g": False}
    assert phase_one_complete(report) is False
