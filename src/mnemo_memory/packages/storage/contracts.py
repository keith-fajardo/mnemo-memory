"""Storage-neutral repository contracts for Mnemo's existing durable domain types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from mnemo_memory.packages.domain import (
    ApprovedEpisodicEvent,
    CheckpointAggregate,
    CheckpointContent,
    CheckpointEventKind,
    CheckpointId,
    CheckpointLifecycleEvent,
    CheckpointRevision,
    CheckpointRevisionId,
    CheckpointSourceObservation,
    CodeEdge,
    CodeFile,
    CodeSnapshot,
    CodeSnapshotId,
    CodeStructureArtifact,
    CodeSymbol,
    CodeSymbolId,
    EventId,
    EvidenceReference,
    MemoryScope,
)
from mnemo_memory.packages.domain.dbt_manifest import (
    DbtLineageEdge,
    DbtManifestArtifact,
    DbtManifestNode,
    DbtManifestSnapshot,
    DbtNodeId,
)
from mnemo_memory.packages.domain.identifiers import DbtSnapshotId


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


class EpisodicEventRepositoryError(Exception):
    """Expected storage-independent episodic-event outcome."""


class EpisodicEventNotFound(EpisodicEventRepositoryError):
    pass


class InvalidEpisodicEventScope(EpisodicEventRepositoryError):
    pass


class EpisodicEventStorageFailure(EpisodicEventRepositoryError):
    pass


class ApprovedEpisodicEventRepositoryError(Exception):
    """Expected storage-independent approved-episodic-event outcome."""


class ApprovedEpisodicEventNotFound(ApprovedEpisodicEventRepositoryError):
    pass


class ApprovedEpisodicEventConflict(ApprovedEpisodicEventRepositoryError):
    pass


class InvalidApprovedEpisodicEventScope(ApprovedEpisodicEventRepositoryError):
    pass


class ApprovedEpisodicEventStorageFailure(ApprovedEpisodicEventRepositoryError):
    pass


@dataclass(frozen=True, slots=True)
class ApprovedEpisodicEventStoreResult:
    event: ApprovedEpisodicEvent
    idempotent: bool


@dataclass(frozen=True, slots=True)
class ApprovedEpisodicEventPage:
    items: tuple[ApprovedEpisodicEvent, ...]
    next_offset: int | None


class ApprovedEpisodicEventRepository(Protocol):
    """Explicit task-scoped decision/failure/tool-outcome facts."""

    def append_approved_event(
        self, event: ApprovedEpisodicEvent
    ) -> ApprovedEpisodicEventStoreResult: ...

    def get_approved_event(
        self, scope: MemoryScope, event_id: EventId
    ) -> ApprovedEpisodicEvent: ...

    def list_approved_events(
        self, scope: MemoryScope, *, offset: int = 0, limit: int = 50
    ) -> ApprovedEpisodicEventPage: ...


class ProjectIndexRepositoryError(Exception):
    """Expected storage-independent project-index outcome."""


class ManifestSnapshotNotFound(ProjectIndexRepositoryError):
    pass


class ManifestNodeNotFound(ProjectIndexRepositoryError):
    pass


class ActiveSnapshotConflict(ProjectIndexRepositoryError):
    pass


class InvalidManifestSnapshotScope(ProjectIndexRepositoryError):
    pass


class InvalidManifestGraph(ProjectIndexRepositoryError):
    pass


class ProjectIndexStorageFailure(ProjectIndexRepositoryError):
    pass


class SourceSnapshotNotFound(ProjectIndexRepositoryError):
    pass


class SourceIndexStorageFailure(ProjectIndexRepositoryError):
    pass


class CheckpointSourceObservationNotFound(ProjectIndexRepositoryError):
    pass


class CheckpointSourceObservationConflict(ProjectIndexRepositoryError):
    pass


class CheckpointSourceObservationStorageFailure(ProjectIndexRepositoryError):
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
        event_kind: CheckpointEventKind = CheckpointEventKind.REVISED,
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


@dataclass(frozen=True, slots=True)
class EpisodicEventStoreResult:
    event: CheckpointLifecycleEvent
    idempotent: bool


@dataclass(frozen=True, slots=True)
class EpisodicEventPage:
    items: tuple[CheckpointLifecycleEvent, ...]
    next_offset: int | None


class CheckpointLifecycleEventRepository(Protocol):
    """Scoped append-only storage for evidence-bearing checkpoint lifecycle facts."""

    def append_event(self, event: CheckpointLifecycleEvent) -> EpisodicEventStoreResult: ...

    def get_event(self, scope: MemoryScope, event_id: EventId) -> CheckpointLifecycleEvent: ...

    def list_events(
        self,
        scope: MemoryScope,
        *,
        checkpoint_id: CheckpointId | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> EpisodicEventPage: ...


@dataclass(frozen=True, slots=True)
class ManifestSnapshotStoreResult:
    snapshot: DbtManifestSnapshot
    idempotent: bool


@dataclass(frozen=True, slots=True)
class ManifestSnapshotPage:
    items: tuple[DbtManifestSnapshot, ...]
    next_offset: int | None


@dataclass(frozen=True, slots=True)
class SourceSnapshotStoreResult:
    snapshot: CodeSnapshot
    idempotent: bool


class ProjectIndexRepository(Protocol):
    def store_and_activate(
        self,
        artifact: DbtManifestArtifact,
        snapshot_id: DbtSnapshotId,
        *,
        expected_active_snapshot_id: DbtSnapshotId | None = None,
    ) -> ManifestSnapshotStoreResult: ...

    def get_snapshot(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId
    ) -> DbtManifestSnapshot: ...

    def get_active_snapshot(self, scope: MemoryScope) -> DbtManifestSnapshot | None: ...

    def get_node(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, unique_id: DbtNodeId
    ) -> DbtManifestNode: ...

    def iter_nodes(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId
    ) -> tuple[DbtManifestNode, ...]: ...

    def iter_edges(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId
    ) -> tuple[DbtLineageEdge, ...]: ...

    def direct_upstream(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, unique_id: DbtNodeId
    ) -> tuple[DbtLineageEdge, ...]: ...

    def direct_downstream(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, unique_id: DbtNodeId
    ) -> tuple[DbtLineageEdge, ...]: ...

    def get_nodes(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, unique_ids: tuple[DbtNodeId, ...]
    ) -> tuple[DbtManifestNode, ...]: ...

    def get_upstream_edges(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, child_ids: tuple[DbtNodeId, ...]
    ) -> tuple[DbtLineageEdge, ...]: ...

    def get_downstream_edges(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, parent_ids: tuple[DbtNodeId, ...]
    ) -> tuple[DbtLineageEdge, ...]: ...

    def list_snapshots(
        self, scope: MemoryScope, *, offset: int = 0, limit: int = 50
    ) -> ManifestSnapshotPage: ...


class SourceStructureRepository(Protocol):
    """Scoped durable storage for rebuildable, static source projections."""

    def store_and_activate(self, artifact: CodeStructureArtifact) -> SourceSnapshotStoreResult: ...

    def get_active_snapshot(self, scope: MemoryScope) -> CodeSnapshot | None: ...

    def get_snapshot(self, scope: MemoryScope, snapshot_id: CodeSnapshotId) -> CodeSnapshot: ...

    def latest_transition(self, scope: MemoryScope) -> tuple[CodeSnapshot, CodeSnapshot] | None:
        """Return the last scoped activation transition as ``(before, after)``.

        Snapshot UUIDs are identities, not clocks.  Implementations therefore retain
        activation history explicitly instead of deriving a previous snapshot from an
        arbitrary identifier order.
        """
        ...

    def list_activation_history(
        self, scope: MemoryScope, *, limit: int = 20
    ) -> tuple[CodeSnapshot, ...]:
        """List most-recent-first scoped activations, retaining immutable snapshots."""
        ...

    def iter_symbols(
        self, scope: MemoryScope, snapshot_id: CodeSnapshotId
    ) -> tuple[CodeSymbol, ...]: ...

    def iter_files(
        self, scope: MemoryScope, snapshot_id: CodeSnapshotId
    ) -> tuple[CodeFile, ...]: ...

    def iter_edges(
        self, scope: MemoryScope, snapshot_id: CodeSnapshotId
    ) -> tuple[CodeEdge, ...]: ...

    def find_symbols(
        self, scope: MemoryScope, snapshot_id: CodeSnapshotId, query: str, *, limit: int
    ) -> tuple[CodeSymbol, ...]: ...

    def module_symbols_for_paths(
        self, scope: MemoryScope, snapshot_id: CodeSnapshotId, relative_paths: tuple[str, ...]
    ) -> tuple[CodeSymbol, ...]: ...

    def symbols_by_ids(
        self, scope: MemoryScope, snapshot_id: CodeSnapshotId, symbol_ids: tuple[CodeSymbolId, ...]
    ) -> tuple[CodeSymbol, ...]: ...

    def edges_from_symbols(
        self, scope: MemoryScope, snapshot_id: CodeSnapshotId, symbol_ids: tuple[CodeSymbolId, ...]
    ) -> tuple[CodeEdge, ...]: ...

    def edges_to_symbols(
        self, scope: MemoryScope, snapshot_id: CodeSnapshotId, symbol_ids: tuple[CodeSymbolId, ...]
    ) -> tuple[CodeEdge, ...]: ...


@dataclass(frozen=True, slots=True)
class CheckpointSourceObservationStoreResult:
    observation: CheckpointSourceObservation
    idempotent: bool


class CheckpointSourceObservationRepository(Protocol):
    """Scoped immutable links between a checkpoint revision and a source snapshot."""

    def append_checkpoint_source_observation(
        self, observation: CheckpointSourceObservation
    ) -> CheckpointSourceObservationStoreResult: ...

    def get_checkpoint_source_observation(
        self, scope: MemoryScope, checkpoint_id: CheckpointId, revision_id: CheckpointRevisionId
    ) -> CheckpointSourceObservation: ...
