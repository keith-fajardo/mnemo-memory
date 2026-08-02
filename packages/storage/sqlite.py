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
from pathlib import Path
from typing import cast

from packages.domain import (
    Checkpoint,
    CheckpointAggregate,
    CheckpointContent,
    CheckpointId,
    CheckpointRevision,
    CheckpointRevisionId,
    CheckpointStatus,
    EvidenceId,
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

from .contracts import (
    CheckpointNotFound,
    CheckpointPage,
    DuplicateCheckpoint,
    InvalidAbandonmentReason,
    InvalidCheckpointScope,
    InvalidLifecycleTransition,
    RepositoryStorageFailure,
    RevisionConflict,
)

LATEST_SCHEMA_VERSION = 2
BUSY_TIMEOUT_MS = 5000


class SQLiteMigrationError(RuntimeError):
    pass


class SQLiteSchemaTooNewError(SQLiteMigrationError):
    pass


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _root() -> Path:
    return Path(__file__).parents[2]


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
        migration_path = _root() / "migrations" / "0001_initial.sql"
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
                for statement in migration_path.read_text().split(";"):
                    if statement.strip():
                        connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (1, ?)",
                    (_timestamp(),),
                )
                if fail_after_version == 1:
                    raise SQLiteMigrationError("injected migration failure")
                version = 1
            if version < 2:
                migration_path = _root() / "migrations" / "0002_checkpoint_aggregate_revisions.sql"
                for statement in migration_path.read_text().split(";"):
                    if statement.strip():
                        connection.execute(statement)
                self._map_legacy_checkpoints(connection)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (2, ?)",
                    (_timestamp(),),
                )
                if fail_after_version == 2:
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

    def create_evidence(self, evidence: EvidenceReference) -> None:
        with self._transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO evidence(evidence_id, source_id, payload_json) VALUES (?, ?, ?)",  # noqa: E501
                (str(evidence.evidence_id), str(evidence.source_id), _json(evidence.to_dict())),
            )

    def get_evidence(self, evidence_id: EvidenceId) -> EvidenceReference | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM evidence WHERE evidence_id = ?", (str(evidence_id),)
            ).fetchone()
        return None if row is None else EvidenceReference.from_dict(json.loads(row["payload_json"]))

    def create_aggregate(
        self, aggregate: CheckpointAggregate, revision: CheckpointRevision
    ) -> None:
        """Compatibility alias for the canonical aggregate creation operation."""
        self.create_checkpoint_aggregate(aggregate, revision)

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

    def create_checkpoint(self, checkpoint: Checkpoint) -> None:
        with self._transaction() as connection:
            self._store_checkpoint(connection, checkpoint)

    def _store_checkpoint(self, connection: sqlite3.Connection, checkpoint: Checkpoint) -> None:
        self._store_scope(connection, checkpoint.scope)
        for evidence in checkpoint.evidence_references:
            connection.execute(
                "INSERT OR IGNORE INTO evidence(evidence_id, source_id, payload_json) VALUES (?, ?, ?)",  # noqa: E501
                (str(evidence.evidence_id), str(evidence.source_id), _json(evidence.to_dict())),
            )
        scope = checkpoint.scope
        connection.execute(
            "INSERT INTO checkpoints(checkpoint_id, owner_id, workspace_id, project_id, session_id, task_id, current_revision) VALUES (?, ?, ?, ?, ?, ?, ?)",  # noqa: E501
            (
                str(checkpoint.checkpoint_id),
                str(scope.owner_id),
                _maybe(scope.workspace_id),
                str(scope.project_id),
                str(scope.session_id),
                str(scope.task_id),
                checkpoint.revision,
            ),
        )
        connection.execute(
            "INSERT INTO checkpoint_revisions(checkpoint_id, revision, status, supersedes_checkpoint_id, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",  # noqa: E501
            (
                str(checkpoint.checkpoint_id),
                checkpoint.revision,
                checkpoint.status.value,
                _maybe(checkpoint.supersedes_checkpoint_id),
                _json(checkpoint.to_dict()),
                checkpoint.created_at.isoformat(),
            ),
        )
        connection.executemany(
            "INSERT INTO checkpoint_evidence(checkpoint_id, revision, evidence_id) VALUES (?, ?, ?)",  # noqa: E501
            [
                (str(checkpoint.checkpoint_id), checkpoint.revision, str(evidence.evidence_id))
                for evidence in checkpoint.evidence_references
            ],
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

    def get_checkpoint(self, checkpoint_id: CheckpointId, scope: MemoryScope) -> Checkpoint | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT revision.payload_json FROM checkpoint_revisions AS revision JOIN checkpoints AS checkpoint ON checkpoint.checkpoint_id = revision.checkpoint_id WHERE revision.checkpoint_id = ? AND checkpoint.owner_id = ? AND checkpoint.project_id = ? AND checkpoint.session_id = ? AND checkpoint.task_id = ?",  # noqa: E501
                (
                    str(checkpoint_id),
                    str(scope.owner_id),
                    str(scope.project_id),
                    str(scope.session_id),
                    str(scope.task_id),
                ),
            ).fetchone()
        return None if row is None else Checkpoint.from_dict(json.loads(row["payload_json"]))

    def get_current_checkpoint(self, scope: MemoryScope) -> Checkpoint | None:
        if scope.project_id is None or scope.session_id is None or scope.task_id is None:
            raise ValueError("current checkpoint requires explicit task scope")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT revision.payload_json FROM checkpoint_revisions AS revision JOIN checkpoints AS checkpoint ON checkpoint.checkpoint_id = revision.checkpoint_id WHERE checkpoint.owner_id = ? AND checkpoint.project_id = ? AND checkpoint.session_id = ? AND checkpoint.task_id = ? AND NOT EXISTS (SELECT 1 FROM checkpoint_revisions AS replacement WHERE replacement.supersedes_checkpoint_id = checkpoint.checkpoint_id) ORDER BY revision.created_at DESC LIMIT 1",  # noqa: E501
                (
                    str(scope.owner_id),
                    str(scope.project_id),
                    str(scope.session_id),
                    str(scope.task_id),
                ),
            ).fetchone()
        return None if row is None else Checkpoint.from_dict(json.loads(row["payload_json"]))

    def list_checkpoint_history(
        self, checkpoint_id: CheckpointId, scope: MemoryScope
    ) -> tuple[Checkpoint, ...]:
        if scope.project_id is None or scope.session_id is None or scope.task_id is None:
            raise ValueError("checkpoint history requires explicit task scope")
        with self._connect() as connection:
            rows = connection.execute(
                "WITH RECURSIVE history(checkpoint_id) AS (SELECT ? UNION ALL SELECT revision.checkpoint_id FROM checkpoint_revisions AS revision JOIN history ON revision.supersedes_checkpoint_id = history.checkpoint_id) SELECT revision.payload_json FROM history JOIN checkpoint_revisions AS revision ON revision.checkpoint_id = history.checkpoint_id JOIN checkpoints AS checkpoint ON checkpoint.checkpoint_id = revision.checkpoint_id WHERE checkpoint.owner_id = ? AND checkpoint.project_id = ? AND checkpoint.session_id = ? AND checkpoint.task_id = ? ORDER BY revision.created_at",  # noqa: E501
                (
                    str(checkpoint_id),
                    str(scope.owner_id),
                    str(scope.project_id),
                    str(scope.session_id),
                    str(scope.task_id),
                ),
            ).fetchall()
        return tuple(Checkpoint.from_dict(json.loads(row["payload_json"])) for row in rows)

    def supersede(self, checkpoint: Checkpoint, replacement: Checkpoint) -> None:
        if (
            replacement.revision != checkpoint.revision + 1
            or replacement.supersedes_checkpoint_id != checkpoint.checkpoint_id
        ):
            raise ValueError(
                "replacement must identify the immediately replaced checkpoint revision"
            )
        with self._transaction() as connection:
            self._store_checkpoint(connection, replacement)


def _json(value: dict[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _maybe(value: object | None) -> str | None:
    return None if value is None else str(value)
