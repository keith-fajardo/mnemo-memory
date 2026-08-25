"""Explicit user/source deletion propagation for production episodic state."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mnemo_memory.packages.domain import (
    EpisodicCandidateReviewAction,
    EpisodicCandidateReviewDecision,
    EpisodicDeletionCause,
    EpisodicExtractionProposal,
    EpisodicMemoryCandidate,
    EpisodicMemoryDeletion,
    EpisodicMemoryGovernanceAction,
    EpisodicMemoryKind,
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
    TaskActivityEventDeletion,
    TaskActivityEventKind,
    TaskId,
    VerificationStatus,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.episodic import (
    EpisodicDeletionService,
    EpisodicRetentionService,
    TaskActivityRetentionService,
)
from mnemo_memory.packages.storage import (
    ActiveEpisodicMemoryNotFound,
    EpisodicDeletionConflict,
    EpisodicDeletionNotFound,
    EpisodicDeletionStorageFailure,
    EpisodicMemoryCandidateConflict,
    EpisodicMemoryCandidateNotFound,
    EpisodicMemoryGovernanceNotFound,
    EpisodicMemoryReviewNotFound,
    ReferenceEpisodicMemoryCandidateRepository,
    ReferenceTaskActivityEventRepository,
    SQLiteCheckpointRepository,
    SQLiteMigrationError,
    TaskActivityEventConflict,
    TaskActivityEventNotFound,
)
from scripts.sqlite_migration_test_support import (
    drop_checkpoint_deletion_schema as _drop_checkpoint_deletion_schema,
)

NOW = datetime(2026, 8, 5, 17, 0, tzinfo=UTC)
DUE = NOW + timedelta(days=1)
SWEEP = DUE + timedelta(hours=1)
PURGED = SWEEP + timedelta(minutes=1)

EventRepository = ReferenceTaskActivityEventRepository | SQLiteCheckpointRepository
MemoryRepository = ReferenceEpisodicMemoryCandidateRepository | SQLiteCheckpointRepository


def _scope(seed: int = 1) -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string(f"08000000-0000-4000-8000-{seed:012d}"),
        ScopeLevel.TASK,
        Visibility.PROJECT,
        WorkspaceId.from_string(f"18000000-0000-4000-8000-{seed:012d}"),
        ProjectId.from_string(f"28000000-0000-4000-8000-{seed:012d}"),
        SessionId.from_string(f"38000000-0000-4000-8000-{seed:012d}"),
        TaskId.from_string(f"48000000-0000-4000-8000-{seed:012d}"),
    )


def _evidence(seed: int, *, user: bool = False) -> EvidenceReference:
    prefix = "59" if user else "58"
    source_prefix = "69" if user else "68"
    return EvidenceReference(
        EvidenceId.from_string(f"{prefix}000000-0000-4000-8000-{seed:012d}"),
        SourceId.from_string(f"{source_prefix}000000-0000-4000-8000-{seed:012d}"),
        EvidenceSourceType.USER_CORRECTION if user else EvidenceSourceType.AGENT_EVENT,
        SourceTrustClass.USER_CORRECTION if user else SourceTrustClass.APPROVED_CHECKPOINT,
        f"fixture://episodic-deletion/{seed}",
        "sha256:" + ("b" if user else "a") * 64,
        EvidenceLocation(f"fixture://episodic-deletion/{seed}"),
        NOW,
        VerificationStatus.VERIFIED,
    )


def _event(scope: MemoryScope, *, seed: int, expiring: bool = False) -> TaskActivityEvent:
    return TaskActivityEvent.create(
        scope=scope,
        kind=TaskActivityEventKind.TASK_OUTCOME,
        actor=TaskActivityActor.AGENT,
        summary=f"The deletion fixture outcome {seed} passed its complete gate.",
        source_event_key=f"episodic-deletion:{seed}",
        sensitivity=Sensitivity.RESTRICTED,
        retention=RetentionSchedule(
            RetentionPolicyId.from_string(f"78000000-0000-4000-8000-{seed:012d}"),
            not expiring,
            NOW,
            NOW,
            NOW,
            None,
            DUE if expiring else None,
        ),
        occurred_at=NOW,
        evidence_references=(_evidence(seed),),
    )


def _candidate(
    event: TaskActivityEvent,
    *,
    seed: int,
    proposal_index: int = 0,
    extractor_version: str | None = None,
) -> EpisodicMemoryCandidate:
    return EpisodicMemoryCandidate.create(
        source_event=event,
        proposal=EpisodicExtractionProposal(
            EpisodicMemoryKind.OUTCOME,
            f"The deletion fixture memory {seed} remains evidence bound.",
            0.94,
            Sensitivity.RESTRICTED,
        ),
        proposal_index=proposal_index,
        sensitivity=Sensitivity.RESTRICTED,
        extractor_version=extractor_version or f"deletion-extractor-v{seed}",
        provider_id="luna-fixture",
        model_id="gpt-5.6-luna-fixture",
        prompt_version="deletion-prompt-v1",
        created_at=NOW,
    )


def _repositories(
    adapter: str, tmp_path: Path, *, name: str
) -> tuple[EventRepository, MemoryRepository]:
    if adapter == "reference":
        events = ReferenceTaskActivityEventRepository()
        return events, ReferenceEpisodicMemoryCandidateRepository(events)
    sqlite = SQLiteCheckpointRepository(
        tmp_path / f"episodic-deletion-{name}.sqlite3", base_directory=tmp_path
    )
    sqlite.migrate()
    return sqlite, sqlite


def _approve_and_correct(
    memories: MemoryRepository, candidate: EpisodicMemoryCandidate
) -> EpisodicMemoryGovernanceAction:
    approval = EpisodicCandidateReviewAction.create(
        scope=candidate.scope,
        candidate_id=candidate.memory_id,
        decision=EpisodicCandidateReviewDecision.APPROVED,
        source_action_key=f"deletion-approve:{candidate.memory_id}",
        reason="I verified this candidate before exercising deletion.",
        reviewed_at=NOW + timedelta(minutes=1),
        evidence_references=(_evidence(90, user=True),),
    )
    memories.review_episodic_memory_candidate(approval)
    correction = EpisodicMemoryGovernanceAction.correct(
        scope=candidate.scope,
        memory_id=candidate.memory_id,
        expected_revision_id=approval.action_id,
        source_action_key=f"deletion-correct:{candidate.memory_id}",
        reason="I verified the corrected wording before deletion.",
        corrected_claim="The corrected deletion fixture remains evidence bound.",
        corrected_sensitivity=Sensitivity.RESTRICTED,
        occurred_at=NOW + timedelta(minutes=2),
        evidence_references=(_evidence(91, user=True),),
    )
    memories.govern_episodic_memory(correction)
    return correction


def test_deletion_contracts_are_deterministic_user_authored_and_payload_free() -> None:
    event = _event(_scope(), seed=1)
    candidate = _candidate(event, seed=1)
    source = TaskActivityEventDeletion.create(
        scope=event.scope,
        event_id=event.event_id,
        source_action_key="delete-source:1",
        deleted_at=NOW,
    )
    memory = EpisodicMemoryDeletion.create(
        scope=candidate.scope,
        memory_id=candidate.memory_id,
        source_event_id=candidate.source_event_id,
        source_action_key="delete-memory:1",
        deleted_at=NOW,
    )
    dependent = EpisodicMemoryDeletion.from_source(
        source,
        memory_id=candidate.memory_id,
        source_event_id=candidate.source_event_id,
    )

    assert TaskActivityEventDeletion.from_dict(source.to_dict()) == source
    assert EpisodicMemoryDeletion.from_dict(memory.to_dict()) == memory
    assert EpisodicMemoryDeletion.from_dict(dependent.to_dict()) == dependent
    assert set(source.to_dict()) == {
        "deletion_id",
        "event_id",
        "scope",
        "actor",
        "source_action_key",
        "deleted_at",
    }
    assert set(memory.to_dict()) == {
        "deletion_id",
        "memory_id",
        "source_event_id",
        "scope",
        "cause",
        "actor",
        "source_action_key",
        "deleted_at",
        "source_deletion_id",
    }
    assert dependent.cause is EpisodicDeletionCause.SOURCE_DELETED
    assert dependent.source_deletion_id == source.deletion_id
    with pytest.raises(ValueError, match="only a user"):
        replace(source, actor=TaskActivityActor.AGENT)
    with pytest.raises(ValueError, match="source does not match"):
        EpisodicMemoryDeletion.from_source(
            source,
            memory_id=candidate.memory_id,
            source_event_id=_event(_scope(), seed=2).event_id,
        )


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_individual_memory_deletion_removes_all_payload_and_prevents_resurrection(
    adapter: str, tmp_path: Path
) -> None:
    events, memories = _repositories(adapter, tmp_path, name=f"memory-{adapter}")
    event = _event(_scope(), seed=3)
    candidate = _candidate(event, seed=3)
    events.append_task_activity_event(event)
    memories.store_episodic_memory_candidates((candidate,))
    correction = _approve_and_correct(memories, candidate)
    service = EpisodicDeletionService(memories)

    result = service.delete_memory(
        scope=candidate.scope,
        memory_id=candidate.memory_id,
        source_event_id=candidate.source_event_id,
        source_action_key="delete-memory:3",
        deleted_at=NOW + timedelta(minutes=3),
    )
    assert result.idempotent is False
    assert (
        memories.get_episodic_memory_deletion(candidate.scope, candidate.memory_id)
        == result.deletion
    )
    assert events.get_task_activity_event(event.scope, event.event_id) == event
    with pytest.raises(EpisodicMemoryCandidateNotFound):
        memories.get_episodic_memory_candidate(candidate.scope, candidate.memory_id)
    with pytest.raises(EpisodicMemoryReviewNotFound):
        memories.get_episodic_memory_review(candidate.scope, candidate.memory_id)
    with pytest.raises(ActiveEpisodicMemoryNotFound):
        memories.get_active_episodic_memory(candidate.scope, candidate.memory_id)
    with pytest.raises(EpisodicMemoryGovernanceNotFound):
        memories.get_episodic_memory_governance(candidate.scope, correction.action_id)
    with pytest.raises(EpisodicMemoryGovernanceNotFound):
        memories.list_episodic_memory_revisions(candidate.scope, candidate.memory_id)
    assert memories.delete_episodic_memory(result.deletion).idempotent is True
    with pytest.raises(EpisodicDeletionConflict):
        service.delete_memory(
            scope=candidate.scope,
            memory_id=candidate.memory_id,
            source_event_id=candidate.source_event_id,
            source_action_key="delete-memory:alternate",
            deleted_at=NOW + timedelta(minutes=4),
        )
    with pytest.raises(EpisodicMemoryCandidateConflict):
        memories.store_episodic_memory_candidates((candidate,))
    with pytest.raises(EpisodicDeletionNotFound):
        memories.get_episodic_memory_deletion(_scope(2), candidate.memory_id)

    if isinstance(memories, SQLiteCheckpointRepository):
        reopened = SQLiteCheckpointRepository(memories.path, base_directory=tmp_path)
        reopened.migrate()
        assert (
            reopened.get_episodic_memory_deletion(candidate.scope, candidate.memory_id)
            == result.deletion
        )
        with sqlite3.connect(memories.path) as connection:
            for table in (
                "episodic_memory_candidates",
                "episodic_candidate_reviews",
                "active_episodic_memories",
                "episodic_memory_governance",
            ):
                assert connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE "
                    + ("candidate_id" if table == "episodic_candidate_reviews" else "memory_id")
                    + " = ?",
                    (str(candidate.memory_id),),
                ).fetchone() == (0,)


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_source_deletion_cascades_to_all_memories_and_preserves_unrelated_state(
    adapter: str, tmp_path: Path
) -> None:
    events, memories = _repositories(adapter, tmp_path, name=f"source-{adapter}")
    scope = _scope()
    source = _event(scope, seed=4)
    first = _candidate(source, seed=4, proposal_index=0, extractor_version="batch-v4")
    second = _candidate(source, seed=5, proposal_index=1, extractor_version="batch-v4")
    unrelated_source = _event(scope, seed=6)
    unrelated = _candidate(unrelated_source, seed=6)
    events.append_task_activity_event(source)
    events.append_task_activity_event(unrelated_source)
    memories.store_episodic_memory_candidates((first, second))
    memories.store_episodic_memory_candidates((unrelated,))
    _approve_and_correct(memories, first)
    service = EpisodicDeletionService(memories)

    result = service.delete_task_event(
        scope=scope,
        event_id=source.event_id,
        source_action_key="delete-source:4",
        deleted_at=NOW + timedelta(minutes=3),
    )
    assert result.idempotent is False
    assert tuple(item.memory_id for item in result.dependent_deletions) == tuple(
        sorted((first.memory_id, second.memory_id), key=str)
    )
    assert all(
        item.cause is EpisodicDeletionCause.SOURCE_DELETED
        and item.source_deletion_id == result.deletion.deletion_id
        for item in result.dependent_deletions
    )
    with pytest.raises(TaskActivityEventNotFound):
        events.get_task_activity_event(scope, source.event_id)
    for candidate in (first, second):
        with pytest.raises(EpisodicMemoryCandidateNotFound):
            memories.get_episodic_memory_candidate(scope, candidate.memory_id)
    assert events.get_task_activity_event(scope, unrelated_source.event_id) == unrelated_source
    assert memories.get_episodic_memory_candidate(scope, unrelated.memory_id) == unrelated
    replay = memories.delete_task_activity_event(result.deletion)
    assert replay.idempotent is True
    assert replay.dependent_deletions == result.dependent_deletions
    with pytest.raises(TaskActivityEventConflict):
        events.append_task_activity_event(source)
    with pytest.raises(EpisodicMemoryCandidateConflict):
        memories.store_episodic_memory_candidates((first, second))

    if isinstance(memories, SQLiteCheckpointRepository):
        reopened = SQLiteCheckpointRepository(memories.path, base_directory=tmp_path)
        reopened.migrate()
        assert reopened.get_task_activity_deletion(scope, source.event_id) == result.deletion
        for item in result.dependent_deletions:
            assert reopened.get_episodic_memory_deletion(scope, item.memory_id) == item
        with sqlite3.connect(memories.path) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM event_outbox WHERE topic = 'task_activity' "
                "AND source_event_id = ?",
                (str(source.event_id),),
            ).fetchone() == (0,)


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_deletion_after_retention_purge_keeps_tombstones_and_purge_valid(
    adapter: str, tmp_path: Path
) -> None:
    events, memories = _repositories(adapter, tmp_path, name=f"retained-{adapter}")
    source = _event(_scope(), seed=7, expiring=True)
    candidate = _candidate(source, seed=7)
    events.append_task_activity_event(source)
    memories.store_episodic_memory_candidates((candidate,))
    memory_retention = EpisodicRetentionService(memories)
    memory_expiration = memory_retention.expire_due(source.scope, as_of=SWEEP).expirations[0]
    memory_retention.purge_expired(source.scope, purged_at=PURGED)
    task_retention = TaskActivityRetentionService(memories)
    task_expiration = task_retention.expire_due(source.scope, as_of=SWEEP).expirations[0]
    task_retention.purge_expired(source.scope, purged_at=PURGED)

    result = EpisodicDeletionService(memories).delete_task_event(
        scope=source.scope,
        event_id=source.event_id,
        source_action_key="delete-retained-source:7",
        deleted_at=PURGED + timedelta(minutes=1),
    )
    assert result.idempotent is False
    assert (
        memories.get_episodic_memory_expiration(source.scope, candidate.memory_id)
        == memory_expiration
    )
    assert memories.get_task_activity_expiration(source.scope, source.event_id) == task_expiration
    assert memories.get_task_activity_deletion(source.scope, source.event_id) == result.deletion
    assert len(result.dependent_deletions) == 1


def test_sqlite_source_deletion_failure_rolls_back_all_tombstones_and_payloads(
    tmp_path: Path,
) -> None:
    sqlite = SQLiteCheckpointRepository(
        tmp_path / "episodic-deletion-atomic.sqlite3", base_directory=tmp_path
    )
    sqlite.migrate()
    source = _event(_scope(), seed=8)
    candidate = _candidate(source, seed=8)
    sqlite.append_task_activity_event(source)
    sqlite.store_episodic_memory_candidates((candidate,))
    with sqlite3.connect(sqlite.path) as connection:
        connection.execute(
            "CREATE TRIGGER reject_source_deletion BEFORE DELETE ON task_activity_events "
            "WHEN OLD.event_id = '"
            + str(source.event_id)
            + "' BEGIN SELECT RAISE(ABORT, 'injected deletion failure'); END"
        )

    with pytest.raises(EpisodicDeletionStorageFailure):
        EpisodicDeletionService(sqlite).delete_task_event(
            scope=source.scope,
            event_id=source.event_id,
            source_action_key="delete-source:atomic",
            deleted_at=NOW + timedelta(minutes=1),
        )
    assert sqlite.get_task_activity_event(source.scope, source.event_id) == source
    assert sqlite.get_episodic_memory_candidate(source.scope, candidate.memory_id) == candidate
    with sqlite3.connect(sqlite.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM task_activity_event_deletions"
        ).fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM episodic_memory_deletions").fetchone() == (
            0,
        )


def test_deletion_migration_is_atomic_forward_only_and_payload_free(tmp_path: Path) -> None:
    sqlite = SQLiteCheckpointRepository(
        tmp_path / "episodic-deletion-migration.sqlite3", base_directory=tmp_path
    )
    sqlite.migrate()
    source = _event(_scope(), seed=9)
    candidate = _candidate(source, seed=9)
    sqlite.append_task_activity_event(source)
    sqlite.store_episodic_memory_candidates((candidate,))
    with sqlite3.connect(sqlite.path) as connection:
        connection.execute("DROP TRIGGER episodic_memory_purge_guard")
        connection.execute("DROP TRIGGER task_activity_purge_guard")
        connection.execute("DROP TABLE episodic_memory_deletions")
        connection.execute("DROP TABLE task_activity_event_deletions")
        _drop_checkpoint_deletion_schema(connection)
        connection.execute("DELETE FROM schema_migrations WHERE version >= 26")

    with pytest.raises(SQLiteMigrationError, match="injected migration failure"):
        sqlite.migrate(fail_after_version=26)
    assert sqlite.schema_version() == 25
    assert sqlite.get_task_activity_event(source.scope, source.event_id) == source
    assert sqlite.get_episodic_memory_candidate(source.scope, candidate.memory_id) == candidate
    with sqlite3.connect(sqlite.path) as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='episodic_memory_deletions'"
            ).fetchone()
            is None
        )

    sqlite.migrate()
    assert sqlite.schema_version() == 32
    with sqlite3.connect(sqlite.path) as connection:
        source_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(task_activity_event_deletions)"
            ).fetchall()
        }
        memory_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(episodic_memory_deletions)").fetchall()
        }
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert source_columns == {
        "deletion_sequence",
        "deletion_id",
        "event_id",
        "actor",
        "source_action_key",
        "deleted_at",
        "owner_id",
        "visibility",
        "workspace_id",
        "project_id",
        "session_id",
        "task_id",
    }
    assert memory_columns == {
        "deletion_sequence",
        "deletion_id",
        "memory_id",
        "source_event_id",
        "cause",
        "source_deletion_id",
        "actor",
        "source_action_key",
        "deleted_at",
        "owner_id",
        "visibility",
        "workspace_id",
        "project_id",
        "session_id",
        "task_id",
    }
