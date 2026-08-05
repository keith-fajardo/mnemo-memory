from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mnemo_memory.packages.application import (
    CheckpointApplicationEpisodicEventNotFound,
    CheckpointApplicationService,
    CorrectApprovedEpisodicEvent,
    GetApprovedEpisodicEventRecord,
    GetCheckpointContext,
    RecordApprovedEpisodicEvent,
    RetractApprovedEpisodicEvent,
    SetApprovedEpisodicEventPin,
)
from mnemo_memory.packages.domain import (
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
    ApprovedEpisodicEventRepository,
    ReferenceApprovedEpisodicEventRepository,
    ReferenceCheckpointRepository,
    SQLiteCheckpointRepository,
    SQLiteMigrationError,
)

NOW = datetime(2026, 8, 5, 11, 0, tzinfo=UTC)


def _scope() -> MemoryScope:
    return MemoryScope(
        OwnerId.new(),
        ScopeLevel.TASK,
        Visibility.PROJECT,
        WorkspaceId.new(),
        ProjectId.new(),
        SessionId.new(),
        TaskId.new(),
    )


def _evidence(seed: str, *, user: bool = False) -> EvidenceReference:
    return EvidenceReference(
        EvidenceId.new(),
        SourceId.new(),
        EvidenceSourceType.USER_CORRECTION if user else EvidenceSourceType.TOOL_RESULT,
        SourceTrustClass.USER_CORRECTION if user else SourceTrustClass.VERIFIED_TOOL_RESULT,
        f"fixture://approved-pin/{seed}",
        "sha256:" + ("b" if user else "a") * 64,
        EvidenceLocation(f"fixture://approved-pin/{seed}"),
        NOW,
        VerificationStatus.VERIFIED,
    )


def _service(
    adapter: str, tmp_path: Path
) -> tuple[CheckpointApplicationService, ApprovedEpisodicEventRepository]:
    if adapter == "reference":
        events = ReferenceApprovedEpisodicEventRepository()
        return (
            CheckpointApplicationService(
                ReferenceCheckpointRepository(),
                clock=lambda: NOW,
                approved_event_repository=events,
            ),
            events,
        )
    sqlite_events = SQLiteCheckpointRepository(
        tmp_path / "approved-pin.sqlite3", base_directory=tmp_path
    )
    sqlite_events.migrate()
    return (
        CheckpointApplicationService(
            sqlite_events,
            clock=lambda: NOW,
            approved_event_repository=sqlite_events,
        ),
        sqlite_events,
    )


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_pin_is_scoped_idempotent_prioritized_and_follows_governance(
    adapter: str, tmp_path: Path
) -> None:
    service, repository_value = _service(adapter, tmp_path)
    repository = repository_value
    scope = _scope()
    older = service.record_approved_event(
        RecordApprovedEpisodicEvent(
            scope,
            ApprovedEventKind.DECISION,
            "Retain the verified project grain.",
            "pin:older",
            (_evidence("older"),),
        )
    ).event
    newer = service.record_approved_event(
        RecordApprovedEpisodicEvent(
            scope,
            ApprovedEventKind.TOOL_OUTCOME,
            "The later bounded check passed.",
            "pin:newer",
            (_evidence("newer"),),
        )
    ).event
    pin = SetApprovedEpisodicEventPin(
        scope,
        older.event_id,
        True,
        "pin-action:older",
        (_evidence("pin", user=True),),
    )
    assert service.set_approved_event_pin(pin).idempotent is False
    assert service.set_approved_event_pin(pin).idempotent is True
    assert service.get_approved_event_record(
        GetApprovedEpisodicEventRecord(scope, older.event_id)
    ).pinned
    listed = repository.list_approved_events(scope, limit=10)
    assert [item.event_id for item in listed.items[:2]] == [older.event_id, newer.event_id]
    packet = service.get_context(
        GetCheckpointContext(
            scope,
            include_approved_events=True,
            maximum_approved_events=1,
        )
    )
    assert len(packet.episodic_memories) == 1
    assert older.summary in packet.episodic_memories[0].content

    service.set_approved_event_pin(
        SetApprovedEpisodicEventPin(
            scope,
            older.event_id,
            False,
            "pin-action:older:off",
            (_evidence("unpin", user=True),),
        )
    )
    assert repository.list_approved_events(scope, limit=10).items[0].event_id == newer.event_id
    service.set_approved_event_pin(
        SetApprovedEpisodicEventPin(
            scope,
            older.event_id,
            True,
            "pin-action:older:on-again",
            (_evidence("repin", user=True),),
        )
    )

    correction = service.correct_approved_event(
        CorrectApprovedEpisodicEvent(
            scope,
            older.event_id,
            "Retain the corrected verified transaction grain.",
            "pin:replacement",
            "The verified correction supersedes the pinned fact.",
            "pin:correct",
            (_evidence("correction"),),
        )
    )
    assert correction.replacement is not None
    replacement_id = correction.replacement.event_id
    assert correction.target.pinned is False
    assert correction.replacement.pinned is True
    assert repository.list_approved_events(scope, limit=10).items[0].event_id == replacement_id

    retracted = service.retract_approved_event(
        RetractApprovedEpisodicEvent(
            scope,
            replacement_id,
            "The user withdrew the corrected pinned fact.",
            "pin:retract",
            (_evidence("retraction"),),
        )
    )
    assert retracted.target.pinned is False
    assert retracted.target.event is None

    other = _scope()
    with pytest.raises(CheckpointApplicationEpisodicEventNotFound):
        service.set_approved_event_pin(
            SetApprovedEpisodicEventPin(
                other,
                newer.event_id,
                True,
                "pin-action:cross-scope",
                (_evidence("cross-scope", user=True),),
            )
        )

    if adapter == "sqlite":
        with sqlite3.connect(tmp_path / "approved-pin.sqlite3") as connection:
            actions = connection.execute(
                "SELECT event_id, pinned FROM approved_episodic_event_pin_actions "
                "ORDER BY action_sequence"
            ).fetchall()
        assert len(actions) == 6
        assert actions[-1] == (str(replacement_id), 0)


def test_migration_27_is_atomic_and_recoverable_from_version_26(tmp_path: Path) -> None:
    path = tmp_path / "pin-migration.sqlite3"
    repository = SQLiteCheckpointRepository(path, base_directory=tmp_path)
    repository.migrate()
    assert repository.schema_version() == 27
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER approved_episodic_event_pin_target_scope_match")
        connection.execute("DROP TABLE approved_episodic_event_pin_evidence")
        connection.execute("DROP TABLE approved_episodic_event_pin_actions")
        connection.execute("DELETE FROM schema_migrations WHERE version = 27")
    assert repository.schema_version() == 26

    with pytest.raises(SQLiteMigrationError, match="injected migration failure"):
        repository.migrate(fail_after_version=27)
    assert repository.schema_version() == 26
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'approved_episodic_event_pin_actions'"
            ).fetchone()
            is None
        )

    repository.migrate()
    assert repository.schema_version() == 27
