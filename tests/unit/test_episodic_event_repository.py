from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from mnemo_memory.packages.domain import (
    CheckpointAggregate,
    CheckpointContent,
    CheckpointEventKind,
    CheckpointId,
    CheckpointLifecycleEvent,
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
    ReferenceCheckpointLifecycleEventRepository,
    SQLiteCheckpointRepository,
)
from mnemo_memory.packages.storage.contracts import CheckpointRepository, EpisodicEventNotFound

NOW = datetime(2026, 8, 3, tzinfo=UTC)


def scope() -> MemoryScope:
    return MemoryScope(
        OwnerId.new(),
        ScopeLevel.TASK,
        Visibility.PROJECT,
        WorkspaceId.new(),
        ProjectId.new(),
        session_id=SessionId.new(),
        task_id=TaskId.new(),
    )


def evidence() -> EvidenceReference:
    return EvidenceReference(
        EvidenceId.new(),
        SourceId.new(),
        EvidenceSourceType.CHECKPOINT,
        SourceTrustClass.USER_AUTHORED,
        "fixture://event",
        "sha256:" + "a" * 64,
        EvidenceLocation("fixture://event"),
        NOW,
        VerificationStatus.VERIFIED,
    )


class _RevisionLookup:
    def __init__(self, value: CheckpointRevision) -> None:
        self.value = value

    def get_revision(
        self,
        _scope: MemoryScope,
        _checkpoint_id: CheckpointId,
        *,
        revision_id: CheckpointRevisionId,
    ) -> CheckpointRevision:
        assert revision_id == self.value.revision_id
        return self.value


def test_reference_event_ledger_is_scoped_idempotent_and_ordered() -> None:
    item_scope = scope()
    checkpoint_id = CheckpointId.new()
    revision_id = CheckpointRevisionId.new()
    item = evidence()
    revision = CheckpointRevision(
        revision_id,
        checkpoint_id,
        1,
        None,
        item_scope,
        CheckpointContent("task", (), "active", ("next",), (), (), (), (), (), (), 1),
        CheckpointStatus.ACTIVE,
        (item,),
        NOW,
    )
    repository = ReferenceCheckpointLifecycleEventRepository(
        cast(CheckpointRepository, _RevisionLookup(revision))
    )
    event = CheckpointLifecycleEvent.for_revision(
        scope=item_scope,
        kind=CheckpointEventKind.CREATED,
        checkpoint_id=checkpoint_id,
        revision_id=revision_id,
        revision_number=1,
        occurred_at=NOW,
        evidence_references=(item,),
    )

    assert repository.append_event(event).idempotent is False
    assert repository.append_event(event).idempotent is True
    assert repository.list_events(item_scope).items == (event,)
    with pytest.raises(EpisodicEventNotFound):
        repository.get_event(scope(), event.event_id)


def test_sqlite_event_ledger_is_durable_scoped_and_idempotent(tmp_path: Path) -> None:
    item_scope = scope()
    checkpoint_id = CheckpointId.new()
    revision_id = CheckpointRevisionId.new()
    item = evidence()
    revision = CheckpointRevision(
        revision_id,
        checkpoint_id,
        1,
        None,
        item_scope,
        CheckpointContent("task", (), "active", ("next",), (), (), (), (), (), (), 1),
        CheckpointStatus.ACTIVE,
        (item,),
        NOW,
    )
    aggregate = CheckpointAggregate(
        checkpoint_id, item_scope, revision_id, 1, CheckpointStatus.ACTIVE, NOW, NOW
    )
    repository = SQLiteCheckpointRepository(tmp_path / "mnemo.sqlite3", base_directory=tmp_path)
    repository.migrate()
    repository.create_checkpoint_aggregate(aggregate, revision)
    event = CheckpointLifecycleEvent.for_revision(
        scope=item_scope,
        kind=CheckpointEventKind.CREATED,
        checkpoint_id=checkpoint_id,
        revision_id=revision_id,
        revision_number=1,
        occurred_at=NOW,
        evidence_references=(item,),
    )

    assert repository.append_event(event).idempotent is False
    assert repository.append_event(event).idempotent is True
    assert repository.get_event(item_scope, event.event_id) == event
    assert repository.list_events(item_scope).items == (event,)
    assert repository.list_events(scope()).items == ()
