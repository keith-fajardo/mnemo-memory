"""Logical expiry and physical purge coverage for minimized task activity events."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mnemo_memory.packages.domain import (
    EpisodicExtractionProposal,
    EpisodicMemoryCandidate,
    EpisodicMemoryKind,
    EventId,
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    MemoryScope,
    OwnerId,
    ProjectId,
    RetentionPolicyId,
    RetentionSchedule,
    ScopeLevel,
    Sensitivity,
    SessionId,
    SourceId,
    SourceTrustClass,
    TaskActivityActor,
    TaskActivityEvent,
    TaskActivityEventExpiration,
    TaskActivityEventKind,
    TaskActivityEventPurge,
    TaskActivityEventRetentionTarget,
    TaskId,
    VerificationStatus,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.episodic import (
    EpisodicRetentionService,
    TaskActivityRetentionService,
)
from mnemo_memory.packages.storage import (
    EpisodicMemoryCandidateConflict,
    ReferenceEpisodicMemoryCandidateRepository,
    ReferenceEventOutboxRepository,
    ReferenceTaskActivityEventRepository,
    SQLiteCheckpointRepository,
    SQLiteMigrationError,
    TaskActivityEventConflict,
    TaskActivityEventNotFound,
    TaskActivityRetentionConflict,
    TaskActivityRetentionNotFound,
    TaskActivityRetentionStorageFailure,
)
from scripts.sqlite_migration_test_support import (
    drop_checkpoint_deletion_schema as _drop_checkpoint_deletion_schema,
)

NOW = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)
DUE = NOW + timedelta(days=30)
SWEEP_TIME = DUE + timedelta(hours=1)
PURGE_TIME = SWEEP_TIME + timedelta(minutes=1)

EventRepository = ReferenceTaskActivityEventRepository | SQLiteCheckpointRepository
RetentionRepository = ReferenceEpisodicMemoryCandidateRepository | SQLiteCheckpointRepository
OutboxRepository = ReferenceEventOutboxRepository | SQLiteCheckpointRepository


def _scope(seed: int = 1) -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string(f"05000000-0000-4000-8000-{seed:012d}"),
        ScopeLevel.TASK,
        Visibility.PROJECT,
        WorkspaceId.from_string(f"15000000-0000-4000-8000-{seed:012d}"),
        ProjectId.from_string(f"25000000-0000-4000-8000-{seed:012d}"),
        SessionId.from_string(f"35000000-0000-4000-8000-{seed:012d}"),
        TaskId.from_string(f"45000000-0000-4000-8000-{seed:012d}"),
    )


def _evidence(seed: int) -> EvidenceReference:
    return EvidenceReference(
        EvidenceId.from_string(f"55000000-0000-4000-8000-{seed:012d}"),
        SourceId.from_string(f"65000000-0000-4000-8000-{seed:012d}"),
        EvidenceSourceType.AGENT_EVENT,
        SourceTrustClass.APPROVED_CHECKPOINT,
        f"fixture://task-activity-retention/{seed}",
        "sha256:" + "a" * 64,
        EvidenceLocation(f"fixture://task-activity-retention/{seed}"),
        NOW,
        VerificationStatus.VERIFIED,
    )


def _event(scope: MemoryScope, *, seed: int, permanent: bool = False) -> TaskActivityEvent:
    return TaskActivityEvent.create(
        scope=scope,
        kind=TaskActivityEventKind.TASK_OUTCOME,
        actor=TaskActivityActor.AGENT,
        summary=f"The minimized task activity outcome {seed} passed verification.",
        source_event_key=f"task-activity-retention:{seed}",
        sensitivity=Sensitivity.RESTRICTED,
        retention=RetentionSchedule(
            RetentionPolicyId.from_string(f"75000000-0000-4000-8000-{seed:012d}"),
            permanent,
            NOW,
            NOW,
            NOW,
            None,
            None if permanent else DUE,
        ),
        occurred_at=NOW,
        evidence_references=(_evidence(seed),),
    )


def _candidate(event: TaskActivityEvent, *, seed: int) -> EpisodicMemoryCandidate:
    return EpisodicMemoryCandidate.create(
        source_event=event,
        proposal=EpisodicExtractionProposal(
            EpisodicMemoryKind.OUTCOME,
            f"The source-bound candidate {seed} remains retention governed.",
            0.91,
            Sensitivity.RESTRICTED,
        ),
        proposal_index=0,
        sensitivity=Sensitivity.RESTRICTED,
        extractor_version=f"task-retention-extractor-v{seed}",
        provider_id="luna-fixture",
        model_id="gpt-5.6-luna-fixture",
        prompt_version="task-retention-prompt-v1",
        created_at=NOW,
    )


def _repositories(
    adapter: str, tmp_path: Path, *, name: str
) -> tuple[EventRepository, RetentionRepository, OutboxRepository]:
    if adapter == "reference":
        events = ReferenceTaskActivityEventRepository()
        memories = ReferenceEpisodicMemoryCandidateRepository(events)
        return events, memories, events.outbox
    sqlite = SQLiteCheckpointRepository(
        tmp_path / f"task-activity-retention-{name}.sqlite3", base_directory=tmp_path
    )
    sqlite.migrate()
    return sqlite, sqlite, sqlite


def test_task_activity_retention_contracts_are_strict_and_payload_free() -> None:
    event = _event(_scope(), seed=1)
    target = TaskActivityEventRetentionTarget(event.event_id, event.scope, event.retention)
    expiration = TaskActivityEventExpiration.create(target, SWEEP_TIME)
    purge = TaskActivityEventPurge.create(expiration, PURGE_TIME)

    assert TaskActivityEventExpiration.from_dict(expiration.to_dict()) == expiration
    assert TaskActivityEventPurge.from_dict(purge.to_dict()) == purge
    assert set(expiration.to_dict()) == {
        "expiration_id",
        "event_id",
        "scope",
        "retention_policy_id",
        "scheduled_expires_at",
        "expired_at",
    }
    assert set(purge.to_dict()) == {
        "purge_id",
        "expiration_id",
        "event_id",
        "scope",
        "purged_at",
    }
    with pytest.raises(ValueError, match="not due"):
        TaskActivityEventExpiration.create(target, NOW)
    with pytest.raises(ValueError, match="before expiration"):
        TaskActivityEventPurge.create(expiration, DUE)
    permanent = _event(_scope(), seed=2, permanent=True)
    with pytest.raises(ValueError, match="permanent"):
        TaskActivityEventExpiration.create(
            TaskActivityEventRetentionTarget(
                permanent.event_id, permanent.scope, permanent.retention
            ),
            SWEEP_TIME,
        )


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_expire_and_purge_hides_payload_cancels_job_and_prevents_resurrection(
    adapter: str, tmp_path: Path
) -> None:
    events, retention, outbox = _repositories(adapter, tmp_path, name=f"main-{adapter}")
    event = _event(_scope(), seed=3)
    events.append_task_activity_event(event)
    service = TaskActivityRetentionService(retention)

    assert service.expire_due(event.scope, as_of=NOW).expirations == ()
    expiration = service.expire_due(event.scope, as_of=SWEEP_TIME).expirations[0]
    assert retention.get_task_activity_expiration(event.scope, event.event_id) == expiration
    with pytest.raises(TaskActivityEventNotFound):
        events.get_task_activity_event(event.scope, event.event_id)
    assert events.list_task_activity_events(event.scope).items == ()
    with pytest.raises(TaskActivityEventConflict):
        events.append_task_activity_event(event)

    purge_result = service.purge_expired(event.scope, purged_at=PURGE_TIME)
    assert purge_result.idempotent is False
    purge = purge_result.purges[0]
    assert retention.get_task_activity_purge(event.scope, event.event_id) == purge
    assert retention.get_task_activity_expiration(event.scope, event.event_id) == expiration
    assert retention.list_unpurged_task_activity_expirations(event.scope) == ()
    assert service.purge_expired(event.scope, purged_at=PURGE_TIME).purges == ()
    assert retention.apply_task_activity_purges((purge,)).idempotent is True
    with pytest.raises(TaskActivityRetentionNotFound):
        retention.get_task_activity_purge(_scope(2), event.event_id)
    assert (
        outbox.claim_event_jobs(
            event.scope,
            worker_id="retention-worker",
            now=PURGE_TIME,
            lease_expires_at=PURGE_TIME + timedelta(minutes=1),
            limit=10,
        )
        == ()
    )

    if isinstance(retention, SQLiteCheckpointRepository):
        with sqlite3.connect(retention.path) as connection:
            assert (
                connection.execute(
                    "SELECT 1 FROM task_activity_events WHERE event_id = ?",
                    (str(event.event_id),),
                ).fetchone()
                is None
            )
            assert (
                connection.execute(
                    "SELECT 1 FROM task_activity_event_evidence WHERE event_id = ?",
                    (str(event.event_id),),
                ).fetchone()
                is None
            )
            assert (
                connection.execute(
                    "SELECT 1 FROM evidence WHERE evidence_id = ?",
                    (str(event.evidence_references[0].evidence_id),),
                ).fetchone()
                is None
            )
            assert connection.execute(
                "SELECT purge_id FROM task_activity_event_expirations WHERE event_id = ?",
                (str(event.event_id),),
            ).fetchone() == (str(purge.purge_id),)
        reopened = SQLiteCheckpointRepository(retention.path, base_directory=tmp_path)
        assert reopened.get_task_activity_purge(event.scope, event.event_id) == purge
        with pytest.raises(TaskActivityEventConflict):
            reopened.append_task_activity_event(event)


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_permanent_task_activity_is_never_expired(adapter: str, tmp_path: Path) -> None:
    events, retention, _ = _repositories(adapter, tmp_path, name=f"permanent-{adapter}")
    event = _event(_scope(), seed=4, permanent=True)
    events.append_task_activity_event(event)

    result = TaskActivityRetentionService(retention).expire_due(
        event.scope, as_of=SWEEP_TIME + timedelta(days=1000)
    )

    assert result.expirations == ()
    assert events.get_task_activity_event(event.scope, event.event_id) == event


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_source_purge_waits_for_candidate_purge_and_preserves_candidate_tombstone(
    adapter: str, tmp_path: Path
) -> None:
    events, retention, _ = _repositories(adapter, tmp_path, name=f"dependent-{adapter}")
    event = _event(_scope(), seed=5)
    candidate = _candidate(event, seed=5)
    events.append_task_activity_event(event)
    retention.store_episodic_memory_candidates((candidate,))
    task_service = TaskActivityRetentionService(retention)
    task_service.expire_due(event.scope, as_of=SWEEP_TIME)

    with pytest.raises(TaskActivityRetentionConflict, match="dependent"):
        task_service.purge_expired(event.scope, purged_at=PURGE_TIME)

    memory_service = EpisodicRetentionService(retention)
    memory_expiration = memory_service.expire_due(event.scope, as_of=SWEEP_TIME).expirations[0]
    memory_purge = memory_service.purge_expired(event.scope, purged_at=PURGE_TIME).purges[0]
    task_purge = task_service.purge_expired(event.scope, purged_at=PURGE_TIME).purges[0]

    assert (
        retention.get_episodic_memory_expiration(event.scope, candidate.memory_id)
        == memory_expiration
    )
    assert retention.get_episodic_memory_purge(event.scope, candidate.memory_id) == memory_purge
    assert retention.get_task_activity_purge(event.scope, event.event_id) == task_purge
    with pytest.raises(EpisodicMemoryCandidateConflict):
        retention.store_episodic_memory_candidates((candidate,))


def test_reference_and_sqlite_task_retention_are_identical(tmp_path: Path) -> None:
    ref_events, reference, _ = _repositories("reference", tmp_path, name="parity-ref")
    sql_events, sqlite, _ = _repositories("sqlite", tmp_path, name="parity-sqlite")
    scope = _scope()
    for seed, permanent in ((6, False), (7, True)):
        event = _event(scope, seed=seed, permanent=permanent)
        ref_events.append_task_activity_event(event)
        sql_events.append_task_activity_event(event)
    ref_service = TaskActivityRetentionService(reference)
    sql_service = TaskActivityRetentionService(sqlite)

    assert ref_service.expire_due(scope, as_of=SWEEP_TIME) == sql_service.expire_due(
        scope, as_of=SWEEP_TIME
    )
    assert reference.list_unpurged_task_activity_expirations(
        scope
    ) == sqlite.list_unpurged_task_activity_expirations(scope)
    assert ref_service.purge_expired(scope, purged_at=PURGE_TIME) == sql_service.purge_expired(
        scope, purged_at=PURGE_TIME
    )


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_conflicting_expiration_and_purge_batches_are_atomic(adapter: str, tmp_path: Path) -> None:
    events, retention, _ = _repositories(adapter, tmp_path, name=f"atomic-{adapter}")
    scope = _scope()
    first = _event(scope, seed=8)
    second = _event(scope, seed=9)
    events.append_task_activity_event(first)
    events.append_task_activity_event(second)
    targets = retention.list_due_task_activity_retention(scope, as_of=SWEEP_TIME)
    expirations = tuple(
        TaskActivityEventExpiration.create(target, SWEEP_TIME) for target in targets
    )
    first_expiration = next(item for item in expirations if item.event_id == first.event_id)
    wrong_policy = RetentionPolicyId.from_string("85000000-0000-4000-8000-000000000009")
    conflicting_expiration = TaskActivityEventExpiration(
        TaskActivityEventExpiration.identity(second.event_id, wrong_policy, DUE),
        second.event_id,
        scope,
        wrong_policy,
        DUE,
        SWEEP_TIME,
    )

    with pytest.raises(TaskActivityRetentionConflict):
        retention.apply_task_activity_expirations((first_expiration, conflicting_expiration))
    for event in (first, second):
        with pytest.raises(TaskActivityRetentionNotFound):
            retention.get_task_activity_expiration(scope, event.event_id)

    retention.apply_task_activity_expirations(expirations)
    purges = tuple(
        TaskActivityEventPurge.create(expiration, PURGE_TIME) for expiration in expirations
    )
    first_purge = next(item for item in purges if item.event_id == first.event_id)
    wrong_expiration_id = EventId.from_string("86000000-0000-4000-8000-000000000009")
    conflicting_purge = TaskActivityEventPurge(
        TaskActivityEventPurge.identity(wrong_expiration_id),
        wrong_expiration_id,
        second.event_id,
        scope,
        PURGE_TIME,
    )
    with pytest.raises(TaskActivityRetentionConflict):
        retention.apply_task_activity_purges((first_purge, conflicting_purge))
    assert len(retention.list_unpurged_task_activity_expirations(scope)) == 2


def test_sqlite_task_purge_failure_rolls_back_every_payload(tmp_path: Path) -> None:
    events, retention, _ = _repositories("sqlite", tmp_path, name="rollback")
    assert isinstance(retention, SQLiteCheckpointRepository)
    scope = _scope()
    first = _event(scope, seed=10)
    second = _event(scope, seed=11)
    events.append_task_activity_event(first)
    events.append_task_activity_event(second)
    service = TaskActivityRetentionService(retention)
    service.expire_due(scope, as_of=SWEEP_TIME)
    purges = tuple(
        TaskActivityEventPurge.create(expiration, PURGE_TIME)
        for expiration in retention.list_unpurged_task_activity_expirations(scope)
    )
    with sqlite3.connect(retention.path) as connection:
        connection.execute(
            "CREATE TRIGGER reject_second_task_purge BEFORE DELETE ON task_activity_events "
            "WHEN OLD.event_id = '"
            + str(second.event_id)
            + "' BEGIN SELECT RAISE(ABORT, 'injected task purge failure'); END"
        )

    with pytest.raises(TaskActivityRetentionStorageFailure):
        retention.apply_task_activity_purges(purges)
    assert len(retention.list_unpurged_task_activity_expirations(scope)) == 2
    with sqlite3.connect(retention.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM task_activity_events WHERE event_id IN (?, ?)",
            (str(first.event_id), str(second.event_id)),
        ).fetchone() == (2,)
        assert connection.execute(
            "SELECT COUNT(*) FROM task_activity_event_expirations WHERE purge_id IS NOT NULL"
        ).fetchone() == (0,)


def test_task_retention_migration_rolls_back_and_preserves_existing_state(
    tmp_path: Path,
) -> None:
    sqlite = SQLiteCheckpointRepository(
        tmp_path / "task-retention-migration.sqlite3", base_directory=tmp_path
    )
    sqlite.migrate()
    event = _event(_scope(), seed=12)
    candidate = _candidate(event, seed=12)
    sqlite.append_task_activity_event(event)
    sqlite.store_episodic_memory_candidates((candidate,))
    memory_service = EpisodicRetentionService(sqlite)
    memory_expiration = memory_service.expire_due(event.scope, as_of=SWEEP_TIME).expirations[0]
    memory_service.purge_expired(event.scope, purged_at=PURGE_TIME)
    with sqlite3.connect(sqlite.path) as connection:
        connection.execute("DROP TABLE episodic_memory_deletions")
        connection.execute("DROP TABLE task_activity_event_deletions")
        connection.execute("DROP TABLE task_activity_event_expirations")
        _drop_checkpoint_deletion_schema(connection)
        connection.execute("DELETE FROM schema_migrations WHERE version >= 25")

    with pytest.raises(SQLiteMigrationError, match="injected migration failure"):
        sqlite.migrate(fail_after_version=25)
    assert sqlite.schema_version() == 24
    assert (
        sqlite.get_episodic_memory_expiration(event.scope, candidate.memory_id) == memory_expiration
    )
    with sqlite3.connect(sqlite.path) as connection:
        assert connection.execute(
            "SELECT event_id FROM task_activity_events WHERE event_id = ?",
            (str(event.event_id),),
        ).fetchone() == (str(event.event_id),)
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='task_activity_event_expirations'"
            ).fetchone()
            is None
        )

    sqlite.migrate()
    assert sqlite.schema_version() == 32
    with sqlite3.connect(sqlite.path) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert "source_event_id" not in {
            row[3]
            for row in connection.execute(
                "PRAGMA foreign_key_list(episodic_memory_expirations)"
            ).fetchall()
        }
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(task_activity_event_expirations)"
            ).fetchall()
        }
    assert columns == {
        "expiration_sequence",
        "expiration_id",
        "event_id",
        "retention_policy_id",
        "scheduled_expires_at",
        "expired_at",
        "purge_id",
        "purged_at",
        "owner_id",
        "visibility",
        "workspace_id",
        "project_id",
        "session_id",
        "task_id",
    }
    task_service = TaskActivityRetentionService(sqlite)
    task_service.expire_due(event.scope, as_of=SWEEP_TIME)
    task_service.purge_expired(event.scope, purged_at=PURGE_TIME)
    assert (
        sqlite.get_episodic_memory_expiration(event.scope, candidate.memory_id) == memory_expiration
    )
