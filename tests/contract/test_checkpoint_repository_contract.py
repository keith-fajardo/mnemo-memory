import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from packages.domain import (
    Checkpoint,
    CheckpointId,
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
from packages.storage import SQLiteCheckpointRepository, SQLiteMigrationError

NOW = datetime(2026, 8, 2, 9, 0, tzinfo=UTC)
HASH = "sha256:" + "c" * 64


def scoped() -> MemoryScope:
    return MemoryScope(
        OwnerId.new(),
        ScopeLevel.TASK,
        Visibility.PROJECT,
        WorkspaceId.new(),
        ProjectId.new(),
        SessionId.new(),
        TaskId.new(),
        None,
    )


def evidence() -> EvidenceReference:
    return EvidenceReference(
        EvidenceId.new(),
        SourceId.new(),
        EvidenceSourceType.REPOSITORY,
        SourceTrustClass.CURRENT_STRUCTURAL,
        "git:abc:file.py",
        HASH,
        EvidenceLocation("repo://file.py"),
        NOW,
        VerificationStatus.VERIFIED,
    )


def checkpoint(scope: MemoryScope, evidence_reference: EvidenceReference) -> Checkpoint:
    return Checkpoint(
        CheckpointId.new(),
        scope,
        "Finish storage",
        (),
        "draft",
        (),
        (),
        (),
        (),
        ("packages/storage/sqlite.py",),
        (),
        ("pytest",),
        (evidence_reference,),
        CheckpointStatus.DRAFT,
        1,
        None,
        None,
        10,
        NOW,
        NOW,
    )


def repository(tmp_path: Path) -> SQLiteCheckpointRepository:
    result = SQLiteCheckpointRepository(tmp_path / "mnemo.sqlite3", base_directory=tmp_path)
    result.migrate()
    return result


def test_migration_fresh_idempotent_and_connection_settings(tmp_path: Path) -> None:
    result = SQLiteCheckpointRepository(tmp_path / "mnemo.sqlite3", base_directory=tmp_path)
    assert result.schema_version() == 0
    result.migrate()
    result.migrate()
    assert result.schema_version() == 2
    assert result.connection_settings() == {"foreign_keys": 1, "busy_timeout": 5000}


def test_injected_migration_failure_is_safe(tmp_path: Path) -> None:
    failed = SQLiteCheckpointRepository(tmp_path / "failed.sqlite3", base_directory=tmp_path)
    with pytest.raises(SQLiteMigrationError):
        failed.migrate(fail_after_version=1)
    assert failed.schema_version() == 0


def test_checkpoint_evidence_round_trip_scope_isolation_and_history(tmp_path: Path) -> None:
    result = repository(tmp_path)
    scope = scoped()
    reference = evidence()
    original = checkpoint(scope, reference)
    result.create_evidence(reference)
    result.create_checkpoint(original)
    assert result.get_evidence(reference.evidence_id) == reference
    assert result.get_checkpoint(original.checkpoint_id, scope) == original
    assert result.get_current_checkpoint(scope) == original
    replacement_id = CheckpointId.new()
    _, replacement = original.revise(replacement_id, NOW + timedelta(minutes=1))
    result.supersede(original, replacement)
    assert result.get_current_checkpoint(scope) == replacement
    assert result.list_checkpoint_history(original.checkpoint_id, scope) == (original, replacement)
    other_scope = scoped()
    assert result.get_checkpoint(original.checkpoint_id, other_scope) is None
    assert result.get_current_checkpoint(other_scope) is None


def test_foreign_keys_duplicates_rollback_and_path_safety(tmp_path: Path) -> None:
    result = repository(tmp_path)
    scope = scoped()
    reference = evidence()
    value = checkpoint(scope, reference)
    result.create_checkpoint(value)
    with pytest.raises(sqlite3.IntegrityError):
        result.create_checkpoint(value)
    assert result.get_checkpoint(value.checkpoint_id, scope) == value
    with pytest.raises(ValueError, match="escapes"):
        SQLiteCheckpointRepository(tmp_path / ".." / "escape.sqlite3", base_directory=tmp_path)
