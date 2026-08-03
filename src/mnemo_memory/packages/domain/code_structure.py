"""Immutable, parser-derived facts for a future general repository structure index.

These values intentionally contain identifiers and metadata only.  Source text, credentials,
comments, and docstrings are not structural facts and are not represented here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from mnemo_memory.packages.domain.identifiers import Identifier
from mnemo_memory.packages.domain.models import MemoryScope


class CodeSnapshotId(Identifier):
    """Identity of one immutable source-tree snapshot."""


class CodeSymbolId(Identifier):
    """Identity of a symbol projection inside one source snapshot."""


class CodeSymbolKind(StrEnum):
    MODULE = "module"
    CLASS = "class"
    INTERFACE = "interface"
    STRUCT = "struct"
    ENUM = "enum"
    TRAIT = "trait"
    FUNCTION = "function"
    ASYNC_FUNCTION = "async_function"


class CodeEdgeKind(StrEnum):
    IMPORTS = "imports"
    CALLS = "calls"
    DEFINES = "defines"


@dataclass(frozen=True, slots=True)
class CodeSnapshot:
    snapshot_id: CodeSnapshotId
    scope: MemoryScope
    source_digest: str
    file_count: int
    symbol_count: int
    edge_count: int

    def __post_init__(self) -> None:
        if not self.source_digest.startswith("sha256:") or len(self.source_digest) != 71:
            raise ValueError("source snapshot requires a sha256 digest")
        if any(value < 0 for value in (self.file_count, self.symbol_count, self.edge_count)):
            raise ValueError("source snapshot counts cannot be negative")


@dataclass(frozen=True, slots=True)
class CodeSymbol:
    snapshot_id: CodeSnapshotId
    symbol_id: CodeSymbolId
    relative_path: str
    qualified_name: str
    kind: CodeSymbolKind
    line: int

    def __post_init__(self) -> None:
        if (
            not self.relative_path
            or self.relative_path.startswith("/")
            or ".." in self.relative_path.split("/")
        ):
            raise ValueError("symbol path must be a safe relative path")
        if not self.qualified_name or self.line < 1:
            raise ValueError("symbol identity is invalid")


@dataclass(frozen=True, slots=True)
class CodeEdge:
    snapshot_id: CodeSnapshotId
    source_symbol_id: CodeSymbolId
    target: str
    kind: CodeEdgeKind
    target_symbol_id: CodeSymbolId | None = None

    def __post_init__(self) -> None:
        if not self.target or len(self.target) > 512:
            raise ValueError("code edge target is invalid")


@dataclass(frozen=True, slots=True)
class CodeStructureArtifact:
    """One immutable, static projection of a scoped source tree.

    This is deliberately structural only: it carries no source text, comments,
    docstrings, environment values, or executable behavior.
    """

    snapshot: CodeSnapshot
    symbols: tuple[CodeSymbol, ...]
    edges: tuple[CodeEdge, ...]

    def __post_init__(self) -> None:
        if any(symbol.snapshot_id != self.snapshot.snapshot_id for symbol in self.symbols):
            raise ValueError("code symbols must belong to their snapshot")
        if any(edge.snapshot_id != self.snapshot.snapshot_id for edge in self.edges):
            raise ValueError("code edges must belong to their snapshot")
        symbol_ids = {symbol.symbol_id for symbol in self.symbols}
        if any(edge.source_symbol_id not in symbol_ids for edge in self.edges):
            raise ValueError("code edges must originate from a known symbol")
        if any(
            edge.target_symbol_id is not None and edge.target_symbol_id not in symbol_ids
            for edge in self.edges
        ):
            raise ValueError("resolved code edges must target a symbol in the same snapshot")
        if (
            len(self.symbols) != self.snapshot.symbol_count
            or len(self.edges) != self.snapshot.edge_count
        ):
            raise ValueError("code snapshot counts must match its projections")
