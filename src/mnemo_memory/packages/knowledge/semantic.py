"""On-device semantic retrieval over explicitly scoped, rebuildable local projections.

The package owns no model SDK.  A connector supplies the local embedding runtime only when a user
has explicitly installed and invoked it.  This module never sends document text over the network,
never treats retrieved text as instructions, and never ranks across scopes.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from math import sqrt
from typing import Protocol

from mnemo_memory.packages.domain import (
    CurrentKnowledgeDocumentSection,
    KnowledgeSectionEmbedding,
    MemoryScope,
    knowledge_section_digest,
)
from mnemo_memory.packages.policy import ContentSafetyPolicy
from mnemo_memory.packages.storage import KnowledgeDocumentRepository


class LocalEmbeddingError(RuntimeError):
    """Sanitized local-runtime failure; it never includes model, path, or source-text details."""


class LocalEmbeddingProvider(Protocol):
    """One explicitly installed local embedding engine; no provider is imported by this package."""

    @property
    def model_id(self) -> str: ...

    def embed_passages(self, passages: tuple[str, ...]) -> tuple[tuple[float, ...], ...]: ...

    def embed_query(self, query: str) -> tuple[float, ...]: ...


@dataclass(frozen=True, slots=True)
class SemanticKnowledgeIndexRequest:
    scope: MemoryScope
    maximum_documents: int = 128

    def __post_init__(self) -> None:
        if not isinstance(self.scope, MemoryScope):
            raise ValueError("semantic knowledge indexing requires an explicit scope")
        if not 1 <= self.maximum_documents <= 128:
            raise ValueError("semantic knowledge document limit is invalid")


@dataclass(frozen=True, slots=True)
class SemanticKnowledgeIndexResult:
    model_id: str
    current_section_count: int
    reused_section_count: int
    indexed_section_count: int


@dataclass(frozen=True, slots=True)
class SemanticKnowledgeSearchRequest:
    scope: MemoryScope
    query: str
    maximum_results: int = 8
    maximum_documents: int = 128
    minimum_similarity: float = 0.2

    def __post_init__(self) -> None:
        if not isinstance(self.scope, MemoryScope):
            raise ValueError("semantic knowledge retrieval requires an explicit scope")
        if not isinstance(self.query, str) or not self.query.strip() or len(self.query) > 512:
            raise ValueError("semantic knowledge query is invalid")
        if not 1 <= self.maximum_results <= 24 or not 1 <= self.maximum_documents <= 128:
            raise ValueError("semantic knowledge retrieval limits are invalid")
        if not -1.0 <= self.minimum_similarity <= 1.0:
            raise ValueError("semantic knowledge minimum similarity is invalid")


@dataclass(frozen=True, slots=True)
class SemanticKnowledgeMatch:
    section: CurrentKnowledgeDocumentSection
    similarity: float


@dataclass(frozen=True, slots=True)
class SemanticKnowledgeSearchResult:
    model_id: str
    indexed_section_count: int
    unindexed_section_count: int
    matches: tuple[SemanticKnowledgeMatch, ...]


class LocalSemanticKnowledgeIndexer:
    """Build an idempotent local vector projection for only current scoped sections."""

    def __init__(
        self,
        repository: KnowledgeDocumentRepository,
        provider: LocalEmbeddingProvider,
        safety_policy: ContentSafetyPolicy | None = None,
    ) -> None:
        self._repository = repository
        self._provider = provider
        self._safety_policy = safety_policy or ContentSafetyPolicy()

    def index(self, request: SemanticKnowledgeIndexRequest) -> SemanticKnowledgeIndexResult:
        sections = self._repository.iter_current_sections(request.scope, request.maximum_documents)
        current = {(item.revision.revision_id, item.section_index): item for item in sections}
        existing = {
            (item.revision_id, item.section_index): item
            for item in self._repository.list_current_section_embeddings(
                request.scope, self._provider.model_id, request.maximum_documents
            )
        }
        pending = tuple(
            item
            for key, item in current.items()
            if (prior := existing.get(key)) is None
            or prior.section_digest != knowledge_section_digest(item.section)
        )
        if pending:
            passages = tuple(_section_text(item) for item in pending)
            safety = self._safety_policy.assess(*passages)
            if not safety.accepted:
                raise LocalEmbeddingError(safety.code or "MNEMO_SEMANTIC_CONTENT_REJECTED")
            vectors = self._provider.embed_passages(passages)
            if len(vectors) != len(pending):
                raise LocalEmbeddingError("MNEMO_SEMANTIC_LOCAL_RESULT_INVALID")
            self._repository.store_section_embeddings(
                request.scope,
                tuple(
                    KnowledgeSectionEmbedding(
                        request.scope,
                        item.revision.revision_id,
                        item.section_index,
                        self._provider.model_id,
                        knowledge_section_digest(item.section),
                        vector,
                    )
                    for item, vector in zip(pending, vectors, strict=True)
                ),
            )
        return SemanticKnowledgeIndexResult(
            self._provider.model_id, len(sections), len(sections) - len(pending), len(pending)
        )


class LocalSemanticKnowledgeRetriever:
    """Rank only stored current scoped vectors with deterministic cosine tie-breaking."""

    def __init__(
        self, repository: KnowledgeDocumentRepository, provider: LocalEmbeddingProvider
    ) -> None:
        self._repository = repository
        self._provider = provider

    def search(self, request: SemanticKnowledgeSearchRequest) -> SemanticKnowledgeSearchResult:
        sections = self._repository.iter_current_sections(request.scope, request.maximum_documents)
        by_key = {(item.revision.revision_id, item.section_index): item for item in sections}
        embeddings = self._repository.list_current_section_embeddings(
            request.scope, self._provider.model_id, request.maximum_documents
        )
        if not embeddings:
            return SemanticKnowledgeSearchResult(self._provider.model_id, 0, len(sections), ())
        query = self._provider.embed_query(request.query)
        matched: list[SemanticKnowledgeMatch] = []
        for embedding in embeddings:
            section = by_key.get((embedding.revision_id, embedding.section_index))
            if section is None or embedding.section_digest != knowledge_section_digest(
                section.section
            ):
                continue
            score = _cosine_similarity(query, embedding.vector)
            if score >= request.minimum_similarity:
                matched.append(SemanticKnowledgeMatch(section, score))
        matched.sort(
            key=lambda item: (
                -item.similarity,
                item.section.revision.document.relative_path,
                str(item.section.revision.revision_id),
                item.section.section_index,
            )
        )
        return SemanticKnowledgeSearchResult(
            self._provider.model_id,
            len(embeddings),
            max(0, len(sections) - len(embeddings)),
            tuple(matched[: request.maximum_results]),
        )

    def search_sections(
        self, scope: MemoryScope, query: str
    ) -> tuple[tuple[CurrentKnowledgeDocumentSection, float], ...]:
        """Expose only bounded section evidence through the application-owned retrieval port."""
        result = self.search(SemanticKnowledgeSearchRequest(scope, query))
        return tuple((match.section, match.similarity) for match in result.matches)


def _section_text(section: CurrentKnowledgeDocumentSection) -> str:
    return section.section.heading + "\n" + section.section.content


def _cosine_similarity(left: Iterable[float], right: Iterable[float]) -> float:
    left_values = tuple(left)
    right_values = tuple(right)
    if len(left_values) != len(right_values) or not left_values:
        raise LocalEmbeddingError("MNEMO_SEMANTIC_LOCAL_DIMENSIONS_INVALID")
    denominator = sqrt(sum(value * value for value in left_values)) * sqrt(
        sum(value * value for value in right_values)
    )
    if denominator == 0.0:
        raise LocalEmbeddingError("MNEMO_SEMANTIC_LOCAL_VECTOR_INVALID")
    numerator = sum(
        left_value * right_value
        for left_value, right_value in zip(left_values, right_values, strict=True)
    )
    return numerator / denominator
