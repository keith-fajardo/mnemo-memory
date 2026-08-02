from datetime import UTC, datetime

import pytest

from packages.domain import (
    CheckpointAggregate,
    CheckpointId,
    CheckpointRevisionId,
    CheckpointStatus,
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    SessionId,
    TaskId,
    Visibility,
)


def scope() -> MemoryScope:
    return MemoryScope(
        owner_id=OwnerId.new(),
        level=ScopeLevel.TASK,
        visibility=Visibility.PROJECT,
        project_id=ProjectId.new(),
        session_id=SessionId.new(),
        task_id=TaskId.new(),
    )


def test_checkpoint_aggregate_uses_distinct_stable_and_revision_ids() -> None:
    checkpoint_id = CheckpointId.new()
    revision_id = CheckpointRevisionId.new()
    assert str(checkpoint_id) != str(revision_id)
    assert hash(checkpoint_id) != hash(revision_id)


def test_aggregate_rejects_invalid_revision_number_and_time() -> None:
    now = datetime(2026, 8, 2, tzinfo=UTC)
    with pytest.raises(ValueError, match="positive"):
        CheckpointAggregate(
            CheckpointId.new(),
            scope(),
            CheckpointRevisionId.new(),
            0,
            CheckpointStatus.ACTIVE,
            now,
            now,
        )
