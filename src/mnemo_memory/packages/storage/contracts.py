"""Storage-neutral repository contracts for Mnemo's existing durable domain types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from mnemo_memory.packages.domain import (
    ActiveEpisodicMemory,
    ApprovedEpisodicEvent,
    ApprovedEpisodicEventGovernance,
    ApprovedEpisodicEventPinAction,
    ApprovedEventExportBundle,
    ApprovedEventLifecycleStatus,
    CheckpointAggregate,
    CheckpointContent,
    CheckpointEventKind,
    CheckpointExportBundle,
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
    DbtCatalogArtifact,
    DbtRunResultsArtifact,
    DbtSourceFreshnessArtifact,
    EpisodicCandidateReviewAction,
    EpisodicExportBundle,
    EpisodicMemoryCandidate,
    EpisodicMemoryDeletion,
    EpisodicMemoryExpiration,
    EpisodicMemoryGovernanceAction,
    EpisodicMemoryPurge,
    EpisodicMemoryRetentionTarget,
    EpisodicMemoryRevision,
    EventId,
    EventOutboxJob,
    EvidenceReference,
    KnowledgeDocumentId,
    KnowledgeDocumentRevision,
    KnowledgeDocumentRevisionId,
    KnowledgeDocumentSectionMatch,
    KnowledgeDocumentTombstone,
    KnowledgeSectionEmbedding,
    KnownKnowledgeDocument,
    MemoryId,
    MemoryScope,
    OutboxJobId,
    ProjectAgent,
    ProjectClientProfile,
    ProjectProcedure,
    ProjectSkill,
    TaskActivityEvent,
    TaskActivityEventDeletion,
    TaskActivityEventExpiration,
    TaskActivityEventPurge,
    TaskActivityEventRetentionTarget,
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


class EventOutboxRepositoryError(Exception):
    """Safe storage-neutral outcome for minimal event-delivery metadata."""


class EventOutboxNotFound(EventOutboxRepositoryError):
    pass


class EventOutboxLeaseConflict(EventOutboxRepositoryError):
    pass


class EventOutboxStorageFailure(EventOutboxRepositoryError):
    pass


@dataclass(frozen=True, slots=True)
class EventOutboxProjectStatus:
    pending: int
    processing: int
    failed: int

    def __post_init__(self) -> None:
        if min(self.pending, self.processing, self.failed) < 0:
            raise ValueError("event outbox status counts cannot be negative")


class EventOutboxRepository(Protocol):
    def get_project_event_job_status(
        self, scope: MemoryScope, *, now: datetime
    ) -> EventOutboxProjectStatus: ...

    def requeue_failed_project_event_jobs(
        self, scope: MemoryScope, *, requested_at: datetime, limit: int
    ) -> int: ...

    def claim_event_jobs(
        self,
        scope: MemoryScope,
        *,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
        limit: int,
    ) -> tuple[EventOutboxJob, ...]: ...

    def complete_event_job(
        self,
        scope: MemoryScope,
        job_id: OutboxJobId,
        *,
        worker_id: str,
        completed_at: datetime,
    ) -> EventOutboxJob: ...

    def retry_event_job(
        self,
        scope: MemoryScope,
        job_id: OutboxJobId,
        *,
        worker_id: str,
        now: datetime,
        available_at: datetime,
        failure_code: str,
    ) -> EventOutboxJob: ...

    def get_event_job(self, scope: MemoryScope, job_id: OutboxJobId) -> EventOutboxJob: ...


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


class ApprovedEpisodicEventSecretRejected(ApprovedEpisodicEventRepositoryError):
    pass


class ApprovedEventExportRepositoryError(ApprovedEpisodicEventRepositoryError):
    pass


class ApprovedEventImportRepositoryError(ApprovedEpisodicEventRepositoryError):
    pass


class ApprovedEventImportConflict(ApprovedEventImportRepositoryError):
    pass


@dataclass(frozen=True, slots=True)
class ApprovedEventImportResult:
    event_count: int
    governance_count: int
    pin_action_count: int
    idempotent: bool

    def __post_init__(self) -> None:
        for value in (self.event_count, self.governance_count, self.pin_action_count):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError("approved event import counts must be non-negative integers")


class ApprovedEventExportRepository(Protocol):
    def export_approved_event_history(
        self, scope: MemoryScope, *, exported_at: datetime
    ) -> ApprovedEventExportBundle: ...


class ApprovedEventImportRepository(Protocol):
    def import_approved_event_history(
        self,
        source: ApprovedEventExportBundle,
        target: ApprovedEventExportBundle,
    ) -> ApprovedEventImportResult: ...


class TaskActivityEventRepositoryError(Exception):
    """Expected storage-neutral outcome for minimized task activity events."""


class TaskActivityEventNotFound(TaskActivityEventRepositoryError):
    pass


class TaskActivityEventConflict(TaskActivityEventRepositoryError):
    pass


class InvalidTaskActivityEventScope(TaskActivityEventRepositoryError):
    pass


class TaskActivityEventRejected(TaskActivityEventRepositoryError):
    pass


class TaskActivityEventStorageFailure(TaskActivityEventRepositoryError):
    pass


class TaskActivityRetentionNotFound(TaskActivityEventRepositoryError):
    pass


class TaskActivityRetentionConflict(TaskActivityEventRepositoryError):
    pass


class TaskActivityRetentionStorageFailure(TaskActivityEventRepositoryError):
    pass


class EpisodicMemoryCandidateRepositoryError(Exception):
    """Expected storage-neutral outcome for inactive extracted candidates."""


class EpisodicMemoryCandidateNotFound(EpisodicMemoryCandidateRepositoryError):
    pass


class EpisodicMemoryCandidateConflict(EpisodicMemoryCandidateRepositoryError):
    pass


class InvalidEpisodicMemoryCandidateScope(EpisodicMemoryCandidateRepositoryError):
    pass


class EpisodicMemoryCandidateRejected(EpisodicMemoryCandidateRepositoryError):
    pass


class EpisodicMemoryCandidateStorageFailure(EpisodicMemoryCandidateRepositoryError):
    pass


class EpisodicMemoryReviewRepositoryError(Exception):
    """Expected outcome for explicit review and activation of one candidate."""


class EpisodicMemoryReviewNotFound(EpisodicMemoryReviewRepositoryError):
    pass


class EpisodicMemoryReviewConflict(EpisodicMemoryReviewRepositoryError):
    pass


class EpisodicMemoryReviewRejected(EpisodicMemoryReviewRepositoryError):
    pass


class EpisodicMemoryReviewStorageFailure(EpisodicMemoryReviewRepositoryError):
    pass


class ActiveEpisodicMemoryNotFound(EpisodicMemoryReviewRepositoryError):
    pass


class EpisodicMemoryGovernanceRepositoryError(Exception):
    """Expected outcome for an append-only active-memory revision action."""


class EpisodicMemoryGovernanceNotFound(EpisodicMemoryGovernanceRepositoryError):
    pass


class EpisodicMemoryGovernanceConflict(EpisodicMemoryGovernanceRepositoryError):
    pass


class EpisodicMemoryGovernanceRejected(EpisodicMemoryGovernanceRepositoryError):
    pass


class EpisodicMemoryGovernanceStorageFailure(EpisodicMemoryGovernanceRepositoryError):
    pass


class EpisodicMemoryRetentionRepositoryError(Exception):
    """Expected outcome for payload-free deterministic episodic expiration."""


class EpisodicMemoryExpirationNotFound(EpisodicMemoryRetentionRepositoryError):
    pass


class EpisodicMemoryExpirationConflict(EpisodicMemoryRetentionRepositoryError):
    pass


class EpisodicMemoryRetentionStorageFailure(EpisodicMemoryRetentionRepositoryError):
    pass


class EpisodicMemoryPurgeNotFound(EpisodicMemoryRetentionRepositoryError):
    pass


class EpisodicMemoryPurgeConflict(EpisodicMemoryRetentionRepositoryError):
    pass


class EpisodicMemoryPurgeStorageFailure(EpisodicMemoryRetentionRepositoryError):
    pass


class EpisodicDeletionRepositoryError(Exception):
    """Expected outcome for explicit payload-free episodic deletion."""


class EpisodicDeletionNotFound(EpisodicDeletionRepositoryError):
    pass


class EpisodicDeletionConflict(EpisodicDeletionRepositoryError):
    pass


class EpisodicDeletionStorageFailure(EpisodicDeletionRepositoryError):
    pass


class EpisodicExportRepositoryError(Exception):
    """Expected outcome for one exact-scope episodic export."""


class InvalidEpisodicExportScope(EpisodicExportRepositoryError):
    pass


class EpisodicExportStorageFailure(EpisodicExportRepositoryError):
    """Safe failure while reading one exact-scope episodic export."""


class EpisodicExportRepository(Protocol):
    def export_episodic_state(
        self, scope: MemoryScope, *, exported_at: datetime
    ) -> EpisodicExportBundle: ...


class EpisodicLifecycleImportRepositoryError(Exception):
    """Expected outcome for atomically retaining imported lifecycle tombstones."""


class EpisodicLifecycleImportConflict(EpisodicLifecycleImportRepositoryError):
    pass


class EpisodicLifecycleImportStorageFailure(EpisodicLifecycleImportRepositoryError):
    pass


@dataclass(frozen=True, slots=True)
class EpisodicLifecycleImportResult:
    imported_count: int
    idempotent: bool

    def __post_init__(self) -> None:
        if (
            isinstance(self.imported_count, bool)
            or not isinstance(self.imported_count, int)
            or self.imported_count < 1
        ):
            raise ValueError("episodic lifecycle import count must be positive")


class EpisodicLifecycleImportRepository(Protocol):
    def import_episodic_lifecycle(
        self,
        source: EpisodicExportBundle,
        target: EpisodicExportBundle,
    ) -> EpisodicLifecycleImportResult: ...


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
class TaskActivityEventStoreResult:
    event: TaskActivityEvent
    idempotent: bool


@dataclass(frozen=True, slots=True)
class TaskActivityEventPage:
    items: tuple[TaskActivityEvent, ...]
    next_offset: int | None


class TaskActivityEventRepository(Protocol):
    def append_task_activity_event(
        self, event: TaskActivityEvent
    ) -> TaskActivityEventStoreResult: ...

    def get_task_activity_event(
        self, scope: MemoryScope, event_id: EventId
    ) -> TaskActivityEvent: ...

    def list_task_activity_events(
        self, scope: MemoryScope, *, offset: int = 0, limit: int = 50
    ) -> TaskActivityEventPage: ...


@dataclass(frozen=True, slots=True)
class TaskActivityExpirationResult:
    expirations: tuple[TaskActivityEventExpiration, ...]
    idempotent: bool


@dataclass(frozen=True, slots=True)
class TaskActivityPurgeResult:
    purges: tuple[TaskActivityEventPurge, ...]
    idempotent: bool


class TaskActivityRetentionRepository(Protocol):
    def list_due_task_activity_retention(
        self, scope: MemoryScope, *, as_of: datetime
    ) -> tuple[TaskActivityEventRetentionTarget, ...]: ...

    def apply_task_activity_expirations(
        self, expirations: tuple[TaskActivityEventExpiration, ...]
    ) -> TaskActivityExpirationResult: ...

    def get_task_activity_expiration(
        self, scope: MemoryScope, event_id: EventId
    ) -> TaskActivityEventExpiration: ...

    def list_unpurged_task_activity_expirations(
        self, scope: MemoryScope
    ) -> tuple[TaskActivityEventExpiration, ...]: ...

    def apply_task_activity_purges(
        self, purges: tuple[TaskActivityEventPurge, ...]
    ) -> TaskActivityPurgeResult: ...

    def get_task_activity_purge(
        self, scope: MemoryScope, event_id: EventId
    ) -> TaskActivityEventPurge: ...


@dataclass(frozen=True, slots=True)
class EpisodicMemoryCandidateStoreResult:
    candidates: tuple[EpisodicMemoryCandidate, ...]
    idempotent: bool


@dataclass(frozen=True, slots=True)
class EpisodicMemoryCandidatePage:
    items: tuple[EpisodicMemoryCandidate, ...]
    next_offset: int | None


class EpisodicMemoryCandidateRepository(Protocol):
    def store_episodic_memory_candidates(
        self, candidates: tuple[EpisodicMemoryCandidate, ...]
    ) -> EpisodicMemoryCandidateStoreResult: ...

    def get_episodic_memory_candidate(
        self, scope: MemoryScope, memory_id: MemoryId
    ) -> EpisodicMemoryCandidate: ...

    def list_episodic_memory_candidates(
        self,
        scope: MemoryScope,
        *,
        source_event_id: EventId | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> EpisodicMemoryCandidatePage: ...


@dataclass(frozen=True, slots=True)
class EpisodicMemoryReviewResult:
    action: EpisodicCandidateReviewAction
    active_memory: ActiveEpisodicMemory | None
    idempotent: bool


@dataclass(frozen=True, slots=True)
class ActiveEpisodicMemoryPage:
    items: tuple[ActiveEpisodicMemory, ...]
    next_offset: int | None


class EpisodicMemoryReviewRepository(Protocol):
    def review_episodic_memory_candidate(
        self, action: EpisodicCandidateReviewAction
    ) -> EpisodicMemoryReviewResult: ...

    def get_episodic_memory_review(
        self, scope: MemoryScope, candidate_id: MemoryId
    ) -> EpisodicCandidateReviewAction: ...

    def get_active_episodic_memory(
        self, scope: MemoryScope, memory_id: MemoryId
    ) -> ActiveEpisodicMemory: ...

    def list_active_episodic_memories(
        self, scope: MemoryScope, *, offset: int = 0, limit: int = 50
    ) -> ActiveEpisodicMemoryPage: ...


@dataclass(frozen=True, slots=True)
class EpisodicMemoryGovernanceResult:
    action: EpisodicMemoryGovernanceAction
    current_revision: EpisodicMemoryRevision
    active_memory: ActiveEpisodicMemory | None
    idempotent: bool


class EpisodicMemoryGovernanceRepository(Protocol):
    def govern_episodic_memory(
        self, action: EpisodicMemoryGovernanceAction
    ) -> EpisodicMemoryGovernanceResult: ...

    def get_episodic_memory_governance(
        self, scope: MemoryScope, action_id: EventId
    ) -> EpisodicMemoryGovernanceAction: ...

    def list_episodic_memory_revisions(
        self, scope: MemoryScope, memory_id: MemoryId
    ) -> tuple[EpisodicMemoryRevision, ...]: ...


@dataclass(frozen=True, slots=True)
class EpisodicMemoryExpirationResult:
    expirations: tuple[EpisodicMemoryExpiration, ...]
    idempotent: bool


@dataclass(frozen=True, slots=True)
class EpisodicMemoryPurgeResult:
    purges: tuple[EpisodicMemoryPurge, ...]
    idempotent: bool


class EpisodicMemoryRetentionRepository(Protocol):
    def list_due_episodic_memory_retention(
        self, scope: MemoryScope, *, as_of: datetime
    ) -> tuple[EpisodicMemoryRetentionTarget, ...]: ...

    def apply_episodic_memory_expirations(
        self, expirations: tuple[EpisodicMemoryExpiration, ...]
    ) -> EpisodicMemoryExpirationResult: ...

    def get_episodic_memory_expiration(
        self, scope: MemoryScope, memory_id: MemoryId
    ) -> EpisodicMemoryExpiration: ...

    def list_unpurged_episodic_memory_expirations(
        self, scope: MemoryScope
    ) -> tuple[EpisodicMemoryExpiration, ...]: ...

    def apply_episodic_memory_purges(
        self, purges: tuple[EpisodicMemoryPurge, ...]
    ) -> EpisodicMemoryPurgeResult: ...

    def get_episodic_memory_purge(
        self, scope: MemoryScope, memory_id: MemoryId
    ) -> EpisodicMemoryPurge: ...


@dataclass(frozen=True, slots=True)
class EpisodicMemoryDeletionResult:
    deletion: EpisodicMemoryDeletion
    idempotent: bool


@dataclass(frozen=True, slots=True)
class TaskActivityDeletionResult:
    deletion: TaskActivityEventDeletion
    dependent_deletions: tuple[EpisodicMemoryDeletion, ...]
    idempotent: bool


class EpisodicDeletionRepository(Protocol):
    def delete_episodic_memory(
        self, deletion: EpisodicMemoryDeletion
    ) -> EpisodicMemoryDeletionResult: ...

    def delete_task_activity_event(
        self, deletion: TaskActivityEventDeletion
    ) -> TaskActivityDeletionResult: ...

    def get_episodic_memory_deletion(
        self, scope: MemoryScope, memory_id: MemoryId
    ) -> EpisodicMemoryDeletion: ...

    def get_task_activity_deletion(
        self, scope: MemoryScope, event_id: EventId
    ) -> TaskActivityEventDeletion: ...


@dataclass(frozen=True, slots=True)
class ApprovedEpisodicEventPage:
    items: tuple[ApprovedEpisodicEvent, ...]
    next_offset: int | None


@dataclass(frozen=True, slots=True)
class ApprovedEpisodicEventRecord:
    """Review state for an active, corrected, or payload-free retracted fact."""

    event_id: EventId
    scope: MemoryScope
    status: ApprovedEventLifecycleStatus
    event: ApprovedEpisodicEvent | None
    governance: ApprovedEpisodicEventGovernance | None
    pinned: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.pinned, bool):
            raise TypeError("approved event pin state must be a boolean")
        if self.status is ApprovedEventLifecycleStatus.ACTIVE:
            if self.event is None or self.governance is not None:
                raise ValueError("active approved event record is invalid")
        elif self.status is ApprovedEventLifecycleStatus.CORRECTED:
            if self.event is None or self.governance is None:
                raise ValueError("corrected approved event record is invalid")
        elif self.event is not None or self.governance is None:
            raise ValueError("retracted approved event record is invalid")
        if self.status is not ApprovedEventLifecycleStatus.ACTIVE and self.pinned:
            raise ValueError("only an active approved event may be pinned")
        if self.event is not None and (
            self.event.event_id != self.event_id or self.event.scope != self.scope
        ):
            raise ValueError("approved event record does not match its event")
        if self.governance is not None and (
            self.governance.target_event_id != self.event_id
            or self.governance.scope != self.scope
            or self.governance.kind.value != self.status.value
        ):
            raise ValueError("approved event record does not match its governance action")


@dataclass(frozen=True, slots=True)
class ApprovedEpisodicEventRecordPage:
    items: tuple[ApprovedEpisodicEventRecord, ...]
    next_offset: int | None


@dataclass(frozen=True, slots=True)
class ApprovedEpisodicEventGovernanceResult:
    target: ApprovedEpisodicEventRecord
    replacement: ApprovedEpisodicEventRecord | None
    idempotent: bool


@dataclass(frozen=True, slots=True)
class ApprovedEpisodicEventPinResult:
    action: ApprovedEpisodicEventPinAction
    record: ApprovedEpisodicEventRecord
    idempotent: bool


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

    def correct_approved_event(
        self,
        replacement: ApprovedEpisodicEvent,
        governance: ApprovedEpisodicEventGovernance,
    ) -> ApprovedEpisodicEventGovernanceResult: ...

    def retract_approved_event(
        self, governance: ApprovedEpisodicEventGovernance
    ) -> ApprovedEpisodicEventGovernanceResult: ...

    def get_approved_event_record(
        self, scope: MemoryScope, event_id: EventId
    ) -> ApprovedEpisodicEventRecord: ...

    def list_approved_event_records(
        self, scope: MemoryScope, *, offset: int = 0, limit: int = 50
    ) -> ApprovedEpisodicEventRecordPage: ...

    def set_approved_event_pin(
        self, action: ApprovedEpisodicEventPinAction
    ) -> ApprovedEpisodicEventPinResult: ...


@dataclass(frozen=True, slots=True)
class KnowledgeDocumentSyncStoreResult:
    """The active metadata remaining after one atomic scoped synchronization."""

    active_documents: tuple[KnownKnowledgeDocument, ...]
    applied_revision_count: int
    applied_tombstone_count: int


class KnowledgeDocumentRepository(Protocol):
    """Scoped durable storage for immutable, policy-approved local knowledge revisions."""

    def list_active_documents(self, scope: MemoryScope) -> tuple[KnownKnowledgeDocument, ...]: ...

    def last_sync_at(self, scope: MemoryScope) -> datetime | None:
        """Return the last successful exact-scope sync, including an empty sync."""
        ...

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


class ProjectProcedureRegistry(Protocol):
    """Scoped read port for versioned checked-in procedures.

    The registry is a selection projection over durable knowledge revisions, not a second source
    of procedure truth. Callers must supply explicit applicability tags.
    """

    def find_current_procedures(
        self, scope: MemoryScope, tags: tuple[str, ...], maximum_procedures: int
    ) -> tuple[ProjectProcedure, ...]: ...

    def find_current_client_profile(
        self, scope: MemoryScope, client: str
    ) -> ProjectClientProfile | None: ...


class ProjectSkillRegistry(Protocol):
    """Scoped live read port for current checked-in skill and agent revisions."""

    def list_current_skills(
        self, scope: MemoryScope, client: str, maximum_skills: int = 32
    ) -> tuple[ProjectSkill, ...]: ...

    def get_current_skill(
        self, scope: MemoryScope, name: str, client: str
    ) -> ProjectSkill | None: ...

    def find_applicable_skills(
        self,
        scope: MemoryScope,
        tags: tuple[str, ...],
        client: str,
        maximum_skills: int = 8,
    ) -> tuple[ProjectSkill, ...]: ...

    def get_current_agent(
        self, scope: MemoryScope, name: str, client: str
    ) -> ProjectAgent | None: ...


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


class SupplementalArtifactConflict(ProjectIndexRepositoryError):
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


class CheckpointExportRepositoryError(Exception):
    """Expected outcome while reading one exact task's complete checkpoint history."""


