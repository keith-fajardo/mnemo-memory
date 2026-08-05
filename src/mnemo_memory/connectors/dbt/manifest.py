"""Offline, bounded parser for the supported dbt manifest artifact schema."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import cast
from uuid import UUID, uuid5

from mnemo_memory.packages.domain import (
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    MemoryScope,
    SourceId,
    SourceTrustClass,
    VerificationStatus,
)
from mnemo_memory.packages.domain.dbt_manifest import (
    ArtifactCurrentness,
    DbtArtifactMetadata,
    DbtLineageEdge,
    DbtManifestArtifact,
    DbtManifestNode,
    DbtNodeId,
    DbtResourceType,
    LineageEdgeType,
    ManifestConsistencyError,
    ManifestCycleError,
    ManifestLimitError,
    ManifestValidationError,
    SourceStateFingerprint,
    UnsupportedManifestSchema,
)

SUPPORTED_MANIFEST_SCHEMA_VERSIONS = frozenset({"https://schemas.getdbt.com/dbt/manifest/v12.json"})
_EVIDENCE_NAMESPACE = UUID("b40f3e51-5db0-4a14-90ae-488aed75b07e")


@dataclass(frozen=True, slots=True)
class DbtManifestLimits:
    """Personal-mode limits that reject hostile artifacts before graph construction."""

    max_artifact_bytes: int = 20_000_000
    max_nodes: int = 50_000
    max_edges: int = 200_000
    max_dependencies_per_node: int = 2_000
    max_string_length: int = 100_000
    max_traversal_nodes: int = 50_000

    def __post_init__(self) -> None:
        if any(
            value < 1
            for value in (
                self.max_artifact_bytes,
                self.max_nodes,
                self.max_edges,
                self.max_dependencies_per_node,
                self.max_string_length,
                self.max_traversal_nodes,
            )
        ):
            raise ValueError("dbt manifest limits must all be positive")


@dataclass(frozen=True, slots=True)
class ManifestParseRequest:
    scope: MemoryScope
    source_identity: str
    ingested_at: datetime
    source_state: SourceStateFingerprint | None = None
    limits: DbtManifestLimits = DbtManifestLimits()

    def __post_init__(self) -> None:
        if not isinstance(self.scope, MemoryScope):
            raise TypeError("manifest parsing requires an explicit MemoryScope")
        if not self.source_identity.strip() or self.source_identity.startswith("/"):
            raise ValueError("source_identity must be non-empty and non-absolute")
        if self.ingested_at.tzinfo is None or self.ingested_at.utcoffset() is None:
            raise ValueError("ingested_at must be timezone-aware")


class DbtManifestParser:
    """Parses JSON only; it never executes dbt, SQL, Jinja, macros, or network calls."""

    def parse(self, raw: bytes | str, request: ManifestParseRequest) -> DbtManifestArtifact:
        encoded = raw if isinstance(raw, bytes) else raw.encode("utf-8")
        if len(encoded) > request.limits.max_artifact_bytes:
            raise ManifestLimitError("dbt manifest exceeds the configured artifact byte limit")
        try:
            decoded = encoded.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ManifestValidationError("dbt manifest must be UTF-8 JSON") from error
        try:
            payload = json.loads(decoded)
        except json.JSONDecodeError as error:
            raise ManifestValidationError("dbt manifest is not valid JSON") from error
        if not isinstance(payload, dict):
            raise ManifestValidationError("dbt manifest top level must be an object")
        value = cast(dict[str, object], payload)
        metadata_value = _mapping(value.get("metadata"), "metadata")
        schema_version = _required_string(metadata_value, "dbt_schema_version")
        if schema_version not in SUPPORTED_MANIFEST_SCHEMA_VERSIONS:
            raise UnsupportedManifestSchema(
                f"unsupported dbt manifest schema version: {schema_version!r}; supported=v12"
            )
        nodes_value = _object_map(value.get("nodes", {}), "nodes")
        sources_value = _object_map(value.get("sources", {}), "sources")
        exposures_value = _object_map(value.get("exposures", {}), "exposures")
        metrics_value = _object_map(value.get("metrics", {}), "metrics")
        semantic_models_value = _object_map(value.get("semantic_models", {}), "semantic_models")
        total_nodes = (
            len(nodes_value)
            + len(sources_value)
            + len(exposures_value)
            + len(metrics_value)
            + len(semantic_models_value)
        )
        if total_nodes > request.limits.max_nodes:
            raise ManifestLimitError("dbt manifest exceeds the configured node limit")
        content_digest = sha256(encoded).hexdigest()
        nodes = self._parse_nodes(nodes_value, "nodes", content_digest, request)
        nodes += self._parse_nodes(sources_value, "sources", content_digest, request)
        nodes += self._parse_nodes(exposures_value, "exposures", content_digest, request)
        nodes += self._parse_nodes(metrics_value, "metrics", content_digest, request)
        nodes += self._parse_nodes(
            semantic_models_value, "semantic_models", content_digest, request
        )
        self._validate_dependencies(nodes)
        edges = self._edges(nodes, content_digest)
        if len(edges) > request.limits.max_edges:
            raise ManifestLimitError("dbt manifest exceeds the configured edge limit")
        self._validate_maps(value, nodes, edges)
        self._validate_acyclic(nodes)
        preliminary_metadata = DbtArtifactMetadata(
            schema_version=schema_version,
            dbt_version=_optional_string(metadata_value.get("dbt_version"), "dbt_version"),
            generated_at=_optional_timestamp(metadata_value.get("generated_at"), "generated_at"),
            invocation_id=_optional_string(metadata_value.get("invocation_id"), "invocation_id"),
            project_name=_optional_string(metadata_value.get("project_name"), "project_name"),
            content_digest=content_digest,
            normalized_graph_digest="0" * 64,
            source_identity=request.source_identity,
            ingested_at=request.ingested_at,
            source_state=request.source_state,
            currentness=ArtifactCurrentness.UNKNOWN,
        )
        artifact = DbtManifestArtifact(
            metadata=preliminary_metadata,
            scope=request.scope,
            nodes=tuple(nodes),
            edges=tuple(edges),
            deferred_resource_counts=tuple(
                sorted(
                    (name, len(_object_map(item, name)))
                    for name, item in value.items()
                    if name == "macros" and isinstance(item, dict)
                )
            ),
        )
        normalized_digest = artifact.digest_normalized_json(artifact.normalized_json())
        return replace(
            artifact,
            metadata=replace(preliminary_metadata, normalized_graph_digest=normalized_digest),
        )

    def parse_for_ingestion(
        self,
        raw: bytes | str,
        *,
        scope: MemoryScope,
        source_identity: str,
        ingested_at: datetime,
        source_state: SourceStateFingerprint | None,
    ) -> DbtManifestArtifact:
        """Application-facing adapter without exposing parser request construction upstream."""
        return self.parse(
            raw,
            ManifestParseRequest(
                scope=scope,
                source_identity=source_identity,
                ingested_at=ingested_at,
                source_state=source_state,
            ),
        )

    def parse_file(self, path: Path, request: ManifestParseRequest) -> DbtManifestArtifact:
        """Read only the caller-selected artifact; the caller supplies its safe path identity."""
        try:
            raw = path.read_bytes()
        except OSError as error:
            raise ManifestValidationError("dbt manifest artifact could not be read") from error
        return self.parse(raw, request)

    def _parse_nodes(
        self,
        entries: Mapping[str, object],
        collection: str,
        content_digest: str,
        request: ManifestParseRequest,
    ) -> list[DbtManifestNode]:
        nodes: list[DbtManifestNode] = []
        for map_key in sorted(entries):
            raw_node = _mapping(entries[map_key], f"{collection}.{map_key}")
            unique_id = _required_string(raw_node, "unique_id")
            if map_key != unique_id:
                raise ManifestValidationError("manifest map key must match contained unique_id")
            self._check_strings(raw_node, request.limits.max_string_length)
            raw_resource_type = _required_string(raw_node, "resource_type")
            expected_type = {
                "sources": "source",
                "exposures": "exposure",
                "metrics": "metric",
                "semantic_models": "semantic_model",
            }.get(collection)
            if expected_type is not None and raw_resource_type != expected_type:
                raise ManifestValidationError(
                    f"{collection} entries must use resource_type {expected_type!r}"
                )
            dependencies = _dependency_ids(raw_node, request.limits.max_dependencies_per_node)
            node_id = DbtNodeId(unique_id)
            evidence = _evidence(content_digest, request, node_id)
            node = DbtManifestNode(
                unique_id=node_id,
                resource_type=_resource_type(raw_resource_type),
                raw_resource_type=raw_resource_type,
                package_name=_required_string(raw_node, "package_name"),
                name=_required_string(raw_node, "name"),
                alias=_optional_string(raw_node.get("alias"), "alias"),
                database=_optional_string(raw_node.get("database"), "database"),
                schema_name=_optional_string(raw_node.get("schema"), "schema"),
                relation_name=_optional_string(raw_node.get("relation_name"), "relation_name"),
                original_file_path=_optional_string(
                    raw_node.get("original_file_path"), "original_file_path"
                ),
                patch_path=_optional_string(raw_node.get("patch_path"), "patch_path"),
                enabled=_enabled(raw_node.get("config")),
                checksum=_checksum(raw_node.get("checksum")),
                tags=_tags(raw_node.get("tags")),
                description=_description(raw_node.get("description")),
                dependency_ids=dependencies,
                evidence=evidence,
            )
            nodes.append(node)
        return nodes

    @staticmethod
    def _check_strings(value: object, limit: int) -> None:
        if isinstance(value, str):
            if len(value) > limit:
                raise ManifestLimitError(
                    "dbt manifest contains a string above the configured limit"
                )
        elif isinstance(value, Mapping):
            for nested in value.values():
                DbtManifestParser._check_strings(nested, limit)
        elif isinstance(value, list):
            for nested in value:
                DbtManifestParser._check_strings(nested, limit)

    @staticmethod
    def _validate_dependencies(nodes: list[DbtManifestNode]) -> None:
        ids = {node.unique_id for node in nodes}
        if len(ids) != len(nodes):
            raise ManifestValidationError(
                "dbt manifest contains duplicate identities across collections"
            )
        for node in nodes:
            missing = set(node.dependency_ids) - ids
            if missing:
                raise ManifestValidationError(
                    "dbt manifest dependency references an unavailable node"
                )

    @staticmethod
    def _edges(nodes: list[DbtManifestNode], content_digest: str) -> list[DbtLineageEdge]:
        edges: list[DbtLineageEdge] = []
        for child in nodes:
            for parent_id in child.dependency_ids:
                edges.append(
                    DbtLineageEdge(
                        parent_id=parent_id,
                        child_id=child.unique_id,
                        edge_type=LineageEdgeType.DBT_DEPENDENCY,
                        evidence=child.evidence,
                        artifact_digest=content_digest,
                    )
                )
        return edges

    @staticmethod
    def _validate_maps(
        payload: Mapping[str, object], nodes: list[DbtManifestNode], edges: list[DbtLineageEdge]
    ) -> None:
        known = {str(node.unique_id) for node in nodes}
        expected_parents: dict[str, set[str]] = {node: set() for node in known}
        expected_children: dict[str, set[str]] = {node: set() for node in known}
        for edge in edges:
            expected_parents[str(edge.child_id)].add(str(edge.parent_id))
            expected_children[str(edge.parent_id)].add(str(edge.child_id))
        for name, expected in (("parent_map", expected_parents), ("child_map", expected_children)):
            if name not in payload:
                continue
            supplied = _object_map(payload[name], name)
            for node_id, dependencies in supplied.items():
                if not isinstance(dependencies, list) or not all(
                    isinstance(item, str) for item in dependencies
                ):
                    raise ManifestConsistencyError(f"{name} contains invalid lineage data")
                # dbt maps may include deferred resources such as semantic models or macros. Only
                # the exact resource kinds parsed into this graph are checked.
                parsed_dependencies = set(cast(list[str], dependencies)) & known
                if node_id in known and parsed_dependencies != expected[node_id]:
                    raise ManifestConsistencyError(f"{name} disagrees with depends_on.nodes")
            if not known.issubset(supplied):
                raise ManifestConsistencyError(f"{name} must cover every parsed node when supplied")

    @staticmethod
    def _validate_acyclic(nodes: list[DbtManifestNode]) -> None:
        parents = {node.unique_id: set(node.dependency_ids) for node in nodes}
        remaining = {node_id: set(dependencies) for node_id, dependencies in parents.items()}
        while remaining:
            roots = {node_id for node_id, dependencies in remaining.items() if not dependencies}
            if not roots:
                raise ManifestCycleError("dbt manifest dependency graph contains a cycle")
            for root in roots:
                remaining.pop(root)
            for dependencies in remaining.values():
                dependencies.difference_update(roots)


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ManifestValidationError(f"dbt manifest {name} must be an object")
    return cast(Mapping[str, object], value)


def _object_map(value: object, name: str) -> Mapping[str, object]:
    result = _mapping(value, name)
    if not all(isinstance(key, str) for key in result):
        raise ManifestValidationError(f"dbt manifest {name} keys must be strings")
    return result


def _required_string(value: Mapping[str, object], name: str) -> str:
    raw = value.get(name)
    if not isinstance(raw, str) or not raw.strip():
        raise ManifestValidationError(f"dbt manifest {name} must be a non-empty string")
    return raw


def _optional_string(value: object, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ManifestValidationError(
            f"dbt manifest {name} must be a non-empty string when present"
        )
    return value


def _optional_timestamp(value: object, name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ManifestValidationError(f"dbt manifest {name} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ManifestValidationError(f"dbt manifest {name} must be a valid timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ManifestValidationError(f"dbt manifest {name} must be timezone-aware")
    return parsed


def _dependency_ids(value: Mapping[str, object], maximum: int) -> tuple[DbtNodeId, ...]:
    depends_on = value.get("depends_on", {})
    if not isinstance(depends_on, Mapping):
        raise ManifestValidationError("dbt manifest depends_on must be an object")
    raw = depends_on.get("nodes", [])
    if not isinstance(raw, list) or not all(isinstance(item, str) and item.strip() for item in raw):
        raise ManifestValidationError("dbt manifest depends_on.nodes must be an array of strings")
    if len(raw) > maximum:
        raise ManifestLimitError("dbt manifest dependency count exceeds the configured limit")
    ids = tuple(DbtNodeId(item) for item in cast(list[str], raw))
    if len(set(ids)) != len(ids):
        raise ManifestValidationError("dbt manifest dependencies must not contain duplicates")
    return ids


def _resource_type(value: str) -> DbtResourceType:
    try:
        return DbtResourceType(value)
    except ValueError:
        return DbtResourceType.OTHER


def _enabled(value: object) -> bool:
    if value is None:
        return True
    if not isinstance(value, Mapping):
        raise ManifestValidationError("dbt manifest config must be an object")
    raw = value.get("enabled", True)
    if not isinstance(raw, bool):
        raise ManifestValidationError("dbt manifest config.enabled must be boolean")
    return raw


def _checksum(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ManifestValidationError("dbt manifest checksum must be an object")
    raw = value.get("checksum")
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ManifestValidationError("dbt manifest checksum must be a string when present")
    # dbt's FileHash permits an empty checksum for resources without a file hash.
    return raw


def _tags(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ManifestValidationError("dbt manifest tags must be an array of non-empty strings")
    return tuple(cast(list[str], value))


def _description(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ManifestValidationError("dbt manifest description must be a string")
    return value


def _evidence(
    content_digest: str, request: ManifestParseRequest, node_id: DbtNodeId
) -> EvidenceReference:
    digest_ref = f"manifest:{content_digest}#{node_id}"
    return EvidenceReference(
        evidence_id=EvidenceId(uuid5(_EVIDENCE_NAMESPACE, f"evidence:{digest_ref}")),
        source_id=SourceId(uuid5(_EVIDENCE_NAMESPACE, f"source:{content_digest}")),
        source_type=EvidenceSourceType.DBT_ARTIFACT,
        trust_class=SourceTrustClass.CURRENT_STRUCTURAL,
        immutable_source_ref=digest_ref,
        content_hash=f"sha256:{content_digest}",
        location=EvidenceLocation(uri=f"{request.source_identity}#{node_id}"),
        observed_at=request.ingested_at,
        verification_status=VerificationStatus.VERIFIED,
    )
