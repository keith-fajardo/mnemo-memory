import asyncio
import time
from uuid import UUID

import pytest

from mnemo_memory.packages.domain import (
    ModelBudgetDenied,
    ModelBudgetReservation,
    ModelTaskType,
    Sensitivity,
    TypedDecisionDataRoute,
    TypedDecisionSource,
    TypedDecisionUnavailableReason,
    WorkspaceId,
)
from mnemo_memory.packages.model_gateway.cascade_router import (
    YES_NO_LABELS,
    AxisKind,
    AxisRoutedClassifier,
    CascadeCommittee,
    ClassifierAxis,
    ClassifierResult,
)
from mnemo_memory.packages.model_gateway.rule_axes import RISK_AXIS, RiskTermClassifier
from mnemo_memory.packages.model_gateway.typed_decisions import (
    AdapterAnswer,
    GuardedTypedDecisionClassifier,
    TypedDecisionAdapterError,
    TypedDecisionRecord,
    TypedDecisionUnavailable,
    bounded_decision_text,
    decide_tier,
)

Reason = TypedDecisionUnavailableReason
NEED = ClassifierAxis("needs_long_term", "needs memory", YES_NO_LABELS, 0.0, kind=AxisKind.YES_NO)
TIER = ClassifierAxis("complexity", "how hard", ("light", "heavy"), 0.6, label_scores=(0.0, 1.0))
WORKSPACE = WorkspaceId(UUID(int=7))
RESERVATION = ModelBudgetReservation(input_tokens=1_000, output_tokens=1, cost_microusd=0)


class FakeAdapter:
    provider_id = "fake"
    model_id = "fake-1"

    def __init__(
        self,
        *,
        answer: AdapterAnswer | None = None,
        error: Exception | None = None,
        delay: float = 0.0,
    ) -> None:
        self.calls: list[tuple[tuple[str, ...], str, float]] = []
        self._answer = answer
        self._error = error
        self._delay = delay

    def answer(
        self, axes: tuple[ClassifierAxis, ...], text: str, *, timeout_seconds: float
    ) -> AdapterAnswer:
        self.calls.append((tuple(axis.name for axis in axes), text, timeout_seconds))
        if self._delay:
            time.sleep(self._delay)
        if self._error is not None:
            raise self._error
        if self._answer is not None:
            return self._answer
        return AdapterAnswer(
            tuple(ClassifierResult(axis.name, axis.allowed_labels[-1], -0.1, 0.9) for axis in axes),
            "fake-1",
            12,
        )


class CountingBudget:
    def __init__(self, *, deny: bool = False) -> None:
        self.calls = 0
        self.deny = deny

    def reserve(
        self,
        workspace_id: WorkspaceId,
        task_type: ModelTaskType,
        reservation: ModelBudgetReservation,
    ) -> None:
        assert task_type is ModelTaskType.TYPED_DECISION
        self.calls += 1
        if self.deny:
            raise ModelBudgetDenied("denied")


class ListRecorder:
    def __init__(self) -> None:
        self.records: list[TypedDecisionRecord] = []

    def record(self, record: TypedDecisionRecord) -> None:
        self.records.append(record)


def _guard(
    adapter: FakeAdapter | None,
    *,
    source: TypedDecisionSource = TypedDecisionSource.SYNTHETIC_FIXTURE,
    budget: CountingBudget | None = None,
    recorder: ListRecorder | None = None,
    deadline: float = 1.0,
) -> GuardedTypedDecisionClassifier:
    return GuardedTypedDecisionClassifier(
        adapter,
        data_route=TypedDecisionDataRoute.SYNTHETIC_ONLY,
        source=source,
        budget=budget or CountingBudget(),
        workspace_id=WORKSPACE,
        reservation=RESERVATION,
        deadline_seconds=deadline,
        recorder=recorder,
    )


