"""Deterministic, evidence-preserving traversal of static source relationships.

This is intentionally an *impact candidate* query, not a runtime call graph.
Only relationships whose parser adapter resolved to an in-snapshot symbol can
participate. Dynamic imports, reflection, generated code, and unresolved calls
remain outside its claims.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath

from mnemo_memory.packages.domain import (
    CodeEdge,
    CodeFile,
    CodeSnapshot,
    CodeSnapshotId,
    CodeSymbol,
    CodeSymbolId,
    MemoryScope,
    SourceFileRename,
    unique_file_renames,
)
from mnemo_memory.packages.storage.contracts import (
    SourceSnapshotNotFound,
    SourceStructureRepository,
)


class SourceImpactDirection(StrEnum):
    """Direction through statically resolved, internal source relationships."""

    DEPENDENTS = "dependents"
    DEPENDENCIES = "dependencies"


@dataclass(frozen=True, slots=True)
class SourceImpactQuery:
    """A scoped impact request for one saved symbol or one exact source-file identity."""

    scope: MemoryScope
    symbol: str | None
    direction: SourceImpactDirection = SourceImpactDirection.DEPENDENTS
    transitive: bool = True
    maximum_depth: int | None = None
    maximum_symbols: int = 200
    maximum_edges: int = 500
    snapshot_id: CodeSnapshotId | None = None
    relative_path: str | None = None

    def __post_init__(self) -> None:
        if (self.symbol is None) == (self.relative_path is None):
            raise ValueError("source impact requires exactly one symbol or relative path")
        if self.symbol is not None and (not self.symbol.strip() or len(self.symbol) > 512):
            raise ValueError("source impact requires a bounded symbol")
        if self.relative_path is not None:
            _validate_relative_path(self.relative_path)
        if self.maximum_depth is not None and self.maximum_depth < 0:
            raise ValueError("source impact maximum depth cannot be negative")
        if self.maximum_symbols < 1 or self.maximum_edges < 1:
            raise ValueError("source impact limits must be positive")


@dataclass(frozen=True, slots=True)
class ImpactedSymbol:
    symbol: CodeSymbol
    depth: int


@dataclass(frozen=True, slots=True)
class SourceImpactResult:
    snapshot: CodeSnapshot
    start_symbols: tuple[CodeSymbol, ...]
    direction: SourceImpactDirection
    transitive: bool
    symbols: tuple[ImpactedSymbol, ...]
    edges: tuple[CodeEdge, ...]
    truncated: bool = False
    truncation_reason: str | None = None


@dataclass(frozen=True, slots=True)
class SourceSnapshotDiff:
    """Metadata-only difference between two immutable snapshots in one scope."""

    before: CodeSnapshot
    after: CodeSnapshot
    file_fingerprints_available: bool
    added_files: tuple[CodeFile, ...]
    removed_files: tuple[CodeFile, ...]
    renamed_files: tuple[SourceFileRename, ...]
    modified_files: tuple[CodeFile, ...]
    added_symbols: tuple[CodeSymbol, ...]
    removed_symbols: tuple[CodeSymbol, ...]
    added_edges: tuple[CodeEdge, ...]
    removed_edges: tuple[CodeEdge, ...]


class SourceImpactService:
    """Repository-port-only query service; no source execution or adapter imports."""

    def __init__(self, repository: SourceStructureRepository) -> None:
        self._repository = repository

    def query(self, request: SourceImpactQuery) -> SourceImpactResult:
        snapshot = (
            self._repository.get_snapshot(request.scope, request.snapshot_id)
            if request.snapshot_id is not None
            else self._repository.get_active_snapshot(request.scope)
        )
        if snapshot is None:
            raise SourceSnapshotNotFound("source snapshot was not found")
        starts = self._starts(request, snapshot.snapshot_id)
        if not starts:
            raise SourceSnapshotNotFound("source symbol was not found")
        if request.maximum_depth == 0:
            return SourceImpactResult(
                snapshot, starts, request.direction, request.transitive, (), ()
            )

        visited = {symbol.symbol_id for symbol in starts}
        frontier = tuple(symbol.symbol_id for symbol in starts)
        symbols: dict[CodeSymbolId, ImpactedSymbol] = {}
        edges: dict[tuple[str, str, str, str | None], CodeEdge] = {}
        depth = 0
        truncated = False
        reason: str | None = None
        while frontier and (request.transitive or depth == 0):
            depth += 1
            if request.maximum_depth is not None and depth > request.maximum_depth:
                truncated, reason = True, "maximum depth reached"
                break
            boundary = self._edges(request, snapshot.snapshot_id, frontier)
            if len(edges) + len(boundary) > request.maximum_edges:
                truncated, reason = True, "maximum edge count reached"
                break
            for edge in boundary:
                edges[_edge_key(edge)] = edge
            if request.direction is SourceImpactDirection.DEPENDENTS:
                next_ids = tuple(
                    sorted(
                        {
                            edge.source_symbol_id
                            for edge in boundary
                            if edge.source_symbol_id not in visited
                        },
                        key=str,
                    )
                )
            else:
                next_ids = tuple(
                    sorted(
                        {
                            edge.target_symbol_id
                            for edge in boundary
                            if edge.target_symbol_id is not None
                            and edge.target_symbol_id not in visited
                        },
                        key=str,
                    )
                )
            if len(symbols) + len(next_ids) > request.maximum_symbols:
                truncated, reason = True, "maximum symbol count reached"
                break
            resolved = self._repository.symbols_by_ids(
                request.scope, snapshot.snapshot_id, next_ids
            )
            for symbol in resolved:
                visited.add(symbol.symbol_id)
                symbols[symbol.symbol_id] = ImpactedSymbol(symbol, depth)
            frontier = tuple(symbol.symbol_id for symbol in resolved)
        ordered = tuple(
            sorted(
                symbols.values(),
                key=lambda item: (
                    item.depth,
                    item.symbol.relative_path,
                    item.symbol.qualified_name,
                    str(item.symbol.symbol_id),
                ),
            )
        )
        return SourceImpactResult(
            snapshot,
            starts,
            request.direction,
            request.transitive,
            ordered,
            tuple(sorted(edges.values(), key=_edge_key)),
            truncated,
            reason,
        )

    def diff(
        self,
        scope: MemoryScope,
        before_snapshot_id: CodeSnapshotId,
        after_snapshot_id: CodeSnapshotId,
    ) -> SourceSnapshotDiff:
        before = self._repository.get_snapshot(scope, before_snapshot_id)
        after = self._repository.get_snapshot(scope, after_snapshot_id)
        before_files = self._repository.iter_files(scope, before_snapshot_id)
        after_files = self._repository.iter_files(scope, after_snapshot_id)
        # Snapshots made before migration 0008 intentionally have no file-level projection.
        # Do not claim every file was added merely because its historical fingerprint is absent.
        file_fingerprints_available = (
            len(before_files) == before.file_count and len(after_files) == after.file_count
        )
        before_files_by_path = {item.relative_path: item for item in before_files}
        after_files_by_path = {item.relative_path: item for item in after_files}
        added_paths = after_files_by_path.keys() - before_files_by_path.keys()
        removed_paths = before_files_by_path.keys() - after_files_by_path.keys()
        renamed_files = (
            unique_file_renames(
                tuple(after_files_by_path[path] for path in sorted(added_paths)),
                tuple(before_files_by_path[path] for path in sorted(removed_paths)),
            )
            if file_fingerprints_available
            else ()
        )
        renamed_after_paths = {item.after.relative_path for item in renamed_files}
        renamed_before_paths = {item.before.relative_path for item in renamed_files}
        before_symbols = self._repository.iter_symbols(scope, before_snapshot_id)
        after_symbols = self._repository.iter_symbols(scope, after_snapshot_id)
        before_by_key = {_symbol_key(item): item for item in before_symbols}
        after_by_key = {_symbol_key(item): item for item in after_symbols}
        before_edges = self._repository.iter_edges(scope, before_snapshot_id)
        after_edges = self._repository.iter_edges(scope, after_snapshot_id)
        before_edge_keys = {_stable_edge_key(item, before_by_key): item for item in before_edges}
        after_edge_keys = {_stable_edge_key(item, after_by_key): item for item in after_edges}
        return SourceSnapshotDiff(
            before,
            after,
            file_fingerprints_available,
            (
                tuple(
                    after_files_by_path[path] for path in sorted(added_paths - renamed_after_paths)
                )
                if file_fingerprints_available
                else ()
            ),
            (
                tuple(
                    before_files_by_path[path]
                    for path in sorted(removed_paths - renamed_before_paths)
                )
                if file_fingerprints_available
                else ()
            ),
            renamed_files,
            (
                tuple(
                    after_files_by_path[path]
                    for path in sorted(before_files_by_path.keys() & after_files_by_path.keys())
                    if before_files_by_path[path].content_digest
                    != after_files_by_path[path].content_digest
                )
                if file_fingerprints_available
                else ()
            ),
            tuple(
                sorted(
                    (after_by_key[key] for key in after_by_key.keys() - before_by_key.keys()),
                    key=_symbol_key,
                )
            ),
            tuple(
                sorted(
                    (before_by_key[key] for key in before_by_key.keys() - after_by_key.keys()),
                    key=_symbol_key,
                )
            ),
            tuple(
                sorted(
                    (
                        after_edge_keys[key]
                        for key in after_edge_keys.keys() - before_edge_keys.keys()
                    ),
                    key=_edge_key,
                )
            ),
            tuple(
                sorted(
                    (
                        before_edge_keys[key]
                        for key in before_edge_keys.keys() - after_edge_keys.keys()
                    ),
                    key=_edge_key,
                )
            ),
        )

    def _starts(
        self, request: SourceImpactQuery, snapshot_id: CodeSnapshotId
    ) -> tuple[CodeSymbol, ...]:
        if request.relative_path is not None:
            # A file identity is exact by contract.  In particular, do not fall back to a
            # same-named file elsewhere in a project: an impact claim would otherwise be wrong.
            return tuple(
                item
                for item in self._repository.find_symbols(
                    request.scope, snapshot_id, request.relative_path, limit=256
                )
                if item.relative_path == request.relative_path
            )
        assert request.symbol is not None
        candidates = self._repository.find_symbols(
            request.scope, snapshot_id, request.symbol, limit=64
        )
        exact = tuple(
            item
            for item in candidates
            if item.qualified_name == request.symbol or item.relative_path == request.symbol
        )
        return exact or candidates

    def _edges(
        self,
        request: SourceImpactQuery,
        snapshot_id: CodeSnapshotId,
        frontier: tuple[CodeSymbolId, ...],
    ) -> tuple[CodeEdge, ...]:
        if request.direction is SourceImpactDirection.DEPENDENTS:
            return self._repository.edges_to_symbols(request.scope, snapshot_id, frontier)
        return tuple(
            edge
            for edge in self._repository.edges_from_symbols(request.scope, snapshot_id, frontier)
            if edge.target_symbol_id is not None
        )


def _symbol_key(symbol: CodeSymbol) -> tuple[str, str, str, int]:
    return (symbol.relative_path, symbol.qualified_name, symbol.kind.value, symbol.line)


def _edge_key(edge: CodeEdge) -> tuple[str, str, str, str | None]:
    return (
        str(edge.source_symbol_id),
        edge.kind.value,
        edge.target,
        str(edge.target_symbol_id) if edge.target_symbol_id else None,
    )


def _stable_edge_key(
    edge: CodeEdge, symbols: dict[tuple[str, str, str, int], CodeSymbol]
) -> tuple[tuple[str, str, str, int] | None, str, str, tuple[str, str, str, int] | None]:
    source = next(
        (key for key, value in symbols.items() if value.symbol_id == edge.source_symbol_id), None
    )
    target = next(
        (key for key, value in symbols.items() if value.symbol_id == edge.target_symbol_id), None
    )
    return (source, edge.kind.value, edge.target, target)


def _validate_relative_path(value: str) -> None:
    if not value or len(value) > 512 or "\\" in value:
        raise ValueError("source impact path must be a bounded relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value != path.as_posix():
        raise ValueError("source impact path must be a canonical relative path")
