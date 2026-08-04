from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from mnemo_memory.packages.domain import (
    KnowledgeDocumentRevision,
    KnowledgeDocumentRevisionId,
    KnowledgeDocumentSourceKind,
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.knowledge import (
    KnowledgeDocumentParser,
    KnowledgeDocumentParseRequest,
    KnowledgeLexicalRetriever,
    KnowledgeRetrievalError,
    KnowledgeSearchRequest,
)
from mnemo_memory.packages.storage import ReferenceKnowledgeDocumentRepository

NOW = datetime(2026, 8, 4, tzinfo=UTC)


def scope(seed: int = 1) -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string(f"00000000-0000-4000-8000-{seed:012d}"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string(f"00000000-0000-4000-8001-{seed:012d}"),
        ProjectId.from_string(f"00000000-0000-4000-8002-{seed:012d}"),
    )


def store(
    repository: ReferenceKnowledgeDocumentRepository,
    path: str,
    content: str,
    *,
    scope_value: MemoryScope | None = None,
) -> KnowledgeDocumentRevision:
    actual_scope = scope() if scope_value is None else scope_value
    document = KnowledgeDocumentParser().parse(
        KnowledgeDocumentParseRequest(actual_scope, path), content
    )
    revision = KnowledgeDocumentRevision(KnowledgeDocumentRevisionId.new(), document, 1, None, NOW)
    repository.apply_sync(actual_scope, (revision,), ())
    return revision


def test_retrieval_is_scoped_bounded_and_deterministically_ranked() -> None:
    repository = ReferenceKnowledgeDocumentRepository()
    lower = store(repository, "notes/a.md", "# Decisions\nUse stable identity.")
    higher = store(
        repository,
        "notes/b.md",
        "# Identity identity\nStable identity preserves revision history.",
    )
    store(repository, "notes/private.md", "# Identity\nOther project only.", scope_value=scope(2))

    result = KnowledgeLexicalRetriever(repository).search(
        KnowledgeSearchRequest(scope(), "identity stable", maximum_results=1)
    )

    assert result.terms == ("identity", "stable")
    assert result.candidate_document_count == 2
    assert result.matches[0].revision == higher
    assert result.matches[0].revision != lower
    assert result.matches[0].section.content == "Stable identity preserves revision history."


def test_retrieval_reads_only_the_current_revision_and_rejects_empty_queries() -> None:
    repository = ReferenceKnowledgeDocumentRepository()
    first = store(repository, "notes/decision.md", "# Decision\nDeprecated approach.")
    current_document = KnowledgeDocumentParser().parse(
        KnowledgeDocumentParseRequest(scope(), "notes/decision.md"), "# Decision\nCurrent approach."
    )
    current_document = replace(current_document, document_id=first.document.document_id)
    current = KnowledgeDocumentRevision(
        KnowledgeDocumentRevisionId.new(), current_document, 2, first.revision_id, NOW
    )
    repository.apply_sync(scope(), (current,), ())
    retriever = KnowledgeLexicalRetriever(repository)

    assert retriever.search(KnowledgeSearchRequest(scope(), "deprecated")).matches == ()
    assert (
        retriever.search(KnowledgeSearchRequest(scope(), "current")).matches[0].revision == current
    )
    with pytest.raises(KnowledgeRetrievalError, match="searchable term"):
        retriever.search(KnowledgeSearchRequest(scope(), "---"))


def test_project_markdown_is_first_only_on_an_exact_lexical_tie() -> None:
    repository = ReferenceKnowledgeDocumentRepository()
    parser = KnowledgeDocumentParser()
    project_document = parser.parse(
        KnowledgeDocumentParseRequest(scope(), "docs/decision.md"),
        "# Decision\nUse bounded retrieval.",
    )
    vault_document = parser.parse(
        KnowledgeDocumentParseRequest(
            scope(), "obsidian/vault/decision.md", KnowledgeDocumentSourceKind.OBSIDIAN
        ),
        "# Decision\nUse bounded retrieval.",
    )
    project_revision = KnowledgeDocumentRevision(
        KnowledgeDocumentRevisionId.new(), project_document, 1, None, NOW
    )
    vault_revision = KnowledgeDocumentRevision(
        KnowledgeDocumentRevisionId.new(), vault_document, 1, None, NOW
    )
    repository.apply_sync(scope(), (vault_revision, project_revision), ())

    result = KnowledgeLexicalRetriever(repository).search(
        KnowledgeSearchRequest(scope(), "bounded retrieval")
    )

    assert tuple(match.revision for match in result.matches) == (
        project_revision,
        vault_revision,
    )