def test_runtime_text_is_blocked_before_any_other_work() -> None:
    adapter, budget, recorder = FakeAdapter(), CountingBudget(), ListRecorder()
    guard = _guard(adapter, source=TypedDecisionSource.RUNTIME, budget=budget, recorder=recorder)
    outcome = asyncio.run(guard.ask((NEED,), "api_key = abcdefghijklmnop1234"))
    assert outcome.unavailable_reason is Reason.DATA_ROUTE_BLOCKED
    assert outcome.results == ()
    assert adapter.calls == [] and budget.calls == 0
    assert [record.outcome for record in recorder.records] == ["data_route_blocked"]


def test_missing_credential_is_reported_without_budget_or_network() -> None:
    budget = CountingBudget()
    outcome = asyncio.run(_guard(None, budget=budget).ask((NEED,), "what did we decide?"))
    assert outcome.unavailable_reason is Reason.NO_CREDENTIAL
    assert budget.calls == 0


def test_secret_in_text_is_never_sent() -> None:
    adapter = FakeAdapter()
    outcome = asyncio.run(_guard(adapter).ask((NEED,), "api_key = abcdefghijklmnop1234"))
    assert outcome.unavailable_reason is Reason.SECRET_BLOCKED
    assert adapter.calls == []


def test_secret_in_axis_text_is_never_sent() -> None:
    adapter = FakeAdapter()
    leaky = ClassifierAxis(
        "helps_0",
        "This stored note would help answer the request: sk-abcdefghijklmnopqrstuvwxyz",
        YES_NO_LABELS,
        0.0,
        kind=AxisKind.YES_NO,
    )
    outcome = asyncio.run(_guard(adapter).ask((leaky,), "resume the task"))
    assert outcome.unavailable_reason is Reason.SECRET_BLOCKED
    assert adapter.calls == []


def test_non_normal_sensitivity_is_never_sent() -> None:
    adapter = FakeAdapter()
    outcome = asyncio.run(_guard(adapter).ask((NEED,), "resume", sensitivity=Sensitivity.PERSONAL))
    assert outcome.unavailable_reason is Reason.SENSITIVITY_BLOCKED
    assert adapter.calls == []


def test_long_text_is_bounded_head_and_tail() -> None:
    adapter = FakeAdapter()
    asyncio.run(_guard(adapter).ask((NEED,), "A" * 300 + "B" * 300))
    sent = adapter.calls[0][1]
    assert sent == "A" * 256 + "\n" + "B" * 255
    assert bounded_decision_text("  short  ") == "short"


def test_malformed_requests_are_schema_invalid_without_network() -> None:
    adapter = FakeAdapter()
    guard = _guard(adapter)
    oversized = ClassifierAxis("big", "x" * 401, YES_NO_LABELS, 0.0, kind=AxisKind.YES_NO)
    for axes, text in (((oversized,), "t"), ((NEED, NEED), "t"), ((), "t"), ((NEED,), "  ")):
        outcome = asyncio.run(guard.ask(axes, text))
        assert outcome.unavailable_reason is Reason.SCHEMA_INVALID
    assert adapter.calls == []


def test_budget_denial_blocks_the_call() -> None:
    adapter = FakeAdapter()
    outcome = asyncio.run(_guard(adapter, budget=CountingBudget(deny=True)).ask((NEED,), "t"))
    assert outcome.unavailable_reason is Reason.BUDGET_DENIED
    assert adapter.calls == []


def test_deadline_returns_promptly_while_adapter_still_runs() -> None:
    adapter = FakeAdapter(delay=0.5)
    started = time.perf_counter()
    outcome = asyncio.run(_guard(adapter, deadline=0.05).ask((NEED,), "t"))
    assert outcome.unavailable_reason is Reason.TIMEOUT
    assert outcome.duration_ms < 400  # ask() returned long before the 0.5 s adapter finished
    assert adapter.calls[0][2] == 0.05
    assert time.perf_counter() - started >= 0.05


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (TypedDecisionAdapterError(Reason.HTTP_ERROR), Reason.HTTP_ERROR),
        (TypedDecisionAdapterError(Reason.SCHEMA_INVALID), Reason.SCHEMA_INVALID),
        (TimeoutError(), Reason.TIMEOUT),
        (RuntimeError("boom"), Reason.HTTP_ERROR),
    ],
)
def test_adapter_errors_map_to_closed_reasons(error: Exception, reason: Reason) -> None:
    outcome = asyncio.run(_guard(FakeAdapter(error=error)).ask((NEED,), "t"))
    assert outcome.unavailable_reason is reason


