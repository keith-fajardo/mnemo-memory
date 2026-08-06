"""Explicit review, activation, rejection, and migration coverage for Issue 16E."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from mnemo_memory.packages.domain import (
    ActiveEpisodicMemory,
    EpisodicCandidateReviewAction,
    EpisodicCandidateReviewDecision,
    EpisodicExtractionProposal,
    EpisodicMemoryCandidate,
    EpisodicMemoryGovernanceAction,
    EpisodicMemoryKind,
    EpisodicMemoryRevisionStatus,
    EventId,
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    MemoryScope,
    MemoryStatus,
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
    active_episodic_memory_at_revision,
    replay_episodic_memory_revisions,
)
from mnemo_memory.packages.storage import (
    ActiveEpisodicMemoryNotFound,
    EpisodicMemoryCandidateRepository,
    EpisodicMemoryGovernanceConflict,
    EpisodicMemoryGovernanceNotFound,
    EpisodicMemoryGovernanceRejected,
    EpisodicMemoryGovernanceRepository,
    EpisodicMemoryGovernanceStorageFailure,
    EpisodicMemoryReviewConflict,
    EpisodicMemoryReviewNotFound,
    EpisodicMemoryReviewRejected,
    EpisodicMemoryReviewRepository,
    EpisodicMemoryReviewStorageFailure,
    ReferenceEpisodicMemoryCandidateRepository,
    ReferenceTaskActivityEventRepository,
    SQLiteCheckpointRepository,
    SQLiteMigrationError,
    TaskActivityEventRepository,
)

NOW = datetime(2026, 8, 5, 11, 0, tzinfo=UTC)


def _scope(seed: int = 1) -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string(f"01000000-0000-4000-8000-{seed:012d}"),
        ScopeLevel.TASK,
        Visibility.PROJECT,
        WorkspaceId.from_string(f"11000000-0000-4000-8000-{seed:012d}"),
        ProjectId.from_string(f"21000000-0000-4000-8000-{seed:012d}"),
        session_id=SessionId.from_string(f"31000000-0000-4000-8000-{seed:012d}"),
        task_id=TaskId.from_string(f"41000000-0000-4000-8000-{seed:012d}"),
    )


def _source_evidence(seed: int = 1) -> EvidenceReference:
    return EvidenceReference(
        EvidenceId.from_string(f"51000000-0000-4000-8000-{seed:012d}"),
        SourceId.from_string(f"61000000-0000-4000-8000-{seed:012d}"),
        EvidenceSourceType.AGENT_EVENT,
        SourceTrustClass.APPROVED_CHECKPOINT,
        f"fixture://candidate-source/{seed}",
        "sha256:" + "a" * 64,
        EvidenceLocation(f"fixture://candidate-source/{seed}"),
        NOW,
        VerificationStatus.VERIFIED,
    )


def _review_evidence(
    seed: int = 1,
    *,
    verification: VerificationStatus = VerificationStatus.VERIFIED,
) -> EvidenceReference:
    return EvidenceReference(
        EvidenceId.from_string(f"52000000-0000-4000-8000-{seed:012d}"),
        SourceId.from_string(f"62000000-0000-4000-8000-{seed:012d}"),
        EvidenceSourceType.USER_CORRECTION,
        SourceTrustClass.USER_CORRECTION,
        f"fixture://user-review/{seed}",
        "sha256:" + "b" * 64,
        EvidenceLocation(f"fixture://user-review/{seed}"),
        NOW + timedelta(minutes=1),
        verification,
    )


def _event(scope: MemoryScope, *, seed: int = 1) -> TaskActivityEvent:
    return TaskActivityEvent.create(
        scope=scope,
        kind=TaskActivityEventKind.TASK_OUTCOME,
        actor=TaskActivityActor.AGENT,
        summary=f"The bounded implementation outcome {seed} is ready for explicit review.",
        source_event_key=f"candidate-review-source:{seed}",
        sensitivity=Sensitivity.RESTRICTED,
        retention=RetentionSchedule(
            RetentionPolicyId.from_string(f"71000000-0000-4000-8000-{seed:012d}"),
            True,
            NOW,
            NOW,
            NOW,
            None,
            None,
        ),
        occurred_at=NOW,
        evidence_references=(_source_evidence(seed),),
    )


def _candidate(event: TaskActivityEvent, *, seed: int = 1) -> EpisodicMemoryCandidate:
    return EpisodicMemoryCandidate.create(
        source_event=event,
        proposal=EpisodicExtractionProposal(
            EpisodicMemoryKind.OUTCOME,
            f"The explicitly reviewed outcome {seed} passed its verification gate.",
            1.0,
            Sensitivity.RESTRICTED,
        ),
        proposal_index=0,
        sensitivity=Sensitivity.RESTRICTED,
        extractor_version=f"extractor-v{seed}",
        provider_id="luna-fixture",
        model_id="gpt-5.6-luna-fixture",
        prompt_version="candidate-prompt-v1",
        created_at=NOW,
    )


def _action(
    candidate: EpisodicMemoryCandidate,
    *,
    decision: EpisodicCandidateReviewDecision = EpisodicCandidateReviewDecision.APPROVED,
    key: str = "review:one",
    reason: str = "I verified the evidence and approve this memory.",
    seed: int = 1,
) -> EpisodicCandidateReviewAction:
    return EpisodicCandidateReviewAction.create(
        scope=candidate.scope,
        candidate_id=candidate.memory_id,
        decision=decision,
        source_action_key=key,
        reason=reason,
        reviewed_at=NOW + timedelta(minutes=1),
        evidence_references=(_review_evidence(seed),),
    )


def _repositories(
    adapter: str, tmp_path: Path
) -> tuple[
    TaskActivityEventRepository,
    EpisodicMemoryCandidateRepository,
    EpisodicMemoryReviewRepository,
]:
    if adapter == "reference":
        events = ReferenceTaskActivityEventRepository()
        candidates = ReferenceEpisodicMemoryCandidateRepository(events)
        return events, candidates, candidates
    sqlite = SQLiteCheckpointRepository(
        tmp_path / f"candidate-review-{adapter}.sqlite3", base_directory=tmp_path
    )
    sqlite.migrate()
    return sqlite, sqlite, sqlite


def _stored_candidate(
    adapter: str, tmp_path: Path, *, scope_seed: int = 1, event_seed: int = 1
) -> tuple[EpisodicMemoryCandidate, EpisodicMemoryReviewRepository]:
    events, candidates, reviews = _repositories(adapter, tmp_path)
    event = _event(_scope(scope_seed), seed=event_seed)
    candidate = _candidate(event, seed=event_seed)
    events.append_task_activity_event(event)
    candidates.store_episodic_memory_candidates((candidate,))
    return candidate, reviews


def test_review_and_active_memory_contracts_are_strict_and_evidence_bearing() -> None:
    event = _event(_scope())
    candidate = _candidate(event)
    action = _action(candidate)
    active = ActiveEpisodicMemory.approve(candidate, action)

    assert EpisodicCandidateReviewAction.from_dict(action.to_dict()) == action
    assert ActiveEpisodicMemory.from_dict(active.to_dict()) == active
    assert candidate.memory.classification.status is MemoryStatus.CANDIDATE
    assert active.memory.classification.status is MemoryStatus.ACTIVE
    assert active.memory.classification.can_enter_context is True
    assert active.memory_id == candidate.memory_id
    assert active.memory.evidence_references == (
        *candidate.evidence_references,
        *action.evidence_references,
    )
    with pytest.raises(ValueError, match="fields are invalid"):
        EpisodicCandidateReviewAction.from_dict({**action.to_dict(), "provider": "model"})
    with pytest.raises(ValueError, match="only a user"):
        EpisodicCandidateReviewAction(
            action.action_id,
            action.scope,
            action.candidate_id,
            action.decision,
            TaskActivityActor.AGENT,
            action.source_action_key,
            action.reason,
            action.reviewed_at,
            action.evidence_references,
        )
    with pytest.raises(ValueError, match="verified user-correction evidence"):
        EpisodicCandidateReviewAction.create(
            scope=candidate.scope,
            candidate_id=candidate.memory_id,
            decision=EpisodicCandidateReviewDecision.APPROVED,
            source_action_key="review:unverified",
            reason="This action lacks verified user evidence.",
            reviewed_at=NOW + timedelta(minutes=1),
            evidence_references=(_review_evidence(2, verification=VerificationStatus.UNVERIFIED),),
        )


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_explicit_approval_is_scoped_idempotent_and_restart_durable(
    adapter: str, tmp_path: Path
) -> None:
    candidate, reviews = _stored_candidate(adapter, tmp_path)
    action = _action(candidate)

    with pytest.raises(ActiveEpisodicMemoryNotFound):
        reviews.get_active_episodic_memory(candidate.scope, candidate.memory_id)
    first = reviews.review_episodic_memory_candidate(action)
    second = reviews.review_episodic_memory_candidate(action)

    assert first.idempotent is False
    assert second.idempotent is True
    assert first.active_memory == second.active_memory
    assert first.active_memory is not None
    assert first.active_memory.memory.classification.status is MemoryStatus.ACTIVE
    assert first.active_memory.memory.classification.sensitivity is Sensitivity.RESTRICTED
    assert reviews.get_episodic_memory_review(candidate.scope, candidate.memory_id) == action
    assert (
        reviews.get_active_episodic_memory(candidate.scope, candidate.memory_id)
        == first.active_memory
    )
    assert reviews.list_active_episodic_memories(candidate.scope).items == (first.active_memory,)
    with pytest.raises(EpisodicMemoryReviewNotFound):
        reviews.get_episodic_memory_review(_scope(2), candidate.memory_id)
    with pytest.raises(ActiveEpisodicMemoryNotFound):
        reviews.get_active_episodic_memory(_scope(2), candidate.memory_id)

    if isinstance(reviews, SQLiteCheckpointRepository):
        reopened = SQLiteCheckpointRepository(reviews.path, base_directory=tmp_path)
        assert reopened.get_episodic_memory_review(candidate.scope, candidate.memory_id) == action
        assert (
            reopened.get_active_episodic_memory(candidate.scope, candidate.memory_id)
            == first.active_memory
        )


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_rejection_is_final_idempotent_and_creates_no_active_payload(
    adapter: str, tmp_path: Path
) -> None:
    candidate, reviews = _stored_candidate(adapter, tmp_path)
    rejected = _action(
        candidate,
        decision=EpisodicCandidateReviewDecision.REJECTED,
        reason="The evidence does not support retaining this as active memory.",
    )

    result = reviews.review_episodic_memory_candidate(rejected)
    assert result.active_memory is None
    assert reviews.review_episodic_memory_candidate(rejected).idempotent is True
    assert reviews.list_active_episodic_memories(candidate.scope).items == ()
    with pytest.raises(ActiveEpisodicMemoryNotFound):
        reviews.get_active_episodic_memory(candidate.scope, candidate.memory_id)
    with pytest.raises(EpisodicMemoryReviewConflict):
        reviews.review_episodic_memory_candidate(_action(candidate, key="review:competing"))


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_review_action_keys_conflict_and_secret_rejection_has_no_partial_state(
    adapter: str, tmp_path: Path
) -> None:
    events, candidates, reviews = _repositories(adapter, tmp_path)
    scope = _scope()
    first_event = _event(scope, seed=1)
    second_event = _event(scope, seed=2)
    first = _candidate(first_event, seed=1)
    second = _candidate(second_event, seed=2)
    events.append_task_activity_event(first_event)
    events.append_task_activity_event(second_event)
    candidates.store_episodic_memory_candidates((first,))
    candidates.store_episodic_memory_candidates((second,))
    reviews.review_episodic_memory_candidate(_action(first, key="review:shared"))

    with pytest.raises(EpisodicMemoryReviewConflict):
        reviews.review_episodic_memory_candidate(_action(second, key="review:shared", seed=2))
    with pytest.raises(EpisodicMemoryReviewRejected):
        reviews.review_episodic_memory_candidate(
            _action(
                second,
                key="review:secret",
                reason="api_key=ABCDEFGHIJKLMNOPQRSTUVWX",
                seed=2,
            )
        )
    with pytest.raises(EpisodicMemoryReviewNotFound):
        reviews.get_episodic_memory_review(scope, second.memory_id)
    with pytest.raises(ActiveEpisodicMemoryNotFound):
        reviews.get_active_episodic_memory(scope, second.memory_id)


def test_sqlite_activation_failure_rolls_back_review_and_active_marker(tmp_path: Path) -> None:
    candidate, reviews = _stored_candidate("sqlite", tmp_path)
    assert isinstance(reviews, SQLiteCheckpointRepository)
    with sqlite3.connect(reviews.path) as connection:
        connection.execute(
            "CREATE TRIGGER reject_active_memory BEFORE INSERT ON active_episodic_memories "
            "BEGIN SELECT RAISE(ABORT, 'injected active memory failure'); END"
        )

    with pytest.raises(EpisodicMemoryReviewStorageFailure):
        reviews.review_episodic_memory_candidate(_action(candidate))
    with pytest.raises(EpisodicMemoryReviewNotFound):
        reviews.get_episodic_memory_review(candidate.scope, candidate.memory_id)
    with pytest.raises(ActiveEpisodicMemoryNotFound):
        reviews.get_active_episodic_memory(candidate.scope, candidate.memory_id)


def test_review_migration_is_additive_atomic_and_preserves_candidates(tmp_path: Path) -> None:
    sqlite = SQLiteCheckpointRepository(
        tmp_path / "review-migration.sqlite3", base_directory=tmp_path
    )
    sqlite.migrate()
    event = _event(_scope())
    candidate = _candidate(event)
    sqlite.append_task_activity_event(event)
    sqlite.store_episodic_memory_candidates((candidate,))
    with sqlite3.connect(sqlite.path) as connection:
        connection.execute("DROP TABLE episodic_memory_deletions")
        connection.execute("DROP TABLE episodic_memory_expirations")
        connection.execute("DROP TABLE episodic_memory_governance_evidence")
        connection.execute("DROP TABLE episodic_memory_governance")
        connection.execute("DROP TABLE active_episodic_memories")
        connection.execute("DROP TABLE episodic_candidate_review_evidence")
        connection.execute("DROP TABLE episodic_candidate_reviews")
        connection.execute("DELETE FROM schema_migrations WHERE version >= 21")

    with pytest.raises(SQLiteMigrationError, match="injected migration failure"):
        sqlite.migrate(fail_after_version=21)
    assert sqlite.schema_version() == 20
    with sqlite3.connect(sqlite.path) as connection:
        assert connection.execute(
            "SELECT memory_id FROM episodic_memory_candidates WHERE memory_id = ?",
            (str(candidate.memory_id),),
        ).fetchone() == (str(candidate.memory_id),)
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='episodic_candidate_reviews'"
            ).fetchone()
            is None
        )

    sqlite.migrate()
    assert sqlite.schema_version() == 29
    with sqlite3.connect(sqlite.path) as connection:
        review_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(episodic_candidate_reviews)"
            ).fetchall()
        }
        active_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(active_episodic_memories)").fetchall()
        }
    assert {
        "action_id",
        "candidate_id",
        "decision",
        "actor",
        "source_action_key",
        "reason",
        "owner_id",
        "project_id",
        "session_id",
        "task_id",
        "reviewed_at",
    } <= review_columns
    assert {"memory_id", "approval_action_id", "activated_at"} <= active_columns


def _governance_repository(
    reviews: EpisodicMemoryReviewRepository,
) -> EpisodicMemoryGovernanceRepository:
    return cast(EpisodicMemoryGovernanceRepository, reviews)


def _correct(
    candidate: EpisodicMemoryCandidate,
    expected_revision_id: EventId,
    *,
    key: str = "governance:correct:one",
    claim: str = "The user-verified corrected outcome passed the complete gate.",
    sensitivity: Sensitivity = Sensitivity.RESTRICTED,
    seed: int = 3,
    minutes: int = 2,
) -> EpisodicMemoryGovernanceAction:
    return EpisodicMemoryGovernanceAction.correct(
        scope=candidate.scope,
        memory_id=candidate.memory_id,
        expected_revision_id=expected_revision_id,
        source_action_key=key,
        reason="The user supplied a verified correction to the extracted claim.",
        corrected_claim=claim,
        corrected_sensitivity=sensitivity,
        occurred_at=NOW + timedelta(minutes=minutes),
        evidence_references=(_review_evidence(seed),),
    )


def _retract(
    candidate: EpisodicMemoryCandidate,
    expected_revision_id: EventId,
    *,
    key: str = "governance:retract:one",
    seed: int = 4,
    minutes: int = 3,
) -> EpisodicMemoryGovernanceAction:
    return EpisodicMemoryGovernanceAction.retract(
        scope=candidate.scope,
        memory_id=candidate.memory_id,
        expected_revision_id=expected_revision_id,
        source_action_key=key,
        reason="The user withdrew this memory after reviewing its evidence.",
        occurred_at=NOW + timedelta(minutes=minutes),
        evidence_references=(_review_evidence(seed),),
    )


def _approved(
    adapter: str, tmp_path: Path
) -> tuple[
    EpisodicMemoryCandidate,
    EpisodicCandidateReviewAction,
    EpisodicMemoryGovernanceRepository,
]:
    candidate, reviews = _stored_candidate(adapter, tmp_path)
    approval = _action(candidate)
    reviews.review_episodic_memory_candidate(approval)
    return candidate, approval, _governance_repository(reviews)


def test_governance_contract_and_replay_are_strict_deterministic_and_payload_safe() -> None:
    event = _event(_scope())
    candidate = _candidate(event)
    approval = _action(candidate)
    active = ActiveEpisodicMemory.approve(candidate, approval)
    correction = _correct(candidate, approval.action_id)
    retraction = _retract(candidate, correction.action_id)

    assert EpisodicMemoryGovernanceAction.from_dict(correction.to_dict()) == correction
    first = replay_episodic_memory_revisions(active, (correction,))
    replayed = replay_episodic_memory_revisions(active, (correction, retraction))
    assert [item.status for item in first] == [
        EpisodicMemoryRevisionStatus.SUPERSEDED,
        EpisodicMemoryRevisionStatus.ACTIVE,
    ]
    assert active_episodic_memory_at_revision(active, first[-1]).memory.claim == (
        correction.corrected_claim
    )
    assert [item.status for item in replayed] == [
        EpisodicMemoryRevisionStatus.SUPERSEDED,
        EpisodicMemoryRevisionStatus.SUPERSEDED,
        EpisodicMemoryRevisionStatus.RETRACTED,
    ]
    assert replayed[-1].claim is None
    assert replayed[-1].sensitivity is None
    with pytest.raises(ValueError, match="fields are invalid"):
        EpisodicMemoryGovernanceAction.from_dict({**correction.to_dict(), "provider": "model"})
    with pytest.raises(ValueError, match="only a user"):
        EpisodicMemoryGovernanceAction(
            correction.action_id,
            correction.scope,
            correction.memory_id,
            correction.kind,
            TaskActivityActor.AGENT,
            correction.expected_revision_id,
            correction.source_action_key,
            correction.reason,
            correction.corrected_claim,
            correction.corrected_sensitivity,
            correction.occurred_at,
            correction.evidence_references,
        )


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_correction_is_scoped_idempotent_revisioned_and_restart_durable(
    adapter: str, tmp_path: Path
) -> None:
    candidate, approval, governance = _approved(adapter, tmp_path)
    correction = _correct(candidate, approval.action_id)

    first = governance.govern_episodic_memory(correction)
    second = governance.govern_episodic_memory(correction)

    assert first.idempotent is False
    assert second.idempotent is True
    assert first.current_revision == second.current_revision
    assert first.active_memory is not None
    assert first.active_memory.memory_id == candidate.memory_id
    assert first.active_memory.memory.claim == correction.corrected_claim
    assert first.active_memory.source_event_id == candidate.source_event_id
    assert first.active_memory.extractor_version == candidate.extractor_version
    assert first.active_memory.memory.retention == candidate.retention
    revisions = governance.list_episodic_memory_revisions(candidate.scope, candidate.memory_id)
    assert [item.revision_number for item in revisions] == [1, 2]
    assert revisions[0].status is EpisodicMemoryRevisionStatus.SUPERSEDED
    assert revisions[1].predecessor_revision_id == approval.action_id
    assert (
        governance.get_episodic_memory_governance(candidate.scope, correction.action_id)
        == correction
    )
    review_repo = cast(EpisodicMemoryReviewRepository, governance)
    assert (
        review_repo.get_active_episodic_memory(candidate.scope, candidate.memory_id)
        == first.active_memory
    )
    with pytest.raises(EpisodicMemoryGovernanceNotFound):
        governance.get_episodic_memory_governance(_scope(2), correction.action_id)

    if isinstance(governance, SQLiteCheckpointRepository):
        reopened = SQLiteCheckpointRepository(governance.path, base_directory=tmp_path)
        assert (
            reopened.list_episodic_memory_revisions(candidate.scope, candidate.memory_id)
            == revisions
        )
        assert (
            reopened.get_active_episodic_memory(candidate.scope, candidate.memory_id)
            == first.active_memory
        )


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_stale_unsafe_or_weaker_corrections_fail_without_fork(adapter: str, tmp_path: Path) -> None:
    candidate, approval, governance = _approved(adapter, tmp_path)
    correction = _correct(candidate, approval.action_id)
    governance.govern_episodic_memory(correction)

    with pytest.raises(EpisodicMemoryGovernanceConflict):
        governance.govern_episodic_memory(
            _correct(
                candidate,
                approval.action_id,
                key="governance:stale",
                claim="This stale correction must not fork history.",
                seed=5,
                minutes=3,
            )
        )
    with pytest.raises(EpisodicMemoryGovernanceRejected):
        governance.govern_episodic_memory(
            _correct(
                candidate,
                correction.action_id,
                key="governance:weaker",
                sensitivity=Sensitivity.NORMAL,
                seed=6,
                minutes=3,
            )
        )
    with pytest.raises(EpisodicMemoryGovernanceRejected):
        governance.govern_episodic_memory(
            _correct(
                candidate,
                correction.action_id,
                key="governance:secret",
                claim="api_key=ABCDEFGHIJKLMNOPQRSTUVWX",
                seed=7,
                minutes=3,
            )
        )
    assert len(governance.list_episodic_memory_revisions(candidate.scope, candidate.memory_id)) == 2


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_retraction_is_terminal_idempotent_and_excluded_from_active_reads(
    adapter: str, tmp_path: Path
) -> None:
    candidate, approval, governance = _approved(adapter, tmp_path)
    correction = _correct(candidate, approval.action_id)
    governance.govern_episodic_memory(correction)
    retraction = _retract(candidate, correction.action_id)

    result = governance.govern_episodic_memory(retraction)
    assert result.active_memory is None
    assert governance.govern_episodic_memory(retraction).idempotent is True
    history = governance.list_episodic_memory_revisions(candidate.scope, candidate.memory_id)
    assert history[-1].status is EpisodicMemoryRevisionStatus.RETRACTED
    assert history[-1].claim is None
    reviews = cast(EpisodicMemoryReviewRepository, governance)
    assert reviews.review_episodic_memory_candidate(approval).active_memory is None
    with pytest.raises(ActiveEpisodicMemoryNotFound):
        reviews.get_active_episodic_memory(candidate.scope, candidate.memory_id)
    assert reviews.list_active_episodic_memories(candidate.scope).items == ()
    with pytest.raises(EpisodicMemoryGovernanceConflict):
        governance.govern_episodic_memory(
            _correct(
                candidate,
                retraction.action_id,
                key="governance:after-retraction",
                seed=8,
                minutes=4,
            )
        )


def test_reference_and_sqlite_replay_identical_governance_streams(tmp_path: Path) -> None:
    histories = []
    for adapter in ("reference", "sqlite"):
        candidate, approval, governance = _approved(adapter, tmp_path)
        correction = _correct(candidate, approval.action_id)
        governance.govern_episodic_memory(correction)
        governance.govern_episodic_memory(_retract(candidate, correction.action_id))
        histories.append(
            governance.list_episodic_memory_revisions(candidate.scope, candidate.memory_id)
        )
    assert histories[0] == histories[1]


def test_sqlite_governance_failure_is_atomic(tmp_path: Path) -> None:
    candidate, approval, governance = _approved("sqlite", tmp_path)
    assert isinstance(governance, SQLiteCheckpointRepository)
    with sqlite3.connect(governance.path) as connection:
        connection.execute(
            "CREATE TRIGGER reject_governance_evidence BEFORE INSERT ON "
            "episodic_memory_governance_evidence "
            "BEGIN SELECT RAISE(ABORT, 'injected governance failure'); END"
        )
    correction = _correct(candidate, approval.action_id)

    with pytest.raises(EpisodicMemoryGovernanceStorageFailure):
        governance.govern_episodic_memory(correction)
    assert len(governance.list_episodic_memory_revisions(candidate.scope, candidate.memory_id)) == 1
    with pytest.raises(EpisodicMemoryGovernanceNotFound):
        governance.get_episodic_memory_governance(candidate.scope, correction.action_id)


def test_governance_migration_is_additive_atomic_and_preserves_active_memory(
    tmp_path: Path,
) -> None:
    candidate, _, governance = _approved("sqlite", tmp_path)
    assert isinstance(governance, SQLiteCheckpointRepository)
    with sqlite3.connect(governance.path) as connection:
        connection.execute("DROP TABLE episodic_memory_deletions")
        connection.execute("DROP TABLE episodic_memory_expirations")
        connection.execute("DROP TABLE episodic_memory_governance_evidence")
        connection.execute("DROP TABLE episodic_memory_governance")
        connection.execute("DELETE FROM schema_migrations WHERE version >= 22")

    with pytest.raises(SQLiteMigrationError, match="injected migration failure"):
        governance.migrate(fail_after_version=22)
    assert governance.schema_version() == 21
    with sqlite3.connect(governance.path) as connection:
        assert connection.execute(
            "SELECT memory_id FROM active_episodic_memories WHERE memory_id = ?",
            (str(candidate.memory_id),),
        ).fetchone() == (str(candidate.memory_id),)
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='episodic_memory_governance'"
            ).fetchone()
            is None
        )

    governance.migrate()
    assert governance.schema_version() == 29
    with sqlite3.connect(governance.path) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(episodic_memory_governance)"
            ).fetchall()
        }
    assert {
        "action_id",
        "memory_id",
        "action_kind",
        "expected_revision_id",
        "source_action_key",
        "reason",
        "corrected_claim",
        "corrected_sensitivity",
        "owner_id",
        "project_id",
        "session_id",
        "task_id",
        "occurred_at",
    } <= columns
