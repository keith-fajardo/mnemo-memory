"""Storage-neutral repository contracts for Mnemo's existing durable domain types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from packages.domain import (
    CheckpointAggregate,
    CheckpointContent,
    CheckpointId,
    CheckpointRevision,
    CheckpointRevisionId,
    EvidenceReference,
    MemoryScope,
)


class CheckpointRepositoryError(Exception):
    """Expected storage-independent checkpoint outcome."""


class CheckpointNotFound(CheckpointRepositoryError):
    pass


class DuplicateCheckpoint(CheckpointRepositoryError):
    pass


class RevisionConflict(CheckpointRepositoryError):
    pass


class InvalidLifecycleTransition(CheckpointRepositoryError):
    pass


class InvalidAbandonmentReason(CheckpointRepositoryError):
    pass


class InvalidCheckpointScope(CheckpointRepositoryError):
    pass


class RepositoryStorageFailure(CheckpointRepositoryError):
    pass


@dataclass(frozen=True, slots=True)
class CheckpointPage:
    items: tuple[CheckpointAggregate, ...]
    next_offset: int | None


class CheckpointRepository(Protocol):
    def get_aggregate(
        self, scope: MemoryScope, checkpoint_id: CheckpointId
    ) -> CheckpointAggregate: ...

    def get_current_revision(
        self, scope: MemoryScope, checkpoint_id: CheckpointId
    ) -> CheckpointRevision: ...

    def create_checkpoint_aggregate(
        self, aggregate: CheckpointAggregate, initial_revision: CheckpointRevision
    ) -> None: ...

    def get_revision(
        self,
        scope: MemoryScope,
        checkpoint_id: CheckpointId,
        *,
        revision_number: int | None = None,
        revision_id: CheckpointRevisionId | None = None,
    ) -> CheckpointRevision: ...

    def append_revision(
        self,
        scope: MemoryScope,
        checkpoint_id: CheckpointId,
        expected_revision_id: CheckpointRevisionId,
        content: CheckpointContent,
        evidence_references: tuple[EvidenceReference, ...],
        created_at: datetime,
    ) -> CheckpointRevision: ...

    def complete_checkpoint(
        self,
        scope: MemoryScope,
        checkpoint_id: CheckpointId,
        expected_revision_id: CheckpointRevisionId,
        content: CheckpointContent,
        evidence_references: tuple[EvidenceReference, ...],
        created_at: datetime,
    ) -> CheckpointRevision: ...

    def abandon_checkpoint(
        self,
        scope: MemoryScope,
        checkpoint_id: CheckpointId,
        expected_revision_id: CheckpointRevisionId,
        reason: str,
        content: CheckpointContent,
        evidence_references: tuple[EvidenceReference, ...],
        created_at: datetime,
    ) -> CheckpointRevision: ...

    def list_current_checkpoints(
        self, scope: MemoryScope, *, offset: int = 0, limit: int = 50
    ) -> CheckpointPage: ...

    def select_current_checkpoint(self, scope: MemoryScope) -> CheckpointAggregate | None: ...
