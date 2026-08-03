"""Versioned, storage-independent context-packet contracts and budget validation."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol, Self, cast

from .identifiers import RequestId, TaskId
from .models import (
    EvidenceReference,
    MemoryScope,
    Sensitivity,
    SourceTrustClass,
    _parse_datetime,
    _require_aware,
    _strict_fields,
)


class TokenEstimator(Protocol):
    """Replaceable local estimator; provider tokenizers belong outside the domain package."""

    def estimate(self, content: str) -> int: ...


class PacketSchemaVersion(str, Enum):
    V1 = "1.0"


class ContextItemType(str, Enum):
    ACTIVE_TASK_CHECKPOINT = "active_task_checkpoint"
    EPISODIC_MEMORY = "episodic_memory"
    KNOWLEDGE = "knowledge"
    STRUCTURAL_FACT = "structural_fact"
    CODE_EXCERPT = "code_excerpt"
    SKILL = "skill"
    MANDATORY_PROCEDURE = "mandatory_procedure"


class ContentRepresentation(str, Enum):
    UNTRUSTED_EVIDENCE = "untrusted_evidence"


class ValidityState(str, Enum):
    CURRENT = "current"
    STALE = "stale"
    UNKNOWN = "unknown"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class ConflictState(str, Enum):
    NONE = "none"
    UNRESOLVED = "unresolved"
    RESOLVED = "resolved"


class OmissionReason(str, Enum):
    TOKEN_BUDGET = "token_budget"
    UNAUTHORIZED_SCOPE = "unauthorized_scope"
    PROHIBITED_SENSITIVITY = "prohibited_sensitivity"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"
    STALE = "stale"
    DUPLICATE = "duplicate"
    LOWER_RANK = "lower_rank"
    INVALID_EVIDENCE = "invalid_evidence"


class OverrideRequesterType(str, Enum):
    OWNER = "owner"
    AGENT = "agent"
    SYSTEM = "system"


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _nonnegative_int(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be an object")
    return cast(Mapping[str, object], value)


@dataclass(frozen=True, slots=True)
class BudgetOverride:
    requester_type: OverrideRequesterType
    requested_by: str
    reason: str
    requested_at: datetime

    def __post_init__(self) -> None:
        _text(self.requested_by, "requested_by")
        _text(self.reason, "reason")
        _require_aware(self.requested_at, "requested_at")

    def to_dict(self) -> dict[str, str]:
        return {
            "requester_type": self.requester_type.value,
            "requested_by": self.requested_by,
            "reason": self.reason,
            "requested_at": self.requested_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        _strict_fields(
            value, {"requester_type", "requested_by", "reason", "requested_at"}, "override"
        )
        return cls(
            requester_type=OverrideRequesterType(_text(value["requester_type"], "requester_type")),
            requested_by=_text(value["requested_by"], "requested_by"),
            reason=_text(value["reason"], "reason"),
            requested_at=_parse_datetime(value["requested_at"], "requested_at"),
        )


@dataclass(frozen=True, slots=True)
class ContextBudget:
    active_task_checkpoint: int = 600
    episodic_memories: int = 800
    knowledge: int = 1200
    structural: int = 1500
    skills_and_procedures: int = 1200
    provenance_and_conflicts: int = 400
    total_limit: int = 5700
    override: BudgetOverride | None = None

    def __post_init__(self) -> None:
        for name in (
            "active_task_checkpoint",
            "episodic_memories",
            "knowledge",
            "structural",
            "skills_and_procedures",
            "provenance_and_conflicts",
            "total_limit",
        ):
            _nonnegative_int(getattr(self, name), name)
        if self.total_limit > 8000 and self.override is None:
            raise ValueError("budgets above 8000 tokens require an explicit typed override")
        if self.total_limit <= 8000 and self.override is not None:
            raise ValueError("an override is valid only for a total limit above 8000 tokens")

    def to_dict(self) -> dict[str, object]:
        return {
            "active_task_checkpoint": self.active_task_checkpoint,
            "episodic_memories": self.episodic_memories,
            "knowledge": self.knowledge,
            "structural": self.structural,
            "skills_and_procedures": self.skills_and_procedures,
            "provenance_and_conflicts": self.provenance_and_conflicts,
            "total_limit": self.total_limit,
            "override": None if self.override is None else self.override.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        fields = {
            "active_task_checkpoint",
            "episodic_memories",
            "knowledge",
            "structural",
            "skills_and_procedures",
            "provenance_and_conflicts",
            "total_limit",
            "override",
        }
        _strict_fields(value, fields, "context budget")
        raw_override = value["override"]
        return cls(
            **{name: _nonnegative_int(value[name], name) for name in fields - {"override"}},
            override=None
            if raw_override is None
            else BudgetOverride.from_dict(_mapping(raw_override, "override")),
        )


DEFAULT_CONTEXT_BUDGET = ContextBudget()


@dataclass(frozen=True, slots=True)
class RankingMetadata:
    rank: int | None = None
    score: float | None = None
    retrieval_method: str | None = None

    def __post_init__(self) -> None:
        if self.rank is not None and (not isinstance(self.rank, int) or self.rank < 1):
            raise ValueError("rank must be a positive integer when present")
        if self.score is not None and (
            not isinstance(self.score, (int, float)) or isinstance(self.score, bool)
        ):
            raise ValueError("score must be numeric when present")
        if self.retrieval_method is not None:
            _text(self.retrieval_method, "retrieval_method")

    def to_dict(self) -> dict[str, object]:
        return {"rank": self.rank, "score": self.score, "retrieval_method": self.retrieval_method}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        _strict_fields(value, {"rank", "score", "retrieval_method"}, "ranking metadata")
        rank = value["rank"]
        score = value["score"]
        method = value["retrieval_method"]
        if rank is not None:
            _nonnegative_int(rank, "rank")
        if method is not None:
            _text(method, "retrieval_method")
        return cls(
            rank=cast(int | None, rank),
            score=cast(float | None, score),
            retrieval_method=cast(str | None, method),
        )


@dataclass(frozen=True, slots=True)
class ContextItem:
    item_id: str
    item_type: ContextItemType
    source_scope: MemoryScope
    content: str
    content_representation: ContentRepresentation
    token_estimate: int
    evidence_references: tuple[EvidenceReference, ...]
    source_trust: SourceTrustClass
    sensitivity: Sensitivity
    validity: ValidityState
    ranking: RankingMetadata | None
    conflict_state: ConflictState
    observed_at: datetime

    def __post_init__(self) -> None:
        _text(self.item_id, "item_id")
        if not isinstance(self.source_scope, MemoryScope):
            raise TypeError("source_scope must be a MemoryScope")
        _text(self.content, "content")
        _nonnegative_int(self.token_estimate, "token_estimate")
        evidence = tuple(self.evidence_references)
        if not evidence or any(not isinstance(item, EvidenceReference) for item in evidence):
            raise ValueError("every context item requires valid evidence references")
        object.__setattr__(self, "evidence_references", evidence)
        if self.sensitivity is Sensitivity.PROHIBITED:
            raise ValueError("prohibited content cannot enter a context packet")
        if self.ranking is not None and not isinstance(self.ranking, RankingMetadata):
            raise TypeError("ranking must be RankingMetadata when present")
        _require_aware(self.observed_at, "observed_at")

    def to_dict(self) -> dict[str, object]:
        return {
            "item_id": self.item_id,
            "item_type": self.item_type.value,
            "source_scope": self.source_scope.to_dict(),
            "content": self.content,
            "content_representation": self.content_representation.value,
            "token_estimate": self.token_estimate,
            "evidence_references": [item.to_dict() for item in self.evidence_references],
            "source_trust": self.source_trust.value,
            "sensitivity": self.sensitivity.value,
            "validity": self.validity.value,
            "ranking": None if self.ranking is None else self.ranking.to_dict(),
            "conflict_state": self.conflict_state.value,
            "observed_at": self.observed_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        fields = {
            "item_id",
            "item_type",
            "source_scope",
            "content",
            "content_representation",
            "token_estimate",
            "evidence_references",
            "source_trust",
            "sensitivity",
            "validity",
            "ranking",
            "conflict_state",
            "observed_at",
        }
        _strict_fields(value, fields, "context item")
        evidence = value["evidence_references"]
        if not isinstance(evidence, list):
            raise TypeError("evidence_references must be an array")
        return cls(
            item_id=_text(value["item_id"], "item_id"),
            item_type=ContextItemType(_text(value["item_type"], "item_type")),
            source_scope=MemoryScope.from_dict(_mapping(value["source_scope"], "source_scope")),
            content=_text(value["content"], "content"),
            content_representation=ContentRepresentation(
                _text(value["content_representation"], "content_representation")
            ),
            token_estimate=_nonnegative_int(value["token_estimate"], "token_estimate"),
            evidence_references=tuple(
                EvidenceReference.from_dict(_mapping(item, "evidence reference"))
                for item in evidence
            ),
            source_trust=SourceTrustClass(_text(value["source_trust"], "source_trust")),
            sensitivity=Sensitivity(_text(value["sensitivity"], "sensitivity")),
            validity=ValidityState(_text(value["validity"], "validity")),
            ranking=None
            if value["ranking"] is None
            else RankingMetadata.from_dict(_mapping(value["ranking"], "ranking")),
            conflict_state=ConflictState(_text(value["conflict_state"], "conflict_state")),
            observed_at=_parse_datetime(value["observed_at"], "observed_at"),
        )


@dataclass(frozen=True, slots=True)
class ProvenanceNotice:
    provenance_id: str
    item_id: str
    source_reference: str
    source_digest: str
    evidence_references: tuple[EvidenceReference, ...]
    token_estimate: int = 0

    def __post_init__(self) -> None:
        for name in ("provenance_id", "item_id", "source_reference", "source_digest"):
            _text(getattr(self, name), name)
        _nonnegative_int(self.token_estimate, "token_estimate")
        evidence = tuple(self.evidence_references)
        if not evidence or any(not isinstance(item, EvidenceReference) for item in evidence):
            raise ValueError("provenance requires valid evidence references")
        object.__setattr__(self, "evidence_references", evidence)

    def to_dict(self) -> dict[str, object]:
        return {
            "provenance_id": self.provenance_id,
            "item_id": self.item_id,
            "source_reference": self.source_reference,
            "source_digest": self.source_digest,
            "evidence_references": [item.to_dict() for item in self.evidence_references],
            "token_estimate": self.token_estimate,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        fields = {
            "provenance_id",
            "item_id",
            "source_reference",
            "source_digest",
            "evidence_references",
            "token_estimate",
        }
        _strict_fields(value, fields, "provenance notice")
        raw_evidence = value["evidence_references"]
        if not isinstance(raw_evidence, list):
            raise TypeError("provenance evidence_references must be an array")
        return cls(
            _text(value["provenance_id"], "provenance_id"),
            _text(value["item_id"], "item_id"),
            _text(value["source_reference"], "source_reference"),
            _text(value["source_digest"], "source_digest"),
            tuple(
                EvidenceReference.from_dict(_mapping(item, "evidence reference"))
                for item in raw_evidence
            ),
            _nonnegative_int(value["token_estimate"], "token_estimate"),
        )


@dataclass(frozen=True, slots=True)
class ConflictNotice:
    conflict_id: str
    item_ids: tuple[str, ...]
    evidence_references: tuple[EvidenceReference, ...]
    state: ConflictState
    token_estimate: int = 0

    def __post_init__(self) -> None:
        _text(self.conflict_id, "conflict_id")
        item_ids = tuple(self.item_ids)
        if len(item_ids) < 2 or any(
            not isinstance(item, str) or not item.strip() for item in item_ids
        ):
            raise ValueError("a conflict must preserve at least two item IDs")
        object.__setattr__(self, "item_ids", item_ids)
        evidence = tuple(self.evidence_references)
        if not evidence or any(not isinstance(item, EvidenceReference) for item in evidence):
            raise ValueError("a conflict requires valid evidence references")
        object.__setattr__(self, "evidence_references", evidence)
        _nonnegative_int(self.token_estimate, "token_estimate")

    def to_dict(self) -> dict[str, object]:
        return {
            "conflict_id": self.conflict_id,
            "item_ids": list(self.item_ids),
            "evidence_references": [item.to_dict() for item in self.evidence_references],
            "state": self.state.value,
            "token_estimate": self.token_estimate,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        fields = {"conflict_id", "item_ids", "evidence_references", "state", "token_estimate"}
        _strict_fields(value, fields, "conflict notice")
        ids, evidence = value["item_ids"], value["evidence_references"]
        if (
            not isinstance(ids, list)
            or not all(isinstance(item, str) for item in ids)
            or not isinstance(evidence, list)
        ):
            raise TypeError("conflict lists are invalid")
        return cls(
            _text(value["conflict_id"], "conflict_id"),
            tuple(ids),
            tuple(
                EvidenceReference.from_dict(_mapping(item, "evidence reference"))
                for item in evidence
            ),
            ConflictState(_text(value["state"], "state")),
            _nonnegative_int(value["token_estimate"], "token_estimate"),
        )


@dataclass(frozen=True, slots=True)
class OmissionNotice:
    item_id: str
    reason: OmissionReason
    detail: str | None = None

    def __post_init__(self) -> None:
        _text(self.item_id, "item_id")
        if self.detail is not None:
            _text(self.detail, "detail")

    def to_dict(self) -> dict[str, str | None]:
        return {"item_id": self.item_id, "reason": self.reason.value, "detail": self.detail}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        _strict_fields(value, {"item_id", "reason", "detail"}, "omission notice")
        detail = value["detail"]
        if detail is not None:
            _text(detail, "detail")
        return cls(
            _text(value["item_id"], "item_id"),
            OmissionReason(_text(value["reason"], "reason")),
            cast(str | None, detail),
        )


@dataclass(frozen=True, slots=True)
class ContextPacket:
    schema_version: PacketSchemaVersion
    request_id: RequestId
    owner_scope: MemoryScope
    query_id: str | None
    task_id: TaskId | None
    created_at: datetime
    expires_at: datetime | None
    declared_total_tokens: int
    budget: ContextBudget
    producer_version: str
    active_task_checkpoint: ContextItem | None = None
    episodic_memories: tuple[ContextItem, ...] = ()
    knowledge_items: tuple[ContextItem, ...] = ()
    structural_items: tuple[ContextItem, ...] = ()
    skills_and_procedures: tuple[ContextItem, ...] = ()
    provenance: tuple[ProvenanceNotice, ...] = ()
    conflicts: tuple[ConflictNotice, ...] = ()
    omissions: tuple[OmissionNotice, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, RequestId) or not isinstance(
            self.owner_scope, MemoryScope
        ):
            raise TypeError("packet IDs and owner_scope must use domain types")
        if (self.query_id is None) == (self.task_id is None):
            raise ValueError("packet requires exactly one query_id or task_id")
        if self.query_id is not None:
            _text(self.query_id, "query_id")
        if self.task_id is not None and not isinstance(self.task_id, TaskId):
            raise TypeError("task_id must be a TaskId")
        _require_aware(self.created_at, "created_at")
        if self.expires_at is not None:
            _require_aware(self.expires_at, "expires_at")
            if self.expires_at < self.created_at:
                raise ValueError("expires_at cannot precede created_at")
        _nonnegative_int(self.declared_total_tokens, "declared_total_tokens")
        _text(self.producer_version, "producer_version")
        if (
            self.active_task_checkpoint is not None
            and self.active_task_checkpoint.item_type is not ContextItemType.ACTIVE_TASK_CHECKPOINT
        ):
            raise ValueError("active_task_checkpoint must have the matching item type")
        sections = {
            "episodic_memories": (self.episodic_memories, {ContextItemType.EPISODIC_MEMORY}),
            "knowledge_items": (self.knowledge_items, {ContextItemType.KNOWLEDGE}),
            "structural_items": (
                self.structural_items,
                {ContextItemType.STRUCTURAL_FACT, ContextItemType.CODE_EXCERPT},
            ),
            "skills_and_procedures": (
                self.skills_and_procedures,
                {ContextItemType.SKILL, ContextItemType.MANDATORY_PROCEDURE},
            ),
        }
        for name, (items, allowed_types) in sections.items():
            typed_items = tuple(items)
            if any(
                not isinstance(item, ContextItem) or item.item_type not in allowed_types
                for item in typed_items
            ):
                raise ValueError(f"{name} contains an item of the wrong type")
            object.__setattr__(self, name, typed_items)
        self._validate_provenance_and_conflicts()
        self._validate_budget()

    def _validate_provenance_and_conflicts(self) -> None:
        items = self.items
        item_ids = {item.item_id for item in items}
        if len(item_ids) != len(items):
            raise ValueError("context item IDs must be unique")
        provenance_ids = {notice.item_id for notice in self.provenance}
        if item_ids != provenance_ids:
            raise ValueError("every included context item requires exactly one provenance notice")
        for notice in self.provenance:
            if notice.item_id not in item_ids:
                raise ValueError("provenance references an unknown item")
        for conflict in self.conflicts:
            if not set(conflict.item_ids).issubset(item_ids):
                raise ValueError("conflict references an unknown context item")

    @property
    def items(self) -> tuple[ContextItem, ...]:
        checkpoint = () if self.active_task_checkpoint is None else (self.active_task_checkpoint,)
        return (
            checkpoint
            + self.episodic_memories
            + self.knowledge_items
            + self.structural_items
            + self.skills_and_procedures
        )

    @property
    def section_tokens(self) -> dict[str, int]:
        return {
            "active_task_checkpoint": 0
            if self.active_task_checkpoint is None
            else self.active_task_checkpoint.token_estimate,
            "episodic_memories": sum(item.token_estimate for item in self.episodic_memories),
            "knowledge": sum(item.token_estimate for item in self.knowledge_items),
            "structural": sum(item.token_estimate for item in self.structural_items),
            "skills_and_procedures": sum(
                item.token_estimate for item in self.skills_and_procedures
            ),
            "provenance_and_conflicts": sum(item.token_estimate for item in self.provenance)
            + sum(item.token_estimate for item in self.conflicts),
        }

    @property
    def computed_total_tokens(self) -> int:
        return sum(self.section_tokens.values())

    @property
    def remaining_budget(self) -> int:
        return self.budget.total_limit - self.computed_total_tokens

    def _validate_budget(self) -> None:
        limits = self.budget.to_dict()
        for section, amount in self.section_tokens.items():
            limit = limits[section]
            assert isinstance(limit, int)
            if amount > limit:
                raise ValueError(f"{section} exceeds its token budget")
        if self.computed_total_tokens > self.budget.total_limit:
            raise ValueError("context packet exceeds its total hard token budget")
        if self.declared_total_tokens != self.computed_total_tokens:
            raise ValueError("declared_total_tokens must equal the schema-defined token sum")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version.value,
            "request_id": str(self.request_id),
            "owner_scope": self.owner_scope.to_dict(),
            "query_id": self.query_id,
            "task_id": None if self.task_id is None else str(self.task_id),
            "created_at": self.created_at.isoformat(),
            "expires_at": None if self.expires_at is None else self.expires_at.isoformat(),
            "declared_total_tokens": self.declared_total_tokens,
            "budget": self.budget.to_dict(),
            "producer_version": self.producer_version,
            "active_task_checkpoint": None
            if self.active_task_checkpoint is None
            else self.active_task_checkpoint.to_dict(),
            "episodic_memories": [item.to_dict() for item in self.episodic_memories],
            "knowledge_items": [item.to_dict() for item in self.knowledge_items],
            "structural_items": [item.to_dict() for item in self.structural_items],
            "skills_and_procedures": [item.to_dict() for item in self.skills_and_procedures],
            "provenance": [item.to_dict() for item in self.provenance],
            "conflicts": [item.to_dict() for item in self.conflicts],
            "omissions": [item.to_dict() for item in self.omissions],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        fields = {
            "schema_version",
            "request_id",
            "owner_scope",
            "query_id",
            "task_id",
            "created_at",
            "expires_at",
            "declared_total_tokens",
            "budget",
            "producer_version",
            "active_task_checkpoint",
            "episodic_memories",
            "knowledge_items",
            "structural_items",
            "skills_and_procedures",
            "provenance",
            "conflicts",
            "omissions",
        }
        _strict_fields(value, fields, "context packet")

        def list_of(name: str, parser: object) -> tuple[object, ...]:
            raw = value[name]
            if not isinstance(raw, list):
                raise TypeError(f"{name} must be an array")
            return tuple(parser(_mapping(item, name)) for item in raw)  # type: ignore[operator]

        raw_checkpoint = value["active_task_checkpoint"]
        return cls(
            schema_version=PacketSchemaVersion(_text(value["schema_version"], "schema_version")),
            request_id=RequestId.from_string(_text(value["request_id"], "request_id")),
            owner_scope=MemoryScope.from_dict(_mapping(value["owner_scope"], "owner_scope")),
            query_id=None if value["query_id"] is None else _text(value["query_id"], "query_id"),
            task_id=None
            if value["task_id"] is None
            else TaskId.from_string(_text(value["task_id"], "task_id")),
            created_at=_parse_datetime(value["created_at"], "created_at"),
            expires_at=None
            if value["expires_at"] is None
            else _parse_datetime(value["expires_at"], "expires_at"),
            declared_total_tokens=_nonnegative_int(
                value["declared_total_tokens"], "declared_total_tokens"
            ),
            budget=ContextBudget.from_dict(_mapping(value["budget"], "budget")),
            producer_version=_text(value["producer_version"], "producer_version"),
            active_task_checkpoint=None
            if raw_checkpoint is None
            else ContextItem.from_dict(_mapping(raw_checkpoint, "active_task_checkpoint")),
            episodic_memories=cast(
                tuple[ContextItem, ...], list_of("episodic_memories", ContextItem.from_dict)
            ),
            knowledge_items=cast(
                tuple[ContextItem, ...], list_of("knowledge_items", ContextItem.from_dict)
            ),
            structural_items=cast(
                tuple[ContextItem, ...], list_of("structural_items", ContextItem.from_dict)
            ),
            skills_and_procedures=cast(
                tuple[ContextItem, ...], list_of("skills_and_procedures", ContextItem.from_dict)
            ),
            provenance=cast(
                tuple[ProvenanceNotice, ...], list_of("provenance", ProvenanceNotice.from_dict)
            ),
            conflicts=cast(
                tuple[ConflictNotice, ...], list_of("conflicts", ConflictNotice.from_dict)
            ),
            omissions=cast(
                tuple[OmissionNotice, ...], list_of("omissions", OmissionNotice.from_dict)
            ),
        )

    @classmethod
    def from_json(cls, value: str) -> Self:
        parsed = json.loads(value)
        return cls.from_dict(_mapping(parsed, "context packet"))
