"""Personal SQLite persistence for exact-scope semantic checkpoints."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mnemo_memory.packages.application import SemanticMemoryService
from mnemo_memory.packages.domain import (
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
    SemanticRendererProfile,
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
from mnemo_memory.packages.episodic import EpisodicDeletionService
from mnemo_memory.packages.storage import (
    SemanticCheckpointNotFound,
    SQLiteCheckpointRepository,
    SQLiteMigrationError,
    SQLiteSemanticCheckpointRepository,
    TaskActivityEventNotFound,
)

NOW = datetime(2026, 8, 12, 11, 0, tzinfo=UTC)


def _scope(seed: int = 1) -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string(f"a0000000-0000-4000-8000-{seed:012d}"),
        ScopeLevel.TASK,
        Visibility.PROJECT,
        WorkspaceId.from_string(f"b0000000-0000-4000-8000-{seed:012d}"),
        ProjectId.from_string(f"c0000000-0000-4000-8000-{seed:012d}"),
        SessionId.from_string(f"d0000000-0000-4000-8000-{seed:012d}"),
        TaskId.from_string(f"e0000000-0000-4000-8000-{seed:012d}"),
    )


def _event(scope: MemoryScope, seed: int, summary: str) -> TaskActivityEvent:
    evidence = EvidenceReference(
        EvidenceId.from_string(f"f0000000-0000-4000-8000-{seed:012d}"),
        SourceId.from_string(f"01000000-0000-4000-8000-{seed:012d}"),
        EvidenceSourceType.AGENT_EVENT,
        SourceTrustClass.APPROVED_CHECKPOINT,
        f"fixture://sqlite-semantic/{seed}",
        "sha256:" + f"{seed:064x}",
        EvidenceLocation(f"fixture://sqlite-semantic/{seed}"),
        NOW,
        VerificationStatus.VERIFIED,
    )
    return TaskActivityEvent.create(
        scope=scope,
        kind=TaskActivityEventKind.TASK_ACTIVITY,
        actor=TaskActivityActor.USER,
        summary=summary,
        source_event_key=f"sqlite-semantic:{seed}",
        sensitivity=Sensitivity.NORMAL,
        retention=RetentionSchedule(
            RetentionPolicyId.from_string("02000000-0000-4000-8000-000000000001"),
            True,
            NOW,
            NOW,
            NOW,
            None,
            None,
        ),
        occurred_at=NOW,
        evidence_references=(evidence,),
    )


def _repositories(
    tmp_path: Path,
) -> tuple[SQLiteCheckpointRepository, SQLiteSemanticCheckpointRepository, Path]:
    path = tmp_path / "semantic.sqlite3"
    events = SQLiteCheckpointRepository(path, base_directory=tmp_path)
    events.migrate()
    return events, SQLiteSemanticCheckpointRepository(path, base_directory=tmp_path), path


def test_migration_31_is_additive_and_creates_semantic_tables(tmp_path: Path) -> None:
    repository, _, path = _repositories(tmp_path)

    assert repository.schema_version() == 31
    with sqlite3.connect(path) as connection:
        names = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'semantic_%'"
            ).fetchall()
        }
    assert names == {
        "semantic_atom_source_events",
        "semantic_checkpoint_atoms",
        "semantic_checkpoint_patch_operations",
        "semantic_checkpoints",
        "semantic_compiled_events",
        "semantic_memory_atoms",
    }


def test_migration_31_rolls_back_atomically_and_retries(tmp_path: Path) -> None:
    repository, _, path = _repositories(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE semantic_checkpoint_atoms")
        connection.execute("DROP TABLE semantic_checkpoint_patch_operations")
        connection.execute("DROP TABLE semantic_compiled_events")
        connection.execute("DROP TABLE semantic_checkpoints")
        connection.execute("DROP TABLE semantic_atom_source_events")
        connection.execute("DROP TABLE semantic_memory_atoms")
        connection.execute("DELETE FROM schema_migrations WHERE version = 31")

    assert repository.schema_version() == 30
    with pytest.raises(SQLiteMigrationError, match="injected"):
        repository.migrate(fail_after_version=31)
    assert repository.schema_version() == 30
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'semantic_memory_atoms'"
            ).fetchone()
            is None
        )

    repository.migrate()
    assert repository.schema_version() == 31


def test_sqlite_service_persists_delta_snapshot_and_provenance(tmp_path: Path) -> None:
    events, checkpoints, path = _repositories(tmp_path)
    service = SemanticMemoryService(
        events,
        checkpoints,
        clock=lambda: NOW,
        snapshot_interval=3,
    )
    scope = _scope()
    first_event = _event(scope, 1, "goal: Persist semantic state.")
    first = service.save_checkpoint(scope, events=(first_event,))
    assert events.get_task_activity_event(scope, first_event.event_id) == first_event
    second = service.save_checkpoint(
        scope,
        events=(_event(scope, 2, "constraint: Do not lose evidence associations."),),
    )
    third = service.save_checkpoint(
        scope,
        events=(_event(scope, 3, "next_action: Run migration tests."),),
    )

    reopened = SQLiteSemanticCheckpointRepository(path, base_directory=tmp_path)
    restored = reopened.materialize_semantic_checkpoint(
        scope, third.checkpoint.checkpoint.checkpoint_id
    )
    assert first.checkpoint.checkpoint.checkpoint_type.value == "snapshot"
    assert first.checkpoint.checkpoint.compression_ratio > 1.0
    assert second.checkpoint.checkpoint.checkpoint_type.value == "delta"
    assert third.checkpoint.checkpoint.checkpoint_type.value == "snapshot"
    assert restored == third.checkpoint
    assert all(atom.source_event_ids for atom in restored.atoms)
    assert (
        service.render_checkpoint(restored, mode=SemanticRendererProfile.AUDIT).included_unit_count
        == 3
    )
    with sqlite3.connect(path) as connection:
        patch_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM semantic_checkpoint_patch_operations"
            ).fetchone()[0]
        )
        reference_count = int(
            connection.execute("SELECT COUNT(*) FROM semantic_checkpoint_atoms").fetchone()[0]
        )
    assert patch_count >= 6
    assert reference_count == 6


def test_authorized_source_deletion_removes_orphan_atom_and_checkpoint_reference(
    tmp_path: Path,
) -> None:
    events, checkpoints, _ = _repositories(tmp_path)
    service = SemanticMemoryService(events, checkpoints, clock=lambda: NOW)
    scope = _scope()
    event = _event(scope, 1, "constraint: Retain only while evidence exists.")
    saved = service.save_checkpoint(scope, events=(event,))

    EpisodicDeletionService(events).delete_task_event(
        scope=scope,
        event_id=event.event_id,
        source_action_key="delete-semantic-source:1",
        deleted_at=NOW,
    )

    with pytest.raises(TaskActivityEventNotFound):
        events.get_task_activity_event(scope, event.event_id)
    assert checkpoints.list_semantic_atoms(scope) == ()
    materialized = checkpoints.materialize_semantic_checkpoint(
        scope, saved.checkpoint.checkpoint.checkpoint_id
    )
    assert materialized.atoms == ()
    assert materialized.references == ()

    continued = service.save_checkpoint(
        scope,
        events=(_event(scope, 2, "goal: Continue after authorized evidence deletion."),),
    )
    assert tuple(atom.object_value for atom in continued.checkpoint.atoms) == (
        "Continue after authorized evidence deletion.",
    )


def test_sqlite_semantic_reads_are_exact_scope_isolated(tmp_path: Path) -> None:
    events, checkpoints, _ = _repositories(tmp_path)
    service = SemanticMemoryService(events, checkpoints, clock=lambda: NOW)
    scope = _scope(1)
    other = _scope(2)
    saved = service.save_checkpoint(scope, events=(_event(scope, 1, "goal: Tenant one only."),))

    assert checkpoints.get_current_semantic_checkpoint(other) is None
    assert checkpoints.list_semantic_atoms(other) == ()
    with pytest.raises(SemanticCheckpointNotFound):
        checkpoints.materialize_semantic_checkpoint(
            other, saved.checkpoint.checkpoint.checkpoint_id
        )


def test_historical_checkpoint_retains_then_active_atom_after_supersession(
    tmp_path: Path,
) -> None:
    events, checkpoints, _ = _repositories(tmp_path)
    service = SemanticMemoryService(events, checkpoints, clock=lambda: NOW)
    scope = _scope()
    first = service.save_checkpoint(scope, events=(_event(scope, 1, "goal: First objective."),))
    second = service.save_checkpoint(
        scope, events=(_event(scope, 2, "goal: Replacement objective."),)
    )

    historical = checkpoints.materialize_semantic_checkpoint(
        scope, first.checkpoint.checkpoint.checkpoint_id
    )
    current = checkpoints.materialize_semantic_checkpoint(
        scope, second.checkpoint.checkpoint.checkpoint_id
    )
    assert tuple(atom.object_value for atom in historical.atoms) == ("First objective.",)
    assert tuple(atom.object_value for atom in current.atoms) == ("Replacement objective.",)
    assert (
        sum(atom.status.value == "superseded" for atom in checkpoints.list_semantic_atoms(scope))
        == 1
    )


def test_sqlite_semantic_retry_is_idempotent_and_does_not_duplicate_state(
    tmp_path: Path,
) -> None:
    events, checkpoints, path = _repositories(tmp_path)
    service = SemanticMemoryService(events, checkpoints, clock=lambda: NOW)
    scope = _scope()
    first = service.save_checkpoint(scope, events=(_event(scope, 1, "goal: Retry without drift."),))
    retry = service.save_checkpoint(scope)

    assert retry.idempotent is True
    assert retry.checkpoint == first.checkpoint
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM semantic_memory_atoms").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM semantic_checkpoints").fetchone()[0] == 1
