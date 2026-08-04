from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from mnemo_memory.packages.application import (
    KnowledgeApplicationRejected,
    KnowledgeDocumentApplicationService,
    SynchronizeKnowledgeDocuments,
)
from mnemo_memory.packages.domain import (
    KnowledgeDocument,
    KnowledgeDocumentRevisionId,
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.knowledge import KnowledgeDocumentParser, KnowledgeDocumentParseRequest
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


def document(path: str, content: str) -> KnowledgeDocument:
    return KnowledgeDocumentParser().parse(KnowledgeDocumentParseRequest(scope(), path), content)


def test_service_creates_revises_renames_and_tombstones_deterministically() -> None:
    repository = ReferenceKnowledgeDocumentRepository()
    identities = iter(
        (
            KnowledgeDocumentRevisionId.from_string("00000000-0000-4000-8000-000000000101"),
            KnowledgeDocumentRevisionId.from_string("00000000-0000-4000-8000-000000000102"),
        )
    )
    service = KnowledgeDocumentApplicationService(
        repository, clock=lambda: NOW, revision_id_factory=lambda: next(identities)
    )
    original = document("notes/decision.md", "# Decision\nUse bounded parsing.")

    created = service.synchronize(SynchronizeKnowledgeDocuments(scope(), (original,)))
    renamed = document("notes/final-decision.md", "# Decision\nUse bounded parsing.")
    renamed_result = service.synchronize(SynchronizeKnowledgeDocuments(scope(), (renamed,)))
    deleted = service.synchronize(SynchronizeKnowledgeDocuments(scope(), ()))

    assert created.store_result.applied_revision_count == 1
    assert created.plan.actions[0].kind.value == "added"
    assert renamed_result.store_result.applied_revision_count == 1
    assert renamed_result.plan.actions[0].kind.value == "renamed"
    assert deleted.store_result.applied_tombstone_count == 1
    assert deleted.store_result.active_documents == ()


def test_service_rejects_secret_before_any_repository_mutation() -> None:
    repository = ReferenceKnowledgeDocumentRepository()
    service = KnowledgeDocumentApplicationService(
        repository,
        clock=lambda: NOW + timedelta(seconds=1),
        revision_id_factory=lambda: KnowledgeDocumentRevisionId.from_string(
            "00000000-0000-4000-8000-000000000103"
        ),
    )

    with pytest.raises(KnowledgeApplicationRejected):
        service.synchronize(
            SynchronizeKnowledgeDocuments(
                scope(), (document("private.md", "# Private\napi_key: 1234567890abcdefghijklmnop"),)
            )
        )

    assert repository.list_active_documents(scope()) == ()


def test_service_rejects_cross_scope_documents_before_storage() -> None:
    service = KnowledgeDocumentApplicationService(
        ReferenceKnowledgeDocumentRepository(),
        clock=lambda: NOW,
        revision_id_factory=KnowledgeDocumentRevisionId.new,
    )
    foreign = KnowledgeDocumentParser().parse(
        KnowledgeDocumentParseRequest(scope(2), "foreign.md"), "# Foreign\nNever mix scopes."
    )

    with pytest.raises(ValueError, match="scope mismatch"):
        service.synchronize(SynchronizeKnowledgeDocuments(scope(), (foreign,)))
