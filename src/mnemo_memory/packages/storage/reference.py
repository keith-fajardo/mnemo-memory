"""Reference adapter for the aggregate/revision checkpoint repository contract."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from threading import Lock
from typing import TypeVar, cast

from mnemo_memory.packages.domain import (
    ActiveEpisodicMemory,
    ApprovedEpisodicEvent,
    ApprovedEpisodicEventGovernance,
    ApprovedEpisodicEventPinAction,
    ApprovedEventGovernanceKind,
    ApprovedEventLifecycleStatus,
    CheckpointAggregate,
    CheckpointContent,
    CheckpointEventKind,
    CheckpointId,
    CheckpointLifecycleEvent,
    CheckpointRevision,
    CheckpointRevisionId,
    CheckpointSourceObservation,
    CheckpointStatus,
    CodeEdge,
    CodeFile,
    CodeSnapshot,
    CodeSnapshotId,
    CodeStructureArtifact,
    CodeSymbol,
    CodeSymbolId,
    CodeSymbolKind,
    CurrentKnowledgeDocumentSection,
    DbtCatalogArtifact,
    DbtRunResultsArtifact,
    DbtSourceFreshnessArtifact,
    EpisodicCandidateReviewAction,
    EpisodicCandidateReviewDecision,
    EpisodicDeletionCause,
    EpisodicExportBundle,
    EpisodicMemoryCandidate,
    EpisodicMemoryDeletion,
    EpisodicMemoryExpiration,
    EpisodicMemoryGovernanceAction,
    EpisodicMemoryPurge,
    EpisodicMemoryRetentionTarget,
    EpisodicMemoryRevision,
    EpisodicMemoryRevisionStatus,
    EventId,
    EventOutboxJob,
    EventOutboxTopic,
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
    ScopeLevel,
    TaskActivityEvent,
    TaskActivityEventDeletion,
    TaskActivityEventExpiration,
    TaskActivityEventPurge,
    TaskActivityEventRetentionTarget,
    active_episodic_memory_at_revision,
    replay_episodic_memory_revisions,
)
from mnemo_memory.packages.domain.dbt_manifest import (
    DbtLineageEdge,
    DbtManifestArtifact,
    DbtManifestNode,
    DbtManifestSnapshot,
    DbtNodeId,
)
from mnemo_memory.packages.domain.identifiers import DbtSnapshotId
from mnemo_memory.packages.policy import (
    ApprovedEpisodicEventSafetyPolicy,
    EpisodicCandidateReviewSafetyPolicy,
    EpisodicMemoryCandidateSafetyPolicy,
    EpisodicMemoryGovernanceSafetyPolicy,
    KnowledgeDocumentSafetyPolicy,
    TaskActivityEventSafetyPolicy,
)

from .contracts import (
    ActiveEpisodicMemoryNotFound,
    ActiveEpisodicMemoryPage,
    ActiveSnapshotConflict,
    ApprovedEpisodicEventConflict,
    ApprovedEpisodicEventGovernanceResult,
    ApprovedEpisodicEventNotFound,
    ApprovedEpisodicEventPage,
    ApprovedEpisodicEventPinResult,
    ApprovedEpisodicEventRecord,
    ApprovedEpisodicEventRecordPage,
    ApprovedEpisodicEventSecretRejected,
    ApprovedEpisodicEventStoreResult,
    CheckpointNotFound,
    CheckpointPage,
    CheckpointRepository,
    CheckpointSourceObservationConflict,
    CheckpointSourceObservationNotFound,
    CheckpointSourceObservationStoreResult,
    DuplicateCheckpoint,
    EpisodicDeletionConflict,
    EpisodicDeletionNotFound,
    EpisodicEventNotFound,
    EpisodicEventPage,
    EpisodicEventStoreResult,
    EpisodicMemoryCandidateConflict,
    EpisodicMemoryCandidateNotFound,
    EpisodicMemoryCandidatePage,
    EpisodicMemoryCandidateRejected,
    EpisodicMemoryCandidateStoreResult,
    EpisodicMemoryDeletionResult,
    EpisodicMemoryExpirationConflict,
    EpisodicMemoryExpirationNotFound,
    EpisodicMemoryExpirationResult,
    EpisodicMemoryGovernanceConflict,
    EpisodicMemoryGovernanceNotFound,
    EpisodicMemoryGovernanceRejected,
    EpisodicMemoryGovernanceResult,
    EpisodicMemoryPurgeConflict,
    EpisodicMemoryPurgeNotFound,
    EpisodicMemoryPurgeResult,
    EpisodicMemoryReviewConflict,
    EpisodicMemoryReviewNotFound,
    EpisodicMemoryReviewRejected,
    EpisodicMemoryReviewResult,
    EventOutboxLeaseConflict,
    EventOutboxNotFound,
    InvalidAbandonmentReason,
    InvalidApprovedEpisodicEventScope,
    InvalidCheckpointScope,
    InvalidEpisodicEventScope,
    InvalidEpisodicExportScope,
    InvalidEpisodicMemoryCandidateScope,
    InvalidKnowledgeDocumentScope,
    InvalidLifecycleTransition,
    InvalidManifestSnapshotScope,
    InvalidTaskActivityEventScope,
    KnowledgeDocumentConflict,
    KnowledgeDocumentNotFound,
    KnowledgeDocumentSecretRejected,
    KnowledgeDocumentSyncStoreResult,
    ManifestNodeNotFound,
    ManifestSnapshotNotFound,
    ManifestSnapshotPage,
    ManifestSnapshotStoreResult,
    RevisionConflict,
    SourceIndexStorageFailure,
    SourceSnapshotNotFound,
    SourceSnapshotStoreResult,
    SourceStructureRepository,
    SupplementalArtifactConflict,
    SupplementalArtifactStoreResult,
    TaskActivityDeletionResult,
    TaskActivityEventConflict,
    TaskActivityEventNotFound,
    TaskActivityEventPage,
    TaskActivityEventRejected,
    TaskActivityEventRepository,
    TaskActivityEventStoreResult,
    TaskActivityExpirationResult,
    TaskActivityPurgeResult,
    TaskActivityRetentionConflict,
    TaskActivityRetentionNotFound,
    TaskActivityRetentionRepository,
    rank_knowledge_sections,
    validate_knowledge_search,
)
from .source_search import source_search_terms, source_symbol_matches, source_symbol_rank

_SupplementalArtifactT = TypeVar(
    "_SupplementalArtifactT",
    DbtCatalogArtifact,
    DbtRunResultsArtifact,
    DbtSourceFreshnessArtifact,
)


def _require_aware_datetime(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


class ReferenceKnowledgeDocumentRepository:
    """Atomic in-memory reference for scoped immutable knowledge revisions.

    Explicit deletion removes every stored content-bearing revision and retains only a private
    tombstone object.  This mirrors the intended SQLite deletion behavior.
    """

    def __init__(self, policy: KnowledgeDocumentSafetyPolicy | None = None) -> None:
        self._policy = policy or KnowledgeDocumentSafetyPolicy()
        self._active: dict[KnowledgeDocumentId, KnownKnowledgeDocument] = {}
        self._revisions: dict[KnowledgeDocumentRevisionId, KnowledgeDocumentRevision] = {}
        self._tombstones: dict[KnowledgeDocumentId, KnowledgeDocumentTombstone] = {}
        self._embeddings: dict[
            tuple[KnowledgeDocumentRevisionId, int, str], KnowledgeSectionEmbedding
        ] = {}

    def list_active_documents(self, scope: MemoryScope) -> tuple[KnownKnowledgeDocument, ...]:
        self._require_scope(scope)
        return tuple(
            sorted(
                (item for item in self._active.values() if item.scope == scope),
                key=lambda item: (item.relative_path, str(item.document_id)),
            )
        )

    def get_current_revision(
        self, scope: MemoryScope, document_id: KnowledgeDocumentId
    ) -> KnowledgeDocumentRevision:
        self._require_scope(scope)
        state = self._active.get(document_id)
        if state is None or state.scope != scope:
            raise KnowledgeDocumentNotFound("knowledge document was not found")
        return self._revisions[state.current_revision_id]

    def get_current_revision_by_path(
        self, scope: MemoryScope, relative_path: str
    ) -> KnowledgeDocumentRevision:
        self._require_scope(scope)
        if not relative_path or relative_path.startswith("/") or ".." in relative_path.split("/"):
            raise KnowledgeDocumentNotFound("knowledge document was not found")
        matching = tuple(
            item
            for item in self._active.values()
            if item.scope == scope and item.relative_path == relative_path
        )
        if len(matching) != 1:
            raise KnowledgeDocumentNotFound("knowledge document was not found")
        return self._revisions[matching[0].current_revision_id]

    def get_revision(
        self,
        scope: MemoryScope,
        document_id: KnowledgeDocumentId,
        revision_id: KnowledgeDocumentRevisionId,
    ) -> KnowledgeDocumentRevision:
        self._require_scope(scope)
        state = self._active.get(document_id)
        revision = self._revisions.get(revision_id)
        if (
            state is None
            or state.scope != scope
            or revision is None
            or revision.document.document_id != document_id
        ):
            raise KnowledgeDocumentNotFound("knowledge document was not found")
        return revision

    def search_current_sections(
        self,
        scope: MemoryScope,
        terms: tuple[str, ...],
        limit: int,
        maximum_documents: int,
    ) -> tuple[KnowledgeDocumentSectionMatch, ...]:
        self._require_scope(scope)
        validate_knowledge_search(terms, limit, maximum_documents)
        active = self.list_active_documents(scope)[:maximum_documents]
        revisions = tuple(self._revisions[known.current_revision_id] for known in active)
        return rank_knowledge_sections(revisions, terms, limit)

    def iter_current_sections(
        self, scope: MemoryScope, maximum_documents: int
    ) -> tuple[CurrentKnowledgeDocumentSection, ...]:
        self._require_scope(scope)
        if not 1 <= maximum_documents <= 128:
            raise KnowledgeDocumentConflict("knowledge document limit is invalid")
        return tuple(
            CurrentKnowledgeDocumentSection(revision, index, section)
            for known in self.list_active_documents(scope)[:maximum_documents]
            for revision in (self._revisions[known.current_revision_id],)
            for index, section in enumerate(revision.document.sections)
        )

    def list_current_section_embeddings(
        self, scope: MemoryScope, model_id: str, maximum_documents: int
    ) -> tuple[KnowledgeSectionEmbedding, ...]:
        self._require_scope(scope)
        if not model_id or len(model_id) > 256 or not 1 <= maximum_documents <= 128:
            raise KnowledgeDocumentConflict("knowledge embedding query is invalid")
        current = {
            (section.revision.revision_id, section.section_index)
            for section in self.iter_current_sections(scope, maximum_documents)
        }
        return tuple(
            item
            for _, item in sorted(
                self._embeddings.items(),
                key=lambda pair: (str(pair[0][0]), pair[0][1], pair[0][2]),
            )
            if item.scope == scope
            and item.model_id == model_id
            and (item.revision_id, item.section_index) in current
        )

    def store_section_embeddings(
        self, scope: MemoryScope, embeddings: tuple[KnowledgeSectionEmbedding, ...]
    ) -> None:
        self._require_scope(scope)
        if not embeddings:
            return
        keys = [(item.revision_id, item.section_index, item.model_id) for item in embeddings]
        if len(set(keys)) != len(keys) or any(item.scope != scope for item in embeddings):
            raise KnowledgeDocumentConflict("knowledge embeddings are invalid")
        current = {
            (section.revision.revision_id, section.section_index)
            for section in self.iter_current_sections(scope, 128)
        }
        if any((item.revision_id, item.section_index) not in current for item in embeddings):
            raise KnowledgeDocumentConflict("knowledge embedding is not current")
        updated = dict(self._embeddings)
        updated.update(
            {(item.revision_id, item.section_index, item.model_id): item for item in embeddings}
        )
        self._embeddings = updated

    def apply_sync(
        self,
        scope: MemoryScope,
        revisions: tuple[KnowledgeDocumentRevision, ...],
        tombstones: tuple[KnowledgeDocumentTombstone, ...],
    ) -> KnowledgeDocumentSyncStoreResult:
        self._require_scope(scope)
        self._validate(scope, revisions, tombstones)
        active, stored_revisions, stored_tombstones, stored_embeddings = (
            dict(self._active),
            dict(self._revisions),
            dict(self._tombstones),
            dict(self._embeddings),
        )
        try:
            for tombstone in tombstones:
                state = active.pop(tombstone.document_id)
                if state.current_revision_id != tombstone.expected_revision_id:
                    raise KnowledgeDocumentConflict("knowledge document current revision conflicts")
                for revision_id, revision in tuple(stored_revisions.items()):
                    if revision.document.document_id == tombstone.document_id:
                        del stored_revisions[revision_id]
                        for key in tuple(stored_embeddings):
                            if key[0] == revision_id:
                                del stored_embeddings[key]
                stored_tombstones[tombstone.document_id] = tombstone
            for revision in revisions:
                document = revision.document
                active[document.document_id] = KnownKnowledgeDocument(
                    document.document_id,
                    scope,
                    document.relative_path,
                    document.content_digest,
                    revision.revision_id,
                    revision.revision_number,
                )
                stored_revisions[revision.revision_id] = revision
                stored_tombstones.pop(document.document_id, None)
        except BaseException:
            raise
        self._active, self._revisions, self._tombstones, self._embeddings = (
            active,
            stored_revisions,
            stored_tombstones,
            stored_embeddings,
        )
        return KnowledgeDocumentSyncStoreResult(
            self.list_active_documents(scope), len(revisions), len(tombstones)
        )

    def _validate(
        self,
        scope: MemoryScope,
        revisions: tuple[KnowledgeDocumentRevision, ...],
        tombstones: tuple[KnowledgeDocumentTombstone, ...],
    ) -> None:
        document_ids = [item.document.document_id for item in revisions]
        tombstone_ids = [item.document_id for item in tombstones]
        if (
            len(set(document_ids)) != len(document_ids)
            or len(set(tombstone_ids)) != len(tombstone_ids)
            or set(document_ids) & set(tombstone_ids)
        ):
            raise KnowledgeDocumentConflict("knowledge sync contains conflicting document actions")
        if any(item.document.scope != scope for item in revisions) or any(
            item.scope != scope for item in tombstones
        ):
            raise InvalidKnowledgeDocumentScope("knowledge document scope is invalid")
        for revision in revisions:
            decision = self._policy.assess(revision.document)
            if not decision.accepted:
                raise KnowledgeDocumentSecretRejected(
                    "knowledge document was rejected by safety policy"
                )
            current = self._active.get(revision.document.document_id)
            if current is None:
                if revision.revision_number != 1 or revision.predecessor_revision_id is not None:
                    raise KnowledgeDocumentConflict(
                        "knowledge document creation revision conflicts"
                    )
            elif (
                current.scope != scope
                or revision.revision_number != current.revision_number + 1
                or revision.predecessor_revision_id != current.current_revision_id
            ):
                raise KnowledgeDocumentConflict("knowledge document current revision conflicts")
        for tombstone in tombstones:
            current = self._active.get(tombstone.document_id)
            if (
                current is None
                or current.scope != scope
                or current.current_revision_id != tombstone.expected_revision_id
                or current.content_digest != tombstone.content_digest
                or current.relative_path != tombstone.relative_path
            ):
                raise KnowledgeDocumentConflict("knowledge document deletion conflicts")
        final_paths = {
            item.relative_path: item.document_id
            for item in self._active.values()
            if item.scope == scope and item.document_id not in set(tombstone_ids)
        }
        for revision in revisions:
            prior = final_paths.get(revision.document.relative_path)
            if prior is not None and prior != revision.document.document_id:
                raise KnowledgeDocumentConflict("knowledge document path conflicts")
            final_paths[revision.document.relative_path] = revision.document.document_id

    @staticmethod
    def _require_scope(scope: MemoryScope) -> None:
        if not isinstance(scope, MemoryScope) or scope.level is not ScopeLevel.PROJECT:
            raise InvalidKnowledgeDocumentScope(
                "knowledge documents require explicit project scope"
            )


class ReferenceEventOutboxRepository:
    """Atomic in-memory reference for scoped at-least-once event delivery."""

    def __init__(self) -> None:
        self._jobs: dict[OutboxJobId, EventOutboxJob] = {}
        self._lock = Lock()

    def claim_event_jobs(
        self,
        scope: MemoryScope,
        *,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
        limit: int,
    ) -> tuple[EventOutboxJob, ...]:
        self._require_scope(scope)
        EventOutboxJob.validate_worker_id(worker_id)
        if not 1 <= limit <= 100:
            raise ValueError("event outbox claim limit must be between 1 and 100")
        if lease_expires_at <= now:
            raise ValueError("event outbox lease must expire after claim time")
        with self._lock:
            eligible = sorted(
                (
                    job
                    for job in self._jobs.values()
                    if job.scope == scope
                    and job.completed_at is None
                    and job.available_at <= now
                    and (job.lease_expires_at is None or job.lease_expires_at <= now)
                ),
                key=lambda job: (job.created_at, str(job.job_id)),
            )[:limit]
            claimed = tuple(job.claim(worker_id, lease_expires_at) for job in eligible)
            self._jobs.update((job.job_id, job) for job in claimed)
            return claimed

    def complete_event_job(
        self,
        scope: MemoryScope,
        job_id: OutboxJobId,
        *,
        worker_id: str,
        completed_at: datetime,
    ) -> EventOutboxJob:
        EventOutboxJob.validate_worker_id(worker_id)
        with self._lock:
            job = self._scoped_job(scope, job_id)
            self._require_lease(job, worker_id, completed_at)
            completed = job.complete(completed_at)
            self._jobs[job_id] = completed
            return completed

    def retry_event_job(
        self,
        scope: MemoryScope,
        job_id: OutboxJobId,
        *,
        worker_id: str,
        now: datetime,
        available_at: datetime,
        failure_code: str,
    ) -> EventOutboxJob:
        EventOutboxJob.validate_worker_id(worker_id)
        EventOutboxJob.validate_failure_code(failure_code)
        if available_at < now:
            raise ValueError("event outbox retry cannot be scheduled in the past")
        with self._lock:
            job = self._scoped_job(scope, job_id)
            self._require_lease(job, worker_id, now)
            retried = job.retry(available_at, failure_code)
            self._jobs[job_id] = retried
            return retried

    def get_event_job(self, scope: MemoryScope, job_id: OutboxJobId) -> EventOutboxJob:
        with self._lock:
            return self._scoped_job(scope, job_id)

    def _enqueue(self, job: EventOutboxJob) -> EventOutboxJob:
        return self._enqueue_many((job,))[0]

    def _enqueue_many(self, jobs: tuple[EventOutboxJob, ...]) -> tuple[EventOutboxJob, ...]:
        with self._lock:
            if len({job.job_id for job in jobs}) != len(jobs):
                raise ValueError("event outbox batch contains duplicate jobs")
            for job in jobs:
                existing = self._jobs.get(job.job_id)
                if existing is not None and existing != job:
                    raise ValueError("event outbox job identity conflicts")
            self._jobs.update((job.job_id, job) for job in jobs)
            return tuple(self._jobs[job.job_id] for job in jobs)

    def _cancel_source_jobs(
        self, topic: EventOutboxTopic, source_event_ids: tuple[EventId, ...]
    ) -> None:
        targets = set(source_event_ids)
        with self._lock:
            self._jobs = {
                job_id: job
                for job_id, job in self._jobs.items()
                if job.topic is not topic or job.source_event_id not in targets
            }

    def _scoped_job(self, scope: MemoryScope, job_id: OutboxJobId) -> EventOutboxJob:
        self._require_scope(scope)
        job = self._jobs.get(job_id)
        if job is None or job.scope != scope:
            raise EventOutboxNotFound("event outbox job was not found")
        return job

    @staticmethod
    def _require_lease(job: EventOutboxJob, worker_id: str, now: datetime) -> None:
        if (
            job.completed_at is not None
            or job.lease_owner != worker_id
            or job.lease_expires_at is None
            or job.lease_expires_at <= now
        ):
            raise EventOutboxLeaseConflict("event outbox lease is not owned by this worker")

    @staticmethod
    def _require_scope(scope: MemoryScope) -> None:
        if not isinstance(scope, MemoryScope) or scope.level is not ScopeLevel.TASK:
            raise ValueError("event outbox operations require explicit task scope")


class ReferenceTaskActivityEventRepository:
    """Append-only in-memory reference for explicitly minimized task activity."""

    def __init__(
        self,
        outbox: ReferenceEventOutboxRepository | None = None,
        policy: TaskActivityEventSafetyPolicy | None = None,
    ) -> None:
        self.outbox = outbox or ReferenceEventOutboxRepository()
        self._policy = policy or TaskActivityEventSafetyPolicy()
        self._events: dict[EventId, TaskActivityEvent] = {}
        self._keys: dict[tuple[MemoryScope, str], EventId] = {}
        self._ordered: list[EventId] = []
        self._expirations: dict[EventId, TaskActivityEventExpiration] = {}
        self._purges: dict[EventId, TaskActivityEventPurge] = {}
        self._deletions: dict[EventId, TaskActivityEventDeletion] = {}
        self._deletion_keys: dict[tuple[MemoryScope, str], EventId] = {}

    def append_task_activity_event(self, event: TaskActivityEvent) -> TaskActivityEventStoreResult:
        self._require_scope(event.scope)
        if not self._policy.assess(event).accepted:
            raise TaskActivityEventRejected("task activity event was rejected by safety policy")
        if event.event_id in self._expirations or event.event_id in self._deletions:
            raise TaskActivityEventConflict(
                "expired or deleted task activity event cannot be restored"
            )
        key = (event.scope, event.source_event_key)
        existing_id = self._keys.get(key)
        if existing_id is not None:
            existing = self._events[existing_id]
            if existing == event:
                return TaskActivityEventStoreResult(existing, True)
            raise TaskActivityEventConflict("task activity event key conflicts")
        if event.event_id in self._events:
            raise TaskActivityEventConflict("task activity event identity conflicts")
        events = dict(self._events)
        keys = dict(self._keys)
        ordered = list(self._ordered)
        events[event.event_id] = event
        keys[key] = event.event_id
        ordered.append(event.event_id)
        self.outbox._enqueue(
            EventOutboxJob.create(
                scope=event.scope,
                topic=EventOutboxTopic.TASK_ACTIVITY,
                source_event_id=event.event_id,
                event_kind=event.kind.value,
                occurred_at=event.occurred_at,
                created_at=event.occurred_at,
            )
        )
        self._events, self._keys, self._ordered = events, keys, ordered
        return TaskActivityEventStoreResult(event, False)

    def get_task_activity_event(self, scope: MemoryScope, event_id: EventId) -> TaskActivityEvent:
        self._require_scope(scope)
        event = self._events.get(event_id)
        if (
            event is None
            or event.scope != scope
            or event_id in self._expirations
            or event_id in self._deletions
        ):
            raise TaskActivityEventNotFound("task activity event was not found")
        return event

    def list_task_activity_events(
        self, scope: MemoryScope, *, offset: int = 0, limit: int = 50
    ) -> TaskActivityEventPage:
        self._require_scope(scope)
        if offset < 0 or limit < 1:
            raise ValueError("event offset must be non-negative and limit must be positive")
        items = tuple(
            self._events[event_id]
            for event_id in reversed(self._ordered)
            if self._events[event_id].scope == scope
            and event_id not in self._expirations
            and event_id not in self._deletions
        )
        return TaskActivityEventPage(
            items[offset : offset + limit],
            offset + limit if offset + limit < len(items) else None,
        )

    def list_due_task_activity_retention(
        self, scope: MemoryScope, *, as_of: datetime
    ) -> tuple[TaskActivityEventRetentionTarget, ...]:
        self._require_scope(scope)
        _require_aware_datetime(as_of, "as_of")
        targets = tuple(
            TaskActivityEventRetentionTarget(event.event_id, event.scope, event.retention)
            for event in self._events.values()
            if event.scope == scope
            and event.event_id not in self._expirations
            and not event.retention.permanent
            and event.retention.is_expired(as_of)
        )
        return tuple(
            sorted(
                targets,
                key=lambda item: (
                    item.retention.expires_at.isoformat()
                    if item.retention.expires_at is not None
                    else "",
                    str(item.event_id),
                ),
            )
        )

    def apply_task_activity_expirations(
        self, expirations: tuple[TaskActivityEventExpiration, ...]
    ) -> TaskActivityExpirationResult:
        values = tuple(expirations)
        if not values:
            return TaskActivityExpirationResult((), True)
        if len(values) > 256 or len({item.event_id for item in values}) != len(values):
            raise ValueError("task activity expiration batch is invalid")
        existing_count = 0
        for expiration in values:
            if not isinstance(expiration, TaskActivityEventExpiration):
                raise TypeError("task activity expiration batch is invalid")
            self._require_scope(expiration.scope)
            event = self._events.get(expiration.event_id)
            if event is None or event.scope != expiration.scope:
                raise TaskActivityRetentionNotFound("task activity retention target was not found")
            if (
                event.retention.permanent
                or event.retention.policy_id != expiration.retention_policy_id
                or event.retention.expires_at != expiration.scheduled_expires_at
                or not event.retention.is_expired(expiration.expired_at)
            ):
                raise TaskActivityRetentionConflict(
                    "task activity expiration does not match canonical retention"
                )
            existing = self._expirations.get(expiration.event_id)
            if existing is not None:
                if existing != expiration:
                    raise TaskActivityRetentionConflict(
                        "task activity event already has a different expiration"
                    )
                existing_count += 1
        stored = dict(self._expirations)
        stored.update((item.event_id, item) for item in values)
        self._expirations = stored
        return TaskActivityExpirationResult(values, existing_count == len(values))

    def get_task_activity_expiration(
        self, scope: MemoryScope, event_id: EventId
    ) -> TaskActivityEventExpiration:
        self._require_scope(scope)
        expiration = self._expirations.get(event_id)
        if expiration is None or expiration.scope != scope:
            raise TaskActivityRetentionNotFound("task activity expiration was not found")
        return expiration

    def list_unpurged_task_activity_expirations(
        self, scope: MemoryScope
    ) -> tuple[TaskActivityEventExpiration, ...]:
        self._require_scope(scope)
        return tuple(
            sorted(
                (
                    expiration
                    for event_id, expiration in self._expirations.items()
                    if expiration.scope == scope and event_id not in self._purges
                ),
                key=lambda item: (item.expired_at.isoformat(), str(item.event_id)),
            )
        )

    def apply_task_activity_purges(
        self, purges: tuple[TaskActivityEventPurge, ...]
    ) -> TaskActivityPurgeResult:
        values = tuple(purges)
        if not values:
            return TaskActivityPurgeResult((), True)
        if len(values) > 256 or len({item.event_id for item in values}) != len(values):
            raise ValueError("task activity purge batch is invalid")
        existing_count = 0
        for purge in values:
            if not isinstance(purge, TaskActivityEventPurge):
                raise TypeError("task activity purge batch is invalid")
            self._require_scope(purge.scope)
            expiration = self._expirations.get(purge.event_id)
            if expiration is None or expiration.scope != purge.scope:
                raise TaskActivityRetentionNotFound(
                    "task activity expiration was not found for purge"
                )
            if (
                purge.expiration_id != expiration.expiration_id
                or purge.purged_at < expiration.expired_at
            ):
                raise TaskActivityRetentionConflict(
                    "task activity purge does not match canonical expiration"
                )
            existing = self._purges.get(purge.event_id)
            if existing is not None:
                if existing != purge:
                    raise TaskActivityRetentionConflict(
                        "task activity event already has a different purge"
                    )
                existing_count += 1
            elif purge.event_id not in self._events and purge.event_id not in self._deletions:
                raise TaskActivityRetentionNotFound(
                    "task activity event payload was not found for purge"
                )
        events = dict(self._events)
        ordered = list(self._ordered)
        stored_purges = dict(self._purges)
        new_event_ids: list[EventId] = []
        for purge in values:
            if purge.event_id in stored_purges:
                continue
            events.pop(purge.event_id)
            ordered = [item for item in ordered if item != purge.event_id]
            stored_purges[purge.event_id] = purge
            new_event_ids.append(purge.event_id)
        self.outbox._cancel_source_jobs(EventOutboxTopic.TASK_ACTIVITY, tuple(new_event_ids))
        self._events, self._ordered, self._purges = events, ordered, stored_purges
        return TaskActivityPurgeResult(values, existing_count == len(values))

    def get_task_activity_purge(
        self, scope: MemoryScope, event_id: EventId
    ) -> TaskActivityEventPurge:
        self._require_scope(scope)
        purge = self._purges.get(event_id)
        if purge is None or purge.scope != scope:
            raise TaskActivityRetentionNotFound("task activity purge was not found")
        return purge

    def _delete_task_activity_event(
        self, deletion: TaskActivityEventDeletion
    ) -> TaskActivityDeletionResult:
        idempotent = self._validate_deletion(deletion)
        if idempotent:
            return TaskActivityDeletionResult(deletion, (), True)
        self._deletions[deletion.event_id] = deletion
        self._deletion_keys[(deletion.scope, deletion.source_action_key)] = deletion.event_id
        self._events.pop(deletion.event_id, None)
        self._ordered = [item for item in self._ordered if item != deletion.event_id]
        self.outbox._cancel_source_jobs(EventOutboxTopic.TASK_ACTIVITY, (deletion.event_id,))
        return TaskActivityDeletionResult(deletion, (), False)

    def _get_task_activity_deletion(
        self, scope: MemoryScope, event_id: EventId
    ) -> TaskActivityEventDeletion:
        self._require_scope(scope)
        deletion = self._deletions.get(event_id)
        if deletion is None or deletion.scope != scope:
            raise EpisodicDeletionNotFound("task activity deletion was not found")
        return deletion

    def _validate_deletion(self, deletion: TaskActivityEventDeletion) -> bool:
        if not isinstance(deletion, TaskActivityEventDeletion):
            raise TypeError("task activity deletion is invalid")
        self._require_scope(deletion.scope)
        existing = self._deletions.get(deletion.event_id)
        if existing is not None:
            if existing == deletion:
                return True
            raise EpisodicDeletionConflict("task activity event already has a different deletion")
        key = (deletion.scope, deletion.source_action_key)
        if key in self._deletion_keys:
            raise EpisodicDeletionConflict("task activity deletion action key conflicts")
        event = self._events.get(deletion.event_id)
        expiration = self._expirations.get(deletion.event_id)
        if not (
            (event is not None and event.scope == deletion.scope)
            or (expiration is not None and expiration.scope == deletion.scope)
        ):
            raise EpisodicDeletionNotFound("task activity event was not found for deletion")
        return False

    @staticmethod
    def _require_scope(scope: MemoryScope) -> None:
        if not isinstance(scope, MemoryScope) or scope.level is not ScopeLevel.TASK:
            raise InvalidTaskActivityEventScope("task activity events require explicit task scope")


class ReferenceEpisodicMemoryCandidateRepository:
    """Atomic in-memory reference for inactive candidates from canonical task events."""

    def __init__(
        self,
        activity_events: TaskActivityEventRepository,
        policy: EpisodicMemoryCandidateSafetyPolicy | None = None,
        review_policy: EpisodicCandidateReviewSafetyPolicy | None = None,
        governance_policy: EpisodicMemoryGovernanceSafetyPolicy | None = None,
    ) -> None:
        self._activity_events = activity_events
        self._policy = policy or EpisodicMemoryCandidateSafetyPolicy()
        self._review_policy = review_policy or EpisodicCandidateReviewSafetyPolicy()
        self._governance_policy = governance_policy or EpisodicMemoryGovernanceSafetyPolicy()
        self._candidates: dict[MemoryId, EpisodicMemoryCandidate] = {}
        self._ordered: list[MemoryId] = []
        self._reviews: dict[MemoryId, EpisodicCandidateReviewAction] = {}
        self._review_keys: dict[tuple[MemoryScope, str], MemoryId] = {}
        self._active: dict[MemoryId, ActiveEpisodicMemory] = {}
        self._active_ordered: list[MemoryId] = []
        self._governance: dict[EventId, EpisodicMemoryGovernanceAction] = {}
        self._governance_keys: dict[tuple[MemoryScope, str], EventId] = {}
        self._governance_order: dict[MemoryId, list[EventId]] = {}
        self._expirations: dict[MemoryId, EpisodicMemoryExpiration] = {}
        self._purges: dict[MemoryId, EpisodicMemoryPurge] = {}
        self._deletions: dict[MemoryId, EpisodicMemoryDeletion] = {}
        self._deletion_keys: dict[tuple[MemoryScope, str], MemoryId] = {}

    def store_episodic_memory_candidates(
        self, candidates: tuple[EpisodicMemoryCandidate, ...]
    ) -> EpisodicMemoryCandidateStoreResult:
        values = self._validate_batch(candidates)
        first = values[0]
        if any(
            candidate.memory_id in self._expirations or candidate.memory_id in self._deletions
            for candidate in values
        ):
            raise EpisodicMemoryCandidateConflict(
                "expired or deleted episodic candidates cannot be restored"
            )
        source = self._activity_events.get_task_activity_event(first.scope, first.source_event_id)
        if any(
            candidate.scope != source.scope
            or candidate.retention != source.retention
            or candidate.evidence_references != source.evidence_references
            for candidate in values
        ):
            raise EpisodicMemoryCandidateConflict(
                "episodic candidate authority fields do not match the source event"
            )
        if any(not self._policy.assess(candidate).accepted for candidate in values):
            raise EpisodicMemoryCandidateRejected(
                "episodic candidate batch was rejected by safety policy"
            )
        existing = tuple(
            sorted(
                (
                    candidate
                    for candidate in self._candidates.values()
                    if candidate.source_event_id == first.source_event_id
                    and candidate.extractor_version == first.extractor_version
                ),
                key=lambda candidate: candidate.proposal_index,
            )
        )
        if existing:
            if any(
                candidate.memory_id in self._expirations or candidate.memory_id in self._deletions
                for candidate in existing
            ):
                raise EpisodicMemoryCandidateConflict(
                    "expired episodic candidates cannot be restored"
                )
            if existing == values:
                return EpisodicMemoryCandidateStoreResult(existing, True)
            raise EpisodicMemoryCandidateConflict(
                "episodic candidate extraction already has different output"
            )
        if any(candidate.memory_id in self._candidates for candidate in values):
            raise EpisodicMemoryCandidateConflict("episodic candidate identity conflicts")
        stored = dict(self._candidates)
        ordered = list(self._ordered)
        stored.update((candidate.memory_id, candidate) for candidate in values)
        ordered.extend(candidate.memory_id for candidate in values)
        self._candidates, self._ordered = stored, ordered
        return EpisodicMemoryCandidateStoreResult(values, False)

    def get_episodic_memory_candidate(
        self, scope: MemoryScope, memory_id: MemoryId
    ) -> EpisodicMemoryCandidate:
        self._require_scope(scope)
        candidate = self._candidates.get(memory_id)
        if (
            candidate is None
            or candidate.scope != scope
            or memory_id in self._expirations
            or memory_id in self._deletions
        ):
            raise EpisodicMemoryCandidateNotFound("episodic memory candidate was not found")
        return candidate

    def list_episodic_memory_candidates(
        self,
        scope: MemoryScope,
        *,
        source_event_id: EventId | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> EpisodicMemoryCandidatePage:
        self._require_scope(scope)
        if offset < 0 or limit < 1:
            raise ValueError("candidate offset must be non-negative and limit must be positive")
        items = tuple(
            self._candidates[memory_id]
            for memory_id in reversed(self._ordered)
            if self._candidates[memory_id].scope == scope
            and memory_id not in self._expirations
            and memory_id not in self._deletions
            and (
                source_event_id is None
                or self._candidates[memory_id].source_event_id == source_event_id
            )
        )
        return EpisodicMemoryCandidatePage(
            items[offset : offset + limit],
            offset + limit if offset + limit < len(items) else None,
        )

    def review_episodic_memory_candidate(
        self, action: EpisodicCandidateReviewAction
    ) -> EpisodicMemoryReviewResult:
        self._require_scope(action.scope)
        if action.candidate_id in self._expirations or action.candidate_id in self._deletions:
            raise EpisodicMemoryReviewNotFound("episodic memory candidate was not found for review")
        candidate = self.get_episodic_memory_candidate(action.scope, action.candidate_id)
        if not self._review_policy.assess(candidate, action).accepted:
            raise EpisodicMemoryReviewRejected(
                "episodic memory review was rejected by safety policy"
            )
        existing = self._reviews.get(action.candidate_id)
        if existing is not None:
            if existing == action:
                try:
                    active = self.get_active_episodic_memory(action.scope, action.candidate_id)
                except ActiveEpisodicMemoryNotFound:
                    active = None
                return EpisodicMemoryReviewResult(existing, active, True)
            raise EpisodicMemoryReviewConflict("episodic candidate already has a different review")
        key = (action.scope, action.source_action_key)
        if key in self._review_keys:
            raise EpisodicMemoryReviewConflict("episodic review action key conflicts")
        active = (
            ActiveEpisodicMemory.approve(candidate, action)
            if action.decision is EpisodicCandidateReviewDecision.APPROVED
            else None
        )
        reviews = dict(self._reviews)
        review_keys = dict(self._review_keys)
        active_values = dict(self._active)
        active_ordered = list(self._active_ordered)
        reviews[action.candidate_id] = action
        review_keys[key] = action.candidate_id
        if active is not None:
            active_values[active.memory_id] = active
            active_ordered.append(active.memory_id)
        self._reviews, self._review_keys = reviews, review_keys
        self._active, self._active_ordered = active_values, active_ordered
        return EpisodicMemoryReviewResult(action, active, False)

    def get_episodic_memory_review(
        self, scope: MemoryScope, candidate_id: MemoryId
    ) -> EpisodicCandidateReviewAction:
        self._require_scope(scope)
        action = self._reviews.get(candidate_id)
        if (
            action is None
            or action.scope != scope
            or candidate_id in self._expirations
            or candidate_id in self._deletions
        ):
            raise EpisodicMemoryReviewNotFound("episodic memory review was not found")
        return action

    def get_active_episodic_memory(
        self, scope: MemoryScope, memory_id: MemoryId
    ) -> ActiveEpisodicMemory:
        self._require_scope(scope)
        base = self._active.get(memory_id)
        if (
            base is None
            or base.scope != scope
            or memory_id in self._expirations
            or memory_id in self._deletions
        ):
            raise ActiveEpisodicMemoryNotFound("active episodic memory was not found")
        revisions = self._revisions_for(base)
        current = revisions[-1]
        if current.status is not EpisodicMemoryRevisionStatus.ACTIVE:
            raise ActiveEpisodicMemoryNotFound("active episodic memory was not found")
        return active_episodic_memory_at_revision(base, current)

    def list_active_episodic_memories(
        self, scope: MemoryScope, *, offset: int = 0, limit: int = 50
    ) -> ActiveEpisodicMemoryPage:
        self._require_scope(scope)
        if offset < 0 or limit < 1:
            raise ValueError("memory offset must be non-negative and limit must be positive")
        active_items: list[ActiveEpisodicMemory] = []
        for memory_id in reversed(self._active_ordered):
            if self._active[memory_id].scope != scope:
                continue
            try:
                active_items.append(self.get_active_episodic_memory(scope, memory_id))
            except ActiveEpisodicMemoryNotFound:
                continue
        items = tuple(active_items)
        return ActiveEpisodicMemoryPage(
            items[offset : offset + limit],
            offset + limit if offset + limit < len(items) else None,
        )

    def govern_episodic_memory(
        self, action: EpisodicMemoryGovernanceAction
    ) -> EpisodicMemoryGovernanceResult:
        self._require_scope(action.scope)
        base = self._active.get(action.memory_id)
        if (
            base is None
            or base.scope != action.scope
            or action.memory_id in self._expirations
            or action.memory_id in self._deletions
        ):
            raise EpisodicMemoryGovernanceNotFound("episodic memory was not found")
        existing = self._governance.get(action.action_id)
        if existing is not None:
            if existing != action:
                raise EpisodicMemoryGovernanceConflict(
                    "episodic memory governance identity conflicts"
                )
            revisions = self._revisions_for(base)
            current = revisions[-1]
            active = (
                active_episodic_memory_at_revision(base, current)
                if current.status is EpisodicMemoryRevisionStatus.ACTIVE
                else None
            )
            return EpisodicMemoryGovernanceResult(existing, current, active, True)
        key = (action.scope, action.source_action_key)
        if key in self._governance_keys:
            raise EpisodicMemoryGovernanceConflict(
                "episodic memory governance action key conflicts"
            )
        revisions = self._revisions_for(base)
        current = revisions[-1]
        if current.status is not EpisodicMemoryRevisionStatus.ACTIVE:
            raise EpisodicMemoryGovernanceConflict("retracted episodic memory is terminal")
        if action.expected_revision_id != current.revision_id:
            raise EpisodicMemoryGovernanceConflict("episodic memory expected revision is stale")
        if action.occurred_at < current.created_at:
            raise EpisodicMemoryGovernanceConflict(
                "episodic memory governance time precedes the current revision"
            )
        current_active = active_episodic_memory_at_revision(base, current)
        if not self._governance_policy.assess(current_active, action).accepted:
            raise EpisodicMemoryGovernanceRejected(
                "episodic memory governance was rejected by safety policy"
            )
        ordered_ids = [*self._governance_order.get(action.memory_id, ()), action.action_id]
        proposed_actions = tuple(
            self._governance[action_id] if action_id != action.action_id else action
            for action_id in ordered_ids
        )
        try:
            proposed_revisions = replay_episodic_memory_revisions(base, proposed_actions)
        except ValueError as error:
            raise EpisodicMemoryGovernanceConflict(
                "episodic memory governance does not form a valid revision"
            ) from error
        governance = dict(self._governance)
        governance_keys = dict(self._governance_keys)
        governance_order = {
            memory_id: list(action_ids) for memory_id, action_ids in self._governance_order.items()
        }
        governance[action.action_id] = action
        governance_keys[key] = action.action_id
        governance_order[action.memory_id] = ordered_ids
        self._governance = governance
        self._governance_keys = governance_keys
        self._governance_order = governance_order
        latest = proposed_revisions[-1]
        active = (
            active_episodic_memory_at_revision(base, latest)
            if latest.status is EpisodicMemoryRevisionStatus.ACTIVE
            else None
        )
        return EpisodicMemoryGovernanceResult(action, latest, active, False)

    def get_episodic_memory_governance(
        self, scope: MemoryScope, action_id: EventId
    ) -> EpisodicMemoryGovernanceAction:
        self._require_scope(scope)
        action = self._governance.get(action_id)
        if (
            action is None
            or action.scope != scope
            or action.memory_id in self._expirations
            or action.memory_id in self._deletions
        ):
            raise EpisodicMemoryGovernanceNotFound(
                "episodic memory governance action was not found"
            )
        return action

    def list_episodic_memory_revisions(
        self, scope: MemoryScope, memory_id: MemoryId
    ) -> tuple[EpisodicMemoryRevision, ...]:
        self._require_scope(scope)
        base = self._active.get(memory_id)
        if (
            base is None
            or base.scope != scope
            or memory_id in self._expirations
            or memory_id in self._deletions
        ):
            raise EpisodicMemoryGovernanceNotFound("episodic memory was not found")
        return self._revisions_for(base)

    def list_due_episodic_memory_retention(
        self, scope: MemoryScope, *, as_of: datetime
    ) -> tuple[EpisodicMemoryRetentionTarget, ...]:
        self._require_scope(scope)
        _require_aware_datetime(as_of, "as_of")
        targets = tuple(
            EpisodicMemoryRetentionTarget(
                candidate.memory_id,
                candidate.source_event_id,
                candidate.scope,
                candidate.retention,
            )
            for candidate in self._candidates.values()
            if candidate.scope == scope
            and candidate.memory_id not in self._expirations
            and not candidate.retention.permanent
            and candidate.retention.is_expired(as_of)
        )
        return tuple(
            sorted(
                targets,
                key=lambda item: (
                    item.retention.expires_at.isoformat()
                    if item.retention.expires_at is not None
                    else "",
                    str(item.memory_id),
                ),
            )
        )

    def apply_episodic_memory_expirations(
        self, expirations: tuple[EpisodicMemoryExpiration, ...]
    ) -> EpisodicMemoryExpirationResult:
        values = tuple(expirations)
        if not values:
            return EpisodicMemoryExpirationResult((), True)
        if len(values) > 256 or len({item.memory_id for item in values}) != len(values):
            raise ValueError("episodic memory expiration batch is invalid")
        existing_count = 0
        for expiration in values:
            if not isinstance(expiration, EpisodicMemoryExpiration):
                raise TypeError("episodic memory expiration batch is invalid")
            self._require_scope(expiration.scope)
            candidate = self._candidates.get(expiration.memory_id)
            if candidate is None or candidate.scope != expiration.scope:
                raise EpisodicMemoryExpirationNotFound(
                    "episodic memory retention target was not found"
                )
            schedule = candidate.retention
            if (
                candidate.source_event_id != expiration.source_event_id
                or schedule.permanent
                or schedule.expires_at != expiration.scheduled_expires_at
                or schedule.policy_id != expiration.retention_policy_id
                or not schedule.is_expired(expiration.expired_at)
            ):
                raise EpisodicMemoryExpirationConflict(
                    "episodic memory expiration does not match canonical retention"
                )
            existing = self._expirations.get(expiration.memory_id)
            if existing is not None:
                if existing != expiration:
                    raise EpisodicMemoryExpirationConflict(
                        "episodic memory already has a different expiration"
                    )
                existing_count += 1
        stored = dict(self._expirations)
        stored.update((item.memory_id, item) for item in values)
        self._expirations = stored
        return EpisodicMemoryExpirationResult(values, existing_count == len(values))

    def get_episodic_memory_expiration(
        self, scope: MemoryScope, memory_id: MemoryId
    ) -> EpisodicMemoryExpiration:
        self._require_scope(scope)
        expiration = self._expirations.get(memory_id)
        if expiration is None or expiration.scope != scope:
            raise EpisodicMemoryExpirationNotFound("episodic memory expiration was not found")
        return expiration

    def list_unpurged_episodic_memory_expirations(
        self, scope: MemoryScope
    ) -> tuple[EpisodicMemoryExpiration, ...]:
        self._require_scope(scope)
        return tuple(
            sorted(
                (
                    expiration
                    for memory_id, expiration in self._expirations.items()
                    if expiration.scope == scope and memory_id not in self._purges
                ),
                key=lambda item: (item.expired_at.isoformat(), str(item.memory_id)),
            )
        )

    def apply_episodic_memory_purges(
        self, purges: tuple[EpisodicMemoryPurge, ...]
    ) -> EpisodicMemoryPurgeResult:
        values = tuple(purges)
        if not values:
            return EpisodicMemoryPurgeResult((), True)
        if len(values) > 256 or len({item.memory_id for item in values}) != len(values):
            raise ValueError("episodic memory purge batch is invalid")
        existing_count = 0
        for purge in values:
            if not isinstance(purge, EpisodicMemoryPurge):
                raise TypeError("episodic memory purge batch is invalid")
            self._require_scope(purge.scope)
            expiration = self._expirations.get(purge.memory_id)
            if expiration is None or expiration.scope != purge.scope:
                raise EpisodicMemoryPurgeNotFound(
                    "episodic memory expiration was not found for purge"
                )
            if (
                purge.expiration_id != expiration.expiration_id
                or purge.purged_at < expiration.expired_at
            ):
                raise EpisodicMemoryPurgeConflict(
                    "episodic memory purge does not match canonical expiration"
                )
            existing = self._purges.get(purge.memory_id)
            if existing is not None:
                if existing != purge:
                    raise EpisodicMemoryPurgeConflict(
                        "episodic memory already has a different purge"
                    )
                existing_count += 1
            elif purge.memory_id not in self._candidates and purge.memory_id not in self._deletions:
                raise EpisodicMemoryPurgeNotFound("episodic memory payload was not found for purge")

        candidates = dict(self._candidates)
        ordered = list(self._ordered)
        reviews = dict(self._reviews)
        review_keys = dict(self._review_keys)
        active = dict(self._active)
        active_ordered = list(self._active_ordered)
        governance = dict(self._governance)
        governance_keys = dict(self._governance_keys)
        governance_order = {
            memory_id: list(action_ids) for memory_id, action_ids in self._governance_order.items()
        }
        stored_purges = dict(self._purges)
        for purge in values:
            if purge.memory_id in stored_purges:
                continue
            candidates.pop(purge.memory_id)
            ordered = [item for item in ordered if item != purge.memory_id]
            review = reviews.pop(purge.memory_id, None)
            if review is not None:
                review_keys.pop((review.scope, review.source_action_key), None)
            active.pop(purge.memory_id, None)
            active_ordered = [item for item in active_ordered if item != purge.memory_id]
            action_ids = governance_order.pop(purge.memory_id, [])
            for action_id in action_ids:
                action = governance.pop(action_id)
                governance_keys.pop((action.scope, action.source_action_key), None)
            stored_purges[purge.memory_id] = purge
        self._candidates, self._ordered = candidates, ordered
        self._reviews, self._review_keys = reviews, review_keys
        self._active, self._active_ordered = active, active_ordered
        self._governance, self._governance_keys = governance, governance_keys
        self._governance_order, self._purges = governance_order, stored_purges
        return EpisodicMemoryPurgeResult(values, existing_count == len(values))

    def get_episodic_memory_purge(
        self, scope: MemoryScope, memory_id: MemoryId
    ) -> EpisodicMemoryPurge:
        self._require_scope(scope)
        purge = self._purges.get(memory_id)
        if purge is None or purge.scope != scope:
            raise EpisodicMemoryPurgeNotFound("episodic memory purge was not found")
        return purge

    def delete_episodic_memory(
        self, deletion: EpisodicMemoryDeletion
    ) -> EpisodicMemoryDeletionResult:
        idempotent = self._validate_memory_deletion(deletion)
        if idempotent:
            return EpisodicMemoryDeletionResult(deletion, True)
        self._store_memory_deletion(deletion)
        return EpisodicMemoryDeletionResult(deletion, False)

    def delete_task_activity_event(
        self, deletion: TaskActivityEventDeletion
    ) -> TaskActivityDeletionResult:
        activity = self._reference_task_activity_events
        source_idempotent = activity._validate_deletion(deletion)
        memory_ids = {
            candidate.memory_id
            for candidate in self._candidates.values()
            if candidate.scope == deletion.scope and candidate.source_event_id == deletion.event_id
        }
        memory_ids.update(
            expiration.memory_id
            for expiration in self._expirations.values()
            if expiration.scope == deletion.scope
            and expiration.source_event_id == deletion.event_id
        )
        memory_ids.update(
            memory_id
            for memory_id, existing in self._deletions.items()
            if existing.scope == deletion.scope and existing.source_event_id == deletion.event_id
        )
        dependent: list[EpisodicMemoryDeletion] = []
        pending: list[EpisodicMemoryDeletion] = []
        for memory_id in sorted(memory_ids, key=str):
            existing = self._deletions.get(memory_id)
            if existing is not None:
                dependent.append(existing)
                continue
            item = EpisodicMemoryDeletion.from_source(
                deletion,
                memory_id=memory_id,
                source_event_id=deletion.event_id,
            )
            self._validate_memory_deletion(item, pending_source_deletion=deletion)
            dependent.append(item)
            pending.append(item)
        if not source_idempotent:
            activity._delete_task_activity_event(deletion)
        for item in pending:
            self._store_memory_deletion(item)
        return TaskActivityDeletionResult(
            deletion,
            tuple(dependent),
            source_idempotent and not pending,
        )

    def get_episodic_memory_deletion(
        self, scope: MemoryScope, memory_id: MemoryId
    ) -> EpisodicMemoryDeletion:
        self._require_scope(scope)
        deletion = self._deletions.get(memory_id)
        if deletion is None or deletion.scope != scope:
            raise EpisodicDeletionNotFound("episodic memory deletion was not found")
        return deletion

    def get_task_activity_deletion(
        self, scope: MemoryScope, event_id: EventId
    ) -> TaskActivityEventDeletion:
        return self._reference_task_activity_events._get_task_activity_deletion(scope, event_id)

    def export_episodic_state(
        self, scope: MemoryScope, *, exported_at: datetime
    ) -> EpisodicExportBundle:
        if not isinstance(scope, MemoryScope) or scope.level is not ScopeLevel.TASK:
            raise InvalidEpisodicExportScope("episodic export requires exact task scope")
        _require_aware_datetime(exported_at, "exported_at")
        activity = self._reference_task_activity_events
        task_events = tuple(
            event
            for event_id, event in activity._events.items()
            if event.scope == scope
            and event_id not in activity._expirations
            and event_id not in activity._deletions
        )
        task_ids = {item.event_id for item in task_events}
        candidates = tuple(
            candidate
            for memory_id, candidate in self._candidates.items()
            if candidate.scope == scope
            and candidate.source_event_id in task_ids
            and memory_id not in self._expirations
            and memory_id not in self._deletions
        )
        candidate_ids = {item.memory_id for item in candidates}
        reviews = tuple(
            review for memory_id, review in self._reviews.items() if memory_id in candidate_ids
        )
        governance_actions = tuple(
            self._governance[action_id]
            for memory_id in sorted(candidate_ids, key=str)
            for action_id in self._governance_order.get(memory_id, ())
        )
        revisions = tuple(
            revision
            for memory_id in sorted(candidate_ids, key=str)
            if (base := self._active.get(memory_id)) is not None
            for revision in self._revisions_for(base)
        )
        return EpisodicExportBundle.create(
            scope=scope,
            exported_at=exported_at,
            task_events=task_events,
            candidates=candidates,
            reviews=reviews,
            governance_actions=governance_actions,
            revisions=revisions,
            memory_expirations=tuple(
                item for item in self._expirations.values() if item.scope == scope
            ),
            memory_purges=tuple(item for item in self._purges.values() if item.scope == scope),
            task_expirations=tuple(
                item for item in activity._expirations.values() if item.scope == scope
            ),
            task_purges=tuple(item for item in activity._purges.values() if item.scope == scope),
            memory_deletions=tuple(
                item for item in self._deletions.values() if item.scope == scope
            ),
            task_deletions=tuple(
                item for item in activity._deletions.values() if item.scope == scope
            ),
        )

    def _validate_memory_deletion(
        self,
        deletion: EpisodicMemoryDeletion,
        *,
        pending_source_deletion: TaskActivityEventDeletion | None = None,
    ) -> bool:
        if not isinstance(deletion, EpisodicMemoryDeletion):
            raise TypeError("episodic memory deletion is invalid")
        self._require_scope(deletion.scope)
        existing = self._deletions.get(deletion.memory_id)
        if existing is not None:
            if existing == deletion:
                return True
            raise EpisodicDeletionConflict("episodic memory already has a different deletion")
        key = (deletion.scope, deletion.source_action_key)
        if key in self._deletion_keys:
            raise EpisodicDeletionConflict("episodic memory deletion action key conflicts")
        candidate = self._candidates.get(deletion.memory_id)
        expiration = self._expirations.get(deletion.memory_id)
        source_event_id: EventId | None = None
        if candidate is not None and candidate.scope == deletion.scope:
            source_event_id = candidate.source_event_id
        elif expiration is not None and expiration.scope == deletion.scope:
            source_event_id = expiration.source_event_id
        if source_event_id is None:
            raise EpisodicDeletionNotFound("episodic memory was not found for deletion")
        if source_event_id != deletion.source_event_id:
            raise EpisodicDeletionConflict("episodic memory deletion source does not match")
        if deletion.cause is EpisodicDeletionCause.SOURCE_DELETED:
            if (
                pending_source_deletion is not None
                and pending_source_deletion.deletion_id == deletion.source_deletion_id
                and pending_source_deletion.event_id == deletion.source_event_id
                and pending_source_deletion.scope == deletion.scope
            ):
                return False
            try:
                source_deletion = self._reference_task_activity_events._get_task_activity_deletion(
                    deletion.scope, deletion.source_event_id
                )
            except EpisodicDeletionNotFound as error:
                raise EpisodicDeletionConflict(
                    "dependent memory deletion has no source deletion"
                ) from error
            if source_deletion.deletion_id != deletion.source_deletion_id:
                raise EpisodicDeletionConflict(
                    "dependent memory deletion source identity conflicts"
                )
        return False

    def _store_memory_deletion(self, deletion: EpisodicMemoryDeletion) -> None:
        self._remove_memory_payload(deletion.memory_id)
        self._deletions[deletion.memory_id] = deletion
        self._deletion_keys[(deletion.scope, deletion.source_action_key)] = deletion.memory_id

    def _remove_memory_payload(self, memory_id: MemoryId) -> None:
        self._candidates.pop(memory_id, None)
        self._ordered = [item for item in self._ordered if item != memory_id]
        review = self._reviews.pop(memory_id, None)
        if review is not None:
            self._review_keys.pop((review.scope, review.source_action_key), None)
        self._active.pop(memory_id, None)
        self._active_ordered = [item for item in self._active_ordered if item != memory_id]
        action_ids = self._governance_order.pop(memory_id, [])
        for action_id in action_ids:
            action = self._governance.pop(action_id)
            self._governance_keys.pop((action.scope, action.source_action_key), None)

    def list_due_task_activity_retention(
        self, scope: MemoryScope, *, as_of: datetime
    ) -> tuple[TaskActivityEventRetentionTarget, ...]:
        return self._task_activity_retention.list_due_task_activity_retention(scope, as_of=as_of)

    def apply_task_activity_expirations(
        self, expirations: tuple[TaskActivityEventExpiration, ...]
    ) -> TaskActivityExpirationResult:
        return self._task_activity_retention.apply_task_activity_expirations(expirations)

    def get_task_activity_expiration(
        self, scope: MemoryScope, event_id: EventId
    ) -> TaskActivityEventExpiration:
        return self._task_activity_retention.get_task_activity_expiration(scope, event_id)

    def list_unpurged_task_activity_expirations(
        self, scope: MemoryScope
    ) -> tuple[TaskActivityEventExpiration, ...]:
        return self._task_activity_retention.list_unpurged_task_activity_expirations(scope)

    def apply_task_activity_purges(
        self, purges: tuple[TaskActivityEventPurge, ...]
    ) -> TaskActivityPurgeResult:
        values = tuple(purges)
        if any(
            candidate.source_event_id == purge.event_id
            for purge in values
            for candidate in self._candidates.values()
        ):
            raise TaskActivityRetentionConflict(
                "task activity event still has dependent episodic candidate payloads"
            )
        return self._task_activity_retention.apply_task_activity_purges(values)

    def get_task_activity_purge(
        self, scope: MemoryScope, event_id: EventId
    ) -> TaskActivityEventPurge:
        return self._task_activity_retention.get_task_activity_purge(scope, event_id)

    @property
    def _task_activity_retention(self) -> TaskActivityRetentionRepository:
        return cast(TaskActivityRetentionRepository, self._activity_events)

    @property
    def _reference_task_activity_events(
        self,
    ) -> ReferenceTaskActivityEventRepository:
        if not isinstance(self._activity_events, ReferenceTaskActivityEventRepository):
            raise TypeError(
                "reference episodic deletion requires the reference task activity adapter"
            )
        return self._activity_events

    def _revisions_for(self, base: ActiveEpisodicMemory) -> tuple[EpisodicMemoryRevision, ...]:
        actions = tuple(
            self._governance[action_id]
            for action_id in self._governance_order.get(base.memory_id, ())
        )
        return replay_episodic_memory_revisions(base, actions)

    @classmethod
    def _validate_batch(
        cls, candidates: tuple[EpisodicMemoryCandidate, ...]
    ) -> tuple[EpisodicMemoryCandidate, ...]:
        values = tuple(candidates)
        if not 1 <= len(values) <= 4 or any(
            not isinstance(candidate, EpisodicMemoryCandidate) for candidate in values
        ):
            raise ValueError("episodic candidate batch is invalid")
        first = values[0]
        cls._require_scope(first.scope)
        if tuple(candidate.proposal_index for candidate in values) != tuple(range(len(values))):
            raise EpisodicMemoryCandidateConflict(
                "episodic candidate proposal indexes must be contiguous"
            )
        if any(
            candidate.scope != first.scope
            or candidate.source_event_id != first.source_event_id
            or candidate.extractor_version != first.extractor_version
            or candidate.provider_id != first.provider_id
            or candidate.model_id != first.model_id
            or candidate.prompt_version != first.prompt_version
            for candidate in values
        ):
            raise EpisodicMemoryCandidateConflict(
                "episodic candidate batch metadata does not match"
            )
        return values

    @staticmethod
    def _require_scope(scope: MemoryScope) -> None:
        if not isinstance(scope, MemoryScope) or scope.level is not ScopeLevel.TASK:
            raise InvalidEpisodicMemoryCandidateScope(
                "episodic candidates require explicit task scope"
            )


class ReferenceApprovedEpisodicEventRepository:
    """Append-only reference store for explicit, evidence-backed task facts."""

    def __init__(
        self,
        outbox: ReferenceEventOutboxRepository | None = None,
        policy: ApprovedEpisodicEventSafetyPolicy | None = None,
    ) -> None:
        self._policy = policy or ApprovedEpisodicEventSafetyPolicy()
        self.outbox = outbox or ReferenceEventOutboxRepository()
        self._events: dict[EventId, ApprovedEpisodicEvent] = {}
        self._keys: dict[tuple[MemoryScope, str], EventId] = {}
        self._governance: dict[EventId, ApprovedEpisodicEventGovernance] = {}
        self._action_keys: dict[tuple[MemoryScope, str], EventId] = {}
        self._pin_actions: dict[EventId, ApprovedEpisodicEventPinAction] = {}
        self._pin_action_keys: dict[tuple[MemoryScope, str], EventId] = {}
        self._current_pins: dict[EventId, ApprovedEpisodicEventPinAction] = {}
        self._ordered: list[EventId] = []

    def append_approved_event(
        self, event: ApprovedEpisodicEvent
    ) -> ApprovedEpisodicEventStoreResult:
        self._require_scope(event.scope)
        if not self._policy.assess_event(event).accepted:
            raise ApprovedEpisodicEventSecretRejected(
                "approved episodic event was rejected by deterministic secret policy"
            )
        key = (event.scope, event.source_event_key)
        existing_id = self._keys.get(key)
        if existing_id is not None:
            existing = self._events[existing_id]
            if existing == event:
                return ApprovedEpisodicEventStoreResult(existing, True)
            raise ApprovedEpisodicEventConflict("approved episodic event key conflicts")
        if event.event_id in self._governance:
            raise ApprovedEpisodicEventConflict("retracted approved event cannot be restored")
        state = self._snapshot()
        try:
            self._events[event.event_id] = event
            self._keys[key] = event.event_id
            self._ordered.append(event.event_id)
            self.outbox._enqueue(self._event_job(event))
        except BaseException:
            self._restore(state)
            raise
        return ApprovedEpisodicEventStoreResult(event, False)

    def get_approved_event(self, scope: MemoryScope, event_id: EventId) -> ApprovedEpisodicEvent:
        self._require_scope(scope)
        event = self._events.get(event_id)
        if event is None or event.scope != scope:
            raise ApprovedEpisodicEventNotFound("approved episodic event was not found")
        return event

    def list_approved_events(
        self, scope: MemoryScope, *, offset: int = 0, limit: int = 50
    ) -> ApprovedEpisodicEventPage:
        self._require_scope(scope)
        if offset < 0 or limit < 1:
            raise ValueError("event offset must be non-negative and limit must be positive")
        items = tuple(
            self._events[event_id]
            for event_id in reversed(self._ordered)
            if event_id in self._events
            and self._events[event_id].scope == scope
            and event_id not in self._governance
        )
        items = tuple(sorted(items, key=lambda item: not self._is_pinned(item.event_id)))
        return ApprovedEpisodicEventPage(
            items[offset : offset + limit],
            offset + limit if offset + limit < len(items) else None,
        )

    def correct_approved_event(
        self,
        replacement: ApprovedEpisodicEvent,
        governance: ApprovedEpisodicEventGovernance,
    ) -> ApprovedEpisodicEventGovernanceResult:
        self._validate_governance(replacement.scope, governance)
        if (
            not self._policy.assess_event(replacement).accepted
            or not self._policy.assess_governance(governance).accepted
        ):
            raise ApprovedEpisodicEventSecretRejected(
                "approved episodic event correction was rejected by secret policy"
            )
        if governance.kind is not ApprovedEventGovernanceKind.CORRECTED:
            raise ApprovedEpisodicEventConflict("approved event correction action is invalid")
        if replacement.event_id != governance.replacement_event_id:
            raise ApprovedEpisodicEventConflict("approved event replacement does not match action")
        existing = self._governance.get(governance.target_event_id)
        if existing is not None:
            replacement_record = self.get_approved_event_record(
                governance.scope, replacement.event_id
            )
            if existing.same_intent(governance) and (
                replacement_record.event is None
                or self._same_event_intent(replacement_record.event, replacement)
            ):
                return ApprovedEpisodicEventGovernanceResult(
                    self.get_approved_event_record(governance.scope, governance.target_event_id),
                    replacement_record,
                    True,
                )
            raise ApprovedEpisodicEventConflict("approved event already has a governance action")
        target = self._events.get(governance.target_event_id)
        if target is None or target.scope != governance.scope:
            raise ApprovedEpisodicEventNotFound("approved episodic event was not found")
        if replacement.kind is not target.kind:
            raise ApprovedEpisodicEventConflict("approved event correction cannot change kind")
        if (
            replacement.event_id in self._events
            or replacement.event_id in self._governance
            or (replacement.scope, replacement.source_event_key) in self._keys
        ):
            raise ApprovedEpisodicEventConflict("approved event replacement conflicts")
        self._require_unused_action_key(governance)
        state = self._snapshot()
        try:
            self._events[replacement.event_id] = replacement
            self._keys[(replacement.scope, replacement.source_event_key)] = replacement.event_id
            self._ordered.append(replacement.event_id)
            self._governance[governance.target_event_id] = governance
            self._action_keys[(governance.scope, governance.source_action_key)] = (
                governance.action_id
            )
            pin_actions = self._transfer_pin(governance, replacement.event_id)
            self.outbox._enqueue_many(
                (
                    self._event_job(replacement),
                    self._governance_job(governance),
                    *(self._pin_job(action) for action in pin_actions),
                )
            )
        except BaseException:
            self._restore(state)
            raise
        return ApprovedEpisodicEventGovernanceResult(
            self.get_approved_event_record(governance.scope, governance.target_event_id),
            self.get_approved_event_record(governance.scope, replacement.event_id),
            False,
        )

    def retract_approved_event(
        self, governance: ApprovedEpisodicEventGovernance
    ) -> ApprovedEpisodicEventGovernanceResult:
        self._validate_governance(governance.scope, governance)
        if not self._policy.assess_governance(governance).accepted:
            raise ApprovedEpisodicEventSecretRejected(
                "approved episodic event retraction was rejected by secret policy"
            )
        if governance.kind is not ApprovedEventGovernanceKind.RETRACTED:
            raise ApprovedEpisodicEventConflict("approved event retraction action is invalid")
        existing = self._governance.get(governance.target_event_id)
        if existing is not None:
            if existing.same_intent(governance):
                return ApprovedEpisodicEventGovernanceResult(
                    self.get_approved_event_record(governance.scope, governance.target_event_id),
                    None,
                    True,
                )
            raise ApprovedEpisodicEventConflict("approved event already has a governance action")
        target = self._events.get(governance.target_event_id)
        if target is None or target.scope != governance.scope:
            raise ApprovedEpisodicEventNotFound("approved episodic event was not found")
        self._require_unused_action_key(governance)
        state = self._snapshot()
        try:
            self._governance[governance.target_event_id] = governance
            self._action_keys[(governance.scope, governance.source_action_key)] = (
                governance.action_id
            )
            pin_action = self._remove_pin_for_retraction(governance)
            del self._events[target.event_id]
            del self._keys[(target.scope, target.source_event_key)]
            self.outbox._enqueue_many(
                (
                    self._governance_job(governance),
                    *(() if pin_action is None else (self._pin_job(pin_action),)),
                )
            )
        except BaseException:
            self._restore(state)
            raise
        return ApprovedEpisodicEventGovernanceResult(
            self.get_approved_event_record(governance.scope, governance.target_event_id),
            None,
            False,
        )

    def get_approved_event_record(
        self, scope: MemoryScope, event_id: EventId
    ) -> ApprovedEpisodicEventRecord:
        self._require_scope(scope)
        event = self._events.get(event_id)
        governance = self._governance.get(event_id)
        if event is not None and event.scope == scope:
            status = (
                ApprovedEventLifecycleStatus.ACTIVE
                if governance is None
                else ApprovedEventLifecycleStatus.CORRECTED
            )
            return ApprovedEpisodicEventRecord(
                event_id,
                scope,
                status,
                event,
                governance,
                status is ApprovedEventLifecycleStatus.ACTIVE and self._is_pinned(event_id),
            )
        if governance is not None and governance.scope == scope:
            return ApprovedEpisodicEventRecord(
                event_id,
                scope,
                ApprovedEventLifecycleStatus.RETRACTED,
                None,
                governance,
            )
        raise ApprovedEpisodicEventNotFound("approved episodic event was not found")

    def list_approved_event_records(
        self, scope: MemoryScope, *, offset: int = 0, limit: int = 50
    ) -> ApprovedEpisodicEventRecordPage:
        self._require_scope(scope)
        if offset < 0 or limit < 1:
            raise ValueError("event offset must be non-negative and limit must be positive")
        records = tuple(
            self.get_approved_event_record(scope, event_id)
            for event_id in reversed(self._ordered)
            if (
                (event_id in self._events and self._events[event_id].scope == scope)
                or (event_id in self._governance and self._governance[event_id].scope == scope)
            )
        )
        return ApprovedEpisodicEventRecordPage(
            records[offset : offset + limit],
            offset + limit if offset + limit < len(records) else None,
        )

    def set_approved_event_pin(
        self, action: ApprovedEpisodicEventPinAction
    ) -> ApprovedEpisodicEventPinResult:
        self._require_scope(action.scope)
        if not self._policy.assess_pin(action).accepted:
            raise ApprovedEpisodicEventSecretRejected(
                "approved episodic event pin was rejected by secret policy"
            )
        existing = self._pin_actions.get(action.action_id)
        if existing is not None:
            if not existing.same_intent(action):
                raise ApprovedEpisodicEventConflict("approved event pin action conflicts")
            return ApprovedEpisodicEventPinResult(
                existing,
                self.get_approved_event_record(action.scope, action.event_id),
                True,
            )
        if (action.scope, action.source_action_key) in self._pin_action_keys:
            raise ApprovedEpisodicEventConflict("approved event pin action key conflicts")
        event = self._events.get(action.event_id)
        if event is None or event.scope != action.scope or action.event_id in self._governance:
            raise ApprovedEpisodicEventNotFound("approved episodic event was not found")
        state = self._snapshot()
        try:
            self._store_pin_action(action)
            self.outbox._enqueue(self._pin_job(action))
        except BaseException:
            self._restore(state)
            raise
        return ApprovedEpisodicEventPinResult(
            action,
            self.get_approved_event_record(action.scope, action.event_id),
            False,
        )

    def _is_pinned(self, event_id: EventId) -> bool:
        action = self._current_pins.get(event_id)
        return action is not None and action.pinned

    def _store_pin_action(self, action: ApprovedEpisodicEventPinAction) -> None:
        self._pin_actions[action.action_id] = action
        self._pin_action_keys[(action.scope, action.source_action_key)] = action.action_id
        self._current_pins[action.event_id] = action

    def _transfer_pin(
        self, governance: ApprovedEpisodicEventGovernance, replacement_event_id: EventId
    ) -> tuple[ApprovedEpisodicEventPinAction, ...]:
        current = self._current_pins.get(governance.target_event_id)
        if current is None or not current.pinned:
            return ()
        actions: list[ApprovedEpisodicEventPinAction] = []
        for event_id, pinned, suffix in (
            (governance.target_event_id, False, "released"),
            (replacement_event_id, True, "transferred"),
        ):
            action = ApprovedEpisodicEventPinAction.create(
                scope=governance.scope,
                event_id=event_id,
                pinned=pinned,
                source_action_key=f"governance-pin-{suffix}:{governance.action_id}",
                occurred_at=governance.occurred_at,
                evidence_references=current.evidence_references,
            )
            self._store_pin_action(action)
            actions.append(action)
        return tuple(actions)

    def _remove_pin_for_retraction(
        self, governance: ApprovedEpisodicEventGovernance
    ) -> ApprovedEpisodicEventPinAction | None:
        current = self._current_pins.get(governance.target_event_id)
        if current is None or not current.pinned:
            return None
        action = ApprovedEpisodicEventPinAction.create(
            scope=governance.scope,
            event_id=governance.target_event_id,
            pinned=False,
            source_action_key=f"governance-pin-retracted:{governance.action_id}",
            occurred_at=governance.occurred_at,
            evidence_references=current.evidence_references,
        )
        self._store_pin_action(action)
        return action

    def _validate_governance(
        self, scope: MemoryScope, governance: ApprovedEpisodicEventGovernance
    ) -> None:
        self._require_scope(scope)
        if governance.scope != scope:
            raise InvalidApprovedEpisodicEventScope(
                "approved event governance requires one complete task scope"
            )

    def _require_unused_action_key(self, governance: ApprovedEpisodicEventGovernance) -> None:
        if (governance.scope, governance.source_action_key) in self._action_keys:
            raise ApprovedEpisodicEventConflict("approved event action key conflicts")

    def _snapshot(
        self,
    ) -> tuple[
        dict[EventId, ApprovedEpisodicEvent],
        dict[tuple[MemoryScope, str], EventId],
        dict[EventId, ApprovedEpisodicEventGovernance],
        dict[tuple[MemoryScope, str], EventId],
        dict[EventId, ApprovedEpisodicEventPinAction],
        dict[tuple[MemoryScope, str], EventId],
        dict[EventId, ApprovedEpisodicEventPinAction],
        list[EventId],
    ]:
        return (
            dict(self._events),
            dict(self._keys),
            dict(self._governance),
            dict(self._action_keys),
            dict(self._pin_actions),
            dict(self._pin_action_keys),
            dict(self._current_pins),
            list(self._ordered),
        )

    def _restore(
        self,
        state: tuple[
            dict[EventId, ApprovedEpisodicEvent],
            dict[tuple[MemoryScope, str], EventId],
            dict[EventId, ApprovedEpisodicEventGovernance],
            dict[tuple[MemoryScope, str], EventId],
            dict[EventId, ApprovedEpisodicEventPinAction],
            dict[tuple[MemoryScope, str], EventId],
            dict[EventId, ApprovedEpisodicEventPinAction],
            list[EventId],
        ],
    ) -> None:
        (
            self._events,
            self._keys,
            self._governance,
            self._action_keys,
            self._pin_actions,
            self._pin_action_keys,
            self._current_pins,
            self._ordered,
        ) = state

    @staticmethod
    def _event_job(event: ApprovedEpisodicEvent) -> EventOutboxJob:
        return EventOutboxJob.create(
            scope=event.scope,
            topic=EventOutboxTopic.APPROVED_EPISODIC,
            source_event_id=event.event_id,
            event_kind=event.kind.value,
            occurred_at=event.occurred_at,
            created_at=event.occurred_at,
        )

    @staticmethod
    def _governance_job(governance: ApprovedEpisodicEventGovernance) -> EventOutboxJob:
        return EventOutboxJob.create(
            scope=governance.scope,
            topic=EventOutboxTopic.APPROVED_GOVERNANCE,
            source_event_id=governance.action_id,
            event_kind=governance.kind.value,
            occurred_at=governance.occurred_at,
            created_at=governance.occurred_at,
        )

    @staticmethod
    def _pin_job(action: ApprovedEpisodicEventPinAction) -> EventOutboxJob:
        return EventOutboxJob.create(
            scope=action.scope,
            topic=EventOutboxTopic.APPROVED_GOVERNANCE,
            source_event_id=action.action_id,
            event_kind="pinned" if action.pinned else "unpinned",
            occurred_at=action.occurred_at,
            created_at=action.occurred_at,
        )

    @staticmethod
    def _same_event_intent(first: ApprovedEpisodicEvent, second: ApprovedEpisodicEvent) -> bool:
        return (
            first.event_id,
            first.scope,
            first.kind,
            first.summary,
            first.source_event_key,
        ) == (
            second.event_id,
            second.scope,
            second.kind,
            second.summary,
            second.source_event_key,
        )

    @staticmethod
    def _require_scope(scope: MemoryScope) -> None:
        if scope.level is not ScopeLevel.TASK:
            raise InvalidApprovedEpisodicEventScope(
                "approved episodic events require explicit task scope"
            )


class ReferenceCheckpointLifecycleEventRepository:
    """Validate-before-mutate reference ledger backed by immutable checkpoint revisions."""

    def __init__(
        self,
        checkpoints: CheckpointRepository,
        outbox: ReferenceEventOutboxRepository | None = None,
    ) -> None:
        self._checkpoints = checkpoints
        self.outbox = outbox or ReferenceEventOutboxRepository()
        self._events: dict[EventId, CheckpointLifecycleEvent] = {}
        self._keys: dict[str, EventId] = {}
        self._ordered: list[EventId] = []

    def append_event(self, event: CheckpointLifecycleEvent) -> EpisodicEventStoreResult:
        self._require_scope(event.scope)
        revision = self._checkpoints.get_revision(
            event.scope,
            event.checkpoint_id,
            revision_id=event.revision_id,
        )
        if (
            revision.revision_number != event.revision_number
            or revision.created_at != event.occurred_at
            or revision.evidence_references != event.evidence_references
        ):
            raise InvalidEpisodicEventScope("event does not match its scoped checkpoint revision")
        existing_id = self._keys.get(event.idempotency_key)
        if existing_id is not None:
            existing = self._events[existing_id]
            if existing == event:
                return EpisodicEventStoreResult(existing, idempotent=True)
            raise InvalidEpisodicEventScope("event idempotency key conflicts")
        if event.event_id in self._events:
            raise InvalidEpisodicEventScope("event identity conflicts")
        state = self._snapshot()
        try:
            self._events[event.event_id] = event
            self._keys[event.idempotency_key] = event.event_id
            self._ordered.append(event.event_id)
            self.outbox._enqueue(
                EventOutboxJob.create(
                    scope=event.scope,
                    topic=EventOutboxTopic.CHECKPOINT_LIFECYCLE,
                    source_event_id=event.event_id,
                    event_kind=event.kind.value,
                    occurred_at=event.occurred_at,
                    created_at=event.occurred_at,
                )
            )
        except BaseException:
            self._restore(state)
            raise
        return EpisodicEventStoreResult(event, idempotent=False)

    def get_event(self, scope: MemoryScope, event_id: EventId) -> CheckpointLifecycleEvent:
        self._require_scope(scope)
        event = self._events.get(event_id)
        if event is None or event.scope != scope:
            raise EpisodicEventNotFound("episodic event was not found")
        return event

    def list_events(
        self,
        scope: MemoryScope,
        *,
        checkpoint_id: CheckpointId | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> EpisodicEventPage:
        self._require_scope(scope)
        if offset < 0 or limit < 1:
            raise ValueError("event offset must be non-negative and limit must be positive")
        events = [
            self._events[event_id]
            for event_id in reversed(self._ordered)
            if self._events[event_id].scope == scope
            and (checkpoint_id is None or self._events[event_id].checkpoint_id == checkpoint_id)
        ]
        return EpisodicEventPage(
            tuple(events[offset : offset + limit]),
            offset + limit if offset + limit < len(events) else None,
        )

    def _snapshot(
        self,
    ) -> tuple[
        dict[EventId, CheckpointLifecycleEvent],
        dict[str, EventId],
        list[EventId],
    ]:
        """Return private copies so the reference aggregate can roll back compound writes."""
        return (
            dict(self._events),
            dict(self._keys),
            list(self._ordered),
        )

    def _restore(
        self,
        state: tuple[
            dict[EventId, CheckpointLifecycleEvent],
            dict[str, EventId],
            list[EventId],
        ],
    ) -> None:
        self._events, self._keys, self._ordered = state

    @staticmethod
    def _require_scope(scope: MemoryScope) -> None:
        if not isinstance(scope, MemoryScope) or scope.level is not ScopeLevel.TASK:
            raise InvalidEpisodicEventScope("episodic events require explicit task scope")


class ReferenceCheckpointSourceObservationRepository:
    """Reference association store that validates both immutable sides before mutation."""

    def __init__(
        self, checkpoints: CheckpointRepository, source: SourceStructureRepository
    ) -> None:
        self._checkpoints = checkpoints
        self._source = source
        self._observations: dict[CheckpointRevisionId, CheckpointSourceObservation] = {}

    def append_checkpoint_source_observation(
        self, observation: CheckpointSourceObservation
    ) -> CheckpointSourceObservationStoreResult:
        if observation.scope.level is not ScopeLevel.TASK:
            raise CheckpointSourceObservationNotFound("checkpoint source observation was not found")
        try:
            revision = self._checkpoints.get_revision(
                observation.scope,
                observation.checkpoint_id,
                revision_id=observation.revision_id,
            )
        except CheckpointNotFound as error:
            raise CheckpointSourceObservationNotFound(
                "checkpoint revision was not found"
            ) from error
        if revision.revision_id != observation.revision_id:
            raise CheckpointSourceObservationNotFound("checkpoint revision was not found")
        project_scope = MemoryScope(
            observation.scope.owner_id,
            ScopeLevel.PROJECT,
            observation.scope.visibility,
            observation.scope.workspace_id,
            observation.scope.project_id,
        )
        try:
            self._source.get_snapshot(project_scope, observation.source_snapshot_id)
        except SourceSnapshotNotFound as error:
            raise CheckpointSourceObservationNotFound("source snapshot was not found") from error
        existing = self._observations.get(observation.revision_id)
        if existing is not None:
            if existing == observation:
                return CheckpointSourceObservationStoreResult(existing, True)
            raise CheckpointSourceObservationConflict(
                "checkpoint revision already has a source observation"
            )
        self._observations[observation.revision_id] = observation
        return CheckpointSourceObservationStoreResult(observation, False)

    def get_checkpoint_source_observation(
        self,
        scope: MemoryScope,
        checkpoint_id: CheckpointId,
        revision_id: CheckpointRevisionId,
    ) -> CheckpointSourceObservation:
        if not isinstance(scope, MemoryScope) or scope.level is not ScopeLevel.TASK:
            raise CheckpointSourceObservationNotFound("checkpoint source observation was not found")
        observation = self._observations.get(revision_id)
        if (
            observation is None
            or observation.scope != scope
            or observation.checkpoint_id != checkpoint_id
        ):
            raise CheckpointSourceObservationNotFound("checkpoint source observation was not found")
        return observation


class ReferenceCheckpointRepository:
    """Storage-independent behavior reference with validate-before-mutate writes."""

    def __init__(self) -> None:
        self._aggregates: dict[CheckpointId, CheckpointAggregate] = {}
        self._revisions: dict[CheckpointId, tuple[CheckpointRevision, ...]] = {}
        self.outbox = ReferenceEventOutboxRepository()
        self.events = ReferenceCheckpointLifecycleEventRepository(self, self.outbox)
        self.approved_events = ReferenceApprovedEpisodicEventRepository(self.outbox)
        self.activity_events = ReferenceTaskActivityEventRepository(self.outbox)
        self.episodic_candidates = ReferenceEpisodicMemoryCandidateRepository(self.activity_events)

    def create_checkpoint_aggregate(
        self, aggregate: CheckpointAggregate, initial_revision: CheckpointRevision
    ) -> None:
        self._require_scope(aggregate.scope)
        if aggregate.checkpoint_id in self._aggregates:
            raise DuplicateCheckpoint()
        if (
            initial_revision.checkpoint_id != aggregate.checkpoint_id
            or initial_revision.scope != aggregate.scope
            or initial_revision.revision_number != 1
            or initial_revision.predecessor_revision_id is not None
            or aggregate.current_revision_id != initial_revision.revision_id
            or aggregate.current_revision_number != 1
            or aggregate.lifecycle_status is not CheckpointStatus.ACTIVE
            or initial_revision.status is not CheckpointStatus.ACTIVE
        ):
            raise InvalidLifecycleTransition(
                "initial aggregate and revision must be active revision one"
            )
        # Both assignments happen only after all validation succeeds.
        event_state = self.events._snapshot()
        try:
            self._aggregates[aggregate.checkpoint_id] = aggregate
            self._revisions[aggregate.checkpoint_id] = (initial_revision,)
            self.events.append_event(
                CheckpointLifecycleEvent.for_revision(
                    scope=aggregate.scope,
                    kind=CheckpointEventKind.CREATED,
                    checkpoint_id=initial_revision.checkpoint_id,
                    revision_id=initial_revision.revision_id,
                    revision_number=initial_revision.revision_number,
                    occurred_at=initial_revision.created_at,
                    evidence_references=initial_revision.evidence_references,
                )
            )
        except BaseException:
            self._aggregates.pop(aggregate.checkpoint_id, None)
            self._revisions.pop(aggregate.checkpoint_id, None)
            self.events._restore(event_state)
            raise

    def get_aggregate(self, scope: MemoryScope, checkpoint_id: CheckpointId) -> CheckpointAggregate:
        self._require_scope(scope)
        aggregate = self._aggregates.get(checkpoint_id)
        if aggregate is None or aggregate.scope != scope:
            raise CheckpointNotFound()
        return aggregate

    def get_current_revision(
        self, scope: MemoryScope, checkpoint_id: CheckpointId
    ) -> CheckpointRevision:
        aggregate = self.get_aggregate(scope, checkpoint_id)
        return self._revisions[checkpoint_id][aggregate.current_revision_number - 1]

    def get_revision(
        self,
        scope: MemoryScope,
        checkpoint_id: CheckpointId,
        *,
        revision_number: int | None = None,
        revision_id: CheckpointRevisionId | None = None,
    ) -> CheckpointRevision:
        self.get_aggregate(scope, checkpoint_id)
        if (revision_number is None) == (revision_id is None):
            raise ValueError("provide exactly one revision selector")
        revisions = self._revisions[checkpoint_id]
        if revision_number is not None:
            if revision_number < 1 or revision_number > len(revisions):
                raise CheckpointNotFound()
            return revisions[revision_number - 1]
        assert revision_id is not None
        for revision in revisions:
            if revision.revision_id == revision_id:
                return revision
        raise CheckpointNotFound()

    def append_revision(
        self,
        scope: MemoryScope,
        checkpoint_id: CheckpointId,
        expected_revision_id: CheckpointRevisionId,
        content: CheckpointContent,
        evidence_references: tuple[EvidenceReference, ...],
        created_at: datetime,
        event_kind: CheckpointEventKind = CheckpointEventKind.REVISED,
    ) -> CheckpointRevision:
        aggregate = self.get_aggregate(scope, checkpoint_id)
        self._require_active_expected(aggregate, expected_revision_id)
        current = self.get_current_revision(scope, checkpoint_id)
        revision = CheckpointRevision(
            revision_id=CheckpointRevisionId.new(),
            checkpoint_id=checkpoint_id,
            revision_number=current.revision_number + 1,
            predecessor_revision_id=current.revision_id,
            scope=scope,
            content=content,
            status=CheckpointStatus.ACTIVE,
            evidence_references=evidence_references,
            created_at=created_at,
        )
        self._replace_current_with_event(aggregate, revision, event_kind)
        return revision

    def complete_checkpoint(
        self,
        scope: MemoryScope,
        checkpoint_id: CheckpointId,
        expected_revision_id: CheckpointRevisionId,
        content: CheckpointContent,
        evidence_references: tuple[EvidenceReference, ...],
        created_at: datetime,
    ) -> CheckpointRevision:
        return self._transition(
            scope,
            checkpoint_id,
            expected_revision_id,
            CheckpointStatus.COMPLETED,
            content,
            evidence_references,
            created_at,
            reason=None,
            event_kind=CheckpointEventKind.COMPLETED,
        )

    def abandon_checkpoint(
        self,
        scope: MemoryScope,
        checkpoint_id: CheckpointId,
        expected_revision_id: CheckpointRevisionId,
        reason: str,
        content: CheckpointContent,
        evidence_references: tuple[EvidenceReference, ...],
        created_at: datetime,
    ) -> CheckpointRevision:
        if not isinstance(reason, str) or not reason.strip():
            raise InvalidAbandonmentReason("abandonment reason must not be blank")
        terminal_content = content
        if reason not in terminal_content.failures:
            terminal_content = replace(
                terminal_content, failures=(*terminal_content.failures, reason)
            )
        return self._transition(
            scope,
            checkpoint_id,
            expected_revision_id,
            CheckpointStatus.ABANDONED,
            terminal_content,
            evidence_references,
            created_at,
            reason=reason,
            event_kind=CheckpointEventKind.ABANDONED,
        )

    def list_current_checkpoints(
        self, scope: MemoryScope, *, offset: int = 0, limit: int = 50
    ) -> CheckpointPage:
        self._require_scope(scope)
        if offset < 0 or limit < 1:
            raise ValueError("offset must be non-negative and limit must be positive")
        active = [
            aggregate
            for aggregate in self._aggregates.values()
            if aggregate.scope == scope and aggregate.lifecycle_status is CheckpointStatus.ACTIVE
        ]
        active.sort(key=lambda item: (-item.updated_at.timestamp(), str(item.checkpoint_id)))
        items = tuple(active[offset : offset + limit])
        next_offset = offset + limit if offset + limit < len(active) else None
        return CheckpointPage(items=items, next_offset=next_offset)

    def select_current_checkpoint(self, scope: MemoryScope) -> CheckpointAggregate | None:
        items = self.list_current_checkpoints(scope, limit=1).items
        return items[0] if items else None

    def _transition(
        self,
        scope: MemoryScope,
        checkpoint_id: CheckpointId,
        expected_revision_id: CheckpointRevisionId,
        status: CheckpointStatus,
        content: CheckpointContent,
        evidence_references: tuple[EvidenceReference, ...],
        created_at: datetime,
        *,
        reason: str | None,
        event_kind: CheckpointEventKind,
    ) -> CheckpointRevision:
        aggregate = self.get_aggregate(scope, checkpoint_id)
        current = self.get_current_revision(scope, checkpoint_id)
        if aggregate.lifecycle_status is not CheckpointStatus.ACTIVE:
            if self._is_identical_terminal_retry(
                current, expected_revision_id, status, content, evidence_references, reason
            ):
                return current
            raise InvalidLifecycleTransition("checkpoint is already terminal")
        self._require_active_expected(aggregate, expected_revision_id)
        if status is CheckpointStatus.COMPLETED and (content.blockers or content.remaining_work):
            raise InvalidLifecycleTransition(
                "completed checkpoint cannot contain blockers or remaining work"
            )
        revision = CheckpointRevision(
            revision_id=CheckpointRevisionId.new(),
            checkpoint_id=checkpoint_id,
            revision_number=current.revision_number + 1,
            predecessor_revision_id=current.revision_id,
            scope=scope,
            content=content,
            status=status,
            evidence_references=evidence_references,
            created_at=created_at,
        )
        self._replace_current_with_event(aggregate, revision, event_kind)
        return revision

    def _replace_current_with_event(
        self,
        aggregate: CheckpointAggregate,
        revision: CheckpointRevision,
        event_kind: CheckpointEventKind,
    ) -> None:
        """Advance the reference aggregate and ledger as one caller-visible write."""
        previous_aggregate = self._aggregates[aggregate.checkpoint_id]
        previous_revisions = self._revisions[aggregate.checkpoint_id]
        event_state = self.events._snapshot()
        try:
            self._replace_current(aggregate, revision)
            self.events.append_event(
                CheckpointLifecycleEvent.for_revision(
                    scope=revision.scope,
                    kind=event_kind,
                    checkpoint_id=revision.checkpoint_id,
                    revision_id=revision.revision_id,
                    revision_number=revision.revision_number,
                    occurred_at=revision.created_at,
                    evidence_references=revision.evidence_references,
                )
            )
        except BaseException:
            self._aggregates[aggregate.checkpoint_id] = previous_aggregate
            self._revisions[aggregate.checkpoint_id] = previous_revisions
            self.events._restore(event_state)
            raise

    def _require_active_expected(
        self, aggregate: CheckpointAggregate, expected_revision_id: CheckpointRevisionId
    ) -> None:
        if aggregate.lifecycle_status is not CheckpointStatus.ACTIVE:
            raise InvalidLifecycleTransition("checkpoint is not active")
        if aggregate.current_revision_id != expected_revision_id:
            raise RevisionConflict("expected revision is not current")

    def _replace_current(
        self, aggregate: CheckpointAggregate, revision: CheckpointRevision
    ) -> None:
        next_aggregate = replace(
            aggregate,
            current_revision_id=revision.revision_id,
            current_revision_number=revision.revision_number,
            lifecycle_status=revision.status,
            updated_at=revision.created_at,
        )
        next_revisions = self._revisions[aggregate.checkpoint_id] + (revision,)
        # Build all immutable replacements before changing either map.
        previous_revisions = self._revisions[aggregate.checkpoint_id]
        previous_aggregate = self._aggregates[aggregate.checkpoint_id]
        try:
            self._revisions[aggregate.checkpoint_id] = next_revisions
            self._aggregates[aggregate.checkpoint_id] = next_aggregate
        except BaseException:
            self._revisions[aggregate.checkpoint_id] = previous_revisions
            self._aggregates[aggregate.checkpoint_id] = previous_aggregate
            raise

    def _is_identical_terminal_retry(
        self,
        current: CheckpointRevision,
        expected_revision_id: CheckpointRevisionId,
        status: CheckpointStatus,
        content: CheckpointContent,
        evidence_references: tuple[EvidenceReference, ...],
        reason: str | None,
    ) -> bool:
        return (
            current.status is status
            and current.predecessor_revision_id == expected_revision_id
            and current.content == content
            and current.evidence_references == tuple(evidence_references)
            and (reason is None or reason in current.content.failures)
        )

    @staticmethod
    def _require_scope(scope: MemoryScope) -> None:
        if not isinstance(scope, MemoryScope) or scope.level is not ScopeLevel.TASK:
            raise InvalidCheckpointScope("checkpoint operations require explicit task scope")


class ReferenceProjectIndexRepository:
    """Behavior reference for immutable project-scoped dbt artifact snapshots."""

    def __init__(self) -> None:
        self._artifacts: dict[DbtSnapshotId, DbtManifestArtifact] = {}
        self._snapshots: dict[DbtSnapshotId, DbtManifestSnapshot] = {}
        self._active: dict[MemoryScope, DbtSnapshotId] = {}
        self._activations: dict[MemoryScope, list[DbtSnapshotId]] = {}
        self._catalogs: dict[tuple[DbtSnapshotId, str], DbtCatalogArtifact] = {}
        self._run_results: dict[tuple[DbtSnapshotId, str], DbtRunResultsArtifact] = {}
        self._source_freshness: dict[tuple[DbtSnapshotId, str], DbtSourceFreshnessArtifact] = {}
        self._active_catalog: dict[DbtSnapshotId, str] = {}
        self._active_run_results: dict[DbtSnapshotId, str] = {}
        self._active_source_freshness: dict[DbtSnapshotId, str] = {}

    def store_and_activate(
        self,
        artifact: DbtManifestArtifact,
        snapshot_id: DbtSnapshotId,
        *,
        expected_active_snapshot_id: DbtSnapshotId | None = None,
    ) -> ManifestSnapshotStoreResult:
        self._require_scope(artifact.scope)
        active = self._active.get(artifact.scope)
        if expected_active_snapshot_id != active and not (
            expected_active_snapshot_id is None and active is None
        ):
            raise ActiveSnapshotConflict("expected active snapshot is not current")
        for existing_id, existing in self._artifacts.items():
            if (
                existing.scope == artifact.scope
                and existing.metadata.content_digest == artifact.metadata.content_digest
            ):
                snapshot = self._snapshots[existing_id]
                if active != existing_id:
                    self._active[artifact.scope] = existing_id
                    snapshot = replace(snapshot, is_active=True)
                    self._snapshots[existing_id] = snapshot
                    if active is not None:
                        self._snapshots[active] = replace(self._snapshots[active], is_active=False)
                    self._activations.setdefault(artifact.scope, []).append(existing_id)
                return ManifestSnapshotStoreResult(snapshot=snapshot, idempotent=True)
        if snapshot_id in self._snapshots:
            raise ActiveSnapshotConflict("snapshot identity already exists")
        snapshot = DbtManifestSnapshot(
            snapshot_id=snapshot_id,
            scope=artifact.scope,
            metadata=artifact.metadata,
            node_count=len(artifact.nodes),
            edge_count=len(artifact.edges),
            is_active=True,
        )
        previous = self._active.get(artifact.scope)
        try:
            self._artifacts[snapshot_id] = artifact
            self._snapshots[snapshot_id] = snapshot
            self._active[artifact.scope] = snapshot_id
            if previous is not None:
                self._snapshots[previous] = replace(self._snapshots[previous], is_active=False)
            self._activations.setdefault(artifact.scope, []).append(snapshot_id)
        except BaseException:
            self._artifacts.pop(snapshot_id, None)
            self._snapshots.pop(snapshot_id, None)
            if previous is None:
                self._active.pop(artifact.scope, None)
            else:
                self._active[artifact.scope] = previous
                self._snapshots[previous] = replace(self._snapshots[previous], is_active=True)
            activations = self._activations.get(artifact.scope)
            if activations and activations[-1] == snapshot_id:
                activations.pop()
            raise
        return ManifestSnapshotStoreResult(snapshot=snapshot, idempotent=False)

    def get_snapshot(self, scope: MemoryScope, snapshot_id: DbtSnapshotId) -> DbtManifestSnapshot:
        self._require_scope(scope)
        snapshot = self._snapshots.get(snapshot_id)
        if snapshot is None or snapshot.scope != scope:
            raise ManifestSnapshotNotFound("manifest snapshot was not found")
        return snapshot

    def get_active_snapshot(self, scope: MemoryScope) -> DbtManifestSnapshot | None:
        self._require_scope(scope)
        snapshot_id = self._active.get(scope)
        return None if snapshot_id is None else self._snapshots[snapshot_id]

    def latest_transition(
        self, scope: MemoryScope
    ) -> tuple[DbtManifestSnapshot, DbtManifestSnapshot] | None:
        self._require_scope(scope)
        values = self._activations.get(scope, [])
        if len(values) < 2:
            return None
        return self._snapshots[values[-2]], self._snapshots[values[-1]]

    def store_catalog_projection(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, artifact: DbtCatalogArtifact
    ) -> SupplementalArtifactStoreResult:
        return self._store_supplemental(
            scope,
            snapshot_id,
            artifact,
            self._catalogs,
            self._active_catalog,
            tuple(item.unique_id for item in artifact.relations),
        )

    def store_run_results_projection(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, artifact: DbtRunResultsArtifact
    ) -> SupplementalArtifactStoreResult:
        return self._store_supplemental(
            scope,
            snapshot_id,
            artifact,
            self._run_results,
            self._active_run_results,
            tuple(item.unique_id for item in artifact.results),
        )

    def store_source_freshness_projection(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, artifact: DbtSourceFreshnessArtifact
    ) -> SupplementalArtifactStoreResult:
        return self._store_supplemental(
            scope,
            snapshot_id,
            artifact,
            self._source_freshness,
            self._active_source_freshness,
            tuple(item.unique_id for item in artifact.results),
        )

    def get_catalog_projection(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId
    ) -> DbtCatalogArtifact | None:
        self.get_snapshot(scope, snapshot_id)
        digest = self._active_catalog.get(snapshot_id)
        return None if digest is None else self._catalogs[(snapshot_id, digest)]

    def get_run_results_projection(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId
    ) -> DbtRunResultsArtifact | None:
        self.get_snapshot(scope, snapshot_id)
        digest = self._active_run_results.get(snapshot_id)
        return None if digest is None else self._run_results[(snapshot_id, digest)]

    def get_source_freshness_projection(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId
    ) -> DbtSourceFreshnessArtifact | None:
        self.get_snapshot(scope, snapshot_id)
        digest = self._active_source_freshness.get(snapshot_id)
        return None if digest is None else self._source_freshness[(snapshot_id, digest)]

    def get_node(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, unique_id: DbtNodeId
    ) -> DbtManifestNode:
        try:
            artifact = self._artifact(scope, snapshot_id)
        except ManifestSnapshotNotFound as error:
            raise ManifestNodeNotFound("manifest node was not found") from error
        for node in artifact.nodes:
            if node.unique_id == unique_id:
                return node
        raise ManifestNodeNotFound("manifest node was not found")

    def find_nodes_by_original_file_path(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, original_file_path: str
    ) -> tuple[DbtManifestNode, ...]:
        try:
            nodes = self._artifact(scope, snapshot_id).nodes
        except ManifestSnapshotNotFound:
            return ()
        return tuple(node for node in nodes if node.original_file_path == original_file_path)

    def iter_nodes(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId
    ) -> tuple[DbtManifestNode, ...]:
        return self._artifact(scope, snapshot_id).nodes

    def iter_edges(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId
    ) -> tuple[DbtLineageEdge, ...]:
        return self._artifact(scope, snapshot_id).edges

    def direct_upstream(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, unique_id: DbtNodeId
    ) -> tuple[DbtLineageEdge, ...]:
        self.get_node(scope, snapshot_id, unique_id)
        return tuple(
            edge for edge in self.iter_edges(scope, snapshot_id) if edge.child_id == unique_id
        )

    def direct_downstream(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, unique_id: DbtNodeId
    ) -> tuple[DbtLineageEdge, ...]:
        self.get_node(scope, snapshot_id, unique_id)
        return tuple(
            edge for edge in self.iter_edges(scope, snapshot_id) if edge.parent_id == unique_id
        )

    def get_nodes(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, unique_ids: tuple[DbtNodeId, ...]
    ) -> tuple[DbtManifestNode, ...]:
        requested = set(unique_ids)
        return tuple(
            node for node in self.iter_nodes(scope, snapshot_id) if node.unique_id in requested
        )

    def get_upstream_edges(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, child_ids: tuple[DbtNodeId, ...]
    ) -> tuple[DbtLineageEdge, ...]:
        requested = set(child_ids)
        return tuple(
            edge for edge in self.iter_edges(scope, snapshot_id) if edge.child_id in requested
        )

    def get_downstream_edges(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, parent_ids: tuple[DbtNodeId, ...]
    ) -> tuple[DbtLineageEdge, ...]:
        requested = set(parent_ids)
        return tuple(
            edge for edge in self.iter_edges(scope, snapshot_id) if edge.parent_id in requested
        )

    def list_snapshots(
        self, scope: MemoryScope, *, offset: int = 0, limit: int = 50
    ) -> ManifestSnapshotPage:
        self._require_scope(scope)
        if offset < 0 or limit < 1:
            raise ValueError("offset must be non-negative and limit must be positive")
        snapshots = sorted(
            (item for item in self._snapshots.values() if item.scope == scope),
            key=lambda item: (item.metadata.ingested_at, str(item.snapshot_id)),
            reverse=True,
        )
        return ManifestSnapshotPage(
            items=tuple(snapshots[offset : offset + limit]),
            next_offset=offset + limit if offset + limit < len(snapshots) else None,
        )

    def _artifact(self, scope: MemoryScope, snapshot_id: DbtSnapshotId) -> DbtManifestArtifact:
        self.get_snapshot(scope, snapshot_id)
        return self._artifacts[snapshot_id]

    def _store_supplemental(
        self,
        scope: MemoryScope,
        snapshot_id: DbtSnapshotId,
        artifact: _SupplementalArtifactT,
        stored: dict[tuple[DbtSnapshotId, str], _SupplementalArtifactT],
        active: dict[DbtSnapshotId, str],
        resource_ids: tuple[DbtNodeId, ...],
    ) -> SupplementalArtifactStoreResult:
        manifest = self._artifact(scope, snapshot_id)
        if artifact.scope != scope:
            raise InvalidManifestSnapshotScope(
                "supplemental dbt artifact requires exact manifest scope"
            )
        manifest_ids = {item.unique_id for item in manifest.nodes}
        if set(resource_ids) - manifest_ids:
            raise SupplementalArtifactConflict(
                "supplemental dbt artifact references a node absent from the manifest snapshot"
            )
        digest = artifact.metadata.content_digest
        key = (snapshot_id, digest)
        existing = stored.get(key)
        if existing is not None:
            if (
                existing.metadata.normalized_digest != artifact.metadata.normalized_digest
                or existing.metadata.source_identity != artifact.metadata.source_identity
            ):
                raise SupplementalArtifactConflict(
                    "supplemental dbt artifact digest conflicts with retained metadata"
                )
            active[snapshot_id] = digest
            return SupplementalArtifactStoreResult(snapshot_id, digest, True)
        stored[key] = artifact
        active[snapshot_id] = digest
        return SupplementalArtifactStoreResult(snapshot_id, digest, False)

    @staticmethod
    def _require_scope(scope: MemoryScope) -> None:
        if not isinstance(scope, MemoryScope) or scope.level is not ScopeLevel.PROJECT:
            raise InvalidManifestSnapshotScope(
                "dbt snapshot operations require explicit project scope"
            )


class ReferenceSourceStructureRepository:
    """Atomic in-memory reference for immutable multi-language source snapshots."""

    def __init__(self) -> None:
        self._artifacts: dict[CodeSnapshotId, CodeStructureArtifact] = {}
        self._active: dict[MemoryScope, CodeSnapshotId] = {}
        self._activations: dict[MemoryScope, list[CodeSnapshotId]] = {}

    def store_and_activate(self, artifact: CodeStructureArtifact) -> SourceSnapshotStoreResult:
        self._require_scope(artifact.snapshot.scope)
        for snapshot in self._artifacts.values():
            if (
                snapshot.snapshot.scope == artifact.snapshot.scope
                and snapshot.snapshot.source_digest == artifact.snapshot.source_digest
            ):
                previous = self._active.get(artifact.snapshot.scope)
                self._active[artifact.snapshot.scope] = snapshot.snapshot.snapshot_id
                if previous != snapshot.snapshot.snapshot_id:
                    self._activations.setdefault(artifact.snapshot.scope, []).append(
                        snapshot.snapshot.snapshot_id
                    )
                return SourceSnapshotStoreResult(snapshot.snapshot, idempotent=True)
        snapshot_id = artifact.snapshot.snapshot_id
        if snapshot_id in self._artifacts:
            raise SourceIndexStorageFailure("source snapshot identity already exists")
        previous = self._active.get(artifact.snapshot.scope)
        try:
            self._artifacts[snapshot_id] = artifact
            self._active[artifact.snapshot.scope] = snapshot_id
        except BaseException:
            self._artifacts.pop(snapshot_id, None)
            if previous is None:
                self._active.pop(artifact.snapshot.scope, None)
            else:
                self._active[artifact.snapshot.scope] = previous
            raise
        self._activations.setdefault(artifact.snapshot.scope, []).append(snapshot_id)
        return SourceSnapshotStoreResult(artifact.snapshot, idempotent=False)

    def get_active_snapshot(self, scope: MemoryScope) -> CodeSnapshot | None:
        self._require_scope(scope)
        snapshot_id = self._active.get(scope)
        return None if snapshot_id is None else self._artifacts[snapshot_id].snapshot

    def get_snapshot(self, scope: MemoryScope, snapshot_id: CodeSnapshotId) -> CodeSnapshot:
        self._require_scope(scope)
        artifact = self._artifacts.get(snapshot_id)
        if artifact is None or artifact.snapshot.scope != scope:
            raise SourceSnapshotNotFound("source snapshot was not found")
        return artifact.snapshot

    def latest_transition(self, scope: MemoryScope) -> tuple[CodeSnapshot, CodeSnapshot] | None:
        self._require_scope(scope)
        activations = self._activations.get(scope, [])
        if len(activations) < 2:
            return None
        return (
            self._artifacts[activations[-2]].snapshot,
            self._artifacts[activations[-1]].snapshot,
        )

    def list_activation_history(
        self, scope: MemoryScope, *, limit: int = 20
    ) -> tuple[CodeSnapshot, ...]:
        self._require_scope(scope)
        if limit < 1 or limit > 100:
            raise ValueError("source snapshot history limit must be between 1 and 100")
        return tuple(
            self._artifacts[snapshot_id].snapshot
            for snapshot_id in reversed(self._activations.get(scope, ()))
        )[:limit]

    def iter_symbols(
        self, scope: MemoryScope, snapshot_id: CodeSnapshotId
    ) -> tuple[CodeSymbol, ...]:
        return self._artifact(scope, snapshot_id).symbols

    def iter_files(self, scope: MemoryScope, snapshot_id: CodeSnapshotId) -> tuple[CodeFile, ...]:
        return self._artifact(scope, snapshot_id).files

    def get_file(
        self, scope: MemoryScope, snapshot_id: CodeSnapshotId, relative_path: str
    ) -> CodeFile | None:
        return next(
            (
                item
                for item in self._artifact(scope, snapshot_id).files
                if item.relative_path == relative_path
            ),
            None,
        )

    def iter_edges(self, scope: MemoryScope, snapshot_id: CodeSnapshotId) -> tuple[CodeEdge, ...]:
        return self._artifact(scope, snapshot_id).edges

    def find_symbols(
        self, scope: MemoryScope, snapshot_id: CodeSnapshotId, query: str, *, limit: int
    ) -> tuple[CodeSymbol, ...]:
        terms = source_search_terms(query)
        if not terms or limit < 1:
            return ()
        return tuple(
            sorted(
                (
                    symbol
                    for symbol in self.iter_symbols(scope, snapshot_id)
                    if source_symbol_matches(symbol, terms)
                ),
                key=lambda symbol: source_symbol_rank(symbol, query, terms),
            )
        )[:limit]

    def module_symbols_for_paths(
        self, scope: MemoryScope, snapshot_id: CodeSnapshotId, relative_paths: tuple[str, ...]
    ) -> tuple[CodeSymbol, ...]:
        requested = frozenset(relative_paths)
        return tuple(
            symbol
            for symbol in self.iter_symbols(scope, snapshot_id)
            if symbol.kind is CodeSymbolKind.MODULE and symbol.relative_path in requested
        )

    def symbols_by_ids(
        self, scope: MemoryScope, snapshot_id: CodeSnapshotId, symbol_ids: tuple[CodeSymbolId, ...]
    ) -> tuple[CodeSymbol, ...]:
        requested = frozenset(symbol_ids)
        return tuple(
            symbol
            for symbol in self.iter_symbols(scope, snapshot_id)
            if symbol.symbol_id in requested
        )

    def edges_from_symbols(
        self, scope: MemoryScope, snapshot_id: CodeSnapshotId, symbol_ids: tuple[CodeSymbolId, ...]
    ) -> tuple[CodeEdge, ...]:
        requested = frozenset(symbol_ids)
        return tuple(
            edge
            for edge in self.iter_edges(scope, snapshot_id)
            if edge.source_symbol_id in requested
        )

    def edges_to_symbols(
        self, scope: MemoryScope, snapshot_id: CodeSnapshotId, symbol_ids: tuple[CodeSymbolId, ...]
    ) -> tuple[CodeEdge, ...]:
        """Return only statically resolved internal incoming relationships."""
        requested = frozenset(symbol_ids)
        return tuple(
            edge
            for edge in self.iter_edges(scope, snapshot_id)
            if edge.target_symbol_id in requested
        )

    def _artifact(self, scope: MemoryScope, snapshot_id: CodeSnapshotId) -> CodeStructureArtifact:
        self.get_snapshot(scope, snapshot_id)
        return self._artifacts[snapshot_id]

    @staticmethod
    def _require_scope(scope: MemoryScope) -> None:
        if not isinstance(scope, MemoryScope) or scope.level is not ScopeLevel.PROJECT:
            raise SourceIndexStorageFailure("source snapshots require explicit project scope")
