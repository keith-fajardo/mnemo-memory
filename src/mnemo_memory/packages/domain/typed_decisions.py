"""Closed vocabularies for hosted typed-decision classification (ADR 0049)."""

from __future__ import annotations

from enum import StrEnum


class TypedDecisionDataRoute(StrEnum):
    """Where decision text may be sent. Phase 1 permits synthetic fixtures only."""

    SYNTHETIC_ONLY = "synthetic_only"


class TypedDecisionSource(StrEnum):
    """Who supplies decision text; bound when a classifier is composed, never per call."""

    RUNTIME = "runtime"
    SYNTHETIC_FIXTURE = "synthetic_fixture"


class TypedDecisionMode(StrEnum):
    OFF = "off"
    SHADOW = "shadow"
    LIVE = "live"


class TypedDecisionKind(StrEnum):
    FRONT_DOOR = "front_door"
    RELEVANCE = "relevance"
    EXTRACTION_GATE = "extraction_gate"
    COMPACTION = "compaction"
    DEDUPE = "dedupe"
    SEMANTIC_KIND = "semantic_kind"
    VERIFY = "verify"
    TIER_HINT = "tier_hint"
    SKILL = "skill"


class TypedDecisionUnavailableReason(StrEnum):
    """Why a decision fell back to today's rules; content-free by construction."""

    DISABLED = "disabled"
    DATA_ROUTE_BLOCKED = "data_route_blocked"
    NO_CREDENTIAL = "no_credential"
    SECRET_BLOCKED = "secret_blocked"
    SENSITIVITY_BLOCKED = "sensitivity_blocked"
    BUDGET_DENIED = "budget_denied"
    TIMEOUT = "timeout"
    HTTP_ERROR = "http_error"
    SCHEMA_INVALID = "schema_invalid"


def typed_decision_source_permitted(
    route: TypedDecisionDataRoute, source: TypedDecisionSource
) -> bool:
    """Return whether ``route`` allows text from ``source`` to leave the machine."""

    if route is TypedDecisionDataRoute.SYNTHETIC_ONLY:
        return source is TypedDecisionSource.SYNTHETIC_FIXTURE
    return False  # type: ignore[unreachable]
