"""Explicit, bounded episodic facts that are safe to retain without transcripts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID, uuid5

from .identifiers import EventId
from .models import EvidenceReference, MemoryScope, ScopeLevel, _parse_datetime, _require_aware

_EVENT_NAMESPACE = UUID("e112b48c-0ac3-4fb1-87a9-9b0b27ed6096")
_MAX_SUMMARY_LENGTH = 1_200
_MAX_SOURCE_KEY_LENGTH = 256


class ApprovedEventKind(StrEnum):
    """Facts a user or connected agent may explicitly record with evidence."""

    DECISION = "decision"
    FAILURE = "failure"
    TOOL_OUTCOME = "tool_outcome"


@dataclass(frozen=True, slots=True)
class ApprovedEpisodicEvent:
    """One immutable scoped fact, never a transcript, prompt, or opaque model trace."""

    event_id: EventId
    scope: MemoryScope
    kind: ApprovedEventKind
    summary: str
    source_event_key: str
    occurred_at: datetime
    evidence_references: tuple[EvidenceReference, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, EventId):
            raise TypeError("event_id must be an EventId")
        if not isinstance(self.scope, MemoryScope) or self.scope.level is not ScopeLevel.TASK:
            raise ValueError("approved episodic events require task scope")
        if not isinstance(self.kind, ApprovedEventKind):
            raise TypeError("approved episodic event kind is invalid")
        if (
            not isinstance(self.summary, str)
            or not self.summary.strip()
            or len(self.summary) > _MAX_SUMMARY_LENGTH
        ):
            raise ValueError("approved episodic event summary is invalid")
        if (
            not isinstance(self.source_event_key, str)
            or not self.source_event_key.strip()
            or len(self.source_event_key) > _MAX_SOURCE_KEY_LENGTH
        ):
            raise ValueError("approved episodic event source key is invalid")
        _require_aware(self.occurred_at, "occurred_at")
        evidence = tuple(self.evidence_references)
        if not evidence or len({item.evidence_id for item in evidence}) != len(evidence):
            raise ValueError("approved episodic event requires unique evidence")
        object.__setattr__(self, "evidence_references", evidence)

    @classmethod
    def create(
        cls,
        *,
        scope: MemoryScope,
        kind: ApprovedEventKind,
        summary: str,
        source_event_key: str,
        occurred_at: datetime,
        evidence_references: tuple[EvidenceReference, ...],
    ) -> Self:
        event_id = EventId(uuid5(_EVENT_NAMESPACE, f"{scope.to_dict()}:{kind}:{source_event_key}"))
        return cls(
            event_id, scope, kind, summary, source_event_key, occurred_at, evidence_references
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": str(self.event_id),
            "scope": self.scope.to_dict(),
            "kind": self.kind.value,
            "summary": self.summary,
            "source_event_key": self.source_event_key,
            "occurred_at": self.occurred_at.isoformat(),
            "evidence_references": [item.to_dict() for item in self.evidence_references],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        expected = {
            "event_id",
            "scope",
            "kind",
            "summary",
            "source_event_key",
            "occurred_at",
            "evidence_references",
        }
        if set(value) != expected:
            raise ValueError("approved episodic event fields are invalid")
        scope = value["scope"]
        evidence = value["evidence_references"]
        if not isinstance(scope, Mapping) or not isinstance(evidence, list):
            raise TypeError("approved episodic event serialization is invalid")
        event_id = value["event_id"]
        kind = value["kind"]
        summary = value["summary"]
        source_event_key = value["source_event_key"]
        if not all(isinstance(item, str) for item in (event_id, kind, summary, source_event_key)):
            raise TypeError("approved episodic event fields must be strings")
        assert isinstance(event_id, str)
        assert isinstance(kind, str)
        assert isinstance(summary, str)
        assert isinstance(source_event_key, str)
        return cls(
            EventId.from_string(event_id),
            MemoryScope.from_dict(scope),
            ApprovedEventKind(kind),
            summary,
            source_event_key,
            _parse_datetime(value["occurred_at"], "occurred_at"),
            tuple(EvidenceReference.from_dict(item) for item in evidence),
        )
