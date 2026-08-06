"""Portable checkpoint history and verified import coverage."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mnemo_memory.packages.application import (
    CheckpointDeletionService,
    CheckpointExportService,
    CheckpointImportService,
    CheckpointTransferConflict,
)
from mnemo_memory.packages.domain import (
    CheckpointAggregate,
    CheckpointContent,
    CheckpointDeletion,
    CheckpointExportBundle,
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
from mnemo_memory.packages.storage import ReferenceCheckpointRepository, SQLiteCheckpointRepository

NOW = datetime(2026, 8, 6, 3, 0, tzinfo=UTC)


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
        f"checkpoint-transfer:{seed}",
        "sha256:" + seed * 64,
        EvidenceLocation(f"fixture://checkpoint-transfer/{seed}"),
        NOW,
        VerificationStatus.VERIFIED,
    )


def _content(seed: str, *, complete: bool = False) -> CheckpointContent:
    return CheckpointContent(
        "transfer the exact checkpoint history",
        (f"completed-{seed}",),
        "complete" if complete else "active",
        () if complete else (f"remaining-{seed}",),
        (f"decision-{seed}",),
        (),
        (),
        ("src/checkpoint.py",),
        (),
        ("pytest",),
        32,
    )


def _create_initial(
    repository: ReferenceCheckpointRepository | SQLiteCheckpointRepository,
    scope: MemoryScope,
    seed: str,
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
        (_evidence(seed),),
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
    return aggregate, revision


def _populated_reference(scope: MemoryScope) -> ReferenceCheckpointRepository:
    repository = ReferenceCheckpointRepository()
    first_aggregate, first = _create_initial(repository, scope, "a")
    second = repository.append_revision(
        scope,
        first_aggregate.checkpoint_id,
        first.revision_id,
        _content("b"),
        (_evidence("b"),),
        NOW + timedelta(minutes=1),
    )
    repository.complete_checkpoint(
        scope,
        first_aggregate.checkpoint_id,
        second.revision_id,
        _content("c", complete=True),
        (_evidence("c"),),
        NOW + timedelta(minutes=2),
    )
    active_aggregate, active = _create_initial(repository, scope, "d")
    repository.expire_checkpoint(
        scope,
        active_aggregate.checkpoint_id,
        active.revision_id,
        active.content,
        active.evidence_references,
        NOW + timedelta(minutes=2),
    )
    deleted_aggregate, _ = _create_initial(repository, scope, "e")
    CheckpointDeletionService(repository).delete(
        scope=scope,
        checkpoint_id=deleted_aggregate.checkpoint_id,
        source_action_key="user:delete:portable-checkpoint",
        deleted_at=NOW + timedelta(minutes=3),
    )
    return repository


def test_checkpoint_export_is_canonical_strict_and_tamper_evident() -> None:
    scope = _scope()
    bundle = CheckpointExportService(_populated_reference(scope)).export(
        scope, exported_at=NOW + timedelta(minutes=3)
    )

    assert len(bundle.aggregates) == 2
    assert len(bundle.revisions) == 5
    assert len(bundle.lifecycle_events) == 5
    assert len(bundle.deletions) == 1
    assert CheckpointExportBundle.from_json(bundle.canonical_json()) == bundle

    tampered = json.loads(bundle.canonical_json())
    tampered["revisions"][0]["content"]["current_state"] = "tampered"
    with pytest.raises(ValueError, match="digest"):
        CheckpointExportBundle.from_dict(tampered)
    duplicated = json.loads(bundle.canonical_json())
    duplicated["lifecycle_events"].append(duplicated["lifecycle_events"][0])
    with pytest.raises(ValueError):
        CheckpointExportBundle.from_dict(duplicated)
    deletion_tampered = json.loads(bundle.canonical_json())
    deletion_tampered["deletions"][0]["source_action_key"] = "user:delete:tampered"
    with pytest.raises(ValueError):
        CheckpointExportBundle.from_dict(deletion_tampered)
    with pytest.raises(ValueError, match="duplicate deletion"):
        CheckpointExportBundle.create(
            scope=scope,
            exported_at=bundle.exported_at,
            deletions=(bundle.deletions[0], bundle.deletions[0]),
        )
    overlap = CheckpointDeletion.create(
        scope=scope,
        checkpoint_id=bundle.aggregates[0].checkpoint_id,
        source_action_key="user:delete:overlapping-live-checkpoint",
        deleted_at=bundle.exported_at,
    )
    with pytest.raises(ValueError, match="retains deleted checkpoint payload"):
        CheckpointExportBundle.create(
            scope=scope,
            exported_at=bundle.exported_at,
            aggregates=bundle.aggregates,
            revisions=bundle.revisions,
            lifecycle_events=bundle.lifecycle_events,
            deletions=(overlap,),
        )


def test_checkpoint_export_reads_legacy_version_one_without_deletion_state() -> None:
    scope = _scope()
    current = CheckpointExportBundle.create(scope=scope, exported_at=NOW)
    legacy = current.to_dict()
    legacy["format_version"] = "mnemo.checkpoint-export.v1"
    legacy.pop("deletions")
    content = {name: value for name, value in legacy.items() if name != "content_digest"}
    encoded = json.dumps(
        content,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    legacy["content_digest"] = "sha256:" + hashlib.sha256(encoded).hexdigest()

    restored = CheckpointExportBundle.from_dict(legacy)

    assert restored.format_version == "mnemo.checkpoint-export.v1"
    assert restored.deletions == ()


def test_checkpoint_import_preserves_identities_rebases_scope_and_is_idempotent() -> None:
    source_scope = _scope()
    target_scope = _scope(2)
    source = _populated_reference(source_scope)
    bundle = CheckpointExportService(source).export(
        source_scope, exported_at=NOW + timedelta(minutes=3)
    )
    target = ReferenceCheckpointRepository()
    service = CheckpointImportService(target, target)

    result = service.import_bundle(bundle, target_scope=target_scope)
    imported = CheckpointExportService(target).export(target_scope, exported_at=bundle.exported_at)

    assert not result.idempotent
    assert result.checkpoint_count == 2
    assert result.revision_count == result.event_count == 5
    assert result.deletion_count == 1
    assert any(item.lifecycle_status is CheckpointStatus.EXPIRED for item in imported.aggregates)
    assert result.source_content_digest == bundle.content_digest
    assert result.target_content_digest == imported.content_digest
    assert result.source_content_digest != result.target_content_digest
    assert {item.checkpoint_id for item in imported.aggregates} == {
        item.checkpoint_id for item in bundle.aggregates
    }
    assert {item.revision_id for item in imported.revisions} == {
        item.revision_id for item in bundle.revisions
    }
    assert {item.event_id for item in imported.lifecycle_events} == {
        item.event_id for item in bundle.lifecycle_events
    }
    assert {item.checkpoint_id for item in imported.deletions} == {
        item.checkpoint_id for item in bundle.deletions
    }
    assert imported.deletions[0].deletion_id != bundle.deletions[0].deletion_id
    assert imported.deletions[0].source_action_key == bundle.deletions[0].source_action_key
    assert all(item.scope == target_scope for item in imported.revisions)
    assert service.import_bundle(bundle, target_scope=target_scope).idempotent

    conflict = ReferenceCheckpointRepository()
    _create_initial(conflict, target_scope, "e")
    with pytest.raises(CheckpointTransferConflict, match="conflicting state"):
        CheckpointImportService(conflict, conflict).import_bundle(bundle, target_scope=target_scope)


def test_sqlite_exports_complete_history_after_restart(tmp_path: Path) -> None:
    scope = _scope()
    path = tmp_path / "checkpoint-transfer.sqlite3"
    repository = SQLiteCheckpointRepository(path, base_directory=tmp_path)
    repository.migrate()
    aggregate, first = _create_initial(repository, scope, "a")
    repository.append_revision(
        scope,
        aggregate.checkpoint_id,
        first.revision_id,
        _content("b"),
        (_evidence("b"),),
        NOW + timedelta(minutes=1),
    )
    deleted_aggregate, _ = _create_initial(repository, scope, "e")
    CheckpointDeletionService(repository).delete(
        scope=scope,
        checkpoint_id=deleted_aggregate.checkpoint_id,
        source_action_key="user:delete:sqlite-portable-checkpoint",
        deleted_at=NOW + timedelta(minutes=2),
    )
    exported_at = NOW + timedelta(minutes=2)
    first_bundle = CheckpointExportService(repository).export(scope, exported_at=exported_at)
    reopened = SQLiteCheckpointRepository(path, base_directory=tmp_path)
    second_bundle = CheckpointExportService(reopened).export(scope, exported_at=exported_at)

    assert first_bundle == second_bundle
    assert len(second_bundle.aggregates) == 1
    assert len(second_bundle.revisions) == len(second_bundle.lifecycle_events) == 2
    assert len(second_bundle.deletions) == 1
    assert (
        CheckpointExportService(reopened).export(_scope(2), exported_at=exported_at).aggregates
        == ()
    )
