from __future__ import annotations

from datetime import UTC, datetime

import pytest

from mnemo_memory.packages.domain import (
    KnowledgeDocumentRevision,
    KnowledgeDocumentRevisionId,
    KnowledgeLinkDirection,
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
    KnowledgeLinkNavigationError,
    KnowledgeLinkNavigationRequest,
    KnowledgeLinkNavigator,
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


def revision(path: str, content: str, *, seed: int) -> KnowledgeDocumentRevision:
    document = KnowledgeDocumentParser().parse(
        KnowledgeDocumentParseRequest(scope(), path), content
    )
    return KnowledgeDocumentRevision(
        KnowledgeDocumentRevisionId.from_string(f"00000000-0000-4000-8003-{seed:012d}"),
        document,
        1,
        None,
        NOW,
    )


def test_scoped_current_links_and_backlinks_resolve_with_revision_evidence() -> None:
    repository = ReferenceKnowledgeDocumentRepository()
    architecture = revision(
        "docs/architecture.md", "# Architecture\n[Decision](decision.md)", seed=1
    )
    decision = revision("docs/decision.md", "# Decision\n[[Architecture]]", seed=2)
    repository.apply_sync(scope(), (architecture, decision), ())
    navigator = KnowledgeLinkNavigator(repository)

    outbound = navigator.navigate(
        KnowledgeLinkNavigationRequest(
            scope(), architecture.document.document_id, KnowledgeLinkDirection.OUTBOUND
        )
    )
    backlinks = navigator.navigate(
        KnowledgeLinkNavigationRequest(
            scope(), architecture.document.document_id, KnowledgeLinkDirection.BACKLINKS
        )
    )

    assert len(outbound.relations) == 1
    assert outbound.relations[0].target_document_id == decision.document.document_id
    assert outbound.relations[0].target_revision_id == decision.revision_id
    assert outbound.relations[0].source_relative_path == "docs/architecture.md"
    assert len(backlinks.relations) == 1
    assert backlinks.relations[0].source_document_id == decision.document.document_id
    assert backlinks.relations[0].target_revision_id == architecture.revision_id
    assert outbound.unresolved_link_count == 0


def test_ambiguous_or_external_links_are_not_guessed() -> None:
    repository = ReferenceKnowledgeDocumentRepository()
    start = revision("notes/start.md", "# Start\n[[Shared]]\n[web](https://example.test)", seed=3)
    first = revision("one/shared.md", "# Shared\nFirst", seed=4)
    second = revision("two/shared.md", "# Shared\nSecond", seed=5)
    repository.apply_sync(scope(), (start, first, second), ())

    result = KnowledgeLinkNavigator(repository).navigate(
        KnowledgeLinkNavigationRequest(
            scope(), start.document.document_id, KnowledgeLinkDirection.OUTBOUND
        )
    )

    assert result.relations == ()
    assert result.unresolved_link_count == 2


def test_link_navigation_is_scope_first_and_does_not_disclose_foreign_document() -> None:
    repository = ReferenceKnowledgeDocumentRepository()
    local = revision("docs/local.md", "# Local", seed=6)
    foreign_document = KnowledgeDocumentParser().parse(
        KnowledgeDocumentParseRequest(scope(2), "docs/foreign.md"), "# Foreign"
    )
    foreign = KnowledgeDocumentRevision(
        KnowledgeDocumentRevisionId.from_string("00000000-0000-4000-8003-000000000007"),
        foreign_document,
        1,
        None,
        NOW,
    )
    repository.apply_sync(scope(), (local,), ())
    repository.apply_sync(scope(2), (foreign,), ())

    with pytest.raises(KnowledgeLinkNavigationError, match="knowledge document was not found"):
        KnowledgeLinkNavigator(repository).navigate(
            KnowledgeLinkNavigationRequest(
                scope(), foreign.document.document_id, KnowledgeLinkDirection.OUTBOUND
            )
        )
