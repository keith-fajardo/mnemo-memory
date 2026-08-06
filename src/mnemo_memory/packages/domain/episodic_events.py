"""Immutable, evidence-bearing lifecycle facts for durable episodic memory.

The initial event slice records checkpoint lifecycle transitions only.  It deliberately carries no
conversation, command, source, environment, SQL, or arbitrary tool payload.  A later event source
can be added only with its own explicit evidence and privacy contract.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID, uuid5

from .identifiers import CheckpointId, CheckpointRevisionId, EventId
from .models import EvidenceReference, MemoryScope, ScopeLevel

_CHECKPOINT_EVENT_NAMESPACE = UUID("b65680e5-bac6-40f0-bfd9-0e201e4f4162")
_MAX_IDEMPOTENCY_KEY_LENGTH = 256


def _require_aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _parse_datetime(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{name} must be a valid ISO-8601 timestamp") from error
    _require_aware(parsed, name)
    return parsed


def _strict_fields(value: Mapping[str, object], expected: set[str], name: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{name} fields are invalid")


class CheckpointEventKind(StrEnum):
    """A transition emitted exactly once for one immutable checkpoint revision."""

    CREATED = "checkpoint_created"
    REVISED = "checkpoint_revised"
    COMPLETED = "checkpoint_completed"
    ABANDONED = "checkpoint_abandoned"
    EXPIRED = "checkpoint_expired"
    LESSON_RECORDED = "checkpoint_lesson_recorded"


@dataclass(frozen=True, slots=True)
class CheckpointLifecycleEvent:
    """A minimal, scoped audit fact anchored to an immutable checkpoint revision.

    The idempotency key is deterministic from the kind and revision identity.  It prevents an
    at-least-once caller from manufacturing duplicate historical events while preserving the
    revision as the single source for checkpoint content and evidence.
    """

    event_id: EventId
    scope: MemoryScope
    kind: CheckpointEventKind
    checkpoint_id: CheckpointId
    revision_id: CheckpointRevisionId
    revision_number: int
    occurred_at: datetime
    idempotency_key: str
    evidence_references: tuple[EvidenceReference, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, EventId):
            raise TypeError("event_id must be an EventId")
        if not isinstance(self.scope, MemoryScope) or self.scope.level is not ScopeLevel.TASK:
            raise ValueError("checkpoint lifecycle event requires a task scope")
        if not isinstance(self.kind, CheckpointEventKind):
            raise TypeError("checkpoint lifecycle event kind is invalid")
        if not isinstance(self.checkpoint_id, CheckpointId):
            raise TypeError("checkpoint_id must be a CheckpointId")
        if not isinstance(self.revision_id, CheckpointRevisionId):
            raise TypeError("revision_id must be a CheckpointRevisionId")
        if not isinstance(self.revision_number, int) or self.revision_number < 1:
            raise ValueError("revision_number must be positive")
        _require_aware(self.occurred_at, "occurred_at")
        if (
            not isinstance(self.idempotency_key, str)
            or not self.idempotency_key
            or len(self.idempotency_key) > _MAX_IDEMPOTENCY_KEY_LENGTH
        ):
            raise ValueError("event idempotency key is invalid")
        evidence = tuple(self.evidence_references)
        if not evidence or any(not isinstance(item, EvidenceReference) for item in evidence):
            raise ValueError("checkpoint lifecycle event requires evidence")
        if len({item.evidence_id for item in evidence}) != len(evidence):
            raise ValueError("checkpoint lifecycle event evidence must be unique")
        object.__setattr__(self, "evidence_references", evidence)

    @classmethod
    def for_revision(
        cls,
        *,
        scope: MemoryScope,
        kind: CheckpointEventKind,
        checkpoint_id: CheckpointId,
        revision_id: CheckpointRevisionId,
        revision_number: int,
        occurred_at: datetime,
        evidence_references: tuple[EvidenceReference, ...],
    ) -> Self:
        """Create the one deterministic event identity valid for a revision transition."""
        key = f"{kind.value}:{checkpoint_id}:{revision_id}"
        return cls(
            EventId(uuid5(_CHECKPOINT_EVENT_NAMESPACE, key)),
            scope,
            kind,
            checkpoint_id,
            revision_id,
            revision_number,
            occurred_at,
            key,
            evidence_references,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": str(self.event_id),
            "scope": self.scope.to_dict(),
            "kind": self.kind.value,
            "checkpoint_id": str(self.checkpoint_id),
            "revision_id": str(self.revision_id),
            "revision_number": self.revision_number,
            "occurred_at": self.occurred_at.isoformat(),
            "idempotency_key": self.idempotency_key,
            "evidence_references": [item.to_dict() for item in self.evidence_references],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        fields = {
            "event_id",
            "scope",
            "kind",
            "checkpoint_id",
            "revision_id",
            "revision_number",
            "occurred_at",
            "idempotency_key",
            "evidence_references",
        }
        _strict_fields(value, fields, "checkpoint lifecycle event")
        scope = value["scope"]
        evidence = value["evidence_references"]
        if not isinstance(scope, Mapping):
            raise TypeError("checkpoint lifecycle event scope must be an object")
        if not isinstance(evidence, list):
            raise TypeError("checkpoint lifecycle event evidence must be an array")
        event_id = value["event_id"]
        checkpoint_id = value["checkpoint_id"]
        revision_id = value["revision_id"]
        kind = value["kind"]
        revision_number = value["revision_number"]
        idempotency_key = value["idempotency_key"]
        if (
            not isinstance(event_id, str)
            or not isinstance(checkpoint_id, str)
            or not isinstance(revision_id, str)
            or not isinstance(kind, str)
        ):
            raise TypeError("checkpoint lifecycle event identifiers must be strings")
        if not isinstance(revision_number, int) or not isinstance(idempotency_key, str):
            raise TypeError("checkpoint lifecycle event metadata is invalid")
        return cls(
            EventId.from_string(event_id),
            MemoryScope.from_dict(scope),
            CheckpointEventKind(kind),
            CheckpointId.from_string(checkpoint_id),
            CheckpointRevisionId.from_string(revision_id),
            revision_number,
            _parse_datetime(value["occurred_at"], "occurred_at"),
            idempotency_key,
            tuple(EvidenceReference.from_dict(item) for item in evidence),
        )
