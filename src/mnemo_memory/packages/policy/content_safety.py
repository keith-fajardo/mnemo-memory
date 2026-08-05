"""Composed content classification with a mandatory deterministic first gate."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from mnemo_memory.packages.domain import Sensitivity

_MAX_CLASSIFIERS = 8
_MAX_VALUES = 2_048
_MAX_TOTAL_BYTES = 1_100_000
_STABLE_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,95}$")
_HIGH_CONFIDENCE_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN(?: [A-Z]+)? PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(
        r"(?im)^\s*(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|secret)"
        r"\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{16,}"
    ),
)
_SENSITIVITY_ORDER = {
    Sensitivity.NORMAL: 0,
    Sensitivity.PERSONAL: 1,
    Sensitivity.CONFIDENTIAL: 2,
    Sensitivity.RESTRICTED: 3,
}


def contains_high_confidence_secret(*values: str) -> bool:
    """Return a content-free deterministic decision shared by all safety boundaries."""
    if any(not isinstance(value, str) for value in values):
        raise TypeError("secret policy values must be strings")
    return any(
        pattern.search(value) is not None
        for value in values
        for pattern in _HIGH_CONFIDENCE_SECRET_PATTERNS
    )


@dataclass(frozen=True, slots=True)
class ContentSafetyDecision:
    """A bounded decision that cannot contain source or matched content."""

    accepted: bool
    sensitivity: Sensitivity
    code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.sensitivity, Sensitivity):
            raise TypeError("content safety sensitivity is invalid")
        if self.accepted:
            if self.sensitivity is Sensitivity.PROHIBITED or self.code is not None:
                raise ValueError("accepted content safety decision is invalid")
        elif (
            self.sensitivity is not Sensitivity.PROHIBITED
            or not isinstance(self.code, str)
            or _STABLE_CODE.fullmatch(self.code) is None
        ):
            raise ValueError("rejected content safety decision is invalid")


class ContentSafetyClassifier(Protocol):
    """Explicitly supplied classifier returning no transformed or matched content."""

    def classify(self, values: tuple[str, ...]) -> ContentSafetyDecision: ...


class DeterministicSecretClassifier:
    """Mandatory high-confidence credential-pattern classifier."""

    def classify(self, values: tuple[str, ...]) -> ContentSafetyDecision:
        if contains_high_confidence_secret(*values):
            return ContentSafetyDecision(
                False,
                Sensitivity.PROHIBITED,
                "MNEMO_CONTENT_SECRET_REJECTED",
            )
        return ContentSafetyDecision(True, Sensitivity.NORMAL)


class ContentSafetyPolicy:
    """Run the mandatory gate first, then a bounded ordered classifier chain."""

    def __init__(self, additional_classifiers: tuple[ContentSafetyClassifier, ...] = ()) -> None:
        classifiers = tuple(additional_classifiers)
        if len(classifiers) > _MAX_CLASSIFIERS or any(
            not callable(getattr(classifier, "classify", None)) for classifier in classifiers
        ):
            raise ValueError("content safety classifier configuration is invalid")
        self._additional_classifiers = classifiers
        self._mandatory_classifier = DeterministicSecretClassifier()

    def assess(self, *values: str) -> ContentSafetyDecision:
        normalized = tuple(values)
        if any(not isinstance(value, str) for value in normalized):
            raise TypeError("content safety values must be strings")
        if (
            len(normalized) > _MAX_VALUES
            or sum(len(value.encode("utf-8")) for value in normalized) > _MAX_TOTAL_BYTES
        ):
            return ContentSafetyDecision(
                False,
                Sensitivity.PROHIBITED,
                "MNEMO_CONTENT_CLASSIFICATION_BOUNDS_EXCEEDED",
            )
        mandatory = self._mandatory_classifier.classify(normalized)
        if not mandatory.accepted:
            return mandatory
        sensitivity = mandatory.sensitivity
        for classifier in self._additional_classifiers:
            try:
                raw_decision: object = classifier.classify(normalized)
            except Exception:
                return ContentSafetyDecision(
                    False,
                    Sensitivity.PROHIBITED,
                    "MNEMO_CONTENT_CLASSIFIER_FAILED",
                )
            if not isinstance(raw_decision, ContentSafetyDecision):
                return ContentSafetyDecision(
                    False,
                    Sensitivity.PROHIBITED,
                    "MNEMO_CONTENT_CLASSIFIER_INVALID",
                )
            decision = raw_decision
            if not decision.accepted:
                return decision
            if _SENSITIVITY_ORDER[decision.sensitivity] > _SENSITIVITY_ORDER[sensitivity]:
                sensitivity = decision.sensitivity
        return ContentSafetyDecision(True, sensitivity)
