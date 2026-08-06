"""Scheduled checkpoint expiry is bounded, scoped, and compare-and-swap safe."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mnemo_memory.packages.application import (
    CheckpointRetentionError,
    CheckpointRetentionService,
)
from mnemo_memory.packages.domain import (
    CheckpointAggregate,
    CheckpointContent,
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
    ReferenceCheckpointRepository,
    SQLiteCheckpointRepository,
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


def _content(seed: str) -> CheckpointContent:
    return CheckpointContent(
        "expire checkpoints on schedule",
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


def _evidence(seed: str, observed_at: datetime) -> EvidenceReference:
    return EvidenceReference(
        EvidenceId.new(),
        SourceId.new(),
        EvidenceSourceType.CHECKPOINT,
        SourceTrustClass.USER_AUTHORED,
        f"checkpoint-retention:{seed}",
        "sha256:" + seed * 64,
        EvidenceLocation(f"fixture://checkpoint-retention/{seed}"),
        observed_at,
        VerificationStatus.VERIFIED,
    )


def _store(
    repository: ReferenceCheckpointRepository | SQLiteCheckpointRepository,
    scope: MemoryScope,
    seed: str,
    updated_at: datetime,
) -> tuple[CheckpointAggregate, CheckpointRevision]:
    checkpoint_id = CheckpointId.new()
    revision = CheckpointRevision(
        CheckpointRevisionId.new(),
        checkpoint_id,
        1,
        None,
        scope,
        _content(seed),
        CheckpointStatus.ACTIVE,
        (_evidence(seed, updated_at),),
        updated_at,
    )
    aggregate = CheckpointAggregate(
        checkpoint_id,
        scope,
        revision.revision_id,
        1,
        CheckpointStatus.ACTIVE,
        updated_at,
        updated_at,
    )
    repository.create_checkpoint_aggregate(aggregate, revision)
    return aggregate, revision


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_retention_expires_only_due_exact_scope_and_is_restart_safe(
    tmp_path: Path, adapter: str
) -> None:
    repository: ReferenceCheckpointRepository | SQLiteCheckpointRepository
    if adapter == "reference":
        repository = ReferenceCheckpointRepository()
    else:
        repository = SQLiteCheckpointRepository(tmp_path / "profile.sqlite3")
        repository.migrate()
    scope = _scope()
    foreign_scope = _scope(2)
    due, due_revision = _store(repository, scope, "a", NOW - timedelta(days=30))
    recent, _ = _store(repository, scope, "b", NOW - timedelta(days=29))
    foreign, _ = _store(repository, foreign_scope, "c", NOW - timedelta(days=90))

    result = CheckpointRetentionService(repository).expire_due(
        scope, as_of=NOW, retention_days=30, limit=1
    )

    assert result.scanned_count == 1
    assert result.expired_count == 1
    assert result.skipped_changed_count == 0
    assert result.expired_revisions[0].status is CheckpointStatus.EXPIRED
    assert result.expired_revisions[0].content == due_revision.content
    assert result.expired_revisions[0].evidence_references == due_revision.evidence_references
    assert (
        repository.get_aggregate(scope, due.checkpoint_id).lifecycle_status
        is CheckpointStatus.EXPIRED
    )
    assert (
        repository.get_aggregate(scope, recent.checkpoint_id).lifecycle_status
        is CheckpointStatus.ACTIVE
    )
    assert (
        repository.get_aggregate(foreign_scope, foreign.checkpoint_id).lifecycle_status
        is CheckpointStatus.ACTIVE
    )
    assert (
        CheckpointRetentionService(repository)
        .expire_due(scope, as_of=NOW, retention_days=30)
        .expired_count
        == 0
    )

    if adapter == "sqlite":
        restarted = SQLiteCheckpointRepository(tmp_path / "profile.sqlite3")
        assert (
            CheckpointRetentionService(restarted)
            .expire_due(scope, as_of=NOW, retention_days=30)
            .expired_count
            == 0
        )


class _ConcurrentRevisionRepository(ReferenceCheckpointRepository):
    def list_active_checkpoints_updated_before(
        self,
        scope: MemoryScope,
        *,
        updated_before: datetime,
        limit: int = 100,
    ) -> tuple[CheckpointAggregate, ...]:
        selected = super().list_active_checkpoints_updated_before(
            scope, updated_before=updated_before, limit=limit
        )
        current = self.get_current_revision(scope, selected[0].checkpoint_id)
        self.append_revision(
            scope,
            selected[0].checkpoint_id,
            current.revision_id,
            _content("d"),
            (_evidence("d", NOW),),
            NOW,
        )
        return selected


def test_retention_skips_a_checkpoint_changed_after_discovery() -> None:
    repository = _ConcurrentRevisionRepository()
    scope = _scope()
    aggregate, _ = _store(repository, scope, "a", NOW - timedelta(days=31))

    result = CheckpointRetentionService(repository).expire_due(scope, as_of=NOW, retention_days=30)

    assert result.expired_count == 0
    assert result.skipped_changed_count == 1
    assert (
        repository.get_aggregate(scope, aggregate.checkpoint_id).lifecycle_status
        is CheckpointStatus.ACTIVE
    )


@pytest.mark.parametrize("retention_days", [0, 3651, True])
def test_retention_rejects_invalid_policy(retention_days: int) -> None:
    with pytest.raises(CheckpointRetentionError):
        CheckpointRetentionService(ReferenceCheckpointRepository()).expire_due(
            _scope(), as_of=NOW, retention_days=retention_days
        )
