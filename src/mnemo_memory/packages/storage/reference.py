"""Reference adapter for the aggregate/revision checkpoint repository contract."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from threading import Lock
from typing import TypeVar

from mnemo_memory.packages.domain import (
    ApprovedEpisodicEvent,
    ApprovedEpisodicEventGovernance,
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
    MemoryScope,
    OutboxJobId,
    ScopeLevel,
    TaskActivityEvent,
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
    KnowledgeDocumentSafetyPolicy,
    TaskActivityEventSafetyPolicy,
)

from .contracts import (
    ActiveSnapshotConflict,
    ApprovedEpisodicEventConflict,
    ApprovedEpisodicEventGovernanceResult,
    ApprovedEpisodicEventNotFound,
    ApprovedEpisodicEventPage,
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
    EpisodicEventNotFound,
    EpisodicEventPage,
    EpisodicEventStoreResult,
    EventOutboxLeaseConflict,
    EventOutboxNotFound,
    InvalidAbandonmentReason,
    InvalidApprovedEpisodicEventScope,
    InvalidCheckpointScope,
    InvalidEpisodicEventScope,
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
    TaskActivityEventConflict,
    TaskActivityEventNotFound,
    TaskActivityEventPage,
    TaskActivityEventRejected,
    TaskActivityEventStoreResult,
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

    def append_task_activity_event(self, event: TaskActivityEvent) -> TaskActivityEventStoreResult:
        self._require_scope(event.scope)
        if not self._policy.assess(event).accepted:
            raise TaskActivityEventRejected("task activity event was rejected by safety policy")
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
        if event is None or event.scope != scope:
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
        )
        return TaskActivityEventPage(
            items[offset : offset + limit],
            offset + limit if offset + limit < len(items) else None,
        )

    @staticmethod
    def _require_scope(scope: MemoryScope) -> None:
        if not isinstance(scope, MemoryScope) or scope.level is not ScopeLevel.TASK:
            raise InvalidTaskActivityEventScope("task activity events require explicit task scope")


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
            self.outbox._enqueue_many(
                (self._event_job(replacement), self._governance_job(governance))
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
            del self._events[target.event_id]
            del self._keys[(target.scope, target.source_event_key)]
            self.outbox._enqueue(self._governance_job(governance))
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
            return ApprovedEpisodicEventRecord(event_id, scope, status, event, governance)
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
        list[EventId],
    ]:
        return (
            dict(self._events),
            dict(self._keys),
            dict(self._governance),
            dict(self._action_keys),
            list(self._ordered),
        )

    def _restore(
        self,
        state: tuple[
            dict[EventId, ApprovedEpisodicEvent],
            dict[tuple[MemoryScope, str], EventId],
            dict[EventId, ApprovedEpisodicEventGovernance],
            dict[tuple[MemoryScope, str], EventId],
            list[EventId],
        ],
    ) -> None:
        (
            self._events,
            self._keys,
            self._governance,
            self._action_keys,
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
