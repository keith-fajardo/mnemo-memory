"""Bounded, deterministic lexical retrieval over explicitly scoped current knowledge revisions."""

from __future__ import annotations

import re
from dataclasses import dataclass

from mnemo_memory.packages.domain import (
    KnowledgeDocumentRevision,
    KnowledgeDocumentSection,
    MemoryScope,
)
from mnemo_memory.packages.storage import KnowledgeDocumentRepository

_TERM_PATTERN = re.compile(r"[^\W_][\w-]{1,63}", re.UNICODE)
_MAX_QUERY_TERMS = 12


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


@dataclass(frozen=True, slots=True)
class KnowledgeSearchMatch:
    """One literal untrusted section with exact retained document and revision identity."""

    revision: KnowledgeDocumentRevision
    section_index: int
    section: KnowledgeDocumentSection
    score: int

    def __post_init__(self) -> None:
        if self.section_index < 0 or self.score < 1:
            raise ValueError("knowledge search match is invalid")


@dataclass(frozen=True, slots=True)
class KnowledgeSearchResult:
    terms: tuple[str, ...]
    candidate_document_count: int
    matches: tuple[KnowledgeSearchMatch, ...]


class KnowledgeLexicalRetriever:
    """Retrieve only current scoped revisions with deterministic literal-term ranking.

    This intentionally does not execute documents, infer instructions, use an LLM, or use an
    embedding service. It is a personal-mode baseline; a future SQLite batched search can preserve
    the same result contract without changing authorization or ranking semantics.
    """

    def __init__(self, repository: KnowledgeDocumentRepository) -> None:
        self._repository = repository

    def search(self, request: KnowledgeSearchRequest) -> KnowledgeSearchResult:
        terms = _terms(request.query)
        if not terms:
            raise KnowledgeRetrievalError("knowledge query requires at least one searchable term")
        documents = self._repository.list_active_documents(request.scope)
        if len(documents) > request.maximum_documents:
            documents = documents[: request.maximum_documents]
        matches: list[KnowledgeSearchMatch] = []
        for known in documents:
            revision = self._repository.get_current_revision(request.scope, known.document_id)
            matches.extend(_matches(revision, terms))
        ordered = tuple(
            sorted(
                matches,
                key=lambda match: (
                    -match.score,
                    match.revision.document.relative_path,
                    str(match.revision.revision_id),
                    match.section_index,
                ),
            )[: request.maximum_results]
        )
        return KnowledgeSearchResult(terms, len(documents), ordered)


def _terms(query: str) -> tuple[str, ...]:
    unique: list[str] = []
    for term in _TERM_PATTERN.findall(query.casefold()):
        if term not in unique:
            unique.append(term)
        if len(unique) == _MAX_QUERY_TERMS:
            break
    return tuple(unique)


def _matches(
    revision: KnowledgeDocumentRevision, terms: tuple[str, ...]
) -> tuple[KnowledgeSearchMatch, ...]:
    matches: list[KnowledgeSearchMatch] = []
    for index, section in enumerate(revision.document.sections):
        heading = section.heading.casefold()
        content = section.content.casefold()
        score = sum(4 * heading.count(term) + content.count(term) for term in terms)
        if score:
            matches.append(KnowledgeSearchMatch(revision, index, section, score))
    return tuple(matches)
