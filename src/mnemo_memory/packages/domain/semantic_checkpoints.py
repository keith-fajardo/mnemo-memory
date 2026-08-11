"""Canonical semantic atoms, patches, and materialized checkpoint metadata."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Protocol, Self
from uuid import UUID, uuid5

from .identifiers import CheckpointId, EventId, MemoryId
from .models import MemoryScope, ScopeLevel, _parse_datetime, _require_aware
from .task_activity_events import TaskActivityEvent

SEMANTIC_CHECKPOINT_SCHEMA_VERSION = "1.0"

_ATOM_NAMESPACE = UUID("f8cc6cbc-5381-44e8-9dce-10a1fdf68761")
_CHECKPOINT_NAMESPACE = UUID("c559476a-0038-45f1-89a8-d9d6930b8b31")
_MAX_TEXT = 4_096
_MAX_QUALIFIERS = 32
_MAX_SOURCE_EVENTS = 64
_MAX_PATCH_OPERATIONS = 512


class SemanticAtomKind(StrEnum):
    GOAL = "goal"
    FACT = "fact"
    STATE = "state"
    DECISION = "decision"
    CONSTRAINT = "constraint"
    PREFERENCE = "preference"
    OPEN_QUESTION = "open_question"
    NEXT_ACTION = "next_action"
    RESULT = "result"
    FAILURE = "failure"
    INFERENCE = "inference"


class SemanticAtomStatus(StrEnum):
    ACTIVE = "active"
    RESOLVED = "resolved"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"


class SemanticCheckpointType(StrEnum):
    DELTA = "delta"
    SNAPSHOT = "snapshot"


class SemanticRendererProfile(StrEnum):
    COMPACT = "compact"
    PORTABLE = "portable"
    AUDIT = "audit"


class SemanticPatchOperationKind(StrEnum):
    ADD = "add"
    UPDATE_METADATA = "update_metadata"
    SUPERSEDE = "supersede"
    RESOLVE = "resolve"
    EXPIRE = "expire"
    ACTIVATE = "activate_in_checkpoint"
    REMOVE = "remove_from_active_checkpoint"


def _text(value: str, name: str, *, allow_blank: bool = False) -> str:
    if not isinstance(value, str) or (not allow_blank and not value.strip()):
        raise ValueError(f"semantic {name} is invalid")
    if len(value) > _MAX_TEXT:
        raise ValueError(f"semantic {name} is too long")
    return value


def _scope_digest(scope: MemoryScope) -> str:
    payload = json.dumps(scope.to_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SemanticMemoryAtom:
    """One typed meaning unit with explicit attribution and evidence lineage."""

    atom_id: MemoryId
    scope: MemoryScope
    kind: SemanticAtomKind
    subject: str
    predicate: str
    object_value: str
    qualifiers: tuple[tuple[str, str], ...]
    confidence: float
    priority: int
    status: SemanticAtomStatus
    valid_from: datetime | None
    valid_until: datetime | None
    source_event_ids: tuple[EventId, ...]
    supersedes_atom_id: MemoryId | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.atom_id, MemoryId):
            raise TypeError("semantic atom identity is invalid")
        if not isinstance(self.scope, MemoryScope) or self.scope.level is not ScopeLevel.TASK:
            raise ValueError("semantic atoms require exact task scope")
        if not isinstance(self.kind, SemanticAtomKind) or not isinstance(
            self.status, SemanticAtomStatus
        ):
            raise TypeError("semantic atom type or status is invalid")
        _text(self.subject, "subject")
        _text(self.predicate, "predicate")
        _text(self.object_value, "object")
        qualifiers = tuple(self.qualifiers)
        if (
            len(qualifiers) > _MAX_QUALIFIERS
            or len({key for key, _ in qualifiers}) != len(qualifiers)
            or any(
                not isinstance(key, str)
                or not isinstance(value, str)
                or not key.strip()
                or not value.strip()
                or len(key) > 128
                or len(value) > 1_024
                for key, value in qualifiers
            )
        ):
            raise ValueError("semantic atom qualifiers are invalid")
        qualifiers = tuple(sorted(qualifiers))
        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
            or not math.isfinite(self.confidence)
            or not 0.0 <= self.confidence <= 1.0
        ):
            raise ValueError("semantic atom confidence is invalid")
        if (
            isinstance(self.priority, bool)
            or not isinstance(self.priority, int)
            or not 0 <= self.priority <= 100
        ):
            raise ValueError("semantic atom priority is invalid")
        source_ids = tuple(self.source_event_ids)
        if (
            not 1 <= len(source_ids) <= _MAX_SOURCE_EVENTS
            or len(set(source_ids)) != len(source_ids)
            or any(not isinstance(item, EventId) for item in source_ids)
        ):
            raise ValueError("semantic atom source events are invalid")
        if self.supersedes_atom_id is not None and not isinstance(
            self.supersedes_atom_id, MemoryId
        ):
            raise TypeError("semantic atom supersession identity is invalid")
        for value, name in (
            (self.created_at, "created_at"),
            (self.updated_at, "updated_at"),
        ):
            _require_aware(value, name)
        if self.updated_at < self.created_at:
            raise ValueError("semantic atom update precedes creation")
        if self.valid_from is not None:
            _require_aware(self.valid_from, "valid_from")
        if self.valid_until is not None:
            _require_aware(self.valid_until, "valid_until")
        if (
            self.valid_from is not None
            and self.valid_until is not None
            and self.valid_until < self.valid_from
        ):
            raise ValueError("semantic atom validity interval is invalid")
        expected = self.identity(
            self.scope,
            self.kind,
            self.subject,
            self.predicate,
            self.object_value,
            qualifiers,
            source_ids,
            self.supersedes_atom_id,
        )
        if self.atom_id != expected:
            raise ValueError("semantic atom identity is not deterministic")
        object.__setattr__(self, "qualifiers", qualifiers)
        object.__setattr__(self, "source_event_ids", source_ids)
        object.__setattr__(self, "confidence", float(self.confidence))

    @staticmethod
    def identity(
        scope: MemoryScope,
        kind: SemanticAtomKind,
        subject: str,
        predicate: str,
        object_value: str,
        qualifiers: tuple[tuple[str, str], ...],
        source_event_ids: tuple[EventId, ...],
        supersedes_atom_id: MemoryId | None,
    ) -> MemoryId:
        identity = {
            "scope": scope.to_dict(),
            "kind": kind.value,
            "subject": subject,
            "predicate": predicate,
            "object": object_value,
            "qualifiers": dict(sorted(qualifiers)),
            "source_event_ids": sorted(str(item) for item in source_event_ids),
            "supersedes_atom_id": (None if supersedes_atom_id is None else str(supersedes_atom_id)),
        }
        canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
        return MemoryId(uuid5(_ATOM_NAMESPACE, canonical))

    @classmethod
    def create(
        cls,
        *,
        scope: MemoryScope,
        kind: SemanticAtomKind,
        subject: str,
        predicate: str,
        object_value: str,
        source_event_ids: tuple[EventId, ...],
        created_at: datetime,
        qualifiers: tuple[tuple[str, str], ...] = (),
        confidence: float = 1.0,
        priority: int = 50,
        status: SemanticAtomStatus = SemanticAtomStatus.ACTIVE,
        valid_from: datetime | None = None,
        valid_until: datetime | None = None,
        supersedes_atom_id: MemoryId | None = None,
        updated_at: datetime | None = None,
    ) -> Self:
        ordered_qualifiers = tuple(sorted(qualifiers))
        ordered_sources = tuple(source_event_ids)
        return cls(
            cls.identity(
                scope,
                kind,
                subject,
                predicate,
                object_value,
                ordered_qualifiers,
                ordered_sources,
                supersedes_atom_id,
            ),
            scope,
            kind,
            subject,
            predicate,
            object_value,
            ordered_qualifiers,
            confidence,
            priority,
            status,
            valid_from,
            valid_until,
            ordered_sources,
            supersedes_atom_id,
            created_at,
            updated_at or created_at,
        )

    def with_status(self, status: SemanticAtomStatus, *, updated_at: datetime) -> Self:
        return replace(self, status=status, updated_at=updated_at)

    def to_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "id": str(self.atom_id),
            "scope": self.scope.to_dict(),
            "kind": self.kind.value,
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.object_value,
            "confidence": self.confidence,
            "priority": self.priority,
            "status": self.status.value,
            "source_event_ids": [str(item) for item in self.source_event_ids],
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
        if self.qualifiers:
            value["qualifiers"] = dict(self.qualifiers)
        if self.valid_from is not None:
            value["valid_from"] = self.valid_from.isoformat()
        if self.valid_until is not None:
            value["valid_until"] = self.valid_until.isoformat()
        if self.supersedes_atom_id is not None:
            value["supersedes_atom_id"] = str(self.supersedes_atom_id)
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        required = {
            "id",
            "scope",
            "kind",
            "subject",
            "predicate",
            "object",
            "confidence",
            "priority",
            "status",
            "source_event_ids",
            "created_at",
            "updated_at",
        }
        optional = {"qualifiers", "valid_from", "valid_until", "supersedes_atom_id"}
        if not required.issubset(value) or not set(value).issubset(required | optional):
            raise ValueError("semantic atom fields are invalid")
        scope = value["scope"]
        source_ids = value["source_event_ids"]
        qualifiers = value.get("qualifiers", {})
        if (
            not isinstance(scope, Mapping)
            or not isinstance(source_ids, list)
            or not all(isinstance(item, str) for item in source_ids)
            or not isinstance(qualifiers, Mapping)
            or not all(
                isinstance(key, str) and isinstance(item, str) for key, item in qualifiers.items()
            )
        ):
            raise TypeError("semantic atom serialization is invalid")
        confidence = value["confidence"]
        priority = value["priority"]
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise TypeError("semantic atom confidence must be numeric")
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise TypeError("semantic atom priority must be an integer")
        strings = ("id", "kind", "subject", "predicate", "object", "status")
        if not all(isinstance(value[name], str) for name in strings):
            raise TypeError("semantic atom text fields are invalid")
        supersedes = value.get("supersedes_atom_id")
        if supersedes is not None and not isinstance(supersedes, str):
            raise TypeError("semantic atom supersession identity is invalid")
        return cls(
            MemoryId.from_string(str(value["id"])),
            MemoryScope.from_dict(scope),
            SemanticAtomKind(str(value["kind"])),
            str(value["subject"]),
            str(value["predicate"]),
            str(value["object"]),
            tuple((str(key), str(item)) for key, item in qualifiers.items()),
            float(confidence),
            priority,
            SemanticAtomStatus(str(value["status"])),
            None
            if value.get("valid_from") is None
            else _parse_datetime(value["valid_from"], "valid_from"),
            None
            if value.get("valid_until") is None
            else _parse_datetime(value["valid_until"], "valid_until"),
            tuple(EventId.from_string(item) for item in source_ids),
            None if supersedes is None else MemoryId.from_string(supersedes),
            _parse_datetime(value["created_at"], "created_at"),
            _parse_datetime(value["updated_at"], "updated_at"),
        )


@dataclass(frozen=True, slots=True)
class SemanticPatchOperation:
    kind: SemanticPatchOperationKind
    atom: SemanticMemoryAtom | None = None
    target_atom_id: MemoryId | None = None
    inclusion_reason: str | None = None
    checkpoint_priority: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, SemanticPatchOperationKind):
            raise TypeError("semantic patch operation kind is invalid")
        atom_required = self.kind in {
            SemanticPatchOperationKind.ADD,
            SemanticPatchOperationKind.UPDATE_METADATA,
            SemanticPatchOperationKind.SUPERSEDE,
        }
        target_required = self.kind in {
            SemanticPatchOperationKind.UPDATE_METADATA,
            SemanticPatchOperationKind.SUPERSEDE,
            SemanticPatchOperationKind.RESOLVE,
            SemanticPatchOperationKind.EXPIRE,
            SemanticPatchOperationKind.ACTIVATE,
            SemanticPatchOperationKind.REMOVE,
        }
        if atom_required != (self.atom is not None):
            raise ValueError("semantic patch atom presence is invalid")
        if target_required != (self.target_atom_id is not None):
            raise ValueError("semantic patch target presence is invalid")
        if self.inclusion_reason is not None:
            _text(self.inclusion_reason, "patch inclusion reason")
        if self.checkpoint_priority is not None and (
            isinstance(self.checkpoint_priority, bool)
            or not isinstance(self.checkpoint_priority, int)
            or not 0 <= self.checkpoint_priority <= 100
        ):
            raise ValueError("semantic checkpoint priority is invalid")

    def to_dict(self) -> dict[str, object]:
        value: dict[str, object] = {"operation": self.kind.value}
        if self.atom is not None:
            value["atom"] = self.atom.to_dict()
        if self.target_atom_id is not None:
            value["target_atom_id"] = str(self.target_atom_id)
        if self.inclusion_reason is not None:
            value["inclusion_reason"] = self.inclusion_reason
        if self.checkpoint_priority is not None:
            value["checkpoint_priority"] = self.checkpoint_priority
        return value


@dataclass(frozen=True, slots=True)
class SemanticCheckpointPatch:
    base_checkpoint_id: CheckpointId | None
    operations: tuple[SemanticPatchOperation, ...]
    processed_event_ids: tuple[EventId, ...] = ()

    def __post_init__(self) -> None:
        if self.base_checkpoint_id is not None and not isinstance(
            self.base_checkpoint_id, CheckpointId
        ):
            raise TypeError("semantic patch base checkpoint identity is invalid")
        operations = tuple(self.operations)
        if not 1 <= len(operations) <= _MAX_PATCH_OPERATIONS or any(
            not isinstance(item, SemanticPatchOperation) for item in operations
        ):
            raise ValueError("semantic checkpoint patch operations are invalid")
        processed = tuple(self.processed_event_ids)
        if (
            len(processed) > _MAX_PATCH_OPERATIONS
            or len(set(processed)) != len(processed)
            or any(not isinstance(item, EventId) for item in processed)
        ):
            raise ValueError("semantic checkpoint processed events are invalid")
        object.__setattr__(self, "operations", operations)
        object.__setattr__(self, "processed_event_ids", processed)

    @property
    def digest(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "base_checkpoint_id": (
                None if self.base_checkpoint_id is None else str(self.base_checkpoint_id)
            ),
            "operations": [item.to_dict() for item in self.operations],
            "processed_event_ids": [str(item) for item in self.processed_event_ids],
        }


@dataclass(frozen=True, slots=True)
class SemanticCheckpointAtom:
    atom_id: MemoryId
    inclusion_reason: str
    checkpoint_priority: int

    def __post_init__(self) -> None:
        if not isinstance(self.atom_id, MemoryId):
            raise TypeError("checkpoint atom identity is invalid")
        _text(self.inclusion_reason, "checkpoint inclusion reason")
        if (
            isinstance(self.checkpoint_priority, bool)
            or not isinstance(self.checkpoint_priority, int)
            or not 0 <= self.checkpoint_priority <= 100
        ):
            raise ValueError("checkpoint atom priority is invalid")


@dataclass(frozen=True, slots=True)
class SemanticCheckpoint:
    checkpoint_id: CheckpointId
    scope: MemoryScope
    parent_checkpoint_id: CheckpointId | None
    generation: int
    schema_version: str
    checkpoint_type: SemanticCheckpointType
    head_event_id: EventId
    created_at: datetime
    renderer_profile: SemanticRendererProfile
    target_tokenizer: str
    measured_tokens: int
    compression_ratio: float
    patch_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.checkpoint_id, CheckpointId) or not isinstance(
            self.head_event_id, EventId
        ):
            raise TypeError("semantic checkpoint identity is invalid")
        if not isinstance(self.scope, MemoryScope) or self.scope.level is not ScopeLevel.TASK:
            raise ValueError("semantic checkpoints require exact task scope")
        if self.parent_checkpoint_id is not None and not isinstance(
            self.parent_checkpoint_id, CheckpointId
        ):
            raise TypeError("semantic checkpoint parent identity is invalid")
        if (
            isinstance(self.generation, bool)
            or not isinstance(self.generation, int)
            or self.generation < 1
        ):
            raise ValueError("semantic checkpoint generation is invalid")
        if self.generation == 1 and self.parent_checkpoint_id is not None:
            raise ValueError("initial semantic checkpoint cannot have a parent")
        if self.generation > 1 and self.parent_checkpoint_id is None:
            raise ValueError("later semantic checkpoint requires a parent")
        if self.schema_version != SEMANTIC_CHECKPOINT_SCHEMA_VERSION:
            raise ValueError("unsupported semantic checkpoint schema")
        if not isinstance(self.checkpoint_type, SemanticCheckpointType) or not isinstance(
            self.renderer_profile, SemanticRendererProfile
        ):
            raise TypeError("semantic checkpoint type or renderer is invalid")
        _text(self.target_tokenizer, "target tokenizer")
        if (
            isinstance(self.measured_tokens, bool)
            or not isinstance(self.measured_tokens, int)
            or self.measured_tokens < 0
        ):
            raise ValueError("semantic checkpoint token count is invalid")
        if (
            isinstance(self.compression_ratio, bool)
            or not isinstance(self.compression_ratio, (int, float))
            or not math.isfinite(self.compression_ratio)
            or self.compression_ratio < 0.0
        ):
            raise ValueError("semantic checkpoint compression ratio is invalid")
        if len(self.patch_digest) != 64 or any(
            character not in "0123456789abcdef" for character in self.patch_digest
        ):
            raise ValueError("semantic checkpoint patch digest is invalid")
        _require_aware(self.created_at, "created_at")
        expected = self.identity(
            self.scope,
            self.parent_checkpoint_id,
            self.generation,
            self.checkpoint_type,
            self.head_event_id,
            self.patch_digest,
        )
        if self.checkpoint_id != expected:
            raise ValueError("semantic checkpoint identity is not deterministic")
        object.__setattr__(self, "compression_ratio", float(self.compression_ratio))

    @staticmethod
    def identity(
        scope: MemoryScope,
        parent_checkpoint_id: CheckpointId | None,
        generation: int,
        checkpoint_type: SemanticCheckpointType,
        head_event_id: EventId,
        patch_digest: str,
    ) -> CheckpointId:
        material = ":".join(
            (
                _scope_digest(scope),
                "root" if parent_checkpoint_id is None else str(parent_checkpoint_id),
                str(generation),
                checkpoint_type.value,
                str(head_event_id),
                patch_digest,
            )
        )
        return CheckpointId(uuid5(_CHECKPOINT_NAMESPACE, material))

    @classmethod
    def create(
        cls,
        *,
        scope: MemoryScope,
        parent_checkpoint_id: CheckpointId | None,
        generation: int,
        checkpoint_type: SemanticCheckpointType,
        head_event_id: EventId,
        created_at: datetime,
        renderer_profile: SemanticRendererProfile,
        target_tokenizer: str,
        measured_tokens: int,
        compression_ratio: float,
        patch_digest: str,
    ) -> Self:
        return cls(
            cls.identity(
                scope,
                parent_checkpoint_id,
                generation,
                checkpoint_type,
                head_event_id,
                patch_digest,
            ),
            scope,
            parent_checkpoint_id,
            generation,
            SEMANTIC_CHECKPOINT_SCHEMA_VERSION,
            checkpoint_type,
            head_event_id,
            created_at,
            renderer_profile,
            target_tokenizer,
            measured_tokens,
            compression_ratio,
            patch_digest,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "checkpoint_id": str(self.checkpoint_id),
            "scope": self.scope.to_dict(),
            "parent_checkpoint_id": (
                None if self.parent_checkpoint_id is None else str(self.parent_checkpoint_id)
            ),
            "generation": self.generation,
            "schema_version": self.schema_version,
            "checkpoint_type": self.checkpoint_type.value,
            "head_event_id": str(self.head_event_id),
            "created_at": self.created_at.isoformat(),
            "renderer_profile": self.renderer_profile.value,
            "target_tokenizer": self.target_tokenizer,
            "measured_tokens": self.measured_tokens,
            "compression_ratio": self.compression_ratio,
            "patch_digest": self.patch_digest,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        expected = {
            "checkpoint_id",
            "scope",
            "parent_checkpoint_id",
            "generation",
            "schema_version",
            "checkpoint_type",
            "head_event_id",
            "created_at",
            "renderer_profile",
            "target_tokenizer",
            "measured_tokens",
            "compression_ratio",
            "patch_digest",
        }
        if set(value) != expected or not isinstance(value["scope"], Mapping):
            raise ValueError("semantic checkpoint fields are invalid")
        parent = value["parent_checkpoint_id"]
        generation = value["generation"]
        measured = value["measured_tokens"]
        ratio = value["compression_ratio"]
        if parent is not None and not isinstance(parent, str):
            raise TypeError("semantic checkpoint parent is invalid")
        if isinstance(generation, bool) or not isinstance(generation, int):
            raise TypeError("semantic checkpoint generation is invalid")
        if isinstance(measured, bool) or not isinstance(measured, int):
            raise TypeError("semantic checkpoint token count is invalid")
        if isinstance(ratio, bool) or not isinstance(ratio, (int, float)):
            raise TypeError("semantic checkpoint compression ratio is invalid")
        return cls(
            CheckpointId.from_string(str(value["checkpoint_id"])),
            MemoryScope.from_dict(value["scope"]),
            None if parent is None else CheckpointId.from_string(parent),
            generation,
            str(value["schema_version"]),
            SemanticCheckpointType(str(value["checkpoint_type"])),
            EventId.from_string(str(value["head_event_id"])),
            _parse_datetime(value["created_at"], "created_at"),
            SemanticRendererProfile(str(value["renderer_profile"])),
            str(value["target_tokenizer"]),
            measured,
            float(ratio),
            str(value["patch_digest"]),
        )


@dataclass(frozen=True, slots=True)
class MaterializedSemanticCheckpoint:
    checkpoint: SemanticCheckpoint
    atoms: tuple[SemanticMemoryAtom, ...]
    references: tuple[SemanticCheckpointAtom, ...]

    def __post_init__(self) -> None:
        atoms = tuple(self.atoms)
        references = tuple(self.references)
        if len({item.atom_id for item in atoms}) != len(atoms):
            raise ValueError("materialized semantic checkpoint contains duplicate atoms")
        if {item.atom_id for item in atoms} != {item.atom_id for item in references}:
            raise ValueError("materialized semantic checkpoint references do not match atoms")
        if any(
            item.scope != self.checkpoint.scope or item.status is not SemanticAtomStatus.ACTIVE
            for item in atoms
        ):
            raise ValueError("materialized checkpoint contains invalid active atoms")
        object.__setattr__(self, "atoms", atoms)
        object.__setattr__(self, "references", references)


class MemoryCompiler(Protocol):
    @property
    def compiler_version(self) -> str: ...

    def compile(
        self,
        scope: MemoryScope,
        events: tuple[TaskActivityEvent, ...],
        active_atoms: tuple[SemanticMemoryAtom, ...],
        *,
        base_checkpoint_id: CheckpointId | None,
    ) -> SemanticCheckpointPatch: ...


def apply_semantic_checkpoint_patch(
    *,
    scope: MemoryScope,
    ledger_atoms: tuple[SemanticMemoryAtom, ...],
    active_references: tuple[SemanticCheckpointAtom, ...],
    patch: SemanticCheckpointPatch,
    available_event_ids: frozenset[EventId],
    applied_at: datetime,
) -> tuple[tuple[SemanticMemoryAtom, ...], tuple[SemanticCheckpointAtom, ...]]:
    """Validate and deterministically apply one patch without partial state."""

    if not isinstance(scope, MemoryScope) or scope.level is not ScopeLevel.TASK:
        raise ValueError("semantic patch application requires exact task scope")
    _require_aware(applied_at, "applied_at")
    ledger = {item.atom_id: item for item in ledger_atoms}
    if len(ledger) != len(ledger_atoms) or any(item.scope != scope for item in ledger_atoms):
        raise ValueError("semantic ledger state is invalid for scope")
    active = {item.atom_id: item for item in active_references}
    if len(active) != len(active_references) or not set(active).issubset(ledger):
        raise ValueError("semantic active state is invalid")

    def validate_atom(atom: SemanticMemoryAtom) -> None:
        if atom.scope != scope or not set(atom.source_event_ids).issubset(available_event_ids):
            raise ValueError("semantic atom scope or evidence reference is invalid")

    def store_atom(atom: SemanticMemoryAtom) -> None:
        validate_atom(atom)
        existing = ledger.get(atom.atom_id)
        if existing is not None and existing != atom:
            raise ValueError("semantic atom identity conflicts")
        ledger[atom.atom_id] = atom

    for operation in patch.operations:
        atom = operation.atom
        target_id = operation.target_atom_id
        if operation.kind is SemanticPatchOperationKind.ADD:
            assert atom is not None
            store_atom(atom)
        elif operation.kind is SemanticPatchOperationKind.UPDATE_METADATA:
            assert atom is not None and target_id is not None
            current = ledger.get(target_id)
            if current is None or atom.atom_id != target_id:
                raise ValueError("semantic metadata update target is unavailable")
            core = (
                "scope",
                "kind",
                "subject",
                "predicate",
                "object_value",
                "source_event_ids",
                "supersedes_atom_id",
                "created_at",
            )
            if any(getattr(current, field) != getattr(atom, field) for field in core):
                raise ValueError("semantic metadata update changed canonical meaning")
            validate_atom(atom)
            ledger[target_id] = atom
            prior_reference = active.get(target_id)
            if prior_reference is not None:
                active[target_id] = replace(
                    prior_reference,
                    checkpoint_priority=atom.priority,
                )
        elif operation.kind is SemanticPatchOperationKind.SUPERSEDE:
            assert atom is not None and target_id is not None
            current = ledger.get(target_id)
            if current is None or atom.supersedes_atom_id != target_id:
                raise ValueError("semantic supersession target is unavailable")
            store_atom(atom)
            if current.status is not SemanticAtomStatus.SUPERSEDED:
                ledger[target_id] = current.with_status(
                    SemanticAtomStatus.SUPERSEDED, updated_at=applied_at
                )
            active.pop(target_id, None)
            active[atom.atom_id] = SemanticCheckpointAtom(
                atom.atom_id,
                operation.inclusion_reason or "superseding_active_meaning",
                operation.checkpoint_priority
                if operation.checkpoint_priority is not None
                else atom.priority,
            )
        elif operation.kind in {
            SemanticPatchOperationKind.RESOLVE,
            SemanticPatchOperationKind.EXPIRE,
        }:
            assert target_id is not None
            current = ledger.get(target_id)
            if current is None:
                raise ValueError("semantic lifecycle target is unavailable")
            status = (
                SemanticAtomStatus.RESOLVED
                if operation.kind is SemanticPatchOperationKind.RESOLVE
                else SemanticAtomStatus.EXPIRED
            )
            if current.status is not status:
                ledger[target_id] = current.with_status(status, updated_at=applied_at)
            active.pop(target_id, None)
        elif operation.kind is SemanticPatchOperationKind.ACTIVATE:
            assert target_id is not None
            current = ledger.get(target_id)
            if current is None or current.status is not SemanticAtomStatus.ACTIVE:
                raise ValueError("only an active semantic atom may enter a checkpoint")
            active[target_id] = SemanticCheckpointAtom(
                target_id,
                operation.inclusion_reason or "active_semantic_state",
                operation.checkpoint_priority
                if operation.checkpoint_priority is not None
                else current.priority,
            )
        elif operation.kind is SemanticPatchOperationKind.REMOVE:
            assert target_id is not None
            if target_id not in ledger:
                raise ValueError("semantic checkpoint removal target is unavailable")
            active.pop(target_id, None)
        else:  # pragma: no cover - closed enum defensive branch
            raise AssertionError("unsupported semantic patch operation")

    ordered_ledger = tuple(sorted(ledger.values(), key=lambda item: str(item.atom_id)))
    ordered_active = tuple(
        sorted(
            active.values(),
            key=lambda item: (-item.checkpoint_priority, str(item.atom_id)),
        )
    )
    return ordered_ledger, ordered_active
