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
    EpisodicMemoryCandidate,
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
    EpisodicMemoryCandidateConflict,
    EpisodicMemoryCandidateNotFound,
    EpisodicMemoryCandidatePage,
    EpisodicMemoryCandidateRejected,
    EpisodicMemoryCandidateRepositoryError,
    EpisodicMemoryCandidateStorageFailure,
    EpisodicMemoryCandidateStoreResult,
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
    InvalidEpisodicMemoryCandidateScope,
)
from .postgres import PostgreSQLConnectionFactory, PostgreSQLCursor
from .postgres_events import _task_scope_values

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
                "workspace_id = CAST(%s AS uuid) AND memory_id = ANY(CAST(%s AS uuid[]))",
                (
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
                self._delete_expired_payload(cursor, purge)
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
    def _delete_expired_payload(cursor: PostgreSQLCursor, purge: EpisodicMemoryPurge) -> None:
        scope_values = _task_scope_values(purge.scope)
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
                (*scope_values, str(purge.memory_id)),
            )
        cursor.execute(
            "DELETE FROM mnemo_team.episodic_memory_candidates WHERE "
            "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
            "AND memory_id = CAST(%s AS uuid)",
            (*scope_values, str(purge.memory_id)),
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
