"""Contract and integration coverage for minimal durable event delivery."""

from __future__ import annotations

import sqlite3
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mnemo_memory.packages.application import (
    EventOutboxHandlerFailure,
    EventOutboxRunner,
)
from mnemo_memory.packages.domain import (
    ApprovedEpisodicEvent,
    ApprovedEpisodicEventGovernance,
    ApprovedEventGovernanceKind,
    ApprovedEventKind,
    CheckpointAggregate,
    CheckpointContent,
    CheckpointId,
    CheckpointRevision,
    CheckpointRevisionId,
    CheckpointStatus,
    EventOutboxJob,
    EventOutboxTopic,
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
    EventOutboxLeaseConflict,
    EventOutboxNotFound,
    ReferenceApprovedEpisodicEventRepository,
    ReferenceCheckpointRepository,
    SQLiteCheckpointRepository,
    SQLiteMigrationError,
)
from mnemo_memory.packages.storage.contracts import (
    ApprovedEpisodicEventRepository,
    ApprovedEpisodicEventStorageFailure,
    CheckpointRepository,
    EventOutboxRepository,
)

NOW = datetime(2026, 8, 5, 8, tzinfo=UTC)


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


def _evidence() -> EvidenceReference:
    return EvidenceReference(
        EvidenceId.new(),
        SourceId.new(),
        EvidenceSourceType.CHECKPOINT,
        SourceTrustClass.USER_AUTHORED,
        "fixture://event-outbox",
        "sha256:" + "a" * 64,
        EvidenceLocation("fixture://event-outbox"),
        NOW,
        VerificationStatus.VERIFIED,
    )


def _event(scope: MemoryScope, key: str, at: datetime) -> ApprovedEpisodicEvent:
    return ApprovedEpisodicEvent.create(
        scope=scope,
        kind=ApprovedEventKind.DECISION,
        summary=f"Approved decision {key}.",
        source_event_key=key,
        occurred_at=at,
        evidence_references=(_evidence(),),
    )


def _repositories(
    adapter: str, tmp_path: Path
) -> tuple[ApprovedEpisodicEventRepository, EventOutboxRepository]:
    if adapter == "reference":
        events = ReferenceApprovedEpisodicEventRepository()
        return events, events.outbox
    sqlite = SQLiteCheckpointRepository(
        tmp_path / f"outbox-{adapter}.sqlite3", base_directory=tmp_path
    )
    sqlite.migrate()
    return sqlite, sqlite


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_event_write_enqueues_one_minimal_deterministic_job(adapter: str, tmp_path: Path) -> None:
    events, outbox = _repositories(adapter, tmp_path)
    scope = _scope()
    event = _event(scope, "decision:one", NOW)

    assert events.append_approved_event(event).idempotent is False
    assert events.append_approved_event(event).idempotent is True
    claimed = outbox.claim_event_jobs(
        scope,
        worker_id="worker-one",
        now=NOW + timedelta(seconds=1),
        lease_expires_at=NOW + timedelta(seconds=31),
        limit=10,
    )

    assert len(claimed) == 1
    job = claimed[0]
    assert job == EventOutboxJob.create(
        scope=scope,
        topic=EventOutboxTopic.APPROVED_EPISODIC,
        source_event_id=event.event_id,
        event_kind=event.kind.value,
        occurred_at=event.occurred_at,
        created_at=event.occurred_at,
    ).claim("worker-one", NOW + timedelta(seconds=31))
    assert not hasattr(job, "summary")
    assert not hasattr(job, "evidence_references")


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_outbox_claims_are_bounded_oldest_first_scoped_and_lease_safe(
    adapter: str, tmp_path: Path
) -> None:
    events, outbox = _repositories(adapter, tmp_path)
    scope = _scope()
    other_scope = _scope()
    first = _event(scope, "decision:first", NOW)
    second = _event(scope, "decision:second", NOW + timedelta(seconds=1))
    events.append_approved_event(first)
    events.append_approved_event(second)

    claimed = outbox.claim_event_jobs(
        scope,
        worker_id="worker-a",
        now=NOW + timedelta(seconds=2),
        lease_expires_at=NOW + timedelta(seconds=12),
        limit=1,
    )
    assert [job.source_event_id for job in claimed] == [first.event_id]
    assert (
        outbox.claim_event_jobs(
            other_scope,
            worker_id="worker-other",
            now=NOW + timedelta(seconds=2),
            lease_expires_at=NOW + timedelta(seconds=12),
            limit=10,
        )
        == ()
    )
    with pytest.raises(EventOutboxNotFound):
        outbox.get_event_job(other_scope, claimed[0].job_id)
    with pytest.raises(EventOutboxLeaseConflict):
        outbox.complete_event_job(
            scope,
            claimed[0].job_id,
            worker_id="worker-b",
            completed_at=NOW + timedelta(seconds=3),
        )

    reclaimed = outbox.claim_event_jobs(
        scope,
        worker_id="worker-b",
        now=NOW + timedelta(seconds=12),
        lease_expires_at=NOW + timedelta(seconds=22),
        limit=1,
    )
    assert reclaimed[0].source_event_id == first.event_id
    assert reclaimed[0].attempt_count == 2
    retried = outbox.retry_event_job(
        scope,
        reclaimed[0].job_id,
        worker_id="worker-b",
        now=NOW + timedelta(seconds=13),
        available_at=NOW + timedelta(seconds=20),
        failure_code="HANDLER_RETRY",
    )
    assert retried.last_failure_code == "HANDLER_RETRY"
    assert (
        outbox.claim_event_jobs(
            scope,
            worker_id="worker-c",
            now=NOW + timedelta(seconds=19),
            lease_expires_at=NOW + timedelta(seconds=29),
            limit=10,
        )[0].source_event_id
        == second.event_id
    )


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_concurrent_claimers_do_not_receive_the_same_job(adapter: str, tmp_path: Path) -> None:
    events, outbox = _repositories(adapter, tmp_path)
    scope = _scope()
    events.append_approved_event(_event(scope, "decision:concurrent", NOW))

    def claim(worker: str) -> tuple[EventOutboxJob, ...]:
        return outbox.claim_event_jobs(
            scope,
            worker_id=worker,
            now=NOW + timedelta(seconds=1),
            lease_expires_at=NOW + timedelta(seconds=31),
            limit=1,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(claim, ("worker-a", "worker-b")))
    assert sorted(len(result) for result in results) == [0, 1]


