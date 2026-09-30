"""Guarded boundary for hosted typed-decision classifiers (spec §5.2, ADR 0049).

Checks run in a fixed order and each fails closed to one ``unavailable`` reason: data route,
credential, request shape, secret scan, sensitivity, size cap, budget, deadline, label check.
The guard never raises from ``ask`` and never records decision text.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from mnemo_memory.packages.domain import (
    ModelBudgetReservation,
    ModelBudgetReservationPort,
    ModelTaskType,
    Sensitivity,
    TypedDecisionDataRoute,
    TypedDecisionSource,
    TypedDecisionUnavailableReason,
    WorkspaceId,
    typed_decision_source_permitted,
)
from mnemo_memory.packages.policy.content_safety import contains_high_confidence_secret

from .cascade_router import (
    CascadeCommittee,
    CascadeRouteDecision,
    CascadeRouterError,
    ClassifierAxis,
    ClassifierResult,
    PromptClassifier,
)

MAXIMUM_TEXT_CHARACTERS = 512
MAXIMUM_AXIS_TEXT_CHARACTERS = 400
MAXIMUM_AXES = 32
_TEXT_HEAD_CHARACTERS = 256
_TEXT_TAIL_CHARACTERS = 255
_MAXIMUM_DEADLINE_SECONDS = 30.0
_MAXIMUM_MODEL_VERSION_LENGTH = 128

Reason = TypedDecisionUnavailableReason


class TypedDecisionAdapterError(RuntimeError):
    """Payload-free adapter failure that carries one closed unavailable reason."""

    def __init__(self, reason: TypedDecisionUnavailableReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


@dataclass(frozen=True, slots=True)
class AdapterAnswer:
    """One provider response already parsed into constrained axis results."""

    results: tuple[ClassifierResult, ...]
    model_version: str
    input_tokens: int


class TypedDecisionAdapter(Protocol):
    """A classifier that answers several axes about one text in one synchronous request."""

    @property
    def provider_id(self) -> str: ...

    @property
    def model_id(self) -> str: ...

    def answer(
        self, axes: tuple[ClassifierAxis, ...], text: str, *, timeout_seconds: float
    ) -> AdapterAnswer: ...


@dataclass(frozen=True, slots=True)
class TypedDecisionRecord:
    """Content-free telemetry for one guarded request: never text, labels or scores."""

    source: str
    axis_count: int
    outcome: str
    duration_ms: int
    model_version: str | None
    input_tokens: int


class TypedDecisionRecorder(Protocol):
    def record(self, record: TypedDecisionRecord) -> None: ...


@dataclass(frozen=True, slots=True)
class TypedDecisionOutcome:
    """Answers in axis order, or no answers and exactly one unavailable reason."""

    results: tuple[ClassifierResult, ...]
    unavailable_reason: TypedDecisionUnavailableReason | None
    duration_ms: int
    model_version: str | None

    @property
    def available(self) -> bool:
        return self.unavailable_reason is None


class TypedDecisionUnavailable(RuntimeError):
    """Raised only through the committee-facing ``classify`` methods."""

    def __init__(self, reason: TypedDecisionUnavailableReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


def bounded_decision_text(text: str) -> str:
    """Keep the head and tail of long text, matching the hook's 512-character routing bound."""

    value = text.strip()
    if len(value) <= MAXIMUM_TEXT_CHARACTERS:
        return value
    return value[:_TEXT_HEAD_CHARACTERS].rstrip() + "\n" + value[-_TEXT_TAIL_CHARACTERS:].lstrip()


