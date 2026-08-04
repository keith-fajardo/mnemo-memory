"""Bounded, local-only discovery of Markdown/Obsidian source documents.

The connector owns filesystem access; the knowledge package owns parsing. Discovery is explicit
and read-only. It never follows a file symlink, executes a file, reads a directory's configuration
as a note, or persists a document by itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from mnemo_memory.packages.domain import MemoryScope
from mnemo_memory.packages.knowledge import (
    KnowledgeDocument,
    KnowledgeDocumentParseError,
    KnowledgeDocumentParseLimits,
    KnowledgeDocumentParser,
    KnowledgeDocumentParseRequest,
    KnowledgeDocumentSourceKind,
)

_SKIP_DIRECTORIES = frozenset(
    {
        ".git",
        ".obsidian",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "node_modules",
    }
)


class MarkdownSourceDiscoveryError(ValueError):
    """Sanitized local-source discovery failure; no document path or content is exposed."""


@dataclass(frozen=True, slots=True)
class MarkdownSourceDiscoveryLimits:
    """Bounded local-document discovery limits for personal mode."""

    max_files: int = 5_000
    max_total_bytes: int = 20_000_000
    document_limits: KnowledgeDocumentParseLimits = field(
        default_factory=KnowledgeDocumentParseLimits
    )

    def __post_init__(self) -> None:
        if self.max_files < 1 or self.max_total_bytes < 1:
            raise ValueError("markdown discovery limits must be positive")


@dataclass(frozen=True, slots=True)
class MarkdownSourceDiscoveryRequest:
    """One caller-approved local root and exact Mnemo scope for Markdown discovery."""

    scope: MemoryScope
    root: Path
    source_kind: KnowledgeDocumentSourceKind = KnowledgeDocumentSourceKind.MARKDOWN
    limits: MarkdownSourceDiscoveryLimits = MarkdownSourceDiscoveryLimits()

    def __post_init__(self) -> None:
        if not isinstance(self.scope, MemoryScope):
            raise TypeError("knowledge discovery requires an explicit scope")
        if not self.root.is_absolute() or not self.root.is_dir():
            raise MarkdownSourceDiscoveryError("MNEMO_KNOWLEDGE_ROOT_INVALID")
        if not isinstance(self.source_kind, KnowledgeDocumentSourceKind):
            raise TypeError("knowledge discovery source kind is invalid")


@dataclass(frozen=True, slots=True)
class MarkdownSourceDiscoveryResult:
    """Deterministically ordered parsed candidates from one bounded local source root."""

    root: Path
    documents: tuple[KnowledgeDocument, ...]
    scanned_file_count: int
    scanned_bytes: int


class MarkdownSourceDiscovery:
    """Read Markdown candidates from an explicit root without persistence or link traversal."""

    def __init__(self, parser: KnowledgeDocumentParser | None = None) -> None:
        self._parser = parser or KnowledgeDocumentParser()

    def discover(self, request: MarkdownSourceDiscoveryRequest) -> MarkdownSourceDiscoveryResult:
        root = request.root.resolve()
        paths = self._paths(root, request.limits)
        total_bytes = 0
        documents: list[KnowledgeDocument] = []
        for path in paths:
            try:
                content = path.read_bytes()
            except OSError as error:
                raise MarkdownSourceDiscoveryError("MNEMO_KNOWLEDGE_READ_FAILED") from error
            total_bytes += len(content)
            if total_bytes > request.limits.max_total_bytes:
                raise MarkdownSourceDiscoveryError("MNEMO_KNOWLEDGE_TOTAL_BYTES_LIMIT")
            relative_path = path.relative_to(root).as_posix()
            try:
                documents.append(
                    self._parser.parse(
                        KnowledgeDocumentParseRequest(
                            request.scope,
                            relative_path,
                            request.source_kind,
                            request.limits.document_limits,
                        ),
                        content,
                    )
                )
            except KnowledgeDocumentParseError as error:
                raise MarkdownSourceDiscoveryError(str(error)) from error
        return MarkdownSourceDiscoveryResult(root, tuple(documents), len(paths), total_bytes)

    @staticmethod
    def _paths(root: Path, limits: MarkdownSourceDiscoveryLimits) -> tuple[Path, ...]:
        paths = tuple(
            path
            for path in sorted(root.rglob("*"))
            if path.is_file()
            and not path.is_symlink()
            and path.suffix.lower() == ".md"
            and not any(part in _SKIP_DIRECTORIES for part in path.relative_to(root).parts)
        )
        if len(paths) > limits.max_files:
            raise MarkdownSourceDiscoveryError("MNEMO_KNOWLEDGE_FILE_LIMIT")
        return paths
