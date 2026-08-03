"""Backend-neutral contract for explicit approved episodic facts."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mnemo_memory.packages.domain import (
    ApprovedEpisodicEvent,
    ApprovedEventKind,
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    SessionId,
    SourceId,
    SourceTrustClass,
    TaskId,
    VerificationStatus,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.storage import (
    ReferenceApprovedEpisodicEventRepository,
    SQLiteCheckpointRepository,
    SQLiteMigrationError,
)
from mnemo_memory.packages.storage.contracts import (
    ApprovedEpisodicEventConflict,
    ApprovedEpisodicEventNotFound,
    ApprovedEpisodicEventRepository,
    ApprovedEpisodicEventStorageFailure,
)

NOW = datetime(2026, 8, 3, tzinfo=UTC)


def _scope() -> MemoryScope:
    return MemoryScope(
        OwnerId.new(),
        ScopeLevel.TASK,
        Visibility.PROJECT,
        WorkspaceId.new(),
        ProjectId.new(),
        session_id=SessionId.new(),
        task_id=TaskId.new(),
    )


def _evidence() -> EvidenceReference:
    return EvidenceReference(
        EvidenceId.new(),
        SourceId.new(),
        EvidenceSourceType.CHECKPOINT,
        SourceTrustClass.USER_AUTHORED,
        "fixture://approved-event",
        "sha256:" + "a" * 64,
        EvidenceLocation("fixture://approved-event"),
        NOW,
        VerificationStatus.VERIFIED,
    )


def _event(
    scope: MemoryScope,
    *,
    key: str = "decision:1",
    summary: str = "Use the verified source grain for the reconciliation.",
    kind: ApprovedEventKind = ApprovedEventKind.DECISION,
    at: datetime = NOW,
) -> ApprovedEpisodicEvent:
    return ApprovedEpisodicEvent.create(
        scope=scope,
        kind=kind,
        summary=summary,
        source_event_key=key,
        occurred_at=at,
        evidence_references=(_evidence(),),
    )


@pytest.fixture(params=["reference", "sqlite"])
def repository(request: pytest.FixtureRequest, tmp_path: Path) -> ApprovedEpisodicEventRepository:
    if request.param == "reference":
        return ReferenceApprovedEpisodicEventRepository()
    value = SQLiteCheckpointRepository(
        tmp_path / "approved-events.sqlite3", base_directory=tmp_path
    )
    value.migrate()
    return value


def test_approved_event_contract_is_scoped_immutable_idempotent_and_ordered(
    repository: ApprovedEpisodicEventRepository,
) -> None:
    item_scope = _scope()
    first = _event(item_scope, key="decision:1", at=NOW)
    second = _event(
        item_scope,
        key="failure:2",
        kind=ApprovedEventKind.FAILURE,
        summary="The prior comparison used a stale seed snapshot.",
        at=NOW + timedelta(seconds=1),
    )

    assert repository.append_approved_event(first).idempotent is False
    assert repository.append_approved_event(first).idempotent is True
    assert repository.append_approved_event(second).idempotent is False
    assert repository.get_approved_event(item_scope, first.event_id) == first
    page = repository.list_approved_events(item_scope, limit=1)
    assert page.items == (second,)
    assert page.next_offset == 1
    assert repository.list_approved_events(item_scope, offset=1).items == (first,)
    assert all(item.evidence_references for item in page.items)


def test_approved_event_contract_preserves_scope_non_disclosure_and_conflict(
    repository: ApprovedEpisodicEventRepository,
) -> None:
    item_scope = _scope()
    event = _event(item_scope)
    repository.append_approved_event(event)

    wrong_scope = _scope()
    assert repository.list_approved_events(wrong_scope).items == ()
    with pytest.raises(ApprovedEpisodicEventNotFound):
        repository.get_approved_event(wrong_scope, event.event_id)
    with pytest.raises(ApprovedEpisodicEventConflict):
        repository.append_approved_event(
            _event(item_scope, summary="A conflicting event with the same source key.")
        )


def test_sqlite_approved_events_are_durable_and_fail_atomically(tmp_path: Path) -> None:
    database = tmp_path / "approved-events.sqlite3"
    repository = SQLiteCheckpointRepository(database, base_directory=tmp_path)
    repository.migrate()
    item_scope = _scope()
    event = _event(item_scope)
    assert repository.append_approved_event(event).event == event
    reopened = SQLiteCheckpointRepository(database, base_directory=tmp_path)
    assert reopened.get_approved_event(item_scope, event.event_id) == event

    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TRIGGER reject_approved_event_evidence BEFORE INSERT "
            "ON approved_episodic_event_evidence "
            "BEGIN SELECT RAISE(ABORT, 'synthetic approved event failure'); END"
        )
    failed = _event(
        item_scope,
        key="tool:3",
        kind=ApprovedEventKind.TOOL_OUTCOME,
        summary="The validation command completed with the expected result.",
    )
    with pytest.raises(ApprovedEpisodicEventStorageFailure):
        repository.append_approved_event(failed)
    assert repository.list_approved_events(item_scope).items == (event,)
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_approved_event_migration_rolls_back_as_one_step(tmp_path: Path) -> None:
    database = tmp_path / "approved-events.sqlite3"
    repository = SQLiteCheckpointRepository(database, base_directory=tmp_path)
    repository.migrate()
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE approved_episodic_event_evidence")
        connection.execute("DROP TABLE approved_episodic_events")
        connection.execute("DELETE FROM schema_migrations WHERE version = 7")

    with pytest.raises(SQLiteMigrationError, match="injected migration failure"):
        repository.migrate(fail_after_version=7)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone() == (6,)
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name = 'approved_episodic_events'"
            ).fetchone()
            is None
        )
    repository.migrate()
