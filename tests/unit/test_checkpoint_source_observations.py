"""Checkpoint/source co-observations stay immutable, scoped, and explicitly non-causal."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from mnemo_memory.packages.application.checkpoint_deletion import CheckpointDeletionService
from mnemo_memory.packages.application.checkpoints import (
    CheckpointApplicationService,
    CreateCheckpoint,
)
from mnemo_memory.packages.domain import (
    CheckpointContent,
    CheckpointSourceObservation,
    CodeSnapshotId,
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
from mnemo_memory.packages.project_index import SourceStructureParser, SourceStructureParseRequest
from mnemo_memory.packages.storage import (
    ReferenceCheckpointRepository,
    ReferenceCheckpointSourceObservationRepository,
    ReferenceSourceStructureRepository,
    SQLiteCheckpointRepository,
    SQLiteSourceStructureRepository,
)
from mnemo_memory.packages.storage.contracts import (
    CheckpointRepository,
    CheckpointSourceObservationConflict,
    CheckpointSourceObservationNotFound,
    CheckpointSourceObservationRepository,
    SourceStructureRepository,
)

NOW = datetime(2026, 8, 4, tzinfo=UTC)


def project_scope() -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string("11111111-1111-4111-8111-111111111111"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string("22222222-2222-4222-8222-222222222222"),
        ProjectId.from_string("33333333-3333-4333-8333-333333333333"),
    )


def task_scope() -> MemoryScope:
    project = project_scope()
    return MemoryScope(
        project.owner_id,
        ScopeLevel.TASK,
        project.visibility,
        project.workspace_id,
        project.project_id,
        SessionId.new(),
        TaskId.new(),
    )


def evidence() -> EvidenceReference:
    return EvidenceReference(
        EvidenceId.new(),
        SourceId.new(),
        EvidenceSourceType.CHECKPOINT,
        SourceTrustClass.USER_AUTHORED,
        "fixture://checkpoint-source-observation",
        "sha256:" + "a" * 64,
        EvidenceLocation("fixture://checkpoint-source-observation"),
        NOW,
        VerificationStatus.VERIFIED,
    )


def content() -> CheckpointContent:
    return CheckpointContent(
        task_objective="Retain a truthful checkpoint/source reference.",
        completed_work=("Saved one durable handoff.",),
        current_state="The source observation is metadata, not an explanation.",
        remaining_work=(),
        decisions=("Keep change rationale in evidence-backed checkpoint content.",),
        failures=(),
        blockers=(),
        relevant_files=("service.py",),
        relevant_artifacts=(),
        verification_performed=("focused test passed",),
        token_estimate=90,
    )


@pytest.mark.parametrize("backend", ("reference", "sqlite"))
def test_checkpoint_source_observation_is_scoped_immutable_and_idempotent(
    tmp_path: Path, backend: str
) -> None:
    project = project_scope()
    task = task_scope()
    root = tmp_path / "source"
    root.mkdir()
    (root / "service.py").write_text("def reconcile():\n    return True\n", encoding="utf-8")
    artifact = SourceStructureParser().parse(SourceStructureParseRequest(project, root))
    checkpoints: CheckpointRepository
    source: SourceStructureRepository
    observations: CheckpointSourceObservationRepository

    if backend == "reference":
        checkpoints = ReferenceCheckpointRepository()
        source = ReferenceSourceStructureRepository()
        source.store_and_activate(artifact)
        observations = ReferenceCheckpointSourceObservationRepository(checkpoints, source)
    else:
        database = tmp_path / "data" / "mnemo.sqlite3"
        sqlite_checkpoints = SQLiteCheckpointRepository(database)
        sqlite_checkpoints.migrate()
        sqlite_source = SQLiteSourceStructureRepository(database)
        sqlite_source.migrate()
        checkpoints = sqlite_checkpoints
        source = sqlite_source
        source.store_and_activate(artifact)
        observations = sqlite_checkpoints

    revision = (
        CheckpointApplicationService(checkpoints, clock=lambda: NOW)
        .create(CreateCheckpoint(task, content(), (evidence(),)))
        .revision
    )
    observation = CheckpointSourceObservation(
        task,
        revision.checkpoint_id,
        revision.revision_id,
        artifact.snapshot.snapshot_id,
        NOW,
    )

    first = observations.append_checkpoint_source_observation(observation)
    second = observations.append_checkpoint_source_observation(observation)

    assert not first.idempotent
    assert second.idempotent
    assert (
        observations.get_checkpoint_source_observation(
            task, revision.checkpoint_id, revision.revision_id
        )
        == observation
    )
    with pytest.raises(CheckpointSourceObservationNotFound):
        observations.get_checkpoint_source_observation(
            MemoryScope(
                task.owner_id,
                ScopeLevel.TASK,
                task.visibility,
                task.workspace_id,
                ProjectId.new(),
                task.session_id,
                task.task_id,
            ),
            revision.checkpoint_id,
            revision.revision_id,
        )
    with pytest.raises(CheckpointSourceObservationNotFound):
        observations.append_checkpoint_source_observation(
            CheckpointSourceObservation(
                task,
                revision.checkpoint_id,
                revision.revision_id,
                CodeSnapshotId.new(),
                NOW,
            )
        )

    if isinstance(checkpoints, SQLiteCheckpointRepository):
        deletion = CheckpointDeletionService(checkpoints).delete(
            scope=task,
            checkpoint_id=revision.checkpoint_id,
            source_action_key="user:delete:observed-checkpoint",
            deleted_at=NOW,
        )
        assert deletion.observation_count == 1
        with pytest.raises(CheckpointSourceObservationNotFound):
            observations.get_checkpoint_source_observation(
                task, revision.checkpoint_id, revision.revision_id
            )


def test_checkpoint_source_observation_rejects_a_second_snapshot_for_one_revision(
    tmp_path: Path,
) -> None:
    project = project_scope()
    task = task_scope()
    root = tmp_path / "source"
    root.mkdir()
    path = root / "service.py"
    path.write_text("def reconcile():\n    return True\n", encoding="utf-8")
    repository = SQLiteCheckpointRepository(tmp_path / "data" / "mnemo.sqlite3")
    repository.migrate()
    source = SQLiteSourceStructureRepository(repository.path)
    source.migrate()
    first = source.store_and_activate(
        SourceStructureParser().parse(SourceStructureParseRequest(project, root))
    )
    path.write_text("def reconcile():\n    return False\n", encoding="utf-8")
    second = source.store_and_activate(
        SourceStructureParser().parse(SourceStructureParseRequest(project, root))
    )
    revision = (
        CheckpointApplicationService(repository, clock=lambda: NOW)
        .create(CreateCheckpoint(task, content(), (evidence(),)))
        .revision
    )
    repository.append_checkpoint_source_observation(
        CheckpointSourceObservation(
            task, revision.checkpoint_id, revision.revision_id, first.snapshot.snapshot_id, NOW
        )
    )

    with pytest.raises(CheckpointSourceObservationConflict):
        repository.append_checkpoint_source_observation(
            CheckpointSourceObservation(
                task,
                revision.checkpoint_id,
                revision.revision_id,
                second.snapshot.snapshot_id,
                NOW,
            )
        )
