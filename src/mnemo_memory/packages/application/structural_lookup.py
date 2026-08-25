"""Deterministic, model-free structural lookups over the active source snapshot.

Answers locate/navigate questions (define/callers/imports/contains) so an agent
does not dispatch a search agent to re-read the tree. Built only on existing
SourceStructureRepository read methods; no source bytes ever touched.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from mnemo_memory.packages.domain import (
    CodeEdgeKind,
    CodeSnapshotId,
    CodeSymbol,
    CodeSymbolId,
    MemoryScope,
)
from mnemo_memory.packages.storage.contracts import SourceStructureRepository

StructuralLookupKind = Literal["define", "callers", "imports", "contains"]

_MAX_LIMIT = 200


@dataclass(frozen=True, slots=True)
class StructuralHit:
    relative_path: str
    qualified_name: str
    kind: str
    line: int


@dataclass(frozen=True, slots=True)
class StructuralLookupResult:
    kind: StructuralLookupKind
    query: str
    hits: tuple[StructuralHit, ...]
    snapshot_id: str | None
    truncated: bool


def _hit(symbol: CodeSymbol) -> StructuralHit:
    return StructuralHit(
        symbol.relative_path, symbol.qualified_name, symbol.kind.value, symbol.line
    )


def _matches_name(symbol: CodeSymbol, target: str) -> bool:
    name = symbol.qualified_name
    return name == target or name.rsplit(".", 1)[-1] == target


class StructuralLookupService:
    def __init__(self, source_repository: SourceStructureRepository) -> None:
        self._source = source_repository

    def lookup(
        self,
        scope: MemoryScope,
        *,
        kind: StructuralLookupKind,
        target: str,
        limit: int = 50,
    ) -> StructuralLookupResult:
        target = target.strip()
        bound = max(1, min(limit, _MAX_LIMIT))
        snapshot = self._source.get_active_snapshot(scope)
        if snapshot is None or not target:
            return StructuralLookupResult(kind, target, (), None, False)
        symbols = self._source.iter_symbols(scope, snapshot.snapshot_id)
        if kind == "define":
            found = [_hit(s) for s in symbols if _matches_name(s, target)]
        elif kind == "contains":
            found = [_hit(s) for s in symbols if s.relative_path == target]
        else:  # callers | imports — implemented in B2
            found = self._edge_lookup(scope, snapshot.snapshot_id, symbols, kind, target)
        return StructuralLookupResult(
            kind,
            target,
            tuple(found[:bound]),
            str(snapshot.snapshot_id),
            truncated=len(found) > bound,
        )

    def _edge_lookup(
        self,
        scope: MemoryScope,
        snapshot_id: CodeSnapshotId,
        symbols: tuple[CodeSymbol, ...],
        kind: StructuralLookupKind,
        target: str,
    ) -> list[StructuralHit]:
        wanted = CodeEdgeKind.CALLS if kind == "callers" else CodeEdgeKind.IMPORTS
        by_id = {s.symbol_id: s for s in symbols}
        target_ids = {s.symbol_id for s in symbols if _matches_name(s, target)}
        edges = self._source.iter_edges(scope, snapshot_id)
        seen: set[CodeSymbolId] = set()
        out: list[StructuralHit] = []
        for edge in edges:
            if edge.kind is not wanted:
                continue
            hit = (
                (edge.target_symbol_id is not None and edge.target_symbol_id in target_ids)
                or edge.target == target
                or edge.target.rsplit(".", 1)[-1] == target
            )
            if not hit:
                continue
            source = by_id.get(edge.source_symbol_id)
            if source is None or source.symbol_id in seen:
                continue
            seen.add(source.symbol_id)
            out.append(_hit(source))
        out.sort(key=lambda h: (h.relative_path, h.line, h.qualified_name))
        return out