class InvalidCheckpointExportScope(CheckpointExportRepositoryError):
    pass


class CheckpointExportStorageFailure(CheckpointExportRepositoryError):
    pass


class CheckpointImportRepositoryError(Exception):
    """Expected outcome while atomically importing checkpoint history."""


class CheckpointImportConflict(CheckpointImportRepositoryError):
    pass


class CheckpointImportStorageFailure(CheckpointImportRepositoryError):
    pass


@dataclass(frozen=True, slots=True)
class CheckpointImportResult:
    checkpoint_count: int
    revision_count: int
    event_count: int
    idempotent: bool

    def __post_init__(self) -> None:
        for value in (self.checkpoint_count, self.revision_count, self.event_count):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError("checkpoint import counts must be non-negative integers")


class CheckpointExportRepository(Protocol):
    def export_checkpoint_history(
        self, scope: MemoryScope, *, exported_at: datetime
    ) -> CheckpointExportBundle: ...


class CheckpointImportRepository(Protocol):
    def import_checkpoint_history(
        self,
        source: CheckpointExportBundle,
        target: CheckpointExportBundle,
    ) -> CheckpointImportResult: ...


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
class SupplementalArtifactStoreResult:
    manifest_snapshot_id: DbtSnapshotId
    content_digest: str
    idempotent: bool


