from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mnemo_memory.packages.domain import (
    KnowledgeDocumentRevision,
    KnowledgeDocumentRevisionId,
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    Sensitivity,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.knowledge import (
    KnowledgeDocumentParser,
    KnowledgeDocumentParseRequest,
    LocalEmbeddingError,
    LocalSemanticKnowledgeIndexer,
    LocalSemanticKnowledgeRetriever,
    SemanticKnowledgeIndexRequest,
    SemanticKnowledgeSearchRequest,
)
from mnemo_memory.packages.policy import ContentSafetyDecision, ContentSafetyPolicy
from mnemo_memory.packages.storage import (
    KnowledgeDocumentNotFound,
    KnowledgeDocumentRepository,
    ReferenceKnowledgeDocumentRepository,
    SQLiteKnowledgeDocumentRepository,
)

NOW = datetime(2026, 8, 4, tzinfo=UTC)


class FakeLocalEmbeddingProvider:
    """A deterministic local-provider double; it never receives or uses external state."""

    model_id = "test-local:semantic-v1"

    def embed_passages(self, passages: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(self._vector(value) for value in passages)

    def embed_query(self, query: str) -> tuple[float, ...]:
        return self._vector(query)

    @staticmethod
    def _vector(value: str) -> tuple[float, ...]:
        lowered = value.casefold()
        if any(term in lowered for term in ("invoice", "charge", "billing", "reconcile")):
            return (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        if any(term in lowered for term in ("deploy", "release", "production")):
            return (0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        return (0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0)


class NoQueryProvider(FakeLocalEmbeddingProvider):
    def embed_query(self, query: str) -> tuple[float, ...]:
        raise AssertionError("an unindexed semantic request must not initialize a local model")


class CountingEmbeddingProvider(FakeLocalEmbeddingProvider):
    def __init__(self) -> None:
        self.passage_calls = 0

    def embed_passages(self, passages: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        self.passage_calls += 1
        return super().embed_passages(passages)


class RejectingClassifier:
    def classify(self, values: tuple[str, ...]) -> ContentSafetyDecision:
        assert values
        return ContentSafetyDecision(
            False, Sensitivity.PROHIBITED, "MNEMO_FIXTURE_EMBEDDING_REJECTED"
        )


def scope(seed: int = 1) -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string(f"00000000-0000-4000-8000-{seed:012d}"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string(f"00000000-0000-4000-8001-{seed:012d}"),
        ProjectId.from_string(f"00000000-0000-4000-8002-{seed:012d}"),
    )


def revision(
    path: str, text: str, *, scope_value: MemoryScope | None = None
) -> KnowledgeDocumentRevision:
    actual_scope = scope() if scope_value is None else scope_value
    document = KnowledgeDocumentParser().parse(
        KnowledgeDocumentParseRequest(actual_scope, path), text
    )
    return KnowledgeDocumentRevision(KnowledgeDocumentRevisionId.new(), document, 1, None, NOW)


@pytest.fixture(params=("reference", "sqlite"))
def repository(request: pytest.FixtureRequest, tmp_path: Path) -> KnowledgeDocumentRepository:
    if request.param == "reference":
        return ReferenceKnowledgeDocumentRepository()
    value = SQLiteKnowledgeDocumentRepository(
        tmp_path / "semantic.sqlite3", base_directory=tmp_path
    )
    value.migrate()
    return value


def test_local_semantic_projection_is_scoped_idempotent_and_current_only(
    repository: KnowledgeDocumentRepository,
) -> None:
    repo = repository
    first = revision("docs/reconciliation.md", "# Invoices\nReconcile invoice totals before close.")
    other = revision("docs/release.md", "# Deployment\nUse the release checklist.")
    repo.apply_sync(scope(), (first, other), ())
    provider = CountingEmbeddingProvider()
    indexer = LocalSemanticKnowledgeIndexer(repo, provider)
    retriever = LocalSemanticKnowledgeRetriever(repo, provider)

    initial = indexer.index(SemanticKnowledgeIndexRequest(scope()))
    repeat = indexer.index(SemanticKnowledgeIndexRequest(scope()))
    result = retriever.search(SemanticKnowledgeSearchRequest(scope(), "charge variance"))

    assert initial.indexed_section_count == 2
    assert repeat.indexed_section_count == 0
    assert repeat.reused_section_count == 2
    assert provider.passage_calls == 1
    assert result.indexed_section_count == 2
    assert result.matches[0].section.revision == first
    assert result.matches[0].similarity == 1.0

    current_document = KnowledgeDocumentParser().parse(
        KnowledgeDocumentParseRequest(scope(), "docs/reconciliation.md"),
        "# Deployment\nThe old invoice guidance was replaced.",
    )
    current_document = replace(current_document, document_id=first.document.document_id)
    current = KnowledgeDocumentRevision(
        KnowledgeDocumentRevisionId.new(), current_document, 2, first.revision_id, NOW
    )
    repo.apply_sync(scope(), (current,), ())
    indexer.index(SemanticKnowledgeIndexRequest(scope()))

    after = retriever.search(SemanticKnowledgeSearchRequest(scope(), "charge variance"))
    assert all(match.section.revision != first for match in after.matches)


def test_local_semantic_projection_never_returns_another_scope(
    repository: KnowledgeDocumentRepository,
) -> None:
    repo = repository
    private = revision(
        "private.md", "# Invoices\nReconcile private invoice totals.", scope_value=scope(2)
    )
    repo.apply_sync(scope(2), (private,), ())
    provider = FakeLocalEmbeddingProvider()
    LocalSemanticKnowledgeIndexer(repo, provider).index(SemanticKnowledgeIndexRequest(scope(2)))

    result = LocalSemanticKnowledgeRetriever(repo, provider).search(
        SemanticKnowledgeSearchRequest(scope(), "charge variance")
    )

    assert result.matches == ()
    assert result.indexed_section_count == 0


def test_unindexed_semantic_request_does_not_initialize_the_local_runtime(
    repository: KnowledgeDocumentRepository,
) -> None:
    repo = repository
    document = revision("notes/billing.md", "# Invoices\nReconcile the invoice total.")
    repo.apply_sync(scope(), (document,), ())

    result = LocalSemanticKnowledgeRetriever(repo, NoQueryProvider()).search(
        SemanticKnowledgeSearchRequest(scope(), "charge variance")
    )

    assert result.matches == ()
    assert result.indexed_section_count == 0
    assert result.unindexed_section_count == 1


def test_local_semantic_index_rechecks_content_before_provider_or_vector_storage(
    repository: KnowledgeDocumentRepository,
) -> None:
    document = revision("notes/billing.md", "# Invoices\nReconcile the invoice total.")
    repository.apply_sync(scope(), (document,), ())
    provider = CountingEmbeddingProvider()
    indexer = LocalSemanticKnowledgeIndexer(
        repository,
        provider,
        ContentSafetyPolicy((RejectingClassifier(),)),
    )

    with pytest.raises(LocalEmbeddingError, match=r"^MNEMO_FIXTURE_EMBEDDING_REJECTED$"):
        indexer.index(SemanticKnowledgeIndexRequest(scope()))

    assert provider.passage_calls == 0
    assert repository.list_current_section_embeddings(scope(), provider.model_id, 128) == ()


def test_current_document_path_lookup_is_scope_first(
    repository: KnowledgeDocumentRepository,
) -> None:
    repo = repository
    document = revision("docs/reconciliation.md", "# Reconciliation\nCurrent procedure.")
    repo.apply_sync(scope(), (document,), ())

    assert repo.get_current_revision_by_path(scope(), "docs/reconciliation.md") == document
    with pytest.raises(KnowledgeDocumentNotFound):
        repo.get_current_revision_by_path(scope(2), "docs/reconciliation.md")
