"""PostgreSQL task-activity event and transactional outbox repositories."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime
from typing import cast

from mnemo_memory.packages.domain import (
    EventId,
    EventOutboxJob,
    EventOutboxTopic,
    EvidenceReference,
    MemoryScope,
    OutboxJobId,
    OwnerId,
    RetentionPolicyId,
    RetentionSchedule,
    ScopeLevel,
    Sensitivity,
    TaskActivityActor,
    TaskActivityEvent,
    TaskActivityEventExpiration,
    TaskActivityEventKind,
    TaskActivityEventPurge,
    TaskActivityEventRetentionTarget,
    WorkspaceId,
)
from mnemo_memory.packages.policy import TaskActivityEventSafetyPolicy, TeamOperation

from .contracts import (
    EventOutboxLeaseConflict,
    EventOutboxNotFound,
    EventOutboxProjectStatus,
    EventOutboxRepositoryError,
    EventOutboxStorageFailure,
    InvalidTaskActivityEventScope,
    TaskActivityEventConflict,
    TaskActivityEventNotFound,
    TaskActivityEventPage,
    TaskActivityEventRejected,
    TaskActivityEventRepositoryError,
    TaskActivityEventStorageFailure,
    TaskActivityEventStoreResult,
    TaskActivityExpirationResult,
    TaskActivityPurgeResult,
    TaskActivityRetentionConflict,
    TaskActivityRetentionNotFound,
    TaskActivityRetentionStorageFailure,
)
from .postgres import PostgreSQLConnectionFactory, PostgreSQLCursor

_TASK_EVENT_COLUMNS = (
    "event_id::text, event_kind, actor_kind, summary, source_event_key, sensitivity, "
    "retention_json::text, occurred_at, evidence_json::text"
)
_OUTBOX_COLUMNS = (
    "job_id::text, topic, source_event_id::text, event_kind, occurred_at, created_at, "
    "available_at, attempt_count, lease_owner, lease_expires_at, completed_at, "
    "last_failure_code"
)
_EXPIRATION_COLUMNS = (
    "expiration_sequence, expiration_id::text, event_id::text, retention_policy_id::text, "
    "scheduled_expires_at, expired_at"
)
_PURGE_COLUMNS = "purge_sequence, purge_id::text, expiration_id::text, event_id::text, purged_at"


def _sqlstate(error: Exception) -> str | None:
    for value in error.args:
        if isinstance(value, Mapping):
            state = value.get("C")
            if isinstance(state, str):
                return state
    return None


def _require_aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _task_scope_values(scope: MemoryScope) -> tuple[str, str, str, str, str, str]:
    if (
        scope.workspace_id is None
        or scope.project_id is None
        or scope.session_id is None
        or scope.task_id is None
    ):
        raise ValueError("team event storage requires exact task scope")
    return (
        str(scope.workspace_id),
        str(scope.project_id),
        str(scope.owner_id),
        scope.visibility.value,
        str(scope.session_id),
        str(scope.task_id),
    )


def _insert_outbox_job(cursor: PostgreSQLCursor, job: EventOutboxJob) -> None:
    cursor.execute(
        "INSERT INTO mnemo_team.event_outbox("
        "workspace_id, project_id, owner_id, visibility, session_id, task_id, job_id, topic, "
        "source_event_id, event_kind, occurred_at, created_at, available_at, attempt_count, "
        "lease_owner, lease_expires_at, completed_at, last_failure_code) VALUES ("
        "CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, CAST(%s AS uuid), "
        "CAST(%s AS uuid), CAST(%s AS uuid), %s, CAST(%s AS uuid), %s, %s, %s, %s, %s, "
        "%s, %s, %s, %s)",
        (
            *_task_scope_values(job.scope),
            str(job.job_id),
            job.topic.value,
            str(job.source_event_id),
            job.event_kind,
            job.occurred_at,
            job.created_at,
            job.available_at,
            job.attempt_count,
            job.lease_owner,
            job.lease_expires_at,
            job.completed_at,
            job.last_failure_code,
        ),
    )


class PostgreSQLTaskActivityEventRepository:
    """One principal/workspace-bound append-only minimized event repository."""

    def __init__(
        self,
        connection_factory: PostgreSQLConnectionFactory,
        *,
        principal_id: OwnerId,
        workspace_id: WorkspaceId,
        policy: TaskActivityEventSafetyPolicy | None = None,
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
        self._policy = policy or TaskActivityEventSafetyPolicy()
        self._statement_timeout_ms = statement_timeout_ms

    def append_task_activity_event(self, event: TaskActivityEvent) -> TaskActivityEventStoreResult:
        self._require_scope(event.scope)
        if not self._policy.assess(event).accepted:
            raise TaskActivityEventRejected("task activity event was rejected by safety policy")
        with self._transaction(TeamOperation.CONTRIBUTE) as cursor:
            cursor.execute(
                "SELECT 1 FROM mnemo_team.task_activity_event_expirations WHERE "
                "workspace_id = CAST(%s AS uuid) AND event_id = CAST(%s AS uuid) "
                "UNION ALL SELECT 1 FROM mnemo_team.task_activity_event_deletions WHERE "
                "workspace_id = CAST(%s AS uuid) AND event_id = CAST(%s AS uuid) "
                "UNION ALL SELECT 1 FROM mnemo_team.imported_episodic_lifecycle WHERE "
                "workspace_id = CAST(%s AS uuid) "
                "AND lifecycle_kind IN ('task_expiration', 'task_deletion') "
                "AND target_id = CAST(%s AS uuid)",
                (
                    str(self._workspace_id),
                    str(event.event_id),
                    str(self._workspace_id),
                    str(event.event_id),
                    str(self._workspace_id),
                    str(event.event_id),
                ),
            )
            if cursor.fetchone() is not None:
                raise TaskActivityEventConflict(
                    "task activity retention tombstone prevents resurrection"
                )
            cursor.execute(
                "SELECT " + _TASK_EVENT_COLUMNS + " FROM mnemo_team.task_activity_events WHERE "
                "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
                "AND source_event_key = %s",
                (*_task_scope_values(event.scope), event.source_event_key),
            )
            row = cursor.fetchone()
            if row is not None:
                stored = self._event_from_row(row, event.scope)
                if stored == event:
                    return TaskActivityEventStoreResult(stored, True)
                raise TaskActivityEventConflict("task activity event key conflicts")
            cursor.execute(
                "SELECT 1 FROM mnemo_team.task_activity_events "
                "WHERE workspace_id = CAST(%s AS uuid) AND event_id = CAST(%s AS uuid)",
                (str(self._workspace_id), str(event.event_id)),
            )
            if cursor.fetchone() is not None:
                raise TaskActivityEventConflict("task activity event identity conflicts")
            cursor.execute(
                "INSERT INTO mnemo_team.task_activity_events("
                "workspace_id, project_id, owner_id, visibility, session_id, task_id, "
                "event_id, source_event_key, event_kind, actor_kind, summary, sensitivity, "
                "retention_json, occurred_at, evidence_json) VALUES ("
                "CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, "
                "CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, %s, %s, %s, %s, "
                "CAST(%s AS jsonb), %s, CAST(%s AS jsonb))",
                (
                    *_task_scope_values(event.scope),
                    str(event.event_id),
                    event.source_event_key,
                    event.kind.value,
                    event.actor.value,
                    event.summary,
                    event.sensitivity.value,
                    json.dumps(event.retention.to_dict(), sort_keys=True, separators=(",", ":")),
                    event.occurred_at,
                    json.dumps(
                        [item.to_dict() for item in event.evidence_references],
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ),
            )
            _insert_outbox_job(
                cursor,
                EventOutboxJob.create(
                    scope=event.scope,
                    topic=EventOutboxTopic.TASK_ACTIVITY,
                    source_event_id=event.event_id,
                    event_kind=event.kind.value,
                    occurred_at=event.occurred_at,
                    created_at=event.occurred_at,
                ),
            )
            return TaskActivityEventStoreResult(event, False)

    def get_task_activity_event(self, scope: MemoryScope, event_id: EventId) -> TaskActivityEvent:
        self._require_scope(scope)
        if not isinstance(event_id, EventId):
            raise TypeError("event_id must be an EventId")
        with self._transaction(TeamOperation.READ) as cursor:
            event = self._select_event(cursor, scope, event_id)
            if event is None:
                raise TaskActivityEventNotFound("task activity event was not found")
            return event

    def list_task_activity_events(
        self, scope: MemoryScope, *, offset: int = 0, limit: int = 50
    ) -> TaskActivityEventPage:
        self._require_scope(scope)
        if offset < 0 or limit < 1:
            raise ValueError("event offset must be non-negative and limit must be positive")
        with self._transaction(TeamOperation.READ) as cursor:
            cursor.execute(
                "SELECT " + _TASK_EVENT_COLUMNS + " FROM mnemo_team.task_activity_events WHERE "
                "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
                "AND NOT EXISTS (SELECT 1 FROM "
                "mnemo_team.task_activity_event_expirations AS expiration WHERE "
                "expiration.workspace_id = task_activity_events.workspace_id "
                "AND expiration.event_id = task_activity_events.event_id) "
                "ORDER BY event_sequence DESC LIMIT %s OFFSET %s",
                (*_task_scope_values(scope), limit + 1, offset),
            )
            rows = tuple(cursor.fetchall())
            items = tuple(self._event_from_row(row, scope) for row in rows[:limit])
            return TaskActivityEventPage(items, offset + limit if len(rows) > limit else None)

    def list_due_task_activity_retention(
        self, scope: MemoryScope, *, as_of: datetime
    ) -> tuple[TaskActivityEventRetentionTarget, ...]:
        self._require_scope(scope)
        _require_aware(as_of, "as_of")
        with self._retention_transaction(TeamOperation.READ) as cursor:
            cursor.execute(
                "SELECT event_id::text, retention_json::text FROM "
                "mnemo_team.task_activity_events AS event WHERE "
                "event.workspace_id = CAST(%s AS uuid) "
                "AND event.project_id = CAST(%s AS uuid) "
                "AND event.owner_id = CAST(%s AS uuid) AND event.visibility = %s "
                "AND event.session_id = CAST(%s AS uuid) AND event.task_id = CAST(%s AS uuid) "
                "AND NOT EXISTS (SELECT 1 FROM "
                "mnemo_team.task_activity_event_expirations AS expiration WHERE "
                "expiration.workspace_id = event.workspace_id "
                "AND expiration.event_id = event.event_id)",
                _task_scope_values(scope),
            )
            targets = tuple(
                TaskActivityEventRetentionTarget(
                    EventId.from_string(str(row[0])),
                    scope,
                    self._retention_from_json(row[1]),
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
        for expiration in values:
            if not isinstance(expiration, TaskActivityEventExpiration):
                raise TypeError("task activity expiration batch is invalid")
            self._require_scope(expiration.scope)
        with self._retention_transaction(TeamOperation.CONTRIBUTE) as cursor:
            existing_count = 0
            pending: list[TaskActivityEventExpiration] = []
            for expiration in values:
                target = self._retention_target(cursor, expiration.scope, expiration.event_id)
                if target is None:
                    raise TaskActivityRetentionNotFound(
                        "task activity retention target was not found"
                    )
                schedule = target.retention
                if (
                    schedule.permanent
                    or schedule.policy_id != expiration.retention_policy_id
                    or schedule.expires_at != expiration.scheduled_expires_at
                    or not schedule.is_expired(expiration.expired_at)
                ):
                    raise TaskActivityRetentionConflict(
                        "task activity expiration does not match canonical retention"
                    )
                existing = self._scoped_expiration(cursor, expiration.scope, expiration.event_id)
                if existing is not None:
                    if existing != expiration:
                        raise TaskActivityRetentionConflict(
                            "task activity event already has a different expiration"
                        )
                    existing_count += 1
                else:
                    pending.append(expiration)
            for expiration in pending:
                cursor.execute(
                    "INSERT INTO mnemo_team.task_activity_event_expirations("
                    "workspace_id, project_id, owner_id, visibility, session_id, task_id, "
                    "expiration_id, event_id, retention_policy_id, scheduled_expires_at, "
                    "expired_at) VALUES (CAST(%s AS uuid), CAST(%s AS uuid), "
                    "CAST(%s AS uuid), %s, CAST(%s AS uuid), CAST(%s AS uuid), "
                    "CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, %s)",
                    (
                        *_task_scope_values(expiration.scope),
                        str(expiration.expiration_id),
                        str(expiration.event_id),
                        str(expiration.retention_policy_id),
                        expiration.scheduled_expires_at.isoformat(),
                        expiration.expired_at.isoformat(),
                    ),
                )
            return TaskActivityExpirationResult(values, existing_count == len(values))

    def get_task_activity_expiration(
        self, scope: MemoryScope, event_id: EventId
    ) -> TaskActivityEventExpiration:
        self._require_scope(scope)
        if not isinstance(event_id, EventId):
            raise TypeError("event_id must be an EventId")
        with self._retention_transaction(TeamOperation.READ) as cursor:
            expiration = self._scoped_expiration(cursor, scope, event_id)
            if expiration is None:
                raise TaskActivityRetentionNotFound("task activity expiration was not found")
            return expiration

    def list_unpurged_task_activity_expirations(
        self, scope: MemoryScope
    ) -> tuple[TaskActivityEventExpiration, ...]:
        self._require_scope(scope)
        with self._retention_transaction(TeamOperation.READ) as cursor:
            cursor.execute(
                "SELECT " + _EXPIRATION_COLUMNS + " FROM "
                "mnemo_team.task_activity_event_expirations AS expiration WHERE "
                "expiration.workspace_id = CAST(%s AS uuid) "
                "AND expiration.project_id = CAST(%s AS uuid) "
                "AND expiration.owner_id = CAST(%s AS uuid) AND expiration.visibility = %s "
                "AND expiration.session_id = CAST(%s AS uuid) "
                "AND expiration.task_id = CAST(%s AS uuid) "
                "AND NOT EXISTS (SELECT 1 FROM mnemo_team.task_activity_event_purges AS purge "
                "WHERE purge.workspace_id = expiration.workspace_id "
                "AND purge.event_id = expiration.event_id)",
                _task_scope_values(scope),
            )
            values = tuple(self._expiration_from_row(row, scope) for row in cursor.fetchall())
        return tuple(
            sorted(
                values,
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
        for purge in values:
            if not isinstance(purge, TaskActivityEventPurge):
                raise TypeError("task activity purge batch is invalid")
            self._require_scope(purge.scope)
        with self._retention_transaction(TeamOperation.CONTRIBUTE) as cursor:
            existing_count = 0
            pending: list[TaskActivityEventPurge] = []
            for purge in values:
                expiration = self._scoped_expiration(cursor, purge.scope, purge.event_id)
                if expiration is None:
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
                existing = self._scoped_purge(cursor, purge.scope, purge.event_id)
                if existing is not None:
                    if existing != purge:
                        raise TaskActivityRetentionConflict(
                            "task activity event already has a different purge"
                        )
                    existing_count += 1
                    continue
                if self._retention_target(cursor, purge.scope, purge.event_id) is None:
                    raise TaskActivityRetentionNotFound(
                        "task activity event payload was not found for purge"
                    )
                cursor.execute(
                    "SELECT 1 FROM mnemo_team.episodic_memory_candidates WHERE "
                    "workspace_id = CAST(%s AS uuid) AND source_event_id = CAST(%s AS uuid) "
                    "LIMIT 1",
                    (str(self._workspace_id), str(purge.event_id)),
                )
                if cursor.fetchone() is not None:
                    raise TaskActivityRetentionConflict(
                        "task activity event still has dependent episodic candidate payloads"
                    )
                pending.append(purge)
            for purge in pending:
                cursor.execute(
                    "INSERT INTO mnemo_team.task_activity_event_purges("
                    "workspace_id, project_id, owner_id, visibility, session_id, task_id, "
                    "purge_id, expiration_id, event_id, purged_at) VALUES ("
                    "CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, "
                    "CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), "
                    "CAST(%s AS uuid), CAST(%s AS uuid), %s)",
                    (
                        *_task_scope_values(purge.scope),
                        str(purge.purge_id),
                        str(purge.expiration_id),
                        str(purge.event_id),
                        purge.purged_at.isoformat(),
                    ),
                )
                cursor.execute(
                    "DELETE FROM mnemo_team.event_outbox WHERE workspace_id = CAST(%s AS uuid) "
                    "AND topic = 'task_activity' AND source_event_id = CAST(%s AS uuid)",
                    (str(self._workspace_id), str(purge.event_id)),
                )
                cursor.execute(
                    "DELETE FROM mnemo_team.task_activity_events WHERE "
                    "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                    "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                    "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
                    "AND event_id = CAST(%s AS uuid)",
                    (*_task_scope_values(purge.scope), str(purge.event_id)),
                )
                if cursor.rowcount != 1:
                    raise TaskActivityRetentionConflict(
                        "task activity purge state changed concurrently"
                    )
            return TaskActivityPurgeResult(values, existing_count == len(values))

    def get_task_activity_purge(
        self, scope: MemoryScope, event_id: EventId
    ) -> TaskActivityEventPurge:
        self._require_scope(scope)
        if not isinstance(event_id, EventId):
            raise TypeError("event_id must be an EventId")
        with self._retention_transaction(TeamOperation.READ) as cursor:
            purge = self._scoped_purge(cursor, scope, event_id)
            if purge is None:
                raise TaskActivityRetentionNotFound("task activity purge was not found")
            return purge

    def _retention_target(
        self, cursor: PostgreSQLCursor, scope: MemoryScope, event_id: EventId
    ) -> TaskActivityEventRetentionTarget | None:
        cursor.execute(
            "SELECT event_id::text, retention_json::text FROM "
            "mnemo_team.task_activity_events WHERE workspace_id = CAST(%s AS uuid) "
            "AND project_id = CAST(%s AS uuid) AND owner_id = CAST(%s AS uuid) "
            "AND visibility = %s AND session_id = CAST(%s AS uuid) "
            "AND task_id = CAST(%s AS uuid) AND event_id = CAST(%s AS uuid)",
            (*_task_scope_values(scope), str(event_id)),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return TaskActivityEventRetentionTarget(
            EventId.from_string(str(row[0])), scope, self._retention_from_json(row[1])
        )

    def _scoped_expiration(
        self, cursor: PostgreSQLCursor, scope: MemoryScope, event_id: EventId
    ) -> TaskActivityEventExpiration | None:
        cursor.execute(
            "SELECT " + _EXPIRATION_COLUMNS + " FROM "
            "mnemo_team.task_activity_event_expirations WHERE "
            "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
            "AND event_id = CAST(%s AS uuid)",
            (*_task_scope_values(scope), str(event_id)),
        )
        row = cursor.fetchone()
        return None if row is None else self._expiration_from_row(row, scope)

    def _scoped_purge(
        self, cursor: PostgreSQLCursor, scope: MemoryScope, event_id: EventId
    ) -> TaskActivityEventPurge | None:
        cursor.execute(
            "SELECT " + _PURGE_COLUMNS + " FROM "
            "mnemo_team.task_activity_event_purges WHERE "
            "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
            "AND event_id = CAST(%s AS uuid)",
            (*_task_scope_values(scope), str(event_id)),
        )
        row = cursor.fetchone()
        return None if row is None else self._purge_from_row(row, scope)

    def _select_event(
        self, cursor: PostgreSQLCursor, scope: MemoryScope, event_id: EventId
    ) -> TaskActivityEvent | None:
        cursor.execute(
            "SELECT " + _TASK_EVENT_COLUMNS + " FROM mnemo_team.task_activity_events WHERE "
            "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
            "AND event_id = CAST(%s AS uuid) AND NOT EXISTS (SELECT 1 FROM "
            "mnemo_team.task_activity_event_expirations AS expiration WHERE "
            "expiration.workspace_id = task_activity_events.workspace_id "
            "AND expiration.event_id = task_activity_events.event_id)",
            (*_task_scope_values(scope), str(event_id)),
        )
        row = cursor.fetchone()
        return None if row is None else self._event_from_row(row, scope)

    @staticmethod
    def _event_from_row(row: Sequence[object], scope: MemoryScope) -> TaskActivityEvent:
        retention = json.loads(str(row[6]))
        evidence = json.loads(str(row[8]))
        if (
            not isinstance(retention, Mapping)
            or not isinstance(evidence, list)
            or not all(isinstance(item, Mapping) for item in evidence)
        ):
            raise TaskActivityEventStorageFailure("task activity stored payload is invalid")
        return TaskActivityEvent(
            EventId.from_string(str(row[0])),
            scope,
            TaskActivityEventKind(str(row[1])),
            TaskActivityActor(str(row[2])),
            str(row[3]),
            str(row[4]),
            Sensitivity(str(row[5])),
            RetentionSchedule.from_dict(retention),
            cast(datetime, row[7]),
            tuple(EvidenceReference.from_dict(item) for item in evidence),
        )

    @staticmethod
    def _retention_from_json(value: object) -> RetentionSchedule:
        payload = json.loads(str(value))
        if not isinstance(payload, Mapping):
            raise ValueError("task activity retention payload is invalid")
        return RetentionSchedule.from_dict(payload)

    @staticmethod
    def _expiration_from_row(
        row: Sequence[object], scope: MemoryScope
    ) -> TaskActivityEventExpiration:
        return TaskActivityEventExpiration(
            EventId.from_string(str(row[1])),
            EventId.from_string(str(row[2])),
            scope,
            RetentionPolicyId.from_string(str(row[3])),
            datetime.fromisoformat(str(row[4])),
            datetime.fromisoformat(str(row[5])),
        )

    @staticmethod
    def _purge_from_row(row: Sequence[object], scope: MemoryScope) -> TaskActivityEventPurge:
        return TaskActivityEventPurge(
            EventId.from_string(str(row[1])),
            EventId.from_string(str(row[2])),
            EventId.from_string(str(row[3])),
            scope,
            datetime.fromisoformat(str(row[4])),
        )

    @contextmanager
    def _transaction(self, operation: TeamOperation) -> Iterator[PostgreSQLCursor]:
        try:
            connection = self._connection_factory()
            connection.autocommit = False
        except Exception as error:
            raise TaskActivityEventStorageFailure(
                "task activity database connection failed"
            ) from error
        cursor = connection.cursor()
        try:
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
            yield cursor
            connection.commit()
        except TaskActivityEventRepositoryError:
            connection.rollback()
            raise
        except Exception as error:
            connection.rollback()
            state = _sqlstate(error)
            if state == "42501" or (state is not None and state.startswith("23")):
                raise TaskActivityEventConflict(
                    "task activity database rejected conflicting state"
                ) from error
            raise TaskActivityEventStorageFailure(
                "task activity database operation failed"
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
            raise TaskActivityRetentionStorageFailure(
                "task activity retention database connection failed"
            ) from error
        cursor = connection.cursor()
        try:
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
            yield cursor
            connection.commit()
        except (TaskActivityRetentionConflict, TaskActivityRetentionNotFound):
            connection.rollback()
            raise
        except Exception as error:
            connection.rollback()
            state = _sqlstate(error)
            if state == "42501" or (state is not None and state.startswith("23")):
                raise TaskActivityRetentionConflict(
                    "task activity retention database rejected conflicting state"
                ) from error
            raise TaskActivityRetentionStorageFailure(
                "task activity retention database operation failed"
            ) from error
        finally:
            cursor.close()
            connection.close()

    def _require_scope(self, scope: MemoryScope) -> None:
        if (
            not isinstance(scope, MemoryScope)
            or scope.level is not ScopeLevel.TASK
            or scope.workspace_id != self._workspace_id
            or scope.project_id is None
            or scope.session_id is None
            or scope.task_id is None
        ):
            raise InvalidTaskActivityEventScope(
                "team task activity events require exact task scope"
            )


class PostgreSQLEventOutboxRepository:
    """One principal/workspace-bound PostgreSQL at-least-once delivery queue."""

    def __init__(
        self,
        connection_factory: PostgreSQLConnectionFactory,
        *,
        principal_id: OwnerId,
        workspace_id: WorkspaceId,
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
        self._statement_timeout_ms = statement_timeout_ms

    def claim_event_jobs(
        self,
        scope: MemoryScope,
        *,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
        limit: int,
    ) -> tuple[EventOutboxJob, ...]:
        self._require_task_scope(scope)
        EventOutboxJob.validate_worker_id(worker_id)
        _require_aware(now, "now")
        _require_aware(lease_expires_at, "lease_expires_at")
        if not 1 <= limit <= 100:
            raise ValueError("event outbox claim limit must be between 1 and 100")
        if lease_expires_at <= now:
            raise ValueError("event outbox lease must expire after claim time")
        with self._transaction(TeamOperation.CONTRIBUTE) as cursor:
            cursor.execute(
                "SELECT " + _OUTBOX_COLUMNS + " FROM mnemo_team.event_outbox WHERE "
                "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
                "AND completed_at IS NULL AND available_at <= %s "
                "AND (lease_expires_at IS NULL OR lease_expires_at <= %s) "
                "ORDER BY created_at ASC, job_id ASC LIMIT %s FOR UPDATE SKIP LOCKED",
                (*_task_scope_values(scope), now, now, limit),
            )
            claimed: list[EventOutboxJob] = []
            for row in cursor.fetchall():
                job = self._job_from_row(row, scope).claim(worker_id, lease_expires_at)
                cursor.execute(
                    "UPDATE mnemo_team.event_outbox SET attempt_count = %s, lease_owner = %s, "
                    "lease_expires_at = %s WHERE workspace_id = CAST(%s AS uuid) "
                    "AND job_id = CAST(%s AS uuid)",
                    (
                        job.attempt_count,
                        job.lease_owner,
                        job.lease_expires_at,
                        str(self._workspace_id),
                        str(job.job_id),
                    ),
                )
                claimed.append(job)
            return tuple(claimed)

    def get_project_event_job_status(
        self, scope: MemoryScope, *, now: datetime
    ) -> EventOutboxProjectStatus:
        self._require_project_scope(scope)
        _require_aware(now, "event outbox status time")
        with self._transaction(TeamOperation.READ) as cursor:
            cursor.execute(
                "SELECT "
                "COUNT(*) FILTER (WHERE (lease_expires_at IS NULL OR lease_expires_at <= %s) "
                "AND last_failure_code IS NULL), "
                "COUNT(*) FILTER (WHERE lease_expires_at > %s), "
                "COUNT(*) FILTER (WHERE (lease_expires_at IS NULL OR lease_expires_at <= %s) "
                "AND last_failure_code IS NOT NULL) "
                "FROM mnemo_team.event_outbox WHERE workspace_id = CAST(%s AS uuid) "
                "AND project_id = CAST(%s AS uuid) AND owner_id = CAST(%s AS uuid) "
                "AND visibility = %s AND completed_at IS NULL",
                (
                    now,
                    now,
                    now,
                    str(scope.workspace_id),
                    str(scope.project_id),
                    str(scope.owner_id),
                    scope.visibility.value,
                ),
            )
            row = cursor.fetchone()
            assert row is not None
            return EventOutboxProjectStatus(*(int(str(value)) for value in row))

    def requeue_failed_project_event_jobs(
        self, scope: MemoryScope, *, requested_at: datetime, limit: int
    ) -> int:
        self._require_project_scope(scope)
        _require_aware(requested_at, "event outbox retry time")
        if not 1 <= limit <= 100:
            raise ValueError("event outbox retry limit must be between 1 and 100")
        with self._transaction(TeamOperation.CONTRIBUTE) as cursor:
            cursor.execute(
                "SELECT job_id::text FROM mnemo_team.event_outbox WHERE "
                "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                "AND completed_at IS NULL AND last_failure_code IS NOT NULL "
                "AND (lease_expires_at IS NULL OR lease_expires_at <= %s) "
                "ORDER BY created_at ASC, job_id ASC LIMIT %s FOR UPDATE SKIP LOCKED",
                (
                    str(scope.workspace_id),
                    str(scope.project_id),
                    str(scope.owner_id),
                    scope.visibility.value,
                    requested_at,
                    limit,
                ),
            )
            job_ids = tuple(str(row[0]) for row in cursor.fetchall())
            for job_id in job_ids:
                cursor.execute(
                    "UPDATE mnemo_team.event_outbox SET available_at = %s, lease_owner = NULL, "
                    "lease_expires_at = NULL, last_failure_code = NULL "
                    "WHERE workspace_id = CAST(%s AS uuid) AND job_id = CAST(%s AS uuid)",
                    (requested_at, str(self._workspace_id), job_id),
                )
            return len(job_ids)

    def complete_event_job(
        self,
        scope: MemoryScope,
        job_id: OutboxJobId,
        *,
        worker_id: str,
        completed_at: datetime,
    ) -> EventOutboxJob:
        self._require_task_scope(scope)
        EventOutboxJob.validate_worker_id(worker_id)
        _require_aware(completed_at, "completed_at")
        with self._transaction(TeamOperation.CONTRIBUTE) as cursor:
            job = self._scoped_job(cursor, scope, job_id, for_update=True)
            self._require_lease(job, worker_id, completed_at)
            completed = job.complete(completed_at)
            cursor.execute(
                "UPDATE mnemo_team.event_outbox SET completed_at = %s, lease_owner = NULL, "
                "lease_expires_at = NULL, last_failure_code = NULL "
                "WHERE workspace_id = CAST(%s AS uuid) AND job_id = CAST(%s AS uuid)",
                (completed_at, str(self._workspace_id), str(job_id)),
            )
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
        self._require_task_scope(scope)
        EventOutboxJob.validate_worker_id(worker_id)
        EventOutboxJob.validate_failure_code(failure_code)
        _require_aware(now, "now")
        _require_aware(available_at, "available_at")
        if available_at < now:
            raise ValueError("event outbox retry cannot be scheduled in the past")
        with self._transaction(TeamOperation.CONTRIBUTE) as cursor:
            job = self._scoped_job(cursor, scope, job_id, for_update=True)
            self._require_lease(job, worker_id, now)
            retried = job.retry(available_at, failure_code)
            cursor.execute(
                "UPDATE mnemo_team.event_outbox SET available_at = %s, lease_owner = NULL, "
                "lease_expires_at = NULL, last_failure_code = %s "
                "WHERE workspace_id = CAST(%s AS uuid) AND job_id = CAST(%s AS uuid)",
                (available_at, failure_code, str(self._workspace_id), str(job_id)),
            )
            return retried

    def get_event_job(self, scope: MemoryScope, job_id: OutboxJobId) -> EventOutboxJob:
        self._require_task_scope(scope)
        with self._transaction(TeamOperation.READ) as cursor:
            return self._scoped_job(cursor, scope, job_id)

    def _scoped_job(
        self,
        cursor: PostgreSQLCursor,
        scope: MemoryScope,
        job_id: OutboxJobId,
        *,
        for_update: bool = False,
    ) -> EventOutboxJob:
        if not isinstance(job_id, OutboxJobId):
            raise TypeError("job_id must be an OutboxJobId")
        cursor.execute(
            "SELECT " + _OUTBOX_COLUMNS + " FROM mnemo_team.event_outbox WHERE "
            "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
            "AND job_id = CAST(%s AS uuid)" + (" FOR UPDATE" if for_update else ""),
            (*_task_scope_values(scope), str(job_id)),
        )
        row = cursor.fetchone()
        if row is None:
            raise EventOutboxNotFound("event outbox job was not found")
        return self._job_from_row(row, scope)

    @staticmethod
    def _job_from_row(row: Sequence[object], scope: MemoryScope) -> EventOutboxJob:
        return EventOutboxJob(
            OutboxJobId.from_string(str(row[0])),
            scope,
            EventOutboxTopic(str(row[1])),
            EventId.from_string(str(row[2])),
            str(row[3]),
            cast(datetime, row[4]),
            cast(datetime, row[5]),
            cast(datetime, row[6]),
            int(str(row[7])),
            None if row[8] is None else str(row[8]),
            cast(datetime | None, row[9]),
            cast(datetime | None, row[10]),
            None if row[11] is None else str(row[11]),
        )

    @staticmethod
    def _require_lease(job: EventOutboxJob, worker_id: str, now: datetime) -> None:
        if (
            job.completed_at is not None
            or job.lease_owner != worker_id
            or job.lease_expires_at is None
            or job.lease_expires_at <= now
        ):
            raise EventOutboxLeaseConflict("event outbox lease is not owned by this worker")

    @contextmanager
    def _transaction(self, operation: TeamOperation) -> Iterator[PostgreSQLCursor]:
        try:
            connection = self._connection_factory()
            connection.autocommit = False
        except Exception as error:
            raise EventOutboxStorageFailure("event outbox database connection failed") from error
        cursor = connection.cursor()
        try:
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
            yield cursor
            connection.commit()
        except EventOutboxRepositoryError:
            connection.rollback()
            raise
        except Exception as error:
            connection.rollback()
            raise EventOutboxStorageFailure("event outbox database operation failed") from error
        finally:
            cursor.close()
            connection.close()

    def _require_task_scope(self, scope: MemoryScope) -> None:
        if (
            not isinstance(scope, MemoryScope)
            or scope.level is not ScopeLevel.TASK
            or scope.workspace_id != self._workspace_id
            or scope.project_id is None
            or scope.session_id is None
            or scope.task_id is None
        ):
            raise ValueError("team event outbox requires exact task scope")

    def _require_project_scope(self, scope: MemoryScope) -> None:
        if (
            not isinstance(scope, MemoryScope)
            or scope.level is not ScopeLevel.PROJECT
            or scope.workspace_id != self._workspace_id
            or scope.project_id is None
        ):
            raise ValueError("team event outbox inspection requires exact project scope")
