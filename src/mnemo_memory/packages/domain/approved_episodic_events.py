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
_GOVERNANCE_NAMESPACE = UUID("95a55f2a-9b29-47e0-a366-99550329a07a")
_MAX_SUMMARY_LENGTH = 1_200
_MAX_SOURCE_KEY_LENGTH = 256
_MAX_GOVERNANCE_REASON_LENGTH = 1_200


class ApprovedEventKind(StrEnum):
    """Facts a user or connected agent may explicitly record with evidence."""

    DECISION = "decision"
    FAILURE = "failure"
    TOOL_OUTCOME = "tool_outcome"


class ApprovedEventGovernanceKind(StrEnum):
    """Explicit immutable actions that change whether an approved fact is active."""

    CORRECTED = "corrected"
    RETRACTED = "retracted"


class ApprovedEventLifecycleStatus(StrEnum):
    ACTIVE = "active"
    CORRECTED = "corrected"
    RETRACTED = "retracted"


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


@dataclass(frozen=True, slots=True)
class ApprovedEpisodicEventGovernance:
    """One evidence-backed correction or retraction applied to one active fact."""

    action_id: EventId
    scope: MemoryScope
    kind: ApprovedEventGovernanceKind
    target_event_id: EventId
    replacement_event_id: EventId | None
    reason: str
    source_action_key: str
    occurred_at: datetime
    evidence_references: tuple[EvidenceReference, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.action_id, EventId) or not isinstance(self.target_event_id, EventId):
            raise TypeError("approved event governance IDs are invalid")
        if not isinstance(self.scope, MemoryScope) or self.scope.level is not ScopeLevel.TASK:
            raise ValueError("approved event governance requires task scope")
        if not isinstance(self.kind, ApprovedEventGovernanceKind):
            raise TypeError("approved event governance kind is invalid")
        if self.kind is ApprovedEventGovernanceKind.CORRECTED:
            if (
                not isinstance(self.replacement_event_id, EventId)
                or self.replacement_event_id == self.target_event_id
            ):
                raise ValueError("correction requires a distinct replacement event")
        elif self.replacement_event_id is not None:
            raise ValueError("retraction cannot identify a replacement event")
        if (
            not isinstance(self.reason, str)
            or not self.reason.strip()
            or len(self.reason) > _MAX_GOVERNANCE_REASON_LENGTH
        ):
            raise ValueError("approved event governance reason is invalid")
        if (
            not isinstance(self.source_action_key, str)
            or not self.source_action_key.strip()
            or len(self.source_action_key) > _MAX_SOURCE_KEY_LENGTH
        ):
            raise ValueError("approved event governance action key is invalid")
        _require_aware(self.occurred_at, "occurred_at")
        evidence = tuple(self.evidence_references)
        if not evidence or len({item.evidence_id for item in evidence}) != len(evidence):
            raise ValueError("approved event governance requires unique evidence")
        object.__setattr__(self, "evidence_references", evidence)

    @classmethod
    def create(
        cls,
        *,
        scope: MemoryScope,
        kind: ApprovedEventGovernanceKind,
        target_event_id: EventId,
        replacement_event_id: EventId | None,
        reason: str,
        source_action_key: str,
        occurred_at: datetime,
        evidence_references: tuple[EvidenceReference, ...],
    ) -> Self:
        action_id = EventId(
            uuid5(
                _GOVERNANCE_NAMESPACE,
                f"{scope.to_dict()}:{kind}:{target_event_id}:{source_action_key}",
            )
        )
        return cls(
            action_id,
            scope,
            kind,
            target_event_id,
            replacement_event_id,
            reason,
            source_action_key,
            occurred_at,
            evidence_references,
        )

    def same_intent(self, other: object) -> bool:
        """Compare caller intent while preserving first-write timestamps and evidence."""
        return isinstance(other, ApprovedEpisodicEventGovernance) and (
            self.action_id,
            self.scope,
            self.kind,
            self.target_event_id,
            self.replacement_event_id,
            self.reason,
            self.source_action_key,
        ) == (
            other.action_id,
            other.scope,
            other.kind,
            other.target_event_id,
            other.replacement_event_id,
            other.reason,
            other.source_action_key,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "action_id": str(self.action_id),
            "scope": self.scope.to_dict(),
            "kind": self.kind.value,
            "target_event_id": str(self.target_event_id),
            "replacement_event_id": (
                None if self.replacement_event_id is None else str(self.replacement_event_id)
            ),
            "reason": self.reason,
            "source_action_key": self.source_action_key,
            "occurred_at": self.occurred_at.isoformat(),
            "evidence_references": [item.to_dict() for item in self.evidence_references],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        expected = {
            "action_id",
            "scope",
            "kind",
            "target_event_id",
            "replacement_event_id",
            "reason",
            "source_action_key",
            "occurred_at",
            "evidence_references",
        }
        if set(value) != expected:
            raise ValueError("approved event governance fields are invalid")
        scope = value["scope"]
        evidence = value["evidence_references"]
        replacement = value["replacement_event_id"]
        strings = (
            value["action_id"],
            value["kind"],
            value["target_event_id"],
            value["reason"],
            value["source_action_key"],
        )
        if (
            not isinstance(scope, Mapping)
            or not isinstance(evidence, list)
            or not all(isinstance(item, str) for item in strings)
            or (replacement is not None and not isinstance(replacement, str))
        ):
            raise TypeError("approved event governance serialization is invalid")
        action_id, kind, target_event_id, reason, source_action_key = strings
        assert isinstance(action_id, str)
        assert isinstance(kind, str)
        assert isinstance(target_event_id, str)
        assert isinstance(reason, str)
        assert isinstance(source_action_key, str)
        return cls(
            EventId.from_string(action_id),
            MemoryScope.from_dict(scope),
            ApprovedEventGovernanceKind(kind),
            EventId.from_string(target_event_id),
            None if replacement is None else EventId.from_string(replacement),
            reason,
            source_action_key,
            _parse_datetime(value["occurred_at"], "occurred_at"),
            tuple(EvidenceReference.from_dict(item) for item in evidence),
        )
