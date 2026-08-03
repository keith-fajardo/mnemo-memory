"""SQLite adapter for Mnemo's local, single-user personal profile."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import replace
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import cast

from mnemo_memory.packages.domain import (
    ApprovedEpisodicEvent,
    ApprovedEventKind,
    Checkpoint,
    CheckpointAggregate,
    CheckpointContent,
    CheckpointEventKind,
    CheckpointId,
    CheckpointLifecycleEvent,
    CheckpointRevision,
    CheckpointRevisionId,
    CheckpointStatus,
    CodeEdge,
    CodeEdgeKind,
    CodeSnapshot,
    CodeSnapshotId,
    CodeStructureArtifact,
    CodeSymbol,
    CodeSymbolId,
    CodeSymbolKind,
    DbtSnapshotId,
    EventId,
    EvidenceReference,
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    SessionId,
    TaskId,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.domain.dbt_manifest import (
    ArtifactCurrentness,
    DbtArtifactMetadata,
    DbtLineageEdge,
    DbtManifestArtifact,
    DbtManifestNode,
    DbtManifestSnapshot,
    DbtNodeId,
    DbtResourceType,
    LineageEdgeType,
    SourceStateFingerprint,
)

from .contracts import (
    ActiveSnapshotConflict,
    ApprovedEpisodicEventConflict,
    ApprovedEpisodicEventNotFound,
    ApprovedEpisodicEventPage,
    ApprovedEpisodicEventStorageFailure,
    ApprovedEpisodicEventStoreResult,
    CheckpointNotFound,
    CheckpointPage,
    DuplicateCheckpoint,
    EpisodicEventNotFound,
    EpisodicEventPage,
    EpisodicEventStorageFailure,
    EpisodicEventStoreResult,
    InvalidAbandonmentReason,
    InvalidApprovedEpisodicEventScope,
    InvalidCheckpointScope,
    InvalidEpisodicEventScope,
    InvalidLifecycleTransition,
    InvalidManifestSnapshotScope,
    ManifestNodeNotFound,
    ManifestSnapshotNotFound,
    ManifestSnapshotPage,
    ManifestSnapshotStoreResult,
    ProjectIndexStorageFailure,
    RepositoryStorageFailure,
    RevisionConflict,
    SourceIndexStorageFailure,
    SourceSnapshotNotFound,
    SourceSnapshotStoreResult,
)

LATEST_SCHEMA_VERSION = 7
BUSY_TIMEOUT_MS = 5000


class SQLiteMigrationError(RuntimeError):
    pass


class SQLiteSchemaTooNewError(SQLiteMigrationError):
    pass


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _migration_text(name: str) -> str:
    """Read package-owned migration data from an installed wheel or source tree."""
    return (
        resources.files("mnemo_memory")
        .joinpath("resources", "migrations", name)
        .read_text(encoding="utf-8")
    )


def resolve_database_path(path: Path, base_directory: Path | None = None) -> Path:
    """Resolve a database path within its declared local base directory."""
    base = (base_directory or path.parent).resolve()
    candidate = path.resolve()
    if candidate != base and base not in candidate.parents:
        raise ValueError("database path escapes its declared base directory")
    if ".." in path.parts:
        raise ValueError("database path must not contain traversal segments")
    return candidate


class SQLiteCheckpointRepository:
    def __init__(self, path: Path, *, base_directory: Path | None = None) -> None:
        self.path = resolve_database_path(path, base_directory)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.path, isolation_level=None, timeout=BUSY_TIMEOUT_MS / 1000
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA journal_mode = WAL")
        if self.path.exists():
            with suppress(OSError):
                os.chmod(self.path, 0o600)
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()

    def migrate(self, *, fail_after_version: int | None = None) -> None:
        with self._transaction() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"  # noqa: E501
            )
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
            ).fetchone()
            version = int(row["version"])
            if version > LATEST_SCHEMA_VERSION:
                raise SQLiteSchemaTooNewError("database schema is newer than this application")
            if version < 1:
                _execute_sql_script(connection, _migration_text("0001_initial.sql"))
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (1, ?)",
                    (_timestamp(),),
                )
                if fail_after_version == 1:
                    raise SQLiteMigrationError("injected migration failure")
                version = 1
            if version < 2:
                _execute_sql_script(
                    connection, _migration_text("0002_checkpoint_aggregate_revisions.sql")
                )
                self._map_legacy_checkpoints(connection)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (2, ?)",
                    (_timestamp(),),
                )
                if fail_after_version == 2:
                    raise SQLiteMigrationError("injected migration failure")
                version = 2
            if version < 3:
                _execute_sql_script(connection, _migration_text("0003_dbt_manifest_snapshots.sql"))
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (3, ?)",
                    (_timestamp(),),
                )
                if fail_after_version == 3:
                    raise SQLiteMigrationError("injected migration failure")
                version = 3
            if version < 4:
                _execute_sql_script(
                    connection, _migration_text("0004_source_structure_snapshots.sql")
                )
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (4, ?)",
                    (_timestamp(),),
                )
                if fail_after_version == 4:
                    raise SQLiteMigrationError("injected migration failure")
                version = 4
            if version < 5:
                _execute_sql_script(
                    connection, _migration_text("0005_source_snapshot_activations.sql")
                )
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (5, ?)",
                    (_timestamp(),),
                )
                if fail_after_version == 5:
                    raise SQLiteMigrationError("injected migration failure")
                version = 5
            if version < 6:
                _execute_sql_script(
                    connection, _migration_text("0006_checkpoint_lifecycle_events.sql")
                )
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (6, ?)",
                    (_timestamp(),),
                )
                if fail_after_version == 6:
                    raise SQLiteMigrationError("injected migration failure")
                version = 6
            if version < 7:
                _execute_sql_script(
                    connection, _migration_text("0007_approved_episodic_events.sql")
                )
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (7, ?)",
                    (_timestamp(),),
                )
                if fail_after_version == 7:
                    raise SQLiteMigrationError("injected migration failure")

    def _map_legacy_checkpoints(self, connection: sqlite3.Connection) -> None:
        headers = {
            row["checkpoint_id"]: row
            for row in connection.execute("SELECT * FROM checkpoints").fetchall()
        }
        revisions = connection.execute("SELECT * FROM checkpoint_revisions").fetchall()
        if any(row["checkpoint_id"] not in headers for row in revisions):
            raise SQLiteMigrationError("legacy revision has no checkpoint header")
        by_id = {row["checkpoint_id"]: row for row in revisions}
        if len(by_id) != len(revisions):
            raise SQLiteMigrationError("legacy checkpoint has ambiguous revisions")
        children: dict[str, list[str]] = {key: [] for key in headers}
        for row in revisions:
            predecessor = row["supersedes_checkpoint_id"]
            if predecessor is not None:
                if predecessor not in by_id:
                    raise SQLiteMigrationError("legacy replacement has a broken predecessor")
                children[str(predecessor)].append(str(row["checkpoint_id"]))
        if any(len(value) > 1 for value in children.values()):
            raise SQLiteMigrationError("legacy replacement chain forks")
        roots = [key for key, row in by_id.items() if row["supersedes_checkpoint_id"] is None]
        visited: set[str] = set()
        for root in roots:
            chain: list[str] = []
            current: str | None = root
            while current is not None:
                if current in chain:
                    raise SQLiteMigrationError("legacy replacement chain contains a cycle")
                chain.append(current)
                next_items = children[current]
                current = next_items[0] if next_items else None
            visited.update(chain)
            scope = headers[root]
            if any(
                tuple(
                    headers[item][field]
                    for field in ("owner_id", "workspace_id", "project_id", "session_id", "task_id")
                )
                != tuple(
                    scope[field]
                    for field in ("owner_id", "workspace_id", "project_id", "session_id", "task_id")
                )
                for item in chain
            ):
                raise SQLiteMigrationError("legacy replacement chain crosses scope")
            revision_ids = [str(uuid.uuid4()) for _ in chain]
            latest = by_id[chain[-1]]
            root_checkpoint = Checkpoint.from_dict(json.loads(by_id[root]["payload_json"]))
            connection.execute(
                "INSERT INTO checkpoint_aggregates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    root,
                    scope["owner_id"],
                    root_checkpoint.scope.visibility.value,
                    scope["workspace_id"],
                    scope["project_id"],
                    scope["session_id"],
                    scope["task_id"],
                    revision_ids[-1],
                    len(chain),
                    latest["status"],
                    by_id[root]["created_at"],
                    latest["created_at"],
                ),
            )
            for number, (legacy_id, revision_id) in enumerate(
                zip(chain, revision_ids, strict=True), start=1
            ):
                legacy = by_id[legacy_id]
                evidence = connection.execute(
                    "SELECT evidence_id FROM checkpoint_evidence WHERE checkpoint_id = ? AND revision = ?",  # noqa: E501
                    (legacy_id, legacy["revision"]),
                ).fetchall()
                if not evidence:
                    raise SQLiteMigrationError("legacy checkpoint is missing provenance")
                predecessor = None if number == 1 else revision_ids[number - 2]
                connection.execute(
                    "INSERT INTO checkpoint_revision_records VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        revision_id,
                        root,
                        number,
                        predecessor,
                        legacy["status"],
                        _json(
                            CheckpointContent.from_legacy(
                                Checkpoint.from_dict(json.loads(legacy["payload_json"]))
                            ).to_dict()
                        ),
                        legacy["created_at"],
                    ),
                )
                connection.executemany(
                    "INSERT INTO checkpoint_revision_evidence VALUES (?, ?)",
                    [(revision_id, item["evidence_id"]) for item in evidence],
                )
        if set(by_id) != visited:
            raise SQLiteMigrationError("legacy replacement chain is cyclic or ambiguous")

    def schema_version(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone()
            if row is None:
                return 0
            version = connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
            ).fetchone()
            return int(version[0])

    def connection_settings(self) -> dict[str, int]:
        with self._connect() as connection:
            return {
                "foreign_keys": int(connection.execute("PRAGMA foreign_keys").fetchone()[0]),
                "busy_timeout": int(connection.execute("PRAGMA busy_timeout").fetchone()[0]),
            }

    def create_checkpoint_aggregate(
        self, aggregate: CheckpointAggregate, initial_revision: CheckpointRevision
    ) -> None:
        self._require_checkpoint_scope(aggregate.scope)
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
        try:
            with self._transaction() as connection:
                self._store_scope(connection, aggregate.scope)
                connection.execute(
                    "INSERT INTO checkpoint_aggregates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    self._aggregate_values(aggregate),
                )
                self._insert_canonical_revision(connection, initial_revision)
                self._insert_lifecycle_event(
                    connection,
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
        except sqlite3.IntegrityError as error:
            raise DuplicateCheckpoint("checkpoint already exists in this scope") from error
        except (ValueError, TypeError):
            raise
        except sqlite3.Error as error:
            raise RepositoryStorageFailure("checkpoint storage operation failed") from error

    def get_aggregate(self, scope: MemoryScope, checkpoint_id: CheckpointId) -> CheckpointAggregate:
        self._require_checkpoint_scope(scope)
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM checkpoint_aggregates WHERE checkpoint_id = ? "
                    "AND owner_id = ? AND visibility = ? AND workspace_id IS ? AND project_id = ? "
                    "AND session_id = ? AND task_id = ?",
                    (str(checkpoint_id), *self._scope_values(scope)),
                ).fetchone()
        except sqlite3.Error as error:
            raise RepositoryStorageFailure("checkpoint storage operation failed") from error
        if row is None:
            raise CheckpointNotFound("checkpoint was not found")
        return self._aggregate_from_row(row)

    def get_current_revision(
        self, scope: MemoryScope, checkpoint_id: CheckpointId
    ) -> CheckpointRevision:
        self._require_checkpoint_scope(scope)
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT revision.* FROM checkpoint_aggregates AS aggregate "
                    "JOIN checkpoint_revision_records AS revision "
                    "ON revision.checkpoint_revision_id = aggregate.current_revision_id "
                    "WHERE aggregate.checkpoint_id = ? AND aggregate.owner_id = ? "
                    "AND aggregate.visibility = ? AND aggregate.workspace_id IS ? "
                    "AND aggregate.project_id = ? "
                    "AND aggregate.session_id = ? AND aggregate.task_id = ?",
                    (str(checkpoint_id), *self._scope_values(scope)),
                ).fetchone()
                if row is None:
                    raise CheckpointNotFound("checkpoint was not found")
                return self._revision_from_row(connection, row, scope)
        except CheckpointNotFound:
            raise
        except sqlite3.Error as error:
            raise RepositoryStorageFailure("checkpoint storage operation failed") from error

    def get_revision(
        self,
        scope: MemoryScope,
        checkpoint_id: CheckpointId,
        *,
        revision_number: int | None = None,
        revision_id: CheckpointRevisionId | None = None,
    ) -> CheckpointRevision:
        self._require_checkpoint_scope(scope)
        if (revision_number is None) == (revision_id is None):
            raise ValueError("provide exactly one revision selector")
        selector = (
            ("revision.revision_number = ?", revision_number)
            if revision_number is not None
            else ("revision.checkpoint_revision_id = ?", str(revision_id))
        )
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT revision.* FROM checkpoint_aggregates AS aggregate "
                    "JOIN checkpoint_revision_records AS revision "
                    "ON revision.checkpoint_id = aggregate.checkpoint_id "
                    "WHERE aggregate.checkpoint_id = ? AND aggregate.owner_id = ? "
                    "AND aggregate.visibility = ? AND aggregate.workspace_id IS ? "
                    "AND aggregate.project_id = ? "
                    "AND aggregate.session_id = ? AND aggregate.task_id = ? AND " + selector[0],
                    (str(checkpoint_id), *self._scope_values(scope), selector[1]),
                ).fetchone()
                if row is None:
                    raise CheckpointNotFound("checkpoint was not found")
                return self._revision_from_row(connection, row, scope)
        except CheckpointNotFound:
            raise
        except sqlite3.Error as error:
            raise RepositoryStorageFailure("checkpoint storage operation failed") from error

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
        self._require_checkpoint_scope(scope)
        if offset < 0 or limit < 1:
            raise ValueError("offset must be non-negative and limit must be positive")
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM checkpoint_aggregates WHERE owner_id = ? "
                    "AND visibility = ? AND workspace_id IS ? AND project_id = ? "
                    "AND session_id = ? "
                    "AND task_id = ? AND lifecycle_status = 'active' "
                    "ORDER BY updated_at DESC, checkpoint_id ASC LIMIT ? OFFSET ?",
                    (*self._scope_values(scope), limit + 1, offset),
                ).fetchall()
        except sqlite3.Error as error:
            raise RepositoryStorageFailure("checkpoint storage operation failed") from error
        items = tuple(self._aggregate_from_row(row) for row in rows[:limit])
        return CheckpointPage(
            items=items,
            next_offset=offset + limit if len(rows) > limit else None,
        )

    def select_current_checkpoint(self, scope: MemoryScope) -> CheckpointAggregate | None:
        items = self.list_current_checkpoints(scope, limit=1).items
        return items[0] if items else None

    def append_event(self, event: CheckpointLifecycleEvent) -> EpisodicEventStoreResult:
        self._require_checkpoint_scope(event.scope)
        try:
            with self._transaction() as connection:
                revision = connection.execute(
                    "SELECT revision.* FROM checkpoint_aggregates AS aggregate "
                    "JOIN checkpoint_revision_records AS revision "
                    "ON revision.checkpoint_id = aggregate.checkpoint_id "
                    "WHERE aggregate.checkpoint_id = ? AND revision.checkpoint_revision_id = ? "
                    "AND aggregate.owner_id = ? AND aggregate.visibility = ? "
                    "AND aggregate.workspace_id IS ? AND aggregate.project_id = ? "
                    "AND aggregate.session_id = ? AND aggregate.task_id = ?",
                    (
                        str(event.checkpoint_id),
                        str(event.revision_id),
                        *self._scope_values(event.scope),
                    ),
                ).fetchone()
                if revision is None:
                    raise InvalidEpisodicEventScope("event revision is unavailable in this scope")
                if (
                    int(revision["revision_number"]) != event.revision_number
                    or str(revision["created_at"]) != event.occurred_at.isoformat()
                ):
                    raise InvalidEpisodicEventScope("event does not match its checkpoint revision")
                existing = connection.execute(
                    "SELECT * FROM checkpoint_lifecycle_events WHERE idempotency_key = ?",
                    (event.idempotency_key,),
                ).fetchone()
                if existing is not None:
                    stored = self._event_from_row(connection, existing, event.scope)
                    if stored == event:
                        return EpisodicEventStoreResult(stored, idempotent=True)
                    raise InvalidEpisodicEventScope("event idempotency key conflicts")
                connection.execute(
                    "INSERT INTO checkpoint_lifecycle_events("
                    "event_id,idempotency_key,event_kind,checkpoint_id,checkpoint_revision_id,"
                    "revision_number,owner_id,visibility,workspace_id,project_id,session_id,task_id,"
                    "occurred_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(event.event_id),
                        event.idempotency_key,
                        event.kind.value,
                        str(event.checkpoint_id),
                        str(event.revision_id),
                        event.revision_number,
                        *self._scope_values(event.scope),
                        event.occurred_at.isoformat(),
                    ),
                )
                return EpisodicEventStoreResult(event, idempotent=False)
        except (InvalidEpisodicEventScope, ValueError, TypeError):
            raise
        except sqlite3.IntegrityError as error:
            raise EpisodicEventStorageFailure("episodic event storage operation failed") from error
        except sqlite3.Error as error:
            raise EpisodicEventStorageFailure("episodic event storage operation failed") from error

    def get_event(self, scope: MemoryScope, event_id: EventId) -> CheckpointLifecycleEvent:
        self._require_checkpoint_scope(scope)
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM checkpoint_lifecycle_events WHERE event_id = ? AND owner_id = ? "
                    "AND visibility = ? AND workspace_id IS ? AND project_id = ? "
                    "AND session_id = ? AND task_id = ?",
                    (str(event_id), *self._scope_values(scope)),
                ).fetchone()
                if row is None:
                    raise EpisodicEventNotFound("episodic event was not found")
                return self._event_from_row(connection, row, scope)
        except EpisodicEventNotFound:
            raise
        except sqlite3.Error as error:
            raise EpisodicEventStorageFailure("episodic event storage operation failed") from error

    def list_events(
        self,
        scope: MemoryScope,
        *,
        checkpoint_id: CheckpointId | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> EpisodicEventPage:
        self._require_checkpoint_scope(scope)
        if offset < 0 or limit < 1:
            raise ValueError("event offset must be non-negative and limit must be positive")
        checkpoint_filter = "" if checkpoint_id is None else " AND checkpoint_id = ?"
        values: tuple[object, ...] = (*self._scope_values(scope),)
        if checkpoint_id is not None:
            values += (str(checkpoint_id),)
        values += (limit + 1, offset)
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM checkpoint_lifecycle_events WHERE owner_id = ? "
                    "AND visibility = ? AND workspace_id IS ? AND project_id = ? "
                    "AND session_id = ? AND task_id = ?"
                    + checkpoint_filter
                    + " ORDER BY event_sequence DESC LIMIT ? OFFSET ?",
                    values,
                ).fetchall()
                items = tuple(self._event_from_row(connection, row, scope) for row in rows[:limit])
        except sqlite3.Error as error:
            raise EpisodicEventStorageFailure("episodic event storage operation failed") from error
        return EpisodicEventPage(items, offset + limit if len(rows) > limit else None)

    def append_approved_event(
        self, event: ApprovedEpisodicEvent
    ) -> ApprovedEpisodicEventStoreResult:
        self._require_approved_episodic_scope(event.scope)
        try:
            with self._transaction() as connection:
                self._store_scope(connection, event.scope)
                existing = connection.execute(
                    "SELECT * FROM approved_episodic_events WHERE owner_id = ? "
                    "AND visibility = ? AND workspace_id IS ? AND project_id = ? "
                    "AND session_id = ? AND task_id = ? AND source_event_key = ?",
                    (*self._scope_values(event.scope), event.source_event_key),
                ).fetchone()
                if existing is not None:
                    stored = self._approved_event_from_row(connection, existing, event.scope)
                    if stored == event:
                        return ApprovedEpisodicEventStoreResult(stored, idempotent=True)
                    raise ApprovedEpisodicEventConflict("approved episodic event key conflicts")
                connection.execute(
                    "INSERT INTO approved_episodic_events("
                    "event_id,source_event_key,event_kind,summary,owner_id,visibility,workspace_id,"
                    "project_id,session_id,task_id,occurred_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(event.event_id),
                        event.source_event_key,
                        event.kind.value,
                        event.summary,
                        *self._scope_values(event.scope),
                        event.occurred_at.isoformat(),
                    ),
                )
                self._insert_approved_event_evidence(connection, event)
                return ApprovedEpisodicEventStoreResult(event, idempotent=False)
        except ApprovedEpisodicEventConflict:
            raise
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise ApprovedEpisodicEventStorageFailure(
                "approved episodic event storage operation failed"
            ) from error

    def get_approved_event(self, scope: MemoryScope, event_id: EventId) -> ApprovedEpisodicEvent:
        self._require_approved_episodic_scope(scope)
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM approved_episodic_events WHERE event_id = ? "
                    "AND owner_id = ? AND visibility = ? AND workspace_id IS ? "
                    "AND project_id = ? AND session_id = ? AND task_id = ?",
                    (str(event_id), *self._scope_values(scope)),
                ).fetchone()
                if row is None:
                    raise ApprovedEpisodicEventNotFound("approved episodic event was not found")
                return self._approved_event_from_row(connection, row, scope)
        except ApprovedEpisodicEventNotFound:
            raise
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise ApprovedEpisodicEventStorageFailure(
                "approved episodic event storage operation failed"
            ) from error

    def list_approved_events(
        self, scope: MemoryScope, *, offset: int = 0, limit: int = 50
    ) -> ApprovedEpisodicEventPage:
        self._require_approved_episodic_scope(scope)
        if offset < 0 or limit < 1:
            raise ValueError("event offset must be non-negative and limit must be positive")
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM approved_episodic_events WHERE owner_id = ? "
                    "AND visibility = ? AND workspace_id IS ? AND project_id = ? "
                    "AND session_id = ? AND task_id = ? "
                    "ORDER BY event_sequence DESC LIMIT ? OFFSET ?",
                    (*self._scope_values(scope), limit + 1, offset),
                ).fetchall()
                items = tuple(
                    self._approved_event_from_row(connection, row, scope) for row in rows[:limit]
                )
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise ApprovedEpisodicEventStorageFailure(
                "approved episodic event storage operation failed"
            ) from error
        return ApprovedEpisodicEventPage(items, offset + limit if len(rows) > limit else None)

    def store_and_activate(
        self,
        artifact: DbtManifestArtifact,
        snapshot_id: DbtSnapshotId,
        *,
        expected_active_snapshot_id: DbtSnapshotId | None = None,
    ) -> ManifestSnapshotStoreResult:
        self._require_project_scope(artifact.scope)
        try:
            with self._transaction() as connection:
                active = self._active_snapshot_row(connection, artifact.scope)
                active_id = (
                    None if active is None else DbtSnapshotId.from_string(active["snapshot_id"])
                )
                if expected_active_snapshot_id != active_id and not (
                    expected_active_snapshot_id is None and active_id is None
                ):
                    raise ActiveSnapshotConflict("expected active snapshot is not current")
                duplicate = connection.execute(
                    "SELECT * FROM dbt_manifest_snapshots WHERE owner_id = ? "
                    "AND workspace_id IS ? AND project_id = ? AND content_digest = ?",
                    (
                        str(artifact.scope.owner_id),
                        _maybe(artifact.scope.workspace_id),
                        str(artifact.scope.project_id),
                        artifact.metadata.content_digest,
                    ),
                ).fetchone()
                if duplicate is not None:
                    existing_id = DbtSnapshotId.from_string(duplicate["snapshot_id"])
                    if active_id != existing_id:
                        connection.execute(
                            "UPDATE dbt_manifest_snapshots SET is_active = 0 "
                            "WHERE owner_id = ? AND project_id = ? AND is_active = 1",
                            (str(artifact.scope.owner_id), str(artifact.scope.project_id)),
                        )
                        connection.execute(
                            "UPDATE dbt_manifest_snapshots SET is_active = 1 WHERE snapshot_id = ?",
                            (str(existing_id),),
                        )
                        duplicate = connection.execute(
                            "SELECT * FROM dbt_manifest_snapshots WHERE snapshot_id = ?",
                            (str(existing_id),),
                        ).fetchone()
                    assert duplicate is not None
                    return ManifestSnapshotStoreResult(
                        snapshot=self._snapshot_from_row(duplicate, artifact.scope), idempotent=True
                    )
                self._store_project_scope(connection, artifact.scope)
                connection.execute(
                    "INSERT INTO dbt_manifest_snapshots VALUES ("
                    "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    self._snapshot_values(snapshot_id, artifact, is_active=False),
                )
                connection.executemany(
                    "INSERT INTO dbt_manifest_nodes VALUES ("
                    "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [self._node_values(snapshot_id, node) for node in artifact.nodes],
                )
                connection.executemany(
                    "INSERT INTO dbt_manifest_edges VALUES (?, ?, ?, ?, ?, ?)",
                    [self._edge_values(snapshot_id, edge) for edge in artifact.edges],
                )
                counts = connection.execute(
                    "SELECT (SELECT COUNT(*) FROM dbt_manifest_nodes "
                    "WHERE snapshot_id = ?) AS nodes, (SELECT COUNT(*) "
                    "FROM dbt_manifest_edges WHERE snapshot_id = ?) AS edges",
                    (str(snapshot_id), str(snapshot_id)),
                ).fetchone()
                if (
                    counts is None
                    or int(counts["nodes"]) != len(artifact.nodes)
                    or int(counts["edges"]) != len(artifact.edges)
                ):
                    raise ProjectIndexStorageFailure("manifest snapshot projection count mismatch")
                if active_id is not None:
                    connection.execute(
                        "UPDATE dbt_manifest_snapshots SET is_active = 0 WHERE snapshot_id = ?",
                        (str(active_id),),
                    )
                updated = connection.execute(
                    "UPDATE dbt_manifest_snapshots SET is_active = 1 WHERE snapshot_id = ? "
                    "AND owner_id = ? AND workspace_id IS ? AND project_id = ?",
                    (
                        str(snapshot_id),
                        str(artifact.scope.owner_id),
                        _maybe(artifact.scope.workspace_id),
                        str(artifact.scope.project_id),
                    ),
                )
                if updated.rowcount != 1:
                    raise ActiveSnapshotConflict("active snapshot could not be selected")
                return ManifestSnapshotStoreResult(
                    snapshot=DbtManifestSnapshot(
                        snapshot_id=snapshot_id,
                        scope=artifact.scope,
                        metadata=artifact.metadata,
                        node_count=len(artifact.nodes),
                        edge_count=len(artifact.edges),
                        is_active=True,
                    ),
                    idempotent=False,
                )
        except (ActiveSnapshotConflict, ProjectIndexStorageFailure):
            raise
        except (ValueError, TypeError):
            raise
        except sqlite3.Error as error:
            raise ProjectIndexStorageFailure("project index storage operation failed") from error

    def get_snapshot(self, scope: MemoryScope, snapshot_id: DbtSnapshotId) -> DbtManifestSnapshot:
        self._require_project_scope(scope)
        try:
            with self._connect() as connection:
                row = self._scoped_snapshot_row(connection, scope, snapshot_id)
        except sqlite3.Error as error:
            raise ProjectIndexStorageFailure("project index storage operation failed") from error
        if row is None:
            raise ManifestSnapshotNotFound("manifest snapshot was not found")
        return self._snapshot_from_row(row, scope)

    def get_active_snapshot(self, scope: MemoryScope) -> DbtManifestSnapshot | None:
        self._require_project_scope(scope)
        try:
            with self._connect() as connection:
                row = self._active_snapshot_row(connection, scope)
        except sqlite3.Error as error:
            raise ProjectIndexStorageFailure("project index storage operation failed") from error
        return None if row is None else self._snapshot_from_row(row, scope)

    def get_node(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, unique_id: DbtNodeId
    ) -> DbtManifestNode:
        nodes = self._scoped_nodes(scope, snapshot_id, "AND node.unique_id = ?", (str(unique_id),))
        if not nodes:
            raise ManifestNodeNotFound("manifest node was not found")
        return nodes[0]

    def iter_nodes(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId
    ) -> tuple[DbtManifestNode, ...]:
        return tuple(self._scoped_nodes(scope, snapshot_id, "", ()))

    def iter_edges(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId
    ) -> tuple[DbtLineageEdge, ...]:
        return tuple(self._scoped_edges(scope, snapshot_id, "", ()))

    def direct_upstream(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, unique_id: DbtNodeId
    ) -> tuple[DbtLineageEdge, ...]:
        self.get_node(scope, snapshot_id, unique_id)
        return tuple(
            self._scoped_edges(
                scope, snapshot_id, "AND edge.child_unique_id = ?", (str(unique_id),)
            )
        )

    def direct_downstream(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, unique_id: DbtNodeId
    ) -> tuple[DbtLineageEdge, ...]:
        self.get_node(scope, snapshot_id, unique_id)
        return tuple(
            self._scoped_edges(
                scope, snapshot_id, "AND edge.parent_unique_id = ?", (str(unique_id),)
            )
        )

    def get_nodes(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, unique_ids: tuple[DbtNodeId, ...]
    ) -> tuple[DbtManifestNode, ...]:
        return tuple(self._scoped_nodes_in(scope, snapshot_id, "node.unique_id", unique_ids))

    def get_upstream_edges(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, child_ids: tuple[DbtNodeId, ...]
    ) -> tuple[DbtLineageEdge, ...]:
        return tuple(self._scoped_edges_in(scope, snapshot_id, "edge.child_unique_id", child_ids))

    def get_downstream_edges(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, parent_ids: tuple[DbtNodeId, ...]
    ) -> tuple[DbtLineageEdge, ...]:
        return tuple(self._scoped_edges_in(scope, snapshot_id, "edge.parent_unique_id", parent_ids))

    def list_snapshots(
        self, scope: MemoryScope, *, offset: int = 0, limit: int = 50
    ) -> ManifestSnapshotPage:
        self._require_project_scope(scope)
        if offset < 0 or limit < 1:
            raise ValueError("offset must be non-negative and limit must be positive")
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM dbt_manifest_snapshots WHERE owner_id = ? "
                    "AND workspace_id IS ? AND project_id = ? ORDER BY ingested_at DESC, "
                    "snapshot_id ASC LIMIT ? OFFSET ?",
                    (
                        str(scope.owner_id),
                        _maybe(scope.workspace_id),
                        str(scope.project_id),
                        limit + 1,
                        offset,
                    ),
                ).fetchall()
        except sqlite3.Error as error:
            raise ProjectIndexStorageFailure("project index storage operation failed") from error
        return ManifestSnapshotPage(
            items=tuple(self._snapshot_from_row(row, scope) for row in rows[:limit]),
            next_offset=offset + limit if len(rows) > limit else None,
        )

    def store_source_and_activate(
        self, artifact: CodeStructureArtifact
    ) -> SourceSnapshotStoreResult:
        """Atomically persist one immutable, static source projection and select it."""
        self._require_project_scope(artifact.snapshot.scope)
        scope = artifact.snapshot.scope
        try:
            with self._transaction() as connection:
                duplicate = connection.execute(
                    "SELECT * FROM source_structure_snapshots WHERE owner_id = ? "
                    "AND workspace_id IS ? AND project_id = ? AND source_digest = ?",
                    (
                        str(scope.owner_id),
                        _maybe(scope.workspace_id),
                        str(scope.project_id),
                        artifact.snapshot.source_digest,
                    ),
                ).fetchone()
                if duplicate is not None:
                    snapshot_id = CodeSnapshotId.from_string(duplicate["snapshot_id"])
                    active = self._active_source_snapshot_row(connection, scope)
                    connection.execute(
                        "UPDATE source_structure_snapshots SET is_active = 0 WHERE owner_id = ? "
                        "AND workspace_id IS ? AND project_id = ? AND is_active = 1",
                        (str(scope.owner_id), _maybe(scope.workspace_id), str(scope.project_id)),
                    )
                    connection.execute(
                        "UPDATE source_structure_snapshots SET is_active = 1 WHERE snapshot_id = ? "
                        "AND owner_id = ? AND workspace_id IS ? AND project_id = ?",
                        (
                            str(snapshot_id),
                            str(scope.owner_id),
                            _maybe(scope.workspace_id),
                            str(scope.project_id),
                        ),
                    )
                    if active is None or active["snapshot_id"] != str(snapshot_id):
                        self._record_source_activation(connection, scope, snapshot_id)
                    return SourceSnapshotStoreResult(
                        self._source_snapshot_from_row(duplicate, scope), idempotent=True
                    )
                self._store_project_scope(connection, scope)
                connection.execute(
                    "INSERT INTO source_structure_snapshots "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(artifact.snapshot.snapshot_id),
                        str(scope.owner_id),
                        scope.visibility.value,
                        _maybe(scope.workspace_id),
                        str(scope.project_id),
                        scope.level.value,
                        artifact.snapshot.source_digest,
                        artifact.snapshot.file_count,
                        artifact.snapshot.symbol_count,
                        artifact.snapshot.edge_count,
                        0,
                    ),
                )
                connection.executemany(
                    "INSERT INTO source_structure_symbols VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (
                            str(artifact.snapshot.snapshot_id),
                            str(symbol.symbol_id),
                            symbol.relative_path,
                            symbol.qualified_name,
                            symbol.kind.value,
                            symbol.line,
                        )
                        for symbol in artifact.symbols
                    ],
                )
                connection.executemany(
                    "INSERT INTO source_structure_edges VALUES (?, ?, ?, ?, ?)",
                    [
                        (
                            str(artifact.snapshot.snapshot_id),
                            str(edge.source_symbol_id),
                            edge.target,
                            edge.kind.value,
                            str(edge.target_symbol_id)
                            if edge.target_symbol_id is not None
                            else None,
                        )
                        for edge in artifact.edges
                    ],
                )
                counts = connection.execute(
                    "SELECT (SELECT COUNT(*) FROM source_structure_symbols WHERE snapshot_id = ?) "
                    "AS symbols, (SELECT COUNT(*) FROM source_structure_edges "
                    "WHERE snapshot_id = ?) "
                    "AS edges",
                    (str(artifact.snapshot.snapshot_id), str(artifact.snapshot.snapshot_id)),
                ).fetchone()
                if (
                    counts is None
                    or int(counts["symbols"]) != len(artifact.symbols)
                    or int(counts["edges"]) != len(artifact.edges)
                ):
                    raise SourceIndexStorageFailure("source snapshot projection count mismatch")
                connection.execute(
                    "UPDATE source_structure_snapshots SET is_active = 0 WHERE owner_id = ? "
                    "AND workspace_id IS ? AND project_id = ? AND is_active = 1",
                    (str(scope.owner_id), _maybe(scope.workspace_id), str(scope.project_id)),
                )
                updated = connection.execute(
                    "UPDATE source_structure_snapshots SET is_active = 1 WHERE snapshot_id = ? "
                    "AND owner_id = ? AND workspace_id IS ? AND project_id = ?",
                    (
                        str(artifact.snapshot.snapshot_id),
                        str(scope.owner_id),
                        _maybe(scope.workspace_id),
                        str(scope.project_id),
                    ),
                )
                if updated.rowcount != 1:
                    raise SourceIndexStorageFailure("source snapshot activation failed")
                self._record_source_activation(connection, scope, artifact.snapshot.snapshot_id)
                return SourceSnapshotStoreResult(artifact.snapshot, idempotent=False)
        except SourceIndexStorageFailure:
            raise
        except (TypeError, ValueError):
            raise
        except sqlite3.Error as error:
            raise SourceIndexStorageFailure("source index storage operation failed") from error

    def get_active_source_snapshot(self, scope: MemoryScope) -> CodeSnapshot | None:
        self._require_project_scope(scope)
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM source_structure_snapshots WHERE owner_id = ? "
                    "AND workspace_id IS ? AND project_id = ? AND is_active = 1",
                    (str(scope.owner_id), _maybe(scope.workspace_id), str(scope.project_id)),
                ).fetchone()
        except sqlite3.Error as error:
            raise SourceIndexStorageFailure("source index storage operation failed") from error
        return None if row is None else self._source_snapshot_from_row(row, scope)

    def get_source_snapshot(self, scope: MemoryScope, snapshot_id: CodeSnapshotId) -> CodeSnapshot:
        self._require_project_scope(scope)
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM source_structure_snapshots "
                    "WHERE snapshot_id = ? AND owner_id = ? "
                    "AND workspace_id IS ? AND project_id = ?",
                    (
                        str(snapshot_id),
                        str(scope.owner_id),
                        _maybe(scope.workspace_id),
                        str(scope.project_id),
                    ),
                ).fetchone()
        except sqlite3.Error as error:
            raise SourceIndexStorageFailure("source index storage operation failed") from error
        if row is None:
            raise SourceSnapshotNotFound("source snapshot was not found")
        return self._source_snapshot_from_row(row, scope)

    def latest_source_transition(
        self, scope: MemoryScope
    ) -> tuple[CodeSnapshot, CodeSnapshot] | None:
        """Return the two most recently activated snapshots in explicit event order."""
        self._require_project_scope(scope)
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT snapshot.* FROM source_snapshot_activations AS activation "
                    "JOIN source_structure_snapshots AS snapshot "
                    "ON snapshot.snapshot_id = activation.snapshot_id "
                    "WHERE activation.owner_id = ? AND activation.workspace_id IS ? "
                    "AND activation.project_id = ? "
                    "ORDER BY activation.activation_id DESC LIMIT 2",
                    (str(scope.owner_id), _maybe(scope.workspace_id), str(scope.project_id)),
                ).fetchall()
        except sqlite3.Error as error:
            raise SourceIndexStorageFailure("source index storage operation failed") from error
        if len(rows) < 2:
            return None
        return (
            self._source_snapshot_from_row(rows[1], scope),
            self._source_snapshot_from_row(rows[0], scope),
        )

    def list_source_activation_history(
        self, scope: MemoryScope, *, limit: int = 20
    ) -> tuple[CodeSnapshot, ...]:
        self._require_project_scope(scope)
        if limit < 1 or limit > 100:
            raise ValueError("source snapshot history limit must be between 1 and 100")
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT snapshot.* FROM source_snapshot_activations AS activation "
                    "JOIN source_structure_snapshots AS snapshot "
                    "ON snapshot.snapshot_id = activation.snapshot_id "
                    "WHERE activation.owner_id = ? AND activation.workspace_id IS ? "
                    "AND activation.project_id = ? "
                    "ORDER BY activation.activation_id DESC LIMIT ?",
                    (
                        str(scope.owner_id),
                        _maybe(scope.workspace_id),
                        str(scope.project_id),
                        limit,
                    ),
                ).fetchall()
        except sqlite3.Error as error:
            raise SourceIndexStorageFailure("source index storage operation failed") from error
        return tuple(self._source_snapshot_from_row(row, scope) for row in rows)

    @staticmethod
    def _source_snapshot_from_row(row: sqlite3.Row, scope: MemoryScope) -> CodeSnapshot:
        return CodeSnapshot(
            CodeSnapshotId.from_string(row["snapshot_id"]),
            scope,
            row["source_digest"],
            int(row["file_count"]),
            int(row["symbol_count"]),
            int(row["edge_count"]),
        )

    def _store_project_scope(self, connection: sqlite3.Connection, scope: MemoryScope) -> None:
        if scope.project_id is None:
            raise InvalidManifestSnapshotScope("dbt snapshot operations require a project")
        connection.execute(
            "INSERT OR IGNORE INTO principals(owner_id) VALUES (?)", (str(scope.owner_id),)
        )
        if scope.workspace_id is not None:
            connection.execute(
                "INSERT OR IGNORE INTO workspaces(workspace_id, owner_id) VALUES (?, ?)",
                (str(scope.workspace_id), str(scope.owner_id)),
            )
        connection.execute(
            "INSERT OR IGNORE INTO projects(project_id, workspace_id, owner_id) VALUES (?, ?, ?)",
            (str(scope.project_id), _maybe(scope.workspace_id), str(scope.owner_id)),
        )

    @staticmethod
    def _record_source_activation(
        connection: sqlite3.Connection, scope: MemoryScope, snapshot_id: CodeSnapshotId
    ) -> None:
        connection.execute(
            "INSERT INTO source_snapshot_activations("
            "snapshot_id, owner_id, workspace_id, project_id, activated_at) VALUES (?, ?, ?, ?, ?)",
            (
                str(snapshot_id),
                str(scope.owner_id),
                _maybe(scope.workspace_id),
                str(scope.project_id),
                _timestamp(),
            ),
        )

    @staticmethod
    def _active_source_snapshot_row(
        connection: sqlite3.Connection, scope: MemoryScope
    ) -> sqlite3.Row | None:
        return cast(
            sqlite3.Row | None,
            connection.execute(
                "SELECT snapshot_id FROM source_structure_snapshots WHERE owner_id = ? "
                "AND workspace_id IS ? AND project_id = ? AND is_active = 1",
                (str(scope.owner_id), _maybe(scope.workspace_id), str(scope.project_id)),
            ).fetchone(),
        )

    def _active_snapshot_row(
        self, connection: sqlite3.Connection, scope: MemoryScope
    ) -> sqlite3.Row | None:
        return cast(
            sqlite3.Row | None,
            connection.execute(
                "SELECT * FROM dbt_manifest_snapshots WHERE owner_id = ? AND workspace_id IS ? "
                "AND project_id = ? AND is_active = 1",
                (str(scope.owner_id), _maybe(scope.workspace_id), str(scope.project_id)),
            ).fetchone(),
        )

    def _scoped_snapshot_row(
        self, connection: sqlite3.Connection, scope: MemoryScope, snapshot_id: DbtSnapshotId
    ) -> sqlite3.Row | None:
        return cast(
            sqlite3.Row | None,
            connection.execute(
                "SELECT * FROM dbt_manifest_snapshots WHERE snapshot_id = ? AND owner_id = ? "
                "AND workspace_id IS ? AND project_id = ?",
                (
                    str(snapshot_id),
                    str(scope.owner_id),
                    _maybe(scope.workspace_id),
                    str(scope.project_id),
                ),
            ).fetchone(),
        )

    def _scoped_nodes(
        self,
        scope: MemoryScope,
        snapshot_id: DbtSnapshotId,
        extra: str,
        values: tuple[str, ...],
    ) -> list[DbtManifestNode]:
        self._require_project_scope(scope)
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT node.* FROM dbt_manifest_nodes AS node JOIN "
                    "dbt_manifest_snapshots AS snapshot ON snapshot.snapshot_id = node.snapshot_id "
                    "WHERE node.snapshot_id = ? AND snapshot.owner_id = ? "
                    "AND snapshot.workspace_id IS ? AND snapshot.project_id = ? "
                    + extra
                    + " ORDER BY node.unique_id ASC",
                    (
                        str(snapshot_id),
                        str(scope.owner_id),
                        _maybe(scope.workspace_id),
                        str(scope.project_id),
                        *values,
                    ),
                ).fetchall()
        except sqlite3.Error as error:
            raise ProjectIndexStorageFailure("project index storage operation failed") from error
        return [self._node_from_row(row) for row in rows]

    def _scoped_nodes_in(
        self,
        scope: MemoryScope,
        snapshot_id: DbtSnapshotId,
        column: str,
        ids: tuple[DbtNodeId, ...],
    ) -> list[DbtManifestNode]:
        if not ids:
            return []
        values = tuple(sorted({str(item) for item in ids}))
        placeholders = ",".join("?" for _ in values)
        return self._scoped_nodes(scope, snapshot_id, f"AND {column} IN ({placeholders})", values)

    def _scoped_edges(
        self,
        scope: MemoryScope,
        snapshot_id: DbtSnapshotId,
        extra: str,
        values: tuple[str, ...],
    ) -> list[DbtLineageEdge]:
        self._require_project_scope(scope)
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT edge.* FROM dbt_manifest_edges AS edge JOIN "
                    "dbt_manifest_snapshots AS snapshot ON snapshot.snapshot_id = edge.snapshot_id "
                    "WHERE edge.snapshot_id = ? AND snapshot.owner_id = ? "
                    "AND snapshot.workspace_id IS ? AND snapshot.project_id = ? "
                    + extra
                    + " ORDER BY edge.parent_unique_id ASC, edge.child_unique_id ASC",
                    (
                        str(snapshot_id),
                        str(scope.owner_id),
                        _maybe(scope.workspace_id),
                        str(scope.project_id),
                        *values,
                    ),
                ).fetchall()
        except sqlite3.Error as error:
            raise ProjectIndexStorageFailure("project index storage operation failed") from error
        return [self._edge_from_row(row) for row in rows]

    def _scoped_edges_in(
        self,
        scope: MemoryScope,
        snapshot_id: DbtSnapshotId,
        column: str,
        ids: tuple[DbtNodeId, ...],
    ) -> list[DbtLineageEdge]:
        if not ids:
            return []
        values = tuple(sorted({str(item) for item in ids}))
        placeholders = ",".join("?" for _ in values)
        return self._scoped_edges(scope, snapshot_id, f"AND {column} IN ({placeholders})", values)

    @staticmethod
    def _require_project_scope(scope: MemoryScope) -> None:
        if not isinstance(scope, MemoryScope) or scope.level is not ScopeLevel.PROJECT:
            raise InvalidManifestSnapshotScope(
                "dbt snapshot operations require explicit project scope"
            )

    @staticmethod
    def _snapshot_values(
        snapshot_id: DbtSnapshotId, artifact: DbtManifestArtifact, *, is_active: bool
    ) -> tuple[object, ...]:
        metadata = artifact.metadata
        scope = artifact.scope
        state: dict[str, object] | None = (
            None
            if metadata.source_state is None
            else {
                "git_commit": metadata.source_state.git_commit,
                "working_tree_fingerprint": metadata.source_state.working_tree_fingerprint,
                "dirty": metadata.source_state.dirty,
                "target_name": metadata.source_state.target_name,
            }
        )
        return (
            str(snapshot_id),
            str(scope.owner_id),
            scope.visibility.value,
            _maybe(scope.workspace_id),
            str(scope.project_id),
            scope.level.value,
            metadata.schema_version,
            metadata.dbt_version,
            metadata.project_name,
            None if metadata.generated_at is None else metadata.generated_at.isoformat(),
            metadata.ingested_at.isoformat(),
            metadata.invocation_id,
            metadata.content_digest,
            metadata.normalized_graph_digest,
            None if state is None else _json(state),
            metadata.currentness.value,
            metadata.source_identity,
            len(artifact.nodes),
            len(artifact.edges),
            int(is_active),
        )

    @staticmethod
    def _node_values(snapshot_id: DbtSnapshotId, node: DbtManifestNode) -> tuple[object, ...]:
        return (
            str(snapshot_id),
            str(node.unique_id),
            node.raw_resource_type,
            node.package_name,
            node.name,
            node.alias,
            node.database,
            node.schema_name,
            node.relation_name,
            node.original_file_path,
            int(node.enabled),
            node.checksum,
            _json({"tags": list(node.tags)}),
            _json(node.evidence.to_dict()),
        )

    @staticmethod
    def _edge_values(snapshot_id: DbtSnapshotId, edge: DbtLineageEdge) -> tuple[object, ...]:
        return (
            str(snapshot_id),
            str(edge.parent_id),
            str(edge.child_id),
            edge.edge_type.value,
            edge.artifact_digest,
            _json(edge.evidence.to_dict()),
        )

    @staticmethod
    def _snapshot_from_row(row: sqlite3.Row, scope: MemoryScope) -> DbtManifestSnapshot:
        raw_state = (
            None if row["source_state_json"] is None else json.loads(row["source_state_json"])
        )
        state = None if raw_state is None else SourceStateFingerprint(**raw_state)
        return DbtManifestSnapshot(
            snapshot_id=DbtSnapshotId.from_string(row["snapshot_id"]),
            scope=scope,
            metadata=DbtArtifactMetadata(
                schema_version=row["manifest_schema_version"],
                dbt_version=row["dbt_version"],
                project_name=row["project_name"],
                generated_at=None
                if row["generated_at"] is None
                else datetime.fromisoformat(row["generated_at"]),
                invocation_id=row["invocation_id"],
                content_digest=row["content_digest"],
                normalized_graph_digest=row["normalized_graph_digest"],
                source_identity=row["source_identity"],
                ingested_at=datetime.fromisoformat(row["ingested_at"]),
                source_state=state,
                currentness=ArtifactCurrentness(row["currentness"]),
            ),
            node_count=int(row["node_count"]),
            edge_count=int(row["edge_count"]),
            is_active=bool(row["is_active"]),
        )

    @staticmethod
    def _node_from_row(row: sqlite3.Row) -> DbtManifestNode:
        evidence = EvidenceReference.from_dict(json.loads(row["evidence_json"]))
        tags = tuple(json.loads(row["tags_json"])["tags"])
        return DbtManifestNode(
            unique_id=DbtNodeId(row["unique_id"]),
            resource_type=(
                DbtResourceType(row["resource_type"])
                if row["resource_type"] in {item.value for item in DbtResourceType}
                else DbtResourceType.OTHER
            ),
            raw_resource_type=row["resource_type"],
            package_name=row["package_name"],
            name=row["name"],
            alias=row["alias"],
            database=row["database_name"],
            schema_name=row["schema_name"],
            relation_name=row["relation_name"],
            original_file_path=row["original_file_path"],
            patch_path=None,
            enabled=bool(row["enabled"]),
            checksum=row["checksum"],
            tags=tags,
            description="",
            dependency_ids=(),
            evidence=evidence,
        )

    @staticmethod
    def _edge_from_row(row: sqlite3.Row) -> DbtLineageEdge:
        return DbtLineageEdge(
            parent_id=DbtNodeId(row["parent_unique_id"]),
            child_id=DbtNodeId(row["child_unique_id"]),
            edge_type=LineageEdgeType(row["edge_type"]),
            evidence=EvidenceReference.from_dict(json.loads(row["evidence_json"])),
            artifact_digest=row["artifact_digest"],
        )

    def _store_scope(self, connection: sqlite3.Connection, scope: MemoryScope) -> None:
        if scope.project_id is None or scope.session_id is None or scope.task_id is None:
            raise ValueError(
                "checkpoint storage requires explicit project, session, and task scope"
            )
        connection.execute(
            "INSERT OR IGNORE INTO principals(owner_id) VALUES (?)", (str(scope.owner_id),)
        )
        if scope.workspace_id is not None:
            connection.execute(
                "INSERT OR IGNORE INTO workspaces(workspace_id, owner_id) VALUES (?, ?)",
                (str(scope.workspace_id), str(scope.owner_id)),
            )
        connection.execute(
            "INSERT OR IGNORE INTO projects(project_id, workspace_id, owner_id) VALUES (?, ?, ?)",
            (str(scope.project_id), _maybe(scope.workspace_id), str(scope.owner_id)),
        )
        connection.execute(
            "INSERT OR IGNORE INTO sessions(session_id, project_id) VALUES (?, ?)",
            (str(scope.session_id), str(scope.project_id)),
        )
        connection.execute(
            "INSERT OR IGNORE INTO tasks(task_id, session_id) VALUES (?, ?)",
            (str(scope.task_id), str(scope.session_id)),
        )
        if scope.agent_id is not None:
            connection.execute(
                "INSERT OR IGNORE INTO agents(agent_id, project_id) VALUES (?, ?)",
                (str(scope.agent_id), str(scope.project_id)),
            )

    @staticmethod
    def _require_approved_episodic_scope(scope: MemoryScope) -> None:
        if not isinstance(scope, MemoryScope) or scope.level is not ScopeLevel.TASK:
            raise InvalidApprovedEpisodicEventScope(
                "approved episodic events require explicit task scope"
            )

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
        self._require_checkpoint_scope(scope)
        try:
            with self._transaction() as connection:
                aggregate_row = self._scoped_aggregate_row(connection, scope, checkpoint_id)
                if aggregate_row is None:
                    raise CheckpointNotFound("checkpoint was not found")
                aggregate = self._aggregate_from_row(aggregate_row)
                current_row = connection.execute(
                    "SELECT * FROM checkpoint_revision_records WHERE checkpoint_revision_id = ?",
                    (str(aggregate.current_revision_id),),
                ).fetchone()
                if current_row is None:
                    raise RepositoryStorageFailure("checkpoint storage is inconsistent")
                current = self._revision_from_row(connection, current_row, scope)
                if aggregate.lifecycle_status is not CheckpointStatus.ACTIVE:
                    if self._is_identical_terminal_retry(
                        current, expected_revision_id, status, content, evidence_references, reason
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
                    revision_id=CheckpointRevisionId.new(),
                    checkpoint_id=checkpoint_id,
                    revision_number=aggregate.current_revision_number + 1,
                    predecessor_revision_id=aggregate.current_revision_id,
                    scope=scope,
                    content=content,
                    status=status,
                    evidence_references=evidence_references,
                    created_at=created_at,
                )
                self._insert_canonical_revision(connection, revision)
                if (
                    self._advance_current_pointer(
                        connection, scope, checkpoint_id, expected_revision_id, revision
                    )
                    != 1
                ):
                    raise RevisionConflict("expected revision is not current")
                self._insert_lifecycle_event(
                    connection,
                    CheckpointLifecycleEvent.for_revision(
                        scope=scope,
                        kind=event_kind,
                        checkpoint_id=revision.checkpoint_id,
                        revision_id=revision.revision_id,
                        revision_number=revision.revision_number,
                        occurred_at=revision.created_at,
                        evidence_references=revision.evidence_references,
                    ),
                )
                return revision
        except (
            CheckpointNotFound,
            InvalidLifecycleTransition,
            RepositoryStorageFailure,
            RevisionConflict,
        ):
            raise
        except (ValueError, TypeError):
            raise
        except sqlite3.Error as error:
            raise RepositoryStorageFailure("checkpoint storage operation failed") from error

    def _insert_canonical_revision(
        self, connection: sqlite3.Connection, revision: CheckpointRevision
    ) -> None:
        connection.execute(
            "INSERT INTO checkpoint_revision_records VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                str(revision.revision_id),
                str(revision.checkpoint_id),
                revision.revision_number,
                _maybe(revision.predecessor_revision_id),
                revision.status.value,
                _json(revision.content.to_dict()),
                revision.created_at.isoformat(),
            ),
        )
        for evidence in revision.evidence_references:
            connection.execute(
                "INSERT OR IGNORE INTO evidence(evidence_id, source_id, payload_json) "
                "VALUES (?, ?, ?)",
                (str(evidence.evidence_id), str(evidence.source_id), _json(evidence.to_dict())),
            )
        connection.executemany(
            "INSERT INTO checkpoint_revision_evidence VALUES (?, ?)",
            [
                (str(revision.revision_id), str(evidence.evidence_id))
                for evidence in revision.evidence_references
            ],
        )

    def _insert_lifecycle_event(
        self, connection: sqlite3.Connection, event: CheckpointLifecycleEvent
    ) -> None:
        connection.execute(
            "INSERT INTO checkpoint_lifecycle_events("
            "event_id,idempotency_key,event_kind,checkpoint_id,checkpoint_revision_id,"
            "revision_number,owner_id,visibility,workspace_id,project_id,session_id,task_id,"
            "occurred_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(event.event_id),
                event.idempotency_key,
                event.kind.value,
                str(event.checkpoint_id),
                str(event.revision_id),
                event.revision_number,
                *self._scope_values(event.scope),
                event.occurred_at.isoformat(),
            ),
        )

    @staticmethod
    def _insert_approved_event_evidence(
        connection: sqlite3.Connection, event: ApprovedEpisodicEvent
    ) -> None:
        for evidence in event.evidence_references:
            connection.execute(
                "INSERT OR IGNORE INTO evidence(evidence_id, source_id, payload_json) "
                "VALUES (?, ?, ?)",
                (str(evidence.evidence_id), str(evidence.source_id), _json(evidence.to_dict())),
            )
            connection.execute(
                "INSERT INTO approved_episodic_event_evidence(event_id, evidence_id) VALUES (?, ?)",
                (str(event.event_id), str(evidence.evidence_id)),
            )

    def _advance_current_pointer(
        self,
        connection: sqlite3.Connection,
        scope: MemoryScope,
        checkpoint_id: CheckpointId,
        expected_revision_id: CheckpointRevisionId,
        revision: CheckpointRevision,
    ) -> int:
        update = connection.execute(
            "UPDATE checkpoint_aggregates SET current_revision_id = ?, "
            "current_revision_number = ?, lifecycle_status = ?, updated_at = ? "
            "WHERE checkpoint_id = ? AND owner_id = ? AND visibility = ? "
            "AND workspace_id IS ? AND project_id = ? AND session_id = ? AND task_id = ? "
            "AND lifecycle_status = 'active' AND current_revision_id = ?",
            (
                str(revision.revision_id),
                revision.revision_number,
                revision.status.value,
                revision.created_at.isoformat(),
                str(checkpoint_id),
                *self._scope_values(scope),
                str(expected_revision_id),
            ),
        )
        return update.rowcount

    def _scoped_aggregate_row(
        self, connection: sqlite3.Connection, scope: MemoryScope, checkpoint_id: CheckpointId
    ) -> sqlite3.Row | None:
        row = connection.execute(
            "SELECT * FROM checkpoint_aggregates WHERE checkpoint_id = ? "
            "AND owner_id = ? AND visibility = ? AND workspace_id IS ? AND project_id = ? "
            "AND session_id = ? AND task_id = ?",
            (str(checkpoint_id), *self._scope_values(scope)),
        ).fetchone()
        return cast(sqlite3.Row | None, row)

    @staticmethod
    def _require_checkpoint_scope(scope: MemoryScope) -> None:
        if not isinstance(scope, MemoryScope) or scope.level is not ScopeLevel.TASK:
            raise InvalidCheckpointScope("checkpoint operations require explicit task scope")

    @staticmethod
    def _scope_values(scope: MemoryScope) -> tuple[str, str, str | None, str, str, str]:
        return (
            str(scope.owner_id),
            scope.visibility.value,
            _maybe(scope.workspace_id),
            str(scope.project_id),
            str(scope.session_id),
            str(scope.task_id),
        )

    @staticmethod
    def _aggregate_values(aggregate: CheckpointAggregate) -> tuple[object, ...]:
        scope = aggregate.scope
        return (
            str(aggregate.checkpoint_id),
            str(scope.owner_id),
            scope.visibility.value,
            _maybe(scope.workspace_id),
            str(scope.project_id),
            str(scope.session_id),
            str(scope.task_id),
            str(aggregate.current_revision_id),
            aggregate.current_revision_number,
            aggregate.lifecycle_status.value,
            aggregate.created_at.isoformat(),
            aggregate.updated_at.isoformat(),
        )

    @staticmethod
    def _aggregate_from_row(row: sqlite3.Row) -> CheckpointAggregate:
        scope = MemoryScope(
            owner_id=OwnerId.from_string(row["owner_id"]),
            level=ScopeLevel.TASK,
            visibility=Visibility(row["visibility"]),
            workspace_id=(
                None
                if row["workspace_id"] is None
                else WorkspaceId.from_string(row["workspace_id"])
            ),
            project_id=ProjectId.from_string(row["project_id"]),
            session_id=SessionId.from_string(row["session_id"]),
            task_id=TaskId.from_string(row["task_id"]),
        )
        return CheckpointAggregate(
            checkpoint_id=CheckpointId.from_string(row["checkpoint_id"]),
            scope=scope,
            current_revision_id=CheckpointRevisionId.from_string(row["current_revision_id"]),
            current_revision_number=int(row["current_revision_number"]),
            lifecycle_status=CheckpointStatus(row["lifecycle_status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _revision_from_row(
        connection: sqlite3.Connection, row: sqlite3.Row, scope: MemoryScope
    ) -> CheckpointRevision:
        evidence_rows = connection.execute(
            "SELECT evidence.payload_json FROM checkpoint_revision_evidence AS link "
            "JOIN evidence ON evidence.evidence_id = link.evidence_id "
            "WHERE link.checkpoint_revision_id = ? ORDER BY link.evidence_id ASC",
            (row["checkpoint_revision_id"],),
        ).fetchall()
        return CheckpointRevision(
            revision_id=CheckpointRevisionId.from_string(row["checkpoint_revision_id"]),
            checkpoint_id=CheckpointId.from_string(row["checkpoint_id"]),
            revision_number=int(row["revision_number"]),
            predecessor_revision_id=(
                None
                if row["predecessor_revision_id"] is None
                else CheckpointRevisionId.from_string(row["predecessor_revision_id"])
            ),
            scope=scope,
            content=CheckpointContent.from_dict(json.loads(row["payload_json"])),
            status=CheckpointStatus(row["status"]),
            evidence_references=tuple(
                EvidenceReference.from_dict(json.loads(item["payload_json"]))
                for item in evidence_rows
            ),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _event_from_row(
        connection: sqlite3.Connection, row: sqlite3.Row, scope: MemoryScope
    ) -> CheckpointLifecycleEvent:
        revision_row = connection.execute(
            "SELECT * FROM checkpoint_revision_records WHERE checkpoint_revision_id = ?",
            (row["checkpoint_revision_id"],),
        ).fetchone()
        if revision_row is None:
            raise EpisodicEventStorageFailure("episodic event revision is unavailable")
        revision = SQLiteCheckpointRepository._revision_from_row(connection, revision_row, scope)
        return CheckpointLifecycleEvent(
            EventId.from_string(row["event_id"]),
            scope,
            CheckpointEventKind(row["event_kind"]),
            CheckpointId.from_string(row["checkpoint_id"]),
            CheckpointRevisionId.from_string(row["checkpoint_revision_id"]),
            int(row["revision_number"]),
            datetime.fromisoformat(row["occurred_at"]),
            row["idempotency_key"],
            revision.evidence_references,
        )

    @staticmethod
    def _approved_event_from_row(
        connection: sqlite3.Connection, row: sqlite3.Row, scope: MemoryScope
    ) -> ApprovedEpisodicEvent:
        evidence_rows = connection.execute(
            "SELECT evidence.payload_json FROM approved_episodic_event_evidence AS link "
            "JOIN evidence ON evidence.evidence_id = link.evidence_id "
            "WHERE link.event_id = ? ORDER BY link.evidence_id ASC",
            (row["event_id"],),
        ).fetchall()
        return ApprovedEpisodicEvent(
            event_id=EventId.from_string(row["event_id"]),
            scope=scope,
            kind=ApprovedEventKind(row["event_kind"]),
            summary=row["summary"],
            source_event_key=row["source_event_key"],
            occurred_at=datetime.fromisoformat(row["occurred_at"]),
            evidence_references=tuple(
                EvidenceReference.from_dict(json.loads(item["payload_json"]))
                for item in evidence_rows
            ),
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


def _json(value: dict[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _execute_sql_script(connection: sqlite3.Connection, script: str) -> None:
    """Execute complete SQLite statements without breaking trigger bodies on semicolons."""
    statement = ""
    for line in script.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            connection.execute(statement)
            statement = ""
    if statement.strip():
        raise SQLiteMigrationError("migration contains an incomplete SQL statement")


def _maybe(value: object | None) -> str | None:
    return None if value is None else str(value)


class SQLiteSourceStructureRepository:
    """Scoped SQLite adapter for immutable multi-language source-structure snapshots.

    It deliberately shares Mnemo's one local database and migration lifecycle while
    exposing only source-structure operations through its public contract.
    """

    def __init__(self, path: Path, *, base_directory: Path | None = None) -> None:
        self._backend = SQLiteCheckpointRepository(path, base_directory=base_directory)

    def migrate(self, *, fail_after_version: int | None = None) -> None:
        self._backend.migrate(fail_after_version=fail_after_version)

    def store_and_activate(self, artifact: CodeStructureArtifact) -> SourceSnapshotStoreResult:
        return self._backend.store_source_and_activate(artifact)

    def get_active_snapshot(self, scope: MemoryScope) -> CodeSnapshot | None:
        return self._backend.get_active_source_snapshot(scope)

    def get_snapshot(self, scope: MemoryScope, snapshot_id: CodeSnapshotId) -> CodeSnapshot:
        return self._backend.get_source_snapshot(scope, snapshot_id)

    def latest_transition(self, scope: MemoryScope) -> tuple[CodeSnapshot, CodeSnapshot] | None:
        return self._backend.latest_source_transition(scope)

    def list_activation_history(
        self, scope: MemoryScope, *, limit: int = 20
    ) -> tuple[CodeSnapshot, ...]:
        return self._backend.list_source_activation_history(scope, limit=limit)

    def iter_symbols(
        self, scope: MemoryScope, snapshot_id: CodeSnapshotId
    ) -> tuple[CodeSymbol, ...]:
        self._backend.get_source_snapshot(scope, snapshot_id)
        try:
            with self._backend._connect() as connection:
                rows = connection.execute(
                    "SELECT symbol.* FROM source_structure_symbols AS symbol JOIN "
                    "source_structure_snapshots AS snapshot "
                    "ON snapshot.snapshot_id = symbol.snapshot_id "
                    "WHERE symbol.snapshot_id = ? AND snapshot.owner_id = ? "
                    "AND snapshot.workspace_id IS ? AND snapshot.project_id = ? "
                    "ORDER BY symbol.relative_path ASC, symbol.line_number ASC, "
                    "symbol.qualified_name ASC, symbol.symbol_id ASC",
                    (
                        str(snapshot_id),
                        str(scope.owner_id),
                        _maybe(scope.workspace_id),
                        str(scope.project_id),
                    ),
                ).fetchall()
        except sqlite3.Error as error:
            raise SourceIndexStorageFailure("source index storage operation failed") from error
        return tuple(
            CodeSymbol(
                snapshot_id,
                CodeSymbolId.from_string(row["symbol_id"]),
                row["relative_path"],
                row["qualified_name"],
                CodeSymbolKind(row["kind"]),
                int(row["line_number"]),
            )
            for row in rows
        )

    def iter_edges(self, scope: MemoryScope, snapshot_id: CodeSnapshotId) -> tuple[CodeEdge, ...]:
        self._backend.get_source_snapshot(scope, snapshot_id)
        try:
            with self._backend._connect() as connection:
                rows = connection.execute(
                    "SELECT edge.* FROM source_structure_edges AS edge JOIN "
                    "source_structure_snapshots AS snapshot "
                    "ON snapshot.snapshot_id = edge.snapshot_id "
                    "WHERE edge.snapshot_id = ? AND snapshot.owner_id = ? "
                    "AND snapshot.workspace_id IS ? AND snapshot.project_id = ? "
                    "ORDER BY edge.source_symbol_id ASC, edge.target ASC, edge.edge_type ASC",
                    (
                        str(snapshot_id),
                        str(scope.owner_id),
                        _maybe(scope.workspace_id),
                        str(scope.project_id),
                    ),
                ).fetchall()
        except sqlite3.Error as error:
            raise SourceIndexStorageFailure("source index storage operation failed") from error
        return tuple(
            CodeEdge(
                snapshot_id,
                CodeSymbolId.from_string(row["source_symbol_id"]),
                row["target"],
                CodeEdgeKind(row["edge_type"]),
                CodeSymbolId.from_string(row["target_symbol_id"])
                if row["target_symbol_id"] is not None
                else None,
            )
            for row in rows
        )

    def find_symbols(
        self, scope: MemoryScope, snapshot_id: CodeSnapshotId, query: str, *, limit: int
    ) -> tuple[CodeSymbol, ...]:
        if not query.strip() or limit < 1:
            return ()
        self._backend.get_source_snapshot(scope, snapshot_id)
        escaped = query.casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        try:
            with self._backend._connect() as connection:
                rows = connection.execute(
                    "SELECT symbol.* FROM source_structure_symbols AS symbol JOIN "
                    "source_structure_snapshots AS snapshot "
                    "ON snapshot.snapshot_id = symbol.snapshot_id "
                    "WHERE symbol.snapshot_id = ? AND snapshot.owner_id = ? "
                    "AND snapshot.workspace_id IS ? AND snapshot.project_id = ? "
                    "AND (lower(symbol.qualified_name) LIKE ? ESCAPE '\\' "
                    "OR lower(symbol.relative_path) LIKE ? ESCAPE '\\') "
                    "ORDER BY symbol.relative_path ASC, symbol.line_number ASC, "
                    "symbol.qualified_name ASC, symbol.symbol_id ASC LIMIT ?",
                    (
                        str(snapshot_id),
                        str(scope.owner_id),
                        _maybe(scope.workspace_id),
                        str(scope.project_id),
                        f"%{escaped}%",
                        f"%{escaped}%",
                        limit,
                    ),
                ).fetchall()
        except sqlite3.Error as error:
            raise SourceIndexStorageFailure("source index storage operation failed") from error
        return self._symbols_from_rows(snapshot_id, rows)

    def module_symbols_for_paths(
        self, scope: MemoryScope, snapshot_id: CodeSnapshotId, relative_paths: tuple[str, ...]
    ) -> tuple[CodeSymbol, ...]:
        if not relative_paths:
            return ()
        self._backend.get_source_snapshot(scope, snapshot_id)
        values = tuple(dict.fromkeys(relative_paths))
        placeholders = ", ".join("?" for _ in values)
        try:
            with self._backend._connect() as connection:
                rows = connection.execute(
                    "SELECT symbol.* FROM source_structure_symbols AS symbol JOIN "
                    "source_structure_snapshots AS snapshot "
                    "ON snapshot.snapshot_id = symbol.snapshot_id "
                    "WHERE symbol.snapshot_id = ? AND snapshot.owner_id = ? "
                    "AND snapshot.workspace_id IS ? AND snapshot.project_id = ? "
                    "AND symbol.kind = 'module' AND symbol.relative_path IN ("
                    + placeholders
                    + ") ORDER BY symbol.relative_path ASC, symbol.symbol_id ASC",
                    (
                        str(snapshot_id),
                        str(scope.owner_id),
                        _maybe(scope.workspace_id),
                        str(scope.project_id),
                        *values,
                    ),
                ).fetchall()
        except sqlite3.Error as error:
            raise SourceIndexStorageFailure("source index storage operation failed") from error
        return self._symbols_from_rows(snapshot_id, rows)

    def symbols_by_ids(
        self, scope: MemoryScope, snapshot_id: CodeSnapshotId, symbol_ids: tuple[CodeSymbolId, ...]
    ) -> tuple[CodeSymbol, ...]:
        if not symbol_ids:
            return ()
        self._backend.get_source_snapshot(scope, snapshot_id)
        values = tuple(dict.fromkeys(str(symbol_id) for symbol_id in symbol_ids))
        placeholders = ", ".join("?" for _ in values)
        try:
            with self._backend._connect() as connection:
                rows = connection.execute(
                    "SELECT symbol.* FROM source_structure_symbols AS symbol JOIN "
                    "source_structure_snapshots AS snapshot "
                    "ON snapshot.snapshot_id = symbol.snapshot_id "
                    "WHERE symbol.snapshot_id = ? AND snapshot.owner_id = ? "
                    "AND snapshot.workspace_id IS ? AND snapshot.project_id = ? "
                    "AND symbol.symbol_id IN ("
                    + placeholders
                    + ") ORDER BY symbol.relative_path ASC, symbol.line_number ASC, "
                    "symbol.qualified_name ASC, symbol.symbol_id ASC",
                    (
                        str(snapshot_id),
                        str(scope.owner_id),
                        _maybe(scope.workspace_id),
                        str(scope.project_id),
                        *values,
                    ),
                ).fetchall()
        except sqlite3.Error as error:
            raise SourceIndexStorageFailure("source index storage operation failed") from error
        return self._symbols_from_rows(snapshot_id, rows)

    def edges_from_symbols(
        self, scope: MemoryScope, snapshot_id: CodeSnapshotId, symbol_ids: tuple[CodeSymbolId, ...]
    ) -> tuple[CodeEdge, ...]:
        if not symbol_ids:
            return ()
        self._backend.get_source_snapshot(scope, snapshot_id)
        values = tuple(dict.fromkeys(str(symbol_id) for symbol_id in symbol_ids))
        placeholders = ", ".join("?" for _ in values)
        try:
            with self._backend._connect() as connection:
                rows = connection.execute(
                    "SELECT edge.* FROM source_structure_edges AS edge JOIN "
                    "source_structure_snapshots AS snapshot "
                    "ON snapshot.snapshot_id = edge.snapshot_id "
                    "WHERE edge.snapshot_id = ? AND snapshot.owner_id = ? "
                    "AND snapshot.workspace_id IS ? AND snapshot.project_id = ? "
                    "AND edge.source_symbol_id IN ("
                    + placeholders
                    + ") ORDER BY edge.source_symbol_id ASC, edge.target ASC, edge.edge_type ASC",
                    (
                        str(snapshot_id),
                        str(scope.owner_id),
                        _maybe(scope.workspace_id),
                        str(scope.project_id),
                        *values,
                    ),
                ).fetchall()
        except sqlite3.Error as error:
            raise SourceIndexStorageFailure("source index storage operation failed") from error
        return tuple(
            CodeEdge(
                snapshot_id,
                CodeSymbolId.from_string(row["source_symbol_id"]),
                row["target"],
                CodeEdgeKind(row["edge_type"]),
                CodeSymbolId.from_string(row["target_symbol_id"])
                if row["target_symbol_id"] is not None
                else None,
            )
            for row in rows
        )

    def edges_to_symbols(
        self, scope: MemoryScope, snapshot_id: CodeSnapshotId, symbol_ids: tuple[CodeSymbolId, ...]
    ) -> tuple[CodeEdge, ...]:
        """Fetch a bounded frontier of resolved internal reverse relationships."""
        if not symbol_ids:
            return ()
        self._backend.get_source_snapshot(scope, snapshot_id)
        values = tuple(dict.fromkeys(str(symbol_id) for symbol_id in symbol_ids))
        placeholders = ", ".join("?" for _ in values)
        try:
            with self._backend._connect() as connection:
                rows = connection.execute(
                    "SELECT edge.* FROM source_structure_edges AS edge JOIN "
                    "source_structure_snapshots AS snapshot "
                    "ON snapshot.snapshot_id = edge.snapshot_id "
                    "WHERE edge.snapshot_id = ? AND snapshot.owner_id = ? "
                    "AND snapshot.workspace_id IS ? AND snapshot.project_id = ? "
                    "AND edge.target_symbol_id IN ("
                    + placeholders
                    + ") ORDER BY edge.source_symbol_id ASC, edge.target ASC, edge.edge_type ASC",
                    (
                        str(snapshot_id),
                        str(scope.owner_id),
                        _maybe(scope.workspace_id),
                        str(scope.project_id),
                        *values,
                    ),
                ).fetchall()
        except sqlite3.Error as error:
            raise SourceIndexStorageFailure("source index storage operation failed") from error
        return tuple(
            CodeEdge(
                snapshot_id,
                CodeSymbolId.from_string(row["source_symbol_id"]),
                row["target"],
                CodeEdgeKind(row["edge_type"]),
                CodeSymbolId.from_string(row["target_symbol_id"])
                if row["target_symbol_id"] is not None
                else None,
            )
            for row in rows
        )

    @staticmethod
    def _symbols_from_rows(
        snapshot_id: CodeSnapshotId, rows: list[sqlite3.Row]
    ) -> tuple[CodeSymbol, ...]:
        return tuple(
            CodeSymbol(
                snapshot_id,
                CodeSymbolId.from_string(row["symbol_id"]),
                row["relative_path"],
                row["qualified_name"],
                CodeSymbolKind(row["kind"]),
                int(row["line_number"]),
            )
            for row in rows
        )
