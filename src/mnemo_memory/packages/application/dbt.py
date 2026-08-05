"""Storage-independent dbt manifest ingestion and bounded lineage queries."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import PurePosixPath
from typing import Protocol

from mnemo_memory.packages.domain.dbt_artifacts import (
    DbtCatalogArtifact,
    DbtRunResultsArtifact,
    DbtSourceFreshnessArtifact,
    DbtSourceFreshnessResult,
    DbtSupplementalArtifactError,
)
from mnemo_memory.packages.domain.dbt_manifest import (
    ArtifactCurrentness,
    DbtLineageEdge,
    DbtManifestArtifact,
    DbtManifestError,
    DbtManifestNode,
    DbtManifestSnapshot,
    DbtNodeId,
    DbtResourceType,
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
    SupplementalArtifactConflict,
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


class DbtCatalogParserPort(Protocol):
    def parse_for_ingestion(
        self,
        raw: bytes | str,
        *,
        scope: MemoryScope,
        source_identity: str,
        ingested_at: datetime,
    ) -> DbtCatalogArtifact: ...


class DbtRunResultsParserPort(Protocol):
    def parse_for_ingestion(
        self,
        raw: bytes | str,
        *,
        scope: MemoryScope,
        source_identity: str,
        ingested_at: datetime,
    ) -> DbtRunResultsArtifact: ...


class DbtSourceFreshnessParserPort(Protocol):
    def parse_for_ingestion(
        self,
        raw: bytes | str,
        *,
        scope: MemoryScope,
        source_identity: str,
        ingested_at: datetime,
    ) -> DbtSourceFreshnessArtifact: ...


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
class IngestCatalog:
    scope: MemoryScope
    snapshot_id: DbtSnapshotId
    raw_catalog: bytes | str
    source_identity: str
    ingested_at: datetime


@dataclass(frozen=True, slots=True)
class IngestRunResults:
    scope: MemoryScope
    snapshot_id: DbtSnapshotId
    raw_run_results: bytes | str
    source_identity: str
    ingested_at: datetime


@dataclass(frozen=True, slots=True)
class IngestSourceFreshness:
    scope: MemoryScope
    snapshot_id: DbtSnapshotId
    raw_sources: bytes | str
    source_identity: str
    ingested_at: datetime


@dataclass(frozen=True, slots=True)
class IngestSupplementalResult:
    content_digest: str
    idempotent: bool


@dataclass(frozen=True, slots=True)
class GetDbtSupplementalArtifacts:
    scope: MemoryScope
    snapshot_id: DbtSnapshotId


@dataclass(frozen=True, slots=True)
class DbtSupplementalArtifacts:
    catalog: DbtCatalogArtifact | None
    run_results: DbtRunResultsArtifact | None
    source_freshness: DbtSourceFreshnessArtifact | None


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
    destination_unique_id: DbtNodeId | None = None

    def __post_init__(self) -> None:
        if self.maximum_depth is not None and self.maximum_depth < 0:
            raise ValueError("maximum_depth must be non-negative")
        if self.maximum_nodes < 1 or self.maximum_edges < 1:
            raise ValueError("lineage result limits must be positive")
        if self.destination_unique_id == self.unique_id:
            raise ValueError("dbt path requires distinct start and destination nodes")


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
    destination_node: DbtManifestNode | None = None
    path_found: bool = True


@dataclass(frozen=True, slots=True)
class QueryTestCoverage:
    scope: MemoryScope
    unique_id: DbtNodeId
    maximum_tests: int = 32
    snapshot_id: DbtSnapshotId | None = None
    current_content_digest: str | None = None
    current_source_state: SourceStateFingerprint | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.maximum_tests <= 100:
            raise ValueError("dbt test coverage limit must be between 1 and 100")


@dataclass(frozen=True, slots=True)
class TestCoverageQueryResult:
    snapshot: DbtManifestSnapshot
    subject_node: DbtManifestNode
    test_nodes: tuple[DbtManifestNode, ...]
    edges: tuple[DbtLineageEdge, ...]
    truncated: bool
    currentness: ArtifactCurrentness
    currentness_reason: str


@dataclass(frozen=True, slots=True)
class QueryManifestSelector:
    scope: MemoryScope
    resource_type: str | None = None
    package_name: str | None = None
    tag: str | None = None
    maximum_nodes: int = 32
    snapshot_id: DbtSnapshotId | None = None
    current_content_digest: str | None = None
    current_source_state: SourceStateFingerprint | None = None

    def __post_init__(self) -> None:
        if self.resource_type is None and self.package_name is None and self.tag is None:
            raise ValueError("dbt selector requires at least one exact filter")
        for field_name in ("resource_type", "package_name", "tag"):
            value = getattr(self, field_name)
            if value is not None and (not value.strip() or len(value) > 256):
                raise ValueError(f"dbt selector {field_name} must be a bounded non-empty string")
        if not 1 <= self.maximum_nodes <= 100:
            raise ValueError("dbt selector node limit must be between 1 and 100")


@dataclass(frozen=True, slots=True)
class ManifestSelectorQueryResult:
    snapshot: DbtManifestSnapshot
    nodes: tuple[DbtManifestNode, ...]
    truncated: bool
    currentness: ArtifactCurrentness
    currentness_reason: str


@dataclass(frozen=True, slots=True)
class QuerySourceFreshness:
    scope: MemoryScope
    unique_id: DbtNodeId
    snapshot_id: DbtSnapshotId | None = None
    current_content_digest: str | None = None
    current_source_state: SourceStateFingerprint | None = None


@dataclass(frozen=True, slots=True)
class SourceFreshnessQueryResult:
    snapshot: DbtManifestSnapshot
    source_node: DbtManifestNode
    observation: DbtSourceFreshnessResult | None
    artifact: DbtSourceFreshnessArtifact | None
    currentness: ArtifactCurrentness
    currentness_reason: str


class DbtManifestApplicationService:
    """Coordinates authoritative parsing with immutable scoped snapshot storage."""

    def __init__(
        self,
        repository: ProjectIndexRepository,
        parser: DbtManifestParserPort | None = None,
        catalog_parser: DbtCatalogParserPort | None = None,
        run_results_parser: DbtRunResultsParserPort | None = None,
        source_freshness_parser: DbtSourceFreshnessParserPort | None = None,
    ) -> None:
        self._repository = repository
        self._parser = parser
        self._catalog_parser = catalog_parser
        self._run_results_parser = run_results_parser
        self._source_freshness_parser = source_freshness_parser

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

    def ingest_catalog(self, command: IngestCatalog) -> IngestSupplementalResult:
        if self._catalog_parser is None:
            raise DbtApplicationInvalidManifest("dbt catalog ingestion parser is unavailable")
        try:
            artifact = self._catalog_parser.parse_for_ingestion(
                command.raw_catalog,
                scope=command.scope,
                source_identity=command.source_identity,
                ingested_at=command.ingested_at,
            )
        except (DbtSupplementalArtifactError, TypeError, ValueError) as error:
            raise DbtApplicationInvalidManifest("dbt catalog is invalid or unsupported") from error
        return self._store_catalog(command, artifact)

    def ingest_run_results(self, command: IngestRunResults) -> IngestSupplementalResult:
        if self._run_results_parser is None:
            raise DbtApplicationInvalidManifest("dbt run-results ingestion parser is unavailable")
        try:
            artifact = self._run_results_parser.parse_for_ingestion(
                command.raw_run_results,
                scope=command.scope,
                source_identity=command.source_identity,
                ingested_at=command.ingested_at,
            )
        except (DbtSupplementalArtifactError, TypeError, ValueError) as error:
            raise DbtApplicationInvalidManifest(
                "dbt run-results is invalid or unsupported"
            ) from error
        return self._store_run_results(command, artifact)

    def ingest_source_freshness(self, command: IngestSourceFreshness) -> IngestSupplementalResult:
        if self._source_freshness_parser is None:
            raise DbtApplicationInvalidManifest("dbt source-freshness parser is unavailable")
        try:
            artifact = self._source_freshness_parser.parse_for_ingestion(
                command.raw_sources,
                scope=command.scope,
                source_identity=command.source_identity,
                ingested_at=command.ingested_at,
            )
        except (DbtSupplementalArtifactError, TypeError, ValueError) as error:
            raise DbtApplicationInvalidManifest(
                "dbt source-freshness artifact is invalid or unsupported"
            ) from error
        return self._store_source_freshness(command, artifact)

    def get_supplemental(self, query: GetDbtSupplementalArtifacts) -> DbtSupplementalArtifacts:
        try:
            return DbtSupplementalArtifacts(
                self._repository.get_catalog_projection(query.scope, query.snapshot_id),
                self._repository.get_run_results_projection(query.scope, query.snapshot_id),
                self._repository.get_source_freshness_projection(query.scope, query.snapshot_id),
            )
        except ManifestSnapshotNotFound as error:
            raise DbtApplicationNotFound(
                "dbt manifest snapshot was not found in the authorized scope"
            ) from error
        except ProjectIndexRepositoryError as error:
            raise DbtApplicationStorageFailure("dbt project index is unavailable") from error

    def _store_catalog(
        self, command: IngestCatalog, artifact: DbtCatalogArtifact
    ) -> IngestSupplementalResult:
        try:
            stored = self._repository.store_catalog_projection(
                command.scope, command.snapshot_id, artifact
            )
        except ManifestSnapshotNotFound as error:
            raise DbtApplicationNotFound(
                "dbt manifest snapshot was not found in the authorized scope"
            ) from error
        except SupplementalArtifactConflict as error:
            raise DbtApplicationConflict(
                "dbt catalog does not match the selected manifest snapshot"
            ) from error
        except ProjectIndexRepositoryError as error:
            raise DbtApplicationStorageFailure("dbt project index is unavailable") from error
        return IngestSupplementalResult(stored.content_digest, stored.idempotent)

    def _store_run_results(
        self, command: IngestRunResults, artifact: DbtRunResultsArtifact
    ) -> IngestSupplementalResult:
        try:
            stored = self._repository.store_run_results_projection(
                command.scope, command.snapshot_id, artifact
            )
        except ManifestSnapshotNotFound as error:
            raise DbtApplicationNotFound(
                "dbt manifest snapshot was not found in the authorized scope"
            ) from error
        except SupplementalArtifactConflict as error:
            raise DbtApplicationConflict(
                "dbt run-results do not match the selected manifest snapshot"
            ) from error
        except ProjectIndexRepositoryError as error:
            raise DbtApplicationStorageFailure("dbt project index is unavailable") from error
        return IngestSupplementalResult(stored.content_digest, stored.idempotent)

    def _store_source_freshness(
        self, command: IngestSourceFreshness, artifact: DbtSourceFreshnessArtifact
    ) -> IngestSupplementalResult:
        try:
            for result in artifact.results:
                node = self._repository.get_node(
                    command.scope, command.snapshot_id, result.unique_id
                )
                if node.resource_type is not DbtResourceType.SOURCE:
                    raise SupplementalArtifactConflict(
                        "dbt source freshness references a non-source manifest node"
                    )
            stored = self._repository.store_source_freshness_projection(
                command.scope, command.snapshot_id, artifact
            )
        except (ManifestSnapshotNotFound, ManifestNodeNotFound) as error:
            raise DbtApplicationNotFound(
                "dbt manifest snapshot was not found in the authorized scope"
            ) from error
        except SupplementalArtifactConflict as error:
            raise DbtApplicationConflict(
                "dbt source freshness does not match the selected manifest snapshot"
            ) from error
        except ProjectIndexRepositoryError as error:
            raise DbtApplicationStorageFailure("dbt project index is unavailable") from error
        return IngestSupplementalResult(stored.content_digest, stored.idempotent)

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
            destination = (
                self._repository.get_node(
                    query.scope, snapshot.snapshot_id, query.destination_unique_id
                )
                if query.destination_unique_id is not None
                else None
            )
        except (ManifestSnapshotNotFound, ManifestNodeNotFound) as error:
            raise DbtApplicationNotFound(
                "dbt lineage node was not found in the authorized scope"
            ) from error
        except ProjectIndexRepositoryError as error:
            raise DbtApplicationStorageFailure("dbt project index is unavailable") from error

        if destination is None:
            nodes, edges, truncated, reason = self._traverse(query, snapshot, start)
            path_found = True
        else:
            nodes, edges, truncated, reason, path_found = self._shortest_path(
                query, snapshot, start, destination
            )
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
            destination_node=destination,
            path_found=path_found,
        )

    def query_test_coverage(self, query: QueryTestCoverage) -> TestCoverageQueryResult:
        """Return directly attached enabled manifest tests without inferring coverage."""
        try:
            snapshot = (
                self._repository.get_snapshot(query.scope, query.snapshot_id)
                if query.snapshot_id is not None
                else self._repository.get_active_snapshot(query.scope)
            )
            if snapshot is None:
                raise ManifestSnapshotNotFound()
            subject = self._repository.get_node(query.scope, snapshot.snapshot_id, query.unique_id)
            downstream = self._repository.direct_downstream(
                query.scope, snapshot.snapshot_id, query.unique_id
            )
            candidates = self._nodes_batched(
                query.scope,
                snapshot.snapshot_id,
                {edge.child_id for edge in downstream},
            )
        except (ManifestSnapshotNotFound, ManifestNodeNotFound) as error:
            raise DbtApplicationNotFound(
                "dbt test coverage node was not found in the authorized scope"
            ) from error
        except ProjectIndexRepositoryError as error:
            raise DbtApplicationStorageFailure("dbt project index is unavailable") from error

        attached_values: list[tuple[DbtManifestNode, DbtLineageEdge]] = []
        for edge in sorted(
            downstream,
            key=lambda item: (str(item.child_id), item.edge_type.value, str(item.parent_id)),
        ):
            node = candidates.get(edge.child_id)
            if node is None:
                raise DbtApplicationStorageFailure("dbt project index graph is inconsistent")
            if node.resource_type is DbtResourceType.TEST and node.enabled:
                attached_values.append((node, edge))
        attached = tuple(attached_values)
        selected = attached[: query.maximum_tests]
        currentness, currentness_reason = _currentness(
            snapshot, query.current_content_digest, query.current_source_state
        )
        return TestCoverageQueryResult(
            snapshot,
            subject,
            tuple(node for node, _ in selected),
            tuple(edge for _, edge in selected),
            len(attached) > len(selected),
            currentness,
            currentness_reason,
        )

    def query_selector(self, query: QueryManifestSelector) -> ManifestSelectorQueryResult:
        """Intersect exact manifest fields without evaluating dbt selector syntax."""
        try:
            snapshot = (
                self._repository.get_snapshot(query.scope, query.snapshot_id)
                if query.snapshot_id is not None
                else self._repository.get_active_snapshot(query.scope)
            )
            if snapshot is None:
                raise ManifestSnapshotNotFound()
            nodes = self._repository.iter_nodes(query.scope, snapshot.snapshot_id)
        except ManifestSnapshotNotFound as error:
            raise DbtApplicationNotFound(
                "dbt selector snapshot was not found in the authorized scope"
            ) from error
        except ProjectIndexRepositoryError as error:
            raise DbtApplicationStorageFailure("dbt project index is unavailable") from error
        matched = tuple(
            node
            for node in sorted(nodes, key=lambda item: str(item.unique_id))
            if node.enabled
            and (query.resource_type is None or node.raw_resource_type == query.resource_type)
            and (query.package_name is None or node.package_name == query.package_name)
            and (query.tag is None or query.tag in node.tags)
        )
        currentness, currentness_reason = _currentness(
            snapshot, query.current_content_digest, query.current_source_state
        )
        return ManifestSelectorQueryResult(
            snapshot,
            matched[: query.maximum_nodes],
            len(matched) > query.maximum_nodes,
            currentness,
            currentness_reason,
        )

    def query_source_freshness(self, query: QuerySourceFreshness) -> SourceFreshnessQueryResult:
        """Return one observed sources.json result without inferring from configuration."""
        try:
            snapshot = (
                self._repository.get_snapshot(query.scope, query.snapshot_id)
                if query.snapshot_id is not None
                else self._repository.get_active_snapshot(query.scope)
            )
            if snapshot is None:
                raise ManifestSnapshotNotFound()
            node = self._repository.get_node(query.scope, snapshot.snapshot_id, query.unique_id)
            if node.resource_type is not DbtResourceType.SOURCE:
                raise DbtApplicationInvalidManifest("dbt freshness requires a source node")
            artifact = self._repository.get_source_freshness_projection(
                query.scope, snapshot.snapshot_id
            )
        except (ManifestSnapshotNotFound, ManifestNodeNotFound) as error:
            raise DbtApplicationNotFound(
                "dbt freshness source was not found in the authorized scope"
            ) from error
        except ProjectIndexRepositoryError as error:
            raise DbtApplicationStorageFailure("dbt project index is unavailable") from error
        observation = (
            next(
                (item for item in artifact.results if item.unique_id == query.unique_id),
                None,
            )
            if artifact is not None
            else None
        )
        currentness, currentness_reason = _currentness(
            snapshot, query.current_content_digest, query.current_source_state
        )
        return SourceFreshnessQueryResult(
            snapshot, node, observation, artifact, currentness, currentness_reason
        )

    def _shortest_path(
        self,
        query: QueryLineage,
        snapshot: DbtManifestSnapshot,
        start: DbtManifestNode,
        destination: DbtManifestNode,
    ) -> tuple[
        tuple[LineageNodeResult, ...],
        tuple[DbtLineageEdge, ...],
        bool,
        str | None,
        bool,
    ]:
        visited = {start.unique_id}
        predecessors: dict[DbtNodeId, tuple[DbtNodeId, DbtLineageEdge]] = {}
        queue: deque[tuple[DbtNodeId, int]] = deque(((start.unique_id, 0),))
        scanned_edges = 0
        truncated = False
        reason: str | None = None
        while queue:
            node_id, depth = queue.popleft()
            if query.maximum_depth is not None and depth >= query.maximum_depth:
                truncated, reason = True, "maximum depth reached"
                continue
            adjacent = self._edges_batched(
                query.scope, snapshot.snapshot_id, (node_id,), query.direction
            )
            candidate_ids = {
                edge.parent_id if query.direction is LineageDirection.UPSTREAM else edge.child_id
                for edge in adjacent
                if (
                    edge.parent_id
                    if query.direction is LineageDirection.UPSTREAM
                    else edge.child_id
                )
                not in visited
            }
            available = self._nodes_batched(query.scope, snapshot.snapshot_id, candidate_ids)
            ordered_edges = sorted(
                adjacent,
                key=lambda edge: (
                    str(
                        edge.parent_id
                        if query.direction is LineageDirection.UPSTREAM
                        else edge.child_id
                    ),
                    edge.edge_type.value,
                    str(edge.parent_id),
                    str(edge.child_id),
                ),
            )
            for edge in ordered_edges:
                scanned_edges += 1
                if scanned_edges > query.maximum_edges:
                    truncated, reason = True, "maximum edge count reached"
                    break
                neighbor = (
                    edge.parent_id
                    if query.direction is LineageDirection.UPSTREAM
                    else edge.child_id
                )
                if neighbor in visited:
                    continue
                node = available.get(neighbor)
                if node is None:
                    raise DbtApplicationStorageFailure("dbt project index graph is inconsistent")
                visited.add(neighbor)
                if not query.include_disabled and not node.enabled:
                    continue
                if len(predecessors) >= query.maximum_nodes:
                    truncated, reason = True, "maximum node count reached"
                    break
                predecessors[neighbor] = (node_id, edge)
                if neighbor == destination.unique_id:
                    return (
                        *self._reconstruct_path(query, snapshot, start, destination, predecessors),
                        True,
                    )
                queue.append((neighbor, depth + 1))
            if truncated and reason in {"maximum edge count reached", "maximum node count reached"}:
                break
        return (), (), truncated, reason or "no directed path", False

    def _reconstruct_path(
        self,
        query: QueryLineage,
        snapshot: DbtManifestSnapshot,
        start: DbtManifestNode,
        destination: DbtManifestNode,
        predecessors: dict[DbtNodeId, tuple[DbtNodeId, DbtLineageEdge]],
    ) -> tuple[tuple[LineageNodeResult, ...], tuple[DbtLineageEdge, ...], bool, None]:
        path_ids = [destination.unique_id]
        path_edges: list[DbtLineageEdge] = []
        current = destination.unique_id
        while current != start.unique_id:
            previous, edge = predecessors[current]
            path_edges.append(edge)
            current = previous
            if current != start.unique_id:
                path_ids.append(current)
        path_ids.reverse()
        path_edges.reverse()
        nodes = self._nodes_batched(query.scope, snapshot.snapshot_id, set(path_ids))
        return (
            tuple(
                LineageNodeResult(nodes[node_id], depth)
                for depth, node_id in enumerate(path_ids, 1)
            ),
            tuple(path_edges),
            False,
            None,
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
            tuple(
                sorted(
                    result_edges,
                    key=lambda edge: (
                        str(edge.parent_id),
                        str(edge.child_id),
                        edge.edge_type.value,
                    ),
                )
            ),
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
        return tuple(
            sorted(
                result,
                key=lambda edge: (
                    str(edge.parent_id),
                    str(edge.child_id),
                    edge.edge_type.value,
                ),
            )
        )


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
