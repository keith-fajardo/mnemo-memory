"""Bounded, deterministic lexical retrieval over explicitly scoped current knowledge revisions."""

from __future__ import annotations

from dataclasses import dataclass

from mnemo_memory.packages.domain import (
    KnowledgeDocumentSectionMatch,
    MemoryScope,
    normalize_knowledge_query,
)
from mnemo_memory.packages.storage import KnowledgeDocumentRepository


class KnowledgeRetrievalError(ValueError):
    """A safe request-validation outcome; it never includes document text."""


@dataclass(frozen=True, slots=True)
class KnowledgeSearchRequest:
    """A bounded, explicit-scope lexical request; there is no broad or ambient search."""

    scope: MemoryScope
    query: str
    maximum_results: int = 8
    maximum_documents: int = 128

    def __post_init__(self) -> None:
        if not isinstance(self.scope, MemoryScope):
            raise KnowledgeRetrievalError("knowledge retrieval requires an explicit scope")
        if not isinstance(self.query, str) or len(self.query) > 512:
            raise KnowledgeRetrievalError("knowledge query is invalid")
        if not 1 <= self.maximum_results <= 24:
            raise KnowledgeRetrievalError("knowledge result limit must be between 1 and 24")
        if not 1 <= self.maximum_documents <= 128:
            raise KnowledgeRetrievalError("knowledge document limit must be between 1 and 128")


KnowledgeSearchMatch = KnowledgeDocumentSectionMatch


@dataclass(frozen=True, slots=True)
class KnowledgeSearchResult:
    terms: tuple[str, ...]
    candidate_document_count: int
    matches: tuple[KnowledgeSearchMatch, ...]


class KnowledgeLexicalRetriever:
    """Retrieve only current scoped revisions with deterministic literal-term ranking.

    This intentionally does not execute documents, infer instructions, use an LLM, or use an
    embedding service. The repository performs its bounded current-section lookup beneath the
    same explicit-scope contract, so SQLite does not retrieve globally and filter afterward.
    """

    def __init__(self, repository: KnowledgeDocumentRepository) -> None:
        self._repository = repository

    def search(self, request: KnowledgeSearchRequest) -> KnowledgeSearchResult:
        terms = normalize_knowledge_query(request.query)
        if not terms:
            raise KnowledgeRetrievalError("knowledge query requires at least one searchable term")
        documents = self._repository.list_active_documents(request.scope)
        candidate_count = min(len(documents), request.maximum_documents)
        return KnowledgeSearchResult(
            terms,
            candidate_count,
            self._repository.search_current_sections(
                request.scope, terms, request.maximum_results, request.maximum_documents
            ),
        )
