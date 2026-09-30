"""Provider-neutral concurrent pre-routing for Mnemo's own model tasks.

This module classifies a task before any answer generation. It does not call a model provider,
retain prompts, or intercept an agent's configured model endpoint. Composition supplies each
classifier adapter (a hosted typed classifier, deterministic rules, or a local model) and remains
responsible for score calibration. A ``heavy`` route is only a recommendation.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

YES_NO_LABELS: tuple[str, str] = ("no", "yes")
_MINIMUM_PROBABILITY = 1e-6


class CascadeRouterError(ValueError):
    """Stable, payload-free invalid-router-input error."""


class AxisKind(StrEnum):
    """How an axis is asked: one label from a closed list, or a yes/no probability."""

    CHOICE = "choice"
    YES_NO = "yes_no"


@dataclass(frozen=True, slots=True)
class ClassifierAxis:
    """One independent, fixed-label classification question."""

    name: str
    instructions: str
    allowed_labels: tuple[str, ...]
    score_weight: float
    veto_score: float | None = None
    kind: AxisKind = field(default=AxisKind.CHOICE, kw_only=True)
    criteria: tuple[tuple[str, str], ...] = field(default=(), kw_only=True)
    label_scores: tuple[float, ...] = field(default=(), kw_only=True)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise CascadeRouterError("MNEMO_CASCADE_AXIS_INVALID")
        if not isinstance(self.instructions, str) or not self.instructions.strip():
            raise CascadeRouterError("MNEMO_CASCADE_AXIS_INVALID")
        if (
            len(self.allowed_labels) < 2
            or any(not isinstance(label, str) or not label.strip() for label in self.allowed_labels)
            or len(set(self.allowed_labels)) != len(self.allowed_labels)
        ):
            raise CascadeRouterError("MNEMO_CASCADE_AXIS_INVALID")
        if (
            isinstance(self.score_weight, bool)
            or not isinstance(self.score_weight, (int, float))
            or not math.isfinite(self.score_weight)
        ):
            raise CascadeRouterError("MNEMO_CASCADE_AXIS_INVALID")
        if self.veto_score is not None and not _probability(self.veto_score):
            raise CascadeRouterError("MNEMO_CASCADE_AXIS_INVALID")
        if not isinstance(self.kind, AxisKind):
            raise CascadeRouterError("MNEMO_CASCADE_AXIS_INVALID")
        if self.kind is AxisKind.YES_NO and self.allowed_labels != YES_NO_LABELS:
            raise CascadeRouterError("MNEMO_CASCADE_AXIS_INVALID")
        if self.criteria and (
            tuple(label for label, _ in self.criteria) != self.allowed_labels
            or any(not isinstance(text, str) or not text.strip() for _, text in self.criteria)
        ):
            raise CascadeRouterError("MNEMO_CASCADE_AXIS_INVALID")
        if self.label_scores and (
            len(self.label_scores) != len(self.allowed_labels)
            or not all(_probability(score) for score in self.label_scores)
        ):
            raise CascadeRouterError("MNEMO_CASCADE_AXIS_INVALID")

    def escalation_score_for(self, probabilities: Mapping[str, float]) -> float:
        """Return p(yes) for a yes/no axis, else the expected label score (0 without scores)."""

        if self.kind is AxisKind.YES_NO:
            return _clamp(probabilities.get("yes", 0.0))
        if not self.label_scores:
            return 0.0
        return _clamp(
            sum(
                probabilities.get(label, 0.0) * score
                for label, score in zip(self.allowed_labels, self.label_scores, strict=True)
            )
        )


@dataclass(frozen=True, slots=True)
class ClassifierResult:
    """A constrained label, its log-probability, escalation score and optional confidence."""

    axis_name: str
    label: str
    label_logprob: float
    escalation_score: float
    confidence: float | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.axis_name, str)
            or not self.axis_name.strip()
            or not isinstance(self.label, str)
            or not self.label.strip()
            or isinstance(self.label_logprob, bool)
            or not isinstance(self.label_logprob, (int, float))
            or not math.isfinite(self.label_logprob)
            or not _probability(self.escalation_score)
            or (self.confidence is not None and not _probability(self.confidence))
        ):
            raise CascadeRouterError("MNEMO_CASCADE_CLASSIFIER_RESULT_INVALID")


def logprob_from_probability(probability: float) -> float:
    """Convert a provider probability to a finite log-probability (p = 0 maps to log(1e-6))."""

    if not _probability(probability):
        raise CascadeRouterError("MNEMO_CASCADE_PROBABILITY_INVALID")
    return math.log(max(float(probability), _MINIMUM_PROBABILITY))


class PromptClassifier(Protocol):
    """Adapter boundary for one constrained, fixed-label classifier request."""

    async def classify(self, axis: ClassifierAxis, prompt: str) -> ClassifierResult: ...


@runtime_checkable
class BatchPromptClassifier(Protocol):
    """Optional adapter capability: answer several axes about one text in one request."""

    async def classify_batch(
        self, axes: tuple[ClassifierAxis, ...], prompt: str
    ) -> tuple[ClassifierResult, ...]: ...


@dataclass(frozen=True, slots=True)
class CascadeRouteDecision:
    """Deterministic pre-route outcome; ``heavy`` is only a recommendation."""

    route: str
    route_score: float
    reason: str
    results: tuple[ClassifierResult, ...]


class CascadeCommittee:
    """Ask heterogeneous classifiers and consolidate their scores into light or heavy."""

    def __init__(self, axes: Sequence[ClassifierAxis], *, escalation_threshold: float) -> None:
        self._axes = tuple(axes)
        if len(self._axes) < 2 or len({axis.name for axis in self._axes}) != len(self._axes):
            raise CascadeRouterError("MNEMO_CASCADE_AXES_INVALID")
        if not _probability(escalation_threshold):
            raise CascadeRouterError("MNEMO_CASCADE_THRESHOLD_INVALID")
        self._escalation_threshold = float(escalation_threshold)

    async def classify(self, prompt: str, classifier: PromptClassifier) -> CascadeRouteDecision:
        """Ask every axis in one batch when supported, else concurrently through ``gather``."""

        if not isinstance(prompt, str) or not prompt.strip():
            raise CascadeRouterError("MNEMO_CASCADE_PROMPT_INVALID")
        results = await classify_axes(classifier, self._axes, prompt)
        by_axis = {result.axis_name: result for result in results}
        if len(by_axis) != len(results) or set(by_axis) != {axis.name for axis in self._axes}:
            raise CascadeRouterError("MNEMO_CASCADE_CLASSIFIER_AXIS_MISMATCH")
        for axis in self._axes:
            if by_axis[axis.name].label not in axis.allowed_labels:
                raise CascadeRouterError("MNEMO_CASCADE_CLASSIFIER_LABEL_INVALID")
        vetoed = next(
            (
                axis
                for axis in self._axes
                if axis.veto_score is not None
                and by_axis[axis.name].escalation_score >= axis.veto_score
            ),
            None,
        )
        route_score = sum(
            axis.score_weight * by_axis[axis.name].escalation_score for axis in self._axes
        )
        if vetoed is not None:
            return CascadeRouteDecision("heavy", route_score, f"veto:{vetoed.name}", results)
        route = "heavy" if route_score >= self._escalation_threshold else "light"
        reason = "threshold" if route == "heavy" else "light"
        return CascadeRouteDecision(route, route_score, reason, results)


async def classify_axes(
    classifier: PromptClassifier, axes: tuple[ClassifierAxis, ...], prompt: str
) -> tuple[ClassifierResult, ...]:
    """Use one ``classify_batch`` call when supported; never serialize independent axes."""

    if isinstance(classifier, BatchPromptClassifier):
        return tuple(await classifier.classify_batch(axes, prompt))
    return tuple(await asyncio.gather(*(classifier.classify(axis, prompt) for axis in axes)))


class AxisRoutedClassifier:
    """Send each axis to its configured classifier; one batch per classifier, all concurrent."""

    def __init__(self, routes: Mapping[str, PromptClassifier]) -> None:
        if not routes or any(not isinstance(name, str) or not name.strip() for name in routes):
            raise CascadeRouterError("MNEMO_CASCADE_ROUTES_INVALID")
        self._routes = dict(routes)

    async def classify(self, axis: ClassifierAxis, prompt: str) -> ClassifierResult:
        return (await self.classify_batch((axis,), prompt))[0]

    async def classify_batch(
        self, axes: tuple[ClassifierAxis, ...], prompt: str
    ) -> tuple[ClassifierResult, ...]:
        groups: dict[int, tuple[PromptClassifier, list[ClassifierAxis]]] = {}
        for axis in axes:
            target = self._routes.get(axis.name)
            if target is None:
                raise CascadeRouterError("MNEMO_CASCADE_AXIS_UNROUTED")
            groups.setdefault(id(target), (target, []))[1].append(axis)
        answered = await asyncio.gather(
            *(classify_axes(target, tuple(group), prompt) for target, group in groups.values())
        )
        by_axis = {result.axis_name: result for results in answered for result in results}
        if set(by_axis) != {axis.name for axis in axes}:
            raise CascadeRouterError("MNEMO_CASCADE_CLASSIFIER_AXIS_MISMATCH")
        return tuple(by_axis[axis.name] for axis in axes)


class PrecomputedClassifier:
    """Serve answers already obtained in one batched request, so no second request is made."""

    def __init__(self, results: Sequence[ClassifierResult]) -> None:
        self._by_axis = {result.axis_name: result for result in results}

    async def classify(self, axis: ClassifierAxis, prompt: str) -> ClassifierResult:
        result = self._by_axis.get(axis.name)
        if result is None:
            raise CascadeRouterError("MNEMO_CASCADE_CLASSIFIER_AXIS_MISMATCH")
        return result


def _probability(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and 0.0 <= float(value) <= 1.0
    )


def _clamp(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise CascadeRouterError("MNEMO_CASCADE_PROBABILITY_INVALID")
    return min(1.0, max(0.0, float(value)))
