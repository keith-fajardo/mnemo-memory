"""Storage-neutral repository contracts for Mnemo's existing durable domain types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from packages.domain import (
    Checkpoint,
    CheckpointAggregate,
    CheckpointId,
    CheckpointRevision,
    EvidenceId,
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


@dataclass(frozen=True, slots=True)
class CheckpointPage:
    items: tuple[CheckpointAggregate, ...]
    next_offset: int | None


class CheckpointRepository(Protocol):
    # Legacy methods remain until 10A.3c migrates current consumers.
    def create_evidence(self, evidence: EvidenceReference) -> None: ...

    def get_evidence(self, evidence_id: EvidenceId) -> EvidenceReference | None: ...

    def create_checkpoint(self, checkpoint: Checkpoint) -> None: ...

    def get_checkpoint(
        self, checkpoint_id: CheckpointId, scope: MemoryScope
    ) -> Checkpoint | None: ...

    def get_current_checkpoint(self, scope: MemoryScope) -> Checkpoint | None: ...

    def list_checkpoint_history(
        self, checkpoint_id: CheckpointId, scope: MemoryScope
    ) -> tuple[Checkpoint, ...]: ...

    def get_aggregate(
        self, scope: MemoryScope, checkpoint_id: CheckpointId
    ) -> CheckpointAggregate: ...

    def get_current_revision(
        self, scope: MemoryScope, checkpoint_id: CheckpointId
    ) -> CheckpointRevision: ...
