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


@dataclass(frozen=True, slots=True)
class DbtStructureResult:
    kind: str
    query: str
    resolved_unique_id: str | None
    currentness: str
    currentness_reason: str
    items: tuple[dict[str, object], ...]
    edges: tuple[dict[str, object], ...]
    truncated: bool


def _looks_like_unique_id(target: str) -> bool:
    # dbt unique_ids are "<resource>.<package>.<name>", resource in a known set
    head = target.split(".", 1)[0]
    return "." in target and head in {
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


def _looks_like_path(target: str) -> bool:
    return "/" in target or target.endswith(".sql")


def _empty(kind: str, target: str, reason: str) -> DbtStructureResult:
    return DbtStructureResult(kind, target, None, "unknown", reason, (), (), False)


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
        current_source_state: Callable[[MemoryScope], SourceStateFingerprint] | None = None,
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
        # Bare-name resolution would require enumerating every node in the
        # active snapshot (e.g. via an active-snapshot/iter_nodes accessor),
        # but DbtManifestApplicationService exposes neither publicly - only
        # the storage-layer repository does, and reaching into a private
        # `_repository` attribute would mean inventing an API contract that
        # isn't there. Per the plan's explicit fallback, bare names are
        # unsupported: fail open to "could not resolve" rather than guess.
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
                "resource_type": n.node.resource_type.value,
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
            result.currentness.value,
            result.currentness_reason,
            items,
            edges,
            result.truncated,
        )

    # _test_coverage / _freshness / _changes: implemented in A2 against the real result fields.
    def _test_coverage(
        self,
        scope: MemoryScope,
        target: str,
        unique_id: str,
        css: SourceStateFingerprint | None,
    ) -> DbtStructureResult:
        raise NotImplementedError

    def _freshness(
        self,
        scope: MemoryScope,
        target: str,
        unique_id: str,
        css: SourceStateFingerprint | None,
    ) -> DbtStructureResult:
        raise NotImplementedError

    def _changes(
        self, scope: MemoryScope, css: SourceStateFingerprint | None
    ) -> DbtStructureResult:
        raise NotImplementedError
