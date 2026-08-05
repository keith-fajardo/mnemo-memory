"""Immutable dbt manifest evidence and lineage value objects.

These types intentionally model only information read from a manifest artifact.  They do
not execute dbt, render SQL, or decide whether an artifact is current in a repository.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from hashlib import sha256

from .identifiers import DbtSnapshotId
from .models import EvidenceReference, MemoryScope, _require_aware


class DbtManifestError(ValueError):
    """Expected, safe-to-report manifest parsing or lineage error."""


class UnsupportedManifestSchema(DbtManifestError):
    pass


class ManifestValidationError(DbtManifestError):
    pass


class ManifestLimitError(DbtManifestError):
    pass


class ManifestConsistencyError(DbtManifestError):
    pass


class ManifestCycleError(DbtManifestError):
    pass


class LineageNodeNotFound(DbtManifestError):
    pass


@dataclass(frozen=True, slots=True, order=True)
class DbtNodeId:
    """A dbt ``unique_id``; it is deliberately not interchangeable with Mnemo UUIDs."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value.strip():
            raise ValueError("dbt node unique_id must be a non-empty string")

    def __str__(self) -> str:
        return self.value


class DbtResourceType(str, Enum):
    MODEL = "model"
    SOURCE = "source"
    TEST = "test"
    SEED = "seed"
    SNAPSHOT = "snapshot"
    ANALYSIS = "analysis"
    EXPOSURE = "exposure"
    METRIC = "metric"
    SEMANTIC_MODEL = "semantic_model"
    OTHER = "other"


class ArtifactCurrentness(str, Enum):
    UNKNOWN = "unknown"
    CURRENT = "current"
    STALE = "stale"


class LineageEdgeType(str, Enum):
    DBT_DEPENDENCY = "dbt_dependency"


@dataclass(frozen=True, slots=True)
class SourceStateFingerprint:
    git_commit: str | None = None
    working_tree_fingerprint: str | None = None
    dirty: bool | None = None
    target_name: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("git_commit", "working_tree_fingerprint", "target_name"):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{field_name} must be a non-empty string when present")
        if self.dirty is not None and not isinstance(self.dirty, bool):
            raise TypeError("dirty must be a boolean when present")


@dataclass(frozen=True, slots=True)
class DbtArtifactMetadata:
    schema_version: str
    dbt_version: str | None
    generated_at: datetime | None
    invocation_id: str | None
    project_name: str | None
    content_digest: str
    normalized_graph_digest: str
    source_identity: str
    ingested_at: datetime
    source_state: SourceStateFingerprint | None = None
    currentness: ArtifactCurrentness = ArtifactCurrentness.UNKNOWN

    def __post_init__(self) -> None:
        if not self.schema_version.startswith("https://schemas.getdbt.com/dbt/manifest/"):
            raise ValueError("schema_version must be an official dbt manifest schema URL")
        for field_name in ("content_digest", "normalized_graph_digest"):
            value = getattr(self, field_name)
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise ValueError(f"{field_name} must be a lowercase SHA-256 hex digest")
        if not self.source_identity.strip() or self.source_identity.startswith("/"):
            raise ValueError("source_identity must be a non-absolute non-empty artifact identity")
        if self.generated_at is not None:
            _require_aware(self.generated_at, "generated_at")
        _require_aware(self.ingested_at, "ingested_at")
        for field_name in ("dbt_version", "invocation_id", "project_name"):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{field_name} must be non-empty when present")


@dataclass(frozen=True, slots=True)
class DbtManifestNode:
    unique_id: DbtNodeId
    resource_type: DbtResourceType
    raw_resource_type: str
    package_name: str
    name: str
    alias: str | None
    database: str | None
    schema_name: str | None
    relation_name: str | None
    original_file_path: str | None
    patch_path: str | None
    enabled: bool
    checksum: str | None
    tags: tuple[str, ...]
    description: str
    dependency_ids: tuple[DbtNodeId, ...]
    evidence: EvidenceReference

    def __post_init__(self) -> None:
        if not isinstance(self.unique_id, DbtNodeId):
            raise TypeError("unique_id must be a DbtNodeId")
        for field_name in ("raw_resource_type", "package_name", "name"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        for field_name in (
            "alias",
            "database",
            "schema_name",
            "relation_name",
            "original_file_path",
            "patch_path",
            "checksum",
        ):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, str):
                raise TypeError(f"{field_name} must be a string when present")
        if not isinstance(self.enabled, bool) or not isinstance(self.description, str):
            raise TypeError("enabled must be boolean and description must be a string")
        tags = tuple(self.tags)
        if any(not isinstance(tag, str) or not tag.strip() for tag in tags):
            raise ValueError("tags must contain non-empty strings")
        dependencies = tuple(self.dependency_ids)
        if any(not isinstance(item, DbtNodeId) for item in dependencies):
            raise TypeError("dependency_ids must contain DbtNodeId values")
        if not isinstance(self.evidence, EvidenceReference):
            raise TypeError("node evidence must be an EvidenceReference")
        object.__setattr__(self, "tags", tags)
        object.__setattr__(self, "dependency_ids", dependencies)


