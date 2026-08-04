"""Storage-independent dbt manifest ingestion and bounded lineage queries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import PurePosixPath
from typing import Protocol

from mnemo_memory.packages.domain.dbt_manifest import (
    ArtifactCurrentness,
    DbtLineageEdge,
    DbtManifestArtifact,
    DbtManifestError,
    DbtManifestNode,
    DbtManifestSnapshot,
    DbtNodeId,
    SourceStateFingerprint,
)
from mnemo_memory.packages.domain.identifiers import DbtSnapshotId
from mnemo_memory.packages.domain.models import MemoryScope
from mnemo_memory.packages.storage.contracts import (
    ActiveSnapshotConflict,
    ManifestNodeNotFound,
    ManifestSnapshotNotFound,
    ProjectIndexRepository,
    ProjectIndexRepositoryError,
)


class DbtManifestParserPort(Protocol):
    def parse_for_ingestion(
        self,
        raw: bytes | str,
        *,
        scope: MemoryScope,
        source_identity: str,
        ingested_at: datetime,
        source_state: SourceStateFingerprint | None,
    ) -> DbtManifestArtifact: ...


class DbtApplicationError(Exception):
    """Safe, storage-neutral application outcome for dbt project intelligence."""


class DbtApplicationNotFound(DbtApplicationError):
    pass


class DbtApplicationAmbiguous(DbtApplicationError):
    """More than one manifest node claims one requested file identity."""

    pass


class DbtApplicationConflict(DbtApplicationError):
    pass


class DbtApplicationInvalidManifest(DbtApplicationError):
    pass


class DbtApplicationStorageFailure(DbtApplicationError):
    pass


class LineageDirection(str, Enum):
    UPSTREAM = "upstream"
    DOWNSTREAM = "downstream"


@dataclass(frozen=True, slots=True)
class IngestManifest:
    scope: MemoryScope
    raw_manifest: bytes | str
    source_identity: str
    ingested_at: datetime
    expected_active_snapshot_id: DbtSnapshotId | None = None
    source_state: SourceStateFingerprint | None = None


@dataclass(frozen=True, slots=True)
class IngestManifestResult:
    snapshot: DbtManifestSnapshot
    idempotent: bool


@dataclass(frozen=True, slots=True)
class QueryLineage:
    scope: MemoryScope
    unique_id: DbtNodeId
    direction: LineageDirection
    transitive: bool = True
    maximum_depth: int | None = None
    maximum_nodes: int = 5_000
    maximum_edges: int = 10_000
    snapshot_id: DbtSnapshotId | None = None
    include_disabled: bool = True
    current_content_digest: str | None = None
    current_source_state: SourceStateFingerprint | None = None

    def __post_init__(self) -> None:
        if self.maximum_depth is not None and self.maximum_depth < 0:
            raise ValueError("maximum_depth must be non-negative")
        if self.maximum_nodes < 1 or self.maximum_edges < 1:
            raise ValueError("lineage result limits must be positive")


@dataclass(frozen=True, slots=True)
class ResolveManifestFile:
    """Resolve one canonical manifest relative file identity in one scoped snapshot."""

    scope: MemoryScope
    original_file_path: str
    snapshot_id: DbtSnapshotId | None = None

    def __post_init__(self) -> None:
        _validate_manifest_relative_path(self.original_file_path)


@dataclass(frozen=True, slots=True)
class ResolvedManifestFile:
    snapshot: DbtManifestSnapshot
    node: DbtManifestNode


@dataclass(frozen=True, slots=True)
class GetActiveManifestStatus:
    scope: MemoryScope
    current_content_digest: str | None = None
    current_source_state: SourceStateFingerprint | None = None


@dataclass(frozen=True, slots=True)
class ManifestStatus:
    snapshot: DbtManifestSnapshot | None
    currentness: ArtifactCurrentness
    reason: str


@dataclass(frozen=True, slots=True)
class LineageNodeResult:
    node: DbtManifestNode
    depth: int


@dataclass(frozen=True, slots=True)
class LineageQueryResult:
    snapshot: DbtManifestSnapshot
    start_node: DbtManifestNode
    direction: LineageDirection
    transitive: bool
    nodes: tuple[LineageNodeResult, ...]
    edges: tuple[DbtLineageEdge, ...]
    truncated: bool
    truncation_reason: str | None
    currentness: ArtifactCurrentness
    currentness_reason: str


class DbtManifestApplicationService:
    """Coordinates authoritative parsing with immutable scoped snapshot storage."""

    def __init__(
        self, repository: ProjectIndexRepository, parser: DbtManifestParserPort | None = None
    ) -> None:
        self._repository = repository
        self._parser = parser

    def ingest(self, command: IngestManifest) -> IngestManifestResult:
        if self._parser is None:
            raise DbtApplicationInvalidManifest("dbt manifest ingestion parser is unavailable")
        try:
            artifact = self._parser.parse_for_ingestion(
                command.raw_manifest,
                scope=command.scope,
                source_identity=command.source_identity,
                ingested_at=command.ingested_at,
                source_state=command.source_state,
            )
        except (DbtManifestError, TypeError, ValueError) as error:
            raise DbtApplicationInvalidManifest("dbt manifest is invalid or unsupported") from error
        try:
            stored = self._repository.store_and_activate(
                artifact,
                DbtSnapshotId.new(),
                expected_active_snapshot_id=command.expected_active_snapshot_id,
            )
        except ActiveSnapshotConflict as error:
            raise DbtApplicationConflict(
                "active dbt manifest changed; retry with current snapshot"
            ) from error
        except ProjectIndexRepositoryError as error:
            raise DbtApplicationStorageFailure("dbt project index is unavailable") from error
        return IngestManifestResult(snapshot=stored.snapshot, idempotent=stored.idempotent)

    def get_active_status(self, query: GetActiveManifestStatus) -> ManifestStatus:
        try:
            snapshot = self._repository.get_active_snapshot(query.scope)
        except ProjectIndexRepositoryError as error:
            raise DbtApplicationStorageFailure("dbt project index is unavailable") from error
        if snapshot is None:
            return ManifestStatus(None, ArtifactCurrentness.UNKNOWN, "no active snapshot")
        state, reason = _currentness(
            snapshot, query.current_content_digest, query.current_source_state
        )
        return ManifestStatus(snapshot, state, reason)

    def resolve_file(self, query: ResolveManifestFile) -> ResolvedManifestFile:
        """Resolve an exact, scoped file only when its manifest ownership is unambiguous."""
        try:
            snapshot = (
                self._repository.get_snapshot(query.scope, query.snapshot_id)
                if query.snapshot_id is not None
                else self._repository.get_active_snapshot(query.scope)
            )
            if snapshot is None:
                raise ManifestSnapshotNotFound()
            matches = self._repository.find_nodes_by_original_file_path(
                query.scope, snapshot.snapshot_id, query.original_file_path
            )
        except (ManifestSnapshotNotFound, ManifestNodeNotFound) as error:
            raise DbtApplicationNotFound(
                "dbt manifest file was not found in the authorized scope"
            ) from error
        except ProjectIndexRepositoryError as error:
            raise DbtApplicationStorageFailure("dbt project index is unavailable") from error
        if not matches:
            raise DbtApplicationNotFound("dbt manifest file was not found in the authorized scope")
        if len(matches) != 1:
            raise DbtApplicationAmbiguous("dbt manifest file maps to multiple nodes")
        return ResolvedManifestFile(snapshot, matches[0])

    def query(self, query: QueryLineage) -> LineageQueryResult:
        try:
            snapshot = (
                self._repository.get_snapshot(query.scope, query.snapshot_id)
                if query.snapshot_id is not None
                else self._repository.get_active_snapshot(query.scope)
            )
            if snapshot is None:
                raise ManifestSnapshotNotFound()
            start = self._repository.get_node(query.scope, snapshot.snapshot_id, query.unique_id)
        except (ManifestSnapshotNotFound, ManifestNodeNotFound) as error:
            raise DbtApplicationNotFound(
                "dbt lineage node was not found in the authorized scope"
            ) from error
        except ProjectIndexRepositoryError as error:
            raise DbtApplicationStorageFailure("dbt project index is unavailable") from error

        nodes, edges, truncated, reason = self._traverse(query, snapshot, start)
        currentness, currentness_reason = _currentness(
            snapshot, query.current_content_digest, query.current_source_state
        )
        return LineageQueryResult(
            snapshot=snapshot,
            start_node=start,
            direction=query.direction,
            transitive=query.transitive,
            nodes=nodes,
            edges=edges,
            truncated=truncated,
            truncation_reason=reason,
            currentness=currentness,
            currentness_reason=currentness_reason,
        )

    def _traverse(
        self, query: QueryLineage, snapshot: DbtManifestSnapshot, start: DbtManifestNode
    ) -> tuple[tuple[LineageNodeResult, ...], tuple[DbtLineageEdge, ...], bool, str | None]:
        visited = {start.unique_id}
        result: dict[DbtNodeId, LineageNodeResult] = {}
        result_edges: set[DbtLineageEdge] = set()
        frontier = {start.unique_id}
        depth = 0
        truncated = False
        reason: str | None = None
        maximum_depth = 1 if not query.transitive else query.maximum_depth
        while frontier:
            if maximum_depth is not None and depth >= maximum_depth:
                if frontier:
                    truncated, reason = True, "maximum depth reached"
                break
            parent_ids = tuple(sorted(frontier, key=str))
            adjacent = self._edges_batched(
                query.scope, snapshot.snapshot_id, parent_ids, query.direction
            )
            next_ids = {
                edge.parent_id if query.direction is LineageDirection.UPSTREAM else edge.child_id
                for edge in adjacent
                if (
                    edge.parent_id
                    if query.direction is LineageDirection.UPSTREAM
                    else edge.child_id
                )
                not in visited
            }
            if not next_ids:
                break
            available = self._nodes_batched(query.scope, snapshot.snapshot_id, next_ids)
            next_frontier: set[DbtNodeId] = set()
            for node_id in sorted(next_ids, key=str):
                node = available.get(node_id)
                if node is None:
                    raise DbtApplicationStorageFailure("dbt project index graph is inconsistent")
                if len(result) >= query.maximum_nodes:
                    truncated, reason = True, "maximum node count reached"
                    break
                visited.add(node_id)
                depth_value = depth + 1
                if query.include_disabled or node.enabled:
                    result[node_id] = LineageNodeResult(node, depth_value)
                    for edge in adjacent:
                        neighbor = (
                            edge.parent_id
                            if query.direction is LineageDirection.UPSTREAM
                            else edge.child_id
                        )
                        if neighbor == node_id:
                            if len(result_edges) >= query.maximum_edges:
                                truncated, reason = True, "maximum edge count reached"
                                break
                            result_edges.add(edge)
                next_frontier.add(node_id)
                if truncated:
                    break
            if truncated:
                break
            frontier = next_frontier
            depth += 1
        return (
            tuple(
                sorted(result.values(), key=lambda value: (value.depth, str(value.node.unique_id)))
            ),
            tuple(sorted(result_edges, key=lambda edge: (str(edge.parent_id), str(edge.child_id)))),
            truncated,
            reason,
        )

    def _nodes_batched(
        self, scope: MemoryScope, snapshot_id: DbtSnapshotId, ids: set[DbtNodeId]
    ) -> dict[DbtNodeId, DbtManifestNode]:
        found: dict[DbtNodeId, DbtManifestNode] = {}
        ordered = tuple(sorted(ids, key=str))
        for index in range(0, len(ordered), 500):
            try:
                rows = self._repository.get_nodes(scope, snapshot_id, ordered[index : index + 500])
            except ProjectIndexRepositoryError as error:
                raise DbtApplicationStorageFailure("dbt project index is unavailable") from error
            found.update({row.unique_id: row for row in rows})
        return found

    def _edges_batched(
        self,
        scope: MemoryScope,
        snapshot_id: DbtSnapshotId,
        ids: tuple[DbtNodeId, ...],
        direction: LineageDirection,
    ) -> tuple[DbtLineageEdge, ...]:
        result: list[DbtLineageEdge] = []
        for index in range(0, len(ids), 500):
            try:
                rows = (
                    self._repository.get_upstream_edges(
                        scope, snapshot_id, ids[index : index + 500]
                    )
                    if direction is LineageDirection.UPSTREAM
                    else self._repository.get_downstream_edges(
                        scope, snapshot_id, ids[index : index + 500]
                    )
                )
            except ProjectIndexRepositoryError as error:
                raise DbtApplicationStorageFailure("dbt project index is unavailable") from error
            result.extend(rows)
        return tuple(sorted(result, key=lambda edge: (str(edge.parent_id), str(edge.child_id))))


def _currentness(
    snapshot: DbtManifestSnapshot,
    current_content_digest: str | None,
    current_source_state: SourceStateFingerprint | None,
) -> tuple[ArtifactCurrentness, str]:
    if current_content_digest is not None:
        if current_content_digest == snapshot.metadata.content_digest:
            return ArtifactCurrentness.CURRENT, "manifest content digest matches"
        return ArtifactCurrentness.STALE, "manifest content digest differs"
    stored = snapshot.metadata.source_state
    if current_source_state is None or stored is None:
        return ArtifactCurrentness.UNKNOWN, "no comparable current source-state evidence"
    if current_source_state.working_tree_fingerprint and stored.working_tree_fingerprint:
        if current_source_state.working_tree_fingerprint == stored.working_tree_fingerprint:
            return ArtifactCurrentness.CURRENT, "working-tree fingerprint matches"
        return ArtifactCurrentness.STALE, "working-tree fingerprint differs"
    if current_source_state.git_commit and stored.git_commit:
        if current_source_state.dirty is False and stored.dirty is False:
            if current_source_state.git_commit == stored.git_commit:
                return ArtifactCurrentness.CURRENT, "clean Git commit matches"
            return ArtifactCurrentness.STALE, "Git commit differs"
        if current_source_state.git_commit != stored.git_commit:
            return ArtifactCurrentness.STALE, "Git commit differs"
    return ArtifactCurrentness.UNKNOWN, "source-state evidence is not safely comparable"


def _validate_manifest_relative_path(value: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 512 or "\\" in value:
        raise ValueError("manifest file path must be a bounded relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value != path.as_posix():
        raise ValueError("manifest file path must be a canonical relative path")
