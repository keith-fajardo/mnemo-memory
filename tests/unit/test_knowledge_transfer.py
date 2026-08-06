"""Portable knowledge history and verified import coverage."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mnemo_memory.packages.application import (
    KnowledgeExportService,
    KnowledgeImportService,
    KnowledgeTransferConflict,
)
from mnemo_memory.packages.domain import (
    KnowledgeDocument,
    KnowledgeDocumentId,
    KnowledgeDocumentLink,
    KnowledgeDocumentRevision,
    KnowledgeDocumentRevisionId,
    KnowledgeDocumentSection,
    KnowledgeDocumentSourceKind,
    KnowledgeDocumentTombstone,
    KnowledgeExportBundle,
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.storage import (
    ReferenceKnowledgeDocumentRepository,
    SQLiteKnowledgeDocumentRepository,
)
from mnemo_memory.packages.storage.contracts import KnowledgeDocumentRepository

NOW = datetime(2026, 8, 6, 8, 0, tzinfo=UTC)


def _scope(seed: int) -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string(f"00000000-0000-0000-0000-{seed:012d}"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string(f"10000000-0000-0000-0000-{seed:012d}"),
        ProjectId.from_string(f"20000000-0000-0000-0000-{seed:012d}"),
    )


def _document(
    scope: MemoryScope,
    document_id: KnowledgeDocumentId,
    path: str,
    seed: str,
    *,
    deleted_payload: bool = False,
) -> KnowledgeDocument:
    content = (
        "This deleted note payload must never cross the transfer boundary."
        if deleted_payload
        else f"Retained knowledge section {seed} with bounded factual context."
    )
    return KnowledgeDocument(
        document_id,
        scope,
        path,
        KnowledgeDocumentSourceKind.OBSIDIAN,
        "sha256:" + seed * 64,
        f"Knowledge {seed}",
        (("owner", "data-platform"),),
        (KnowledgeDocumentSection("Decision", 2, content),),
        (
            KnowledgeDocumentLink("zeta.md", "markdown"),
            KnowledgeDocumentLink("Alpha", "wiki"),
        ),
    )


def _populate(repository: KnowledgeDocumentRepository, scope: MemoryScope) -> None:
    retained_id = KnowledgeDocumentId.from_string("50000000-0000-0000-0000-000000000001")
    deleted_id = KnowledgeDocumentId.from_string("50000000-0000-0000-0000-000000000002")
    first_id = KnowledgeDocumentRevisionId.from_string("60000000-0000-0000-0000-000000000001")
    second_id = KnowledgeDocumentRevisionId.from_string("60000000-0000-0000-0000-000000000002")
    deleted_revision_id = KnowledgeDocumentRevisionId.from_string(
        "60000000-0000-0000-0000-000000000003"
    )
    first = KnowledgeDocumentRevision(
        first_id,
        _document(scope, retained_id, "Architecture/decision.md", "a"),
        1,
        None,
        NOW,
    )
    deleted = KnowledgeDocumentRevision(
        deleted_revision_id,
        _document(
            scope,
            deleted_id,
            "Archive/deleted.md",
            "c",
            deleted_payload=True,
        ),
        1,
        None,
        NOW,
    )
    repository.apply_sync(scope, (first, deleted), ())
    second = KnowledgeDocumentRevision(
        second_id,
        _document(scope, retained_id, "Architecture/renamed.md", "b"),
        2,
        first_id,
        NOW + timedelta(minutes=1),
    )
    repository.apply_sync(scope, (second,), ())
    repository.apply_sync(
        scope,
        (),
        (
            KnowledgeDocumentTombstone(
                deleted_id,
                scope,
                deleted.document.relative_path,
                deleted.document.content_digest,
                deleted_revision_id,
                NOW + timedelta(minutes=2),
            ),
        ),
    )


def test_knowledge_export_is_complete_strict_and_payload_minimized() -> None:
    scope = _scope(1)
    repository = ReferenceKnowledgeDocumentRepository(clock=lambda: NOW + timedelta(minutes=3))
    _populate(repository, scope)
    bundle = KnowledgeExportService(repository).export(
        scope, exported_at=NOW + timedelta(minutes=4)
    )

    assert len(bundle.active_documents) == 1
    assert len(bundle.revisions) == 2
    assert len(bundle.deletions) == 1
    assert bundle.revisions[-1].document.relative_path == "Architecture/renamed.md"
    assert bundle.revisions[-1].document.links == (
        KnowledgeDocumentLink("zeta.md", "markdown"),
        KnowledgeDocumentLink("Alpha", "wiki"),
    )
    assert "deleted note payload" not in bundle.canonical_json()
    assert KnowledgeExportBundle.from_json(bundle.canonical_json()) == bundle

    tampered = json.loads(bundle.canonical_json())
    tampered["revisions"][0]["document"]["sections"][0]["content"] = "tampered"
    with pytest.raises(ValueError, match="digest"):
        KnowledgeExportBundle.from_dict(tampered)
    broken = json.loads(bundle.canonical_json())
    broken["revisions"][1]["predecessor_revision_id"] = None
    with pytest.raises(ValueError):
        KnowledgeExportBundle.from_dict(broken)


def test_knowledge_import_rebases_documents_preserves_revisions_and_replays() -> None:
    source_scope = _scope(1)
    target_scope = _scope(2)
    source = ReferenceKnowledgeDocumentRepository(clock=lambda: NOW + timedelta(minutes=3))
    _populate(source, source_scope)
    bundle = KnowledgeExportService(source).export(
        source_scope, exported_at=NOW + timedelta(minutes=4)
    )
    target = ReferenceKnowledgeDocumentRepository()
    service = KnowledgeImportService(target, target)

    result = service.import_bundle(bundle, target_scope=target_scope)
    imported = KnowledgeExportService(target).export(target_scope, exported_at=bundle.exported_at)

    assert not result.idempotent
    assert (result.active_document_count, result.revision_count, result.deletion_count) == (1, 2, 1)
    assert result.source_content_digest == bundle.content_digest
    assert result.target_content_digest == imported.content_digest
    assert result.source_content_digest != result.target_content_digest
    assert {item.revision_id for item in imported.revisions} == {
        item.revision_id for item in bundle.revisions
    }
    assert {item.document_id for item in imported.active_documents}.isdisjoint(
        item.document_id for item in bundle.active_documents
    )
    assert (
        target.get_current_revision_by_path(target_scope, "Architecture/renamed.md")
        == imported.revisions[-1]
    )
    assert service.import_bundle(bundle, target_scope=target_scope).idempotent

    conflict = ReferenceKnowledgeDocumentRepository()
    _populate(conflict, target_scope)
    with pytest.raises(KnowledgeTransferConflict, match="conflicting state"):
        KnowledgeImportService(conflict, conflict).import_bundle(bundle, target_scope=target_scope)


def test_sqlite_knowledge_export_survives_restart(tmp_path: Path) -> None:
    scope = _scope(1)
    path = tmp_path / "knowledge-transfer.sqlite3"
    repository = SQLiteKnowledgeDocumentRepository(path, base_directory=tmp_path)
    repository.migrate()
    _populate(repository, scope)
    first = KnowledgeExportService(repository).export(scope, exported_at=NOW + timedelta(minutes=4))

    restarted = SQLiteKnowledgeDocumentRepository(path, base_directory=tmp_path)
    restarted.migrate()
    second = KnowledgeExportService(restarted).export(scope, exported_at=first.exported_at)

    assert second == first
    assert len(second.revisions) == 2
    assert len(second.deletions) == 1
    assert "deleted note payload" not in second.canonical_json()
