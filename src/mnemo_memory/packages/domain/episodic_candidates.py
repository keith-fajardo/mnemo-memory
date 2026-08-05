"""Typed, evidence-bearing episodic candidates proposed from minimized task events."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, Self
from uuid import UUID, uuid5

from .identifiers import EventId, MemoryId
from .models import (
    DurableClaim,
    EvidenceReference,
    EvidenceSourceType,
    MemoryClassification,
    MemoryScope,
    MemoryStatus,
    RetentionSchedule,
    ScopeLevel,
    Sensitivity,
    SourceTrustClass,
    VerificationStatus,
    _parse_datetime,
    _require_aware,
)
from .task_activity_events import TaskActivityActor, TaskActivityEvent, TaskActivityEventKind

_CANDIDATE_NAMESPACE = UUID("ed4ec4fe-7e6c-42aa-b98b-d0d9a9c34876")
_REVIEW_NAMESPACE = UUID("34339822-e386-49d5-ac4f-f97fb94a2a4d")
_MAX_CANDIDATES = 4
_MAX_CLAIM_LENGTH = 1_200
_MAX_VERSION_LENGTH = 128


class EpisodicMemoryKind(StrEnum):
    DECISION = "decision"
    FAILURE = "failure"
    OUTCOME = "outcome"
    LESSON = "lesson"
    PREFERENCE = "preference"


@dataclass(frozen=True, slots=True)
class EpisodicExtractionRequest:
    """The only minimized event fields an extraction provider may receive."""

    event_id: EventId
    kind: TaskActivityEventKind
    actor: TaskActivityActor
    summary: str
    max_candidates: int = _MAX_CANDIDATES

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, EventId):
            raise TypeError("episodic extraction event identity is invalid")
        if not isinstance(self.kind, TaskActivityEventKind) or not isinstance(
            self.actor, TaskActivityActor
        ):
            raise TypeError("episodic extraction event metadata is invalid")
        if (
            not isinstance(self.summary, str)
            or not self.summary.strip()
            or len(self.summary) > _MAX_CLAIM_LENGTH
        ):
            raise ValueError("episodic extraction summary is invalid")
        if isinstance(self.max_candidates, bool) or not 1 <= self.max_candidates <= _MAX_CANDIDATES:
            raise ValueError("episodic extraction candidate limit is invalid")

    @classmethod
    def from_event(cls, event: TaskActivityEvent) -> Self:
        if not isinstance(event, TaskActivityEvent):
            raise TypeError("episodic extraction requires a task activity event")
        return cls(event.event_id, event.kind, event.actor, event.summary)

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": str(self.event_id),
            "kind": self.kind.value,
            "actor": self.actor.value,
            "summary": self.summary,
            "max_candidates": self.max_candidates,
        }


@dataclass(frozen=True, slots=True)
class EpisodicExtractionProposal:
    """Strict provider proposal; it deliberately cannot carry authority-bearing fields."""

    kind: EpisodicMemoryKind
    claim: str
    confidence: float
    sensitivity: Sensitivity

    def __post_init__(self) -> None:
        if not isinstance(self.kind, EpisodicMemoryKind):
            raise TypeError("episodic proposal kind is invalid")
        if (
            not isinstance(self.claim, str)
            or not self.claim.strip()
            or len(self.claim) > _MAX_CLAIM_LENGTH
        ):
            raise ValueError("episodic proposal claim is invalid")
        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
            or not math.isfinite(self.confidence)
            or not 0.0 <= self.confidence <= 1.0
        ):
            raise ValueError("episodic proposal confidence is invalid")
        if (
            not isinstance(self.sensitivity, Sensitivity)
            or self.sensitivity is Sensitivity.PROHIBITED
        ):
            raise ValueError("prohibited content cannot become an episodic proposal")
        object.__setattr__(self, "confidence", float(self.confidence))


class EpisodicExtractionPort(Protocol):
    @property
    def extractor_version(self) -> str: ...

    @property
    def provider_id(self) -> str: ...

    @property
    def model_id(self) -> str: ...

    @property
    def prompt_version(self) -> str: ...

    def extract(
        self, request: EpisodicExtractionRequest
    ) -> tuple[EpisodicExtractionProposal, ...]: ...


@dataclass(frozen=True, slots=True)
class EpisodicMemoryCandidate:
    memory: DurableClaim
    kind: EpisodicMemoryKind
    source_event_id: EventId
    proposal_index: int
    confidence: float
    extractor_version: str
    provider_id: str
    model_id: str
    prompt_version: str
    created_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.memory, DurableClaim):
            raise TypeError("episodic candidate memory is invalid")
        if (
            self.memory.scope.level is not ScopeLevel.TASK
            or self.memory.classification.status is not MemoryStatus.CANDIDATE
            or self.memory.classification.sensitivity is Sensitivity.PROHIBITED
        ):
            raise ValueError("episodic candidate must be non-prohibited candidate memory")
        if not isinstance(self.kind, EpisodicMemoryKind) or not isinstance(
            self.source_event_id, EventId
        ):
            raise TypeError("episodic candidate type or source identity is invalid")
        if isinstance(self.proposal_index, bool) or not 0 <= self.proposal_index < _MAX_CANDIDATES:
            raise ValueError("episodic candidate proposal index is invalid")
        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
            or not math.isfinite(self.confidence)
            or not 0.0 <= self.confidence <= 1.0
        ):
            raise ValueError("episodic candidate confidence is invalid")
        for name in ("extractor_version", "provider_id", "model_id", "prompt_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > _MAX_VERSION_LENGTH:
                raise ValueError(f"episodic candidate {name} is invalid")
        _require_aware(self.created_at, "created_at")
        expected_id = self.identity(
            self.source_event_id, self.extractor_version, self.proposal_index
        )
        if self.memory.memory_id != expected_id:
            raise ValueError("episodic candidate identity is not deterministic")
        object.__setattr__(self, "confidence", float(self.confidence))

    @staticmethod
    def identity(source_event_id: EventId, extractor_version: str, proposal_index: int) -> MemoryId:
        return MemoryId(
            uuid5(_CANDIDATE_NAMESPACE, f"{source_event_id}:{extractor_version}:{proposal_index}")
        )

    @classmethod
    def create(
        cls,
        *,
        source_event: TaskActivityEvent,
        proposal: EpisodicExtractionProposal,
        proposal_index: int,
        sensitivity: Sensitivity,
        extractor_version: str,
        provider_id: str,
        model_id: str,
        prompt_version: str,
        created_at: datetime,
    ) -> Self:
        if not isinstance(source_event, TaskActivityEvent):
            raise TypeError("episodic candidate source event is invalid")
        memory_id = cls.identity(source_event.event_id, extractor_version, proposal_index)
        return cls(
            DurableClaim(
                memory_id,
                source_event.scope,
                MemoryClassification(sensitivity, MemoryStatus.CANDIDATE),
                source_event.retention,
                proposal.claim,
                source_event.evidence_references,
            ),
            proposal.kind,
            source_event.event_id,
            proposal_index,
            proposal.confidence,
            extractor_version,
            provider_id,
            model_id,
            prompt_version,
            created_at,
        )

    @property
    def memory_id(self) -> MemoryId:
        return self.memory.memory_id

    @property
    def scope(self) -> MemoryScope:
        return self.memory.scope

    @property
    def retention(self) -> RetentionSchedule:
        return self.memory.retention

    @property
    def evidence_references(self) -> tuple[EvidenceReference, ...]:
        return self.memory.evidence_references

    def to_dict(self) -> dict[str, object]:
        return {
            "memory": self.memory.to_dict(),
            "kind": self.kind.value,
            "source_event_id": str(self.source_event_id),
            "proposal_index": self.proposal_index,
            "confidence": self.confidence,
            "extractor_version": self.extractor_version,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "prompt_version": self.prompt_version,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        expected = {
            "memory",
            "kind",
            "source_event_id",
            "proposal_index",
            "confidence",
            "extractor_version",
            "provider_id",
            "model_id",
            "prompt_version",
            "created_at",
        }
        if set(value) != expected:
            raise ValueError("episodic candidate fields are invalid")
        memory = value["memory"]
        if not isinstance(memory, Mapping):
            raise TypeError("episodic candidate memory must be an object")
        string_fields = (
            value["kind"],
            value["source_event_id"],
            value["extractor_version"],
            value["provider_id"],
            value["model_id"],
            value["prompt_version"],
        )
        if not all(isinstance(item, str) for item in string_fields):
            raise TypeError("episodic candidate metadata must be strings")
        index = value["proposal_index"]
        confidence = value["confidence"]
        if isinstance(index, bool) or not isinstance(index, int):
            raise TypeError("episodic candidate proposal index must be an integer")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise TypeError("episodic candidate confidence must be numeric")
        return cls(
            DurableClaim.from_dict(memory),
            EpisodicMemoryKind(str(value["kind"])),
            EventId.from_string(str(value["source_event_id"])),
            index,
            float(confidence),
            str(value["extractor_version"]),
            str(value["provider_id"]),
            str(value["model_id"]),
            str(value["prompt_version"]),
            _parse_datetime(value["created_at"], "created_at"),
        )


class EpisodicCandidateReviewDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class EpisodicCandidateReviewAction:
    action_id: EventId
    scope: MemoryScope
    candidate_id: MemoryId
    decision: EpisodicCandidateReviewDecision
    actor: TaskActivityActor
    source_action_key: str
    reason: str
    reviewed_at: datetime
    evidence_references: tuple[EvidenceReference, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.action_id, EventId) or not isinstance(self.candidate_id, MemoryId):
            raise TypeError("episodic candidate review identity is invalid")
        if not isinstance(self.scope, MemoryScope) or self.scope.level is not ScopeLevel.TASK:
            raise ValueError("episodic candidate review requires task scope")
        if not isinstance(self.decision, EpisodicCandidateReviewDecision):
            raise TypeError("episodic candidate review decision is invalid")
        if self.actor is not TaskActivityActor.USER:
            raise ValueError("only a user may review an episodic candidate")
        if (
            not isinstance(self.source_action_key, str)
            or not self.source_action_key.strip()
            or len(self.source_action_key) > 256
        ):
            raise ValueError("episodic candidate review action key is invalid")
        if (
            not isinstance(self.reason, str)
            or not self.reason.strip()
            or len(self.reason) > _MAX_CLAIM_LENGTH
        ):
            raise ValueError("episodic candidate review reason is invalid")
        _require_aware(self.reviewed_at, "reviewed_at")
        evidence = tuple(self.evidence_references)
        if (
            not 1 <= len(evidence) <= 16
            or len({item.evidence_id for item in evidence}) != len(evidence)
            or any(
                not isinstance(item, EvidenceReference)
                or item.source_type is not EvidenceSourceType.USER_CORRECTION
                or item.trust_class is not SourceTrustClass.USER_CORRECTION
                or item.verification_status is not VerificationStatus.VERIFIED
                for item in evidence
            )
        ):
            raise ValueError("episodic candidate review requires verified user-correction evidence")
        expected = self.identity(self.scope, self.candidate_id, self.source_action_key)
        if self.action_id != expected:
            raise ValueError("episodic candidate review identity is not deterministic")
        object.__setattr__(
            self,
            "evidence_references",
            tuple(sorted(evidence, key=lambda item: str(item.evidence_id))),
        )

    @staticmethod
    def identity(scope: MemoryScope, candidate_id: MemoryId, source_action_key: str) -> EventId:
        return EventId(
            uuid5(_REVIEW_NAMESPACE, f"{scope.to_dict()}:{candidate_id}:{source_action_key}")
        )

    @classmethod
    def create(
        cls,
        *,
        scope: MemoryScope,
        candidate_id: MemoryId,
        decision: EpisodicCandidateReviewDecision,
        source_action_key: str,
        reason: str,
        reviewed_at: datetime,
        evidence_references: tuple[EvidenceReference, ...],
    ) -> Self:
        return cls(
            cls.identity(scope, candidate_id, source_action_key),
            scope,
            candidate_id,
            decision,
            TaskActivityActor.USER,
            source_action_key,
            reason,
            reviewed_at,
            evidence_references,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "action_id": str(self.action_id),
            "scope": self.scope.to_dict(),
            "candidate_id": str(self.candidate_id),
            "decision": self.decision.value,
            "actor": self.actor.value,
            "source_action_key": self.source_action_key,
            "reason": self.reason,
            "reviewed_at": self.reviewed_at.isoformat(),
            "evidence_references": [item.to_dict() for item in self.evidence_references],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        expected = {
            "action_id",
            "scope",
            "candidate_id",
            "decision",
            "actor",
            "source_action_key",
            "reason",
            "reviewed_at",
            "evidence_references",
        }
        if set(value) != expected:
            raise ValueError("episodic candidate review fields are invalid")
        scope = value["scope"]
        evidence = value["evidence_references"]
        text = tuple(
            value[name]
            for name in (
                "action_id",
                "candidate_id",
                "decision",
                "actor",
                "source_action_key",
                "reason",
            )
        )
        if (
            not isinstance(scope, Mapping)
            or not isinstance(evidence, list)
            or not all(isinstance(item, Mapping) for item in evidence)
            or not all(isinstance(item, str) for item in text)
        ):
            raise TypeError("episodic candidate review serialization is invalid")
        return cls(
            EventId.from_string(str(value["action_id"])),
            MemoryScope.from_dict(scope),
            MemoryId.from_string(str(value["candidate_id"])),
            EpisodicCandidateReviewDecision(str(value["decision"])),
            TaskActivityActor(str(value["actor"])),
            str(value["source_action_key"]),
            str(value["reason"]),
            _parse_datetime(value["reviewed_at"], "reviewed_at"),
            tuple(EvidenceReference.from_dict(item) for item in evidence),
        )


@dataclass(frozen=True, slots=True)
class ActiveEpisodicMemory:
    memory: DurableClaim
    kind: EpisodicMemoryKind
    candidate_id: MemoryId
    source_event_id: EventId
    confidence: float
    extractor_version: str
    provider_id: str
    model_id: str
    prompt_version: str
    approval_action_id: EventId
    activated_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.memory, DurableClaim):
            raise TypeError("active episodic memory is invalid")
        if (
            self.memory.classification.status is not MemoryStatus.ACTIVE
            or self.memory.classification.sensitivity is Sensitivity.PROHIBITED
            or self.memory.scope.level is not ScopeLevel.TASK
        ):
            raise ValueError("active episodic memory classification is invalid")
        if self.memory.memory_id != self.candidate_id:
            raise ValueError("active episodic memory must retain candidate identity")
        if not isinstance(self.kind, EpisodicMemoryKind) or not isinstance(
            self.source_event_id, EventId
        ):
            raise TypeError("active episodic memory provenance is invalid")
        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
            or not math.isfinite(self.confidence)
            or not 0.0 <= self.confidence <= 1.0
        ):
            raise ValueError("active episodic memory confidence is invalid")
        for name in ("extractor_version", "provider_id", "model_id", "prompt_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > _MAX_VERSION_LENGTH:
                raise ValueError(f"active episodic memory {name} is invalid")
        if not isinstance(self.approval_action_id, EventId):
            raise TypeError("active episodic memory approval identity is invalid")
        _require_aware(self.activated_at, "activated_at")
        object.__setattr__(self, "confidence", float(self.confidence))

    @classmethod
    def approve(
        cls,
        candidate: EpisodicMemoryCandidate,
        action: EpisodicCandidateReviewAction,
    ) -> Self:
        if action.decision is not EpisodicCandidateReviewDecision.APPROVED:
            raise ValueError("only approval can create active episodic memory")
        if action.scope != candidate.scope or action.candidate_id != candidate.memory_id:
            raise ValueError("episodic candidate approval scope or identity does not match")
        evidence_by_id: dict[object, EvidenceReference] = {}
        for evidence in (*candidate.evidence_references, *action.evidence_references):
            existing = evidence_by_id.get(evidence.evidence_id)
            if existing is not None and existing != evidence:
                raise ValueError("episodic memory evidence identity conflicts")
            evidence_by_id[evidence.evidence_id] = evidence
        memory = DurableClaim(
            candidate.memory_id,
            candidate.scope,
            candidate.memory.classification.activate(),
            candidate.retention,
            candidate.memory.claim,
            tuple(evidence_by_id.values()),
        )
        return cls(
            memory,
            candidate.kind,
            candidate.memory_id,
            candidate.source_event_id,
            candidate.confidence,
            candidate.extractor_version,
            candidate.provider_id,
            candidate.model_id,
            candidate.prompt_version,
            action.action_id,
            action.reviewed_at,
        )

    @property
    def memory_id(self) -> MemoryId:
        return self.memory.memory_id

    @property
    def scope(self) -> MemoryScope:
        return self.memory.scope

    def to_dict(self) -> dict[str, object]:
        return {
            "memory": self.memory.to_dict(),
            "kind": self.kind.value,
            "candidate_id": str(self.candidate_id),
            "source_event_id": str(self.source_event_id),
            "confidence": self.confidence,
            "extractor_version": self.extractor_version,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "prompt_version": self.prompt_version,
            "approval_action_id": str(self.approval_action_id),
            "activated_at": self.activated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        expected = {
            "memory",
            "kind",
            "candidate_id",
            "source_event_id",
            "confidence",
            "extractor_version",
            "provider_id",
            "model_id",
            "prompt_version",
            "approval_action_id",
            "activated_at",
        }
        if set(value) != expected:
            raise ValueError("active episodic memory fields are invalid")
        memory = value["memory"]
        string_fields = tuple(
            value[name]
            for name in (
                "kind",
                "candidate_id",
                "source_event_id",
                "extractor_version",
                "provider_id",
                "model_id",
                "prompt_version",
                "approval_action_id",
            )
        )
        if not isinstance(memory, Mapping) or not all(
            isinstance(item, str) for item in string_fields
        ):
            raise TypeError("active episodic memory serialization is invalid")
        confidence = value["confidence"]
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise TypeError("active episodic memory confidence must be numeric")
        return cls(
            DurableClaim.from_dict(memory),
            EpisodicMemoryKind(str(value["kind"])),
            MemoryId.from_string(str(value["candidate_id"])),
            EventId.from_string(str(value["source_event_id"])),
            float(confidence),
            str(value["extractor_version"]),
            str(value["provider_id"]),
            str(value["model_id"]),
            str(value["prompt_version"]),
            EventId.from_string(str(value["approval_action_id"])),
            _parse_datetime(value["activated_at"], "activated_at"),
        )
