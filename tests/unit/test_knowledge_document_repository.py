from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mnemo_memory.packages.domain import (
    KnowledgeDocumentId,
    KnowledgeDocumentRevision,
    KnowledgeDocumentRevisionId,
    KnowledgeDocumentTombstone,
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.knowledge import KnowledgeDocumentParser, KnowledgeDocumentParseRequest
from mnemo_memory.packages.storage import (
    KnowledgeDocumentConflict,
    KnowledgeDocumentNotFound,
    KnowledgeDocumentSecretRejected,
    ReferenceKnowledgeDocumentRepository,
    SQLiteKnowledgeDocumentRepository,
)

NOW = datetime(2026, 8, 4, tzinfo=UTC)


def scope(seed: int = 1) -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string(f"00000000-0000-4000-8000-{seed:012d}"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string(f"00000000-0000-4000-8001-{seed:012d}"),
        ProjectId.from_string(f"00000000-0000-4000-8002-{seed:012d}"),
    )


def revision(
    path: str,
    content: str,
    *,
    number: int = 1,
    predecessor: KnowledgeDocumentRevisionId | None = None,
    document_id: KnowledgeDocumentId | None = None,
) -> KnowledgeDocumentRevision:
    document = KnowledgeDocumentParser().parse(
        KnowledgeDocumentParseRequest(scope(), path), content
    )
    if document_id is not None:
        document = replace(document, document_id=document_id)
    return KnowledgeDocumentRevision(
        KnowledgeDocumentRevisionId.new(),
        document,
        number,
        predecessor,
        NOW + timedelta(seconds=number),
    )


def test_reference_repository_stores_immutable_revisions_and_scopes_reads() -> None:
    repository = ReferenceKnowledgeDocumentRepository()
    first = revision("notes/decision.md", "# Decision\nUse deterministic parsing.")

    created = repository.apply_sync(scope(), (first,), ())
    current = repository.get_current_revision(scope(), first.document.document_id)
    second = revision(
        "notes/decision.md",
        "# Decision\nUse bounded deterministic parsing.",
        number=2,
        predecessor=first.revision_id,
        document_id=first.document.document_id,
    )
    repository.apply_sync(scope(), (second,), ())

    assert created.applied_revision_count == 1
    assert current == first
    assert repository.get_revision(scope(), first.document.document_id, first.revision_id) == first
    assert repository.get_current_revision(scope(), first.document.document_id) == second
    assert repository.list_active_documents(scope())[0].revision_number == 2
    with pytest.raises(KnowledgeDocumentNotFound):
        repository.get_current_revision(scope(2), first.document.document_id)


def test_reference_repository_rejects_stale_or_secret_writes_without_partial_state() -> None:
    repository = ReferenceKnowledgeDocumentRepository()
    first = revision("note.md", "# note\nSafe.")
    repository.apply_sync(scope(), (first,), ())
    stale = revision(
        "note.md",
        "# note\nChanged.",
        number=2,
        predecessor=KnowledgeDocumentRevisionId.new(),
        document_id=first.document.document_id,
    )
    unsafe = revision("unsafe.md", "# private\napi_key: 1234567890abcdefghijklmnop")

    with pytest.raises(KnowledgeDocumentConflict):
        repository.apply_sync(scope(), (stale,), ())
    with pytest.raises(KnowledgeDocumentSecretRejected):
        repository.apply_sync(scope(), (unsafe,), ())
    assert repository.get_current_revision(scope(), first.document.document_id) == first
    assert len(repository.list_active_documents(scope())) == 1


def test_reference_repository_rejects_an_invalid_batch_without_partial_creation() -> None:
    repository = ReferenceKnowledgeDocumentRepository()
    accepted = revision("accepted.md", "# Accepted\nSafe.")
    invalid = revision(
        "invalid.md",
        "# Invalid\nWrong predecessor.",
        number=2,
        predecessor=KnowledgeDocumentRevisionId.new(),
    )

    with pytest.raises(KnowledgeDocumentConflict):
        repository.apply_sync(scope(), (accepted, invalid), ())

    assert repository.list_active_documents(scope()) == ()


def test_reference_repository_tombstone_removes_payload_and_keeps_source_unavailable() -> None:
    repository = ReferenceKnowledgeDocumentRepository()
    first = revision("note.md", "# note\nDo not retain this after deletion.")
    repository.apply_sync(scope(), (first,), ())
    tombstone = KnowledgeDocumentTombstone(
        first.document.document_id,
        scope(),
        first.document.relative_path,
        first.document.content_digest,
        first.revision_id,
        NOW + timedelta(minutes=1),
    )

    result = repository.apply_sync(scope(), (), (tombstone,))

    assert result.active_documents == ()
    assert result.applied_tombstone_count == 1
    with pytest.raises(KnowledgeDocumentNotFound):
        repository.get_current_revision(scope(), first.document.document_id)


def test_sqlite_repository_is_atomic_scoped_and_removes_deleted_payload(tmp_path: Path) -> None:
    repository = SQLiteKnowledgeDocumentRepository(
        tmp_path / "knowledge.sqlite3", base_directory=tmp_path
    )
    repository.migrate()
    first = revision("notes/decision.md", "# Decision\nUse deterministic parsing.")
    repository.apply_sync(scope(), (first,), ())
    second = revision(
        "notes/decision.md",
        "# Decision\nUse bounded deterministic parsing.",
        number=2,
        predecessor=first.revision_id,
        document_id=first.document.document_id,
    )
    repository.apply_sync(scope(), (second,), ())
    assert repository.get_revision(scope(), first.document.document_id, first.revision_id) == first
    matches = repository.search_current_sections(scope(), ("bounded",), 8, 128)
    assert matches[0].revision == second
    assert matches[0].section.content == "Use bounded deterministic parsing."
    tombstone = KnowledgeDocumentTombstone(
        first.document.document_id,
        scope(),
        second.document.relative_path,
        second.document.content_digest,
        second.revision_id,
        NOW + timedelta(minutes=1),
    )

    repository.apply_sync(scope(), (), (tombstone,))

    assert repository.list_active_documents(scope()) == ()
    with pytest.raises(KnowledgeDocumentNotFound):
        repository.get_current_revision(scope(), first.document.document_id)
    with sqlite3.connect(tmp_path / "knowledge.sqlite3") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM knowledge_document_revisions"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT COUNT(*) FROM knowledge_document_sections"
        ).fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM knowledge_document_links").fetchone() == (
            0,
        )
        assert connection.execute(
            "SELECT relative_path, content_digest FROM knowledge_document_tombstones"
        ).fetchone() == (second.document.relative_path, second.document.content_digest)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_sqlite_repository_rolls_back_invalid_batch_and_hides_cross_scope(tmp_path: Path) -> None:
    repository = SQLiteKnowledgeDocumentRepository(
        tmp_path / "atomic.sqlite3", base_directory=tmp_path
    )
    repository.migrate()
    accepted = revision("accepted.md", "# Accepted\nSafe.")
    invalid = revision(
        "invalid.md",
        "# Invalid\nWrong predecessor.",
        number=2,
        predecessor=KnowledgeDocumentRevisionId.new(),
    )

    with pytest.raises(KnowledgeDocumentConflict):
        repository.apply_sync(scope(), (accepted, invalid), ())

    assert repository.list_active_documents(scope()) == ()
    repository.apply_sync(scope(), (accepted,), ())
    with pytest.raises(KnowledgeDocumentNotFound):
        repository.get_current_revision(scope(2), accepted.document.document_id)
