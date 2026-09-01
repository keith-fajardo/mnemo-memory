"""Deterministic, model-free dbt structural lookups over the active manifest.

Answers lineage/impact/test-coverage/freshness/changes for dbt projects so an
agent does not dig through target/manifest.json. Built only on existing
DbtManifestApplicationService query methods; never runs dbt; never raises.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from mnemo_memory.packages.application.dbt import (
    DbtManifestApplicationService,
    LineageDirection,
    QueryLineage,
    QueryManifestChanges,
    QueryManifestSelector,
    QuerySourceFreshness,
    QueryTestCoverage,
    ResolveManifestFile,
)
from mnemo_memory.packages.domain import MemoryScope
from mnemo_memory.packages.domain.dbt_manifest import DbtNodeId, SourceStateFingerprint

DbtStructureKind = Literal[
    "upstream", "downstream", "impact", "test_coverage", "freshness", "changes"
]
_VALID_KINDS = frozenset(
    {"upstream", "downstream", "impact", "test_coverage", "freshness", "changes"}
)
_LINEAGE_KINDS = frozenset({"upstream", "downstream", "impact"})

# dbt unique_ids are "<resource>.<package>.<name>"; this is also the
# exhaustive set of `raw_resource_type` values query_selector can filter on,
# reused to compose a full-manifest name search (see _resolve_bare_name).
_KNOWN_RESOURCE_TYPES = frozenset(
    {
        "model",
        "source",
        "seed",
        "snapshot",
        "test",
        "analysis",
        "exposure",
        "metric",
        "semantic_model",
        "macro",
    }
)


@dataclass(frozen=True, slots=True)
class DbtStructureResult:
    kind: str
    query: str
    resolved_unique_id: str | None
    snapshot_id: str | None
    currentness: str
    currentness_reason: str
    items: tuple[dict[str, object], ...]
    edges: tuple[dict[str, object], ...]
    truncated: bool


def _looks_like_unique_id(target: str) -> bool:
    head = target.split(".", 1)[0]
    return "." in target and head in _KNOWN_RESOURCE_TYPES


def _looks_like_path(target: str) -> bool:
    return "/" in target or target.endswith(".sql")


def _empty(kind: str, target: str, reason: str) -> DbtStructureResult:
    return DbtStructureResult(kind, target, None, None, "unknown", reason, (), (), False)


class DbtStructureService:
    """Dispatches (kind, target) to DbtManifestApplicationService queries.

    Additive over DbtManifestApplicationService: does not modify it, the
    storage contract, or the reference repository. Fails open (never raises)
    on any unresolved target, missing manifest, or query error.
    """

    def __init__(
        self,
        dbt_service: DbtManifestApplicationService,
        *,
        current_source_state: Callable[[MemoryScope], SourceStateFingerprint | None] | None = None,
    ) -> None:
        self._dbt = dbt_service
        self._current_source_state = current_source_state

    def lookup(
        self,
        scope: MemoryScope,
        *,
        kind: DbtStructureKind,
        target: str = "",
        depth: int | None = None,
    ) -> DbtStructureResult:
        target = target.strip()
        if kind not in _VALID_KINDS:
            return _empty(kind, target, "unsupported kind")
        css = self._resolve_source_state(scope)
        try:
            if kind == "changes":
                return self._changes(scope, css)
            unique_id = self._resolve(scope, target)
            if unique_id is None:
                return _empty(kind, target, "could not resolve target to a dbt node")
            if kind in _LINEAGE_KINDS:
                return self._lineage(scope, kind, target, unique_id, depth, css)
            if kind == "test_coverage":
                return self._test_coverage(scope, target, unique_id, css)
            return self._freshness(scope, target, unique_id, css)  # kind == "freshness"
        except Exception:
            return _empty(kind, target, "dbt structural query failed")

    def _resolve_source_state(self, scope: MemoryScope) -> SourceStateFingerprint | None:
        if self._current_source_state is None:
            return None
        try:
            return self._current_source_state(scope)
        except Exception:
            return None

    def _resolve(self, scope: MemoryScope, target: str) -> str | None:
        if not target:
            return None
        if _looks_like_unique_id(target):
            return target
        if _looks_like_path(target):
            try:
                resolved = self._dbt.resolve_file(ResolveManifestFile(scope, target))
                return str(resolved.node.unique_id)
            except Exception:
                return None
        return self._resolve_bare_name(scope, target)

    def _resolve_bare_name(self, scope: MemoryScope, target: str) -> str | None:
        """Resolve a bare node name by composing the existing public query_selector.

        DbtManifestApplicationService has no direct "get node by name"
        accessor, but query_selector(resource_type=...) is public and real,
        so a full-manifest name search is composed from one call per known
        resource type, filtering client-side on `.name`. Exactly one match
        across all resource types resolves; zero or more than one match
        (ambiguous name) fails open to None rather than guessing. A
        truncated listing with zero matches also fails open to None - it
        cannot be told apart from a match that was cut off by the page
        limit, so it must not be reported as a definitive "not found".
        """
        try:
            matches: list[str] = []
            for resource_type in _KNOWN_RESOURCE_TYPES:
                result = self._dbt.query_selector(
                    QueryManifestSelector(
                        scope=scope, resource_type=resource_type, maximum_nodes=100
                    )
                )
                matches.extend(str(node.unique_id) for node in result.nodes if node.name == target)
            if len(matches) == 1:
                return matches[0]
            return None
        except Exception:
            return None

    def _lineage(
        self,
        scope: MemoryScope,
        kind: str,
        target: str,
        unique_id: str,
        depth: int | None,
        css: SourceStateFingerprint | None,
    ) -> DbtStructureResult:
        direction = LineageDirection.UPSTREAM if kind == "upstream" else LineageDirection.DOWNSTREAM
        result = self._dbt.query(
            QueryLineage(
                scope=scope,
                unique_id=DbtNodeId(unique_id),
                direction=direction,
                transitive=True,
                maximum_depth=depth,
                current_source_state=css,
            )
        )
        items: tuple[dict[str, object], ...] = tuple(
            {
                "unique_id": str(n.node.unique_id),
                "name": n.node.name,
                # raw_resource_type (not the resource_type enum, which folds
                # unknown types to "other") so a consumer joining nodes
                # across kinds sees one consistent string per node - see
                # _test_coverage/_changes, which use the same field.
                "resource_type": n.node.raw_resource_type,
                "relative_path": n.node.original_file_path,
                "depth": n.depth,
            }
            for n in result.nodes
        )
        edges: tuple[dict[str, object], ...] = tuple(
            {
                "parent_id": str(e.parent_id),
                "child_id": str(e.child_id),
                "edge_type": e.edge_type.value,
            }
            for e in result.edges
        )
        return DbtStructureResult(
            kind,
            target,
            unique_id,
            str(result.snapshot.snapshot_id),
            result.currentness.value,
            result.currentness_reason,
            items,
            edges,
            result.truncated,
        )

    def _test_coverage(
        self,
        scope: MemoryScope,
        target: str,
        unique_id: str,
        css: SourceStateFingerprint | None,
    ) -> DbtStructureResult:
        result = self._dbt.query_test_coverage(
            QueryTestCoverage(
                scope=scope,
                unique_id=DbtNodeId(unique_id),
                current_source_state=css,
            )
        )
        items: tuple[dict[str, object], ...] = tuple(
            {
                "test_unique_id": str(node.unique_id),
                "subject_node": str(result.subject_node.unique_id),
                "resource_type": node.raw_resource_type,
                "relative_path": node.original_file_path,
            }
            for node in result.test_nodes
        )
        # edges=(): TestCoverageQueryResult.edges carries the test-attachment
        # edges, but per the brief's item shape test_coverage only surfaces
        # the attached test nodes themselves - omitted deliberately, not an
        # oversight.
        return DbtStructureResult(
            "test_coverage",
            target,
            unique_id,
            str(result.snapshot.snapshot_id),
            result.currentness.value,
            result.currentness_reason,
            items,
            (),
            result.truncated,
        )

    def _freshness(
        self,
        scope: MemoryScope,
        target: str,
        unique_id: str,
        css: SourceStateFingerprint | None,
    ) -> DbtStructureResult:
        result = self._dbt.query_source_freshness(
            QuerySourceFreshness(
                scope=scope,
                unique_id=DbtNodeId(unique_id),
                current_source_state=css,
            )
        )
        observation = result.observation
        items: tuple[dict[str, object], ...]
        if observation is None:
            items = ()
        else:
            items = (
                {
                    "source_unique_id": str(result.source_node.unique_id),
                    "status": observation.status.value,
                    "max_loaded_at": (
                        None
                        if observation.max_loaded_at is None
                        else observation.max_loaded_at.isoformat()
                    ),
                    "age_seconds": observation.age_seconds,
                },
            )
        return DbtStructureResult(
            "freshness",
            target,
            unique_id,
            str(result.snapshot.snapshot_id),
            result.currentness.value,
            result.currentness_reason,
            items,
            (),
            False,
        )

    def _changes(
        self, scope: MemoryScope, css: SourceStateFingerprint | None
    ) -> DbtStructureResult:
        result = self._dbt.query_changes(
            QueryManifestChanges(scope=scope, current_source_state=css)
        )
        items: tuple[dict[str, object], ...] = tuple(
            {
                "kind": change.kind.value,
                "unique_id": str(change.unique_id),
                "resource_type": change.node.raw_resource_type,
                "relative_path": change.node.original_file_path,
            }
            for change in result.changes
        )
        return DbtStructureResult(
            "changes",
            "",
            None,
            str(result.after_snapshot.snapshot_id),
            result.currentness.value,
            result.currentness_reason,
            items,
            (),
            result.truncated,
        )