def test_sqlite_outbox_survives_repository_restart(tmp_path: Path) -> None:
    events, outbox = _repositories("sqlite", tmp_path)
    assert isinstance(outbox, SQLiteCheckpointRepository)
    scope = _scope()
    event = _event(scope, "decision:restart", NOW)
    events.append_approved_event(event)
    job = outbox.claim_event_jobs(
        scope,
        worker_id="worker-a",
        now=NOW + timedelta(seconds=1),
        lease_expires_at=NOW + timedelta(seconds=31),
        limit=1,
    )[0]

    reopened = SQLiteCheckpointRepository(outbox.path, base_directory=tmp_path)
    assert reopened.get_event_job(scope, job.job_id) == job


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_checkpoint_and_governance_events_are_enqueued_atomically(
    adapter: str, tmp_path: Path
) -> None:
    scope = _scope()
    event = _event(scope, "decision:governed", NOW)
    replacement = _event(scope, "decision:replacement", NOW + timedelta(seconds=1))
    governance = ApprovedEpisodicEventGovernance.create(
        scope=scope,
        kind=ApprovedEventGovernanceKind.CORRECTED,
        target_event_id=event.event_id,
        replacement_event_id=replacement.event_id,
        reason="The user supplied a corrected decision.",
        source_action_key="correct:one",
        occurred_at=NOW + timedelta(seconds=2),
        evidence_references=(_evidence(),),
    )
    checkpoints: CheckpointRepository
    if adapter == "reference":
        checkpoints = ReferenceCheckpointRepository()
        approved: ApprovedEpisodicEventRepository = checkpoints.approved_events
        outbox: EventOutboxRepository = checkpoints.outbox
    else:
        sqlite = SQLiteCheckpointRepository(
            tmp_path / "outbox-combined.sqlite3", base_directory=tmp_path
        )
        sqlite.migrate()
        checkpoints = sqlite
        approved = sqlite
        outbox = sqlite
    revision_id = CheckpointRevisionId.new()
    checkpoint_id = CheckpointId.new()
    revision = CheckpointRevision(
        revision_id,
        checkpoint_id,
        1,
        None,
        scope,
        CheckpointContent("task", (), "active", ("next",), (), (), (), (), (), (), 1),
        CheckpointStatus.ACTIVE,
        (_evidence(),),
        NOW,
    )
    checkpoints.create_checkpoint_aggregate(
        CheckpointAggregate(
            checkpoint_id, scope, revision_id, 1, CheckpointStatus.ACTIVE, NOW, NOW
        ),
        revision,
    )
    approved.append_approved_event(event)
    approved.correct_approved_event(replacement, governance)

    jobs = outbox.claim_event_jobs(
        scope,
        worker_id="worker-all",
        now=NOW + timedelta(seconds=3),
        lease_expires_at=NOW + timedelta(seconds=33),
        limit=10,
    )
    assert Counter(job.topic for job in jobs) == {
        EventOutboxTopic.CHECKPOINT_LIFECYCLE: 1,
        EventOutboxTopic.APPROVED_EPISODIC: 2,
        EventOutboxTopic.APPROVED_GOVERNANCE: 1,
    }


