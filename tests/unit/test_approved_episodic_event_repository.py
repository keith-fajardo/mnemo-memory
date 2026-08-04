"""Backend-neutral contract for explicit approved episodic facts."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mnemo_memory.packages.domain import (
    ApprovedEpisodicEvent,
    ApprovedEpisodicEventGovernance,
    ApprovedEventGovernanceKind,
    ApprovedEventKind,
    ApprovedEventLifecycleStatus,
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
    ApprovedEpisodicEventSecretRejected,
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


def _governance(
    scope: MemoryScope,
    target: ApprovedEpisodicEvent,
    *,
    kind: ApprovedEventGovernanceKind,
    replacement: ApprovedEpisodicEvent | None = None,
    key: str = "governance:1",
    reason: str = "The retained fact needs an explicit user correction.",
    at: datetime = NOW + timedelta(seconds=2),
) -> ApprovedEpisodicEventGovernance:
    return ApprovedEpisodicEventGovernance.create(
        scope=scope,
        kind=kind,
        target_event_id=target.event_id,
        replacement_event_id=None if replacement is None else replacement.event_id,
        reason=reason,
        source_action_key=key,
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


def test_approved_event_correction_and_retraction_are_scoped_idempotent_and_active_only(
    repository: ApprovedEpisodicEventRepository,
) -> None:
    item_scope = _scope()
    original = _event(item_scope)
    replacement = _event(
        item_scope,
        key="decision:corrected:1",
        summary="Use the verified transaction grain for the reconciliation.",
        at=NOW + timedelta(seconds=2),
    )
    correction = _governance(
        item_scope,
        original,
        kind=ApprovedEventGovernanceKind.CORRECTED,
        replacement=replacement,
    )
    repository.append_approved_event(original)

    corrected = repository.correct_approved_event(replacement, correction)
    assert corrected.idempotent is False
    assert corrected.target.status is ApprovedEventLifecycleStatus.CORRECTED
    assert corrected.replacement is not None
    assert corrected.replacement.status is ApprovedEventLifecycleStatus.ACTIVE
    assert repository.correct_approved_event(replacement, correction).idempotent is True
    assert repository.list_approved_events(item_scope).items == (replacement,)
    assert [item.status for item in repository.list_approved_event_records(item_scope).items] == [
        ApprovedEventLifecycleStatus.ACTIVE,
        ApprovedEventLifecycleStatus.CORRECTED,
    ]

    competing = _governance(
        item_scope,
        original,
        kind=ApprovedEventGovernanceKind.RETRACTED,
        key="governance:competing",
    )
    with pytest.raises(ApprovedEpisodicEventConflict):
        repository.retract_approved_event(competing)

    retraction = _governance(
        item_scope,
        replacement,
        kind=ApprovedEventGovernanceKind.RETRACTED,
        key="governance:retract-replacement",
        reason="The corrected fact was explicitly withdrawn.",
        at=NOW + timedelta(seconds=3),
    )
    retracted = repository.retract_approved_event(retraction)
    assert retracted.idempotent is False
    assert retracted.target.status is ApprovedEventLifecycleStatus.RETRACTED
    assert retracted.target.event is None
    assert repository.retract_approved_event(retraction).idempotent is True
    assert repository.list_approved_events(item_scope).items == ()
    correction_retry = repository.correct_approved_event(replacement, correction)
    assert correction_retry.idempotent is True
    assert correction_retry.replacement is not None
    assert correction_retry.replacement.status is ApprovedEventLifecycleStatus.RETRACTED
    with pytest.raises(ApprovedEpisodicEventNotFound):
        repository.get_approved_event(item_scope, replacement.event_id)


def test_approved_event_governance_preserves_cross_scope_non_disclosure(
    repository: ApprovedEpisodicEventRepository,
) -> None:
    item_scope = _scope()
    wrong_scope = _scope()
    original = _event(item_scope)
    replacement = _event(
        wrong_scope,
        key="decision:wrong-scope",
        summary="This other task must not replace the original fact.",
    )
    repository.append_approved_event(original)
    action = _governance(
        wrong_scope,
        original,
        kind=ApprovedEventGovernanceKind.CORRECTED,
        replacement=replacement,
    )

    with pytest.raises((ApprovedEpisodicEventNotFound, ApprovedEpisodicEventConflict)):
        repository.correct_approved_event(replacement, action)
    with pytest.raises(ApprovedEpisodicEventNotFound):
        repository.get_approved_event_record(wrong_scope, original.event_id)
    assert repository.list_approved_event_records(wrong_scope).items == ()


def test_approved_event_and_governance_reject_high_confidence_secrets_before_storage(
    repository: ApprovedEpisodicEventRepository,
) -> None:
    item_scope = _scope()
    secret_event = _event(
        item_scope,
        summary="api_key=ABCDEFGHIJKLMNOPQRSTUVWX",
    )
    with pytest.raises(ApprovedEpisodicEventSecretRejected):
        repository.append_approved_event(secret_event)
    assert repository.list_approved_event_records(item_scope).items == ()

    original = _event(item_scope, key="safe:original")
    repository.append_approved_event(original)
    secret_replacement = _event(
        item_scope,
        key="secret:replacement",
        summary="access_token=ABCDEFGHIJKLMNOPQRSTUVWX",
    )
    correction = _governance(
        item_scope,
        original,
        kind=ApprovedEventGovernanceKind.CORRECTED,
        replacement=secret_replacement,
    )
    with pytest.raises(ApprovedEpisodicEventSecretRejected):
        repository.correct_approved_event(secret_replacement, correction)
    secret_retraction = _governance(
        item_scope,
        original,
        kind=ApprovedEventGovernanceKind.RETRACTED,
        key="secret:retraction",
        reason="password=ABCDEFGHIJKLMNOPQRSTUVWX",
    )
    with pytest.raises(ApprovedEpisodicEventSecretRejected):
        repository.retract_approved_event(secret_retraction)
    assert repository.list_approved_events(item_scope).items == (original,)


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


def test_sqlite_approved_event_governance_is_durable_payload_erasing_and_atomic(
    tmp_path: Path,
) -> None:
    database = tmp_path / "approved-event-governance.sqlite3"
    repository = SQLiteCheckpointRepository(database, base_directory=tmp_path)
    repository.migrate()
    item_scope = _scope()
    original = _event(item_scope)
    repository.append_approved_event(original)
    replacement = _event(
        item_scope,
        key="decision:corrected:durable",
        summary="Use the verified transaction grain after reviewing the evidence.",
    )
    correction = _governance(
        item_scope,
        original,
        kind=ApprovedEventGovernanceKind.CORRECTED,
        replacement=replacement,
    )
    repository.correct_approved_event(replacement, correction)
    reopened = SQLiteCheckpointRepository(database, base_directory=tmp_path)
    assert (
        reopened.get_approved_event_record(item_scope, original.event_id).status
        is ApprovedEventLifecycleStatus.CORRECTED
    )

    retraction = _governance(
        item_scope,
        replacement,
        kind=ApprovedEventGovernanceKind.RETRACTED,
        key="governance:durable-retraction",
        reason="Remove the corrected fact payload from retained episodic memory.",
    )
    reopened.retract_approved_event(retraction)
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        assert (
            connection.execute(
                "SELECT 1 FROM approved_episodic_events WHERE event_id = ?",
                (str(replacement.event_id),),
            ).fetchone()
            is None
        )
        assert (
            connection.execute(
                "SELECT 1 FROM approved_episodic_event_evidence WHERE event_id = ?",
                (str(replacement.event_id),),
            ).fetchone()
            is None
        )
        tombstone = connection.execute(
            "SELECT target_event_id, action_kind, reason "
            "FROM approved_episodic_event_governance WHERE target_event_id = ?",
            (str(replacement.event_id),),
        ).fetchone()
        assert dict(tombstone) == {
            "target_event_id": str(replacement.event_id),
            "action_kind": "retracted",
            "reason": retraction.reason,
        }

    next_event = _event(
        item_scope,
        key="decision:atomic",
        summary="This correction must roll back with its rejected evidence link.",
    )
    next_action = _governance(
        item_scope,
        original,
        kind=ApprovedEventGovernanceKind.CORRECTED,
        replacement=next_event,
        key="governance:second-correction",
    )
    with pytest.raises(ApprovedEpisodicEventConflict):
        reopened.correct_approved_event(next_event, next_action)
    assert reopened.list_approved_events(item_scope).items == ()


def test_sqlite_governance_failures_roll_back_replacement_action_and_retraction(
    tmp_path: Path,
) -> None:
    database = tmp_path / "approved-event-governance-rollback.sqlite3"
    repository = SQLiteCheckpointRepository(database, base_directory=tmp_path)
    repository.migrate()
    item_scope = _scope()
    original = _event(item_scope)
    repository.append_approved_event(original)
    replacement = _event(
        item_scope,
        key="decision:rollback:replacement",
        summary="This replacement must roll back with the governance action.",
    )
    correction = _governance(
        item_scope,
        original,
        kind=ApprovedEventGovernanceKind.CORRECTED,
        replacement=replacement,
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TRIGGER reject_governance_evidence BEFORE INSERT "
            "ON approved_episodic_event_governance_evidence "
            "BEGIN SELECT RAISE(ABORT, 'synthetic governance failure'); END"
        )
    with pytest.raises(ApprovedEpisodicEventStorageFailure):
        repository.correct_approved_event(replacement, correction)
    assert repository.list_approved_events(item_scope).items == (original,)
    with pytest.raises(ApprovedEpisodicEventNotFound):
        repository.get_approved_event(item_scope, replacement.event_id)
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER reject_governance_evidence")
        connection.execute(
            "CREATE TRIGGER reject_approved_event_delete BEFORE DELETE "
            "ON approved_episodic_events "
            "BEGIN SELECT RAISE(ABORT, 'synthetic retraction failure'); END"
        )
    retraction = _governance(
        item_scope,
        original,
        kind=ApprovedEventGovernanceKind.RETRACTED,
        key="governance:rollback:retraction",
    )
    with pytest.raises(ApprovedEpisodicEventStorageFailure):
        repository.retract_approved_event(retraction)
    assert repository.list_approved_events(item_scope).items == (original,)
    assert (
        repository.get_approved_event_record(item_scope, original.event_id).status
        is ApprovedEventLifecycleStatus.ACTIVE
    )


def test_approved_event_migration_rolls_back_as_one_step(tmp_path: Path) -> None:
    database = tmp_path / "approved-events.sqlite3"
    repository = SQLiteCheckpointRepository(database, base_directory=tmp_path)
    repository.migrate()
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE approved_episodic_event_governance_evidence")
        connection.execute("DROP TABLE approved_episodic_event_governance")
        connection.execute("DROP TRIGGER checkpoint_source_observation_snapshot_scope_match")
        connection.execute("DROP TRIGGER checkpoint_source_observation_checkpoint_scope_match")
        connection.execute("DROP TABLE checkpoint_source_observations")
        connection.execute("DROP TABLE source_structure_files")
        connection.execute("DROP TABLE approved_episodic_event_evidence")
        connection.execute("DROP TABLE approved_episodic_events")
        connection.execute("DELETE FROM schema_migrations WHERE version >= 7")

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


def test_approved_event_governance_migration_rolls_back_as_one_step(tmp_path: Path) -> None:
    database = tmp_path / "approved-event-governance-migration.sqlite3"
    repository = SQLiteCheckpointRepository(database, base_directory=tmp_path)
    repository.migrate()
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE approved_episodic_event_governance_evidence")
        connection.execute("DROP TABLE approved_episodic_event_governance")
        connection.execute("DELETE FROM schema_migrations WHERE version = 13")

    with pytest.raises(SQLiteMigrationError, match="injected migration failure"):
        repository.migrate(fail_after_version=13)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone() == (12,)
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                "AND name = 'approved_episodic_event_governance'"
            ).fetchone()
            is None
        )
    repository.migrate()
