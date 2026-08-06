"""Explicit checkpoint deletion, payload erasure, and anti-resurrection coverage."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mnemo_memory.packages.application import CheckpointDeletionService
from mnemo_memory.packages.domain import (
    CheckpointAggregate,
    CheckpointContent,
    CheckpointDeletion,
    CheckpointId,
    CheckpointRevision,
    CheckpointRevisionId,
    CheckpointStatus,
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
    CheckpointDeletionConflict,
    CheckpointDeletionNotFound,
    CheckpointNotFound,
    DuplicateCheckpoint,
    ReferenceCheckpointRepository,
    SQLiteCheckpointRepository,
    SQLiteMigrationError,
)

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def _scope(seed: int = 1) -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string(f"00000000-0000-0000-0000-{seed:012d}"),
        ScopeLevel.TASK,
        Visibility.PROJECT,
        WorkspaceId.from_string(f"10000000-0000-0000-0000-{seed:012d}"),
        ProjectId.from_string(f"20000000-0000-0000-0000-{seed:012d}"),
        SessionId.from_string(f"30000000-0000-0000-0000-{seed:012d}"),
        TaskId.from_string(f"40000000-0000-0000-0000-{seed:012d}"),
    )


def _evidence(seed: str) -> EvidenceReference:
    return EvidenceReference(
        EvidenceId.new(),
        SourceId.new(),
        EvidenceSourceType.CHECKPOINT,
        SourceTrustClass.USER_AUTHORED,
        f"checkpoint-deletion:{seed}",
        "sha256:" + seed * 64,
        EvidenceLocation(f"fixture://checkpoint-deletion/{seed}"),
        NOW,
        VerificationStatus.VERIFIED,
    )


def _content(seed: str) -> CheckpointContent:
    return CheckpointContent(
        "delete one exact checkpoint",
        (f"completed-{seed}",),
        "active",
        (f"remaining-{seed}",),
        (f"decision-{seed}",),
        (),
        (),
        ("src/checkpoint.py",),
        (),
        ("pytest",),
        24,
    )


def _stored(
    repository: ReferenceCheckpointRepository | SQLiteCheckpointRepository,
) -> tuple[MemoryScope, CheckpointAggregate, CheckpointRevision]:
    scope = _scope()
    checkpoint_id = CheckpointId.new()
    revision = CheckpointRevision(
        CheckpointRevisionId.new(),
        checkpoint_id,
        1,
        None,
        scope,
        _content("a"),
        CheckpointStatus.ACTIVE,
        (_evidence("a"),),
        NOW,
    )
    aggregate = CheckpointAggregate(
        checkpoint_id,
        scope,
        revision.revision_id,
        1,
        CheckpointStatus.ACTIVE,
        NOW,
        NOW,
    )
    repository.create_checkpoint_aggregate(aggregate, revision)
    repository.append_revision(
        scope,
        checkpoint_id,
        revision.revision_id,
        _content("b"),
        (_evidence("b"),),
        NOW + timedelta(minutes=1),
    )
    return scope, aggregate, revision


@pytest.mark.parametrize(
    "repository",
    [ReferenceCheckpointRepository()],
)
def test_reference_deletion_is_idempotent_and_prevents_resurrection(
    repository: ReferenceCheckpointRepository,
) -> None:
    scope, aggregate, initial = _stored(repository)
    service = CheckpointDeletionService(repository)

    with pytest.raises(CheckpointDeletionNotFound):
        service.delete(
            scope=_scope(2),
            checkpoint_id=aggregate.checkpoint_id,
            source_action_key="user:delete:wrong-scope",
            deleted_at=NOW + timedelta(minutes=2),
        )

    result = service.delete(
        scope=scope,
        checkpoint_id=aggregate.checkpoint_id,
        source_action_key="user:delete:checkpoint-1",
        deleted_at=NOW + timedelta(minutes=2),
    )

    assert (result.revision_count, result.event_count, result.outbox_count) == (2, 2, 2)
    assert CheckpointDeletion.from_dict(result.deletion.to_dict()) == result.deletion
    tampered = result.deletion.to_dict()
    tampered["source_action_key"] = "user:delete:tampered"
    with pytest.raises(ValueError, match="not deterministic"):
        CheckpointDeletion.from_dict(tampered)
    assert repository.get_checkpoint_deletion(scope, aggregate.checkpoint_id) == result.deletion
    with pytest.raises(CheckpointNotFound):
        repository.get_aggregate(scope, aggregate.checkpoint_id)
    assert service.delete(
        scope=scope,
        checkpoint_id=aggregate.checkpoint_id,
        source_action_key="user:delete:checkpoint-1",
        deleted_at=NOW + timedelta(minutes=2),
    ).idempotent
    with pytest.raises(CheckpointDeletionConflict):
        repository.delete_checkpoint(
            CheckpointDeletion.create(
                scope=scope,
                checkpoint_id=aggregate.checkpoint_id,
                source_action_key="user:delete:different",
                deleted_at=NOW + timedelta(minutes=3),
            )
        )
    reused_scope, reused_aggregate, _ = _stored(repository)
    assert reused_scope == scope
    with pytest.raises(CheckpointDeletionConflict):
        service.delete(
            scope=scope,
            checkpoint_id=reused_aggregate.checkpoint_id,
            source_action_key="user:delete:checkpoint-1",
            deleted_at=NOW + timedelta(minutes=3),
        )
    with pytest.raises(DuplicateCheckpoint):
        repository.create_checkpoint_aggregate(aggregate, initial)


def test_sqlite_deletion_erases_payload_jobs_and_orphaned_evidence(tmp_path: Path) -> None:
    repository = SQLiteCheckpointRepository(tmp_path / "mnemo.db", base_directory=tmp_path)
    repository.migrate()
    scope, aggregate, initial = _stored(repository)

    with sqlite3.connect(repository.path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError, match="requires tombstone"):
            connection.execute(
                "DELETE FROM checkpoint_lifecycle_events WHERE checkpoint_id = ?",
                (str(aggregate.checkpoint_id),),
            )

    result = CheckpointDeletionService(repository).delete(
        scope=scope,
        checkpoint_id=aggregate.checkpoint_id,
        source_action_key="user:delete:sqlite-checkpoint",
        deleted_at=NOW + timedelta(minutes=2),
    )

    assert (result.revision_count, result.event_count, result.outbox_count) == (2, 2, 2)
    with sqlite3.connect(repository.path) as connection:
        for table in (
            "checkpoint_aggregates",
            "checkpoint_revision_records",
            "checkpoint_revision_evidence",
            "checkpoint_lifecycle_events",
            "event_outbox",
            "evidence",
        ):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        row = connection.execute(
            "SELECT checkpoint_id, actor, source_action_key FROM checkpoint_deletions"
        ).fetchone()
        assert row == (
            str(aggregate.checkpoint_id),
            "user",
            "user:delete:sqlite-checkpoint",
        )
    reused_scope, reused_aggregate, _ = _stored(repository)
    assert reused_scope == scope
    with pytest.raises(CheckpointDeletionConflict):
        CheckpointDeletionService(repository).delete(
            scope=scope,
            checkpoint_id=reused_aggregate.checkpoint_id,
            source_action_key="user:delete:sqlite-checkpoint",
            deleted_at=NOW + timedelta(minutes=3),
        )
    with pytest.raises(DuplicateCheckpoint):
        repository.create_checkpoint_aggregate(aggregate, initial)


def test_checkpoint_deletion_migration_rolls_back_and_retries(tmp_path: Path) -> None:
    repository = SQLiteCheckpointRepository(tmp_path / "migration.db", base_directory=tmp_path)

    with pytest.raises(SQLiteMigrationError, match="injected"):
        repository.migrate(fail_after_version=30)

    assert repository.schema_version() == 0
    repository.migrate()
    assert repository.schema_version() == 30
