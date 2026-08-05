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
    KnowledgeDocumentSectionMatch,
    KnowledgeDocumentTombstone,
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    Sensitivity,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.knowledge import KnowledgeDocumentParser, KnowledgeDocumentParseRequest
from mnemo_memory.packages.policy import (
    ContentSafetyDecision,
    KnowledgeDocumentSafetyPolicy,
)
from mnemo_memory.packages.storage import (
    KnowledgeDocumentConflict,
    KnowledgeDocumentNotFound,
    KnowledgeDocumentRepository,
    KnowledgeDocumentSecretRejected,
    ReferenceKnowledgeDocumentRepository,
    SQLiteKnowledgeDocumentRepository,
    SQLiteMigrationError,
)

NOW = datetime(2026, 8, 4, tzinfo=UTC)


class RejectingKnowledgeClassifier:
    def classify(self, values: tuple[str, ...]) -> ContentSafetyDecision:
        assert values
        return ContentSafetyDecision(
            False, Sensitivity.PROHIBITED, "MNEMO_FIXTURE_KNOWLEDGE_REJECTED"
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
    path: str,
    content: str,
    *,
    number: int = 1,
    predecessor: KnowledgeDocumentRevisionId | None = None,
    document_id: KnowledgeDocumentId | None = None,
    scope_value: MemoryScope | None = None,
) -> KnowledgeDocumentRevision:
    actual_scope = scope() if scope_value is None else scope_value
    document = KnowledgeDocumentParser().parse(
        KnowledgeDocumentParseRequest(actual_scope, path), content
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


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_pluggable_classifier_rejection_prevents_knowledge_persistence(
    adapter: str, tmp_path: Path
) -> None:
    policy = KnowledgeDocumentSafetyPolicy((RejectingKnowledgeClassifier(),))
    repository: KnowledgeDocumentRepository
    if adapter == "reference":
        repository = ReferenceKnowledgeDocumentRepository(policy)
    else:
        repository = SQLiteKnowledgeDocumentRepository(
            tmp_path / "classified-knowledge.sqlite3",
            base_directory=tmp_path,
            policy=policy,
        )
        repository.migrate()
    item = revision("notes/safe.md", "# Safe\nOrdinary bounded content.")

    with pytest.raises(KnowledgeDocumentSecretRejected):
        repository.apply_sync(scope(), (item,), ())

    assert repository.list_active_documents(scope()) == ()


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
        # The rebuildable FTS projection must not retain text after an explicit deletion.
        assert connection.execute(
            "SELECT COUNT(*) FROM knowledge_document_section_fts"
        ).fetchone() == (0,)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_sqlite_fts_projection_contains_only_current_scoped_revisions_and_migrates_v10_data(
    tmp_path: Path,
) -> None:
    path = tmp_path / "fts.sqlite3"
    repository = SQLiteKnowledgeDocumentRepository(path, base_directory=tmp_path)
    repository.migrate()
    first = revision("notes/decision.md", "# Café post-hook\nDeprecated plan.")
    repository.apply_sync(scope(), (first,), ())
    second = revision(
        "notes/decision.md",
        "# Café post-hook\nCurrent bounded plan.",
        number=2,
        predecessor=first.revision_id,
        document_id=first.document.document_id,
    )
    private = revision(
        "notes/private.md", "# Café post-hook\nOther scope only.", scope_value=scope(2)
    )
    repository.apply_sync(scope(), (second,), ())
    repository.apply_sync(scope(2), (private,), ())

    # FTS uses the same literal-token behavior as the reference adapter: accents normalize,
    # hyphenated tokens remain literal, old revisions and other scopes cannot be retrieved.
    assert repository.search_current_sections(scope(), ("cafe", "post-hook"), 8, 128) == (
        KnowledgeDocumentSectionMatch(second, 0, second.document.sections[0], 8),
    )
    assert repository.search_current_sections(scope(), ("deprecated",), 8, 128) == ()
    assert repository.search_current_sections(scope(2), ("current",), 8, 128) == ()

    # Simulate a valid pre-0011 database: the forward migration rehydrates only its active rows.
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE dbt_source_freshness_results")
        connection.execute("DROP TABLE dbt_source_freshness_artifacts")
        connection.execute("DROP TABLE dbt_run_result_timings")
        connection.execute("DROP TABLE dbt_run_results")
        connection.execute("DROP TABLE dbt_catalog_columns")
        connection.execute("DROP TABLE dbt_catalog_relations")
        connection.execute("DROP TABLE dbt_supplemental_artifacts")
        connection.execute("DROP TABLE approved_episodic_event_governance_evidence")
        connection.execute("DROP TABLE approved_episodic_event_governance")
        connection.execute("DROP TABLE knowledge_section_embeddings")
        connection.execute("DROP TABLE knowledge_document_section_fts")
        connection.execute("DROP TRIGGER IF EXISTS episodic_memory_purge_guard")
        connection.execute("DROP TRIGGER IF EXISTS task_activity_purge_guard")
        connection.execute("DROP TABLE episodic_memory_deletions")
        connection.execute("DROP TABLE task_activity_event_deletions")
        connection.execute("DELETE FROM schema_migrations WHERE version >= 11")
    repository.migrate()
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT revision_id FROM knowledge_document_section_fts "
            "WHERE owner_id = ? AND project_id = ? ORDER BY revision_id",
            (str(scope().owner_id), str(scope().project_id)),
        ).fetchall() == [(str(second.revision_id),)]


def test_knowledge_fts_migration_is_atomic(tmp_path: Path) -> None:
    path = tmp_path / "fts-migration.sqlite3"
    repository = SQLiteKnowledgeDocumentRepository(path, base_directory=tmp_path)
    repository.migrate()
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE dbt_source_freshness_results")
        connection.execute("DROP TABLE dbt_source_freshness_artifacts")
        connection.execute("DROP TABLE dbt_run_result_timings")
        connection.execute("DROP TABLE dbt_run_results")
        connection.execute("DROP TABLE dbt_catalog_columns")
        connection.execute("DROP TABLE dbt_catalog_relations")
        connection.execute("DROP TABLE dbt_supplemental_artifacts")
        connection.execute("DROP TABLE approved_episodic_event_governance_evidence")
        connection.execute("DROP TABLE approved_episodic_event_governance")
        connection.execute("DROP TABLE knowledge_section_embeddings")
        connection.execute("DROP TABLE knowledge_document_section_fts")
        connection.execute("DROP TRIGGER IF EXISTS episodic_memory_purge_guard")
        connection.execute("DROP TRIGGER IF EXISTS task_activity_purge_guard")
        connection.execute("DROP TABLE episodic_memory_deletions")
        connection.execute("DROP TABLE task_activity_event_deletions")
        connection.execute("DELETE FROM schema_migrations WHERE version >= 11")

    with pytest.raises(SQLiteMigrationError, match="injected migration failure"):
        repository.migrate(fail_after_version=11)

    with sqlite3.connect(path) as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                "AND name = 'knowledge_document_section_fts'"
            ).fetchone()
            is None
        )
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone() == (10,)


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
