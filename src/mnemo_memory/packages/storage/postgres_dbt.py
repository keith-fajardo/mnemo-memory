"""PostgreSQL implementation of immutable team dbt manifest projections."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import cast

from mnemo_memory.packages.domain import (
    ArtifactCurrentness,
    DbtArtifactMetadata,
    DbtLineageEdge,
    DbtManifestArtifact,
    DbtManifestNode,
    DbtManifestSnapshot,
    DbtNodeId,
    DbtResourceType,
    DbtSnapshotId,
    EvidenceReference,
    LineageEdgeType,
    MemoryScope,
    OwnerId,
    ScopeLevel,
    SourceStateFingerprint,
    WorkspaceId,
)
from mnemo_memory.packages.policy import TeamOperation

from .contracts import (
    ActiveSnapshotConflict,
    InvalidManifestGraph,
    InvalidManifestSnapshotScope,
    ManifestNodeNotFound,
    ManifestSnapshotNotFound,
    ManifestSnapshotPage,
    ManifestSnapshotStoreResult,
    ProjectIndexRepositoryError,
    ProjectIndexStorageFailure,
)
from .postgres import PostgreSQLConnectionFactory, PostgreSQLCursor

_SNAPSHOT_COLUMNS = (
    "snapshot_id::text, schema_version, dbt_version, generated_at, invocation_id, project_name, "
    "content_digest, normalized_graph_digest, source_identity, ingested_at, source_state::text, "
    "currentness, node_count, edge_count, is_active"
)
_NODE_COLUMNS = (
    "unique_id, resource_type, raw_resource_type, package_name, name, alias, database_name, "
    "schema_name, relation_name, original_file_path, patch_path, enabled, checksum, tags::text, "
    "description, dependency_ids::text, macro_dependency_ids::text, evidence::text"
)
_EDGE_COLUMNS = "parent_id, child_id, edge_type, evidence::text, artifact_digest"


class PostgreSQLProjectIndexRepository:
    """One principal/workspace-bound immutable dbt project-index repository."""

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
                "SELECT last_synced_at FROM mnemo_team.dbt_manifest_sync_status WHERE "
                "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s",
                self._scope_values(scope),
            )
            row = cursor.fetchone()
            return None if row is None else cast(datetime, row[0])

    def store_and_activate(
        self,
        artifact: DbtManifestArtifact,
        snapshot_id: DbtSnapshotId,
        *,
        expected_active_snapshot_id: DbtSnapshotId | None = None,
    ) -> ManifestSnapshotStoreResult:
        if not isinstance(artifact, DbtManifestArtifact):
            raise TypeError("manifest artifact is invalid")
        if not isinstance(snapshot_id, DbtSnapshotId):
            raise TypeError("snapshot_id must be a DbtSnapshotId")
        scope = artifact.scope
        self._require_scope(scope)
        node_ids = {node.unique_id for node in artifact.nodes}
        if any(
            edge.parent_id not in node_ids or edge.child_id not in node_ids
            for edge in artifact.edges
        ):
            raise InvalidManifestGraph("manifest edge endpoint is absent from the snapshot")
        observed_at = datetime.now(UTC)
        with self._transaction(TeamOperation.CONTRIBUTE) as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"mnemo-dbt:{self._workspace_id}:{scope.project_id}",),
            )
            active = self._active_snapshot(cursor, scope)
            active_id = None if active is None else active.snapshot_id
            if expected_active_snapshot_id != active_id:
                raise ActiveSnapshotConflict("expected active snapshot is not current")
            cursor.execute(
                "SELECT " + _SNAPSHOT_COLUMNS + " FROM mnemo_team.dbt_manifest_snapshots WHERE "
                "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s AND content_digest = %s",
                (*self._scope_values(scope), artifact.metadata.content_digest),
            )
            duplicate_row = cursor.fetchone()
            idempotent = duplicate_row is not None
            if duplicate_row is None:
                cursor.execute(
                    "SELECT 1 FROM mnemo_team.dbt_manifest_snapshots WHERE "
                    "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                    "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                    "AND snapshot_id = CAST(%s AS uuid)",
                    (*self._scope_values(scope), str(snapshot_id)),
                )
                if cursor.fetchone() is not None:
                    raise ActiveSnapshotConflict("manifest snapshot identity already exists")
                self._insert_artifact(cursor, artifact, snapshot_id)
                target = DbtManifestSnapshot(
                    snapshot_id,
                    scope,
                    artifact.metadata,
                    len(artifact.nodes),
                    len(artifact.edges),
                    False,
                )
            else:
                target = self._snapshot_from_row(duplicate_row, scope)
            if target.snapshot_id != active_id:
                cursor.execute(
                    "INSERT INTO mnemo_team.dbt_manifest_activations("
                    "workspace_id, project_id, owner_id, visibility, snapshot_id, activated_at) "
                    "VALUES (CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, "
                    "CAST(%s AS uuid), %s)",
                    (*self._scope_values(scope), str(target.snapshot_id), observed_at),
                )
                if active_id is not None:
                    cursor.execute(
                        "UPDATE mnemo_team.dbt_manifest_snapshots SET is_active = false WHERE "
                        "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                        "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                        "AND snapshot_id = CAST(%s AS uuid) AND is_active",
                        (*self._scope_values(scope), str(active_id)),
                    )
                    if cursor.rowcount != 1:
                        raise ProjectIndexStorageFailure("manifest activation changed concurrently")
                cursor.execute(
                    "UPDATE mnemo_team.dbt_manifest_snapshots SET is_active = true WHERE "
                    "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                    "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                    "AND snapshot_id = CAST(%s AS uuid) AND NOT is_active",
                    (*self._scope_values(scope), str(target.snapshot_id)),
                )
                if cursor.rowcount != 1:
                    raise ProjectIndexStorageFailure("manifest snapshot activation failed")
                target = DbtManifestSnapshot(
                    target.snapshot_id,
                    target.scope,
                    target.metadata,
                    target.node_count,
                    target.edge_count,
                    True,
                )
            cursor.execute(
                "INSERT INTO mnemo_team.dbt_manifest_sync_status("
                "workspace_id, project_id, owner_id, visibility, last_synced_at) VALUES ("
                "CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, %s) "
                "ON CONFLICT (workspace_id, project_id, owner_id, visibility) DO UPDATE "
                "SET last_synced_at = EXCLUDED.last_synced_at",
                (*self._scope_values(scope), observed_at),
            )
            return ManifestSnapshotStoreResult(target, idempotent)

    def get_snapshot(self, scope: MemoryScope, snapshot_id: DbtSnapshotId) -> DbtManifestSnapshot:
        self._require_scope(scope)
        if not isinstance(snapshot_id, DbtSnapshotId):
            raise TypeError("snapshot_id must be a DbtSnapshotId")
        with self._transaction(TeamOperation.READ) as cursor:
            return self._required_snapshot(cursor, scope, snapshot_id)

    def get_active_snapshot(self, scope: MemoryScope) -> DbtManifestSnapshot | None:
        self._require_scope(scope)
        with self._transaction(TeamOperation.READ) as cursor:
            return self._active_snapshot(cursor, scope)

    def latest_transition(
        self, scope: MemoryScope
    ) -> tuple[DbtManifestSnapshot, DbtManifestSnapshot] | None:
        self._require_scope(scope)
        with self._transaction(TeamOperation.READ) as cursor:
            cursor.execute(
                "SELECT "
                + ", ".join("snapshot." + column.strip() for column in _SNAPSHOT_COLUMNS.split(","))
                + " FROM mnemo_team.dbt_manifest_activations AS activation JOIN "
                "mnemo_team.dbt_manifest_snapshots AS snapshot ON "
                "snapshot.workspace_id = activation.workspace_id "
                "AND snapshot.snapshot_id = activation.snapshot_id WHERE "
                "activation.workspace_id = CAST(%s AS uuid) "
                "AND activation.project_id = CAST(%s AS uuid) "
                "AND activation.owner_id = CAST(%s AS uuid) AND activation.visibility = %s "
                "ORDER BY activation.activation_sequence DESC LIMIT 2",
                self._scope_values(scope),
            )
            rows = cursor.fetchall()
            if len(rows) < 2:
                return None
            return self._snapshot_from_row(rows[1], scope), self._snapshot_from_row(rows[0], scope)

    def get_node(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, unique_id: DbtNodeId
    ) -> DbtManifestNode:
        self._require_scope(scope)
        if not isinstance(unique_id, DbtNodeId):
            raise TypeError("unique_id must be a DbtNodeId")
        with self._transaction(TeamOperation.READ) as cursor:
            try:
                self._required_snapshot(cursor, scope, snapshot_id)
            except ManifestSnapshotNotFound as error:
                raise ManifestNodeNotFound("manifest node was not found") from error
            cursor.execute(
                "SELECT " + _NODE_COLUMNS + " FROM mnemo_team.dbt_manifest_nodes WHERE "
                "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                "AND snapshot_id = CAST(%s AS uuid) AND unique_id = %s",
                (*self._scope_values(scope), str(snapshot_id), str(unique_id)),
            )
            row = cursor.fetchone()
            if row is None:
                raise ManifestNodeNotFound("manifest node was not found")
            return self._node_from_row(row)

    def find_nodes_by_original_file_path(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, original_file_path: str
    ) -> tuple[DbtManifestNode, ...]:
        self._require_scope(scope)
        with self._transaction(TeamOperation.READ) as cursor:
            try:
                self._required_snapshot(cursor, scope, snapshot_id)
            except ManifestSnapshotNotFound:
                return ()
            cursor.execute(
                "SELECT " + _NODE_COLUMNS + " FROM mnemo_team.dbt_manifest_nodes WHERE "
                "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                "AND snapshot_id = CAST(%s AS uuid) AND original_file_path = %s "
                "ORDER BY unique_id ASC",
                (*self._scope_values(scope), str(snapshot_id), original_file_path),
            )
            return tuple(self._node_from_row(row) for row in cursor.fetchall())

    def iter_nodes(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId
    ) -> tuple[DbtManifestNode, ...]:
        self._require_scope(scope)
        with self._transaction(TeamOperation.READ) as cursor:
            self._required_snapshot(cursor, scope, snapshot_id)
            return self._nodes(cursor, scope, snapshot_id)

    def iter_edges(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId
    ) -> tuple[DbtLineageEdge, ...]:
        self._require_scope(scope)
        with self._transaction(TeamOperation.READ) as cursor:
            self._required_snapshot(cursor, scope, snapshot_id)
            return self._edges(cursor, scope, snapshot_id)

    def direct_upstream(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, unique_id: DbtNodeId
    ) -> tuple[DbtLineageEdge, ...]:
        self.get_node(scope, snapshot_id, unique_id)
        return self._selected_edges(scope, snapshot_id, "child_id", (unique_id,))

    def direct_downstream(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, unique_id: DbtNodeId
    ) -> tuple[DbtLineageEdge, ...]:
        self.get_node(scope, snapshot_id, unique_id)
        return self._selected_edges(scope, snapshot_id, "parent_id", (unique_id,))

    def get_nodes(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, unique_ids: tuple[DbtNodeId, ...]
    ) -> tuple[DbtManifestNode, ...]:
        self._require_scope(scope)
        with self._transaction(TeamOperation.READ) as cursor:
            self._required_snapshot(cursor, scope, snapshot_id)
            if not unique_ids:
                return ()
            values = tuple(dict.fromkeys(str(item) for item in unique_ids))
            placeholders = ", ".join("%s" for _ in values)
            cursor.execute(
                "SELECT " + _NODE_COLUMNS + " FROM mnemo_team.dbt_manifest_nodes WHERE "
                "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                "AND snapshot_id = CAST(%s AS uuid) AND unique_id IN ("
                + placeholders
                + ") ORDER BY unique_id ASC",
                (*self._scope_values(scope), str(snapshot_id), *values),
            )
            return tuple(self._node_from_row(row) for row in cursor.fetchall())

    def get_upstream_edges(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, child_ids: tuple[DbtNodeId, ...]
    ) -> tuple[DbtLineageEdge, ...]:
        return self._selected_edges(scope, snapshot_id, "child_id", child_ids)

    def get_downstream_edges(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, parent_ids: tuple[DbtNodeId, ...]
    ) -> tuple[DbtLineageEdge, ...]:
        return self._selected_edges(scope, snapshot_id, "parent_id", parent_ids)

    def list_snapshots(
        self, scope: MemoryScope, *, offset: int = 0, limit: int = 50
    ) -> ManifestSnapshotPage:
        self._require_scope(scope)
        if offset < 0 or limit < 1:
            raise ValueError("offset must be non-negative and limit must be positive")
        with self._transaction(TeamOperation.READ) as cursor:
            cursor.execute(
                "SELECT " + _SNAPSHOT_COLUMNS + " FROM mnemo_team.dbt_manifest_snapshots WHERE "
                "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                "ORDER BY ingested_at DESC, snapshot_id DESC OFFSET %s LIMIT %s",
                (*self._scope_values(scope), offset, limit + 1),
            )
            rows = cursor.fetchall()
            items = tuple(self._snapshot_from_row(row, scope) for row in rows[:limit])
            return ManifestSnapshotPage(items, offset + limit if len(rows) > limit else None)

    def _insert_artifact(
        self, cursor: PostgreSQLCursor, artifact: DbtManifestArtifact, snapshot_id: DbtSnapshotId
    ) -> None:
        scope_values = self._scope_values(artifact.scope)
        metadata = artifact.metadata
        source_state = None
        if metadata.source_state is not None:
            source_state = self._json(
                {
                    "git_commit": metadata.source_state.git_commit,
                    "working_tree_fingerprint": metadata.source_state.working_tree_fingerprint,
                    "dirty": metadata.source_state.dirty,
                    "target_name": metadata.source_state.target_name,
                }
            )
        cursor.execute(
            "INSERT INTO mnemo_team.dbt_manifest_snapshots("
            "workspace_id, project_id, owner_id, visibility, snapshot_id, schema_version, "
            "dbt_version, generated_at, invocation_id, project_name, content_digest, "
            "normalized_graph_digest, source_identity, ingested_at, source_state, currentness, "
            "deferred_resource_counts, node_count, edge_count) VALUES (CAST(%s AS uuid), "
            "CAST(%s AS uuid), CAST(%s AS uuid), %s, CAST(%s AS uuid), %s, %s, %s, %s, %s, "
            "%s, %s, %s, %s, CAST(%s AS jsonb), %s, CAST(%s AS jsonb), %s, %s)",
            (
                *scope_values,
                str(snapshot_id),
                metadata.schema_version,
                metadata.dbt_version,
                metadata.generated_at,
                metadata.invocation_id,
                metadata.project_name,
                metadata.content_digest,
                metadata.normalized_graph_digest,
                metadata.source_identity,
                metadata.ingested_at,
                source_state,
                metadata.currentness.value,
                self._json(list(artifact.deferred_resource_counts)),
                len(artifact.nodes),
                len(artifact.edges),
            ),
        )
        for node in artifact.nodes:
            cursor.execute(
                "INSERT INTO mnemo_team.dbt_manifest_nodes("
                "workspace_id, project_id, owner_id, visibility, snapshot_id, unique_id, "
                "resource_type, raw_resource_type, package_name, name, alias, database_name, "
                "schema_name, relation_name, original_file_path, patch_path, enabled, checksum, "
                "tags, description, dependency_ids, macro_dependency_ids, evidence) VALUES ("
                "CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, CAST(%s AS uuid), "
                "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CAST(%s AS jsonb), "
                "%s, CAST(%s AS jsonb), CAST(%s AS jsonb), CAST(%s AS jsonb))",
                (
                    *scope_values,
                    str(snapshot_id),
                    str(node.unique_id),
                    node.resource_type.value,
                    node.raw_resource_type,
                    node.package_name,
                    node.name,
                    node.alias,
                    node.database,
                    node.schema_name,
                    node.relation_name,
                    node.original_file_path,
                    node.patch_path,
                    node.enabled,
                    node.checksum,
                    self._json(list(node.tags)),
                    node.description,
                    self._json([str(item) for item in node.dependency_ids]),
                    self._json([str(item) for item in node.macro_dependency_ids]),
                    self._json(node.evidence.to_dict()),
                ),
            )
        for edge in artifact.edges:
            cursor.execute(
                "INSERT INTO mnemo_team.dbt_lineage_edges("
                "workspace_id, project_id, owner_id, visibility, snapshot_id, parent_id, "
                "child_id, edge_type, evidence, artifact_digest) VALUES (CAST(%s AS uuid), "
                "CAST(%s AS uuid), CAST(%s AS uuid), %s, CAST(%s AS uuid), %s, %s, %s, "
                "CAST(%s AS jsonb), %s)",
                (
                    *scope_values,
                    str(snapshot_id),
                    str(edge.parent_id),
                    str(edge.child_id),
                    edge.edge_type.value,
                    self._json(edge.evidence.to_dict()),
                    edge.artifact_digest,
                ),
            )

    def _active_snapshot(
        self, cursor: PostgreSQLCursor, scope: MemoryScope
    ) -> DbtManifestSnapshot | None:
        cursor.execute(
            "SELECT " + _SNAPSHOT_COLUMNS + " FROM mnemo_team.dbt_manifest_snapshots WHERE "
            "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s AND is_active",
            self._scope_values(scope),
        )
        row = cursor.fetchone()
        return None if row is None else self._snapshot_from_row(row, scope)

    def _required_snapshot(
        self, cursor: PostgreSQLCursor, scope: MemoryScope, snapshot_id: DbtSnapshotId
    ) -> DbtManifestSnapshot:
        cursor.execute(
            "SELECT " + _SNAPSHOT_COLUMNS + " FROM mnemo_team.dbt_manifest_snapshots WHERE "
            "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND snapshot_id = CAST(%s AS uuid)",
            (*self._scope_values(scope), str(snapshot_id)),
        )
        row = cursor.fetchone()
        if row is None:
            raise ManifestSnapshotNotFound("manifest snapshot was not found")
        return self._snapshot_from_row(row, scope)

    def _nodes(
        self, cursor: PostgreSQLCursor, scope: MemoryScope, snapshot_id: DbtSnapshotId
    ) -> tuple[DbtManifestNode, ...]:
        cursor.execute(
            "SELECT " + _NODE_COLUMNS + " FROM mnemo_team.dbt_manifest_nodes WHERE "
            "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND snapshot_id = CAST(%s AS uuid) ORDER BY unique_id ASC",
            (*self._scope_values(scope), str(snapshot_id)),
        )
        return tuple(self._node_from_row(row) for row in cursor.fetchall())

    def _edges(
        self, cursor: PostgreSQLCursor, scope: MemoryScope, snapshot_id: DbtSnapshotId
    ) -> tuple[DbtLineageEdge, ...]:
        cursor.execute(
            "SELECT " + _EDGE_COLUMNS + " FROM mnemo_team.dbt_lineage_edges WHERE "
            "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND snapshot_id = CAST(%s AS uuid) "
            "ORDER BY parent_id ASC, child_id ASC, edge_type ASC",
            (*self._scope_values(scope), str(snapshot_id)),
        )
        return tuple(self._edge_from_row(row) for row in cursor.fetchall())

    def _selected_edges(
        self,
        scope: MemoryScope,
        snapshot_id: DbtSnapshotId,
        column: str,
        unique_ids: tuple[DbtNodeId, ...],
    ) -> tuple[DbtLineageEdge, ...]:
        self._require_scope(scope)
        with self._transaction(TeamOperation.READ) as cursor:
            self._required_snapshot(cursor, scope, snapshot_id)
            if not unique_ids:
                return ()
            values = tuple(dict.fromkeys(str(item) for item in unique_ids))
            placeholders = ", ".join("%s" for _ in values)
            cursor.execute(
                "SELECT " + _EDGE_COLUMNS + " FROM mnemo_team.dbt_lineage_edges WHERE "
                "workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
                "AND snapshot_id = CAST(%s AS uuid) AND "
                + column
                + " IN ("
                + placeholders
                + ") ORDER BY parent_id ASC, child_id ASC, edge_type ASC",
                (*self._scope_values(scope), str(snapshot_id), *values),
            )
            return tuple(self._edge_from_row(row) for row in cursor.fetchall())

    @classmethod
    def _snapshot_from_row(cls, row: Sequence[object], scope: MemoryScope) -> DbtManifestSnapshot:
        source_state_value = cls._json_value(row[10])
        source_state = None
        if source_state_value is not None:
            if not isinstance(source_state_value, Mapping):
                raise ProjectIndexStorageFailure("stored manifest source state is invalid")
            source_state = SourceStateFingerprint(
                cls._optional_string(source_state_value.get("git_commit")),
                cls._optional_string(source_state_value.get("working_tree_fingerprint")),
                cls._optional_bool(source_state_value.get("dirty")),
                cls._optional_string(source_state_value.get("target_name")),
            )
        metadata = DbtArtifactMetadata(
            str(row[1]),
            cls._optional_string(row[2]),
            None if row[3] is None else cast(datetime, row[3]),
            cls._optional_string(row[4]),
            cls._optional_string(row[5]),
            str(row[6]),
            str(row[7]),
            str(row[8]),
            cast(datetime, row[9]),
            source_state,
            ArtifactCurrentness(str(row[11])),
        )
        return DbtManifestSnapshot(
            DbtSnapshotId.from_string(str(row[0])),
            scope,
            metadata,
            int(str(row[12])),
            int(str(row[13])),
            bool(row[14]),
        )

    @classmethod
    def _node_from_row(cls, row: Sequence[object]) -> DbtManifestNode:
        tags = cls._string_list(row[13], "tags")
        dependencies = cls._string_list(row[15], "dependency_ids")
        macro_dependencies = cls._string_list(row[16], "macro_dependency_ids")
        evidence_value = cls._json_value(row[17])
        if not isinstance(evidence_value, Mapping):
            raise ProjectIndexStorageFailure("stored manifest node evidence is invalid")
        return DbtManifestNode(
            DbtNodeId(str(row[0])),
            DbtResourceType(str(row[1])),
            str(row[2]),
            str(row[3]),
            str(row[4]),
            cls._optional_string(row[5]),
            cls._optional_string(row[6]),
            cls._optional_string(row[7]),
            cls._optional_string(row[8]),
            cls._optional_string(row[9]),
            cls._optional_string(row[10]),
            bool(row[11]),
            cls._optional_string(row[12]),
            tags,
            str(row[14]),
            tuple(DbtNodeId(item) for item in dependencies),
            tuple(DbtNodeId(item) for item in macro_dependencies),
            EvidenceReference.from_dict(evidence_value),
        )

    @classmethod
    def _edge_from_row(cls, row: Sequence[object]) -> DbtLineageEdge:
        evidence_value = cls._json_value(row[3])
        if not isinstance(evidence_value, Mapping):
            raise ProjectIndexStorageFailure("stored manifest edge evidence is invalid")
        return DbtLineageEdge(
            DbtNodeId(str(row[0])),
            DbtNodeId(str(row[1])),
            LineageEdgeType(str(row[2])),
            EvidenceReference.from_dict(evidence_value),
            str(row[4]),
        )

    @classmethod
    def _string_list(cls, value: object, name: str) -> tuple[str, ...]:
        parsed = cls._json_value(value)
        if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
            raise ProjectIndexStorageFailure(f"stored manifest {name} is invalid")
        return tuple(parsed)

    @staticmethod
    def _json(value: object) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    @staticmethod
    def _json_value(value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError as error:
                raise ProjectIndexStorageFailure("stored manifest JSON is invalid") from error
        return value

    @staticmethod
    def _optional_string(value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ProjectIndexStorageFailure("stored manifest string is invalid")
        return value

    @staticmethod
    def _optional_bool(value: object) -> bool | None:
        if value is None:
            return None
        if not isinstance(value, bool):
            raise ProjectIndexStorageFailure("stored manifest boolean is invalid")
        return value

    def _require_scope(self, scope: MemoryScope) -> None:
        if (
            not isinstance(scope, MemoryScope)
            or scope.level is not ScopeLevel.PROJECT
            or scope.workspace_id != self._workspace_id
            or scope.project_id is None
        ):
            raise InvalidManifestSnapshotScope(
                "team dbt snapshots require the bound exact project scope"
            )

    @staticmethod
    def _scope_values(scope: MemoryScope) -> tuple[str, str, str, str]:
        if scope.workspace_id is None or scope.project_id is None:
            raise InvalidManifestSnapshotScope("team dbt snapshots require project scope")
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
            raise ProjectIndexStorageFailure("project index database connection failed") from error
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
            raise ProjectIndexStorageFailure("project index database operation failed") from error
        finally:
            cursor.close()
            connection.close()
