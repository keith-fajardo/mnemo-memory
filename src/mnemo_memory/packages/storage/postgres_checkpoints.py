"""PostgreSQL implementation of scoped checkpoint and lifecycle-event repositories."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from typing import cast

from mnemo_memory.packages.domain import (
    CheckpointAggregate,
    CheckpointContent,
    CheckpointEventKind,
    CheckpointId,
    CheckpointLifecycleEvent,
    CheckpointRevision,
    CheckpointRevisionId,
    CheckpointStatus,
    EventId,
    EvidenceReference,
    MemoryScope,
    OwnerId,
    ScopeLevel,
    WorkspaceId,
)
from mnemo_memory.packages.policy import TeamOperation

from .contracts import (
    CheckpointNotFound,
    CheckpointPage,
    CheckpointRepositoryError,
    DuplicateCheckpoint,
    EpisodicEventNotFound,
    EpisodicEventPage,
    EpisodicEventRepositoryError,
    EpisodicEventStorageFailure,
    EpisodicEventStoreResult,
    InvalidAbandonmentReason,
    InvalidCheckpointScope,
    InvalidEpisodicEventScope,
    InvalidLifecycleTransition,
    RepositoryStorageFailure,
    RevisionConflict,
)
from .postgres import PostgreSQLConnectionFactory, PostgreSQLCursor

_AGGREGATE_COLUMNS = (
    "checkpoint_id::text, current_revision_id::text, current_revision_number, "
    "lifecycle_status, created_at, updated_at"
)
_REVISION_COLUMNS = (
    "revision.revision_id::text, revision.checkpoint_id::text, "
    "revision.revision_number, revision.predecessor_revision_id::text, "
    "revision.status, revision.content_json::text, revision.evidence_json::text, "
    "revision.created_at"
)
_EVENT_COLUMNS = (
    "event.event_id::text, event.event_kind, event.checkpoint_id::text, "
    "event.revision_id::text, event.revision_number, event.occurred_at, "
    "event.idempotency_key, revision.evidence_json::text"
)


def _sqlstate(error: Exception) -> str | None:
    for value in error.args:
        if isinstance(value, Mapping):
            state = value.get("C")
            if isinstance(state, str):
                return state
    return None


class PostgreSQLCheckpointRepository:
    """One principal/workspace-bound durable team checkpoint repository."""

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

    def create_checkpoint_aggregate(
        self, aggregate: CheckpointAggregate, initial_revision: CheckpointRevision
    ) -> None:
        self._require_scope(aggregate.scope)
        if (
            aggregate.checkpoint_id != initial_revision.checkpoint_id
            or aggregate.scope != initial_revision.scope
            or aggregate.current_revision_id != initial_revision.revision_id
            or aggregate.current_revision_number != 1
            or initial_revision.revision_number != 1
            or initial_revision.predecessor_revision_id is not None
            or aggregate.lifecycle_status is not CheckpointStatus.ACTIVE
            or initial_revision.status is not CheckpointStatus.ACTIVE
        ):
            raise InvalidLifecycleTransition(
                "initial aggregate and revision must be active revision one"
            )
        with self._transaction(TeamOperation.CONTRIBUTE) as cursor:
            if self._select_aggregate(cursor, aggregate.scope, aggregate.checkpoint_id) is not None:
                raise DuplicateCheckpoint("checkpoint already exists in this scope")
            cursor.execute(
                "INSERT INTO mnemo_team.checkpoint_aggregates("
                "workspace_id, project_id, owner_id, visibility, session_id, task_id, "
                "checkpoint_id, current_revision_id, current_revision_number, "
                "lifecycle_status, created_at, updated_at) VALUES ("
                "CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, "
                "CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), "
                "%s, %s, %s, %s)",
                (
                    *self._scope_values(aggregate.scope),
                    str(aggregate.checkpoint_id),
                    str(aggregate.current_revision_id),
                    aggregate.current_revision_number,
                    aggregate.lifecycle_status.value,
                    aggregate.created_at,
                    aggregate.updated_at,
                ),
            )
            self._insert_revision(cursor, initial_revision)
            self._insert_event(
                cursor,
                CheckpointLifecycleEvent.for_revision(
                    scope=aggregate.scope,
                    kind=CheckpointEventKind.CREATED,
                    checkpoint_id=initial_revision.checkpoint_id,
                    revision_id=initial_revision.revision_id,
                    revision_number=initial_revision.revision_number,
                    occurred_at=initial_revision.created_at,
                    evidence_references=initial_revision.evidence_references,
                ),
            )

    def get_aggregate(self, scope: MemoryScope, checkpoint_id: CheckpointId) -> CheckpointAggregate:
        self._require_scope(scope)
        if not isinstance(checkpoint_id, CheckpointId):
            raise TypeError("checkpoint_id must be a CheckpointId")
        with self._transaction(TeamOperation.READ) as cursor:
            aggregate = self._select_aggregate(cursor, scope, checkpoint_id)
            if aggregate is None:
                raise CheckpointNotFound("checkpoint was not found")
            return aggregate

    def get_current_revision(
        self, scope: MemoryScope, checkpoint_id: CheckpointId
    ) -> CheckpointRevision:
        self._require_scope(scope)
        if not isinstance(checkpoint_id, CheckpointId):
            raise TypeError("checkpoint_id must be a CheckpointId")
        with self._transaction(TeamOperation.READ) as cursor:
            cursor.execute(
                "SELECT "
                + _REVISION_COLUMNS
                + " FROM mnemo_team.checkpoint_aggregates AS aggregate "
                "JOIN mnemo_team.checkpoint_revisions AS revision "
                "ON revision.workspace_id = aggregate.workspace_id "
                "AND revision.revision_id = aggregate.current_revision_id "
                "WHERE aggregate.workspace_id = CAST(%s AS uuid) "
                "AND aggregate.project_id = CAST(%s AS uuid) "
                "AND aggregate.owner_id = CAST(%s AS uuid) AND aggregate.visibility = %s "
                "AND aggregate.session_id = CAST(%s AS uuid) "
                "AND aggregate.task_id = CAST(%s AS uuid) "
                "AND aggregate.checkpoint_id = CAST(%s AS uuid)",
                (*self._scope_values(scope), str(checkpoint_id)),
            )
            row = cursor.fetchone()
            if row is None:
                raise CheckpointNotFound("checkpoint was not found")
            return self._revision_from_row(row, scope)

    def get_revision(
        self,
        scope: MemoryScope,
        checkpoint_id: CheckpointId,
        *,
        revision_number: int | None = None,
        revision_id: CheckpointRevisionId | None = None,
    ) -> CheckpointRevision:
        self._require_scope(scope)
        if not isinstance(checkpoint_id, CheckpointId):
            raise TypeError("checkpoint_id must be a CheckpointId")
        if (revision_number is None) == (revision_id is None):
            raise ValueError("provide exactly one revision selector")
        if revision_number is not None:
            if not isinstance(revision_number, int) or isinstance(revision_number, bool):
                raise ValueError("revision number must be an integer")
            selector = "revision_number = %s"
            selector_value: object = revision_number
        else:
            if not isinstance(revision_id, CheckpointRevisionId):
                raise TypeError("revision_id must be a CheckpointRevisionId")
            selector = "revision_id = CAST(%s AS uuid)"
            selector_value = str(revision_id)
        with self._transaction(TeamOperation.READ) as cursor:
            cursor.execute(
                "SELECT "
                + _REVISION_COLUMNS
                + " FROM mnemo_team.checkpoint_revisions AS revision WHERE "
                "revision.workspace_id = CAST(%s AS uuid) "
                "AND revision.project_id = CAST(%s AS uuid) "
                "AND revision.owner_id = CAST(%s AS uuid) AND revision.visibility = %s "
                "AND revision.session_id = CAST(%s AS uuid) "
                "AND revision.task_id = CAST(%s AS uuid) "
                "AND revision.checkpoint_id = CAST(%s AS uuid) AND " + selector,
                (*self._scope_values(scope), str(checkpoint_id), selector_value),
            )
            row = cursor.fetchone()
            if row is None:
                raise CheckpointNotFound("checkpoint was not found")
            return self._revision_from_row(row, scope)

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
        if event_kind not in (CheckpointEventKind.REVISED, CheckpointEventKind.LESSON_RECORDED):
            raise InvalidLifecycleTransition("active checkpoint revision event kind is invalid")
        return self._mutate_revision(
            scope,
            checkpoint_id,
            expected_revision_id,
            CheckpointStatus.ACTIVE,
            content,
            evidence_references,
            created_at,
            reason=None,
            event_kind=event_kind,
        )

    def complete_checkpoint(
        self,
        scope: MemoryScope,
        checkpoint_id: CheckpointId,
        expected_revision_id: CheckpointRevisionId,
        content: CheckpointContent,
        evidence_references: tuple[EvidenceReference, ...],
        created_at: datetime,
    ) -> CheckpointRevision:
        return self._mutate_revision(
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
        return self._mutate_revision(
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
        with self._transaction(TeamOperation.READ) as cursor:
            cursor.execute(
                "SELECT " + _AGGREGATE_COLUMNS + " FROM mnemo_team.checkpoint_aggregates "
                "WHERE workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
                "AND lifecycle_status = 'active' "
                "ORDER BY updated_at DESC, checkpoint_id ASC LIMIT %s OFFSET %s",
                (*self._scope_values(scope), limit + 1, offset),
            )
            rows = tuple(cursor.fetchall())
            items = tuple(self._aggregate_from_row(row, scope) for row in rows[:limit])
            return CheckpointPage(items, offset + limit if len(rows) > limit else None)

    def select_current_checkpoint(self, scope: MemoryScope) -> CheckpointAggregate | None:
        items = self.list_current_checkpoints(scope, limit=1).items
        return items[0] if items else None

    def append_event(self, event: CheckpointLifecycleEvent) -> EpisodicEventStoreResult:
        self._require_event_scope(event.scope)
        with self._transaction(TeamOperation.CONTRIBUTE, event_storage=True) as cursor:
            revision = self._select_revision(
                cursor, event.scope, event.checkpoint_id, event.revision_id
            )
            if (
                revision is None
                or revision.revision_number != event.revision_number
                or revision.created_at != event.occurred_at
                or revision.evidence_references != event.evidence_references
            ):
                raise InvalidEpisodicEventScope(
                    "event does not match its scoped checkpoint revision"
                )
            existing = self._select_event_by_key(cursor, event.scope, event.idempotency_key)
            if existing is not None:
                if existing == event:
                    return EpisodicEventStoreResult(existing, True)
                raise InvalidEpisodicEventScope("event idempotency key conflicts")
            existing_identity = self._select_event(cursor, event.scope, event.event_id)
            if existing_identity is not None:
                raise InvalidEpisodicEventScope("event identity conflicts")
            self._insert_event(cursor, event)
            return EpisodicEventStoreResult(event, False)

    def get_event(self, scope: MemoryScope, event_id: EventId) -> CheckpointLifecycleEvent:
        self._require_event_scope(scope)
        if not isinstance(event_id, EventId):
            raise TypeError("event_id must be an EventId")
        with self._transaction(TeamOperation.READ, event_storage=True) as cursor:
            event = self._select_event(cursor, scope, event_id)
            if event is None:
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
        self._require_event_scope(scope)
        if checkpoint_id is not None and not isinstance(checkpoint_id, CheckpointId):
            raise TypeError("checkpoint_id must be a CheckpointId")
        if offset < 0 or limit < 1:
            raise ValueError("event offset must be non-negative and limit must be positive")
        checkpoint_filter = (
            "" if checkpoint_id is None else " AND event.checkpoint_id = CAST(%s AS uuid)"
        )
        values: tuple[object, ...] = self._scope_values(scope)
        if checkpoint_id is not None:
            values += (str(checkpoint_id),)
        values += (limit + 1, offset)
        with self._transaction(TeamOperation.READ, event_storage=True) as cursor:
            cursor.execute(
                "SELECT "
                + _EVENT_COLUMNS
                + " FROM mnemo_team.checkpoint_lifecycle_events AS event "
                "JOIN mnemo_team.checkpoint_revisions AS revision "
                "ON revision.workspace_id = event.workspace_id "
                "AND revision.revision_id = event.revision_id "
                "WHERE event.workspace_id = CAST(%s AS uuid) "
                "AND event.project_id = CAST(%s AS uuid) "
                "AND event.owner_id = CAST(%s AS uuid) AND event.visibility = %s "
                "AND event.session_id = CAST(%s AS uuid) "
                "AND event.task_id = CAST(%s AS uuid)"
                + checkpoint_filter
                + " ORDER BY event.event_sequence DESC LIMIT %s OFFSET %s",
                values,
            )
            rows = tuple(cursor.fetchall())
            items = tuple(self._event_from_row(row, scope) for row in rows[:limit])
            return EpisodicEventPage(items, offset + limit if len(rows) > limit else None)

    def _mutate_revision(
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
        self._require_scope(scope)
        if not isinstance(checkpoint_id, CheckpointId):
            raise TypeError("checkpoint_id must be a CheckpointId")
        if not isinstance(expected_revision_id, CheckpointRevisionId):
            raise TypeError("expected_revision_id must be a CheckpointRevisionId")
        if not isinstance(content, CheckpointContent):
            raise TypeError("content must be a CheckpointContent")
        with self._transaction(TeamOperation.CONTRIBUTE) as cursor:
            aggregate = self._select_aggregate(cursor, scope, checkpoint_id, for_update=True)
            if aggregate is None:
                raise CheckpointNotFound("checkpoint was not found")
            current = self._select_revision(
                cursor, scope, checkpoint_id, aggregate.current_revision_id
            )
            if current is None:
                raise RepositoryStorageFailure("checkpoint storage is inconsistent")
            if aggregate.lifecycle_status is not CheckpointStatus.ACTIVE:
                if self._is_identical_terminal_retry(
                    current,
                    expected_revision_id,
                    status,
                    content,
                    evidence_references,
                    reason,
                ):
                    return current
                raise InvalidLifecycleTransition("checkpoint is already terminal")
            if aggregate.current_revision_id != expected_revision_id:
                raise RevisionConflict("expected revision is not current")
            if status is CheckpointStatus.COMPLETED and (
                content.blockers or content.remaining_work
            ):
                raise InvalidLifecycleTransition(
                    "completed checkpoint cannot contain blockers or remaining work"
                )
            revision = CheckpointRevision(
                CheckpointRevisionId.new(),
                checkpoint_id,
                aggregate.current_revision_number + 1,
                aggregate.current_revision_id,
                scope,
                content,
                status,
                evidence_references,
                created_at,
            )
            self._insert_revision(cursor, revision)
            cursor.execute(
                "UPDATE mnemo_team.checkpoint_aggregates SET "
                "current_revision_id = CAST(%s AS uuid), current_revision_number = %s, "
                "lifecycle_status = %s, updated_at = %s "
                "WHERE workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
                "AND checkpoint_id = CAST(%s AS uuid) AND lifecycle_status = 'active' "
                "AND current_revision_id = CAST(%s AS uuid)",
                (
                    str(revision.revision_id),
                    revision.revision_number,
                    revision.status.value,
                    revision.created_at,
                    *self._scope_values(scope),
                    str(checkpoint_id),
                    str(expected_revision_id),
                ),
            )
            if cursor.rowcount != 1:
                raise RevisionConflict("expected revision is not current")
            self._insert_event(
                cursor,
                CheckpointLifecycleEvent.for_revision(
                    scope=scope,
                    kind=event_kind,
                    checkpoint_id=checkpoint_id,
                    revision_id=revision.revision_id,
                    revision_number=revision.revision_number,
                    occurred_at=revision.created_at,
                    evidence_references=revision.evidence_references,
                ),
            )
            return revision

    @contextmanager
    def _transaction(
        self, operation: TeamOperation, *, event_storage: bool = False
    ) -> Iterator[PostgreSQLCursor]:
        try:
            connection = self._connection_factory()
            connection.autocommit = False
        except Exception as error:
            if event_storage:
                raise EpisodicEventStorageFailure(
                    "episodic event database connection failed"
                ) from error
            raise RepositoryStorageFailure("checkpoint database connection failed") from error
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
        except (CheckpointRepositoryError, EpisodicEventRepositoryError):
            connection.rollback()
            raise
        except Exception as error:
            connection.rollback()
            state = _sqlstate(error)
            if event_storage:
                raise EpisodicEventStorageFailure(
                    "episodic event database operation failed"
                ) from error
            if state == "42501" or (state is not None and state.startswith("23")):
                raise RevisionConflict("checkpoint database rejected conflicting state") from error
            raise RepositoryStorageFailure("checkpoint database operation failed") from error
        finally:
            cursor.close()
            connection.close()

    def _select_aggregate(
        self,
        cursor: PostgreSQLCursor,
        scope: MemoryScope,
        checkpoint_id: CheckpointId,
        *,
        for_update: bool = False,
    ) -> CheckpointAggregate | None:
        cursor.execute(
            "SELECT " + _AGGREGATE_COLUMNS + " FROM mnemo_team.checkpoint_aggregates "
            "WHERE workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
            "AND checkpoint_id = CAST(%s AS uuid)" + (" FOR UPDATE" if for_update else ""),
            (*self._scope_values(scope), str(checkpoint_id)),
        )
        row = cursor.fetchone()
        return None if row is None else self._aggregate_from_row(row, scope)

    def _select_revision(
        self,
        cursor: PostgreSQLCursor,
        scope: MemoryScope,
        checkpoint_id: CheckpointId,
        revision_id: CheckpointRevisionId,
    ) -> CheckpointRevision | None:
        cursor.execute(
            "SELECT " + _REVISION_COLUMNS + " FROM mnemo_team.checkpoint_revisions AS revision "
            "WHERE revision.workspace_id = CAST(%s AS uuid) "
            "AND revision.project_id = CAST(%s AS uuid) "
            "AND revision.owner_id = CAST(%s AS uuid) AND revision.visibility = %s "
            "AND revision.session_id = CAST(%s AS uuid) "
            "AND revision.task_id = CAST(%s AS uuid) "
            "AND revision.checkpoint_id = CAST(%s AS uuid) "
            "AND revision.revision_id = CAST(%s AS uuid)",
            (*self._scope_values(scope), str(checkpoint_id), str(revision_id)),
        )
        row = cursor.fetchone()
        return None if row is None else self._revision_from_row(row, scope)

    def _select_event(
        self, cursor: PostgreSQLCursor, scope: MemoryScope, event_id: EventId
    ) -> CheckpointLifecycleEvent | None:
        return self._select_event_where(
            cursor, scope, "event.event_id = CAST(%s AS uuid)", str(event_id)
        )

    def _select_event_by_key(
        self, cursor: PostgreSQLCursor, scope: MemoryScope, idempotency_key: str
    ) -> CheckpointLifecycleEvent | None:
        return self._select_event_where(
            cursor, scope, "event.idempotency_key = %s", idempotency_key
        )

    def _select_event_where(
        self,
        cursor: PostgreSQLCursor,
        scope: MemoryScope,
        selector: str,
        selector_value: object,
    ) -> CheckpointLifecycleEvent | None:
        cursor.execute(
            "SELECT " + _EVENT_COLUMNS + " FROM mnemo_team.checkpoint_lifecycle_events AS event "
            "JOIN mnemo_team.checkpoint_revisions AS revision "
            "ON revision.workspace_id = event.workspace_id "
            "AND revision.revision_id = event.revision_id "
            "WHERE event.workspace_id = CAST(%s AS uuid) "
            "AND event.project_id = CAST(%s AS uuid) "
            "AND event.owner_id = CAST(%s AS uuid) AND event.visibility = %s "
            "AND event.session_id = CAST(%s AS uuid) AND event.task_id = CAST(%s AS uuid) AND "
            + selector,
            (*self._scope_values(scope), selector_value),
        )
        row = cursor.fetchone()
        return None if row is None else self._event_from_row(row, scope)

    @staticmethod
    def _insert_revision(cursor: PostgreSQLCursor, revision: CheckpointRevision) -> None:
        cursor.execute(
            "INSERT INTO mnemo_team.checkpoint_revisions("
            "workspace_id, project_id, owner_id, visibility, session_id, task_id, "
            "checkpoint_id, revision_id, revision_number, predecessor_revision_id, "
            "status, content_json, evidence_json, created_at) VALUES ("
            "CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, "
            "CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, "
            "CAST(%s AS uuid), %s, CAST(%s AS jsonb), CAST(%s AS jsonb), %s)",
            (
                *PostgreSQLCheckpointRepository._scope_values(revision.scope),
                str(revision.checkpoint_id),
                str(revision.revision_id),
                revision.revision_number,
                None
                if revision.predecessor_revision_id is None
                else str(revision.predecessor_revision_id),
                revision.status.value,
                json.dumps(revision.content.to_dict(), sort_keys=True, separators=(",", ":")),
                json.dumps(
                    [item.to_dict() for item in revision.evidence_references],
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                revision.created_at,
            ),
        )

    @staticmethod
    def _insert_event(cursor: PostgreSQLCursor, event: CheckpointLifecycleEvent) -> None:
        cursor.execute(
            "INSERT INTO mnemo_team.checkpoint_lifecycle_events("
            "workspace_id, project_id, owner_id, visibility, session_id, task_id, "
            "event_id, idempotency_key, event_kind, checkpoint_id, revision_id, "
            "revision_number, occurred_at) VALUES ("
            "CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, "
            "CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, %s, "
            "CAST(%s AS uuid), CAST(%s AS uuid), %s, %s)",
            (
                *PostgreSQLCheckpointRepository._scope_values(event.scope),
                str(event.event_id),
                event.idempotency_key,
                event.kind.value,
                str(event.checkpoint_id),
                str(event.revision_id),
                event.revision_number,
                event.occurred_at,
            ),
        )

    @staticmethod
    def _aggregate_from_row(row: Sequence[object], scope: MemoryScope) -> CheckpointAggregate:
        return CheckpointAggregate(
            CheckpointId.from_string(str(row[0])),
            scope,
            CheckpointRevisionId.from_string(str(row[1])),
            int(str(row[2])),
            CheckpointStatus(str(row[3])),
            cast(datetime, row[4]),
            cast(datetime, row[5]),
        )

    @staticmethod
    def _revision_from_row(row: Sequence[object], scope: MemoryScope) -> CheckpointRevision:
        content = json.loads(str(row[5]))
        evidence = json.loads(str(row[6]))
        if (
            not isinstance(content, Mapping)
            or not isinstance(evidence, list)
            or not all(isinstance(item, Mapping) for item in evidence)
        ):
            raise RepositoryStorageFailure("checkpoint stored payload is invalid")
        return CheckpointRevision(
            CheckpointRevisionId.from_string(str(row[0])),
            CheckpointId.from_string(str(row[1])),
            int(str(row[2])),
            None if row[3] is None else CheckpointRevisionId.from_string(str(row[3])),
            scope,
            CheckpointContent.from_dict(content),
            CheckpointStatus(str(row[4])),
            tuple(EvidenceReference.from_dict(item) for item in evidence),
            cast(datetime, row[7]),
        )

    @staticmethod
    def _event_from_row(row: Sequence[object], scope: MemoryScope) -> CheckpointLifecycleEvent:
        evidence = json.loads(str(row[7]))
        if not isinstance(evidence, list) or not all(
            isinstance(item, Mapping) for item in evidence
        ):
            raise EpisodicEventStorageFailure("episodic event stored payload is invalid")
        return CheckpointLifecycleEvent(
            EventId.from_string(str(row[0])),
            scope,
            CheckpointEventKind(str(row[1])),
            CheckpointId.from_string(str(row[2])),
            CheckpointRevisionId.from_string(str(row[3])),
            int(str(row[4])),
            cast(datetime, row[5]),
            str(row[6]),
            tuple(EvidenceReference.from_dict(item) for item in evidence),
        )

    @staticmethod
    def _is_identical_terminal_retry(
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

    def _require_scope(self, scope: MemoryScope) -> None:
        if (
            not isinstance(scope, MemoryScope)
            or scope.level is not ScopeLevel.TASK
            or scope.workspace_id != self._workspace_id
            or scope.project_id is None
            or scope.session_id is None
            or scope.task_id is None
        ):
            raise InvalidCheckpointScope("team checkpoints require an exact task scope")

    def _require_event_scope(self, scope: MemoryScope) -> None:
        try:
            self._require_scope(scope)
        except InvalidCheckpointScope as error:
            raise InvalidEpisodicEventScope(
                "team checkpoint events require an exact task scope"
            ) from error

    @staticmethod
    def _scope_values(scope: MemoryScope) -> tuple[str, str, str, str, str, str]:
        if (
            scope.workspace_id is None
            or scope.project_id is None
            or scope.session_id is None
            or scope.task_id is None
        ):
            raise InvalidCheckpointScope("team checkpoints require an exact task scope")
        return (
            str(scope.workspace_id),
            str(scope.project_id),
            str(scope.owner_id),
            scope.visibility.value,
            str(scope.session_id),
            str(scope.task_id),
        )