@dataclass(frozen=True, slots=True)
class SourceSnapshotStoreResult:
    snapshot: CodeSnapshot
    idempotent: bool


class ProjectIndexRepository(Protocol):
    def last_sync_at(self, scope: MemoryScope) -> datetime | None:
        """Return the last successful exact-scope dbt manifest sync."""
        ...

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

    def latest_transition(
        self, scope: MemoryScope
    ) -> tuple[DbtManifestSnapshot, DbtManifestSnapshot] | None:
        """Return the last explicit activation transition as ``(before, after)``."""
        ...

    def store_catalog_projection(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, artifact: DbtCatalogArtifact
    ) -> SupplementalArtifactStoreResult: ...

    def store_run_results_projection(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, artifact: DbtRunResultsArtifact
    ) -> SupplementalArtifactStoreResult: ...

    def store_source_freshness_projection(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, artifact: DbtSourceFreshnessArtifact
    ) -> SupplementalArtifactStoreResult: ...

    def get_catalog_projection(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId
    ) -> DbtCatalogArtifact | None: ...

    def get_run_results_projection(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId
    ) -> DbtRunResultsArtifact | None: ...

    def get_source_freshness_projection(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId
    ) -> DbtSourceFreshnessArtifact | None: ...

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

    def last_sync_at(self, scope: MemoryScope) -> datetime | None:
        """Return the last successful exact-scope source-structure sync."""
        ...

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
    ) -> tuple[CodeSymbol, ...]:
        """Return bounded scoped matches: exact identity, prefix, then all-token lexical rank."""
        ...

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
