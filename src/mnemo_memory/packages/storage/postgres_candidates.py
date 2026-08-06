"""PostgreSQL episodic candidate and explicit review repositories."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime
from typing import cast

from mnemo_memory.packages.domain import (
    ActiveEpisodicMemory,
    DurableClaim,
    EpisodicCandidateReviewAction,
    EpisodicCandidateReviewDecision,
    EpisodicDeletionCause,
    EpisodicExportBundle,
    EpisodicMemoryCandidate,
    EpisodicMemoryDeletion,
    EpisodicMemoryExpiration,
    EpisodicMemoryGovernanceAction,
    EpisodicMemoryGovernanceKind,
    EpisodicMemoryKind,
    EpisodicMemoryPurge,
    EpisodicMemoryRetentionTarget,
    EpisodicMemoryRevision,
    EpisodicMemoryRevisionStatus,
    EventId,
    EvidenceReference,
    MemoryClassification,
    MemoryId,
    MemoryScope,
    MemoryStatus,
    OwnerId,
    RetentionPolicyId,
    RetentionSchedule,
    ScopeLevel,
    Sensitivity,
    TaskActivityActor,
    TaskActivityEventDeletion,
    TaskActivityEventExpiration,
    TaskActivityEventPurge,
    WorkspaceId,
    active_episodic_memory_at_revision,
    replay_episodic_memory_revisions,
)
from mnemo_memory.packages.policy import (
    EpisodicCandidateReviewSafetyPolicy,
    EpisodicMemoryCandidateSafetyPolicy,
    EpisodicMemoryGovernanceSafetyPolicy,
    TeamOperation,
)

from .contracts import (
    ActiveEpisodicMemoryNotFound,
    ActiveEpisodicMemoryPage,
    EpisodicDeletionConflict,
    EpisodicDeletionNotFound,
    EpisodicDeletionRepositoryError,
    EpisodicDeletionStorageFailure,
    EpisodicExportRepositoryError,
    EpisodicExportStorageFailure,
    EpisodicLifecycleImportConflict,
    EpisodicLifecycleImportRepositoryError,
    EpisodicLifecycleImportResult,
    EpisodicLifecycleImportStorageFailure,
    EpisodicMemoryCandidateConflict,
    EpisodicMemoryCandidateNotFound,
    EpisodicMemoryCandidatePage,
    EpisodicMemoryCandidateRejected,
    EpisodicMemoryCandidateRepositoryError,
    EpisodicMemoryCandidateStorageFailure,
    EpisodicMemoryCandidateStoreResult,
    EpisodicMemoryDeletionResult,
    EpisodicMemoryExpirationConflict,
    EpisodicMemoryExpirationNotFound,
    EpisodicMemoryExpirationResult,
    EpisodicMemoryGovernanceConflict,
    EpisodicMemoryGovernanceNotFound,
    EpisodicMemoryGovernanceRejected,
    EpisodicMemoryGovernanceRepositoryError,
    EpisodicMemoryGovernanceResult,
    EpisodicMemoryGovernanceStorageFailure,
    EpisodicMemoryPurgeConflict,
    EpisodicMemoryPurgeNotFound,
    EpisodicMemoryPurgeResult,
    EpisodicMemoryPurgeStorageFailure,
    EpisodicMemoryRetentionRepositoryError,
    EpisodicMemoryRetentionStorageFailure,
    EpisodicMemoryReviewConflict,
    EpisodicMemoryReviewNotFound,
    EpisodicMemoryReviewRejected,
    EpisodicMemoryReviewRepositoryError,
    EpisodicMemoryReviewResult,
    EpisodicMemoryReviewStorageFailure,
    InvalidEpisodicExportScope,
    InvalidEpisodicMemoryCandidateScope,
    TaskActivityDeletionResult,
)
from .postgres import PostgreSQLConnectionFactory, PostgreSQLCursor
from .postgres_events import (
    _EXPIRATION_COLUMNS as _TASK_EXPIRATION_COLUMNS,
)
from .postgres_events import (
    _PURGE_COLUMNS as _TASK_PURGE_COLUMNS,
)
from .postgres_events import (
    _TASK_EVENT_COLUMNS,
    PostgreSQLTaskActivityEventRepository,
    _task_scope_values,
)

_CANDIDATE_COLUMNS = (
    "candidate_sequence, memory_id::text, source_event_id::text, proposal_index, memory_kind, "
    "claim, confidence, sensitivity, status, extractor_version, provider_id, model_id, "
    "prompt_version, retention_json::text, created_at, evidence_json::text"
)
_REVIEW_COLUMNS = (
    "action_sequence, action_id::text, candidate_id::text, decision, actor, source_action_key, "
    "reason, reviewed_at, evidence_json::text"
)
_GOVERNANCE_COLUMNS = (
    "action_sequence, action_id::text, memory_id::text, action_kind, actor, "
    "expected_revision_id::text, source_action_key, reason, corrected_claim, "
    "corrected_sensitivity, occurred_at, evidence_json::text"
)
_EXPIRATION_COLUMNS = (
    "expiration_sequence, expiration_id::text, memory_id::text, source_event_id::text, "
    "retention_policy_id::text, scheduled_expires_at, expired_at"
)
_PURGE_COLUMNS = "purge_sequence, purge_id::text, expiration_id::text, memory_id::text, purged_at"
_MEMORY_DELETION_COLUMNS = (
    "deletion_sequence, deletion_id::text, memory_id::text, source_event_id::text, cause, "
    "source_deletion_id::text, actor, source_action_key, deleted_at"
)
_SOURCE_DELETION_COLUMNS = (
    "deletion_sequence, deletion_id::text, event_id::text, actor, source_action_key, deleted_at"
)
_CANDIDATE_EXPORT_COLUMNS = (
    "candidate.candidate_sequence, candidate.memory_id::text, "
    "candidate.source_event_id::text, candidate.proposal_index, candidate.memory_kind, "
    "candidate.claim, candidate.confidence, candidate.sensitivity, candidate.status, "
    "candidate.extractor_version, candidate.provider_id, candidate.model_id, "
    "candidate.prompt_version, candidate.retention_json::text, candidate.created_at, "
    "candidate.evidence_json::text"
)
_IMPORTED_LIFECYCLE_GROUPS = (
    ("memory_expiration", "memory_expirations", "memory_id"),
    ("memory_purge", "memory_purges", "memory_id"),
    ("task_expiration", "task_expirations", "event_id"),
    ("task_purge", "task_purges", "event_id"),
    ("memory_deletion", "memory_deletions", "memory_id"),
    ("task_deletion", "task_deletions", "event_id"),
)
_NATIVE_LIFECYCLE_TARGETS = {
    "memory_expiration": ("episodic_memory_expirations", "memory_id"),
    "memory_purge": ("episodic_memory_purges", "memory_id"),
    "task_expiration": ("task_activity_event_expirations", "event_id"),
    "task_purge": ("task_activity_event_purges", "event_id"),
    "memory_deletion": ("episodic_memory_deletions", "memory_id"),
    "task_deletion": ("task_activity_event_deletions", "event_id"),
}


def _sqlstate(error: Exception) -> str | None:
    for value in error.args:
        if isinstance(value, Mapping):
            state = value.get("C")
            if isinstance(state, str):
                return state
    return None


class PostgreSQLEpisodicMemoryRepository:
    """One principal/workspace-bound inactive-candidate and review repository."""

    def __init__(
        self,
        connection_factory: PostgreSQLConnectionFactory,
        *,
        principal_id: OwnerId,
        workspace_id: WorkspaceId,
        candidate_policy: EpisodicMemoryCandidateSafetyPolicy | None = None,
        review_policy: EpisodicCandidateReviewSafetyPolicy | None = None,
        governance_policy: EpisodicMemoryGovernanceSafetyPolicy | None = None,
        statement_timeout_ms: int = 5000,
    ) -> None:
        if not isinstance(principal_id, OwnerId):
            raise TypeError("principal_id must be an OwnerId")
        if not isinstance(workspace_id, WorkspaceId):
            raise TypeError("workspace_id must be a WorkspaceId")
        if (
            not isinstance(statement_timeout_ms, int)
            or isinstance(statement_timeout_ms, bool)
            or not 1 <= statement_timeout_ms <= 60_000
        ):
            raise ValueError("statement_timeout_ms must be between 1 and 60000")
        self._connection_factory = connection_factory
        self._principal_id = principal_id
        self._workspace_id = workspace_id
        self._candidate_policy = candidate_policy or EpisodicMemoryCandidateSafetyPolicy()
        self._review_policy = review_policy or EpisodicCandidateReviewSafetyPolicy()
        self._governance_policy = governance_policy or EpisodicMemoryGovernanceSafetyPolicy()
        self._statement_timeout_ms = statement_timeout_ms

    def store_episodic_memory_candidates(
        self, candidates: tuple[EpisodicMemoryCandidate, ...]
    ) -> EpisodicMemoryCandidateStoreResult:
        values = self._validate_batch(candidates)
        if any(not self._candidate_policy.assess(candidate).accepted for candidate in values):
            raise EpisodicMemoryCandidateRejected(
                "episodic candidate batch was rejected by safety policy"
            )
        first = values[0]
        with self._candidate_transaction(TeamOperation.CONTRIBUTE) as cursor:
            cursor.execute(
                "SELECT retention_json::text, evidence_json::text FROM "
                "mnemo_team.task_activity_events WHERE workspace_id = CAST(%s AS uuid) "
                "AND project_id = CAST(%s AS uuid) AND owner_id = CAST(%s AS uuid) "
                "AND visibility = %s AND session_id = CAST(%s AS uuid) "
                "AND task_id = CAST(%s AS uuid) AND event_id = CAST(%s AS uuid)",
                (*_task_scope_values(first.scope), str(first.source_event_id)),
            )
            source = cursor.fetchone()
            if source is None:
                raise EpisodicMemoryCandidateConflict(
                    "episodic candidate source event is unavailable"
                )
            retention = self._retention_from_json(source[0])
            evidence = self._evidence_from_json(source[1])
            if any(
                candidate.retention != retention or candidate.evidence_references != evidence
                for candidate in values
            ):
                raise EpisodicMemoryCandidateConflict(
                    "episodic candidate authority fields do not match the source event"
                )
            cursor.execute(
                "SELECT memory_id::text FROM mnemo_team.episodic_memory_expirations WHERE "
                "workspace_id = CAST(%s AS uuid) AND memory_id = ANY(CAST(%s AS uuid[])) "
                "UNION ALL SELECT memory_id::text FROM mnemo_team.episodic_memory_deletions "
                "WHERE workspace_id = CAST(%s AS uuid) AND memory_id = ANY(CAST(%s AS uuid[])) "
                "UNION ALL SELECT target_id::text FROM "
                "mnemo_team.imported_episodic_lifecycle WHERE "
                "workspace_id = CAST(%s AS uuid) "
                "AND lifecycle_kind IN ('memory_expiration', 'memory_deletion') "
                "AND target_id = ANY(CAST(%s AS uuid[]))",
                (
                    str(self._workspace_id),
                    "{" + ",".join(str(candidate.memory_id) for candidate in values) + "}",
                    str(self._workspace_id),
                    "{" + ",".join(str(candidate.memory_id) for candidate in values) + "}",
                    str(self._workspace_id),
                    "{" + ",".join(str(candidate.memory_id) for candidate in values) + "}",
                ),
            )
            if cursor.fetchone() is not None:
                raise EpisodicMemoryCandidateConflict(
                    "episodic candidate retention tombstone prevents resurrection"
                )
            cursor.execute(
                "SELECT " + _CANDIDATE_COLUMNS + " FROM "
                "mnemo_team.episodic_memory_candidates WHERE "
                "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
                "AND source_event_id = CAST(%s AS uuid) AND extractor_version = %s "
                "ORDER BY proposal_index ASC",
                (
                    *_task_scope_values(first.scope),
                    str(first.source_event_id),
                    first.extractor_version,
                ),
            )
            rows = tuple(cursor.fetchall())
            if rows:
                existing = tuple(self._candidate_from_row(row, first.scope) for row in rows)
                if existing == values:
                    return EpisodicMemoryCandidateStoreResult(existing, True)
                raise EpisodicMemoryCandidateConflict(
                    "episodic candidate extraction already has different output"
                )
            for candidate in values:
                cursor.execute(
                    "SELECT 1 FROM mnemo_team.episodic_memory_candidates "
                    "WHERE workspace_id = CAST(%s AS uuid) AND memory_id = CAST(%s AS uuid)",
                    (str(self._workspace_id), str(candidate.memory_id)),
                )
                if cursor.fetchone() is not None:
                    raise EpisodicMemoryCandidateConflict("episodic candidate identity conflicts")
            for candidate in values:
                self._insert_candidate(cursor, candidate)
            return EpisodicMemoryCandidateStoreResult(values, False)

    def get_episodic_memory_candidate(
        self, scope: MemoryScope, memory_id: MemoryId
    ) -> EpisodicMemoryCandidate:
        self._require_scope(scope)
        if not isinstance(memory_id, MemoryId):
            raise TypeError("memory_id must be a MemoryId")
        with self._candidate_transaction(TeamOperation.READ) as cursor:
            candidate = self._scoped_candidate(cursor, scope, memory_id)
            if candidate is None:
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
        self._validate_page(offset, limit, "candidate")
        if source_event_id is not None and not isinstance(source_event_id, EventId):
            raise TypeError("source_event_id must be an EventId")
        source_filter = "" if source_event_id is None else " AND source_event_id = CAST(%s AS uuid)"
        parameters: tuple[object, ...] = _task_scope_values(scope)
        if source_event_id is not None:
            parameters += (str(source_event_id),)
        parameters += (limit + 1, offset)
        with self._candidate_transaction(TeamOperation.READ) as cursor:
            cursor.execute(
                "SELECT " + _CANDIDATE_COLUMNS + " FROM "
                "mnemo_team.episodic_memory_candidates WHERE "
                "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid)"
                + source_filter
                + " AND NOT EXISTS (SELECT 1 FROM "
                "mnemo_team.episodic_memory_expirations AS expiration WHERE "
                "expiration.workspace_id = episodic_memory_candidates.workspace_id "
                "AND expiration.memory_id = episodic_memory_candidates.memory_id)"
                + " ORDER BY candidate_sequence DESC LIMIT %s OFFSET %s",
                parameters,
            )
            rows = tuple(cursor.fetchall())
            items = tuple(self._candidate_from_row(row, scope) for row in rows[:limit])
            return EpisodicMemoryCandidatePage(items, offset + limit if len(rows) > limit else None)

    def review_episodic_memory_candidate(
        self, action: EpisodicCandidateReviewAction
    ) -> EpisodicMemoryReviewResult:
        self._require_scope(action.scope)
        with self._review_transaction(TeamOperation.CONTRIBUTE) as cursor:
            candidate = self._scoped_candidate(cursor, action.scope, action.candidate_id)
            if candidate is None:
                raise EpisodicMemoryReviewNotFound(
                    "episodic memory candidate was not found for review"
                )
            if not self._review_policy.assess(candidate, action).accepted:
                raise EpisodicMemoryReviewRejected(
                    "episodic memory review was rejected by safety policy"
                )
            existing = self._scoped_review(cursor, action.scope, action.candidate_id)
            if existing is not None:
                if existing == action:
                    return EpisodicMemoryReviewResult(
                        existing,
                        self._optional_active(cursor, action.scope, action.candidate_id),
                        True,
                    )
                raise EpisodicMemoryReviewConflict(
                    "episodic candidate already has a different review"
                )
            cursor.execute(
                "SELECT 1 FROM mnemo_team.episodic_candidate_reviews WHERE "
                "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
                "AND source_action_key = %s",
                (*_task_scope_values(action.scope), action.source_action_key),
            )
            if cursor.fetchone() is not None:
                raise EpisodicMemoryReviewConflict("episodic review action key conflicts")
            self._insert_review(cursor, action)
            active = None
            if action.decision is EpisodicCandidateReviewDecision.APPROVED:
                active = ActiveEpisodicMemory.approve(candidate, action)
                cursor.execute(
                    "INSERT INTO mnemo_team.active_episodic_memories("
                    "workspace_id, project_id, owner_id, visibility, session_id, task_id, "
                    "memory_id, approval_action_id, activated_at) VALUES ("
                    "CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, "
                    "CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), "
                    "CAST(%s AS uuid), %s)",
                    (
                        *_task_scope_values(action.scope),
                        str(active.memory_id),
                        str(active.approval_action_id),
                        active.activated_at,
                    ),
                )
            return EpisodicMemoryReviewResult(action, active, False)

    def get_episodic_memory_review(
        self, scope: MemoryScope, candidate_id: MemoryId
    ) -> EpisodicCandidateReviewAction:
        self._require_scope(scope)
        if not isinstance(candidate_id, MemoryId):
            raise TypeError("candidate_id must be a MemoryId")
        with self._review_transaction(TeamOperation.READ) as cursor:
            action = self._scoped_review(cursor, scope, candidate_id)
            if action is None:
                raise EpisodicMemoryReviewNotFound("episodic memory review was not found")
            return action

    def get_active_episodic_memory(
        self, scope: MemoryScope, memory_id: MemoryId
    ) -> ActiveEpisodicMemory:
        self._require_scope(scope)
        if not isinstance(memory_id, MemoryId):
            raise TypeError("memory_id must be a MemoryId")
        with self._review_transaction(TeamOperation.READ) as cursor:
            active = self._optional_active(cursor, scope, memory_id)
            if active is None:
                raise ActiveEpisodicMemoryNotFound("active episodic memory was not found")
            return active

    def list_active_episodic_memories(
        self, scope: MemoryScope, *, offset: int = 0, limit: int = 50
    ) -> ActiveEpisodicMemoryPage:
        self._require_scope(scope)
        self._validate_page(offset, limit, "memory")
        with self._review_transaction(TeamOperation.READ) as cursor:
            cursor.execute(
                "SELECT active.memory_id::text FROM mnemo_team.active_episodic_memories AS active "
                "JOIN mnemo_team.episodic_candidate_reviews AS review "
                "ON review.workspace_id = active.workspace_id "
                "AND review.action_id = active.approval_action_id WHERE "
                "active.workspace_id = CAST(%s AS uuid) "
                "AND active.project_id = CAST(%s AS uuid) "
                "AND active.owner_id = CAST(%s AS uuid) AND active.visibility = %s "
                "AND active.session_id = CAST(%s AS uuid) AND active.task_id = CAST(%s AS uuid) "
                "AND NOT EXISTS (SELECT 1 FROM mnemo_team.episodic_memory_expirations "
                "AS expiration WHERE expiration.workspace_id = active.workspace_id "
                "AND expiration.memory_id = active.memory_id) "
                "AND NOT EXISTS (SELECT 1 FROM mnemo_team.episodic_memory_governance AS action "
                "WHERE action.workspace_id = active.workspace_id "
                "AND action.memory_id = active.memory_id AND action.action_kind = 'retracted') "
                "ORDER BY review.action_sequence DESC LIMIT %s OFFSET %s",
                (*_task_scope_values(scope), limit + 1, offset),
            )
            rows = tuple(cursor.fetchall())
            items = tuple(
                self._required_active(cursor, scope, MemoryId.from_string(str(row[0])))
                for row in rows[:limit]
            )
            return ActiveEpisodicMemoryPage(items, offset + limit if len(rows) > limit else None)

    def govern_episodic_memory(
        self, action: EpisodicMemoryGovernanceAction
    ) -> EpisodicMemoryGovernanceResult:
        self._require_scope(action.scope)
        with self._governance_transaction(TeamOperation.CONTRIBUTE) as cursor:
            base = self._base_active(cursor, action.scope, action.memory_id)
            if base is None:
                raise EpisodicMemoryGovernanceNotFound("episodic memory was not found")
            existing = self._scoped_governance_by_action(cursor, action.scope, action.action_id)
            if existing is not None:
                if existing != action:
                    raise EpisodicMemoryGovernanceConflict(
                        "episodic memory governance identity conflicts"
                    )
                revisions = self._revisions(cursor, base)
                current = revisions[-1]
                active = (
                    active_episodic_memory_at_revision(base, current)
                    if current.status is EpisodicMemoryRevisionStatus.ACTIVE
                    else None
                )
                return EpisodicMemoryGovernanceResult(existing, current, active, True)
            cursor.execute(
                "SELECT 1 FROM mnemo_team.episodic_memory_governance WHERE "
                "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
                "AND source_action_key = %s",
                (*_task_scope_values(action.scope), action.source_action_key),
            )
            if cursor.fetchone() is not None:
                raise EpisodicMemoryGovernanceConflict(
                    "episodic memory governance action key conflicts"
                )
            revisions = self._revisions(cursor, base)
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
            actions = (*self._governance_actions(cursor, base.scope, base.memory_id), action)
            try:
                proposed = replay_episodic_memory_revisions(base, actions)
            except ValueError as error:
                raise EpisodicMemoryGovernanceConflict(
                    "episodic memory governance does not form a valid revision"
                ) from error
            self._insert_governance(cursor, action)
            latest = proposed[-1]
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
        if not isinstance(action_id, EventId):
            raise TypeError("action_id must be an EventId")
        with self._governance_transaction(TeamOperation.READ) as cursor:
            action = self._scoped_governance_by_action(cursor, scope, action_id)
            if action is None:
                raise EpisodicMemoryGovernanceNotFound(
                    "episodic memory governance action was not found"
                )
            return action

    def list_episodic_memory_revisions(
        self, scope: MemoryScope, memory_id: MemoryId
    ) -> tuple[EpisodicMemoryRevision, ...]:
        self._require_scope(scope)
        if not isinstance(memory_id, MemoryId):
            raise TypeError("memory_id must be a MemoryId")
        with self._governance_transaction(TeamOperation.READ) as cursor:
            base = self._base_active(cursor, scope, memory_id)
            if base is None:
                raise EpisodicMemoryGovernanceNotFound("episodic memory was not found")
            return self._revisions(cursor, base)

    def export_episodic_state(
        self, scope: MemoryScope, *, exported_at: datetime
    ) -> EpisodicExportBundle:
        if (
            not isinstance(scope, MemoryScope)
            or scope.level is not ScopeLevel.TASK
            or scope.workspace_id != self._workspace_id
            or scope.project_id is None
            or scope.session_id is None
            or scope.task_id is None
        ):
            raise InvalidEpisodicExportScope(
                "team episodic export requires the bound exact task scope"
            )
        self._require_aware(exported_at, "exported_at")
        scope_values = _task_scope_values(scope)
        with self._export_transaction() as cursor:
            cursor.execute(
                "SELECT "
                + _TASK_EVENT_COLUMNS
                + " FROM mnemo_team.task_activity_events AS event WHERE "
                "event.workspace_id = CAST(%s AS uuid) "
                "AND event.project_id = CAST(%s AS uuid) "
                "AND event.owner_id = CAST(%s AS uuid) AND event.visibility = %s "
                "AND event.session_id = CAST(%s AS uuid) AND event.task_id = CAST(%s AS uuid) "
                "AND NOT EXISTS (SELECT 1 FROM mnemo_team.task_activity_event_expirations "
                "AS expiration WHERE expiration.workspace_id = event.workspace_id "
                "AND expiration.event_id = event.event_id) "
                "AND NOT EXISTS (SELECT 1 FROM mnemo_team.task_activity_event_deletions "
                "AS deletion WHERE deletion.workspace_id = event.workspace_id "
                "AND deletion.event_id = event.event_id) ORDER BY event.event_id ASC",
                scope_values,
            )
            task_events = tuple(
                PostgreSQLTaskActivityEventRepository._event_from_row(row, scope)
                for row in cursor.fetchall()
            )
            cursor.execute(
                "SELECT "
                + _CANDIDATE_EXPORT_COLUMNS
                + " FROM mnemo_team.episodic_memory_candidates AS candidate "
                "JOIN mnemo_team.task_activity_events AS source "
                "ON source.workspace_id = candidate.workspace_id "
                "AND source.event_id = candidate.source_event_id WHERE "
                "candidate.workspace_id = CAST(%s AS uuid) "
                "AND candidate.project_id = CAST(%s AS uuid) "
                "AND candidate.owner_id = CAST(%s AS uuid) AND candidate.visibility = %s "
                "AND candidate.session_id = CAST(%s AS uuid) "
                "AND candidate.task_id = CAST(%s AS uuid) "
                "AND NOT EXISTS (SELECT 1 FROM mnemo_team.episodic_memory_expirations "
                "AS expiration WHERE expiration.workspace_id = candidate.workspace_id "
                "AND expiration.memory_id = candidate.memory_id) "
                "AND NOT EXISTS (SELECT 1 FROM mnemo_team.episodic_memory_deletions "
                "AS deletion WHERE deletion.workspace_id = candidate.workspace_id "
                "AND deletion.memory_id = candidate.memory_id) "
                "AND NOT EXISTS (SELECT 1 FROM mnemo_team.task_activity_event_expirations "
                "AS expiration WHERE expiration.workspace_id = source.workspace_id "
                "AND expiration.event_id = source.event_id) "
                "AND NOT EXISTS (SELECT 1 FROM mnemo_team.task_activity_event_deletions "
                "AS deletion WHERE deletion.workspace_id = source.workspace_id "
                "AND deletion.event_id = source.event_id) ORDER BY candidate.memory_id ASC",
                scope_values,
            )
            candidates = tuple(self._candidate_from_row(row, scope) for row in cursor.fetchall())
            candidate_ids = "{" + ",".join(str(item.memory_id) for item in candidates) + "}"
            reviews: tuple[EpisodicCandidateReviewAction, ...] = ()
            governance_actions: tuple[EpisodicMemoryGovernanceAction, ...] = ()
            revisions: tuple[EpisodicMemoryRevision, ...] = ()
            if candidates:
                cursor.execute(
                    "SELECT "
                    + _REVIEW_COLUMNS
                    + " FROM mnemo_team.episodic_candidate_reviews WHERE "
                    "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                    "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                    "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
                    "AND candidate_id = ANY(CAST(%s AS uuid[])) ORDER BY candidate_id ASC",
                    (*scope_values, candidate_ids),
                )
                reviews = tuple(self._review_from_row(row, scope) for row in cursor.fetchall())
                cursor.execute(
                    "SELECT "
                    + _GOVERNANCE_COLUMNS
                    + " FROM mnemo_team.episodic_memory_governance WHERE "
                    "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                    "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                    "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
                    "AND memory_id = ANY(CAST(%s AS uuid[])) "
                    "ORDER BY memory_id ASC, action_sequence ASC",
                    (*scope_values, candidate_ids),
                )
                governance_actions = tuple(
                    self._governance_from_row(row, scope) for row in cursor.fetchall()
                )
                cursor.execute(
                    "SELECT memory_id::text, approval_action_id::text FROM "
                    "mnemo_team.active_episodic_memories WHERE "
                    "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                    "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                    "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
                    "AND memory_id = ANY(CAST(%s AS uuid[])) ORDER BY memory_id ASC",
                    (*scope_values, candidate_ids),
                )
                active_rows = tuple(cursor.fetchall())
                candidate_by_id = {item.memory_id: item for item in candidates}
                review_by_id = {item.candidate_id: item for item in reviews}
                actions_by_id: dict[MemoryId, list[EpisodicMemoryGovernanceAction]] = {}
                for action in governance_actions:
                    actions_by_id.setdefault(action.memory_id, []).append(action)
                revision_values: list[EpisodicMemoryRevision] = []
                for row in active_rows:
                    memory_id = MemoryId.from_string(str(row[0]))
                    candidate = candidate_by_id[memory_id]
                    review = review_by_id[memory_id]
                    base = ActiveEpisodicMemory.approve(candidate, review)
                    if base.approval_action_id != EventId.from_string(str(row[1])):
                        raise ValueError("episodic export active approval relationship is invalid")
                    revision_values.extend(
                        replay_episodic_memory_revisions(
                            base, tuple(actions_by_id.get(memory_id, ()))
                        )
                    )
                revisions = tuple(revision_values)
            cursor.execute(
                "SELECT "
                + _EXPIRATION_COLUMNS
                + " FROM mnemo_team.episodic_memory_expirations WHERE "
                "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
                "ORDER BY memory_id ASC",
                scope_values,
            )
            memory_expirations = tuple(
                self._expiration_from_row(row, scope) for row in cursor.fetchall()
            )
            cursor.execute(
                "SELECT " + _PURGE_COLUMNS + " FROM mnemo_team.episodic_memory_purges WHERE "
                "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
                "ORDER BY memory_id ASC",
                scope_values,
            )
            memory_purges = tuple(self._purge_from_row(row, scope) for row in cursor.fetchall())
            cursor.execute(
                "SELECT "
                + _TASK_EXPIRATION_COLUMNS
                + " FROM mnemo_team.task_activity_event_expirations WHERE "
                "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
                "ORDER BY event_id ASC",
                scope_values,
            )
            task_expirations = tuple(
                PostgreSQLTaskActivityEventRepository._expiration_from_row(row, scope)
                for row in cursor.fetchall()
            )
            cursor.execute(
                "SELECT "
                + _TASK_PURGE_COLUMNS
                + " FROM mnemo_team.task_activity_event_purges WHERE "
                "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
                "ORDER BY event_id ASC",
                scope_values,
            )
            task_purges = tuple(
                PostgreSQLTaskActivityEventRepository._purge_from_row(row, scope)
                for row in cursor.fetchall()
            )
            cursor.execute(
                "SELECT "
                + _MEMORY_DELETION_COLUMNS
                + " FROM mnemo_team.episodic_memory_deletions WHERE "
                "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
                "ORDER BY memory_id ASC",
                scope_values,
            )
            memory_deletions = tuple(
                self._memory_deletion_from_row(row, scope) for row in cursor.fetchall()
            )
            cursor.execute(
                "SELECT "
                + _SOURCE_DELETION_COLUMNS
                + " FROM mnemo_team.task_activity_event_deletions WHERE "
                "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
                "ORDER BY event_id ASC",
                scope_values,
            )
            task_deletions = tuple(
                self._source_deletion_from_row(row, scope) for row in cursor.fetchall()
            )
            imported = self._imported_lifecycle(cursor, scope)
            return EpisodicExportBundle.create(
                scope=scope,
                exported_at=exported_at,
                task_events=task_events,
                candidates=candidates,
                reviews=reviews,
                governance_actions=governance_actions,
                revisions=revisions,
                memory_expirations=(*memory_expirations, *imported[0]),
                memory_purges=(*memory_purges, *imported[1]),
                task_expirations=(*task_expirations, *imported[2]),
                task_purges=(*task_purges, *imported[3]),
                memory_deletions=(*memory_deletions, *imported[4]),
                task_deletions=(*task_deletions, *imported[5]),
            )

    def import_episodic_lifecycle(
        self,
        source: EpisodicExportBundle,
        target: EpisodicExportBundle,
    ) -> EpisodicLifecycleImportResult:
        if not isinstance(source, EpisodicExportBundle) or not isinstance(
            target, EpisodicExportBundle
        ):
            raise TypeError("episodic lifecycle import requires validated bundles")
        self._require_scope(target.scope)
        lifecycle_count = sum(
            len(getattr(target, name)) for _, name, _ in _IMPORTED_LIFECYCLE_GROUPS
        )
        if lifecycle_count < 1:
            raise ValueError("episodic lifecycle import requires tombstones")
        if any(
            len(getattr(source, name)) != len(getattr(target, name))
            for _, name, _ in _IMPORTED_LIFECYCLE_GROUPS
        ):
            raise EpisodicLifecycleImportConflict(
                "episodic lifecycle source and target counts differ"
            )

        with self._lifecycle_import_transaction() as cursor:
            existing_count = 0
            for kind, group_name, identity_name in _IMPORTED_LIFECYCLE_GROUPS:
                source_items = getattr(source, group_name)
                target_items = getattr(target, group_name)
                for source_item, target_item in zip(source_items, target_items, strict=True):
                    source_id = getattr(source_item, identity_name)
                    target_id = getattr(target_item, identity_name)
                    cursor.execute(
                        "SELECT source_id::text, source_content_digest, payload_json::text "
                        "FROM mnemo_team.imported_episodic_lifecycle WHERE "
                        "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                        "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                        "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
                        "AND lifecycle_kind = %s AND "
                        "(target_id = CAST(%s AS uuid) OR source_id = CAST(%s AS uuid))",
                        (
                            *_task_scope_values(target.scope),
                            kind,
                            str(target_id),
                            str(source_id),
                        ),
                    )
                    row = cursor.fetchone()
                    if row is not None:
                        payload = json.loads(str(row[2]))
                        if (
                            str(row[0]) != str(source_id)
                            or str(row[1]) != source.content_digest
                            or payload != target_item.to_dict()
                        ):
                            raise EpisodicLifecycleImportConflict(
                                "episodic lifecycle import mapping conflicts"
                            )
                        existing_count += 1
                        continue
                    native_table, native_identity = _NATIVE_LIFECYCLE_TARGETS[kind]
                    cursor.execute(
                        f"SELECT 1 FROM mnemo_team.{native_table} WHERE "
                        "workspace_id = CAST(%s AS uuid) AND "
                        f"{native_identity} = CAST(%s AS uuid)",
                        (str(self._workspace_id), str(target_id)),
                    )
                    if cursor.fetchone() is not None:
                        raise EpisodicLifecycleImportConflict(
                            "episodic lifecycle import target already exists"
                        )
                    cursor.execute(
                        "INSERT INTO mnemo_team.imported_episodic_lifecycle("
                        "workspace_id, project_id, owner_id, visibility, session_id, task_id, "
                        "lifecycle_kind, target_id, source_id, source_content_digest, "
                        "payload_json, imported_at) VALUES (CAST(%s AS uuid), CAST(%s AS uuid), "
                        "CAST(%s AS uuid), %s, CAST(%s AS uuid), CAST(%s AS uuid), %s, "
                        "CAST(%s AS uuid), CAST(%s AS uuid), %s, CAST(%s AS jsonb), %s)",
                        (
                            *_task_scope_values(target.scope),
                            kind,
                            str(target_id),
                            str(source_id),
                            source.content_digest,
                            json.dumps(
                                target_item.to_dict(),
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                            target.exported_at,
                        ),
                    )
            return EpisodicLifecycleImportResult(
                lifecycle_count,
                existing_count == lifecycle_count,
            )

    def list_due_episodic_memory_retention(
        self, scope: MemoryScope, *, as_of: datetime
    ) -> tuple[EpisodicMemoryRetentionTarget, ...]:
        self._require_scope(scope)
        self._require_aware(as_of, "as_of")
        with self._retention_transaction(TeamOperation.READ) as cursor:
            cursor.execute(
                "SELECT candidate.memory_id::text, candidate.source_event_id::text, "
                "candidate.retention_json::text FROM "
                "mnemo_team.episodic_memory_candidates AS candidate WHERE "
                "candidate.workspace_id = CAST(%s AS uuid) "
                "AND candidate.project_id = CAST(%s AS uuid) "
                "AND candidate.owner_id = CAST(%s AS uuid) AND candidate.visibility = %s "
                "AND candidate.session_id = CAST(%s AS uuid) "
                "AND candidate.task_id = CAST(%s AS uuid) "
                "AND NOT EXISTS (SELECT 1 FROM mnemo_team.episodic_memory_expirations "
                "AS expiration WHERE expiration.workspace_id = candidate.workspace_id "
                "AND expiration.memory_id = candidate.memory_id)",
                _task_scope_values(scope),
            )
            targets = tuple(
                EpisodicMemoryRetentionTarget(
                    MemoryId.from_string(str(row[0])),
                    EventId.from_string(str(row[1])),
                    scope,
                    self._retention_from_json(row[2]),
                )
                for row in cursor.fetchall()
            )
        due = tuple(
            target
            for target in targets
            if not target.retention.permanent and target.retention.is_expired(as_of)
        )
        return tuple(
            sorted(
                due,
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
        for expiration in values:
            if not isinstance(expiration, EpisodicMemoryExpiration):
                raise TypeError("episodic memory expiration batch is invalid")
            self._require_scope(expiration.scope)
        with self._retention_transaction(TeamOperation.CONTRIBUTE) as cursor:
            existing_count = 0
            pending: list[EpisodicMemoryExpiration] = []
            for expiration in values:
                target = self._retention_target(cursor, expiration.scope, expiration.memory_id)
                if target is None:
                    raise EpisodicMemoryExpirationNotFound(
                        "episodic memory retention target was not found"
                    )
                schedule = target.retention
                if (
                    target.source_event_id != expiration.source_event_id
                    or schedule.permanent
                    or schedule.policy_id != expiration.retention_policy_id
                    or schedule.expires_at != expiration.scheduled_expires_at
                    or not schedule.is_expired(expiration.expired_at)
                ):
                    raise EpisodicMemoryExpirationConflict(
                        "episodic memory expiration does not match canonical retention"
                    )
                existing = self._scoped_expiration(cursor, expiration.scope, expiration.memory_id)
                if existing is not None:
                    if existing != expiration:
                        raise EpisodicMemoryExpirationConflict(
                            "episodic memory already has a different expiration"
                        )
                    existing_count += 1
                else:
                    pending.append(expiration)
            for expiration in pending:
                cursor.execute(
                    "INSERT INTO mnemo_team.episodic_memory_expirations("
                    "workspace_id, project_id, owner_id, visibility, session_id, task_id, "
                    "expiration_id, memory_id, source_event_id, retention_policy_id, "
                    "scheduled_expires_at, expired_at) VALUES (CAST(%s AS uuid), "
                    "CAST(%s AS uuid), CAST(%s AS uuid), %s, CAST(%s AS uuid), "
                    "CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), "
                    "CAST(%s AS uuid), CAST(%s AS uuid), %s, %s)",
                    (
                        *_task_scope_values(expiration.scope),
                        str(expiration.expiration_id),
                        str(expiration.memory_id),
                        str(expiration.source_event_id),
                        str(expiration.retention_policy_id),
                        expiration.scheduled_expires_at.isoformat(),
                        expiration.expired_at.isoformat(),
                    ),
                )
            return EpisodicMemoryExpirationResult(values, existing_count == len(values))

    def get_episodic_memory_expiration(
        self, scope: MemoryScope, memory_id: MemoryId
    ) -> EpisodicMemoryExpiration:
        self._require_scope(scope)
        if not isinstance(memory_id, MemoryId):
            raise TypeError("memory_id must be a MemoryId")
        with self._retention_transaction(TeamOperation.READ) as cursor:
            expiration = self._scoped_expiration(cursor, scope, memory_id)
            if expiration is None:
                raise EpisodicMemoryExpirationNotFound("episodic memory expiration was not found")
            return expiration

    def list_unpurged_episodic_memory_expirations(
        self, scope: MemoryScope
    ) -> tuple[EpisodicMemoryExpiration, ...]:
        self._require_scope(scope)
        with self._purge_transaction(TeamOperation.READ) as cursor:
            cursor.execute(
                "SELECT " + _EXPIRATION_COLUMNS + " FROM "
                "mnemo_team.episodic_memory_expirations AS expiration WHERE "
                "expiration.workspace_id = CAST(%s AS uuid) "
                "AND expiration.project_id = CAST(%s AS uuid) "
                "AND expiration.owner_id = CAST(%s AS uuid) AND expiration.visibility = %s "
                "AND expiration.session_id = CAST(%s AS uuid) "
                "AND expiration.task_id = CAST(%s AS uuid) "
                "AND NOT EXISTS (SELECT 1 FROM mnemo_team.episodic_memory_purges AS purge "
                "WHERE purge.workspace_id = expiration.workspace_id "
                "AND purge.memory_id = expiration.memory_id) "
                "ORDER BY expiration.expired_at ASC, expiration.memory_id ASC",
                _task_scope_values(scope),
            )
            expirations = tuple(self._expiration_from_row(row, scope) for row in cursor.fetchall())
            return tuple(
                sorted(
                    expirations,
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
        for purge in values:
            if not isinstance(purge, EpisodicMemoryPurge):
                raise TypeError("episodic memory purge batch is invalid")
            self._require_scope(purge.scope)
        with self._purge_transaction(TeamOperation.CONTRIBUTE) as cursor:
            existing_count = 0
            pending: list[EpisodicMemoryPurge] = []
            for purge in values:
                expiration = self._scoped_expiration(cursor, purge.scope, purge.memory_id)
                if expiration is None:
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
                existing = self._scoped_purge(cursor, purge.scope, purge.memory_id)
                if existing is not None:
                    if existing != purge:
                        raise EpisodicMemoryPurgeConflict(
                            "episodic memory already has a different purge"
                        )
                    existing_count += 1
                elif self._retention_target(cursor, purge.scope, purge.memory_id) is None:
                    raise EpisodicMemoryPurgeNotFound(
                        "episodic memory payload was not found for purge"
                    )
                else:
                    pending.append(purge)
            for purge in pending:
                cursor.execute(
                    "INSERT INTO mnemo_team.episodic_memory_purges("
                    "workspace_id, project_id, owner_id, visibility, session_id, task_id, "
                    "purge_id, expiration_id, memory_id, purged_at) VALUES ("
                    "CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, "
                    "CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), "
                    "CAST(%s AS uuid), CAST(%s AS uuid), %s)",
                    (
                        *_task_scope_values(purge.scope),
                        str(purge.purge_id),
                        str(purge.expiration_id),
                        str(purge.memory_id),
                        purge.purged_at.isoformat(),
                    ),
                )
                self._delete_memory_payload(cursor, purge.scope, purge.memory_id)
            return EpisodicMemoryPurgeResult(values, existing_count == len(values))

    def get_episodic_memory_purge(
        self, scope: MemoryScope, memory_id: MemoryId
    ) -> EpisodicMemoryPurge:
        self._require_scope(scope)
        if not isinstance(memory_id, MemoryId):
            raise TypeError("memory_id must be a MemoryId")
        with self._purge_transaction(TeamOperation.READ) as cursor:
            purge = self._scoped_purge(cursor, scope, memory_id)
            if purge is None:
                raise EpisodicMemoryPurgeNotFound("episodic memory purge was not found")
            return purge

    def delete_episodic_memory(
        self, deletion: EpisodicMemoryDeletion
    ) -> EpisodicMemoryDeletionResult:
        if not isinstance(deletion, EpisodicMemoryDeletion):
            raise TypeError("episodic memory deletion is invalid")
        self._require_scope(deletion.scope)
        if deletion.cause is not EpisodicDeletionCause.USER:
            raise EpisodicDeletionConflict("individual memory deletion must be user initiated")
        with self._deletion_transaction(TeamOperation.CONTRIBUTE) as cursor:
            existing = self._scoped_memory_deletion(cursor, deletion.scope, deletion.memory_id)
            if existing is not None:
                if existing != deletion:
                    raise EpisodicDeletionConflict("episodic memory already has another deletion")
                return EpisodicMemoryDeletionResult(existing, True)
            source_event_id = self._memory_deletion_target(
                cursor, deletion.scope, deletion.memory_id
            )
            if source_event_id is None:
                raise EpisodicDeletionNotFound("episodic memory deletion target was not found")
            if source_event_id != deletion.source_event_id:
                raise EpisodicDeletionConflict("episodic memory deletion source conflicts")
            self._ensure_memory_deletion_key_available(cursor, deletion)
            self._insert_memory_deletion(cursor, deletion)
            if self._retention_target(cursor, deletion.scope, deletion.memory_id) is not None:
                self._delete_memory_payload(cursor, deletion.scope, deletion.memory_id)
            return EpisodicMemoryDeletionResult(deletion, False)

    def delete_task_activity_event(
        self, deletion: TaskActivityEventDeletion
    ) -> TaskActivityDeletionResult:
        if not isinstance(deletion, TaskActivityEventDeletion):
            raise TypeError("task activity deletion is invalid")
        self._require_scope(deletion.scope)
        with self._deletion_transaction(TeamOperation.CONTRIBUTE) as cursor:
            existing = self._scoped_source_deletion(cursor, deletion.scope, deletion.event_id)
            if existing is not None:
                if existing != deletion:
                    raise EpisodicDeletionConflict("task activity event has another deletion")
                dependents = self._source_dependent_deletions(
                    cursor, deletion.scope, deletion.event_id
                )
                return TaskActivityDeletionResult(existing, dependents, True)
            if not self._source_deletion_target(cursor, deletion.scope, deletion.event_id):
                raise EpisodicDeletionNotFound("task activity deletion target was not found")
            cursor.execute(
                "SELECT 1 FROM mnemo_team.task_activity_event_deletions WHERE "
                "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
                "AND source_action_key = %s",
                (*_task_scope_values(deletion.scope), deletion.source_action_key),
            )
            if cursor.fetchone() is not None:
                raise EpisodicDeletionConflict("task activity deletion action key conflicts")
            memory_ids = self._source_memory_ids(cursor, deletion.scope, deletion.event_id)
            self._insert_source_deletion(cursor, deletion)
            dependent_deletions: list[EpisodicMemoryDeletion] = []
            for memory_id in memory_ids:
                dependent = EpisodicMemoryDeletion.from_source(
                    deletion, memory_id=memory_id, source_event_id=deletion.event_id
                )
                current = self._scoped_memory_deletion(cursor, dependent.scope, dependent.memory_id)
                if current is not None:
                    dependent_deletions.append(current)
                else:
                    self._ensure_memory_deletion_key_available(cursor, dependent)
                    self._insert_memory_deletion(cursor, dependent)
                    dependent_deletions.append(dependent)
                if self._retention_target(cursor, dependent.scope, dependent.memory_id) is not None:
                    self._delete_memory_payload(cursor, dependent.scope, dependent.memory_id)
            cursor.execute(
                "DELETE FROM mnemo_team.event_outbox WHERE workspace_id = CAST(%s AS uuid) "
                "AND topic = 'task_activity' AND source_event_id = CAST(%s AS uuid)",
                (str(self._workspace_id), str(deletion.event_id)),
            )
            cursor.execute(
                "DELETE FROM mnemo_team.task_activity_events WHERE workspace_id = CAST(%s AS uuid) "
                "AND project_id = CAST(%s AS uuid) AND owner_id = CAST(%s AS uuid) "
                "AND visibility = %s AND session_id = CAST(%s AS uuid) "
                "AND task_id = CAST(%s AS uuid) AND event_id = CAST(%s AS uuid)",
                (*_task_scope_values(deletion.scope), str(deletion.event_id)),
            )
            return TaskActivityDeletionResult(deletion, tuple(dependent_deletions), False)

    def get_episodic_memory_deletion(
        self, scope: MemoryScope, memory_id: MemoryId
    ) -> EpisodicMemoryDeletion:
        self._require_scope(scope)
        with self._deletion_transaction(TeamOperation.READ) as cursor:
            deletion = self._scoped_memory_deletion(cursor, scope, memory_id)
            if deletion is None:
                raise EpisodicDeletionNotFound("episodic memory deletion was not found")
            return deletion

    def get_task_activity_deletion(
        self, scope: MemoryScope, event_id: EventId
    ) -> TaskActivityEventDeletion:
        self._require_scope(scope)
        with self._deletion_transaction(TeamOperation.READ) as cursor:
            deletion = self._scoped_source_deletion(cursor, scope, event_id)
            if deletion is None:
                raise EpisodicDeletionNotFound("task activity deletion was not found")
            return deletion

    def _imported_lifecycle(
        self, cursor: PostgreSQLCursor, scope: MemoryScope
    ) -> tuple[
        tuple[EpisodicMemoryExpiration, ...],
        tuple[EpisodicMemoryPurge, ...],
        tuple[TaskActivityEventExpiration, ...],
        tuple[TaskActivityEventPurge, ...],
        tuple[EpisodicMemoryDeletion, ...],
        tuple[TaskActivityEventDeletion, ...],
    ]:
        cursor.execute(
            "SELECT lifecycle_kind, payload_json::text FROM "
            "mnemo_team.imported_episodic_lifecycle WHERE "
            "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
            "ORDER BY lifecycle_kind ASC, target_id ASC",
            _task_scope_values(scope),
        )
        memory_expirations: list[EpisodicMemoryExpiration] = []
        memory_purges: list[EpisodicMemoryPurge] = []
        task_expirations: list[TaskActivityEventExpiration] = []
        task_purges: list[TaskActivityEventPurge] = []
        memory_deletions: list[EpisodicMemoryDeletion] = []
        task_deletions: list[TaskActivityEventDeletion] = []
        for row in cursor.fetchall():
            payload = json.loads(str(row[1]))
            if not isinstance(payload, Mapping):
                raise ValueError("imported episodic lifecycle payload is invalid")
            kind = str(row[0])
            if kind == "memory_expiration":
                memory_expirations.append(EpisodicMemoryExpiration.from_dict(payload))
            elif kind == "memory_purge":
                memory_purges.append(EpisodicMemoryPurge.from_dict(payload))
            elif kind == "task_expiration":
                task_expirations.append(TaskActivityEventExpiration.from_dict(payload))
            elif kind == "task_purge":
                task_purges.append(TaskActivityEventPurge.from_dict(payload))
            elif kind == "memory_deletion":
                memory_deletions.append(EpisodicMemoryDeletion.from_dict(payload))
            elif kind == "task_deletion":
                task_deletions.append(TaskActivityEventDeletion.from_dict(payload))
            else:
                raise ValueError("imported episodic lifecycle kind is invalid")
        return (
            tuple(memory_expirations),
            tuple(memory_purges),
            tuple(task_expirations),
            tuple(task_purges),
            tuple(memory_deletions),
            tuple(task_deletions),
        )

    def _insert_candidate(
        self, cursor: PostgreSQLCursor, candidate: EpisodicMemoryCandidate
    ) -> None:
        memory = candidate.memory
        cursor.execute(
            "INSERT INTO mnemo_team.episodic_memory_candidates("
            "workspace_id, project_id, owner_id, visibility, session_id, task_id, memory_id, "
            "source_event_id, proposal_index, memory_kind, claim, confidence, sensitivity, "
            "status, extractor_version, provider_id, model_id, prompt_version, retention_json, "
            "created_at, evidence_json) VALUES (CAST(%s AS uuid), CAST(%s AS uuid), "
            "CAST(%s AS uuid), %s, CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), "
            "CAST(%s AS uuid), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "CAST(%s AS jsonb), %s, CAST(%s AS jsonb))",
            (
                *_task_scope_values(candidate.scope),
                str(candidate.memory_id),
                str(candidate.source_event_id),
                candidate.proposal_index,
                candidate.kind.value,
                memory.claim,
                candidate.confidence,
                memory.classification.sensitivity.value,
                memory.classification.status.value,
                candidate.extractor_version,
                candidate.provider_id,
                candidate.model_id,
                candidate.prompt_version,
                self._canonical_json(candidate.retention.to_dict()),
                candidate.created_at,
                self._evidence_json(candidate.evidence_references),
            ),
        )

    def _insert_review(
        self, cursor: PostgreSQLCursor, action: EpisodicCandidateReviewAction
    ) -> None:
        cursor.execute(
            "INSERT INTO mnemo_team.episodic_candidate_reviews("
            "workspace_id, project_id, owner_id, visibility, session_id, task_id, action_id, "
            "candidate_id, decision, actor, source_action_key, reason, reviewed_at, "
            "evidence_json) VALUES (CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, "
            "CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, %s, "
            "%s, %s, %s, CAST(%s AS jsonb))",
            (
                *_task_scope_values(action.scope),
                str(action.action_id),
                str(action.candidate_id),
                action.decision.value,
                action.actor.value,
                action.source_action_key,
                action.reason,
                action.reviewed_at,
                self._evidence_json(action.evidence_references),
            ),
        )

    def _insert_governance(
        self, cursor: PostgreSQLCursor, action: EpisodicMemoryGovernanceAction
    ) -> None:
        cursor.execute(
            "INSERT INTO mnemo_team.episodic_memory_governance("
            "workspace_id, project_id, owner_id, visibility, session_id, task_id, action_id, "
            "memory_id, action_kind, actor, expected_revision_id, source_action_key, reason, "
            "corrected_claim, corrected_sensitivity, occurred_at, evidence_json) VALUES ("
            "CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, CAST(%s AS uuid), "
            "CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, %s, CAST(%s AS uuid), "
            "%s, %s, %s, %s, %s, CAST(%s AS jsonb))",
            (
                *_task_scope_values(action.scope),
                str(action.action_id),
                str(action.memory_id),
                action.kind.value,
                action.actor.value,
                str(action.expected_revision_id),
                action.source_action_key,
                action.reason,
                action.corrected_claim,
                None
                if action.corrected_sensitivity is None
                else action.corrected_sensitivity.value,
                action.occurred_at,
                self._evidence_json(action.evidence_references),
            ),
        )

    @staticmethod
    def _ensure_memory_deletion_key_available(
        cursor: PostgreSQLCursor, deletion: EpisodicMemoryDeletion
    ) -> None:
        cursor.execute(
            "SELECT 1 FROM mnemo_team.episodic_memory_deletions WHERE "
            "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
            "AND source_action_key = %s",
            (*_task_scope_values(deletion.scope), deletion.source_action_key),
        )
        if cursor.fetchone() is not None:
            raise EpisodicDeletionConflict("episodic deletion action key conflicts")

    @staticmethod
    def _insert_memory_deletion(cursor: PostgreSQLCursor, deletion: EpisodicMemoryDeletion) -> None:
        cursor.execute(
            "INSERT INTO mnemo_team.episodic_memory_deletions("
            "workspace_id, project_id, owner_id, visibility, session_id, task_id, deletion_id, "
            "memory_id, source_event_id, cause, source_deletion_id, actor, source_action_key, "
            "deleted_at) VALUES (CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, "
            "CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), "
            "CAST(%s AS uuid), %s, CAST(%s AS uuid), %s, %s, %s)",
            (
                *_task_scope_values(deletion.scope),
                str(deletion.deletion_id),
                str(deletion.memory_id),
                str(deletion.source_event_id),
                deletion.cause.value,
                None if deletion.source_deletion_id is None else str(deletion.source_deletion_id),
                deletion.actor.value,
                deletion.source_action_key,
                deletion.deleted_at.isoformat(),
            ),
        )

    @staticmethod
    def _insert_source_deletion(
        cursor: PostgreSQLCursor, deletion: TaskActivityEventDeletion
    ) -> None:
        cursor.execute(
            "INSERT INTO mnemo_team.task_activity_event_deletions("
            "workspace_id, project_id, owner_id, visibility, session_id, task_id, deletion_id, "
            "event_id, actor, source_action_key, deleted_at) VALUES (CAST(%s AS uuid), "
            "CAST(%s AS uuid), CAST(%s AS uuid), %s, CAST(%s AS uuid), CAST(%s AS uuid), "
            "CAST(%s AS uuid), CAST(%s AS uuid), %s, %s, %s)",
            (
                *_task_scope_values(deletion.scope),
                str(deletion.deletion_id),
                str(deletion.event_id),
                deletion.actor.value,
                deletion.source_action_key,
                deletion.deleted_at.isoformat(),
            ),
        )

    def _scoped_candidate(
        self, cursor: PostgreSQLCursor, scope: MemoryScope, memory_id: MemoryId
    ) -> EpisodicMemoryCandidate | None:
        cursor.execute(
            "SELECT " + _CANDIDATE_COLUMNS + " FROM "
            "mnemo_team.episodic_memory_candidates WHERE "
            "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
            "AND memory_id = CAST(%s AS uuid) AND NOT EXISTS (SELECT 1 FROM "
            "mnemo_team.episodic_memory_expirations AS expiration WHERE "
            "expiration.workspace_id = episodic_memory_candidates.workspace_id "
            "AND expiration.memory_id = episodic_memory_candidates.memory_id)",
            (*_task_scope_values(scope), str(memory_id)),
        )
        row = cursor.fetchone()
        return None if row is None else self._candidate_from_row(row, scope)

    def _scoped_review(
        self, cursor: PostgreSQLCursor, scope: MemoryScope, candidate_id: MemoryId
    ) -> EpisodicCandidateReviewAction | None:
        cursor.execute(
            "SELECT " + _REVIEW_COLUMNS + " FROM mnemo_team.episodic_candidate_reviews WHERE "
            "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
            "AND candidate_id = CAST(%s AS uuid) AND NOT EXISTS (SELECT 1 FROM "
            "mnemo_team.episodic_memory_expirations AS expiration WHERE "
            "expiration.workspace_id = episodic_candidate_reviews.workspace_id "
            "AND expiration.memory_id = episodic_candidate_reviews.candidate_id)",
            (*_task_scope_values(scope), str(candidate_id)),
        )
        row = cursor.fetchone()
        return None if row is None else self._review_from_row(row, scope)

    def _optional_active(
        self, cursor: PostgreSQLCursor, scope: MemoryScope, memory_id: MemoryId
    ) -> ActiveEpisodicMemory | None:
        base = self._base_active(cursor, scope, memory_id)
        if base is None:
            return None
        current = self._revisions(cursor, base)[-1]
        if current.status is not EpisodicMemoryRevisionStatus.ACTIVE:
            return None
        return active_episodic_memory_at_revision(base, current)

    def _base_active(
        self, cursor: PostgreSQLCursor, scope: MemoryScope, memory_id: MemoryId
    ) -> ActiveEpisodicMemory | None:
        cursor.execute(
            "SELECT approval_action_id::text, activated_at FROM "
            "mnemo_team.active_episodic_memories WHERE workspace_id = CAST(%s AS uuid) "
            "AND project_id = CAST(%s AS uuid) AND owner_id = CAST(%s AS uuid) "
            "AND visibility = %s AND session_id = CAST(%s AS uuid) "
            "AND task_id = CAST(%s AS uuid) AND memory_id = CAST(%s AS uuid) "
            "AND NOT EXISTS (SELECT 1 FROM mnemo_team.episodic_memory_expirations "
            "AS expiration WHERE expiration.workspace_id = active_episodic_memories.workspace_id "
            "AND expiration.memory_id = active_episodic_memories.memory_id)",
            (*_task_scope_values(scope), str(memory_id)),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        candidate = self._scoped_candidate(cursor, scope, memory_id)
        review = self._scoped_review(cursor, scope, memory_id)
        if candidate is None or review is None:
            raise EpisodicMemoryReviewStorageFailure(
                "active episodic memory provenance is unavailable"
            )
        active = ActiveEpisodicMemory.approve(candidate, review)
        if str(active.approval_action_id) != str(row[0]) or active.activated_at != cast(
            datetime, row[1]
        ):
            raise EpisodicMemoryReviewStorageFailure("active episodic memory provenance conflicts")
        return active

    def _revisions(
        self, cursor: PostgreSQLCursor, base: ActiveEpisodicMemory
    ) -> tuple[EpisodicMemoryRevision, ...]:
        return replay_episodic_memory_revisions(
            base, self._governance_actions(cursor, base.scope, base.memory_id)
        )

    def _governance_actions(
        self, cursor: PostgreSQLCursor, scope: MemoryScope, memory_id: MemoryId
    ) -> tuple[EpisodicMemoryGovernanceAction, ...]:
        cursor.execute(
            "SELECT " + _GOVERNANCE_COLUMNS + " FROM "
            "mnemo_team.episodic_memory_governance WHERE "
            "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
            "AND memory_id = CAST(%s AS uuid) ORDER BY action_sequence ASC",
            (*_task_scope_values(scope), str(memory_id)),
        )
        return tuple(self._governance_from_row(row, scope) for row in cursor.fetchall())

    def _scoped_governance_by_action(
        self, cursor: PostgreSQLCursor, scope: MemoryScope, action_id: EventId
    ) -> EpisodicMemoryGovernanceAction | None:
        cursor.execute(
            "SELECT " + _GOVERNANCE_COLUMNS + " FROM "
            "mnemo_team.episodic_memory_governance WHERE "
            "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
            "AND action_id = CAST(%s AS uuid) AND NOT EXISTS (SELECT 1 FROM "
            "mnemo_team.episodic_memory_expirations AS expiration WHERE "
            "expiration.workspace_id = episodic_memory_governance.workspace_id "
            "AND expiration.memory_id = episodic_memory_governance.memory_id)",
            (*_task_scope_values(scope), str(action_id)),
        )
        row = cursor.fetchone()
        return None if row is None else self._governance_from_row(row, scope)

    def _memory_deletion_target(
        self, cursor: PostgreSQLCursor, scope: MemoryScope, memory_id: MemoryId
    ) -> EventId | None:
        cursor.execute(
            "SELECT source_event_id::text FROM mnemo_team.episodic_memory_candidates WHERE "
            "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
            "AND memory_id = CAST(%s AS uuid) UNION ALL SELECT source_event_id::text FROM "
            "mnemo_team.episodic_memory_expirations WHERE workspace_id = CAST(%s AS uuid) "
            "AND project_id = CAST(%s AS uuid) AND owner_id = CAST(%s AS uuid) "
            "AND visibility = %s AND session_id = CAST(%s AS uuid) "
            "AND task_id = CAST(%s AS uuid) AND memory_id = CAST(%s AS uuid) LIMIT 1",
            (
                *_task_scope_values(scope),
                str(memory_id),
                *_task_scope_values(scope),
                str(memory_id),
            ),
        )
        row = cursor.fetchone()
        return None if row is None else EventId.from_string(str(row[0]))

    def _source_deletion_target(
        self, cursor: PostgreSQLCursor, scope: MemoryScope, event_id: EventId
    ) -> bool:
        cursor.execute(
            "SELECT 1 FROM mnemo_team.task_activity_events WHERE "
            "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
            "AND event_id = CAST(%s AS uuid) UNION ALL SELECT 1 FROM "
            "mnemo_team.task_activity_event_expirations WHERE workspace_id = CAST(%s AS uuid) "
            "AND project_id = CAST(%s AS uuid) AND owner_id = CAST(%s AS uuid) "
            "AND visibility = %s AND session_id = CAST(%s AS uuid) "
            "AND task_id = CAST(%s AS uuid) AND event_id = CAST(%s AS uuid) LIMIT 1",
            (
                *_task_scope_values(scope),
                str(event_id),
                *_task_scope_values(scope),
                str(event_id),
            ),
        )
        return cursor.fetchone() is not None

    def _source_memory_ids(
        self, cursor: PostgreSQLCursor, scope: MemoryScope, event_id: EventId
    ) -> tuple[MemoryId, ...]:
        cursor.execute(
            "SELECT memory_id::text FROM mnemo_team.episodic_memory_candidates WHERE "
            "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
            "AND source_event_id = CAST(%s AS uuid) UNION SELECT memory_id::text FROM "
            "mnemo_team.episodic_memory_expirations WHERE workspace_id = CAST(%s AS uuid) "
            "AND project_id = CAST(%s AS uuid) AND owner_id = CAST(%s AS uuid) "
            "AND visibility = %s AND session_id = CAST(%s AS uuid) "
            "AND task_id = CAST(%s AS uuid) AND source_event_id = CAST(%s AS uuid) "
            "UNION SELECT memory_id::text FROM mnemo_team.episodic_memory_deletions WHERE "
            "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
            "AND source_event_id = CAST(%s AS uuid) ORDER BY 1 ASC",
            (
                *_task_scope_values(scope),
                str(event_id),
                *_task_scope_values(scope),
                str(event_id),
                *_task_scope_values(scope),
                str(event_id),
            ),
        )
        return tuple(MemoryId.from_string(str(row[0])) for row in cursor.fetchall())

    def _scoped_memory_deletion(
        self, cursor: PostgreSQLCursor, scope: MemoryScope, memory_id: MemoryId
    ) -> EpisodicMemoryDeletion | None:
        cursor.execute(
            "SELECT " + _MEMORY_DELETION_COLUMNS + " FROM "
            "mnemo_team.episodic_memory_deletions WHERE workspace_id = CAST(%s AS uuid) "
            "AND project_id = CAST(%s AS uuid) AND owner_id = CAST(%s AS uuid) "
            "AND visibility = %s AND session_id = CAST(%s AS uuid) "
            "AND task_id = CAST(%s AS uuid) AND memory_id = CAST(%s AS uuid)",
            (*_task_scope_values(scope), str(memory_id)),
        )
        row = cursor.fetchone()
        return None if row is None else self._memory_deletion_from_row(row, scope)

    def _scoped_source_deletion(
        self, cursor: PostgreSQLCursor, scope: MemoryScope, event_id: EventId
    ) -> TaskActivityEventDeletion | None:
        cursor.execute(
            "SELECT " + _SOURCE_DELETION_COLUMNS + " FROM "
            "mnemo_team.task_activity_event_deletions WHERE workspace_id = CAST(%s AS uuid) "
            "AND project_id = CAST(%s AS uuid) AND owner_id = CAST(%s AS uuid) "
            "AND visibility = %s AND session_id = CAST(%s AS uuid) "
            "AND task_id = CAST(%s AS uuid) AND event_id = CAST(%s AS uuid)",
            (*_task_scope_values(scope), str(event_id)),
        )
        row = cursor.fetchone()
        return None if row is None else self._source_deletion_from_row(row, scope)

    def _source_dependent_deletions(
        self, cursor: PostgreSQLCursor, scope: MemoryScope, event_id: EventId
    ) -> tuple[EpisodicMemoryDeletion, ...]:
        cursor.execute(
            "SELECT " + _MEMORY_DELETION_COLUMNS + " FROM "
            "mnemo_team.episodic_memory_deletions WHERE workspace_id = CAST(%s AS uuid) "
            "AND project_id = CAST(%s AS uuid) AND owner_id = CAST(%s AS uuid) "
            "AND visibility = %s AND session_id = CAST(%s AS uuid) "
            "AND task_id = CAST(%s AS uuid) AND source_event_id = CAST(%s AS uuid) "
            "ORDER BY memory_id ASC",
            (*_task_scope_values(scope), str(event_id)),
        )
        return tuple(self._memory_deletion_from_row(row, scope) for row in cursor.fetchall())

    def _retention_target(
        self, cursor: PostgreSQLCursor, scope: MemoryScope, memory_id: MemoryId
    ) -> EpisodicMemoryRetentionTarget | None:
        cursor.execute(
            "SELECT memory_id::text, source_event_id::text, retention_json::text FROM "
            "mnemo_team.episodic_memory_candidates WHERE "
            "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
            "AND memory_id = CAST(%s AS uuid)",
            (*_task_scope_values(scope), str(memory_id)),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return EpisodicMemoryRetentionTarget(
            MemoryId.from_string(str(row[0])),
            EventId.from_string(str(row[1])),
            scope,
            self._retention_from_json(row[2]),
        )

    def _scoped_expiration(
        self, cursor: PostgreSQLCursor, scope: MemoryScope, memory_id: MemoryId
    ) -> EpisodicMemoryExpiration | None:
        cursor.execute(
            "SELECT " + _EXPIRATION_COLUMNS + " FROM "
            "mnemo_team.episodic_memory_expirations WHERE "
            "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
            "AND memory_id = CAST(%s AS uuid)",
            (*_task_scope_values(scope), str(memory_id)),
        )
        row = cursor.fetchone()
        return None if row is None else self._expiration_from_row(row, scope)

    def _scoped_purge(
        self, cursor: PostgreSQLCursor, scope: MemoryScope, memory_id: MemoryId
    ) -> EpisodicMemoryPurge | None:
        cursor.execute(
            "SELECT " + _PURGE_COLUMNS + " FROM mnemo_team.episodic_memory_purges WHERE "
            "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
            "AND memory_id = CAST(%s AS uuid)",
            (*_task_scope_values(scope), str(memory_id)),
        )
        row = cursor.fetchone()
        return None if row is None else self._purge_from_row(row, scope)

    @staticmethod
    def _delete_memory_payload(
        cursor: PostgreSQLCursor, scope: MemoryScope, memory_id: MemoryId
    ) -> None:
        scope_values = _task_scope_values(scope)
        for table, target_column in (
            ("episodic_memory_governance", "memory_id"),
            ("active_episodic_memories", "memory_id"),
            ("episodic_candidate_reviews", "candidate_id"),
        ):
            cursor.execute(
                "DELETE FROM mnemo_team." + table + " WHERE workspace_id = CAST(%s AS uuid) "
                "AND project_id = CAST(%s AS uuid) AND owner_id = CAST(%s AS uuid) "
                "AND visibility = %s AND session_id = CAST(%s AS uuid) "
                "AND task_id = CAST(%s AS uuid) AND " + target_column + " = CAST(%s AS uuid)",
                (*scope_values, str(memory_id)),
            )
        cursor.execute(
            "DELETE FROM mnemo_team.episodic_memory_candidates WHERE "
            "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
            "AND memory_id = CAST(%s AS uuid)",
            (*scope_values, str(memory_id)),
        )
        if cursor.rowcount != 1:
            raise EpisodicMemoryPurgeConflict("episodic memory purge state changed concurrently")

    def _required_active(
        self, cursor: PostgreSQLCursor, scope: MemoryScope, memory_id: MemoryId
    ) -> ActiveEpisodicMemory:
        active = self._optional_active(cursor, scope, memory_id)
        if active is None:
            raise EpisodicMemoryReviewStorageFailure(
                "active episodic memory provenance is unavailable"
            )
        return active

    @staticmethod
    def _candidate_from_row(row: Sequence[object], scope: MemoryScope) -> EpisodicMemoryCandidate:
        retention = PostgreSQLEpisodicMemoryRepository._retention_from_json(row[13])
        evidence = PostgreSQLEpisodicMemoryRepository._evidence_from_json(row[15])
        memory = DurableClaim(
            MemoryId.from_string(str(row[1])),
            scope,
            MemoryClassification(Sensitivity(str(row[7])), MemoryStatus(str(row[8]))),
            retention,
            str(row[5]),
            evidence,
        )
        return EpisodicMemoryCandidate(
            memory,
            EpisodicMemoryKind(str(row[4])),
            EventId.from_string(str(row[2])),
            int(str(row[3])),
            float(str(row[6])),
            str(row[9]),
            str(row[10]),
            str(row[11]),
            str(row[12]),
            cast(datetime, row[14]),
        )

    @staticmethod
    def _review_from_row(
        row: Sequence[object], scope: MemoryScope
    ) -> EpisodicCandidateReviewAction:
        return EpisodicCandidateReviewAction(
            EventId.from_string(str(row[1])),
            scope,
            MemoryId.from_string(str(row[2])),
            EpisodicCandidateReviewDecision(str(row[3])),
            TaskActivityActor(str(row[4])),
            str(row[5]),
            str(row[6]),
            cast(datetime, row[7]),
            PostgreSQLEpisodicMemoryRepository._evidence_from_json(row[8]),
        )

    @staticmethod
    def _governance_from_row(
        row: Sequence[object], scope: MemoryScope
    ) -> EpisodicMemoryGovernanceAction:
        return EpisodicMemoryGovernanceAction(
            EventId.from_string(str(row[1])),
            scope,
            MemoryId.from_string(str(row[2])),
            EpisodicMemoryGovernanceKind(str(row[3])),
            TaskActivityActor(str(row[4])),
            EventId.from_string(str(row[5])),
            str(row[6]),
            str(row[7]),
            None if row[8] is None else str(row[8]),
            None if row[9] is None else Sensitivity(str(row[9])),
            cast(datetime, row[10]),
            PostgreSQLEpisodicMemoryRepository._evidence_from_json(row[11]),
        )

    @staticmethod
    def _expiration_from_row(row: Sequence[object], scope: MemoryScope) -> EpisodicMemoryExpiration:
        return EpisodicMemoryExpiration(
            EventId.from_string(str(row[1])),
            MemoryId.from_string(str(row[2])),
            EventId.from_string(str(row[3])),
            scope,
            RetentionPolicyId.from_string(str(row[4])),
            datetime.fromisoformat(str(row[5])),
            datetime.fromisoformat(str(row[6])),
        )

    @staticmethod
    def _purge_from_row(row: Sequence[object], scope: MemoryScope) -> EpisodicMemoryPurge:
        return EpisodicMemoryPurge(
            EventId.from_string(str(row[1])),
            EventId.from_string(str(row[2])),
            MemoryId.from_string(str(row[3])),
            scope,
            datetime.fromisoformat(str(row[4])),
        )

    @staticmethod
    def _memory_deletion_from_row(
        row: Sequence[object], scope: MemoryScope
    ) -> EpisodicMemoryDeletion:
        return EpisodicMemoryDeletion(
            EventId.from_string(str(row[1])),
            MemoryId.from_string(str(row[2])),
            EventId.from_string(str(row[3])),
            scope,
            EpisodicDeletionCause(str(row[4])),
            TaskActivityActor(str(row[6])),
            str(row[7]),
            datetime.fromisoformat(str(row[8])),
            None if row[5] is None else EventId.from_string(str(row[5])),
        )

    @staticmethod
    def _source_deletion_from_row(
        row: Sequence[object], scope: MemoryScope
    ) -> TaskActivityEventDeletion:
        return TaskActivityEventDeletion(
            EventId.from_string(str(row[1])),
            EventId.from_string(str(row[2])),
            scope,
            TaskActivityActor(str(row[3])),
            str(row[4]),
            datetime.fromisoformat(str(row[5])),
        )

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
        cls._require_task_scope(first.scope)
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

    def _require_scope(self, scope: MemoryScope) -> None:
        self._require_task_scope(scope)
        if scope.workspace_id != self._workspace_id:
            raise InvalidEpisodicMemoryCandidateScope(
                "team episodic candidates require the bound workspace"
            )

    @staticmethod
    def _require_task_scope(scope: MemoryScope) -> None:
        if (
            not isinstance(scope, MemoryScope)
            or scope.level is not ScopeLevel.TASK
            or scope.workspace_id is None
            or scope.project_id is None
            or scope.session_id is None
            or scope.task_id is None
        ):
            raise InvalidEpisodicMemoryCandidateScope(
                "team episodic candidates require exact task scope"
            )

    @staticmethod
    def _validate_page(offset: int, limit: int, label: str) -> None:
        if offset < 0 or limit < 1:
            raise ValueError(f"{label} offset must be non-negative and limit must be positive")

    @staticmethod
    def _require_aware(value: datetime, label: str) -> None:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{label} must be timezone-aware")

    @staticmethod
    def _canonical_json(value: object) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))

    @classmethod
    def _evidence_json(cls, evidence: tuple[EvidenceReference, ...]) -> str:
        return cls._canonical_json([item.to_dict() for item in evidence])

    @staticmethod
    def _evidence_from_json(value: object) -> tuple[EvidenceReference, ...]:
        payload = json.loads(str(value))
        if not isinstance(payload, list) or not all(isinstance(item, Mapping) for item in payload):
            raise ValueError("episodic evidence payload is invalid")
        return tuple(EvidenceReference.from_dict(item) for item in payload)

    @staticmethod
    def _retention_from_json(value: object) -> RetentionSchedule:
        payload = json.loads(str(value))
        if not isinstance(payload, Mapping):
            raise ValueError("episodic retention payload is invalid")
        return RetentionSchedule.from_dict(payload)

    @contextmanager
    def _candidate_transaction(self, operation: TeamOperation) -> Iterator[PostgreSQLCursor]:
        try:
            connection = self._connection_factory()
            connection.autocommit = False
        except Exception as error:
            raise EpisodicMemoryCandidateStorageFailure(
                "episodic candidate database connection failed"
            ) from error
        cursor = connection.cursor()
        try:
            self._configure(cursor, operation)
            yield cursor
            connection.commit()
        except EpisodicMemoryCandidateRepositoryError:
            connection.rollback()
            raise
        except Exception as error:
            connection.rollback()
            state = _sqlstate(error)
            if state == "42501" or (state is not None and state.startswith("23")):
                raise EpisodicMemoryCandidateConflict(
                    "episodic candidate database rejected conflicting state"
                ) from error
            raise EpisodicMemoryCandidateStorageFailure(
                "episodic candidate database operation failed"
            ) from error
        finally:
            cursor.close()
            connection.close()

    @contextmanager
    def _review_transaction(self, operation: TeamOperation) -> Iterator[PostgreSQLCursor]:
        try:
            connection = self._connection_factory()
            connection.autocommit = False
        except Exception as error:
            raise EpisodicMemoryReviewStorageFailure(
                "episodic review database connection failed"
            ) from error
        cursor = connection.cursor()
        try:
            self._configure(cursor, operation)
            yield cursor
            connection.commit()
        except EpisodicMemoryReviewRepositoryError:
            connection.rollback()
            raise
        except Exception as error:
            connection.rollback()
            state = _sqlstate(error)
            if state == "42501" or (state is not None and state.startswith("23")):
                raise EpisodicMemoryReviewConflict(
                    "episodic review database rejected conflicting state"
                ) from error
            raise EpisodicMemoryReviewStorageFailure(
                "episodic review database operation failed"
            ) from error
        finally:
            cursor.close()
            connection.close()

    @contextmanager
    def _governance_transaction(self, operation: TeamOperation) -> Iterator[PostgreSQLCursor]:
        try:
            connection = self._connection_factory()
            connection.autocommit = False
        except Exception as error:
            raise EpisodicMemoryGovernanceStorageFailure(
                "episodic governance database connection failed"
            ) from error
        cursor = connection.cursor()
        try:
            self._configure(cursor, operation)
            yield cursor
            connection.commit()
        except EpisodicMemoryGovernanceRepositoryError:
            connection.rollback()
            raise
        except Exception as error:
            connection.rollback()
            state = _sqlstate(error)
            if state == "42501" or (state is not None and state.startswith("23")):
                raise EpisodicMemoryGovernanceConflict(
                    "episodic governance database rejected conflicting state"
                ) from error
            raise EpisodicMemoryGovernanceStorageFailure(
                "episodic governance database operation failed"
            ) from error
        finally:
            cursor.close()
            connection.close()

    @contextmanager
    def _retention_transaction(self, operation: TeamOperation) -> Iterator[PostgreSQLCursor]:
        try:
            connection = self._connection_factory()
            connection.autocommit = False
        except Exception as error:
            raise EpisodicMemoryRetentionStorageFailure(
                "episodic retention database connection failed"
            ) from error
        cursor = connection.cursor()
        try:
            self._configure(cursor, operation)
            yield cursor
            connection.commit()
        except EpisodicMemoryRetentionRepositoryError:
            connection.rollback()
            raise
        except Exception as error:
            connection.rollback()
            state = _sqlstate(error)
            if state == "42501" or (state is not None and state.startswith("23")):
                raise EpisodicMemoryExpirationConflict(
                    "episodic retention database rejected conflicting state"
                ) from error
            raise EpisodicMemoryRetentionStorageFailure(
                "episodic retention database operation failed"
            ) from error
        finally:
            cursor.close()
            connection.close()

    @contextmanager
    def _purge_transaction(self, operation: TeamOperation) -> Iterator[PostgreSQLCursor]:
        try:
            connection = self._connection_factory()
            connection.autocommit = False
        except Exception as error:
            raise EpisodicMemoryPurgeStorageFailure(
                "episodic purge database connection failed"
            ) from error
        cursor = connection.cursor()
        try:
            self._configure(cursor, operation)
            yield cursor
            connection.commit()
        except EpisodicMemoryRetentionRepositoryError:
            connection.rollback()
            raise
        except Exception as error:
            connection.rollback()
            state = _sqlstate(error)
            if state == "42501" or (state is not None and state.startswith("23")):
                raise EpisodicMemoryPurgeConflict(
                    "episodic purge database rejected conflicting state"
                ) from error
            raise EpisodicMemoryPurgeStorageFailure(
                "episodic purge database operation failed"
            ) from error
        finally:
            cursor.close()
            connection.close()

    @contextmanager
    def _deletion_transaction(self, operation: TeamOperation) -> Iterator[PostgreSQLCursor]:
        try:
            connection = self._connection_factory()
            connection.autocommit = False
        except Exception as error:
            raise EpisodicDeletionStorageFailure(
                "episodic deletion database connection failed"
            ) from error
        cursor = connection.cursor()
        try:
            self._configure(cursor, operation)
            yield cursor
            connection.commit()
        except EpisodicDeletionRepositoryError:
            connection.rollback()
            raise
        except Exception as error:
            connection.rollback()
            state = _sqlstate(error)
            if state == "42501" or (state is not None and state.startswith("23")):
                raise EpisodicDeletionConflict(
                    "episodic deletion database rejected conflicting state"
                ) from error
            raise EpisodicDeletionStorageFailure(
                "episodic deletion database operation failed"
            ) from error
        finally:
            cursor.close()
            connection.close()

    @contextmanager
    def _export_transaction(self) -> Iterator[PostgreSQLCursor]:
        try:
            connection = self._connection_factory()
            connection.autocommit = False
        except Exception as error:
            raise EpisodicExportStorageFailure(
                "episodic export database connection failed"
            ) from error
        cursor = connection.cursor()
        try:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            self._configure(cursor, TeamOperation.READ)
            yield cursor
            connection.commit()
        except EpisodicExportRepositoryError:
            connection.rollback()
            raise
        except Exception as error:
            connection.rollback()
            raise EpisodicExportStorageFailure(
                "episodic export database operation failed"
            ) from error
        finally:
            cursor.close()
            connection.close()

    @contextmanager
    def _lifecycle_import_transaction(self) -> Iterator[PostgreSQLCursor]:
        try:
            connection = self._connection_factory()
            connection.autocommit = False
        except Exception as error:
            raise EpisodicLifecycleImportStorageFailure(
                "episodic lifecycle import database connection failed"
            ) from error
        cursor = connection.cursor()
        try:
            self._configure(cursor, TeamOperation.CONTRIBUTE)
            yield cursor
            connection.commit()
        except EpisodicLifecycleImportRepositoryError:
            connection.rollback()
            raise
        except Exception as error:
            connection.rollback()
            state = _sqlstate(error)
            if state == "42501" or (state is not None and state.startswith("23")):
                raise EpisodicLifecycleImportConflict(
                    "episodic lifecycle import database rejected conflicting state"
                ) from error
            raise EpisodicLifecycleImportStorageFailure(
                "episodic lifecycle import database operation failed"
            ) from error
        finally:
            cursor.close()
            connection.close()

    def _configure(self, cursor: PostgreSQLCursor, operation: TeamOperation) -> None:
        cursor.execute(
            "SELECT set_config('mnemo.principal_id', %s, true), "
            "set_config('mnemo.workspace_id', %s, true), "
            "set_config('mnemo.operation', %s, true), "
            "set_config('statement_timeout', %s, true)",
            (
                str(self._principal_id),
                str(self._workspace_id),
                operation.value,
                str(self._statement_timeout_ms),
            ),
        )
