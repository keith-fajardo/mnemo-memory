"""Guarded boundary for hosted typed-decision classifiers (spec §5.2, ADR 0049).

Checks run in a fixed order and each fails closed to one ``unavailable`` reason: data route,
credential, request shape, secret scan, sensitivity, size cap, budget, deadline, label check.
The guard never raises from ``ask`` and never records decision text.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import math
import threading
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
        if not _deadline_valid(deadline_seconds):
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

        reason: TypedDecisionUnavailableReason | None
        answer: AdapterAnswer | None
        try:
            started: float | None = self._clock()
        except Exception:
            started = None
        try:
            axis_tuple: tuple[ClassifierAxis, ...] | None = tuple(axes)
        except Exception:
            axis_tuple = None
        try:
            reason, answer = await self._ask(axis_tuple, text, sensitivity)
        except Exception:
            reason, answer = Reason.HTTP_ERROR, None
        try:
            if started is None:
                raise ValueError("clock unavailable")
            duration_ms = max(0, round((self._clock() - started) * 1_000))
        except Exception:
            duration_ms = 0
            if reason is None:
                reason, answer = Reason.HTTP_ERROR, None
        outcome = TypedDecisionOutcome(
            () if answer is None else answer.results,
            reason,
            duration_ms,
            None if answer is None else answer.model_version,
        )
        self._record(
            0 if axis_tuple is None else len(axis_tuple),
            outcome,
            0 if answer is None else answer.input_tokens,
        )
        return outcome

    async def ask_each(
        self,
        requests: Sequence[tuple[Sequence[ClassifierAxis], str]],
        *,
        total_deadline_seconds: float,
        sensitivity: Sensitivity = Sensitivity.NORMAL,
    ) -> tuple[TypedDecisionOutcome, ...]:
        """Ask every request concurrently within one total budget; outcomes in request order.

        Requests still running at the budget are cancelled and reported as ``timeout``, and so
        are those still running when the caller cancels this call (the ``CancelledError`` then
        propagates). An invalid budget is a caller programming error and raises ``ValueError``;
        nothing else raises.
        """

        if not _deadline_valid(total_deadline_seconds):
            raise ValueError("typed decision total deadline must be within (0, 30] seconds")
        try:
            pending_requests = tuple(requests)
        except Exception:
            pending_requests = ()
        if not pending_requests:
            return ()
        tasks = [
            asyncio.ensure_future(self._ask_request(request, sensitivity))
            for request in pending_requests
        ]
        timed_out = TypedDecisionOutcome(
            (), Reason.TIMEOUT, round(total_deadline_seconds * 1_000), None
        )
        try:
            await asyncio.wait(tasks, timeout=total_deadline_seconds)
        finally:
            # Also runs when the caller cancels us. ``ask`` records only requests that finish,
            # so each cancelled one is recorded here, exactly once.
            unfinished = [task for task in tasks if not task.done()]
            for task in unfinished:
                task.cancel()
            if unfinished:
                await asyncio.wait(unfinished)
            for request, task in zip(pending_requests, tasks, strict=True):
                if task.cancelled():
                    self._record(_request_axis_count(request), timed_out, 0)
        return tuple(timed_out if task.cancelled() else task.result() for task in tasks)

    async def _ask_request(
        self, request: tuple[Sequence[ClassifierAxis], str], sensitivity: Sensitivity
    ) -> TypedDecisionOutcome:
        try:
            axes, text = request
        except Exception:
            axes, text = (), ""  # a malformed request runs the checks and is schema_invalid
        return await self.ask(axes, text, sensitivity=sensitivity)

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
        self,
        axes: tuple[ClassifierAxis, ...] | None,
        text: object,
        sensitivity: Sensitivity,
    ) -> tuple[TypedDecisionUnavailableReason | None, AdapterAnswer | None]:
        adapter = self._adapter
        if not typed_decision_source_permitted(self._data_route, self._source):
            return Reason.DATA_ROUTE_BLOCKED, None
        if adapter is None:
            return Reason.NO_CREDENTIAL, None
        if axes is None or not isinstance(text, str) or not _request_shape_valid(axes, text):
            return Reason.SCHEMA_INVALID, None
        axis_texts = tuple(value for axis in axes for value in _axis_strings(axis))
        bounded = bounded_decision_text(text)
        if contains_high_confidence_secret(text, bounded, *axis_texts):
            return Reason.SECRET_BLOCKED, None
        if sensitivity is not Sensitivity.NORMAL:
            return Reason.SENSITIVITY_BLOCKED, None
        if any(len(value) > MAXIMUM_AXIS_TEXT_CHARACTERS for value in axis_texts):
            return Reason.SCHEMA_INVALID, None
        try:
            self._budget.reserve(
                self._workspace_id, ModelTaskType.TYPED_DECISION, self._reservation
            )
        except Exception:
            return Reason.BUDGET_DENIED, None
        try:
            answer = await asyncio.wait_for(
                _run_in_daemon_thread(
                    functools.partial(adapter.answer, axes, bounded, timeout_seconds=self._deadline)
                ),
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


def _deadline_valid(seconds: object) -> bool:
    return (
        not isinstance(seconds, bool)
        and isinstance(seconds, (int, float))
        and math.isfinite(seconds)
        and 0.0 < seconds <= _MAXIMUM_DEADLINE_SECONDS
    )


def _request_axis_count(request: tuple[Sequence[ClassifierAxis], str]) -> int:
    try:
        return len(tuple(request[0]))
    except Exception:
        return 0


def _run_in_daemon_thread[T](fn: Callable[[], T]) -> asyncio.Future[T]:
    """Run ``fn`` on a daemon thread so an abandoned call never blocks loop shutdown or exit.

    ``wait_for`` may cancel the future first; a late result is then dropped, and a result that
    arrives after the loop closed is discarded.
    """

    loop = asyncio.get_running_loop()
    future: asyncio.Future[T] = loop.create_future()

    def deliver(value: T) -> None:
        if not future.done():
            future.set_result(value)

    def fail(error: Exception) -> None:
        if not future.done():
            future.set_exception(error)

    def work() -> None:
        try:
            value = fn()
        except Exception as error:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(fail, error)
            return
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(deliver, value)

    threading.Thread(target=work, daemon=True, name="mnemo-typed-decision").start()
    return future


def _axis_strings(axis: ClassifierAxis) -> tuple[str, ...]:
    """Every axis string a provider adapter may send."""

    return (
        axis.name,
        axis.instructions,
        *axis.allowed_labels,
        *(value for pair in axis.criteria for value in pair),
    )


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