def test_answers_outside_the_contract_are_schema_invalid() -> None:
    wrong_label = AdapterAnswer((ClassifierResult("needs_long_term", "maybe", -0.1, 0.5),), "v", 1)
    wrong_axis = AdapterAnswer((ClassifierResult("other", "yes", -0.1, 0.5),), "v", 1)
    for answer in (wrong_label, wrong_axis):
        outcome = asyncio.run(_guard(FakeAdapter(answer=answer)).ask((NEED,), "t"))
        assert outcome.unavailable_reason is Reason.SCHEMA_INVALID


def test_successful_answer_returns_results_and_content_free_telemetry() -> None:
    recorder = ListRecorder()
    outcome = asyncio.run(_guard(FakeAdapter(), recorder=recorder).ask((NEED, TIER), "secretless"))
    assert outcome.available
    assert [result.axis_name for result in outcome.results] == ["needs_long_term", "complexity"]
    assert outcome.model_version == "fake-1"
    (record,) = recorder.records
    assert (record.source, record.axis_count, record.outcome, record.input_tokens) == (
        "synthetic_fixture",
        2,
        "answered",
        12,
    )
    assert "secretless" not in repr(record)


def test_a_failing_recorder_never_breaks_the_caller() -> None:
    class BrokenRecorder:
        def record(self, record: TypedDecisionRecord) -> None:
            raise RuntimeError("telemetry down")

    guard = GuardedTypedDecisionClassifier(
        FakeAdapter(),
        data_route=TypedDecisionDataRoute.SYNTHETIC_ONLY,
        source=TypedDecisionSource.SYNTHETIC_FIXTURE,
        budget=CountingBudget(),
        workspace_id=WORKSPACE,
        reservation=RESERVATION,
        deadline_seconds=1.0,
        recorder=BrokenRecorder(),
    )
    assert asyncio.run(guard.ask((NEED,), "t")).available


def test_classify_batch_raises_typed_unavailable_for_committees() -> None:
    guard = _guard(FakeAdapter(), source=TypedDecisionSource.RUNTIME)
    with pytest.raises(TypedDecisionUnavailable) as caught:
        asyncio.run(guard.classify_batch((NEED,), "t"))
    assert caught.value.reason is Reason.DATA_ROUTE_BLOCKED


def test_decide_tier_falls_back_to_heavy_when_unavailable() -> None:
    committee = CascadeCommittee((TIER, RISK_AXIS), escalation_threshold=0.5)
    blocked = _guard(FakeAdapter(), source=TypedDecisionSource.RUNTIME)
    classifier = AxisRoutedClassifier({"complexity": blocked, "risk": RiskTermClassifier()})
    tier = asyncio.run(decide_tier(committee, classifier, "rename a variable"))
    assert (tier.route, tier.reason, tier.decision) == (
        "heavy",
        "unavailable:data_route_blocked",
        None,
    )

    light_answer = AdapterAnswer((ClassifierResult("complexity", "light", -0.1, 0.1),), "v", 1)
    allowed = _guard(FakeAdapter(answer=light_answer))
    classifier = AxisRoutedClassifier({"complexity": allowed, "risk": RiskTermClassifier()})
    assert asyncio.run(decide_tier(committee, classifier, "rename a variable")).route == "light"


def test_guard_rejects_invalid_configuration() -> None:
    for deadline in (0.0, 31.0):
        with pytest.raises(ValueError):
            _guard(FakeAdapter(), deadline=deadline)
