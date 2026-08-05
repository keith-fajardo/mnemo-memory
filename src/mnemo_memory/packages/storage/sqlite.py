"""SQLite adapter for Mnemo's local, single-user personal profile."""

from __future__ import annotations

import json
import os
import sqlite3
import struct
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
    ApprovedEpisodicEventGovernance,
    ApprovedEventGovernanceKind,
    ApprovedEventKind,
    ApprovedEventLifecycleStatus,
    Checkpoint,
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
    CodeEdgeKind,
    CodeFile,
    CodeSnapshot,
    CodeSnapshotId,
    CodeStructureArtifact,
    CodeSymbol,
    CodeSymbolId,
    CodeSymbolKind,
    CurrentKnowledgeDocumentSection,
    DbtCatalogArtifact,
    DbtCatalogCollection,
    DbtCatalogColumn,
    DbtCatalogRelation,
    DbtFreshnessPeriod,
    DbtFreshnessStatus,
    DbtFreshnessThreshold,
    DbtNodeRunResult,
    DbtRunResultsArtifact,
    DbtRunStatus,
    DbtRunTiming,
    DbtSnapshotId,
    DbtSourceFreshnessArtifact,
    DbtSourceFreshnessResult,
    DbtSupplementalArtifactMetadata,
    EventId,
    EventOutboxJob,
    EventOutboxTopic,
    EvidenceReference,
    KnowledgeDocument,
    KnowledgeDocumentId,
    KnowledgeDocumentLink,
    KnowledgeDocumentRevision,
    KnowledgeDocumentRevisionId,
    KnowledgeDocumentSection,
    KnowledgeDocumentSectionMatch,
    KnowledgeDocumentSourceKind,
    KnowledgeDocumentTombstone,
    KnowledgeSectionEmbedding,
    KnownKnowledgeDocument,
    MemoryScope,
    OutboxJobId,
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
from mnemo_memory.packages.policy import (
    ApprovedEpisodicEventSafetyPolicy,
    KnowledgeDocumentSafetyPolicy,
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
    ApprovedEpisodicEventStorageFailure,
    ApprovedEpisodicEventStoreResult,
    CheckpointNotFound,
    CheckpointPage,
    CheckpointSourceObservationConflict,
    CheckpointSourceObservationNotFound,
    CheckpointSourceObservationStorageFailure,
    CheckpointSourceObservationStoreResult,
    DuplicateCheckpoint,
    EpisodicEventNotFound,
    EpisodicEventPage,
    EpisodicEventStorageFailure,
    EpisodicEventStoreResult,
    EventOutboxLeaseConflict,
    EventOutboxNotFound,
    EventOutboxStorageFailure,
    InvalidAbandonmentReason,
    InvalidApprovedEpisodicEventScope,
    InvalidCheckpointScope,
    InvalidEpisodicEventScope,
    InvalidKnowledgeDocumentScope,
    InvalidLifecycleTransition,
    InvalidManifestSnapshotScope,
    KnowledgeDocumentConflict,
    KnowledgeDocumentNotFound,
    KnowledgeDocumentSecretRejected,
    KnowledgeDocumentStorageFailure,
    KnowledgeDocumentSyncStoreResult,
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
    SupplementalArtifactConflict,
    SupplementalArtifactStoreResult,
    rank_knowledge_sections,
    validate_knowledge_search,
)
from .source_search import source_search_terms, source_symbol_matches, source_symbol_rank

LATEST_SCHEMA_VERSION = 18
BUSY_TIMEOUT_MS = 5000


class SQLiteMigrationError(RuntimeError):
    pass