class GuardedTypedDecisionClassifier:
    """Wrap one adapter with data-route, secret, sensitivity, budget and deadline policy."""

    def __init__(
        self,
        adapter: TypedDecisionAdapter | None,
        *,
        data_route: TypedDecisionDataRoute,
        source: TypedDecisionSource,
        budget: ModelBudgetReservationPort,
        workspace_id: WorkspaceId,
        reservation: ModelBudgetReservation,
        deadline_seconds: float,
        recorder: TypedDecisionRecorder | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        if not isinstance(data_route, TypedDecisionDataRoute) or not isinstance(
            source, TypedDecisionSource
        ):
            raise TypeError("typed decision route and source must be closed values")
        if not isinstance(workspace_id, WorkspaceId) or not isinstance(
            reservation, ModelBudgetReservation
        ):
            raise TypeError("typed decision budget scope is invalid")
        if (
            isinstance(deadline_seconds, bool)
            or not isinstance(deadline_seconds, (int, float))
            or not math.isfinite(deadline_seconds)
            or not 0.0 < deadline_seconds <= _MAXIMUM_DEADLINE_SECONDS
        ):
            raise ValueError("typed decision deadline must be within (0, 30] seconds")
        self._adapter = adapter
        self._data_route = data_route
        self._source = source
        self._budget = budget
        self._workspace_id = workspace_id
        self._reservation = reservation
        self._deadline = float(deadline_seconds)
        self._recorder = recorder
        self._clock = clock

    async def ask(
        self,
        axes: Sequence[ClassifierAxis],
        text: str,
        *,
        sensitivity: Sensitivity = Sensitivity.NORMAL,
    ) -> TypedDecisionOutcome:
        """Return answers in axis order or one closed unavailable reason; never raises."""

        started = self._clock()
        axis_tuple = tuple(axes)
        try:
            reason, answer = await self._ask(axis_tuple, text, sensitivity)
        except Exception:
            reason, answer = Reason.HTTP_ERROR, None
        duration_ms = max(0, round((self._clock() - started) * 1_000))
        outcome = TypedDecisionOutcome(
            () if answer is None else answer.results,
            reason,
            duration_ms,
            None if answer is None else answer.model_version,
        )
        self._record(len(axis_tuple), outcome, 0 if answer is None else answer.input_tokens)
        return outcome

    async def classify_batch(
        self, axes: tuple[ClassifierAxis, ...], prompt: str
    ) -> tuple[ClassifierResult, ...]:
        outcome = await self.ask(axes, prompt)
        if outcome.unavailable_reason is not None:
            raise TypedDecisionUnavailable(outcome.unavailable_reason)
        return outcome.results

    async def classify(self, axis: ClassifierAxis, prompt: str) -> ClassifierResult:
        return (await self.classify_batch((axis,), prompt))[0]

    async def _ask(
        self, axes: tuple[ClassifierAxis, ...], text: object, sensitivity: Sensitivity
    ) -> tuple[TypedDecisionUnavailableReason | None, AdapterAnswer | None]:
        adapter = self._adapter
        if not typed_decision_source_permitted(self._data_route, self._source):
            return Reason.DATA_ROUTE_BLOCKED, None
        if adapter is None:
            return Reason.NO_CREDENTIAL, None
        if not isinstance(text, str) or not _request_shape_valid(axes, text):
            return Reason.SCHEMA_INVALID, None
        axis_texts = tuple(
            value
            for axis in axes
            for value in (axis.instructions, *(description for _, description in axis.criteria))
        )
        if contains_high_confidence_secret(text, *axis_texts):
            return Reason.SECRET_BLOCKED, None
        if sensitivity is not Sensitivity.NORMAL:
            return Reason.SENSITIVITY_BLOCKED, None
        if any(len(value) > MAXIMUM_AXIS_TEXT_CHARACTERS for value in axis_texts):
            return Reason.SCHEMA_INVALID, None
        bounded = bounded_decision_text(text)
        try:
            self._budget.reserve(
                self._workspace_id, ModelTaskType.TYPED_DECISION, self._reservation
            )
        except Exception:
            return Reason.BUDGET_DENIED, None
        try:
            answer = await asyncio.wait_for(
                asyncio.to_thread(adapter.answer, axes, bounded, timeout_seconds=self._deadline),
                timeout=self._deadline,
            )
        except TypedDecisionAdapterError as error:
            return error.reason, None
        except TimeoutError:
            return Reason.TIMEOUT, None
        except Exception:
            return Reason.HTTP_ERROR, None
        if not _answer_matches(axes, answer):
            return Reason.SCHEMA_INVALID, None
        return None, answer

    def _record(self, axis_count: int, outcome: TypedDecisionOutcome, input_tokens: int) -> None:
        recorder = self._recorder
        if recorder is None:
            return
        reason = outcome.unavailable_reason
        record = TypedDecisionRecord(
            self._source.value,
            axis_count,
            "answered" if reason is None else reason.value,
            outcome.duration_ms,
            outcome.model_version,
            input_tokens,
        )
        with contextlib.suppress(Exception):
            recorder.record(record)


@dataclass(frozen=True, slots=True)
class TierDecision:
    """Committee verdict, or ``heavy`` (the safe side) with an ``unavailable:`` reason."""

    route: str
    reason: str
    decision: CascadeRouteDecision | None


async def decide_tier(
    committee: CascadeCommittee, classifier: PromptClassifier, prompt: str
) -> TierDecision:
    """Return the committee verdict; any unavailable axis or router error means heavy."""

    try:
        decision = await committee.classify(prompt, classifier)
    except TypedDecisionUnavailable as error:
        return TierDecision("heavy", f"unavailable:{error.reason.value}", None)
    except CascadeRouterError:
        return TierDecision("heavy", "unavailable:router_error", None)
    except Exception:
        return TierDecision("heavy", "unavailable:error", None)
    return TierDecision(decision.route, decision.reason, decision)


def _request_shape_valid(axes: tuple[ClassifierAxis, ...], text: str) -> bool:
    return (
        bool(text.strip())
        and 0 < len(axes) <= MAXIMUM_AXES
        and all(isinstance(axis, ClassifierAxis) for axis in axes)
        and len({axis.name for axis in axes}) == len(axes)
    )


def _answer_matches(axes: tuple[ClassifierAxis, ...], answer: object) -> bool:
    if not isinstance(answer, AdapterAnswer):
        return False
    version = answer.model_version
    tokens = answer.input_tokens
    return (
        isinstance(version, str)
        and bool(version.strip())
        and len(version) <= _MAXIMUM_MODEL_VERSION_LENGTH
        and not isinstance(tokens, bool)
        and isinstance(tokens, int)
        and tokens >= 0
        and len(answer.results) == len(axes)
        and all(
            isinstance(result, ClassifierResult)
            and result.axis_name == axis.name
            and result.label in axis.allowed_labels
            for axis, result in zip(axes, answer.results, strict=True)
        )
    )