def test_failed_reference_event_write_rolls_back_event_and_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = ReferenceApprovedEpisodicEventRepository()
    scope = _scope()

    def fail(_job: EventOutboxJob) -> EventOutboxJob:
        raise RuntimeError("injected")

    monkeypatch.setattr(repository.outbox, "_enqueue", fail)
    with pytest.raises(RuntimeError, match="injected"):
        repository.append_approved_event(_event(scope, "decision:reference-rollback", NOW))
    assert repository.list_approved_events(scope).items == ()


def test_failed_sqlite_event_transaction_persists_neither_event_nor_job(
    tmp_path: Path,
) -> None:
    repository = SQLiteCheckpointRepository(
        tmp_path / "outbox-atomic.sqlite3", base_directory=tmp_path
    )
    repository.migrate()
    scope = _scope()
    event = _event(scope, "decision:rollback", NOW)
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            "CREATE TRIGGER reject_outbox BEFORE INSERT ON event_outbox "
            "BEGIN SELECT RAISE(ABORT, 'injected outbox failure'); END"
        )

    with pytest.raises(ApprovedEpisodicEventStorageFailure):
        repository.append_approved_event(event)
    assert repository.list_approved_events(scope).items == ()
    with sqlite3.connect(repository.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM event_outbox").fetchone()[0] == 0


def test_event_outbox_migration_rolls_back_as_one_step(tmp_path: Path) -> None:
    repository = SQLiteCheckpointRepository(
        tmp_path / "outbox-migration.sqlite3", base_directory=tmp_path
    )
    repository.migrate()
    with sqlite3.connect(repository.path) as connection:
        connection.execute("DROP TABLE event_outbox")
        connection.execute("DELETE FROM schema_migrations WHERE version >= 18")

    with pytest.raises(SQLiteMigrationError, match="injected migration failure"):
        repository.migrate(fail_after_version=18)
    assert repository.schema_version() == 17
    with sqlite3.connect(repository.path) as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'event_outbox'"
            ).fetchone()
            is None
        )
    repository.migrate()
    assert repository.schema_version() == 25
    with sqlite3.connect(repository.path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(event_outbox)").fetchall()
        }
    assert columns == {
        "job_id",
        "topic",
        "source_event_id",
        "event_kind",
        "owner_id",
        "visibility",
        "workspace_id",
        "project_id",
        "session_id",
        "task_id",
        "occurred_at",
        "created_at",
        "available_at",
        "attempt_count",
        "lease_owner",
        "lease_expires_at",
        "completed_at",
        "last_failure_code",
    }


def test_runner_retries_with_stable_codes_and_supports_idempotent_handler_effects(
    tmp_path: Path,
) -> None:
    events, outbox = _repositories("reference", tmp_path)
    scope = _scope()
    event = _event(scope, "decision:runner", NOW)
    events.append_approved_event(event)

    class Handler:
        def __init__(self) -> None:
            self.calls = 0
            self.effects: set[str] = set()

        def handle(self, job: EventOutboxJob) -> None:
            self.calls += 1
            self.effects.add(str(job.job_id))
            if self.calls == 1:
                raise EventOutboxHandlerFailure("TEMPORARY_HANDLER_FAILURE")

    times = iter(
        (
            NOW + timedelta(seconds=1),
            NOW + timedelta(seconds=2),
            NOW + timedelta(seconds=4),
            NOW + timedelta(seconds=5),
        )
    )
    handler = Handler()
    runner = EventOutboxRunner(outbox, handler, clock=lambda: next(times))
    job_id = EventOutboxJob.create(
        scope=scope,
        topic=EventOutboxTopic.APPROVED_EPISODIC,
        source_event_id=event.event_id,
        event_kind=event.kind.value,
        occurred_at=event.occurred_at,
        created_at=event.occurred_at,
    ).job_id
    first = runner.run(scope, worker_id="runner", retry_delay_seconds=1)
    assert outbox.get_event_job(scope, job_id).last_failure_code == "TEMPORARY_HANDLER_FAILURE"
    second = runner.run(scope, worker_id="runner", retry_delay_seconds=1)

    assert (first.claimed, first.retried, first.completed) == (1, 1, 0)
    assert (second.claimed, second.retried, second.completed) == (1, 0, 1)
    assert handler.calls == 2
    assert len(handler.effects) == 1
    assert outbox.get_event_job(scope, job_id).completed_at == NOW + timedelta(seconds=5)
