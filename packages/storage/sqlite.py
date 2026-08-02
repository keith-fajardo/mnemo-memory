"""SQLite adapter for Mnemo's local, single-user personal profile."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path

from packages.domain import (
    Checkpoint,
    CheckpointContent,
    CheckpointId,
    EvidenceId,
    EvidenceReference,
    MemoryScope,
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
            connection.execute(
                "INSERT INTO checkpoint_aggregates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    root,
                    scope["owner_id"],
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
