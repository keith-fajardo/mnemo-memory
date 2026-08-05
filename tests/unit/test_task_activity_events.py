"""Domain, repository, safety, outbox, and migration coverage for minimized activity events."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mnemo_memory.packages.domain import (
    ApprovedEpisodicEvent,
    ApprovedEventKind,
    EventOutboxJob,
    EventOutboxTopic,
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
    TaskActivityEventKind,
    TaskId,
    VerificationStatus,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.policy import (
    ContentSafetyDecision,
    TaskActivityEventSafetyPolicy,
)
from mnemo_memory.packages.storage import (
    EventOutboxRepository,
    ReferenceTaskActivityEventRepository,
    SQLiteCheckpointRepository,
    SQLiteMigrationError,
    TaskActivityEventConflict,
    TaskActivityEventNotFound,
    TaskActivityEventRejected,
    TaskActivityEventRepository,
    TaskActivityEventStorageFailure,
)

NOW = datetime(2026, 8, 5, 10, tzinfo=UTC)


def _scope(seed: int = 1) -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string(f"00000000-0000-4000-8000-{seed:012d}"),
        ScopeLevel.TASK,
        Visibility.PROJECT,
        WorkspaceId.from_string(f"00000000-0000-4000-8001-{seed:012d}"),
        ProjectId.from_string(f"00000000-0000-4000-8002-{seed:012d}"),
        SessionId.from_string(f"00000000-0000-4000-8003-{seed:012d}"),
        TaskId.from_string(f"00000000-0000-4000-8004-{seed:012d}"),
    )


def _retention(at: datetime = NOW) -> RetentionSchedule:
    return RetentionSchedule(
        RetentionPolicyId.from_string("00000000-0000-4000-8005-000000000001"),
        False,
        at,
        at,
        at,
        None,
        at + timedelta(days=30),
    )


def _evidence(at: datetime = NOW) -> EvidenceReference:
    return EvidenceReference(
        EvidenceId.new(),
        SourceId.new(),
        EvidenceSourceType.AGENT_EVENT,
        SourceTrustClass.APPROVED_CHECKPOINT,
        "fixture://task-activity",
        "sha256:" + "a" * 64,
        EvidenceLocation("fixture://task-activity"),
        at,
        VerificationStatus.VERIFIED,
    )


def _event(
    scope: MemoryScope,
    *,
    kind: TaskActivityEventKind = TaskActivityEventKind.TASK_ACTIVITY,
    actor: TaskActivityActor = TaskActivityActor.AGENT,
    key: str = "activity:one",
    summary: str = "The scoped implementation task entered its verification phase.",
    sensitivity: Sensitivity = Sensitivity.NORMAL,
    at: datetime = NOW,
) -> TaskActivityEvent:
    return TaskActivityEvent.create(
        scope=scope,
        kind=kind,
        actor=actor,
        summary=summary,
        source_event_key=key,
        sensitivity=sensitivity,
        retention=_retention(at),
        occurred_at=at,
        evidence_references=(_evidence(at),),
    )


def _repositories(
    adapter: str, tmp_path: Path
) -> tuple[TaskActivityEventRepository, EventOutboxRepository]:
    if adapter == "reference":
        events = ReferenceTaskActivityEventRepository()
        return events, events.outbox
    sqlite = SQLiteCheckpointRepository(
        tmp_path / f"task-activity-{adapter}.sqlite3", base_directory=tmp_path
    )
    sqlite.migrate()
    return sqlite, sqlite


@pytest.mark.parametrize("kind", tuple(TaskActivityEventKind))
def test_task_activity_event_is_strict_minimized_and_serializable(
    kind: TaskActivityEventKind,
) -> None:
    event = _event(_scope(), kind=kind, key=f"kind:{kind.value}")

    assert TaskActivityEvent.from_dict(event.to_dict()) == event
    assert set(event.to_dict()) == {
        "event_id",
        "scope",
        "kind",
        "actor",
        "summary",
        "source_event_key",
        "sensitivity",
        "retention",
        "occurred_at",
        "evidence_references",
    }
    assert all(
        field not in event.to_dict()
        for field in ("transcript", "prompt", "arguments", "command", "tool_body", "source")
    )
    with pytest.raises(ValueError, match="fields are invalid"):
        TaskActivityEvent.from_dict({**event.to_dict(), "prompt": "not accepted"})


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_task_activity_repository_is_scoped_ordered_idempotent_and_enqueues_once(
    adapter: str, tmp_path: Path
) -> None:
    repository, outbox = _repositories(adapter, tmp_path)
    scope = _scope()
    first = _event(scope, key="activity:first", at=NOW)
    second = _event(
        scope,
        kind=TaskActivityEventKind.TASK_OUTCOME,
        key="activity:second",
        summary="The bounded verification gate passed.",
        at=NOW + timedelta(seconds=1),
    )

    assert repository.append_task_activity_event(first).idempotent is False
    assert repository.append_task_activity_event(first).idempotent is True
    assert repository.append_task_activity_event(second).idempotent is False
    page = repository.list_task_activity_events(scope, limit=1)
    assert page.items == (second,)
    assert page.next_offset == 1
    assert repository.list_task_activity_events(scope, offset=1).items == (first,)
    assert repository.list_task_activity_events(_scope(2)).items == ()
    with pytest.raises(TaskActivityEventNotFound):
        repository.get_task_activity_event(_scope(2), first.event_id)

    jobs = outbox.claim_event_jobs(
        scope,
        worker_id="activity-worker",
        now=NOW + timedelta(seconds=2),
        lease_expires_at=NOW + timedelta(seconds=32),
        limit=10,
    )
    assert [job.source_event_id for job in jobs] == [first.event_id, second.event_id]
    assert all(job.topic is EventOutboxTopic.TASK_ACTIVITY for job in jobs)
    assert not hasattr(jobs[0], "summary")


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_task_activity_rejects_conflicts_and_secrets_without_partial_state(
    adapter: str, tmp_path: Path
) -> None:
    repository, outbox = _repositories(adapter, tmp_path)
    scope = _scope()
    first = _event(scope)
    repository.append_task_activity_event(first)
    with pytest.raises(TaskActivityEventConflict):
        repository.append_task_activity_event(
            _event(scope, summary="A conflicting summary for the same source key.")
        )
    with pytest.raises(TaskActivityEventRejected):
        repository.append_task_activity_event(
            _event(
                scope,
                key="activity:secret",
                summary="api_key=ABCDEFGHIJKLMNOPQRSTUVWX",
            )
        )
    assert repository.list_task_activity_events(scope).items == (first,)
    jobs = outbox.claim_event_jobs(
        scope,
        worker_id="activity-worker",
        now=NOW + timedelta(seconds=2),
        lease_expires_at=NOW + timedelta(seconds=32),
        limit=10,
    )
    assert len(jobs) == 1


class RestrictedClassifier:
    def classify(self, values: tuple[str, ...]) -> ContentSafetyDecision:
        assert values
        return ContentSafetyDecision(True, Sensitivity.RESTRICTED)


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_task_activity_rejects_underclassified_plugin_result(adapter: str, tmp_path: Path) -> None:
    policy = TaskActivityEventSafetyPolicy((RestrictedClassifier(),))
    if adapter == "reference":
        repository: TaskActivityEventRepository = ReferenceTaskActivityEventRepository(
            policy=policy
        )
    else:
        sqlite = SQLiteCheckpointRepository(
            tmp_path / "task-activity-classified.sqlite3",
            base_directory=tmp_path,
            task_activity_policy=policy,
        )
        sqlite.migrate()
        repository = sqlite

    with pytest.raises(TaskActivityEventRejected):
        repository.append_task_activity_event(_event(_scope(), sensitivity=Sensitivity.NORMAL))
    assert repository.list_task_activity_events(_scope()).items == ()


def test_sqlite_task_activity_is_durable_and_event_outbox_failure_is_atomic(
    tmp_path: Path,
) -> None:
    repository = SQLiteCheckpointRepository(
        tmp_path / "task-activity-durable.sqlite3", base_directory=tmp_path
    )
    repository.migrate()
    scope = _scope()
    event = _event(scope)
    repository.append_task_activity_event(event)
    reopened = SQLiteCheckpointRepository(repository.path, base_directory=tmp_path)
    assert reopened.get_task_activity_event(scope, event.event_id) == event

    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            "CREATE TRIGGER reject_task_activity_outbox BEFORE INSERT ON event_outbox "
            "WHEN NEW.topic = 'task_activity' "
            "BEGIN SELECT RAISE(ABORT, 'injected task activity outbox failure'); END"
        )
    failed = _event(scope, key="activity:failed", at=NOW + timedelta(seconds=1))
    with pytest.raises(TaskActivityEventStorageFailure):
        repository.append_task_activity_event(failed)
    with pytest.raises(TaskActivityEventNotFound):
        repository.get_task_activity_event(scope, failed.event_id)


def test_reference_task_activity_outbox_failure_is_atomic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = ReferenceTaskActivityEventRepository()
    scope = _scope()

    def fail(_job: EventOutboxJob) -> EventOutboxJob:
        raise RuntimeError("injected")

    monkeypatch.setattr(repository.outbox, "_enqueue", fail)
    with pytest.raises(RuntimeError, match="injected"):
        repository.append_task_activity_event(_event(scope))
    assert repository.list_task_activity_events(scope).items == ()


def test_task_activity_migration_rolls_back_and_preserves_existing_outbox_jobs(
    tmp_path: Path,
) -> None:
    repository = SQLiteCheckpointRepository(
        tmp_path / "task-activity-migration.sqlite3", base_directory=tmp_path
    )
    repository.migrate()
    scope = _scope()
    evidence = _evidence()
    approved = ApprovedEpisodicEvent.create(
        scope=scope,
        kind=ApprovedEventKind.DECISION,
        summary="Keep the existing approved event job across migration.",
        source_event_key="approved:migration",
        occurred_at=NOW,
        evidence_references=(evidence,),
    )
    repository.append_approved_event(approved)
    approved_job_id = EventOutboxJob.create(
        scope=scope,
        topic=EventOutboxTopic.APPROVED_EPISODIC,
        source_event_id=approved.event_id,
        event_kind=approved.kind.value,
        occurred_at=approved.occurred_at,
        created_at=approved.occurred_at,
    ).job_id
    with sqlite3.connect(repository.path) as connection:
        connection.execute("DROP TABLE active_episodic_memories")
        connection.execute("DROP TABLE episodic_candidate_review_evidence")
        connection.execute("DROP TABLE episodic_candidate_reviews")
        connection.execute("DROP TABLE episodic_memory_candidate_evidence")
        connection.execute("DROP TABLE episodic_memory_candidates")
        connection.execute("DROP TABLE task_activity_event_evidence")
        connection.execute("DROP TABLE task_activity_events")
        connection.execute("DELETE FROM schema_migrations WHERE version >= 19")

    with pytest.raises(SQLiteMigrationError, match="injected migration failure"):
        repository.migrate(fail_after_version=19)
    assert repository.schema_version() == 18
    assert repository.get_event_job(scope, approved_job_id).source_event_id == approved.event_id
    with sqlite3.connect(repository.path) as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'task_activity_events'"
            ).fetchone()
            is None
        )

    repository.migrate()
    assert repository.schema_version() == 21
    assert repository.get_event_job(scope, approved_job_id).source_event_id == approved.event_id
    with sqlite3.connect(repository.path) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(task_activity_events)").fetchall()
        }
        outbox_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'event_outbox'"
        ).fetchone()[0]
    assert {
        "event_id",
        "source_event_key",
        "event_kind",
        "actor_kind",
        "summary",
        "sensitivity",
        "retention_policy_id",
        "owner_id",
        "visibility",
        "workspace_id",
        "project_id",
        "session_id",
        "task_id",
        "occurred_at",
    } <= columns
    assert {
        "transcript",
        "prompt",
        "arguments",
        "command",
        "tool_body",
        "tool_result",
        "source_content",
    }.isdisjoint(columns)
    assert "'task_activity'" in outbox_sql
