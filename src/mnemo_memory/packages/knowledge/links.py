"""Deterministic scoped Markdown/Obsidian link and backlink navigation.

This resolves only declared links from current retained document revisions. It neither follows
filesystem links nor infers a knowledge graph from prose, tags, or model output.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath

from mnemo_memory.packages.domain import (
    KnowledgeDocumentId,
    KnowledgeDocumentRelation,
    KnowledgeDocumentRevision,
    KnowledgeLinkDirection,
    MemoryScope,
)
from mnemo_memory.packages.storage import KnowledgeDocumentNotFound, KnowledgeDocumentRepository


class KnowledgeLinkNavigationError(ValueError):
    """Safe invalid navigation request; it never includes note content."""


@dataclass(frozen=True, slots=True)
class KnowledgeLinkNavigationRequest:
    """Explicit scoped navigation from one current document identity."""

    scope: MemoryScope
    document_id: KnowledgeDocumentId
    direction: KnowledgeLinkDirection
    limit: int = 32
    maximum_documents: int = 128

    def __post_init__(self) -> None:
        if not isinstance(self.scope, MemoryScope):
            raise KnowledgeLinkNavigationError("knowledge navigation requires an explicit scope")
        if not isinstance(self.document_id, KnowledgeDocumentId):
            raise KnowledgeLinkNavigationError("knowledge document identity is invalid")
        if not isinstance(self.direction, KnowledgeLinkDirection):
            raise KnowledgeLinkNavigationError("knowledge navigation direction is invalid")
        if not 1 <= self.limit <= 64 or not 1 <= self.maximum_documents <= 128:
            raise KnowledgeLinkNavigationError("knowledge navigation limit is invalid")


@dataclass(frozen=True, slots=True)
class KnowledgeLinkNavigationResult:
    """Stable current relations plus a count of declared outbound links not resolved uniquely."""

    relations: tuple[KnowledgeDocumentRelation, ...]
    unresolved_link_count: int


class KnowledgeLinkNavigator:
    """Resolve direct current document links deterministically after explicit scope filtering."""

    def __init__(self, repository: KnowledgeDocumentRepository) -> None:
        self._repository = repository

    def navigate(self, request: KnowledgeLinkNavigationRequest) -> KnowledgeLinkNavigationResult:
        try:
            start = self._repository.get_current_revision(request.scope, request.document_id)
        except (KnowledgeDocumentNotFound, TypeError) as error:
            raise KnowledgeLinkNavigationError("knowledge document was not found") from error
        active = self._repository.list_active_documents(request.scope)[: request.maximum_documents]
        revisions = tuple(
            self._repository.get_current_revision(request.scope, known.document_id)
            for known in active
        )
        by_id = {revision.document.document_id: revision for revision in revisions}
        if start.document.document_id not in by_id:
            # The requested scoped document exists but falls outside a caller-selected small scan.
            # It remains a valid start so outbound links can be resolved against the bounded set.
            revisions = (*revisions, start)
            by_id[start.document.document_id] = start
        resolved: list[KnowledgeDocumentRelation] = []
        unresolved = 0
        for source in revisions:
            for link in source.document.links:
                target = _resolve_link(source, link.kind, link.target, revisions)
                if target is None:
                    if source.document.document_id == start.document.document_id:
                        unresolved += 1
                    continue
                relation = KnowledgeDocumentRelation(
                    source.document.document_id,
                    source.revision_id,
                    source.document.relative_path,
                    target.document.document_id,
                    target.revision_id,
                    target.document.relative_path,
                    link.kind,
                    link.target,
                )
                if request.direction is KnowledgeLinkDirection.OUTBOUND:
                    if source.document.document_id == start.document.document_id:
                        resolved.append(relation)
                elif target.document.document_id == start.document.document_id:
                    resolved.append(relation)
        ordered = tuple(
            sorted(
                resolved,
                key=lambda item: (
                    item.source_relative_path,
                    item.target_relative_path,
                    item.link_kind,
                    item.declared_target,
                ),
            )[: request.limit]
        )
        return KnowledgeLinkNavigationResult(ordered, unresolved)


def _resolve_link(
    source: KnowledgeDocumentRevision,
    kind: str,
    declared_target: str,
    revisions: tuple[KnowledgeDocumentRevision, ...],
) -> KnowledgeDocumentRevision | None:
    """Return exactly one candidate or none; ambiguity is deliberately not guessed."""
    if kind == "markdown":
        candidate = _relative_markdown_target(source.document.relative_path, declared_target)
        return _unique(
            revision for revision in revisions if candidate == revision.document.relative_path
        )
    if kind == "wiki":
        target = declared_target.casefold()
        return _unique(
            revision
            for revision in revisions
            if target
            in {
                revision.document.title.casefold(),
                PurePosixPath(revision.document.relative_path).stem.casefold(),
                revision.document.relative_path.removesuffix(".md").casefold(),
            }
        )
    return None


def _relative_markdown_target(source_path: str, target: str) -> str | None:
    """Resolve only an in-memory safe relative target; URLs, anchors, and escapes are excluded."""
    target = target.split("#", maxsplit=1)[0].strip()
    if not target or ":" in target or target.startswith("/") or "\\" in target:
        return None
    parts: list[str] = []
    for part in (*PurePosixPath(source_path).parent.parts, *PurePosixPath(target).parts):
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
            continue
        parts.append(part)
    return "/".join(parts) or None


def _unique(candidates: Iterable[KnowledgeDocumentRevision]) -> KnowledgeDocumentRevision | None:
    values = tuple(candidates)
    return values[0] if len(values) == 1 else None