@dataclass(frozen=True, slots=True)
class DbtLineageEdge:
    parent_id: DbtNodeId
    child_id: DbtNodeId
    edge_type: LineageEdgeType
    evidence: EvidenceReference
    artifact_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.parent_id, DbtNodeId) or not isinstance(self.child_id, DbtNodeId):
            raise TypeError("lineage edge endpoints must be DbtNodeId values")
        if self.parent_id == self.child_id:
            raise ValueError("a dbt lineage edge cannot point to itself")
        if not isinstance(self.evidence, EvidenceReference):
            raise TypeError("edge evidence must be an EvidenceReference")
        if len(self.artifact_digest) != 64:
            raise ValueError("artifact_digest must be a SHA-256 hex digest")


@dataclass(frozen=True, slots=True)
class DbtManifestArtifact:
    metadata: DbtArtifactMetadata
    scope: MemoryScope
    nodes: tuple[DbtManifestNode, ...]
    edges: tuple[DbtLineageEdge, ...]
    deferred_resource_counts: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.metadata, DbtArtifactMetadata) or not isinstance(
            self.scope, MemoryScope
        ):
            raise TypeError("artifact requires metadata and explicit memory scope")
        nodes = tuple(self.nodes)
        ids = [node.unique_id for node in nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("manifest artifact contains duplicate node identities")
        if any(not isinstance(edge, DbtLineageEdge) for edge in self.edges):
            raise TypeError("artifact edges must be DbtLineageEdge values")
        object.__setattr__(
            self, "nodes", tuple(sorted(nodes, key=lambda node: str(node.unique_id)))
        )
        object.__setattr__(
            self,
            "edges",
            tuple(sorted(self.edges, key=lambda edge: (str(edge.parent_id), str(edge.child_id)))),
        )

    def normalized_json(self) -> str:
        """Stable graph-only representation used to fingerprint structural evidence."""
        value = {
            "schema_version": self.metadata.schema_version,
            "nodes": [
                {
                    "unique_id": str(node.unique_id),
                    "resource_type": node.raw_resource_type,
                    "package_name": node.package_name,
                    "name": node.name,
                    "alias": node.alias,
                    "database": node.database,
                    "schema_name": node.schema_name,
                    "relation_name": node.relation_name,
                    "original_file_path": node.original_file_path,
                    "patch_path": node.patch_path,
                    "enabled": node.enabled,
                    "checksum": node.checksum,
                    "tags": list(node.tags),
                    "description": node.description,
                    "dependency_ids": [str(item) for item in node.dependency_ids],
                }
                for node in self.nodes
            ],
            "edges": [
                {
                    "parent_id": str(edge.parent_id),
                    "child_id": str(edge.child_id),
                    "type": edge.edge_type.value,
                }
                for edge in self.edges
            ],
            "deferred_resource_counts": list(self.deferred_resource_counts),
        }
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    @staticmethod
    def digest_normalized_json(value: str) -> str:
        return sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class DbtManifestSnapshot:
    """Immutable identity and activation state for one persisted structural projection."""

    snapshot_id: DbtSnapshotId
    scope: MemoryScope
    metadata: DbtArtifactMetadata
    node_count: int
    edge_count: int
    is_active: bool

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot_id, DbtSnapshotId):
            raise TypeError("snapshot_id must be a DbtSnapshotId")
        if not isinstance(self.scope, MemoryScope) or not isinstance(
            self.metadata, DbtArtifactMetadata
        ):
            raise TypeError("snapshot requires scope and artifact metadata")
        if self.node_count < 0 or self.edge_count < 0:
            raise ValueError("snapshot counts must be non-negative")