class SQLiteSchemaTooNewError(SQLiteMigrationError):
    pass


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _require_aware_datetime(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _outbox_timestamp(value: datetime) -> str:
    """Canonicalize outbox times so SQLite's indexed text comparisons are chronological."""
    _require_aware_datetime(value, "event outbox timestamp")
    return value.astimezone(UTC).isoformat()


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
                version = 7
            if version < 8:
                _execute_sql_script(
                    connection, _migration_text("0008_source_file_fingerprints.sql")
                )
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (8, ?)",
                    (_timestamp(),),
                )
                if fail_after_version == 8:
                    raise SQLiteMigrationError("injected migration failure")
                version = 8
            if version < 9:
                # Earlier local builds applied the unreleased script but did not record version 9.
                # Detect that exact recoverable state instead of replaying CREATE TABLE statements.
                observation_table = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                    "AND name = 'checkpoint_source_observations'"
                ).fetchone()
                if observation_table is None:
                    _execute_sql_script(
                        connection, _migration_text("0009_checkpoint_source_observations.sql")
                    )
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (9, ?)",
                    (_timestamp(),),
                )
                if fail_after_version == 9:
                    raise SQLiteMigrationError("injected migration failure")
                version = 9
            if version < 10:
                # Permit recovery from an interrupted unreleased local migration where the
                # schema objects were created but the migration ledger was not recorded.
                knowledge_table = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                    "AND name = 'knowledge_document_sources'"
                ).fetchone()
                if knowledge_table is None:
                    _execute_sql_script(connection, _migration_text("0010_knowledge_documents.sql"))
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (10, ?)",
                    (_timestamp(),),
                )
                if fail_after_version == 10:
                    raise SQLiteMigrationError("injected migration failure")
                version = 10
            if version < 11:
                # Permit recovery from an interrupted local migration where the rebuildable FTS
                # projection exists but the migration ledger was not recorded.
                knowledge_fts_table = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                    "AND name = 'knowledge_document_section_fts'"
                ).fetchone()
                if knowledge_fts_table is None:
                    _execute_sql_script(
                        connection, _migration_text("0011_knowledge_section_fts.sql")
                    )
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (11, ?)",
                    (_timestamp(),),
                )
                if fail_after_version == 11:
                    raise SQLiteMigrationError("injected migration failure")
                version = 11
            if version < 12:
                # The semantic rows are rebuildable.  As with FTS, an interrupted unreleased
                # local migration may have created the table before its ledger entry.
                embeddings_table = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                    "AND name = 'knowledge_section_embeddings'"
                ).fetchone()
                if embeddings_table is None:
                    _execute_sql_script(
                        connection, _migration_text("0012_knowledge_section_embeddings.sql")
                    )
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (12, ?)",
                    (_timestamp(),),
                )
                if fail_after_version == 12:
                    raise SQLiteMigrationError("injected migration failure")
                version = 12
            if version < 13:
                _execute_sql_script(
                    connection,
                    _migration_text("0013_approved_episodic_event_governance.sql"),
                )
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (13, ?)",
                    (_timestamp(),),
                )
                if fail_after_version == 13:
                    raise SQLiteMigrationError("injected migration failure")
                version = 13
            if version < 14:
                _execute_sql_script(
                    connection,
                    _migration_text("0014_dbt_supplemental_artifacts.sql"),
                )
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (14, ?)",
                    (_timestamp(),),
                )
                if fail_after_version == 14:
                    raise SQLiteMigrationError("injected migration failure")
                version = 14
            if version < 15:
                _execute_sql_script(
                    connection,
                    _migration_text("0015_dbt_macro_dependency_edges.sql"),
                )
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (15, ?)",
                    (_timestamp(),),
                )
                if fail_after_version == 15:
                    raise SQLiteMigrationError("injected migration failure")
                version = 15
            if version < 16:
                _execute_sql_script(
                    connection,
                    _migration_text("0016_dbt_source_freshness.sql"),
                )
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (16, ?)",
                    (_timestamp(),),
                )
                if fail_after_version == 16:
                    raise SQLiteMigrationError("injected migration failure")
                version = 16
            if version < 17:
                # Permit recovery from an interrupted unreleased local migration where the
                # additive activation ledger exists but its migration entry was not recorded.
                activation_table = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                    "AND name = 'dbt_manifest_activations'"
                ).fetchone()
                if activation_table is None:
                    _execute_sql_script(
                        connection,
                        _migration_text("0017_dbt_manifest_activations.sql"),
                    )
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (17, ?)",
                    (_timestamp(),),
                )
                if fail_after_version == 17:
                    raise SQLiteMigrationError("injected migration failure")
                version = 17
            if version < 18:
                # Recover an interrupted unreleased additive migration whose table exists but
                # whose ledger entry was not committed.
                outbox_table = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'event_outbox'"
                ).fetchone()
                if outbox_table is None:
                    _execute_sql_script(connection, _migration_text("0018_event_outbox.sql"))
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (18, ?)",
                    (_timestamp(),),
                )
                if fail_after_version == 18:
                    raise SQLiteMigrationError("injected migration failure")
                version = 18

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

    def append_checkpoint_source_observation(
        self, observation: CheckpointSourceObservation
    ) -> CheckpointSourceObservationStoreResult:
        """Durably link one exact revision to a scoped source snapshot without inferring cause."""
        self._require_checkpoint_scope(observation.scope)
        scope = observation.scope
        try:
            with self._transaction() as connection:
                revision = connection.execute(
                    "SELECT revision.checkpoint_revision_id "
                    "FROM checkpoint_revision_records AS revision "
                    "JOIN checkpoint_aggregates AS aggregate "
                    "ON aggregate.checkpoint_id = revision.checkpoint_id "
                    "WHERE revision.checkpoint_revision_id = ? AND revision.checkpoint_id = ? "
                    "AND aggregate.owner_id = ? AND aggregate.visibility = ? "
                    "AND aggregate.workspace_id IS ? AND aggregate.project_id = ? "
                    "AND aggregate.session_id = ? AND aggregate.task_id = ?",
                    (
                        str(observation.revision_id),
                        str(observation.checkpoint_id),
                        *self._scope_values(scope),
                    ),
                ).fetchone()
                if revision is None:
                    raise CheckpointSourceObservationNotFound("checkpoint revision was not found")
                snapshot = connection.execute(
                    "SELECT snapshot_id FROM source_structure_snapshots WHERE snapshot_id = ? "
                    "AND owner_id = ? AND workspace_id IS ? AND project_id = ?",
                    (
                        str(observation.source_snapshot_id),
                        str(scope.owner_id),
                        _maybe(scope.workspace_id),
                        str(scope.project_id),
                    ),
                ).fetchone()
                if snapshot is None:
                    raise CheckpointSourceObservationNotFound("source snapshot was not found")
                existing = connection.execute(
                    "SELECT * FROM checkpoint_source_observations WHERE checkpoint_revision_id = ?",
                    (str(observation.revision_id),),
                ).fetchone()
                if existing is not None:
                    stored = self._checkpoint_source_observation_from_row(existing, scope)
                    if stored == observation:
                        return CheckpointSourceObservationStoreResult(stored, idempotent=True)
                    raise CheckpointSourceObservationConflict(
                        "checkpoint revision already has a source observation"
                    )
                connection.execute(
                    "INSERT INTO checkpoint_source_observations("
                    "checkpoint_revision_id,checkpoint_id,source_snapshot_id,owner_id,visibility,"
                    "workspace_id,project_id,session_id,task_id,observed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(observation.revision_id),
                        str(observation.checkpoint_id),
                        str(observation.source_snapshot_id),
                        *self._scope_values(scope),
                        observation.observed_at.isoformat(),
                    ),
                )
                return CheckpointSourceObservationStoreResult(observation, idempotent=False)
        except (
            CheckpointSourceObservationNotFound,
            CheckpointSourceObservationConflict,
            ValueError,
            TypeError,
        ):
            raise
        except sqlite3.IntegrityError as error:
            raise CheckpointSourceObservationStorageFailure(
                "checkpoint source observation storage failed"
            ) from error
        except sqlite3.Error as error:
            raise CheckpointSourceObservationStorageFailure(
                "checkpoint source observation storage failed"
            ) from error

    def get_checkpoint_source_observation(
        self,
        scope: MemoryScope,
        checkpoint_id: CheckpointId,
        revision_id: CheckpointRevisionId,
    ) -> CheckpointSourceObservation:
        self._require_checkpoint_scope(scope)
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM checkpoint_source_observations WHERE checkpoint_revision_id = ? "
                    "AND checkpoint_id = ? AND owner_id = ? AND visibility = ? "
                    "AND workspace_id IS ? AND project_id = ? AND session_id = ? AND task_id = ?",
                    (str(revision_id), str(checkpoint_id), *self._scope_values(scope)),
                ).fetchone()
        except sqlite3.Error as error:
            raise CheckpointSourceObservationStorageFailure(
                "checkpoint source observation storage failed"
            ) from error
        if row is None:
            raise CheckpointSourceObservationNotFound("checkpoint source observation was not found")
        return self._checkpoint_source_observation_from_row(row, scope)

    def claim_event_jobs(
        self,
        scope: MemoryScope,
        *,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
        limit: int,
    ) -> tuple[EventOutboxJob, ...]:
        self._require_checkpoint_scope(scope)
        EventOutboxJob.validate_worker_id(worker_id)
        _require_aware_datetime(now, "now")
        _require_aware_datetime(lease_expires_at, "lease_expires_at")
        if not 1 <= limit <= 100:
            raise ValueError("event outbox claim limit must be between 1 and 100")
        if lease_expires_at <= now:
            raise ValueError("event outbox lease must expire after claim time")
        try:
            with self._transaction() as connection:
                rows = connection.execute(
                    "SELECT * FROM event_outbox WHERE owner_id = ? AND visibility = ? "
                    "AND workspace_id IS ? AND project_id = ? AND session_id = ? AND task_id = ? "
                    "AND completed_at IS NULL AND available_at <= ? "
                    "AND (lease_expires_at IS NULL OR lease_expires_at <= ?) "
                    "ORDER BY created_at ASC, job_id ASC LIMIT ?",
                    (
                        *self._scope_values(scope),
                        _outbox_timestamp(now),
                        _outbox_timestamp(now),
                        limit,
                    ),
                ).fetchall()
                claimed: list[EventOutboxJob] = []
                for row in rows:
                    job = self._event_outbox_from_row(row).claim(worker_id, lease_expires_at)
                    connection.execute(
                        "UPDATE event_outbox SET attempt_count = ?, lease_owner = ?, "
                        "lease_expires_at = ? WHERE job_id = ?",
                        (
                            job.attempt_count,
                            job.lease_owner,
                            _outbox_timestamp(job.lease_expires_at)
                            if job.lease_expires_at is not None
                            else None,
                            str(job.job_id),
                        ),
                    )
                    claimed.append(job)
                return tuple(claimed)
        except (ValueError, TypeError):
            raise
        except sqlite3.Error as error:
            raise EventOutboxStorageFailure("event outbox claim failed") from error

    def complete_event_job(
        self,
        scope: MemoryScope,
        job_id: OutboxJobId,
        *,
        worker_id: str,
        completed_at: datetime,
    ) -> EventOutboxJob:
        self._require_checkpoint_scope(scope)
        EventOutboxJob.validate_worker_id(worker_id)
        _require_aware_datetime(completed_at, "completed_at")
        try:
            with self._transaction() as connection:
                job = self._scoped_event_outbox_job(connection, scope, job_id)
                self._require_event_outbox_lease(job, worker_id, completed_at)
                completed = job.complete(completed_at)
                connection.execute(
                    "UPDATE event_outbox SET completed_at = ?, lease_owner = NULL, "
                    "lease_expires_at = NULL, last_failure_code = NULL WHERE job_id = ?",
                    (_outbox_timestamp(completed_at), str(job_id)),
                )
                return completed
        except (EventOutboxNotFound, EventOutboxLeaseConflict, ValueError, TypeError):
            raise
        except sqlite3.Error as error:
            raise EventOutboxStorageFailure("event outbox completion failed") from error

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
        self._require_checkpoint_scope(scope)
        EventOutboxJob.validate_worker_id(worker_id)
        EventOutboxJob.validate_failure_code(failure_code)
        _require_aware_datetime(now, "now")
        _require_aware_datetime(available_at, "available_at")
        if available_at < now:
            raise ValueError("event outbox retry cannot be scheduled in the past")
        try:
            with self._transaction() as connection:
                job = self._scoped_event_outbox_job(connection, scope, job_id)
                self._require_event_outbox_lease(job, worker_id, now)
                retried = job.retry(available_at, failure_code)
                connection.execute(
                    "UPDATE event_outbox SET available_at = ?, lease_owner = NULL, "
                    "lease_expires_at = NULL, last_failure_code = ? WHERE job_id = ?",
                    (_outbox_timestamp(available_at), failure_code, str(job_id)),
                )
                return retried
        except (EventOutboxNotFound, EventOutboxLeaseConflict, ValueError, TypeError):
            raise
        except sqlite3.Error as error:
            raise EventOutboxStorageFailure("event outbox retry failed") from error

    def get_event_job(self, scope: MemoryScope, job_id: OutboxJobId) -> EventOutboxJob:
        self._require_checkpoint_scope(scope)
        try:
            with self._connect() as connection:
                return self._scoped_event_outbox_job(connection, scope, job_id)
        except EventOutboxNotFound:
            raise
        except (sqlite3.Error, ValueError, TypeError) as error:
            raise EventOutboxStorageFailure("event outbox read failed") from error

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
                self._insert_lifecycle_event(connection, event)
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
        if not ApprovedEpisodicEventSafetyPolicy().assess_event(event).accepted:
            raise ApprovedEpisodicEventSecretRejected(
                "approved episodic event was rejected by deterministic secret policy"
            )
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
                governed = connection.execute(
                    "SELECT 1 FROM approved_episodic_event_governance WHERE target_event_id = ?",
                    (str(event.event_id),),
                ).fetchone()
                if governed is not None:
                    raise ApprovedEpisodicEventConflict(
                        "retracted approved event cannot be restored"
                    )
                self._insert_approved_event(connection, event)
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
                    "AND NOT EXISTS (SELECT 1 FROM approved_episodic_event_governance AS action "
                    "WHERE action.target_event_id = approved_episodic_events.event_id) "
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

    def correct_approved_event(
        self,
        replacement: ApprovedEpisodicEvent,
        governance: ApprovedEpisodicEventGovernance,
    ) -> ApprovedEpisodicEventGovernanceResult:
        self._validate_approved_governance(replacement.scope, governance)
        policy = ApprovedEpisodicEventSafetyPolicy()
        if (
            not policy.assess_event(replacement).accepted
            or not policy.assess_governance(governance).accepted
        ):
            raise ApprovedEpisodicEventSecretRejected(
                "approved episodic event correction was rejected by secret policy"
            )
        if (
            governance.kind is not ApprovedEventGovernanceKind.CORRECTED
            or replacement.event_id != governance.replacement_event_id
        ):
            raise ApprovedEpisodicEventConflict("approved event correction action is invalid")
        try:
            with self._transaction() as connection:
                self._store_scope(connection, governance.scope)
                existing = self._scoped_approved_governance_row(
                    connection, governance.scope, governance.target_event_id
                )
                if existing is not None:
                    stored_action = self._approved_governance_from_row(
                        connection, existing, governance.scope
                    )
                    if not stored_action.same_intent(governance):
                        raise ApprovedEpisodicEventConflict(
                            "approved event already has a governance action"
                        )
                    replacement_record = self._approved_event_record(
                        connection, governance.scope, replacement.event_id
                    )
                    if (
                        replacement_record.event is not None
                        and not self._same_approved_event_intent(
                            replacement_record.event, replacement
                        )
                    ):
                        raise ApprovedEpisodicEventConflict("approved event replacement conflicts")
                    return ApprovedEpisodicEventGovernanceResult(
                        self._approved_event_record(
                            connection, governance.scope, governance.target_event_id
                        ),
                        replacement_record,
                        True,
                    )
                target_row = self._scoped_approved_event_row(
                    connection, governance.scope, governance.target_event_id
                )
                if target_row is None:
                    raise ApprovedEpisodicEventNotFound("approved episodic event was not found")
                target = self._approved_event_from_row(connection, target_row, governance.scope)
                if replacement.kind is not target.kind:
                    raise ApprovedEpisodicEventConflict(
                        "approved event correction cannot change kind"
                    )
                self._require_available_approved_replacement(connection, replacement, governance)
                self._insert_approved_event(connection, replacement)
                self._insert_approved_governance(
                    connection, governance, int(target_row["event_sequence"])
                )
                return ApprovedEpisodicEventGovernanceResult(
                    self._approved_event_record(
                        connection, governance.scope, governance.target_event_id
                    ),
                    self._approved_event_record(connection, governance.scope, replacement.event_id),
                    False,
                )
        except (ApprovedEpisodicEventConflict, ApprovedEpisodicEventNotFound):
            raise
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise ApprovedEpisodicEventStorageFailure(
                "approved episodic event governance operation failed"
            ) from error

    def retract_approved_event(
        self, governance: ApprovedEpisodicEventGovernance
    ) -> ApprovedEpisodicEventGovernanceResult:
        self._validate_approved_governance(governance.scope, governance)
        if not ApprovedEpisodicEventSafetyPolicy().assess_governance(governance).accepted:
            raise ApprovedEpisodicEventSecretRejected(
                "approved episodic event retraction was rejected by secret policy"
            )
        if governance.kind is not ApprovedEventGovernanceKind.RETRACTED:
            raise ApprovedEpisodicEventConflict("approved event retraction action is invalid")
        try:
            with self._transaction() as connection:
                self._store_scope(connection, governance.scope)
                existing = self._scoped_approved_governance_row(
                    connection, governance.scope, governance.target_event_id
                )
                if existing is not None:
                    stored_action = self._approved_governance_from_row(
                        connection, existing, governance.scope
                    )
                    if stored_action.same_intent(governance):
                        return ApprovedEpisodicEventGovernanceResult(
                            self._approved_event_record(
                                connection, governance.scope, governance.target_event_id
                            ),
                            None,
                            True,
                        )
                    raise ApprovedEpisodicEventConflict(
                        "approved event already has a governance action"
                    )
                target_row = self._scoped_approved_event_row(
                    connection, governance.scope, governance.target_event_id
                )
                if target_row is None:
                    raise ApprovedEpisodicEventNotFound("approved episodic event was not found")
                self._require_available_approved_action_key(connection, governance)
                self._insert_approved_governance(
                    connection, governance, int(target_row["event_sequence"])
                )
                connection.execute(
                    "DELETE FROM approved_episodic_event_evidence WHERE event_id = ?",
                    (str(governance.target_event_id),),
                )
                connection.execute(
                    "DELETE FROM approved_episodic_events WHERE event_id = ?",
                    (str(governance.target_event_id),),
                )
                return ApprovedEpisodicEventGovernanceResult(
                    self._approved_event_record(
                        connection, governance.scope, governance.target_event_id
                    ),
                    None,
                    False,
                )
        except (ApprovedEpisodicEventConflict, ApprovedEpisodicEventNotFound):
            raise
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise ApprovedEpisodicEventStorageFailure(
                "approved episodic event governance operation failed"
            ) from error

    def get_approved_event_record(
        self, scope: MemoryScope, event_id: EventId
    ) -> ApprovedEpisodicEventRecord:
        self._require_approved_episodic_scope(scope)
        try:
            with self._connect() as connection:
                return self._approved_event_record(connection, scope, event_id)
        except ApprovedEpisodicEventNotFound:
            raise
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise ApprovedEpisodicEventStorageFailure(
                "approved episodic event governance operation failed"
            ) from error

    def list_approved_event_records(
        self, scope: MemoryScope, *, offset: int = 0, limit: int = 50
    ) -> ApprovedEpisodicEventRecordPage:
        self._require_approved_episodic_scope(scope)
        if offset < 0 or limit < 1:
            raise ValueError("event offset must be non-negative and limit must be positive")
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT event_sequence AS record_sequence, event_id FROM "
                    "approved_episodic_events WHERE owner_id = ? AND visibility = ? "
                    "AND workspace_id IS ? AND project_id = ? AND session_id = ? AND task_id = ? "
                    "UNION ALL "
                    "SELECT target_event_sequence AS record_sequence, target_event_id AS event_id "
                    "FROM approved_episodic_event_governance WHERE action_kind = 'retracted' "
                    "AND owner_id = ? AND visibility = ? AND workspace_id IS ? AND project_id = ? "
                    "AND session_id = ? AND task_id = ? "
                    "ORDER BY record_sequence DESC LIMIT ? OFFSET ?",
                    (
                        *self._scope_values(scope),
                        *self._scope_values(scope),
                        limit + 1,
                        offset,
                    ),
                ).fetchall()
                items = tuple(
                    self._approved_event_record(
                        connection, scope, EventId.from_string(row["event_id"])
                    )
                    for row in rows[:limit]
                )
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise ApprovedEpisodicEventStorageFailure(
                "approved episodic event governance operation failed"
            ) from error
        return ApprovedEpisodicEventRecordPage(items, offset + limit if len(rows) > limit else None)

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
                        connection.execute(
                            "INSERT INTO dbt_manifest_activations("
                            "snapshot_id, owner_id, workspace_id, project_id, activated_at) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (
                                str(existing_id),
                                str(artifact.scope.owner_id),
                                _maybe(artifact.scope.workspace_id),
                                str(artifact.scope.project_id),
                                _timestamp(),
                            ),
                        )
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
                connection.execute(
                    "INSERT INTO dbt_manifest_activations("
                    "snapshot_id, owner_id, workspace_id, project_id, activated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        str(snapshot_id),
                        str(artifact.scope.owner_id),
                        _maybe(artifact.scope.workspace_id),
                        str(artifact.scope.project_id),
                        _timestamp(),
                    ),
                )
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

    def latest_transition(
        self, scope: MemoryScope
    ) -> tuple[DbtManifestSnapshot, DbtManifestSnapshot] | None:
        self._require_project_scope(scope)
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT snapshot.* FROM dbt_manifest_activations AS activation "
                    "JOIN dbt_manifest_snapshots AS snapshot "
                    "ON snapshot.snapshot_id = activation.snapshot_id "
                    "WHERE activation.owner_id = ? AND activation.workspace_id IS ? "
                    "AND activation.project_id = ? ORDER BY activation.activation_id DESC LIMIT 2",
                    (
                        str(scope.owner_id),
                        _maybe(scope.workspace_id),
                        str(scope.project_id),
                    ),
                ).fetchall()
        except sqlite3.Error as error:
            raise ProjectIndexStorageFailure("project index storage operation failed") from error
        if len(rows) < 2:
            return None
        return self._snapshot_from_row(rows[1], scope), self._snapshot_from_row(rows[0], scope)

    def store_catalog_projection(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, artifact: DbtCatalogArtifact
    ) -> SupplementalArtifactStoreResult:
        self._validate_supplemental_scope(scope, artifact.scope)
        try:
            with self._transaction() as connection:
                self._require_supplemental_manifest(
                    connection,
                    scope,
                    snapshot_id,
                    tuple(item.unique_id for item in artifact.relations),
                )
                idempotent = self._store_supplemental_header(
                    connection,
                    snapshot_id,
                    "catalog",
                    artifact.metadata,
                    catalog_error_count=artifact.error_count,
                    elapsed_time_seconds=None,
                    command_name=None,
                )
                if not idempotent:
                    connection.executemany(
                        "INSERT INTO dbt_catalog_relations VALUES "
                        "(?, 'catalog', ?, ?, ?, ?, ?, ?, ?, ?)",
                        [
                            (
                                str(snapshot_id),
                                artifact.metadata.content_digest,
                                str(item.unique_id),
                                item.collection.value,
                                item.relation_type,
                                item.database,
                                item.schema_name,
                                item.name,
                                _json(item.evidence.to_dict()),
                            )
                            for item in artifact.relations
                        ],
                    )
                    connection.executemany(
                        "INSERT INTO dbt_catalog_columns VALUES (?, ?, ?, ?, ?, ?)",
                        [
                            (
                                str(snapshot_id),
                                artifact.metadata.content_digest,
                                str(item.unique_id),
                                column.index,
                                column.name,
                                column.data_type,
                            )
                            for item in artifact.relations
                            for column in item.columns
                        ],
                    )
                self._activate_supplemental(
                    connection, snapshot_id, "catalog", artifact.metadata.content_digest
                )
                return SupplementalArtifactStoreResult(
                    snapshot_id, artifact.metadata.content_digest, idempotent
                )
        except (ManifestSnapshotNotFound, SupplementalArtifactConflict):
            raise
        except (TypeError, ValueError):
            raise
        except sqlite3.Error as error:
            raise ProjectIndexStorageFailure(
                "supplemental dbt catalog storage operation failed"
            ) from error

    def store_run_results_projection(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, artifact: DbtRunResultsArtifact
    ) -> SupplementalArtifactStoreResult:
        self._validate_supplemental_scope(scope, artifact.scope)
        try:
            with self._transaction() as connection:
                self._require_supplemental_manifest(
                    connection,
                    scope,
                    snapshot_id,
                    tuple(item.unique_id for item in artifact.results),
                )
                idempotent = self._store_supplemental_header(
                    connection,
                    snapshot_id,
                    "run_results",
                    artifact.metadata,
                    catalog_error_count=None,
                    elapsed_time_seconds=artifact.elapsed_time_seconds,
                    command_name=artifact.command,
                )
                if not idempotent:
                    connection.executemany(
                        "INSERT INTO dbt_run_results VALUES (?, 'run_results', ?, ?, ?, ?, ?, ?)",
                        [
                            (
                                str(snapshot_id),
                                artifact.metadata.content_digest,
                                str(item.unique_id),
                                item.status.value,
                                item.execution_time_seconds,
                                item.failures,
                                _json(item.evidence.to_dict()),
                            )
                            for item in artifact.results
                        ],
                    )
                    connection.executemany(
                        "INSERT INTO dbt_run_result_timings VALUES (?, ?, ?, ?, ?, ?)",
                        [
                            (
                                str(snapshot_id),
                                artifact.metadata.content_digest,
                                str(item.unique_id),
                                timing.name,
                                None
                                if timing.started_at is None
                                else timing.started_at.isoformat(),
                                None
                                if timing.completed_at is None
                                else timing.completed_at.isoformat(),
                            )
                            for item in artifact.results
                            for timing in item.timing
                        ],
                    )
                self._activate_supplemental(
                    connection, snapshot_id, "run_results", artifact.metadata.content_digest
                )
                return SupplementalArtifactStoreResult(
                    snapshot_id, artifact.metadata.content_digest, idempotent
                )
        except (ManifestSnapshotNotFound, SupplementalArtifactConflict):
            raise
        except (TypeError, ValueError):
            raise
        except sqlite3.Error as error:
            raise ProjectIndexStorageFailure(
                "supplemental dbt run-results storage operation failed"
            ) from error

    def store_source_freshness_projection(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, artifact: DbtSourceFreshnessArtifact
    ) -> SupplementalArtifactStoreResult:
        self._validate_supplemental_scope(scope, artifact.scope)
        try:
            with self._transaction() as connection:
                self._require_supplemental_manifest(
                    connection,
                    scope,
                    snapshot_id,
                    tuple(item.unique_id for item in artifact.results),
                )
                existing = connection.execute(
                    "SELECT * FROM dbt_source_freshness_artifacts "
                    "WHERE manifest_snapshot_id = ? AND content_digest = ?",
                    (str(snapshot_id), artifact.metadata.content_digest),
                ).fetchone()
                idempotent = existing is not None
                if existing is not None:
                    if (
                        existing["normalized_digest"] != artifact.metadata.normalized_digest
                        or existing["source_identity"] != artifact.metadata.source_identity
                        or existing["schema_version"] != artifact.metadata.schema_version
                    ):
                        raise SupplementalArtifactConflict(
                            "source-freshness digest conflicts with retained metadata"
                        )
                else:
                    connection.execute(
                        "INSERT INTO dbt_source_freshness_artifacts VALUES "
                        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                        (
                            str(snapshot_id),
                            artifact.metadata.content_digest,
                            artifact.metadata.schema_version,
                            artifact.metadata.dbt_version,
                            None
                            if artifact.metadata.generated_at is None
                            else artifact.metadata.generated_at.isoformat(),
                            artifact.metadata.invocation_id,
                            artifact.metadata.normalized_digest,
                            artifact.metadata.source_identity,
                            artifact.metadata.ingested_at.isoformat(),
                            artifact.elapsed_time_seconds,
                        ),
                    )
                    connection.executemany(
                        "INSERT INTO dbt_source_freshness_results VALUES "
                        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [
                            (
                                str(snapshot_id),
                                artifact.metadata.content_digest,
                                str(item.unique_id),
                                item.status.value,
                                None
                                if item.max_loaded_at is None
                                else item.max_loaded_at.isoformat(),
                                None
                                if item.snapshotted_at is None
                                else item.snapshotted_at.isoformat(),
                                item.age_seconds,
                                None if item.warn_after is None else item.warn_after.count,
                                None if item.warn_after is None else item.warn_after.period.value,
                                None if item.error_after is None else item.error_after.count,
                                None if item.error_after is None else item.error_after.period.value,
                                item.execution_time_seconds,
                                _json(item.evidence.to_dict()),
                            )
                            for item in artifact.results
                        ],
                    )
                connection.execute(
                    "UPDATE dbt_source_freshness_artifacts SET is_active = 0 "
                    "WHERE manifest_snapshot_id = ? AND is_active = 1",
                    (str(snapshot_id),),
                )
                updated = connection.execute(
                    "UPDATE dbt_source_freshness_artifacts SET is_active = 1 "
                    "WHERE manifest_snapshot_id = ? AND content_digest = ?",
                    (str(snapshot_id), artifact.metadata.content_digest),
                )
                if updated.rowcount != 1:
                    raise SupplementalArtifactConflict(
                        "source-freshness artifact could not be activated"
                    )
                return SupplementalArtifactStoreResult(
                    snapshot_id, artifact.metadata.content_digest, idempotent
                )
        except (ManifestSnapshotNotFound, SupplementalArtifactConflict):
            raise
        except (TypeError, ValueError):
            raise
        except sqlite3.Error as error:
            raise ProjectIndexStorageFailure(
                "supplemental dbt source-freshness storage operation failed"
            ) from error

    def get_catalog_projection(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId
    ) -> DbtCatalogArtifact | None:
        self._require_project_scope(scope)
        try:
            with self._connect() as connection:
                self._require_supplemental_manifest(connection, scope, snapshot_id, ())
                header = self._active_supplemental_row(connection, snapshot_id, "catalog")
                if header is None:
                    return None
                relations = connection.execute(
                    "SELECT * FROM dbt_catalog_relations WHERE manifest_snapshot_id = ? "
                    "AND content_digest = ? ORDER BY unique_id ASC",
                    (str(snapshot_id), header["content_digest"]),
                ).fetchall()
                columns = connection.execute(
                    "SELECT * FROM dbt_catalog_columns WHERE manifest_snapshot_id = ? "
                    "AND content_digest = ? ORDER BY unique_id ASC, column_index ASC",
                    (str(snapshot_id), header["content_digest"]),
                ).fetchall()
                by_relation: dict[str, list[DbtCatalogColumn]] = {}
                for row in columns:
                    by_relation.setdefault(row["unique_id"], []).append(
                        DbtCatalogColumn(
                            int(row["column_index"]), row["column_name"], row["data_type"]
                        )
                    )
                return DbtCatalogArtifact(
                    self._supplemental_metadata_from_row(header),
                    scope,
                    tuple(
                        DbtCatalogRelation(
                            DbtNodeId(row["unique_id"]),
                            DbtCatalogCollection(row["collection_kind"]),
                            row["relation_type"],
                            row["database_name"],
                            row["schema_name"],
                            row["relation_name"],
                            tuple(by_relation.get(row["unique_id"], ())),
                            EvidenceReference.from_dict(json.loads(row["evidence_json"])),
                        )
                        for row in relations
                    ),
                    int(header["catalog_error_count"]),
                )
        except ManifestSnapshotNotFound:
            raise
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise ProjectIndexStorageFailure("supplemental dbt catalog retrieval failed") from error

    def get_run_results_projection(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId
    ) -> DbtRunResultsArtifact | None:
        self._require_project_scope(scope)
        try:
            with self._connect() as connection:
                self._require_supplemental_manifest(connection, scope, snapshot_id, ())
                header = self._active_supplemental_row(connection, snapshot_id, "run_results")
                if header is None:
                    return None
                results = connection.execute(
                    "SELECT * FROM dbt_run_results WHERE manifest_snapshot_id = ? "
                    "AND content_digest = ? ORDER BY unique_id ASC",
                    (str(snapshot_id), header["content_digest"]),
                ).fetchall()
                timings = connection.execute(
                    "SELECT * FROM dbt_run_result_timings WHERE manifest_snapshot_id = ? "
                    "AND content_digest = ? ORDER BY unique_id ASC, timing_name ASC",
                    (str(snapshot_id), header["content_digest"]),
                ).fetchall()
                by_result: dict[str, list[DbtRunTiming]] = {}
                for row in timings:
                    by_result.setdefault(row["unique_id"], []).append(
                        DbtRunTiming(
                            row["timing_name"],
                            None
                            if row["started_at"] is None
                            else datetime.fromisoformat(row["started_at"]),
                            None
                            if row["completed_at"] is None
                            else datetime.fromisoformat(row["completed_at"]),
                        )
                    )
                return DbtRunResultsArtifact(
                    self._supplemental_metadata_from_row(header),
                    scope,
                    float(header["elapsed_time_seconds"]),
                    header["command_name"],
                    tuple(
                        DbtNodeRunResult(
                            DbtNodeId(row["unique_id"]),
                            DbtRunStatus(row["status"]),
                            float(row["execution_time_seconds"]),
                            None if row["failures"] is None else int(row["failures"]),
                            tuple(by_result.get(row["unique_id"], ())),
                            EvidenceReference.from_dict(json.loads(row["evidence_json"])),
                        )
                        for row in results
                    ),
                )
        except ManifestSnapshotNotFound:
            raise
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise ProjectIndexStorageFailure(
                "supplemental dbt run-results retrieval failed"
            ) from error

    def get_source_freshness_projection(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId
    ) -> DbtSourceFreshnessArtifact | None:
        self._require_project_scope(scope)
        try:
            with self._connect() as connection:
                self._require_supplemental_manifest(connection, scope, snapshot_id, ())
                header = connection.execute(
                    "SELECT * FROM dbt_source_freshness_artifacts "
                    "WHERE manifest_snapshot_id = ? AND is_active = 1",
                    (str(snapshot_id),),
                ).fetchone()
                if header is None:
                    return None
                rows = connection.execute(
                    "SELECT * FROM dbt_source_freshness_results "
                    "WHERE manifest_snapshot_id = ? AND content_digest = ? "
                    "ORDER BY unique_id ASC",
                    (str(snapshot_id), header["content_digest"]),
                ).fetchall()
                metadata = DbtSupplementalArtifactMetadata(
                    header["schema_version"],
                    header["dbt_version"],
                    None
                    if header["generated_at"] is None
                    else datetime.fromisoformat(header["generated_at"]),
                    header["invocation_id"],
                    header["content_digest"],
                    header["normalized_digest"],
                    header["source_identity"],
                    datetime.fromisoformat(header["ingested_at"]),
                )
                return DbtSourceFreshnessArtifact(
                    metadata,
                    scope,
                    float(header["elapsed_time_seconds"]),
                    tuple(
                        DbtSourceFreshnessResult(
                            DbtNodeId(row["unique_id"]),
                            DbtFreshnessStatus(row["status"]),
                            None
                            if row["max_loaded_at"] is None
                            else datetime.fromisoformat(row["max_loaded_at"]),
                            None
                            if row["snapshotted_at"] is None
                            else datetime.fromisoformat(row["snapshotted_at"]),
                            None if row["age_seconds"] is None else float(row["age_seconds"]),
                            None
                            if row["warn_count"] is None
                            else DbtFreshnessThreshold(
                                int(row["warn_count"]), DbtFreshnessPeriod(row["warn_period"])
                            ),
                            None
                            if row["error_count"] is None
                            else DbtFreshnessThreshold(
                                int(row["error_count"]),
                                DbtFreshnessPeriod(row["error_period"]),
                            ),
                            None
                            if row["execution_time_seconds"] is None
                            else float(row["execution_time_seconds"]),
                            EvidenceReference.from_dict(json.loads(row["evidence_json"])),
                        )
                        for row in rows
                    ),
                )
        except ManifestSnapshotNotFound:
            raise
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise ProjectIndexStorageFailure(
                "supplemental dbt source-freshness retrieval failed"
            ) from error

    def get_node(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, unique_id: DbtNodeId
    ) -> DbtManifestNode:
        nodes = self._scoped_nodes(scope, snapshot_id, "AND node.unique_id = ?", (str(unique_id),))
        if not nodes:
            raise ManifestNodeNotFound("manifest node was not found")
        return nodes[0]

    def find_nodes_by_original_file_path(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, original_file_path: str
    ) -> tuple[DbtManifestNode, ...]:
        return tuple(
            self._scoped_nodes(
                scope,
                snapshot_id,
                "AND node.original_file_path = ?",
                (original_file_path,),
            )
        )

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
                    "INSERT INTO source_structure_files VALUES (?, ?, ?)",
                    [
                        (
                            str(artifact.snapshot.snapshot_id),
                            item.relative_path,
                            item.content_digest,
                        )
                        for item in artifact.files
                    ],
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
                    "SELECT (SELECT COUNT(*) FROM source_structure_files WHERE snapshot_id = ?) "
                    "AS files, (SELECT COUNT(*) FROM source_structure_symbols "
                    "WHERE snapshot_id = ?) AS symbols, (SELECT COUNT(*) FROM "
                    "source_structure_edges WHERE snapshot_id = ?) AS edges",
                    (
                        str(artifact.snapshot.snapshot_id),
                        str(artifact.snapshot.snapshot_id),
                        str(artifact.snapshot.snapshot_id),
                    ),
                ).fetchone()
                if (
                    counts is None
                    or int(counts["files"]) != len(artifact.files)
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

    def _validate_supplemental_scope(self, scope: MemoryScope, artifact_scope: MemoryScope) -> None:
        self._require_project_scope(scope)
        if artifact_scope != scope:
            raise InvalidManifestSnapshotScope(
                "supplemental dbt artifact requires exact manifest scope"
            )

    def _require_supplemental_manifest(
        self,
        connection: sqlite3.Connection,
        scope: MemoryScope,
        snapshot_id: DbtSnapshotId,
        resource_ids: tuple[DbtNodeId, ...],
    ) -> None:
        if self._scoped_snapshot_row(connection, scope, snapshot_id) is None:
            raise ManifestSnapshotNotFound("manifest snapshot was not found")
        found: set[str] = set()
        requested = tuple(sorted({str(item) for item in resource_ids}))
        for index in range(0, len(requested), 500):
            batch = requested[index : index + 500]
            placeholders = ",".join("?" for _ in batch)
            rows = connection.execute(
                "SELECT unique_id FROM dbt_manifest_nodes WHERE snapshot_id = ? "
                f"AND unique_id IN ({placeholders})",
                (str(snapshot_id), *batch),
            ).fetchall()
            found.update(row["unique_id"] for row in rows)
        if found != set(requested):
            raise SupplementalArtifactConflict(
                "supplemental dbt artifact references a node absent from the manifest snapshot"
            )

    @staticmethod
    def _store_supplemental_header(
        connection: sqlite3.Connection,
        snapshot_id: DbtSnapshotId,
        artifact_kind: str,
        metadata: DbtSupplementalArtifactMetadata,
        *,
        catalog_error_count: int | None,
        elapsed_time_seconds: float | None,
        command_name: str | None,
    ) -> bool:
        existing = connection.execute(
            "SELECT * FROM dbt_supplemental_artifacts WHERE manifest_snapshot_id = ? "
            "AND artifact_kind = ? AND content_digest = ?",
            (str(snapshot_id), artifact_kind, metadata.content_digest),
        ).fetchone()
        if existing is not None:
            if (
                existing["normalized_digest"] != metadata.normalized_digest
                or existing["source_identity"] != metadata.source_identity
                or existing["schema_version"] != metadata.schema_version
            ):
                raise SupplementalArtifactConflict(
                    "supplemental dbt artifact digest conflicts with retained metadata"
                )
            return True
        connection.execute(
            "INSERT INTO dbt_supplemental_artifacts VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (
                str(snapshot_id),
                artifact_kind,
                metadata.content_digest,
                metadata.schema_version,
                metadata.dbt_version,
                None if metadata.generated_at is None else metadata.generated_at.isoformat(),
                metadata.invocation_id,
                metadata.normalized_digest,
                metadata.source_identity,
                metadata.ingested_at.isoformat(),
                catalog_error_count,
                elapsed_time_seconds,
                command_name,
            ),
        )
        return False

    @staticmethod
    def _activate_supplemental(
        connection: sqlite3.Connection,
        snapshot_id: DbtSnapshotId,
        artifact_kind: str,
        content_digest: str,
    ) -> None:
        connection.execute(
            "UPDATE dbt_supplemental_artifacts SET is_active = 0 "
            "WHERE manifest_snapshot_id = ? AND artifact_kind = ? AND is_active = 1",
            (str(snapshot_id), artifact_kind),
        )
        updated = connection.execute(
            "UPDATE dbt_supplemental_artifacts SET is_active = 1 "
            "WHERE manifest_snapshot_id = ? AND artifact_kind = ? AND content_digest = ?",
            (str(snapshot_id), artifact_kind, content_digest),
        )
        if updated.rowcount != 1:
            raise SupplementalArtifactConflict("supplemental dbt artifact could not be activated")

    @staticmethod
    def _active_supplemental_row(
        connection: sqlite3.Connection, snapshot_id: DbtSnapshotId, artifact_kind: str
    ) -> sqlite3.Row | None:
        return cast(
            sqlite3.Row | None,
            connection.execute(
                "SELECT * FROM dbt_supplemental_artifacts WHERE manifest_snapshot_id = ? "
                "AND artifact_kind = ? AND is_active = 1",
                (str(snapshot_id), artifact_kind),
            ).fetchone(),
        )

    @staticmethod
    def _supplemental_metadata_from_row(
        row: sqlite3.Row,
    ) -> DbtSupplementalArtifactMetadata:
        return DbtSupplementalArtifactMetadata(
            row["schema_version"],
            row["dbt_version"],
            None if row["generated_at"] is None else datetime.fromisoformat(row["generated_at"]),
            row["invocation_id"],
            row["content_digest"],
            row["normalized_digest"],
            row["source_identity"],
            datetime.fromisoformat(row["ingested_at"]),
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
                    + " ORDER BY edge.parent_unique_id ASC, edge.child_unique_id ASC, "
                    "edge.edge_type ASC",
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
            macro_dependency_ids=(),
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
        self._insert_event_outbox(
            connection,
            EventOutboxJob.create(
                scope=event.scope,
                topic=EventOutboxTopic.CHECKPOINT_LIFECYCLE,
                source_event_id=event.event_id,
                event_kind=event.kind.value,
                occurred_at=event.occurred_at,
                created_at=event.occurred_at,
            ),
        )

    @staticmethod
    def _insert_approved_event(
        connection: sqlite3.Connection, event: ApprovedEpisodicEvent
    ) -> None:
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
                *SQLiteCheckpointRepository._scope_values(event.scope),
                event.occurred_at.isoformat(),
            ),
        )
        SQLiteCheckpointRepository._insert_approved_event_evidence(connection, event)
        SQLiteCheckpointRepository._insert_event_outbox(
            connection,
            EventOutboxJob.create(
                scope=event.scope,
                topic=EventOutboxTopic.APPROVED_EPISODIC,
                source_event_id=event.event_id,
                event_kind=event.kind.value,
                occurred_at=event.occurred_at,
                created_at=event.occurred_at,
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

    @staticmethod
    def _insert_approved_governance(
        connection: sqlite3.Connection,
        governance: ApprovedEpisodicEventGovernance,
        target_event_sequence: int,
    ) -> None:
        connection.execute(
            "INSERT INTO approved_episodic_event_governance("
            "action_id,source_action_key,action_kind,target_event_id,target_event_sequence,"
            "replacement_event_id,reason,owner_id,visibility,workspace_id,project_id,session_id,"
            "task_id,occurred_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(governance.action_id),
                governance.source_action_key,
                governance.kind.value,
                str(governance.target_event_id),
                target_event_sequence,
                None
                if governance.replacement_event_id is None
                else str(governance.replacement_event_id),
                governance.reason,
                *SQLiteCheckpointRepository._scope_values(governance.scope),
                governance.occurred_at.isoformat(),
            ),
        )
        for evidence in governance.evidence_references:
            connection.execute(
                "INSERT OR IGNORE INTO evidence(evidence_id, source_id, payload_json) "
                "VALUES (?, ?, ?)",
                (str(evidence.evidence_id), str(evidence.source_id), _json(evidence.to_dict())),
            )
            connection.execute(
                "INSERT INTO approved_episodic_event_governance_evidence(action_id, evidence_id) "
                "VALUES (?, ?)",
                (str(governance.action_id), str(evidence.evidence_id)),
            )
        SQLiteCheckpointRepository._insert_event_outbox(
            connection,
            EventOutboxJob.create(
                scope=governance.scope,
                topic=EventOutboxTopic.APPROVED_GOVERNANCE,
                source_event_id=governance.action_id,
                event_kind=governance.kind.value,
                occurred_at=governance.occurred_at,
                created_at=governance.occurred_at,
            ),
        )

    @staticmethod
    def _insert_event_outbox(connection: sqlite3.Connection, job: EventOutboxJob) -> None:
        connection.execute(
            "INSERT INTO event_outbox("
            "job_id,topic,source_event_id,event_kind,owner_id,visibility,workspace_id,project_id,"
            "session_id,task_id,occurred_at,created_at,available_at,attempt_count,lease_owner,"
            "lease_expires_at,completed_at,last_failure_code) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(job.job_id),
                job.topic.value,
                str(job.source_event_id),
                job.event_kind,
                *SQLiteCheckpointRepository._scope_values(job.scope),
                _outbox_timestamp(job.occurred_at),
                _outbox_timestamp(job.created_at),
                _outbox_timestamp(job.available_at),
                job.attempt_count,
                job.lease_owner,
                None if job.lease_expires_at is None else _outbox_timestamp(job.lease_expires_at),
                None if job.completed_at is None else _outbox_timestamp(job.completed_at),
                job.last_failure_code,
            ),
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
    def _event_outbox_from_row(row: sqlite3.Row) -> EventOutboxJob:
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
        return EventOutboxJob(
            job_id=OutboxJobId.from_string(row["job_id"]),
            scope=scope,
            topic=EventOutboxTopic(row["topic"]),
            source_event_id=EventId.from_string(row["source_event_id"]),
            event_kind=row["event_kind"],
            occurred_at=datetime.fromisoformat(row["occurred_at"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            available_at=datetime.fromisoformat(row["available_at"]),
            attempt_count=int(row["attempt_count"]),
            lease_owner=row["lease_owner"],
            lease_expires_at=(
                None
                if row["lease_expires_at"] is None
                else datetime.fromisoformat(row["lease_expires_at"])
            ),
            completed_at=(
                None if row["completed_at"] is None else datetime.fromisoformat(row["completed_at"])
            ),
            last_failure_code=row["last_failure_code"],
        )

    def _scoped_event_outbox_job(
        self,
        connection: sqlite3.Connection,
        scope: MemoryScope,
        job_id: OutboxJobId,
    ) -> EventOutboxJob:
        row = connection.execute(
            "SELECT * FROM event_outbox WHERE job_id = ? AND owner_id = ? AND visibility = ? "
            "AND workspace_id IS ? AND project_id = ? AND session_id = ? AND task_id = ?",
            (str(job_id), *self._scope_values(scope)),
        ).fetchone()
        if row is None:
            raise EventOutboxNotFound("event outbox job was not found")
        return self._event_outbox_from_row(row)

    @staticmethod
    def _require_event_outbox_lease(job: EventOutboxJob, worker_id: str, now: datetime) -> None:
        if (
            job.completed_at is not None
            or job.lease_owner != worker_id
            or job.lease_expires_at is None
            or job.lease_expires_at <= now
        ):
            raise EventOutboxLeaseConflict("event outbox lease is not owned by this worker")

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
    def _checkpoint_source_observation_from_row(
        row: sqlite3.Row, scope: MemoryScope
    ) -> CheckpointSourceObservation:
        return CheckpointSourceObservation(
            scope=scope,
            checkpoint_id=CheckpointId.from_string(row["checkpoint_id"]),
            revision_id=CheckpointRevisionId.from_string(row["checkpoint_revision_id"]),
            source_snapshot_id=CodeSnapshotId.from_string(row["source_snapshot_id"]),
            observed_at=datetime.fromisoformat(row["observed_at"]),
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
    def _approved_governance_from_row(
        connection: sqlite3.Connection, row: sqlite3.Row, scope: MemoryScope
    ) -> ApprovedEpisodicEventGovernance:
        evidence_rows = connection.execute(
            "SELECT evidence.payload_json "
            "FROM approved_episodic_event_governance_evidence AS link "
            "JOIN evidence ON evidence.evidence_id = link.evidence_id "
            "WHERE link.action_id = ? ORDER BY link.evidence_id ASC",
            (row["action_id"],),
        ).fetchall()
        return ApprovedEpisodicEventGovernance(
            action_id=EventId.from_string(row["action_id"]),
            scope=scope,
            kind=ApprovedEventGovernanceKind(row["action_kind"]),
            target_event_id=EventId.from_string(row["target_event_id"]),
            replacement_event_id=(
                None
                if row["replacement_event_id"] is None
                else EventId.from_string(row["replacement_event_id"])
            ),
            reason=row["reason"],
            source_action_key=row["source_action_key"],
            occurred_at=datetime.fromisoformat(row["occurred_at"]),
            evidence_references=tuple(
                EvidenceReference.from_dict(json.loads(item["payload_json"]))
                for item in evidence_rows
            ),
        )

    @staticmethod
    def _scoped_approved_event_row(
        connection: sqlite3.Connection, scope: MemoryScope, event_id: EventId
    ) -> sqlite3.Row | None:
        return cast(
            sqlite3.Row | None,
            connection.execute(
                "SELECT * FROM approved_episodic_events WHERE event_id = ? "
                "AND owner_id = ? AND visibility = ? AND workspace_id IS ? "
                "AND project_id = ? AND session_id = ? AND task_id = ?",
                (str(event_id), *SQLiteCheckpointRepository._scope_values(scope)),
            ).fetchone(),
        )

    @staticmethod
    def _scoped_approved_governance_row(
        connection: sqlite3.Connection, scope: MemoryScope, event_id: EventId
    ) -> sqlite3.Row | None:
        return cast(
            sqlite3.Row | None,
            connection.execute(
                "SELECT * FROM approved_episodic_event_governance WHERE target_event_id = ? "
                "AND owner_id = ? AND visibility = ? AND workspace_id IS ? "
                "AND project_id = ? AND session_id = ? AND task_id = ?",
                (str(event_id), *SQLiteCheckpointRepository._scope_values(scope)),
            ).fetchone(),
        )

    @staticmethod
    def _approved_event_record(
        connection: sqlite3.Connection, scope: MemoryScope, event_id: EventId
    ) -> ApprovedEpisodicEventRecord:
        event_row = SQLiteCheckpointRepository._scoped_approved_event_row(
            connection, scope, event_id
        )
        governance_row = SQLiteCheckpointRepository._scoped_approved_governance_row(
            connection, scope, event_id
        )
        if event_row is not None:
            event = SQLiteCheckpointRepository._approved_event_from_row(
                connection, event_row, scope
            )
            governance = (
                None
                if governance_row is None
                else SQLiteCheckpointRepository._approved_governance_from_row(
                    connection, governance_row, scope
                )
            )
            return ApprovedEpisodicEventRecord(
                event_id,
                scope,
                ApprovedEventLifecycleStatus.ACTIVE
                if governance is None
                else ApprovedEventLifecycleStatus.CORRECTED,
                event,
                governance,
            )
        if governance_row is not None and governance_row["action_kind"] == "retracted":
            governance = SQLiteCheckpointRepository._approved_governance_from_row(
                connection, governance_row, scope
            )
            return ApprovedEpisodicEventRecord(
                event_id,
                scope,
                ApprovedEventLifecycleStatus.RETRACTED,
                None,
                governance,
            )
        raise ApprovedEpisodicEventNotFound("approved episodic event was not found")

    @staticmethod
    def _same_approved_event_intent(
        first: ApprovedEpisodicEvent, second: ApprovedEpisodicEvent
    ) -> bool:
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

    def _validate_approved_governance(
        self, scope: MemoryScope, governance: ApprovedEpisodicEventGovernance
    ) -> None:
        self._require_approved_episodic_scope(scope)
        if governance.scope != scope:
            raise InvalidApprovedEpisodicEventScope(
                "approved event governance requires one complete task scope"
            )

    @staticmethod
    def _require_available_approved_action_key(
        connection: sqlite3.Connection, governance: ApprovedEpisodicEventGovernance
    ) -> None:
        existing = connection.execute(
            "SELECT 1 FROM approved_episodic_event_governance WHERE owner_id = ? "
            "AND visibility = ? AND workspace_id IS ? AND project_id = ? "
            "AND session_id = ? AND task_id = ? AND source_action_key = ?",
            (
                *SQLiteCheckpointRepository._scope_values(governance.scope),
                governance.source_action_key,
            ),
        ).fetchone()
        if existing is not None:
            raise ApprovedEpisodicEventConflict("approved event action key conflicts")

    def _require_available_approved_replacement(
        self,
        connection: sqlite3.Connection,
        replacement: ApprovedEpisodicEvent,
        governance: ApprovedEpisodicEventGovernance,
    ) -> None:
        self._require_available_approved_action_key(connection, governance)
        event_conflict = connection.execute(
            "SELECT 1 FROM approved_episodic_events WHERE event_id = ? OR "
            "(owner_id = ? AND visibility = ? AND workspace_id IS ? AND project_id = ? "
            "AND session_id = ? AND task_id = ? AND source_event_key = ?)",
            (
                str(replacement.event_id),
                *self._scope_values(replacement.scope),
                replacement.source_event_key,
            ),
        ).fetchone()
        governed = connection.execute(
            "SELECT 1 FROM approved_episodic_event_governance WHERE target_event_id = ?",
            (str(replacement.event_id),),
        ).fetchone()
        if event_conflict is not None or governed is not None:
            raise ApprovedEpisodicEventConflict("approved event replacement conflicts")

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


def _pack_embedding_vector(vector: tuple[float, ...]) -> bytes:
    """Serialize bounded finite floats without a pickle or architecture-dependent payload."""
    return struct.pack(f"!{len(vector)}f", *vector)


def _unpack_embedding_vector(payload: bytes, dimensions: int) -> tuple[float, ...]:
    if not 8 <= dimensions <= 4_096 or len(payload) != dimensions * 4:
        raise ValueError("knowledge embedding vector storage is invalid")
    return tuple(struct.unpack(f"!{dimensions}f", payload))


class _KnowledgeOperations:
    """SQLite-specific knowledge operations kept separate from checkpoint public methods."""

    @staticmethod
    def list_active_knowledge_documents(
        backend: SQLiteCheckpointRepository, scope: MemoryScope
    ) -> tuple[KnownKnowledgeDocument, ...]:
        backend._require_project_scope(scope)
        try:
            with backend._connect() as connection:
                rows = connection.execute(
                    "SELECT source.document_id, source.relative_path, source.content_digest, "
                    "source.current_revision_id, "
                    "revision.revision_number FROM knowledge_document_sources AS source "
                    "JOIN knowledge_document_revisions AS revision "
                    "ON revision.revision_id = source.current_revision_id "
                    "WHERE source.owner_id = ? AND source.workspace_id IS ? "
                    "AND source.project_id = ? AND source.is_deleted = 0 "
                    "ORDER BY source.relative_path ASC, source.document_id ASC",
                    (str(scope.owner_id), _maybe(scope.workspace_id), str(scope.project_id)),
                ).fetchall()
        except sqlite3.Error as error:
            raise KnowledgeDocumentStorageFailure("knowledge storage operation failed") from error
        return tuple(_KnowledgeOperations._known_knowledge_document(row, scope) for row in rows)

    @staticmethod
    def get_current_knowledge_revision(
        backend: SQLiteCheckpointRepository, scope: MemoryScope, document_id: KnowledgeDocumentId
    ) -> KnowledgeDocumentRevision:
        backend._require_project_scope(scope)
        try:
            with backend._connect() as connection:
                row = connection.execute(
                    "SELECT revision.* FROM knowledge_document_sources AS source JOIN "
                    "knowledge_document_revisions AS revision "
                    "ON revision.revision_id = source.current_revision_id "
                    "WHERE source.document_id = ? AND source.owner_id = ? "
                    "AND source.workspace_id IS ? AND source.project_id = ? "
                    "AND source.is_deleted = 0",
                    (
                        str(document_id),
                        str(scope.owner_id),
                        _maybe(scope.workspace_id),
                        str(scope.project_id),
                    ),
                ).fetchone()
                if row is None:
                    raise KnowledgeDocumentNotFound("knowledge document was not found")
                return _KnowledgeOperations._knowledge_revision_from_row(connection, row, scope)
        except KnowledgeDocumentNotFound:
            raise
        except sqlite3.Error as error:
            raise KnowledgeDocumentStorageFailure("knowledge storage operation failed") from error

    @staticmethod
    def get_current_knowledge_revision_by_path(
        backend: SQLiteCheckpointRepository, scope: MemoryScope, relative_path: str
    ) -> KnowledgeDocumentRevision:
        """Resolve one exact current source path through complete SQL scope predicates."""
        backend._require_project_scope(scope)
        if not relative_path or relative_path.startswith("/") or ".." in relative_path.split("/"):
            raise KnowledgeDocumentNotFound("knowledge document was not found")
        try:
            with backend._connect() as connection:
                row = connection.execute(
                    "SELECT revision.* FROM knowledge_document_sources AS source JOIN "
                    "knowledge_document_revisions AS revision "
                    "ON revision.revision_id = source.current_revision_id "
                    "WHERE source.relative_path = ? AND source.owner_id = ? "
                    "AND source.workspace_id IS ? AND source.project_id = ? "
                    "AND source.is_deleted = 0",
                    (
                        relative_path,
                        str(scope.owner_id),
                        _maybe(scope.workspace_id),
                        str(scope.project_id),
                    ),
                ).fetchone()
                if row is None:
                    raise KnowledgeDocumentNotFound("knowledge document was not found")
                return _KnowledgeOperations._knowledge_revision_from_row(connection, row, scope)
        except KnowledgeDocumentNotFound:
            raise
        except sqlite3.Error as error:
            raise KnowledgeDocumentStorageFailure("knowledge storage operation failed") from error

    @staticmethod
    def get_knowledge_revision(
        backend: SQLiteCheckpointRepository,
        scope: MemoryScope,
        document_id: KnowledgeDocumentId,
        revision_id: KnowledgeDocumentRevisionId,
    ) -> KnowledgeDocumentRevision:
        """Retrieve one retained revision through both explicit source and revision identity."""
        backend._require_project_scope(scope)
        try:
            with backend._connect() as connection:
                row = connection.execute(
                    "SELECT revision.* FROM knowledge_document_sources AS source JOIN "
                    "knowledge_document_revisions AS revision "
                    "ON revision.document_id = source.document_id "
                    "WHERE source.document_id = ? AND revision.revision_id = ? "
                    "AND source.owner_id = ? AND source.workspace_id IS ? "
                    "AND source.project_id = ? AND source.is_deleted = 0",
                    (
                        str(document_id),
                        str(revision_id),
                        str(scope.owner_id),
                        _maybe(scope.workspace_id),
                        str(scope.project_id),
                    ),
                ).fetchone()
                if row is None:
                    raise KnowledgeDocumentNotFound("knowledge document was not found")
                return _KnowledgeOperations._knowledge_revision_from_row(connection, row, scope)
        except KnowledgeDocumentNotFound:
            raise
        except sqlite3.Error as error:
            raise KnowledgeDocumentStorageFailure("knowledge storage operation failed") from error

    @staticmethod
    def search_current_knowledge_sections(
        backend: SQLiteCheckpointRepository,
        scope: MemoryScope,
        terms: tuple[str, ...],
        limit: int,
        maximum_documents: int,
    ) -> tuple[KnowledgeDocumentSectionMatch, ...]:
        """Search one current, SQL-scoped FTS projection before shared deterministic ranking."""
        backend._require_project_scope(scope)
        validate_knowledge_search(terms, limit, maximum_documents)
        try:
            with backend._connect() as connection:
                fts_query = " OR ".join(f'"{term}"' for term in terms)
                matched_rows = connection.execute(
                    "WITH selected_sources AS ("
                    "SELECT document_id, current_revision_id FROM knowledge_document_sources "
                    "WHERE owner_id = ? AND workspace_id IS ? AND project_id = ? "
                    "AND is_deleted = 0 ORDER BY relative_path ASC, document_id ASC LIMIT ?"
                    ") SELECT fts.revision_id, fts.section_index "
                    "FROM knowledge_document_section_fts AS fts "
                    "JOIN selected_sources AS source "
                    "ON source.document_id = fts.document_id "
                    "AND source.current_revision_id = fts.revision_id "
                    "WHERE fts.owner_id = ? AND fts.workspace_id IS ? AND fts.project_id = ? "
                    "AND knowledge_document_section_fts MATCH ? "
                    "ORDER BY fts.relative_path ASC, fts.revision_id ASC, fts.section_index ASC",
                    (
                        str(scope.owner_id),
                        _maybe(scope.workspace_id),
                        str(scope.project_id),
                        maximum_documents,
                        str(scope.owner_id),
                        _maybe(scope.workspace_id),
                        str(scope.project_id),
                        fts_query,
                    ),
                ).fetchall()
                matched_keys = {
                    (row["revision_id"], int(row["section_index"])) for row in matched_rows
                }
                if not matched_keys:
                    return ()
                revision_ids = sorted({revision_id for revision_id, _ in matched_keys})
                placeholders = ", ".join("?" for _ in revision_ids)
                rows = connection.execute(
                    "SELECT revision.* FROM knowledge_document_sources AS source JOIN "
                    "knowledge_document_revisions AS revision "
                    "ON revision.revision_id = source.current_revision_id "
                    "WHERE source.owner_id = ? AND source.workspace_id IS ? "
                    "AND source.project_id = ? AND source.is_deleted = 0 "
                    f"AND revision.revision_id IN ({placeholders}) "
                    "ORDER BY source.relative_path ASC, source.document_id ASC",
                    (
                        str(scope.owner_id),
                        _maybe(scope.workspace_id),
                        str(scope.project_id),
                        *revision_ids,
                    ),
                ).fetchall()
                revisions = tuple(
                    _KnowledgeOperations._knowledge_revision_from_row(connection, row, scope)
                    for row in rows
                )
                return tuple(
                    match
                    for match in rank_knowledge_sections(revisions, terms, limit)
                    if (str(match.revision.revision_id), match.section_index) in matched_keys
                )
        except KnowledgeDocumentConflict:
            raise
        except sqlite3.Error as error:
            raise KnowledgeDocumentStorageFailure("knowledge storage operation failed") from error

    @staticmethod
    def iter_current_knowledge_sections(
        backend: SQLiteCheckpointRepository, scope: MemoryScope, maximum_documents: int
    ) -> tuple[CurrentKnowledgeDocumentSection, ...]:
        """Load only selected current sections, in source/section order, for a local projection."""
        backend._require_project_scope(scope)
        if not 1 <= maximum_documents <= 128:
            raise KnowledgeDocumentConflict("knowledge document limit is invalid")
        try:
            with backend._connect() as connection:
                rows = connection.execute(
                    "WITH selected_sources AS ("
                    "SELECT document_id, current_revision_id FROM knowledge_document_sources "
                    "WHERE owner_id = ? AND workspace_id IS ? AND project_id = ? "
                    "AND is_deleted = 0 ORDER BY relative_path ASC, document_id ASC LIMIT ?"
                    ") SELECT revision.* FROM selected_sources AS selected "
                    "JOIN knowledge_document_revisions AS revision "
                    "ON revision.revision_id = selected.current_revision_id "
                    "ORDER BY revision.relative_path ASC, revision.document_id ASC",
                    (
                        str(scope.owner_id),
                        _maybe(scope.workspace_id),
                        str(scope.project_id),
                        maximum_documents,
                    ),
                ).fetchall()
                return tuple(
                    CurrentKnowledgeDocumentSection(revision, index, section)
                    for row in rows
                    for revision in (
                        _KnowledgeOperations._knowledge_revision_from_row(connection, row, scope),
                    )
                    for index, section in enumerate(revision.document.sections)
                )
        except KnowledgeDocumentConflict:
            raise
        except sqlite3.Error as error:
            raise KnowledgeDocumentStorageFailure("knowledge storage operation failed") from error

    @staticmethod
    def list_current_knowledge_section_embeddings(
        backend: SQLiteCheckpointRepository,
        scope: MemoryScope,
        model_id: str,
        maximum_documents: int,
    ) -> tuple[KnowledgeSectionEmbedding, ...]:
        backend._require_project_scope(scope)
        if not model_id or len(model_id) > 256 or not 1 <= maximum_documents <= 128:
            raise KnowledgeDocumentConflict("knowledge embedding query is invalid")
        try:
            with backend._connect() as connection:
                rows = connection.execute(
                    "WITH selected_sources AS ("
                    "SELECT document_id, current_revision_id FROM knowledge_document_sources "
                    "WHERE owner_id = ? AND workspace_id IS ? AND project_id = ? "
                    "AND is_deleted = 0 ORDER BY relative_path ASC, document_id ASC LIMIT ?"
                    ") SELECT embedding.* FROM selected_sources AS selected "
                    "JOIN knowledge_section_embeddings AS embedding "
                    "ON embedding.revision_id = selected.current_revision_id "
                    "WHERE embedding.owner_id = ? AND embedding.workspace_id IS ? "
                    "AND embedding.project_id = ? AND embedding.model_id = ? "
                    "ORDER BY embedding.revision_id ASC, embedding.section_index ASC",
                    (
                        str(scope.owner_id),
                        _maybe(scope.workspace_id),
                        str(scope.project_id),
                        maximum_documents,
                        str(scope.owner_id),
                        _maybe(scope.workspace_id),
                        str(scope.project_id),
                        model_id,
                    ),
                ).fetchall()
                return tuple(
                    KnowledgeSectionEmbedding(
                        scope,
                        KnowledgeDocumentRevisionId.from_string(row["revision_id"]),
                        int(row["section_index"]),
                        row["model_id"],
                        row["section_digest"],
                        _unpack_embedding_vector(row["vector_blob"], int(row["dimensions"])),
                    )
                    for row in rows
                )
        except (ValueError, struct.error) as error:
            raise KnowledgeDocumentStorageFailure(
                "knowledge embedding storage is invalid"
            ) from error
        except sqlite3.Error as error:
            raise KnowledgeDocumentStorageFailure("knowledge storage operation failed") from error

    @staticmethod
    def store_knowledge_section_embeddings(
        backend: SQLiteCheckpointRepository,
        scope: MemoryScope,
        embeddings: tuple[KnowledgeSectionEmbedding, ...],
    ) -> None:
        backend._require_project_scope(scope)
        if not embeddings:
            return
        keys = [(item.revision_id, item.section_index, item.model_id) for item in embeddings]
        if len(set(keys)) != len(keys) or any(item.scope != scope for item in embeddings):
            raise KnowledgeDocumentConflict("knowledge embeddings are invalid")
        try:
            with backend._transaction() as connection:
                backend._store_project_scope(connection, scope)
                for item in embeddings:
                    exists = connection.execute(
                        "SELECT 1 FROM knowledge_document_sources AS source "
                        "JOIN knowledge_document_sections AS section "
                        "ON section.revision_id = source.current_revision_id "
                        "WHERE source.owner_id = ? AND source.workspace_id IS ? "
                        "AND source.project_id = ? AND source.is_deleted = 0 "
                        "AND section.revision_id = ? AND section.section_index = ?",
                        (
                            str(scope.owner_id),
                            _maybe(scope.workspace_id),
                            str(scope.project_id),
                            str(item.revision_id),
                            item.section_index,
                        ),
                    ).fetchone()
                    if exists is None:
                        raise KnowledgeDocumentConflict("knowledge embedding is not current")
                connection.executemany(
                    "INSERT INTO knowledge_section_embeddings("
                    "revision_id, section_index, owner_id, workspace_id, project_id, model_id, "
                    "section_digest, dimensions, vector_blob) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(revision_id, section_index, model_id) DO UPDATE SET "
                    "section_digest = excluded.section_digest, dimensions = excluded.dimensions, "
                    "vector_blob = excluded.vector_blob",
                    [
                        (
                            str(item.revision_id),
                            item.section_index,
                            str(scope.owner_id),
                            _maybe(scope.workspace_id),
                            str(scope.project_id),
                            item.model_id,
                            item.section_digest,
                            len(item.vector),
                            _pack_embedding_vector(item.vector),
                        )
                        for item in embeddings
                    ],
                )
        except KnowledgeDocumentConflict:
            raise
        except sqlite3.IntegrityError as error:
            raise KnowledgeDocumentConflict("knowledge embedding storage conflicts") from error
        except (sqlite3.Error, struct.error) as error:
            raise KnowledgeDocumentStorageFailure("knowledge storage operation failed") from error

    @staticmethod
    def apply_knowledge_sync(
        backend: SQLiteCheckpointRepository,
        scope: MemoryScope,
        revisions: tuple[KnowledgeDocumentRevision, ...],
        tombstones: tuple[KnowledgeDocumentTombstone, ...],
    ) -> KnowledgeDocumentSyncStoreResult:
        """Atomically apply scoped revisions and destructive payload deletion tombstones."""
        backend._require_project_scope(scope)
        _KnowledgeOperations._validate_knowledge_sync(scope, revisions, tombstones)
        try:
            with backend._transaction() as connection:
                backend._store_project_scope(connection, scope)
                for tombstone in tombstones:
                    current = _KnowledgeOperations._knowledge_source_row(
                        connection, scope, tombstone.document_id
                    )
                    if (
                        current is None
                        or int(current["is_deleted"]) != 0
                        or current["current_revision_id"] != str(tombstone.expected_revision_id)
                        or current["content_digest"] != tombstone.content_digest
                        or current["relative_path"] != tombstone.relative_path
                    ):
                        raise KnowledgeDocumentConflict("knowledge document deletion conflicts")
                    connection.execute(
                        "UPDATE knowledge_document_sources SET current_revision_id = NULL, "
                        "is_deleted = 1, deleted_at = ? WHERE document_id = ? "
                        "AND owner_id = ? AND workspace_id IS ? AND project_id = ?",
                        (
                            tombstone.deleted_at.isoformat(),
                            str(tombstone.document_id),
                            str(scope.owner_id),
                            _maybe(scope.workspace_id),
                            str(scope.project_id),
                        ),
                    )
                    connection.execute(
                        "INSERT INTO knowledge_document_tombstones "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(document_id) DO UPDATE SET "
                        "relative_path = excluded.relative_path, "
                        "content_digest = excluded.content_digest, "
                        "deleted_at = excluded.deleted_at",
                        (
                            str(tombstone.document_id),
                            str(scope.owner_id),
                            scope.visibility.value,
                            _maybe(scope.workspace_id),
                            str(scope.project_id),
                            tombstone.relative_path,
                            tombstone.content_digest,
                            tombstone.deleted_at.isoformat(),
                        ),
                    )
                    # The predecessor relationship deliberately uses RESTRICT so a
                    # malformed write cannot erase history.  Payload erasure must
                    # therefore remove the immutable chain newest-first; sections
                    # and links cascade with each revision.
                    revision_rows = connection.execute(
                        "SELECT revision_id FROM knowledge_document_revisions "
                        "WHERE document_id = ? ORDER BY revision_number DESC",
                        (str(tombstone.document_id),),
                    ).fetchall()
                    for revision_row in revision_rows:
                        connection.execute(
                            "DELETE FROM knowledge_document_revisions WHERE revision_id = ?",
                            (revision_row["revision_id"],),
                        )
                for revision in revisions:
                    _KnowledgeOperations._store_knowledge_revision(connection, scope, revision)
                _KnowledgeOperations._rebuild_knowledge_search_index(connection, scope)
                rows = connection.execute(
                    "SELECT source.document_id, source.relative_path, source.content_digest, "
                    "source.current_revision_id, revision.revision_number "
                    "FROM knowledge_document_sources AS source JOIN knowledge_document_revisions "
                    "AS revision ON revision.revision_id = source.current_revision_id "
                    "WHERE source.owner_id = ? AND source.workspace_id IS ? "
                    "AND source.project_id = ? AND source.is_deleted = 0 "
                    "ORDER BY source.relative_path ASC, source.document_id ASC",
                    (str(scope.owner_id), _maybe(scope.workspace_id), str(scope.project_id)),
                ).fetchall()
                return KnowledgeDocumentSyncStoreResult(
                    tuple(
                        _KnowledgeOperations._known_knowledge_document(row, scope) for row in rows
                    ),
                    len(revisions),
                    len(tombstones),
                )
        except (
            KnowledgeDocumentConflict,
            KnowledgeDocumentSecretRejected,
            InvalidKnowledgeDocumentScope,
        ):
            raise
        except sqlite3.IntegrityError as error:
            raise KnowledgeDocumentConflict(
                "knowledge document storage constraint conflicts"
            ) from error
        except sqlite3.Error as error:
            raise KnowledgeDocumentStorageFailure("knowledge storage operation failed") from error

    @staticmethod
    def _store_knowledge_revision(
        connection: sqlite3.Connection,
        scope: MemoryScope,
        revision: KnowledgeDocumentRevision,
    ) -> None:
        document = revision.document
        safety = KnowledgeDocumentSafetyPolicy().assess(document)
        if not safety.accepted:
            raise KnowledgeDocumentSecretRejected(
                "knowledge document was rejected by safety policy"
            )
        existing = _KnowledgeOperations._knowledge_source_row(
            connection, scope, document.document_id
        )
        if existing is None:
            if revision.revision_number != 1 or revision.predecessor_revision_id is not None:
                raise KnowledgeDocumentConflict("knowledge document creation revision conflicts")
            connection.execute(
                "INSERT INTO knowledge_document_sources "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(document.document_id),
                    str(scope.owner_id),
                    scope.visibility.value,
                    _maybe(scope.workspace_id),
                    str(scope.project_id),
                    scope.level.value,
                    document.relative_path,
                    document.content_digest,
                    None,
                    0,
                    revision.created_at.isoformat(),
                    None,
                ),
            )
        elif (
            int(existing["is_deleted"]) != 0
            or existing["current_revision_id"] != str(revision.predecessor_revision_id)
            or revision.revision_number
            != _KnowledgeOperations._knowledge_revision_number(
                connection, revision.predecessor_revision_id
            )
            + 1
        ):
            raise KnowledgeDocumentConflict("knowledge document current revision conflicts")
        connection.execute(
            "INSERT INTO knowledge_document_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ",
            (
                str(revision.revision_id),
                str(document.document_id),
                revision.revision_number,
                _maybe(revision.predecessor_revision_id),
                document.source_kind.value,
                document.relative_path,
                document.content_digest,
                document.title,
                json.dumps(document.frontmatter, separators=(",", ":")),
                revision.created_at.isoformat(),
            ),
        )
        connection.executemany(
            "INSERT INTO knowledge_document_sections VALUES (?, ?, ?, ?, ?)",
            [
                (str(revision.revision_id), index, section.heading, section.level, section.content)
                for index, section in enumerate(document.sections)
            ],
        )
        connection.executemany(
            "INSERT INTO knowledge_document_links VALUES (?, ?, ?)",
            [(str(revision.revision_id), link.target, link.kind) for link in document.links],
        )
        updated = connection.execute(
            "UPDATE knowledge_document_sources SET relative_path = ?, content_digest = ?, "
            "current_revision_id = ?, is_deleted = 0, deleted_at = NULL "
            "WHERE document_id = ? AND owner_id = ? AND workspace_id IS ? AND project_id = ?",
            (
                document.relative_path,
                document.content_digest,
                str(revision.revision_id),
                str(document.document_id),
                str(scope.owner_id),
                _maybe(scope.workspace_id),
                str(scope.project_id),
            ),
        )
        if updated.rowcount != 1:
            raise KnowledgeDocumentConflict("knowledge document activation conflicts")

    @staticmethod
    def _rebuild_knowledge_search_index(connection: sqlite3.Connection, scope: MemoryScope) -> None:
        """Atomically retain FTS rows for only current revisions in one complete scope."""
        parameters = (str(scope.owner_id), _maybe(scope.workspace_id), str(scope.project_id))
        connection.execute(
            "DELETE FROM knowledge_document_section_fts WHERE owner_id = ? "
            "AND workspace_id IS ? AND project_id = ?",
            parameters,
        )
        connection.execute(
            "INSERT INTO knowledge_document_section_fts("
            "document_id, revision_id, section_index, owner_id, workspace_id, project_id, "
            "relative_path, heading, content) "
            "SELECT source.document_id, revision.revision_id, section.section_index, "
            "source.owner_id, source.workspace_id, source.project_id, source.relative_path, "
            "section.heading, section.content FROM knowledge_document_sources AS source "
            "JOIN knowledge_document_revisions AS revision "
            "ON revision.revision_id = source.current_revision_id "
            "JOIN knowledge_document_sections AS section "
            "ON section.revision_id = revision.revision_id "
            "WHERE source.owner_id = ? AND source.workspace_id IS ? AND source.project_id = ? "
            "AND source.is_deleted = 0",
            parameters,
        )

    @staticmethod
    def _knowledge_revision_number(
        connection: sqlite3.Connection, revision_id: KnowledgeDocumentRevisionId | None
    ) -> int:
        if revision_id is None:
            return 0
        row = connection.execute(
            "SELECT revision_number FROM knowledge_document_revisions WHERE revision_id = ?",
            (str(revision_id),),
        ).fetchone()
        if row is None:
            raise KnowledgeDocumentConflict("knowledge document predecessor was not found")
        return int(row["revision_number"])

    @staticmethod
    def _known_knowledge_document(row: sqlite3.Row, scope: MemoryScope) -> KnownKnowledgeDocument:
        return KnownKnowledgeDocument(
            KnowledgeDocumentId.from_string(row["document_id"]),
            scope,
            row["relative_path"],
            row["content_digest"],
            KnowledgeDocumentRevisionId.from_string(row["current_revision_id"]),
            int(row["revision_number"]),
        )

    @staticmethod
    def _knowledge_source_row(
        connection: sqlite3.Connection, scope: MemoryScope, document_id: KnowledgeDocumentId
    ) -> sqlite3.Row | None:
        return cast(
            sqlite3.Row | None,
            connection.execute(
                "SELECT * FROM knowledge_document_sources WHERE document_id = ? AND owner_id = ? "
                "AND workspace_id IS ? AND project_id = ?",
                (
                    str(document_id),
                    str(scope.owner_id),
                    _maybe(scope.workspace_id),
                    str(scope.project_id),
                ),
            ).fetchone(),
        )

    @staticmethod
    def _knowledge_revision_from_row(
        connection: sqlite3.Connection, row: sqlite3.Row, scope: MemoryScope
    ) -> KnowledgeDocumentRevision:
        revision_id = KnowledgeDocumentRevisionId.from_string(row["revision_id"])
        sections = tuple(
            KnowledgeDocumentSection(item["heading"], int(item["heading_level"]), item["content"])
            for item in connection.execute(
                "SELECT heading, heading_level, content FROM knowledge_document_sections "
                "WHERE revision_id = ? ORDER BY section_index ASC",
                (str(revision_id),),
            ).fetchall()
        )
        links = tuple(
            KnowledgeDocumentLink(item["link_target"], item["link_kind"])
            for item in connection.execute(
                "SELECT link_target, link_kind FROM knowledge_document_links WHERE revision_id = ? "
                "ORDER BY link_kind ASC, link_target ASC",
                (str(revision_id),),
            ).fetchall()
        )
        frontmatter_value = json.loads(row["frontmatter_json"])
        if not isinstance(frontmatter_value, list) or any(
            not isinstance(item, list)
            or len(item) != 2
            or not all(isinstance(value, str) for value in item)
            for item in frontmatter_value
        ):
            raise KnowledgeDocumentStorageFailure("knowledge document stored payload is invalid")
        return KnowledgeDocumentRevision(
            revision_id,
            KnowledgeDocument(
                KnowledgeDocumentId.from_string(row["document_id"]),
                scope,
                row["relative_path"],
                KnowledgeDocumentSourceKind(row["source_kind"]),
                row["content_digest"],
                row["title"],
                tuple((item[0], item[1]) for item in frontmatter_value),
                sections,
                links,
            ),
            int(row["revision_number"]),
            KnowledgeDocumentRevisionId.from_string(row["predecessor_revision_id"])
            if row["predecessor_revision_id"] is not None
            else None,
            datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _validate_knowledge_sync(
        scope: MemoryScope,
        revisions: tuple[KnowledgeDocumentRevision, ...],
        tombstones: tuple[KnowledgeDocumentTombstone, ...],
    ) -> None:
        if any(item.document.scope != scope for item in revisions) or any(
            item.scope != scope for item in tombstones
        ):
            raise InvalidKnowledgeDocumentScope("knowledge document scope is invalid")
        document_ids = [item.document.document_id for item in revisions]
        tombstone_ids = [item.document_id for item in tombstones]
        if (
            len(set(document_ids)) != len(document_ids)
            or len(set(tombstone_ids)) != len(tombstone_ids)
            or set(document_ids) & set(tombstone_ids)
        ):
            raise KnowledgeDocumentConflict("knowledge sync contains conflicting document actions")


class SQLiteKnowledgeDocumentRepository:
    """Scoped SQLite adapter for immutable local knowledge document revisions."""

    def __init__(self, path: Path, *, base_directory: Path | None = None) -> None:
        self._backend = SQLiteCheckpointRepository(path, base_directory=base_directory)

    def migrate(self, *, fail_after_version: int | None = None) -> None:
        self._backend.migrate(fail_after_version=fail_after_version)

    def list_active_documents(self, scope: MemoryScope) -> tuple[KnownKnowledgeDocument, ...]:
        return _KnowledgeOperations.list_active_knowledge_documents(self._backend, scope)

    def get_current_revision(
        self, scope: MemoryScope, document_id: KnowledgeDocumentId
    ) -> KnowledgeDocumentRevision:
        return _KnowledgeOperations.get_current_knowledge_revision(
            self._backend, scope, document_id
        )

    def get_current_revision_by_path(
        self, scope: MemoryScope, relative_path: str
    ) -> KnowledgeDocumentRevision:
        return _KnowledgeOperations.get_current_knowledge_revision_by_path(
            self._backend, scope, relative_path
        )

    def get_revision(
        self,
        scope: MemoryScope,
        document_id: KnowledgeDocumentId,
        revision_id: KnowledgeDocumentRevisionId,
    ) -> KnowledgeDocumentRevision:
        return _KnowledgeOperations.get_knowledge_revision(
            self._backend, scope, document_id, revision_id
        )

    def search_current_sections(
        self,
        scope: MemoryScope,
        terms: tuple[str, ...],
        limit: int,
        maximum_documents: int,
    ) -> tuple[KnowledgeDocumentSectionMatch, ...]:
        return _KnowledgeOperations.search_current_knowledge_sections(
            self._backend, scope, terms, limit, maximum_documents
        )

    def iter_current_sections(
        self, scope: MemoryScope, maximum_documents: int
    ) -> tuple[CurrentKnowledgeDocumentSection, ...]:
        return _KnowledgeOperations.iter_current_knowledge_sections(
            self._backend, scope, maximum_documents
        )

    def list_current_section_embeddings(
        self, scope: MemoryScope, model_id: str, maximum_documents: int
    ) -> tuple[KnowledgeSectionEmbedding, ...]:
        return _KnowledgeOperations.list_current_knowledge_section_embeddings(
            self._backend, scope, model_id, maximum_documents
        )

    def store_section_embeddings(
        self, scope: MemoryScope, embeddings: tuple[KnowledgeSectionEmbedding, ...]
    ) -> None:
        _KnowledgeOperations.store_knowledge_section_embeddings(self._backend, scope, embeddings)

    def apply_sync(
        self,
        scope: MemoryScope,
        revisions: tuple[KnowledgeDocumentRevision, ...],
        tombstones: tuple[KnowledgeDocumentTombstone, ...],
    ) -> KnowledgeDocumentSyncStoreResult:
        return _KnowledgeOperations.apply_knowledge_sync(
            self._backend, scope, revisions, tombstones
        )


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

    def iter_files(self, scope: MemoryScope, snapshot_id: CodeSnapshotId) -> tuple[CodeFile, ...]:
        self._backend.get_source_snapshot(scope, snapshot_id)
        try:
            with self._backend._connect() as connection:
                rows = connection.execute(
                    "SELECT file.* FROM source_structure_files AS file JOIN "
                    "source_structure_snapshots AS snapshot "
                    "ON snapshot.snapshot_id = file.snapshot_id "
                    "WHERE file.snapshot_id = ? AND snapshot.owner_id = ? "
                    "AND snapshot.workspace_id IS ? AND snapshot.project_id = ? "
                    "ORDER BY file.relative_path ASC",
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
            CodeFile(snapshot_id, row["relative_path"], row["content_digest"]) for row in rows
        )

    def get_file(
        self, scope: MemoryScope, snapshot_id: CodeSnapshotId, relative_path: str
    ) -> CodeFile | None:
        self._backend.get_source_snapshot(scope, snapshot_id)
        try:
            with self._backend._connect() as connection:
                row = connection.execute(
                    "SELECT file.* FROM source_structure_files AS file JOIN "
                    "source_structure_snapshots AS snapshot "
                    "ON snapshot.snapshot_id = file.snapshot_id "
                    "WHERE file.snapshot_id = ? AND snapshot.owner_id = ? "
                    "AND snapshot.workspace_id IS ? AND snapshot.project_id = ? "
                    "AND file.relative_path = ?",
                    (
                        str(snapshot_id),
                        str(scope.owner_id),
                        _maybe(scope.workspace_id),
                        str(scope.project_id),
                        relative_path,
                    ),
                ).fetchone()
        except sqlite3.Error as error:
            raise SourceIndexStorageFailure("source index storage operation failed") from error
        return (
            None
            if row is None
            else CodeFile(snapshot_id, row["relative_path"], row["content_digest"])
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
        terms = source_search_terms(query)
        if not terms or limit < 1:
            return ()
        self._backend.get_source_snapshot(scope, snapshot_id)
        escaped_terms = tuple(
            term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") for term in terms
        )
        term_clauses = " AND ".join(
            "(lower(symbol.qualified_name) LIKE ? ESCAPE '\\' "
            "OR lower(symbol.relative_path) LIKE ? ESCAPE '\\')"
            for _ in escaped_terms
        )
        query_values = tuple(value for term in escaped_terms for value in (f"%{term}%",) * 2)
        candidate_limit = min(limit * 8, 2048)
        try:
            with self._backend._connect() as connection:
                rows = connection.execute(
                    "SELECT symbol.* FROM source_structure_symbols AS symbol JOIN "
                    "source_structure_snapshots AS snapshot "
                    "ON snapshot.snapshot_id = symbol.snapshot_id "
                    "WHERE symbol.snapshot_id = ? AND snapshot.owner_id = ? "
                    "AND snapshot.workspace_id IS ? AND snapshot.project_id = ? "
                    "AND "
                    + term_clauses
                    + " ORDER BY symbol.relative_path ASC, symbol.line_number ASC, "
                    "symbol.qualified_name ASC, symbol.symbol_id ASC LIMIT ?",
                    (
                        str(snapshot_id),
                        str(scope.owner_id),
                        _maybe(scope.workspace_id),
                        str(scope.project_id),
                        *query_values,
                        candidate_limit,
                    ),
                ).fetchall()
        except sqlite3.Error as error:
            raise SourceIndexStorageFailure("source index storage operation failed") from error
        return tuple(
            sorted(
                (
                    symbol
                    for symbol in self._symbols_from_rows(snapshot_id, rows)
                    if source_symbol_matches(symbol, terms)
                ),
                key=lambda symbol: source_symbol_rank(symbol, query, terms),
            )
        )[:limit]

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
