"""PostgreSQL approved episodic-event and governance repository."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime
from typing import cast

from mnemo_memory.packages.domain import (
    ApprovedEpisodicEvent,
    ApprovedEpisodicEventGovernance,
    ApprovedEpisodicEventPinAction,
    ApprovedEventExportBundle,
    ApprovedEventGovernanceKind,
    ApprovedEventKind,
    ApprovedEventLifecycleStatus,
    EventId,
    EventOutboxJob,
    EventOutboxTopic,
    EvidenceReference,
    MemoryScope,
    OwnerId,
    ScopeLevel,
    WorkspaceId,
    approved_event_import_identity,
)
from mnemo_memory.packages.policy import ApprovedEpisodicEventSafetyPolicy, TeamOperation

from .contracts import (
    ApprovedEpisodicEventConflict,
    ApprovedEpisodicEventGovernanceResult,
    ApprovedEpisodicEventNotFound,
    ApprovedEpisodicEventPage,
    ApprovedEpisodicEventPinResult,
    ApprovedEpisodicEventRecord,
    ApprovedEpisodicEventRecordPage,
    ApprovedEpisodicEventRepositoryError,
    ApprovedEpisodicEventSecretRejected,
    ApprovedEpisodicEventStorageFailure,
    ApprovedEpisodicEventStoreResult,
    ApprovedEventExportRepositoryError,
    ApprovedEventImportConflict,
    ApprovedEventImportResult,
    InvalidApprovedEpisodicEventScope,
)
from .postgres import PostgreSQLConnectionFactory, PostgreSQLCursor
from .postgres_events import _insert_outbox_job, _task_scope_values

_EVENT_COLUMNS = (
    "event_sequence, event_id::text, source_event_key, event_kind, summary, occurred_at, "
    "evidence_json::text"
)
_GOVERNANCE_COLUMNS = (
    "target_event_sequence, action_id::text, source_action_key, action_kind, "
    "target_event_id::text, replacement_event_id::text, reason, occurred_at, "
    "evidence_json::text"
)
_PIN_COLUMNS = (
    "action_id::text, event_id::text, pinned, source_action_key, occurred_at, evidence_json::text"
)


def _sqlstate(error: Exception) -> str | None:
    for value in error.args:
        if isinstance(value, Mapping):
            state = value.get("C")
            if isinstance(state, str):
                return state
    return None


class PostgreSQLApprovedEpisodicEventRepository:
    """One principal/workspace-bound approved fact repository."""

    def __init__(
        self,
        connection_factory: PostgreSQLConnectionFactory,
        *,
        principal_id: OwnerId,
        workspace_id: WorkspaceId,
        policy: ApprovedEpisodicEventSafetyPolicy | None = None,
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
        self._policy = policy or ApprovedEpisodicEventSafetyPolicy()
        self._statement_timeout_ms = statement_timeout_ms

    def append_approved_event(
        self, event: ApprovedEpisodicEvent
    ) -> ApprovedEpisodicEventStoreResult:
        self._require_scope(event.scope)
        if not self._policy.assess_event(event).accepted:
            raise ApprovedEpisodicEventSecretRejected(
                "approved episodic event was rejected by deterministic secret policy"
            )
        with self._transaction(TeamOperation.CONTRIBUTE) as cursor:
            existing = self._event_by_source_key(cursor, event.scope, event.source_event_key)
            if existing is not None:
                stored = self._event_from_row(existing, event.scope)
                if stored == event:
                    return ApprovedEpisodicEventStoreResult(stored, True)
                raise ApprovedEpisodicEventConflict("approved episodic event key conflicts")
            cursor.execute(
                "SELECT 1 FROM mnemo_team.approved_episodic_event_governance "
                "WHERE workspace_id = CAST(%s AS uuid) AND target_event_id = CAST(%s AS uuid)",
                (str(self._workspace_id), str(event.event_id)),
            )
            if cursor.fetchone() is not None:
                raise ApprovedEpisodicEventConflict("retracted approved event cannot be restored")
            self._insert_event(cursor, event)
            return ApprovedEpisodicEventStoreResult(event, False)

    def get_approved_event(self, scope: MemoryScope, event_id: EventId) -> ApprovedEpisodicEvent:
        self._require_scope(scope)
        if not isinstance(event_id, EventId):
            raise TypeError("event_id must be an EventId")
        with self._transaction(TeamOperation.READ) as cursor:
            row = self._scoped_event_row(cursor, scope, event_id)
            if row is None:
                raise ApprovedEpisodicEventNotFound("approved episodic event was not found")
            return self._event_from_row(row, scope)

    def list_approved_events(
        self, scope: MemoryScope, *, offset: int = 0, limit: int = 50
    ) -> ApprovedEpisodicEventPage:
        self._require_scope(scope)
        self._validate_page(offset, limit)
        with self._transaction(TeamOperation.READ) as cursor:
            cursor.execute(
                "SELECT " + _EVENT_COLUMNS + " FROM mnemo_team.approved_episodic_events AS event "
                "WHERE event.workspace_id = CAST(%s AS uuid) "
                "AND event.project_id = CAST(%s AS uuid) "
                "AND event.owner_id = CAST(%s AS uuid) AND event.visibility = %s "
                "AND event.session_id = CAST(%s AS uuid) AND event.task_id = CAST(%s AS uuid) "
                "AND NOT EXISTS (SELECT 1 FROM "
                "mnemo_team.approved_episodic_event_governance AS action "
                "WHERE action.workspace_id = event.workspace_id "
                "AND action.target_event_id = event.event_id) "
                "ORDER BY COALESCE((SELECT pin.pinned FROM "
                "mnemo_team.approved_episodic_event_pin_actions AS pin "
                "WHERE pin.workspace_id = event.workspace_id AND pin.event_id = event.event_id "
                "ORDER BY pin.action_sequence DESC LIMIT 1), false) DESC, "
                "event.event_sequence DESC LIMIT %s OFFSET %s",
                (*_task_scope_values(scope), limit + 1, offset),
            )
            rows = tuple(cursor.fetchall())
            items = tuple(self._event_from_row(row, scope) for row in rows[:limit])
            return ApprovedEpisodicEventPage(items, offset + limit if len(rows) > limit else None)

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
        if (
            governance.kind is not ApprovedEventGovernanceKind.CORRECTED
            or replacement.event_id != governance.replacement_event_id
        ):
            raise ApprovedEpisodicEventConflict("approved event correction action is invalid")
        with self._transaction(TeamOperation.CONTRIBUTE) as cursor:
            existing = self._scoped_governance_row(
                cursor, governance.scope, governance.target_event_id
            )
            if existing is not None:
                stored = self._governance_from_row(existing, governance.scope)
                if not stored.same_intent(governance):
                    raise ApprovedEpisodicEventConflict(
                        "approved event already has a governance action"
                    )
                replacement_record = self._record(cursor, governance.scope, replacement.event_id)
                if replacement_record.event is not None and not self._same_event_intent(
                    replacement_record.event, replacement
                ):
                    raise ApprovedEpisodicEventConflict("approved event replacement conflicts")
                return ApprovedEpisodicEventGovernanceResult(
                    self._record(cursor, governance.scope, governance.target_event_id),
                    replacement_record,
                    True,
                )
            target_row = self._scoped_event_row(
                cursor, governance.scope, governance.target_event_id
            )
            if target_row is None:
                raise ApprovedEpisodicEventNotFound("approved episodic event was not found")
            target = self._event_from_row(target_row, governance.scope)
            if replacement.kind is not target.kind:
                raise ApprovedEpisodicEventConflict("approved event correction cannot change kind")
            self._require_available_action_key(cursor, governance)
            self._require_available_replacement(cursor, replacement)
            current_pin = self._current_pin(cursor, governance.scope, governance.target_event_id)
            self._insert_event(cursor, replacement)
            self._insert_governance(cursor, governance, int(str(target_row[0])))
            if current_pin is not None and current_pin.pinned:
                self._insert_pin_transfer(cursor, governance, replacement.event_id, current_pin)
            return ApprovedEpisodicEventGovernanceResult(
                self._record(cursor, governance.scope, governance.target_event_id),
                self._record(cursor, governance.scope, replacement.event_id),
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
        with self._transaction(TeamOperation.CONTRIBUTE) as cursor:
            existing = self._scoped_governance_row(
                cursor, governance.scope, governance.target_event_id
            )
            if existing is not None:
                stored = self._governance_from_row(existing, governance.scope)
                if not stored.same_intent(governance):
                    raise ApprovedEpisodicEventConflict(
                        "approved event already has a governance action"
                    )
                return ApprovedEpisodicEventGovernanceResult(
                    self._record(cursor, governance.scope, governance.target_event_id),
                    None,
                    True,
                )
            target_row = self._scoped_event_row(
                cursor, governance.scope, governance.target_event_id
            )
            if target_row is None:
                raise ApprovedEpisodicEventNotFound("approved episodic event was not found")
            self._require_available_action_key(cursor, governance)
            current_pin = self._current_pin(cursor, governance.scope, governance.target_event_id)
            self._insert_governance(cursor, governance, int(str(target_row[0])))
            if current_pin is not None and current_pin.pinned:
                self._insert_pin(
                    cursor,
                    ApprovedEpisodicEventPinAction.create(
                        scope=governance.scope,
                        event_id=governance.target_event_id,
                        pinned=False,
                        source_action_key=f"governance-pin-retracted:{governance.action_id}",
                        occurred_at=governance.occurred_at,
                        evidence_references=current_pin.evidence_references,
                    ),
                )
            cursor.execute(
                "DELETE FROM mnemo_team.approved_episodic_events WHERE "
                "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
                "AND event_id = CAST(%s AS uuid)",
                (*_task_scope_values(governance.scope), str(governance.target_event_id)),
            )
            if cursor.rowcount != 1:
                raise ApprovedEpisodicEventConflict("approved event retraction lost its target")
            return ApprovedEpisodicEventGovernanceResult(
                self._record(cursor, governance.scope, governance.target_event_id), None, False
            )

    def get_approved_event_record(
        self, scope: MemoryScope, event_id: EventId
    ) -> ApprovedEpisodicEventRecord:
        self._require_scope(scope)
        if not isinstance(event_id, EventId):
            raise TypeError("event_id must be an EventId")
        with self._transaction(TeamOperation.READ) as cursor:
            return self._record(cursor, scope, event_id)

    def list_approved_event_records(
        self, scope: MemoryScope, *, offset: int = 0, limit: int = 50
    ) -> ApprovedEpisodicEventRecordPage:
        self._require_scope(scope)
        self._validate_page(offset, limit)
        values = _task_scope_values(scope)
        with self._transaction(TeamOperation.READ) as cursor:
            cursor.execute(
                "SELECT event_sequence AS record_sequence, event_id::text FROM "
                "mnemo_team.approved_episodic_events WHERE "
                "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
                "UNION ALL SELECT target_event_sequence AS record_sequence, "
                "target_event_id::text FROM mnemo_team.approved_episodic_event_governance "
                "WHERE action_kind = 'retracted' AND workspace_id = CAST(%s AS uuid) "
                "AND project_id = CAST(%s AS uuid) AND owner_id = CAST(%s AS uuid) "
                "AND visibility = %s AND session_id = CAST(%s AS uuid) "
                "AND task_id = CAST(%s AS uuid) ORDER BY record_sequence DESC "
                "LIMIT %s OFFSET %s",
                (*values, *values, limit + 1, offset),
            )
            rows = tuple(cursor.fetchall())
            items = tuple(
                self._record(cursor, scope, EventId.from_string(str(row[1])))
                for row in rows[:limit]
            )
            return ApprovedEpisodicEventRecordPage(
                items, offset + limit if len(rows) > limit else None
            )

    def set_approved_event_pin(
        self, action: ApprovedEpisodicEventPinAction
    ) -> ApprovedEpisodicEventPinResult:
        self._require_scope(action.scope)
        if not self._policy.assess_pin(action).accepted:
            raise ApprovedEpisodicEventSecretRejected(
                "approved episodic event pin was rejected by secret policy"
            )
        with self._transaction(TeamOperation.CONTRIBUTE) as cursor:
            cursor.execute(
                "SELECT " + _PIN_COLUMNS + " FROM "
                "mnemo_team.approved_episodic_event_pin_actions WHERE "
                "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
                "AND action_id = CAST(%s AS uuid)",
                (*_task_scope_values(action.scope), str(action.action_id)),
            )
            existing = cursor.fetchone()
            if existing is not None:
                stored = self._pin_from_row(existing, action.scope)
                if not stored.same_intent(action):
                    raise ApprovedEpisodicEventConflict("approved event pin action conflicts")
                return ApprovedEpisodicEventPinResult(
                    stored, self._record(cursor, action.scope, action.event_id), True
                )
            cursor.execute(
                "SELECT 1 FROM mnemo_team.approved_episodic_event_pin_actions WHERE "
                "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
                "AND source_action_key = %s",
                (*_task_scope_values(action.scope), action.source_action_key),
            )
            if cursor.fetchone() is not None:
                raise ApprovedEpisodicEventConflict("approved event pin action key conflicts")
            if (
                self._scoped_event_row(cursor, action.scope, action.event_id) is None
                or self._scoped_governance_row(cursor, action.scope, action.event_id) is not None
            ):
                raise ApprovedEpisodicEventNotFound("approved episodic event was not found")
            self._insert_pin(cursor, action)
            return ApprovedEpisodicEventPinResult(
                action, self._record(cursor, action.scope, action.event_id), False
            )

    def export_approved_event_history(
        self, scope: MemoryScope, *, exported_at: datetime
    ) -> ApprovedEventExportBundle:
        self._require_scope(scope)
        try:
            with self._transaction(TeamOperation.READ) as cursor:
                return self._export_with_cursor(cursor, scope, exported_at)
        except ApprovedEpisodicEventRepositoryError as error:
            raise ApprovedEventExportRepositoryError(
                "approved event export storage operation failed"
            ) from error

    def import_approved_event_history(
        self,
        source: ApprovedEventExportBundle,
        target: ApprovedEventExportBundle,
    ) -> ApprovedEventImportResult:
        if not isinstance(source, ApprovedEventExportBundle) or not isinstance(
            target, ApprovedEventExportBundle
        ):
            raise TypeError("approved event import requires validated bundles")
        self._require_scope(target.scope)
        self._validate_import_rebase(source, target)
        with self._transaction(TeamOperation.CONTRIBUTE) as cursor:
            before = self._export_with_cursor(cursor, target.scope, target.exported_at)
            if self._same_export(before, target):
                return ApprovedEventImportResult(
                    len(target.events),
                    len(target.governance_actions),
                    len(target.pin_history),
                    True,
                )
            if before.events or before.governance_actions or before.pin_history:
                raise ApprovedEventImportConflict(
                    "approved event import target contains conflicting state"
                )
            source_events = {item.source_event_key: item for item in source.events}
            for event in target.events:
                self._insert_imported_event(
                    cursor, event, source_events[event.source_event_key], source.content_digest
                )
            source_governance = {item.source_action_key: item for item in source.governance_actions}
            ordered_governance = sorted(
                target.governance_actions,
                key=lambda item: item.kind is not ApprovedEventGovernanceKind.RETRACTED,
            )
            target_event_ids = {item.event_id for item in target.events}
            for action in ordered_governance:
                source_action = source_governance[action.source_action_key]
                self._insert_imported_governance(
                    cursor,
                    action,
                    source_action,
                    source.content_digest,
                    missing_target=action.target_event_id not in target_event_ids,
                )
            source_pins = {
                item.action.source_action_key: item.action for item in source.pin_history
            }
            for entry in target.pin_history:
                self._insert_imported_pin(
                    cursor,
                    entry.action,
                    source_pins[entry.action.source_action_key],
                    source.content_digest,
                    missing_target=entry.action.event_id not in target_event_ids,
                )
            after = self._export_with_cursor(cursor, target.scope, target.exported_at)
            if not self._same_export(after, target):
                raise ApprovedEventImportConflict("approved event import verification failed")
            return ApprovedEventImportResult(
                len(target.events),
                len(target.governance_actions),
                len(target.pin_history),
                False,
            )

    def _export_with_cursor(
        self, cursor: PostgreSQLCursor, scope: MemoryScope, exported_at: datetime
    ) -> ApprovedEventExportBundle:
        values = _task_scope_values(scope)
        cursor.execute(
            "SELECT " + _EVENT_COLUMNS + " FROM mnemo_team.approved_episodic_events WHERE "
            "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
            "ORDER BY event_id ASC",
            values,
        )
        events = tuple(self._event_from_row(row, scope) for row in cursor.fetchall())
        cursor.execute(
            "SELECT " + _GOVERNANCE_COLUMNS + " FROM "
            "mnemo_team.approved_episodic_event_governance WHERE "
            "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
            "ORDER BY target_event_id ASC",
            values,
        )
        governance = tuple(self._governance_from_row(row, scope) for row in cursor.fetchall())
        cursor.execute(
            "SELECT " + _PIN_COLUMNS + " FROM "
            "mnemo_team.approved_episodic_event_pin_actions WHERE "
            "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
            "ORDER BY action_sequence ASC",
            values,
        )
        pins = tuple(self._pin_from_row(row, scope) for row in cursor.fetchall())
        return ApprovedEventExportBundle.create(
            scope=scope,
            exported_at=exported_at,
            events=events,
            governance_actions=governance,
            pin_actions=pins,
        )

    @staticmethod
    def _validate_import_rebase(
        source: ApprovedEventExportBundle, target: ApprovedEventExportBundle
    ) -> None:
        if source.exported_at != target.exported_at:
            raise ApprovedEventImportConflict("approved event import timestamps differ")
        mapped: dict[EventId, EventId] = {}
        expected_events: list[ApprovedEpisodicEvent] = []
        for event in source.events:
            expected_event = ApprovedEpisodicEvent.create(
                scope=target.scope,
                kind=event.kind,
                summary=event.summary,
                source_event_key=event.source_event_key,
                occurred_at=event.occurred_at,
                evidence_references=event.evidence_references,
            )
            mapped[event.event_id] = expected_event.event_id
            expected_events.append(expected_event)
        for action in source.governance_actions:
            if action.target_event_id not in mapped:
                mapped[action.target_event_id] = approved_event_import_identity(
                    target.scope, action.target_event_id
                )
        expected_governance = tuple(
            ApprovedEpisodicEventGovernance.create(
                scope=target.scope,
                kind=action.kind,
                target_event_id=mapped[action.target_event_id],
                replacement_event_id=(
                    None
                    if action.replacement_event_id is None
                    else mapped[action.replacement_event_id]
                ),
                reason=action.reason,
                source_action_key=action.source_action_key,
                occurred_at=action.occurred_at,
                evidence_references=action.evidence_references,
            )
            for action in source.governance_actions
        )
        expected_pins = tuple(
            ApprovedEpisodicEventPinAction.create(
                scope=target.scope,
                event_id=mapped[entry.action.event_id],
                pinned=entry.action.pinned,
                source_action_key=entry.action.source_action_key,
                occurred_at=entry.action.occurred_at,
                evidence_references=entry.action.evidence_references,
            )
            for entry in source.pin_history
        )
        expected_bundle = ApprovedEventExportBundle.create(
            scope=target.scope,
            exported_at=source.exported_at,
            events=tuple(expected_events),
            governance_actions=expected_governance,
            pin_actions=expected_pins,
        )
        if not PostgreSQLApprovedEpisodicEventRepository._same_export(expected_bundle, target):
            raise ApprovedEventImportConflict("approved event import source and target differ")

    @staticmethod
    def _same_export(left: ApprovedEventExportBundle, right: ApprovedEventExportBundle) -> bool:
        return (
            left.format_version == right.format_version
            and left.scope == right.scope
            and left.exported_at == right.exported_at
            and left.events == right.events
            and left.governance_actions == right.governance_actions
            and left.pin_history == right.pin_history
        )

    def _insert_imported_event(
        self,
        cursor: PostgreSQLCursor,
        event: ApprovedEpisodicEvent,
        source: ApprovedEpisodicEvent,
        source_digest: str,
    ) -> None:
        cursor.execute(
            "INSERT INTO mnemo_team.approved_episodic_events("
            "workspace_id, project_id, owner_id, visibility, session_id, task_id, event_id, "
            "source_event_key, event_kind, summary, occurred_at, evidence_json, "
            "import_source_event_id, import_source_content_digest, imported_at) VALUES ("
            "CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, CAST(%s AS uuid), "
            "CAST(%s AS uuid), CAST(%s AS uuid), %s, %s, %s, %s, CAST(%s AS jsonb), "
            "CAST(%s AS uuid), %s, CURRENT_TIMESTAMP)",
            (
                *_task_scope_values(event.scope),
                str(event.event_id),
                event.source_event_key,
                event.kind.value,
                event.summary,
                event.occurred_at,
                self._evidence_json(event.evidence_references),
                str(source.event_id),
                source_digest,
            ),
        )
        _insert_outbox_job(
            cursor,
            EventOutboxJob.create(
                scope=event.scope,
                topic=EventOutboxTopic.APPROVED_EPISODIC,
                source_event_id=event.event_id,
                event_kind=event.kind.value,
                occurred_at=event.occurred_at,
                created_at=event.occurred_at,
            ),
        )

    def _insert_imported_governance(
        self,
        cursor: PostgreSQLCursor,
        action: ApprovedEpisodicEventGovernance,
        source: ApprovedEpisodicEventGovernance,
        source_digest: str,
        *,
        missing_target: bool,
    ) -> None:
        target_sequence = 0
        if not missing_target:
            target_row = self._scoped_event_row(cursor, action.scope, action.target_event_id)
            if target_row is None:
                raise ApprovedEventImportConflict("approved event import target is missing")
            target_sequence = int(str(target_row[0]))
        cursor.execute(
            "INSERT INTO mnemo_team.approved_episodic_event_governance("
            "workspace_id, project_id, owner_id, visibility, session_id, task_id, action_id, "
            "source_action_key, action_kind, target_event_id, target_event_sequence, "
            "replacement_event_id, reason, occurred_at, evidence_json, import_source_action_id, "
            "import_source_target_event_id, import_source_replacement_event_id, "
            "import_source_content_digest, imported_at, imported_without_target_payload) "
            "VALUES (CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, "
            "CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, %s, "
            "CAST(%s AS uuid), %s, CAST(%s AS uuid), %s, %s, CAST(%s AS jsonb), "
            "CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, CURRENT_TIMESTAMP, %s)",
            (
                *_task_scope_values(action.scope),
                str(action.action_id),
                action.source_action_key,
                action.kind.value,
                str(action.target_event_id),
                target_sequence,
                None if action.replacement_event_id is None else str(action.replacement_event_id),
                action.reason,
                action.occurred_at,
                self._evidence_json(action.evidence_references),
                str(source.action_id),
                str(source.target_event_id),
                None if source.replacement_event_id is None else str(source.replacement_event_id),
                source_digest,
                missing_target,
            ),
        )
        _insert_outbox_job(
            cursor,
            EventOutboxJob.create(
                scope=action.scope,
                topic=EventOutboxTopic.APPROVED_GOVERNANCE,
                source_event_id=action.action_id,
                event_kind=action.kind.value,
                occurred_at=action.occurred_at,
                created_at=action.occurred_at,
            ),
        )

    def _insert_imported_pin(
        self,
        cursor: PostgreSQLCursor,
        action: ApprovedEpisodicEventPinAction,
        source: ApprovedEpisodicEventPinAction,
        source_digest: str,
        *,
        missing_target: bool,
    ) -> None:
        cursor.execute(
            "INSERT INTO mnemo_team.approved_episodic_event_pin_actions("
            "workspace_id, project_id, owner_id, visibility, session_id, task_id, action_id, "
            "event_id, pinned, source_action_key, occurred_at, evidence_json, "
            "import_source_action_id, import_source_event_id, import_source_content_digest, "
            "imported_at, imported_without_event_payload) VALUES ("
            "CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, CAST(%s AS uuid), "
            "CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, %s, %s, "
            "CAST(%s AS jsonb), CAST(%s AS uuid), CAST(%s AS uuid), %s, CURRENT_TIMESTAMP, %s)",
            (
                *_task_scope_values(action.scope),
                str(action.action_id),
                str(action.event_id),
                action.pinned,
                action.source_action_key,
                action.occurred_at,
                self._evidence_json(action.evidence_references),
                str(source.action_id),
                str(source.event_id),
                source_digest,
                missing_target,
            ),
        )
        _insert_outbox_job(
            cursor,
            EventOutboxJob.create(
                scope=action.scope,
                topic=EventOutboxTopic.APPROVED_GOVERNANCE,
                source_event_id=action.action_id,
                event_kind="pinned" if action.pinned else "unpinned",
                occurred_at=action.occurred_at,
                created_at=action.occurred_at,
            ),
        )

    def _insert_event(self, cursor: PostgreSQLCursor, event: ApprovedEpisodicEvent) -> None:
        cursor.execute(
            "INSERT INTO mnemo_team.approved_episodic_events("
            "workspace_id, project_id, owner_id, visibility, session_id, task_id, event_id, "
            "source_event_key, event_kind, summary, occurred_at, evidence_json) VALUES ("
            "CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, CAST(%s AS uuid), "
            "CAST(%s AS uuid), CAST(%s AS uuid), %s, %s, %s, %s, CAST(%s AS jsonb))",
            (
                *_task_scope_values(event.scope),
                str(event.event_id),
                event.source_event_key,
                event.kind.value,
                event.summary,
                event.occurred_at,
                self._evidence_json(event.evidence_references),
            ),
        )
        _insert_outbox_job(
            cursor,
            EventOutboxJob.create(
                scope=event.scope,
                topic=EventOutboxTopic.APPROVED_EPISODIC,
                source_event_id=event.event_id,
                event_kind=event.kind.value,
                occurred_at=event.occurred_at,
                created_at=event.occurred_at,
            ),
        )

    def _insert_governance(
        self,
        cursor: PostgreSQLCursor,
        governance: ApprovedEpisodicEventGovernance,
        target_sequence: int,
    ) -> None:
        cursor.execute(
            "INSERT INTO mnemo_team.approved_episodic_event_governance("
            "workspace_id, project_id, owner_id, visibility, session_id, task_id, action_id, "
            "source_action_key, action_kind, target_event_id, target_event_sequence, "
            "replacement_event_id, reason, occurred_at, evidence_json) VALUES ("
            "CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, CAST(%s AS uuid), "
            "CAST(%s AS uuid), CAST(%s AS uuid), %s, %s, CAST(%s AS uuid), %s, "
            "CAST(%s AS uuid), %s, %s, CAST(%s AS jsonb))",
            (
                *_task_scope_values(governance.scope),
                str(governance.action_id),
                governance.source_action_key,
                governance.kind.value,
                str(governance.target_event_id),
                target_sequence,
                None
                if governance.replacement_event_id is None
                else str(governance.replacement_event_id),
                governance.reason,
                governance.occurred_at,
                self._evidence_json(governance.evidence_references),
            ),
        )
        _insert_outbox_job(
            cursor,
            EventOutboxJob.create(
                scope=governance.scope,
                topic=EventOutboxTopic.APPROVED_GOVERNANCE,
                source_event_id=governance.action_id,
                event_kind=governance.kind.value,
                occurred_at=governance.occurred_at,
                created_at=governance.occurred_at,
            ),
        )

    def _insert_pin(self, cursor: PostgreSQLCursor, action: ApprovedEpisodicEventPinAction) -> None:
        cursor.execute(
            "INSERT INTO mnemo_team.approved_episodic_event_pin_actions("
            "workspace_id, project_id, owner_id, visibility, session_id, task_id, action_id, "
            "event_id, pinned, source_action_key, occurred_at, evidence_json) VALUES ("
            "CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, CAST(%s AS uuid), "
            "CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, %s, %s, "
            "CAST(%s AS jsonb))",
            (
                *_task_scope_values(action.scope),
                str(action.action_id),
                str(action.event_id),
                action.pinned,
                action.source_action_key,
                action.occurred_at,
                self._evidence_json(action.evidence_references),
            ),
        )
        _insert_outbox_job(
            cursor,
            EventOutboxJob.create(
                scope=action.scope,
                topic=EventOutboxTopic.APPROVED_GOVERNANCE,
                source_event_id=action.action_id,
                event_kind="pinned" if action.pinned else "unpinned",
                occurred_at=action.occurred_at,
                created_at=action.occurred_at,
            ),
        )

    def _insert_pin_transfer(
        self,
        cursor: PostgreSQLCursor,
        governance: ApprovedEpisodicEventGovernance,
        replacement_event_id: EventId,
        current_pin: ApprovedEpisodicEventPinAction,
    ) -> None:
        for event_id, pinned, suffix in (
            (governance.target_event_id, False, "released"),
            (replacement_event_id, True, "transferred"),
        ):
            self._insert_pin(
                cursor,
                ApprovedEpisodicEventPinAction.create(
                    scope=governance.scope,
                    event_id=event_id,
                    pinned=pinned,
                    source_action_key=f"governance-pin-{suffix}:{governance.action_id}",
                    occurred_at=governance.occurred_at,
                    evidence_references=current_pin.evidence_references,
                ),
            )

    def _record(
        self, cursor: PostgreSQLCursor, scope: MemoryScope, event_id: EventId
    ) -> ApprovedEpisodicEventRecord:
        event_row = self._scoped_event_row(cursor, scope, event_id)
        governance_row = self._scoped_governance_row(cursor, scope, event_id)
        if event_row is not None:
            event = self._event_from_row(event_row, scope)
            governance = (
                None if governance_row is None else self._governance_from_row(governance_row, scope)
            )
            return ApprovedEpisodicEventRecord(
                event_id,
                scope,
                ApprovedEventLifecycleStatus.ACTIVE
                if governance is None
                else ApprovedEventLifecycleStatus.CORRECTED,
                event,
                governance,
                governance is None and self._is_pinned(cursor, scope, event_id),
            )
        if governance_row is not None and str(governance_row[3]) == "retracted":
            return ApprovedEpisodicEventRecord(
                event_id,
                scope,
                ApprovedEventLifecycleStatus.RETRACTED,
                None,
                self._governance_from_row(governance_row, scope),
            )
        raise ApprovedEpisodicEventNotFound("approved episodic event was not found")

    def _event_by_source_key(
        self, cursor: PostgreSQLCursor, scope: MemoryScope, source_event_key: str
    ) -> Sequence[object] | None:
        cursor.execute(
            "SELECT " + _EVENT_COLUMNS + " FROM mnemo_team.approved_episodic_events WHERE "
            "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
            "AND source_event_key = %s",
            (*_task_scope_values(scope), source_event_key),
        )
        return cursor.fetchone()

    def _scoped_event_row(
        self,
        cursor: PostgreSQLCursor,
        scope: MemoryScope,
        event_id: EventId,
    ) -> Sequence[object] | None:
        cursor.execute(
            "SELECT " + _EVENT_COLUMNS + " FROM mnemo_team.approved_episodic_events WHERE "
            "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
            "AND event_id = CAST(%s AS uuid)",
            (*_task_scope_values(scope), str(event_id)),
        )
        return cursor.fetchone()

    def _scoped_governance_row(
        self, cursor: PostgreSQLCursor, scope: MemoryScope, event_id: EventId
    ) -> Sequence[object] | None:
        cursor.execute(
            "SELECT " + _GOVERNANCE_COLUMNS + " FROM "
            "mnemo_team.approved_episodic_event_governance WHERE "
            "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
            "AND target_event_id = CAST(%s AS uuid)",
            (*_task_scope_values(scope), str(event_id)),
        )
        return cursor.fetchone()

    def _current_pin(
        self, cursor: PostgreSQLCursor, scope: MemoryScope, event_id: EventId
    ) -> ApprovedEpisodicEventPinAction | None:
        cursor.execute(
            "SELECT " + _PIN_COLUMNS + " FROM "
            "mnemo_team.approved_episodic_event_pin_actions WHERE "
            "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
            "AND event_id = CAST(%s AS uuid) ORDER BY action_sequence DESC LIMIT 1",
            (*_task_scope_values(scope), str(event_id)),
        )
        row = cursor.fetchone()
        return None if row is None else self._pin_from_row(row, scope)

    def _is_pinned(self, cursor: PostgreSQLCursor, scope: MemoryScope, event_id: EventId) -> bool:
        current = self._current_pin(cursor, scope, event_id)
        return current is not None and current.pinned

    def _require_available_action_key(
        self, cursor: PostgreSQLCursor, governance: ApprovedEpisodicEventGovernance
    ) -> None:
        cursor.execute(
            "SELECT 1 FROM mnemo_team.approved_episodic_event_governance WHERE "
            "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND session_id = CAST(%s AS uuid) AND task_id = CAST(%s AS uuid) "
            "AND source_action_key = %s",
            (*_task_scope_values(governance.scope), governance.source_action_key),
        )
        if cursor.fetchone() is not None:
            raise ApprovedEpisodicEventConflict("approved event action key conflicts")

    def _require_available_replacement(
        self, cursor: PostgreSQLCursor, replacement: ApprovedEpisodicEvent
    ) -> None:
        cursor.execute(
            "SELECT 1 FROM mnemo_team.approved_episodic_events WHERE "
            "workspace_id = CAST(%s AS uuid) AND ((event_id = CAST(%s AS uuid)) OR "
            "(project_id = CAST(%s AS uuid) AND owner_id = CAST(%s AS uuid) "
            "AND visibility = %s AND session_id = CAST(%s AS uuid) "
            "AND task_id = CAST(%s AS uuid) AND source_event_key = %s))",
            (
                str(self._workspace_id),
                str(replacement.event_id),
                str(replacement.scope.project_id),
                str(replacement.scope.owner_id),
                replacement.scope.visibility.value,
                str(replacement.scope.session_id),
                str(replacement.scope.task_id),
                replacement.source_event_key,
            ),
        )
        event_conflict = cursor.fetchone() is not None
        cursor.execute(
            "SELECT 1 FROM mnemo_team.approved_episodic_event_governance "
            "WHERE workspace_id = CAST(%s AS uuid) AND target_event_id = CAST(%s AS uuid)",
            (str(self._workspace_id), str(replacement.event_id)),
        )
        if event_conflict or cursor.fetchone() is not None:
            raise ApprovedEpisodicEventConflict("approved event replacement conflicts")

    @staticmethod
    def _event_from_row(row: Sequence[object], scope: MemoryScope) -> ApprovedEpisodicEvent:
        evidence = PostgreSQLApprovedEpisodicEventRepository._evidence_from_json(row[6])
        return ApprovedEpisodicEvent(
            EventId.from_string(str(row[1])),
            scope,
            ApprovedEventKind(str(row[3])),
            str(row[4]),
            str(row[2]),
            cast(datetime, row[5]),
            evidence,
        )

    @staticmethod
    def _governance_from_row(
        row: Sequence[object], scope: MemoryScope
    ) -> ApprovedEpisodicEventGovernance:
        return ApprovedEpisodicEventGovernance(
            EventId.from_string(str(row[1])),
            scope,
            ApprovedEventGovernanceKind(str(row[3])),
            EventId.from_string(str(row[4])),
            None if row[5] is None else EventId.from_string(str(row[5])),
            str(row[6]),
            str(row[2]),
            cast(datetime, row[7]),
            PostgreSQLApprovedEpisodicEventRepository._evidence_from_json(row[8]),
        )

    @staticmethod
    def _pin_from_row(row: Sequence[object], scope: MemoryScope) -> ApprovedEpisodicEventPinAction:
        return ApprovedEpisodicEventPinAction(
            EventId.from_string(str(row[0])),
            scope,
            EventId.from_string(str(row[1])),
            bool(row[2]),
            str(row[3]),
            cast(datetime, row[4]),
            PostgreSQLApprovedEpisodicEventRepository._evidence_from_json(row[5]),
        )

    @staticmethod
    def _evidence_json(evidence: tuple[EvidenceReference, ...]) -> str:
        return json.dumps(
            [item.to_dict() for item in evidence], sort_keys=True, separators=(",", ":")
        )

    @staticmethod
    def _evidence_from_json(value: object) -> tuple[EvidenceReference, ...]:
        payload = json.loads(str(value))
        if not isinstance(payload, list) or not all(isinstance(item, Mapping) for item in payload):
            raise ApprovedEpisodicEventStorageFailure("approved event evidence is invalid")
        return tuple(EvidenceReference.from_dict(item) for item in payload)

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

    def _validate_governance(
        self, scope: MemoryScope, governance: ApprovedEpisodicEventGovernance
    ) -> None:
        self._require_scope(scope)
        if governance.scope != scope:
            raise InvalidApprovedEpisodicEventScope(
                "approved event governance requires one complete task scope"
            )

    @staticmethod
    def _validate_page(offset: int, limit: int) -> None:
        if offset < 0 or limit < 1:
            raise ValueError("event offset must be non-negative and limit must be positive")

    def _require_scope(self, scope: MemoryScope) -> None:
        if (
            not isinstance(scope, MemoryScope)
            or scope.level is not ScopeLevel.TASK
            or scope.workspace_id != self._workspace_id
            or scope.project_id is None
            or scope.session_id is None
            or scope.task_id is None
        ):
            raise InvalidApprovedEpisodicEventScope(
                "team approved episodic events require exact task scope"
            )

    @contextmanager
    def _transaction(self, operation: TeamOperation) -> Iterator[PostgreSQLCursor]:
        try:
            connection = self._connection_factory()
            connection.autocommit = False
        except Exception as error:
            raise ApprovedEpisodicEventStorageFailure(
                "approved event database connection failed"
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
        except ApprovedEpisodicEventRepositoryError:
            connection.rollback()
            raise
        except Exception as error:
            connection.rollback()
            state = _sqlstate(error)
            if state == "42501" or (state is not None and state.startswith("23")):
                raise ApprovedEpisodicEventConflict(
                    "approved event database rejected conflicting state"
                ) from error
            raise ApprovedEpisodicEventStorageFailure(
                "approved event database operation failed"
            ) from error
        finally:
            cursor.close()
            connection.close()
