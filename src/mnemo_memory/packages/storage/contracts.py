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
    CurrentKnowledgeDocumentSection,
    EventId,
    EvidenceReference,
    KnowledgeDocumentId,
    KnowledgeDocumentRevision,
    KnowledgeDocumentRevisionId,
    KnowledgeDocumentSectionMatch,
    KnowledgeDocumentTombstone,
    KnowledgeSectionEmbedding,
    KnownKnowledgeDocument,
    MemoryScope,
    knowledge_search_tokens,
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


class KnowledgeDocumentRepositoryError(Exception):
    """Expected storage-independent local-knowledge outcome."""


class KnowledgeDocumentNotFound(KnowledgeDocumentRepositoryError):
    pass


class KnowledgeDocumentConflict(KnowledgeDocumentRepositoryError):
    pass


class InvalidKnowledgeDocumentScope(KnowledgeDocumentRepositoryError):
    pass


class KnowledgeDocumentSecretRejected(KnowledgeDocumentRepositoryError):
    pass


class KnowledgeDocumentStorageFailure(KnowledgeDocumentRepositoryError):
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


@dataclass(frozen=True, slots=True)
class KnowledgeDocumentSyncStoreResult:
    """The active metadata remaining after one atomic scoped synchronization."""

    active_documents: tuple[KnownKnowledgeDocument, ...]
    applied_revision_count: int
    applied_tombstone_count: int


class KnowledgeDocumentRepository(Protocol):
    """Scoped durable storage for immutable, policy-approved local knowledge revisions."""

    def list_active_documents(self, scope: MemoryScope) -> tuple[KnownKnowledgeDocument, ...]: ...

    def get_current_revision(
        self, scope: MemoryScope, document_id: KnowledgeDocumentId
    ) -> KnowledgeDocumentRevision: ...

    def get_current_revision_by_path(
        self, scope: MemoryScope, relative_path: str
    ) -> KnowledgeDocumentRevision:
        """Resolve one current document by its exact safe path inside the supplied scope."""

    def get_revision(
        self,
        scope: MemoryScope,
        document_id: KnowledgeDocumentId,
        revision_id: KnowledgeDocumentRevisionId,
    ) -> KnowledgeDocumentRevision:
        """Return one scoped retained historical revision, never an unscoped revision lookup."""

    def search_current_sections(
        self,
        scope: MemoryScope,
        terms: tuple[str, ...],
        limit: int,
        maximum_documents: int,
    ) -> tuple[KnowledgeDocumentSectionMatch, ...]:
        """Rank current scoped sections only; terms must already be bounded and normalized."""

    def iter_current_sections(
        self, scope: MemoryScope, maximum_documents: int
    ) -> tuple[CurrentKnowledgeDocumentSection, ...]:
        """Return bounded current sections only, in stable source/section order."""

    def list_current_section_embeddings(
        self, scope: MemoryScope, model_id: str, maximum_documents: int
    ) -> tuple[KnowledgeSectionEmbedding, ...]:
        """Return only embeddings whose revision remains current in one complete scope."""

    def store_section_embeddings(
        self, scope: MemoryScope, embeddings: tuple[KnowledgeSectionEmbedding, ...]
    ) -> None:
        """Atomically store validated rebuildable projections for currently active sections."""

    def apply_sync(
        self,
        scope: MemoryScope,
        revisions: tuple[KnowledgeDocumentRevision, ...],
        tombstones: tuple[KnowledgeDocumentTombstone, ...],
    ) -> KnowledgeDocumentSyncStoreResult:
        """Apply an all-or-nothing source reconciliation; deletions erase document payload rows."""
        ...


def validate_knowledge_search(terms: tuple[str, ...], limit: int, maximum_documents: int) -> None:
    """Validate a storage-neutral bounded lexical query without interpreting document text."""
    if (
        not terms
        or len(terms) > 24
        or any(not isinstance(term, str) or not term or len(term) > 64 for term in terms)
    ):
        raise KnowledgeDocumentConflict("knowledge search terms are invalid")
    if not 1 <= limit <= 24 or not 1 <= maximum_documents <= 128:
        raise KnowledgeDocumentConflict("knowledge search limits are invalid")


def rank_knowledge_sections(
    revisions: tuple[KnowledgeDocumentRevision, ...], terms: tuple[str, ...], limit: int
) -> tuple[KnowledgeDocumentSectionMatch, ...]:
    """Rank literal terms over current scoped revisions with stable, reproducible ordering."""
    matches: list[KnowledgeDocumentSectionMatch] = []
    for revision in revisions:
        for index, section in enumerate(revision.document.sections):
            heading = knowledge_search_tokens(section.heading)
            content = knowledge_search_tokens(section.content)
            score = sum(4 * heading.count(term) + content.count(term) for term in terms)
            if score:
                matches.append(KnowledgeDocumentSectionMatch(revision, index, section, score))
    return tuple(
        sorted(
            matches,
            key=lambda match: (
                -match.score,
                # A checked-in project note is the nearer project source on an exact lexical tie.
                # This is only ordering: it never makes either untrusted note authoritative.
                0 if match.revision.document.source_kind.value == "markdown" else 1,
                match.revision.document.relative_path,
                str(match.revision.revision_id),
                match.section_index,
            ),
        )[:limit]
    )


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

    def find_nodes_by_original_file_path(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, original_file_path: str
    ) -> tuple[DbtManifestNode, ...]:
        """Return exact scoped manifest matches; callers must reject ambiguity."""
        ...

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

    def get_file(
        self, scope: MemoryScope, snapshot_id: CodeSnapshotId, relative_path: str
    ) -> CodeFile | None:
        """Return one exact scoped file projection without exposing other paths."""
        ...

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
