"""Deterministic local rule axes for the cascade committee (no model, no network)."""

from __future__ import annotations

import re

from .cascade_router import (
    CascadeRouterError,
    ClassifierAxis,
    ClassifierResult,
    logprob_from_probability,
)

RISK_AXIS = ClassifierAxis(
    "risk",
    "Local rule: the task touches migrations, authorization, deletion, security or external writes",
    ("low", "high"),
    0.0,
    veto_score=1.0,
    label_scores=(0.0, 1.0),
)

# Tag names follow the long-horizon harness risk tags; patterns are Mnemo-owned term lists.
_RISK_TERMS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "migration",
        re.compile(r"\b(?:migrat\w*|schema changes?|alter table|backfill\w*)\b", re.IGNORECASE),
    ),
    (
        "authorization",
        re.compile(
            r"\b(?:authoriz\w*|authenticat\w*|auth|oauth|permission\w*"
            r"|login|rbac|access control)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "deletion",
        re.compile(
            r"\b(?:delet\w*|purg\w*|truncat\w*|wipe\w*|drop (?:table|database|column))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "security",
        re.compile(
            r"\b(?:secret\w*|credential\w*|password\w*|api keys?|encrypt\w*|vulnerab\w*)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "external_write",
        re.compile(r"\b(?:deploy\w*|publish\w*|production|force[- ]push\w*)\b", re.IGNORECASE),
    ),
)


def matched_risk_tags(prompt: str) -> tuple[str, ...]:
    """Return the risk tags whose terms appear in ``prompt`` (content-free output)."""

    return tuple(tag for tag, pattern in _RISK_TERMS if pattern.search(prompt))


class RiskTermClassifier:
    """Answer only ``RISK_AXIS``; a match is certain ``high`` and vetoes a light verdict."""

    async def classify(self, axis: ClassifierAxis, prompt: str) -> ClassifierResult:
        if axis != RISK_AXIS:
            raise CascadeRouterError("MNEMO_CASCADE_RULE_AXIS_UNSUPPORTED")
        high = bool(matched_risk_tags(prompt))
        return ClassifierResult(
            axis.name,
            "high" if high else "low",
            logprob_from_probability(1.0),
            1.0 if high else 0.0,
        )
