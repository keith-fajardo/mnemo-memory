"""Deterministic episodic retention and payload exclusion coverage for Issue 16G."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mnemo_memory.packages.domain import (
    EpisodicCandidateReviewAction,
    EpisodicCandidateReviewDecision,
    EpisodicExtractionProposal,
    EpisodicMemoryCandidate,
    EpisodicMemoryExpiration,
    EpisodicMemoryGovernanceAction,
    EpisodicMemoryKind,
    EpisodicMemoryRetentionTarget,
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
    TaskActivityEventKind,
    TaskId,
    VerificationStatus,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.episodic import EpisodicRetentionService
from mnemo_memory.packages.storage import (
    ActiveEpisodicMemoryNotFound,
    EpisodicMemoryCandidateConflict,
    EpisodicMemoryCandidateNotFound,
    EpisodicMemoryExpirationConflict,
    EpisodicMemoryExpirationNotFound,
    EpisodicMemoryGovernanceNotFound,
    EpisodicMemoryRetentionStorageFailure,
    EpisodicMemoryReviewNotFound,
    ReferenceEpisodicMemoryCandidateRepository,
    ReferenceTaskActivityEventRepository,
    SQLiteCheckpointRepository,
    SQLiteMigrationError,
)

NOW = datetime(2026, 8, 5, 13, 0, tzinfo=UTC)
DUE = NOW + timedelta(days=1)
SWEEP_TIME = DUE + timedelta(hours=1)

MemoryRepository = ReferenceEpisodicMemoryCandidateRepository | SQLiteCheckpointRepository
EventRepository = ReferenceTaskActivityEventRepository | SQLiteCheckpointRepository


def _scope(seed: int = 1) -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string(f"03000000-0000-4000-8000-{seed:012d}"),
        ScopeLevel.TASK,
        Visibility.PROJECT,
        WorkspaceId.from_string(f"13000000-0000-4000-8000-{seed:012d}"),
        ProjectId.from_string(f"23000000-0000-4000-8000-{seed:012d}"),
        session_id=SessionId.from_string(f"33000000-0000-4000-8000-{seed:012d}"),
        task_id=TaskId.from_string(f"43000000-0000-4000-8000-{seed:012d}"),
    )


def _evidence(seed: int, *, user: bool = False) -> EvidenceReference:
    return EvidenceReference(
        EvidenceId.from_string(f"{'54' if user else '53'}000000-0000-4000-8000-{seed:012d}"),
        SourceId.from_string(f"{'64' if user else '63'}000000-0000-4000-8000-{seed:012d}"),
        EvidenceSourceType.USER_CORRECTION if user else EvidenceSourceType.AGENT_EVENT,
        SourceTrustClass.USER_CORRECTION if user else SourceTrustClass.APPROVED_CHECKPOINT,
        f"fixture://episodic-retention/{'user' if user else 'source'}/{seed}",
        "sha256:" + ("b" if user else "a") * 64,
        EvidenceLocation(f"fixture://episodic-retention/{'user' if user else 'source'}/{seed}"),
        NOW + (timedelta(minutes=1) if user else timedelta()),
        VerificationStatus.VERIFIED,
    )


def _event(scope: MemoryScope, *, seed: int = 1, permanent: bool = False) -> TaskActivityEvent:
    return TaskActivityEvent.create(
        scope=scope,
        kind=TaskActivityEventKind.TASK_OUTCOME,
        actor=TaskActivityActor.AGENT,
        summary=f"The retention-bound implementation outcome {seed} passed verification.",
        source_event_key=f"episodic-retention:{seed}",
        sensitivity=Sensitivity.RESTRICTED,
        retention=RetentionSchedule(
            RetentionPolicyId.from_string(f"73000000-0000-4000-8000-{seed:012d}"),
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


def _candidate(event: TaskActivityEvent, *, seed: int = 1) -> EpisodicMemoryCandidate:
    return EpisodicMemoryCandidate.create(
        source_event=event,
        proposal=EpisodicExtractionProposal(
            EpisodicMemoryKind.OUTCOME,
            f"The retention-bound outcome {seed} remains available until its schedule.",
            0.93,
            Sensitivity.RESTRICTED,
        ),
        proposal_index=0,
        sensitivity=Sensitivity.RESTRICTED,
        extractor_version=f"retention-extractor-v{seed}",
        provider_id="luna-fixture",
        model_id="gpt-5.6-luna-fixture",
        prompt_version="retention-prompt-v1",
        created_at=NOW,
    )


def _approval(
    candidate: EpisodicMemoryCandidate, *, seed: int = 1
) -> EpisodicCandidateReviewAction:
    return EpisodicCandidateReviewAction.create(
        scope=candidate.scope,
        candidate_id=candidate.memory_id,
        decision=EpisodicCandidateReviewDecision.APPROVED,
        source_action_key=f"retention-approval:{seed}",
        reason="I verified this memory and its bounded retention schedule.",
        reviewed_at=NOW + timedelta(minutes=1),
        evidence_references=(_evidence(seed, user=True),),
    )


def _correction(
    candidate: EpisodicMemoryCandidate,
    expected_revision_id: EventId,
    *,
    seed: int = 2,
) -> EpisodicMemoryGovernanceAction:
    return EpisodicMemoryGovernanceAction.correct(
        scope=candidate.scope,
        memory_id=candidate.memory_id,
        expected_revision_id=expected_revision_id,
        source_action_key=f"retention-correction:{seed}",
        reason="I verified a clearer claim without changing its retention schedule.",
        corrected_claim="The corrected outcome remains subject to its original expiry.",
        corrected_sensitivity=Sensitivity.RESTRICTED,
        occurred_at=NOW + timedelta(minutes=2),
        evidence_references=(_evidence(seed, user=True),),
    )


def _repositories(
    adapter: str, tmp_path: Path, *, name: str = "main"
) -> tuple[EventRepository, MemoryRepository]:
    if adapter == "reference":
        events = ReferenceTaskActivityEventRepository()
        return events, ReferenceEpisodicMemoryCandidateRepository(events)
    sqlite = SQLiteCheckpointRepository(
        tmp_path / f"episodic-retention-{name}.sqlite3", base_directory=tmp_path
    )
    sqlite.migrate()
    return sqlite, sqlite


def _store(
    events: EventRepository,
    memories: MemoryRepository,
    *,
    scope: MemoryScope,
    seed: int,
    permanent: bool = False,
) -> EpisodicMemoryCandidate:
    event = _event(scope, seed=seed, permanent=permanent)
    candidate = _candidate(event, seed=seed)
    events.append_task_activity_event(event)
    memories.store_episodic_memory_candidates((candidate,))
    return candidate


def test_expiration_contract_is_strict_deterministic_and_payload_free() -> None:
    candidate = _candidate(_event(_scope()))
    target = EpisodicMemoryRetentionTarget(
        candidate.memory_id,
        candidate.source_event_id,
        candidate.scope,
        candidate.retention,
    )
    expiration = EpisodicMemoryExpiration.create(target, SWEEP_TIME)

    assert EpisodicMemoryExpiration.from_dict(expiration.to_dict()) == expiration
    assert EpisodicMemoryExpiration.create(target, SWEEP_TIME) == expiration
    assert set(expiration.to_dict()) == {
        "expiration_id",
        "memory_id",
        "source_event_id",
        "scope",
        "retention_policy_id",
        "scheduled_expires_at",
        "expired_at",
    }
    with pytest.raises(ValueError, match="not due"):
        EpisodicMemoryExpiration.create(target, NOW)
    permanent = _candidate(_event(_scope(), permanent=True))
    with pytest.raises(ValueError, match="permanent"):
        EpisodicMemoryExpiration.create(
            EpisodicMemoryRetentionTarget(
                permanent.memory_id,
                permanent.source_event_id,
                permanent.scope,
                permanent.retention,
            ),
            SWEEP_TIME,
        )


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_due_expiration_hides_every_payload_and_survives_restart(
    adapter: str, tmp_path: Path
) -> None:
    events, memories = _repositories(adapter, tmp_path)
    candidate = _store(events, memories, scope=_scope(), seed=1)
    approval = _approval(candidate)
    memories.review_episodic_memory_candidate(approval)
    correction = _correction(candidate, approval.action_id)
    memories.govern_episodic_memory(correction)
    service = EpisodicRetentionService(memories)

    assert service.expire_due(candidate.scope, as_of=NOW).expirations == ()
    result = service.expire_due(candidate.scope, as_of=SWEEP_TIME)
    assert result.idempotent is False
    assert len(result.expirations) == 1
    expiration = result.expirations[0]
    assert (
        memories.get_episodic_memory_expiration(candidate.scope, candidate.memory_id) == expiration
    )
    assert service.expire_due(candidate.scope, as_of=SWEEP_TIME).expirations == ()

    with pytest.raises(EpisodicMemoryCandidateNotFound):
        memories.get_episodic_memory_candidate(candidate.scope, candidate.memory_id)
    assert memories.list_episodic_memory_candidates(candidate.scope).items == ()
    with pytest.raises(EpisodicMemoryReviewNotFound):
        memories.get_episodic_memory_review(candidate.scope, candidate.memory_id)
    with pytest.raises(EpisodicMemoryReviewNotFound):
        memories.review_episodic_memory_candidate(approval)
    with pytest.raises(ActiveEpisodicMemoryNotFound):
        memories.get_active_episodic_memory(candidate.scope, candidate.memory_id)
    assert memories.list_active_episodic_memories(candidate.scope).items == ()
    with pytest.raises(EpisodicMemoryGovernanceNotFound):
        memories.get_episodic_memory_governance(candidate.scope, correction.action_id)
    with pytest.raises(EpisodicMemoryGovernanceNotFound):
        memories.list_episodic_memory_revisions(candidate.scope, candidate.memory_id)
    with pytest.raises(EpisodicMemoryGovernanceNotFound):
        memories.govern_episodic_memory(correction)
    with pytest.raises(EpisodicMemoryCandidateConflict):
        memories.store_episodic_memory_candidates((candidate,))
    with pytest.raises(EpisodicMemoryExpirationNotFound):
        memories.get_episodic_memory_expiration(_scope(2), candidate.memory_id)

    if isinstance(memories, SQLiteCheckpointRepository):
        reopened = SQLiteCheckpointRepository(memories.path, base_directory=tmp_path)
        assert (
            reopened.get_episodic_memory_expiration(candidate.scope, candidate.memory_id)
            == expiration
        )
        with pytest.raises(EpisodicMemoryCandidateNotFound):
            reopened.get_episodic_memory_candidate(candidate.scope, candidate.memory_id)
        with pytest.raises(ActiveEpisodicMemoryNotFound):
            reopened.get_active_episodic_memory(candidate.scope, candidate.memory_id)


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_permanent_retention_is_never_selected_or_hidden(adapter: str, tmp_path: Path) -> None:
    events, memories = _repositories(adapter, tmp_path, name=f"permanent-{adapter}")
    candidate = _store(events, memories, scope=_scope(), seed=3, permanent=True)

    result = EpisodicRetentionService(memories).expire_due(
        candidate.scope, as_of=SWEEP_TIME + timedelta(days=1000)
    )

    assert result.expirations == ()
    assert memories.get_episodic_memory_candidate(candidate.scope, candidate.memory_id) == candidate
    with pytest.raises(EpisodicMemoryExpirationNotFound):
        memories.get_episodic_memory_expiration(candidate.scope, candidate.memory_id)


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_retraction_cannot_escape_the_original_expiry(adapter: str, tmp_path: Path) -> None:
    events, memories = _repositories(adapter, tmp_path, name=f"retracted-{adapter}")
    candidate = _store(events, memories, scope=_scope(), seed=11)
    approval = _approval(candidate, seed=11)
    memories.review_episodic_memory_candidate(approval)
    retraction = EpisodicMemoryGovernanceAction.retract(
        scope=candidate.scope,
        memory_id=candidate.memory_id,
        expected_revision_id=approval.action_id,
        source_action_key="retention-retraction:11",
        reason="I retract the claim without changing its canonical retention schedule.",
        occurred_at=NOW + timedelta(minutes=2),
        evidence_references=(_evidence(12, user=True),),
    )
    memories.govern_episodic_memory(retraction)

    result = EpisodicRetentionService(memories).expire_due(candidate.scope, as_of=SWEEP_TIME)

    assert len(result.expirations) == 1
    assert result.expirations[0].memory_id == candidate.memory_id
    assert (
        memories.get_episodic_memory_expiration(candidate.scope, candidate.memory_id)
        == result.expirations[0]
    )


def test_reference_and_sqlite_discover_the_same_due_targets(tmp_path: Path) -> None:
    reference_events, reference = _repositories("reference", tmp_path, name="parity-ref")
    sqlite_events, sqlite = _repositories("sqlite", tmp_path, name="parity-sqlite")
    scope = _scope()
    for seed, permanent in ((4, False), (5, True)):
        event = _event(scope, seed=seed, permanent=permanent)
        candidate = _candidate(event, seed=seed)
        reference_events.append_task_activity_event(event)
        sqlite_events.append_task_activity_event(event)
        reference.store_episodic_memory_candidates((candidate,))
        sqlite.store_episodic_memory_candidates((candidate,))

    assert reference.list_due_episodic_memory_retention(
        scope, as_of=SWEEP_TIME
    ) == sqlite.list_due_episodic_memory_retention(scope, as_of=SWEEP_TIME)


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_conflicting_batch_is_atomic_and_exact_replay_is_idempotent(
    adapter: str, tmp_path: Path
) -> None:
    events, memories = _repositories(adapter, tmp_path, name=f"atomic-{adapter}")
    scope = _scope()
    first = _store(events, memories, scope=scope, seed=6)
    second = _store(events, memories, scope=scope, seed=7)
    targets = memories.list_due_episodic_memory_retention(scope, as_of=SWEEP_TIME)
    expirations = tuple(EpisodicMemoryExpiration.create(target, SWEEP_TIME) for target in targets)
    first_expiration = next(item for item in expirations if item.memory_id == first.memory_id)
    second_expiration = next(item for item in expirations if item.memory_id == second.memory_id)
    conflicting = replace(second_expiration, source_event_id=first_expiration.source_event_id)

    with pytest.raises(EpisodicMemoryExpirationConflict):
        memories.apply_episodic_memory_expirations((first_expiration, conflicting))
    for candidate in (first, second):
        assert memories.get_episodic_memory_candidate(scope, candidate.memory_id) == candidate
        with pytest.raises(EpisodicMemoryExpirationNotFound):
            memories.get_episodic_memory_expiration(scope, candidate.memory_id)

    stored = memories.apply_episodic_memory_expirations((first_expiration,))
    replayed = memories.apply_episodic_memory_expirations((first_expiration,))
    assert stored.idempotent is False
    assert replayed.idempotent is True
    assert replayed.expirations == (first_expiration,)


def test_sqlite_expiration_storage_failure_rolls_back_the_whole_batch(
    tmp_path: Path,
) -> None:
    events, memories = _repositories("sqlite", tmp_path, name="trigger-rollback")
    assert isinstance(memories, SQLiteCheckpointRepository)
    scope = _scope()
    first = _store(events, memories, scope=scope, seed=8)
    second = _store(events, memories, scope=scope, seed=9)
    expirations = tuple(
        EpisodicMemoryExpiration.create(target, SWEEP_TIME)
        for target in memories.list_due_episodic_memory_retention(scope, as_of=SWEEP_TIME)
    )
    with sqlite3.connect(memories.path) as connection:
        connection.execute(
            "CREATE TRIGGER reject_second_expiration BEFORE INSERT "
            "ON episodic_memory_expirations WHEN NEW.memory_id = '"
            + str(second.memory_id)
            + "' BEGIN SELECT RAISE(ABORT, 'injected expiration failure'); END"
        )

    with pytest.raises(EpisodicMemoryRetentionStorageFailure):
        memories.apply_episodic_memory_expirations(expirations)
    for candidate in (first, second):
        assert memories.get_episodic_memory_candidate(scope, candidate.memory_id) == candidate
        with pytest.raises(EpisodicMemoryExpirationNotFound):
            memories.get_episodic_memory_expiration(scope, candidate.memory_id)


def test_expiration_migration_is_atomic_payload_free_and_preserves_candidates(
    tmp_path: Path,
) -> None:
    sqlite = SQLiteCheckpointRepository(
        tmp_path / "episodic-retention-migration.sqlite3", base_directory=tmp_path
    )
    sqlite.migrate()
    candidate = _store(sqlite, sqlite, scope=_scope(), seed=10)
    with sqlite3.connect(sqlite.path) as connection:
        connection.execute("DROP TABLE episodic_memory_expirations")
        connection.execute("DELETE FROM schema_migrations WHERE version = 23")

    with pytest.raises(SQLiteMigrationError, match="injected migration failure"):
        sqlite.migrate(fail_after_version=23)
    assert sqlite.schema_version() == 22
    with sqlite3.connect(sqlite.path) as connection:
        assert connection.execute(
            "SELECT memory_id FROM episodic_memory_candidates WHERE memory_id = ?",
            (str(candidate.memory_id),),
        ).fetchone() == (str(candidate.memory_id),)
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='episodic_memory_expirations'"
            ).fetchone()
            is None
        )

    sqlite.migrate()
    assert sqlite.schema_version() == 23
    assert sqlite.get_episodic_memory_candidate(candidate.scope, candidate.memory_id) == candidate
    with sqlite3.connect(sqlite.path) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(episodic_memory_expirations)"
            ).fetchall()
        }
    assert columns == {
        "expiration_sequence",
        "expiration_id",
        "memory_id",
        "source_event_id",
        "retention_policy_id",
        "scheduled_expires_at",
        "expired_at",
        "owner_id",
        "visibility",
        "workspace_id",
        "project_id",
        "session_id",
        "task_id",
    }
