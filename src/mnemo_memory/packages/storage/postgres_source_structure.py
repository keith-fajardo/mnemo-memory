"""PostgreSQL implementation of rebuildable team source-structure projections."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import cast

from mnemo_memory.packages.domain import (
    CodeEdge,
    CodeEdgeKind,
    CodeFile,
    CodeSnapshot,
    CodeSnapshotId,
    CodeStructureArtifact,
    CodeSymbol,
    CodeSymbolId,
    CodeSymbolKind,
    MemoryScope,
    OwnerId,
    ScopeLevel,
    WorkspaceId,
)
from mnemo_memory.packages.policy import TeamOperation

from .contracts import (
    ProjectIndexRepositoryError,
    SourceIndexStorageFailure,
    SourceSnapshotNotFound,
    SourceSnapshotStoreResult,
)
from .postgres import PostgreSQLConnectionFactory, PostgreSQLCursor
from .source_search import source_search_terms, source_symbol_matches, source_symbol_rank

_SNAPSHOT_COLUMNS = "snapshot_id::text, source_digest, file_count, symbol_count, edge_count"
_SYMBOL_COLUMNS = "symbol_id::text, relative_path, qualified_name, symbol_kind, line_number"
_EDGE_COLUMNS = "source_symbol_id::text, target, edge_kind, target_symbol_id::text"


class PostgreSQLSourceStructureRepository:
    """One principal/workspace-bound immutable source projection repository."""

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

    def last_sync_at(self, scope: MemoryScope) -> datetime | None:
        self._require_scope(scope)
        with self._transaction(TeamOperation.READ) as cursor:
            cursor.execute(
                "SELECT last_synced_at FROM mnemo_team.source_structure_sync_status WHERE "
                "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s",
                self._scope_values(scope),
            )
            row = cursor.fetchone()
            return None if row is None else cast(datetime, row[0])

    def store_and_activate(self, artifact: CodeStructureArtifact) -> SourceSnapshotStoreResult:
        if not isinstance(artifact, CodeStructureArtifact):
            raise TypeError("source structure artifact is invalid")
        scope = artifact.snapshot.scope
        self._require_scope(scope)
        observed_at = datetime.now(UTC)
        with self._transaction(TeamOperation.CONTRIBUTE) as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"mnemo-source:{self._workspace_id}:{scope.project_id}",),
            )
            cursor.execute(
                "SELECT " + _SNAPSHOT_COLUMNS + " FROM mnemo_team.source_structure_snapshots WHERE "
                "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s AND source_digest = %s",
                (*self._scope_values(scope), artifact.snapshot.source_digest),
            )
            duplicate_row = cursor.fetchone()
            idempotent = duplicate_row is not None
            if duplicate_row is None:
                self._insert_artifact(cursor, artifact)
                target = artifact.snapshot
            else:
                target = self._snapshot_from_row(duplicate_row, scope)
            cursor.execute(
                "SELECT snapshot_id::text FROM mnemo_team.source_structure_snapshots WHERE "
                "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s AND is_active",
                self._scope_values(scope),
            )
            active_row = cursor.fetchone()
            active_id = (
                None if active_row is None else CodeSnapshotId.from_string(str(active_row[0]))
            )
            if active_id != target.snapshot_id:
                cursor.execute(
                    "INSERT INTO mnemo_team.source_snapshot_activations("
                    "workspace_id, project_id, owner_id, visibility, snapshot_id, activated_at) "
                    "VALUES (CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, "
                    "CAST(%s AS uuid), %s)",
                    (*self._scope_values(scope), str(target.snapshot_id), observed_at),
                )
                if active_id is not None:
                    cursor.execute(
                        "UPDATE mnemo_team.source_structure_snapshots SET is_active = false WHERE "
                        "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                        "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                        "AND snapshot_id = CAST(%s AS uuid) AND is_active",
                        (*self._scope_values(scope), str(active_id)),
                    )
                    if cursor.rowcount != 1:
                        raise SourceIndexStorageFailure("source activation changed concurrently")
                cursor.execute(
                    "UPDATE mnemo_team.source_structure_snapshots SET is_active = true WHERE "
                    "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                    "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                    "AND snapshot_id = CAST(%s AS uuid) AND NOT is_active",
                    (*self._scope_values(scope), str(target.snapshot_id)),
                )
                if cursor.rowcount != 1:
                    raise SourceIndexStorageFailure("source snapshot activation failed")
            cursor.execute(
                "INSERT INTO mnemo_team.source_structure_sync_status("
                "workspace_id, project_id, owner_id, visibility, last_synced_at) VALUES ("
                "CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, %s) "
                "ON CONFLICT (workspace_id, project_id, owner_id, visibility) DO UPDATE "
                "SET last_synced_at = EXCLUDED.last_synced_at",
                (*self._scope_values(scope), observed_at),
            )
            return SourceSnapshotStoreResult(target, idempotent)

    def get_active_snapshot(self, scope: MemoryScope) -> CodeSnapshot | None:
        self._require_scope(scope)
        with self._transaction(TeamOperation.READ) as cursor:
            cursor.execute(
                "SELECT " + _SNAPSHOT_COLUMNS + " FROM mnemo_team.source_structure_snapshots WHERE "
                "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s AND is_active",
                self._scope_values(scope),
            )
            row = cursor.fetchone()
            return None if row is None else self._snapshot_from_row(row, scope)

    def get_snapshot(self, scope: MemoryScope, snapshot_id: CodeSnapshotId) -> CodeSnapshot:
        self._require_scope(scope)
        if not isinstance(snapshot_id, CodeSnapshotId):
            raise TypeError("snapshot_id must be a CodeSnapshotId")
        with self._transaction(TeamOperation.READ) as cursor:
            return self._required_snapshot(cursor, scope, snapshot_id)

    def latest_transition(self, scope: MemoryScope) -> tuple[CodeSnapshot, CodeSnapshot] | None:
        history = self.list_activation_history(scope, limit=2)
        return None if len(history) < 2 else (history[1], history[0])

    def list_activation_history(
        self, scope: MemoryScope, *, limit: int = 20
    ) -> tuple[CodeSnapshot, ...]:
        self._require_scope(scope)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("source snapshot history limit must be between 1 and 100")
        with self._transaction(TeamOperation.READ) as cursor:
            cursor.execute(
                "SELECT "
                + ", ".join("snapshot." + item.strip() for item in _SNAPSHOT_COLUMNS.split(","))
                + " FROM mnemo_team.source_snapshot_activations AS activation JOIN "
                "mnemo_team.source_structure_snapshots AS snapshot ON "
                "snapshot.workspace_id = activation.workspace_id "
                "AND snapshot.snapshot_id = activation.snapshot_id WHERE "
                "activation.workspace_id = CAST(%s AS uuid) "
                "AND activation.project_id = CAST(%s AS uuid) "
                "AND activation.owner_id = CAST(%s AS uuid) AND activation.visibility = %s "
                "ORDER BY activation.activation_sequence DESC LIMIT %s",
                (*self._scope_values(scope), limit),
            )
            return tuple(self._snapshot_from_row(row, scope) for row in cursor.fetchall())

    def iter_symbols(
        self, scope: MemoryScope, snapshot_id: CodeSnapshotId
    ) -> tuple[CodeSymbol, ...]:
        self._require_scope(scope)
        with self._transaction(TeamOperation.READ) as cursor:
            self._required_snapshot(cursor, scope, snapshot_id)
            rows = self._symbol_rows(cursor, scope, snapshot_id)
            return tuple(self._symbol_from_row(row, snapshot_id) for row in rows)

    def iter_files(self, scope: MemoryScope, snapshot_id: CodeSnapshotId) -> tuple[CodeFile, ...]:
        self._require_scope(scope)
        with self._transaction(TeamOperation.READ) as cursor:
            self._required_snapshot(cursor, scope, snapshot_id)
            cursor.execute(
                "SELECT relative_path, content_digest FROM mnemo_team.source_structure_files "
                "WHERE workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                "AND snapshot_id = CAST(%s AS uuid) ORDER BY relative_path ASC",
                (*self._scope_values(scope), str(snapshot_id)),
            )
            return tuple(
                CodeFile(snapshot_id, str(row[0]), str(row[1])) for row in cursor.fetchall()
            )

    def get_file(
        self, scope: MemoryScope, snapshot_id: CodeSnapshotId, relative_path: str
    ) -> CodeFile | None:
        self._require_scope(scope)
        with self._transaction(TeamOperation.READ) as cursor:
            self._required_snapshot(cursor, scope, snapshot_id)
            cursor.execute(
                "SELECT relative_path, content_digest FROM mnemo_team.source_structure_files "
                "WHERE workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                "AND snapshot_id = CAST(%s AS uuid) AND relative_path = %s",
                (*self._scope_values(scope), str(snapshot_id), relative_path),
            )
            row = cursor.fetchone()
            return None if row is None else CodeFile(snapshot_id, str(row[0]), str(row[1]))

    def iter_edges(self, scope: MemoryScope, snapshot_id: CodeSnapshotId) -> tuple[CodeEdge, ...]:
        self._require_scope(scope)
        with self._transaction(TeamOperation.READ) as cursor:
            self._required_snapshot(cursor, scope, snapshot_id)
            rows = self._edge_rows(cursor, scope, snapshot_id)
            return tuple(self._edge_from_row(row, snapshot_id) for row in rows)

    def find_symbols(
        self, scope: MemoryScope, snapshot_id: CodeSnapshotId, query: str, *, limit: int
    ) -> tuple[CodeSymbol, ...]:
        self._require_scope(scope)
        terms = source_search_terms(query)
        if not terms or limit < 1:
            return ()
        candidate_limit = min(limit * 8, 2048)
        escaped = tuple(
            term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") for term in terms
        )
        clauses = " AND ".join(
            "(lower(qualified_name) LIKE %s ESCAPE '\\' "
            "OR lower(relative_path) LIKE %s ESCAPE '\\')"
            for _ in escaped
        )
        values = tuple(value for term in escaped for value in (f"%{term}%", f"%{term}%"))
        with self._transaction(TeamOperation.READ) as cursor:
            self._required_snapshot(cursor, scope, snapshot_id)
            cursor.execute(
                "SELECT " + _SYMBOL_COLUMNS + " FROM mnemo_team.source_structure_symbols WHERE "
                "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                "AND snapshot_id = CAST(%s AS uuid) AND "
                + clauses
                + " ORDER BY relative_path ASC, line_number ASC, qualified_name ASC, "
                "symbol_id ASC LIMIT %s",
                (*self._scope_values(scope), str(snapshot_id), *values, candidate_limit),
            )
            symbols = tuple(self._symbol_from_row(row, snapshot_id) for row in cursor.fetchall())
        return tuple(
            sorted(
                (symbol for symbol in symbols if source_symbol_matches(symbol, terms)),
                key=lambda symbol: source_symbol_rank(symbol, query, terms),
            )
        )[:limit]

    def module_symbols_for_paths(
        self, scope: MemoryScope, snapshot_id: CodeSnapshotId, relative_paths: tuple[str, ...]
    ) -> tuple[CodeSymbol, ...]:
        if not relative_paths:
            return ()
        return self._selected_symbols(
            scope, snapshot_id, "relative_path", tuple(dict.fromkeys(relative_paths)), modules=True
        )

    def symbols_by_ids(
        self, scope: MemoryScope, snapshot_id: CodeSnapshotId, symbol_ids: tuple[CodeSymbolId, ...]
    ) -> tuple[CodeSymbol, ...]:
        if not symbol_ids:
            return ()
        return self._selected_symbols(
            scope, snapshot_id, "symbol_id", tuple(dict.fromkeys(str(item) for item in symbol_ids))
        )

    def edges_from_symbols(
        self, scope: MemoryScope, snapshot_id: CodeSnapshotId, symbol_ids: tuple[CodeSymbolId, ...]
    ) -> tuple[CodeEdge, ...]:
        return self._selected_edges(scope, snapshot_id, "source_symbol_id", symbol_ids)

    def edges_to_symbols(
        self, scope: MemoryScope, snapshot_id: CodeSnapshotId, symbol_ids: tuple[CodeSymbolId, ...]
    ) -> tuple[CodeEdge, ...]:
        return self._selected_edges(scope, snapshot_id, "target_symbol_id", symbol_ids)

    def _insert_artifact(self, cursor: PostgreSQLCursor, artifact: CodeStructureArtifact) -> None:
        scope = artifact.snapshot.scope
        values = self._scope_values(scope)
        cursor.execute(
            "INSERT INTO mnemo_team.source_structure_snapshots("
            "workspace_id, project_id, owner_id, visibility, snapshot_id, source_digest, "
            "file_count, symbol_count, edge_count) VALUES (CAST(%s AS uuid), "
            "CAST(%s AS uuid), CAST(%s AS uuid), %s, CAST(%s AS uuid), %s, %s, %s, %s)",
            (
                *values,
                str(artifact.snapshot.snapshot_id),
                artifact.snapshot.source_digest,
                artifact.snapshot.file_count,
                artifact.snapshot.symbol_count,
                artifact.snapshot.edge_count,
            ),
        )
        for file_item in artifact.files:
            cursor.execute(
                "INSERT INTO mnemo_team.source_structure_files("
                "workspace_id, project_id, owner_id, visibility, snapshot_id, relative_path, "
                "content_digest) VALUES (CAST(%s AS uuid), CAST(%s AS uuid), "
                "CAST(%s AS uuid), %s, CAST(%s AS uuid), %s, %s)",
                (
                    *values,
                    str(file_item.snapshot_id),
                    file_item.relative_path,
                    file_item.content_digest,
                ),
            )
        for symbol in artifact.symbols:
            cursor.execute(
                "INSERT INTO mnemo_team.source_structure_symbols("
                "workspace_id, project_id, owner_id, visibility, snapshot_id, symbol_id, "
                "relative_path, qualified_name, symbol_kind, line_number) VALUES ("
                "CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, "
                "CAST(%s AS uuid), CAST(%s AS uuid), %s, %s, %s, %s)",
                (
                    *values,
                    str(symbol.snapshot_id),
                    str(symbol.symbol_id),
                    symbol.relative_path,
                    symbol.qualified_name,
                    symbol.kind.value,
                    symbol.line,
                ),
            )
        for sequence, edge in enumerate(artifact.edges):
            cursor.execute(
                "INSERT INTO mnemo_team.source_structure_edges("
                "workspace_id, project_id, owner_id, visibility, snapshot_id, edge_sequence, "
                "source_symbol_id, target, edge_kind, target_symbol_id) VALUES ("
                "CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, "
                "CAST(%s AS uuid), %s, CAST(%s AS uuid), %s, %s, CAST(%s AS uuid))",
                (
                    *values,
                    str(edge.snapshot_id),
                    sequence,
                    str(edge.source_symbol_id),
                    edge.target,
                    edge.kind.value,
                    None if edge.target_symbol_id is None else str(edge.target_symbol_id),
                ),
            )

    def _selected_symbols(
        self,
        scope: MemoryScope,
        snapshot_id: CodeSnapshotId,
        column: str,
        values: tuple[str, ...],
        *,
        modules: bool = False,
    ) -> tuple[CodeSymbol, ...]:
        self._require_scope(scope)
        placeholders = ", ".join("%s" for _ in values)
        with self._transaction(TeamOperation.READ) as cursor:
            self._required_snapshot(cursor, scope, snapshot_id)
            cursor.execute(
                "SELECT " + _SYMBOL_COLUMNS + " FROM mnemo_team.source_structure_symbols WHERE "
                "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                "AND snapshot_id = CAST(%s AS uuid) AND "
                + column
                + "::text IN ("
                + placeholders
                + ")"
                + (" AND symbol_kind = 'module'" if modules else "")
                + " ORDER BY relative_path ASC, line_number ASC, qualified_name ASC, symbol_id ASC",
                (
                    *self._scope_values(scope),
                    str(snapshot_id),
                    *values,
                ),
            )
            return tuple(self._symbol_from_row(row, snapshot_id) for row in cursor.fetchall())

    def _selected_edges(
        self,
        scope: MemoryScope,
        snapshot_id: CodeSnapshotId,
        column: str,
        symbol_ids: tuple[CodeSymbolId, ...],
    ) -> tuple[CodeEdge, ...]:
        if not symbol_ids:
            return ()
        self._require_scope(scope)
        with self._transaction(TeamOperation.READ) as cursor:
            self._required_snapshot(cursor, scope, snapshot_id)
            cursor.execute(
                "SELECT " + _EDGE_COLUMNS + " FROM mnemo_team.source_structure_edges WHERE "
                "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                "AND snapshot_id = CAST(%s AS uuid) AND " + column + " = ANY(CAST(%s AS uuid[])) "
                "ORDER BY source_symbol_id ASC, target ASC, edge_kind ASC, edge_sequence ASC",
                (
                    *self._scope_values(scope),
                    str(snapshot_id),
                    "{" + ",".join(str(item) for item in dict.fromkeys(symbol_ids)) + "}",
                ),
            )
            return tuple(self._edge_from_row(row, snapshot_id) for row in cursor.fetchall())

    def _required_snapshot(
        self, cursor: PostgreSQLCursor, scope: MemoryScope, snapshot_id: CodeSnapshotId
    ) -> CodeSnapshot:
        cursor.execute(
            "SELECT " + _SNAPSHOT_COLUMNS + " FROM mnemo_team.source_structure_snapshots WHERE "
            "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND snapshot_id = CAST(%s AS uuid)",
            (*self._scope_values(scope), str(snapshot_id)),
        )
        row = cursor.fetchone()
        if row is None:
            raise SourceSnapshotNotFound("source snapshot was not found")
        return self._snapshot_from_row(row, scope)

    def _symbol_rows(
        self, cursor: PostgreSQLCursor, scope: MemoryScope, snapshot_id: CodeSnapshotId
    ) -> Sequence[Sequence[object]]:
        cursor.execute(
            "SELECT " + _SYMBOL_COLUMNS + " FROM mnemo_team.source_structure_symbols WHERE "
            "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND snapshot_id = CAST(%s AS uuid) "
            "ORDER BY relative_path ASC, line_number ASC, qualified_name ASC, symbol_id ASC",
            (*self._scope_values(scope), str(snapshot_id)),
        )
        return cursor.fetchall()

    def _edge_rows(
        self, cursor: PostgreSQLCursor, scope: MemoryScope, snapshot_id: CodeSnapshotId
    ) -> Sequence[Sequence[object]]:
        cursor.execute(
            "SELECT " + _EDGE_COLUMNS + " FROM mnemo_team.source_structure_edges WHERE "
            "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND snapshot_id = CAST(%s AS uuid) "
            "ORDER BY source_symbol_id ASC, target ASC, edge_kind ASC, edge_sequence ASC",
            (*self._scope_values(scope), str(snapshot_id)),
        )
        return cursor.fetchall()

    @staticmethod
    def _snapshot_from_row(row: Sequence[object], scope: MemoryScope) -> CodeSnapshot:
        return CodeSnapshot(
            CodeSnapshotId.from_string(str(row[0])),
            scope,
            str(row[1]),
            int(str(row[2])),
            int(str(row[3])),
            int(str(row[4])),
        )

    @staticmethod
    def _symbol_from_row(row: Sequence[object], snapshot_id: CodeSnapshotId) -> CodeSymbol:
        return CodeSymbol(
            snapshot_id,
            CodeSymbolId.from_string(str(row[0])),
            str(row[1]),
            str(row[2]),
            CodeSymbolKind(str(row[3])),
            int(str(row[4])),
        )

    @staticmethod
    def _edge_from_row(row: Sequence[object], snapshot_id: CodeSnapshotId) -> CodeEdge:
        return CodeEdge(
            snapshot_id,
            CodeSymbolId.from_string(str(row[0])),
            str(row[1]),
            CodeEdgeKind(str(row[2])),
            None if row[3] is None else CodeSymbolId.from_string(str(row[3])),
        )

    def _require_scope(self, scope: MemoryScope) -> None:
        if (
            not isinstance(scope, MemoryScope)
            or scope.level is not ScopeLevel.PROJECT
            or scope.workspace_id != self._workspace_id
            or scope.project_id is None
        ):
            raise SourceIndexStorageFailure(
                "team source snapshots require the bound exact project scope"
            )

    @staticmethod
    def _scope_values(scope: MemoryScope) -> tuple[str, str, str, str]:
        if scope.workspace_id is None or scope.project_id is None:
            raise SourceIndexStorageFailure("team source snapshots require project scope")
        return (
            str(scope.workspace_id),
            str(scope.project_id),
            str(scope.owner_id),
            scope.visibility.value,
        )

    @contextmanager
    def _transaction(self, operation: TeamOperation) -> Iterator[PostgreSQLCursor]:
        try:
            connection = self._connection_factory()
            connection.autocommit = False
        except Exception as error:
            raise SourceIndexStorageFailure("source index database connection failed") from error
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
        except ProjectIndexRepositoryError:
            connection.rollback()
            raise
        except Exception as error:
            connection.rollback()
            raise SourceIndexStorageFailure("source index database operation failed") from error
        finally:
            cursor.close()
            connection.close()
