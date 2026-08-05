"""Explicit, minimized task activity facts that never contain raw interaction bodies."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID, uuid5

from .identifiers import EventId
from .models import (
    EvidenceReference,
    MemoryScope,
    RetentionSchedule,
    ScopeLevel,
    Sensitivity,
    _parse_datetime,
    _require_aware,
)

_EVENT_NAMESPACE = UUID("7ee7348c-c90e-4659-8663-a0bcc2aec7b3")
_MAX_SUMMARY_LENGTH = 1_200
_MAX_SOURCE_KEY_LENGTH = 256
_MAX_EVIDENCE = 64


class TaskActivityEventKind(StrEnum):
    CONVERSATION_HANDOFF = "conversation_handoff"
    TASK_ACTIVITY = "task_activity"
    TOOL_INVOCATION = "tool_invocation"
    TASK_OUTCOME = "task_outcome"


class TaskActivityActor(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    AGENT = "agent"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class TaskActivityEvent:
    """One explicitly submitted summary, not a transcript, prompt, command, or tool body."""

    event_id: EventId
    scope: MemoryScope
    kind: TaskActivityEventKind
    actor: TaskActivityActor
    summary: str
    source_event_key: str
    sensitivity: Sensitivity
    retention: RetentionSchedule
    occurred_at: datetime
    evidence_references: tuple[EvidenceReference, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, EventId):
            raise TypeError("task activity event identity is invalid")
        if not isinstance(self.scope, MemoryScope) or self.scope.level is not ScopeLevel.TASK:
            raise ValueError("task activity events require task scope")
        if not isinstance(self.kind, TaskActivityEventKind) or not isinstance(
            self.actor, TaskActivityActor
        ):
            raise TypeError("task activity event category is invalid")
        if (
            not isinstance(self.summary, str)
            or not self.summary.strip()
            or len(self.summary) > _MAX_SUMMARY_LENGTH
        ):
            raise ValueError("task activity event summary is invalid")
        if (
            not isinstance(self.source_event_key, str)
            or not self.source_event_key.strip()
            or len(self.source_event_key) > _MAX_SOURCE_KEY_LENGTH
        ):
            raise ValueError("task activity event source key is invalid")
        if (
            not isinstance(self.sensitivity, Sensitivity)
            or self.sensitivity is Sensitivity.PROHIBITED
        ):
            raise ValueError("prohibited content cannot become a task activity event")
        if not isinstance(self.retention, RetentionSchedule):
            raise TypeError("task activity event retention is invalid")
        _require_aware(self.occurred_at, "occurred_at")
        evidence = tuple(self.evidence_references)
        if (
            not 1 <= len(evidence) <= _MAX_EVIDENCE
            or any(not isinstance(item, EvidenceReference) for item in evidence)
            or len({item.evidence_id for item in evidence}) != len(evidence)
        ):
            raise ValueError("task activity event requires bounded unique evidence")
        object.__setattr__(self, "evidence_references", evidence)

    @classmethod
    def create(
        cls,
        *,
        scope: MemoryScope,
        kind: TaskActivityEventKind,
        actor: TaskActivityActor,
        summary: str,
        source_event_key: str,
        sensitivity: Sensitivity,
        retention: RetentionSchedule,
        occurred_at: datetime,
        evidence_references: tuple[EvidenceReference, ...],
    ) -> Self:
        event_id = EventId(
            uuid5(_EVENT_NAMESPACE, f"{scope.to_dict()}:{kind.value}:{source_event_key}")
        )
        return cls(
            event_id,
            scope,
            kind,
            actor,
            summary,
            source_event_key,
            sensitivity,
            retention,
            occurred_at,
            evidence_references,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": str(self.event_id),
            "scope": self.scope.to_dict(),
            "kind": self.kind.value,
            "actor": self.actor.value,
            "summary": self.summary,
            "source_event_key": self.source_event_key,
            "sensitivity": self.sensitivity.value,
            "retention": self.retention.to_dict(),
            "occurred_at": self.occurred_at.isoformat(),
            "evidence_references": [item.to_dict() for item in self.evidence_references],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        expected = {
            "event_id",
            "scope",
            "kind",
            "actor",
            "summary",
            "source_event_key",
            "sensitivity",
            "retention",
            "occurred_at",
            "evidence_references",
        }
        if set(value) != expected:
            raise ValueError("task activity event fields are invalid")
        scope = value["scope"]
        retention = value["retention"]
        evidence = value["evidence_references"]
        if (
            not isinstance(scope, Mapping)
            or not isinstance(retention, Mapping)
            or not isinstance(evidence, list)
        ):
            raise TypeError("task activity event serialization is invalid")
        event_id = value["event_id"]
        kind = value["kind"]
        actor = value["actor"]
        summary = value["summary"]
        source_event_key = value["source_event_key"]
        sensitivity = value["sensitivity"]
        if not all(
            isinstance(item, str)
            for item in (event_id, kind, actor, summary, source_event_key, sensitivity)
        ):
            raise TypeError("task activity event fields must be strings")
        assert isinstance(event_id, str)
        assert isinstance(kind, str)
        assert isinstance(actor, str)
        assert isinstance(summary, str)
        assert isinstance(source_event_key, str)
        assert isinstance(sensitivity, str)
        return cls(
            EventId.from_string(event_id),
            MemoryScope.from_dict(scope),
            TaskActivityEventKind(kind),
            TaskActivityActor(actor),
            summary,
            source_event_key,
            Sensitivity(sensitivity),
            RetentionSchedule.from_dict(retention),
            _parse_datetime(value["occurred_at"], "occurred_at"),
            tuple(EvidenceReference.from_dict(item) for item in evidence),
        )
