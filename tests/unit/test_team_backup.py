"""Verified team backup manifests and restore drills are bounded and content-free."""

from __future__ import annotations

import hashlib
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mnemo_memory.packages.application import (
    TeamBackupError,
    TeamBackupService,
    TeamDatabaseInventory,
)
from mnemo_memory.packages.domain import (
    TEAM_BACKUP_FORMAT_V1,
    TeamBackupManifest,
    TeamBackupTableCount,
)

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
COUNTS = (
    TeamBackupTableCount("checkpoint_deletions", 0),
    TeamBackupTableCount("knowledge_document_tombstones", 0),
    TeamBackupTableCount("schema_migrations", 21),
    TeamBackupTableCount("workspaces", 2),
)
ERASURES = (
    TeamBackupTableCount("checkpoint_deletions", 0),
    TeamBackupTableCount("knowledge_document_tombstones", 0),
)


class _BackupPort:
    source_database = "mnemo_live"

    def __init__(self) -> None:
        self.inventory_value = TeamDatabaseInventory(21, COUNTS, ERASURES)
        self.empty_targets: list[str] = []
        self.restored_targets: list[str] = []

    def dump_snapshot(self, destination: Path) -> TeamDatabaseInventory:
        destination.write_bytes(b"MNEMO POSTGRES CUSTOM BACKUP")
        return self.inventory_value

    def validate_archive(self, archive: Path) -> None:
        if archive.read_bytes() != b"MNEMO POSTGRES CUSTOM BACKUP":
            raise TeamBackupError("MNEMO_TEAM_BACKUP_INVALID")

    def require_empty_target(self, target_database: str) -> None:
        if target_database == "not_empty":
            raise TeamBackupError("MNEMO_TEAM_RESTORE_TARGET_NOT_EMPTY")
        self.empty_targets.append(target_database)

    def restore_archive(self, archive: Path, target_database: str) -> None:
        self.restored_targets.append(target_database)

    def inventory(self, database: str) -> TeamDatabaseInventory:
        return self.inventory_value


def test_manifest_is_strict_digest_bound_and_round_trips() -> None:
    digest = "sha256:" + hashlib.sha256(b"backup").hexdigest()
    manifest = TeamBackupManifest.create(
        created_at=NOW,
        schema_version=21,
        artifact_name=f"mnemo-team-v21-{digest.removeprefix('sha256:')}.dump",
        artifact_digest=digest,
        size_bytes=6,
        table_counts=COUNTS,
        erasure_counts=ERASURES,
    )

    assert TeamBackupManifest.from_json(manifest.canonical_json()) == manifest
    with pytest.raises(ValueError, match="identity"):
        replace(manifest, size_bytes=7)
    with pytest.raises(ValueError, match="inventory"):
        replace(manifest, table_counts=tuple(reversed(COUNTS)))
    with pytest.raises(ValueError, match="identity"):
        replace(
            manifest,
            erasure_counts=(
                TeamBackupTableCount("checkpoint_deletions", 1),
                TeamBackupTableCount("knowledge_document_tombstones", 0),
            ),
        )


def test_backup_is_private_atomic_and_restore_drill_verifies_inventory(tmp_path: Path) -> None:
    port = _BackupPort()
    times = iter((10.0, 10.125))
    service = TeamBackupService(port, clock=lambda: NOW, timer=lambda: next(times))

    backup = service.create(tmp_path.resolve() / "team-backups")

    assert backup.artifact_path.read_bytes() == b"MNEMO POSTGRES CUSTOM BACKUP"
    assert backup.manifest_path.read_text() == backup.manifest.canonical_json()
    assert backup.artifact_path.stat().st_mode & 0o777 == 0o600
    assert backup.manifest_path.stat().st_mode & 0o777 == 0o600
    assert backup.artifact_path.parent.stat().st_mode & 0o777 == 0o700
    assert backup.manifest.artifact_digest == (
        "sha256:" + hashlib.sha256(backup.artifact_path.read_bytes()).hexdigest()
    )
    assert not any(path.name.startswith(".mnemo") for path in backup.artifact_path.parent.iterdir())

    restored = service.restore_drill(backup.manifest_path, target_database="mnemo_restore_drill")

    assert restored.schema_version == 21
    assert restored.table_count == 4 and restored.row_count == 23
    assert restored.duration_ms == 125
    assert port.empty_targets == ["mnemo_restore_drill"]
    assert port.restored_targets == ["mnemo_restore_drill"]


def test_restore_rejects_live_nonempty_tampered_and_mismatched_targets(tmp_path: Path) -> None:
    port = _BackupPort()
    service = TeamBackupService(port, clock=lambda: NOW)
    backup = service.create(tmp_path.resolve() / "backups")

    with pytest.raises(TeamBackupError, match="TARGET_INVALID"):
        service.restore_drill(backup.manifest_path, target_database="mnemo_live")
    with pytest.raises(TeamBackupError, match="NOT_EMPTY"):
        service.restore_drill(backup.manifest_path, target_database="not_empty")
    backup.artifact_path.write_bytes(b"tampered")
    with pytest.raises(TeamBackupError, match="BACKUP_INVALID"):
        service.restore_drill(backup.manifest_path, target_database="clean_target")

    second = service.create(tmp_path.resolve() / "backups-2")
    port.inventory_value = TeamDatabaseInventory(
        21,
        (
            TeamBackupTableCount("checkpoint_deletions", 0),
            TeamBackupTableCount("knowledge_document_tombstones", 0),
            TeamBackupTableCount("schema_migrations", 21),
            TeamBackupTableCount("workspaces", 3),
        ),
        ERASURES,
    )
    with pytest.raises(TeamBackupError, match="VERIFICATION_FAILED"):
        service.restore_drill(second.manifest_path, target_database="mismatched_target")


def test_backup_removes_partial_files_and_rejects_unsafe_directory(tmp_path: Path) -> None:
    class _FailedPort(_BackupPort):
        def dump_snapshot(self, destination: Path) -> TeamDatabaseInventory:
            destination.write_bytes(b"partial sensitive backup")
            raise TeamBackupError("MNEMO_TEAM_BACKUP_FAILED")

    directory = tmp_path.resolve() / "failed"
    with pytest.raises(TeamBackupError, match="BACKUP_FAILED"):
        TeamBackupService(_FailedPort(), clock=lambda: NOW).create(directory)
    assert list(directory.iterdir()) == []

    outside = tmp_path / "outside"
    outside.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(outside, target_is_directory=True)
    with pytest.raises(TeamBackupError, match="DIRECTORY_UNSAFE"):
        TeamBackupService(_BackupPort(), clock=lambda: NOW).create(linked)


def test_backup_never_overwrites_an_identical_final_name(tmp_path: Path) -> None:
    directory = tmp_path.resolve() / "backups"
    service = TeamBackupService(_BackupPort(), clock=lambda: NOW)
    first = service.create(directory)
    original_archive = first.artifact_path.read_bytes()
    original_manifest = first.manifest_path.read_bytes()

    with pytest.raises(TeamBackupError, match="BACKUP_CONFLICT"):
        service.create(directory)

    assert first.artifact_path.read_bytes() == original_archive
    assert first.manifest_path.read_bytes() == original_manifest
    assert sorted(path.name for path in directory.iterdir()) == sorted(
        (first.artifact_path.name, first.manifest_path.name)
    )


def test_backup_preserves_a_destination_created_during_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path.resolve() / "backups"
    original_link = os.link
    collision = b"independently published archive"

    def competing_link(source: Path, destination: Path, *, follow_symlinks: bool = True) -> None:
        if destination.suffix == ".dump":
            destination.write_bytes(collision)
            raise FileExistsError
        original_link(source, destination, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(os, "link", competing_link)

    with pytest.raises(TeamBackupError, match="BACKUP_CONFLICT"):
        TeamBackupService(_BackupPort(), clock=lambda: NOW).create(directory)

    published = tuple(directory.glob("*.dump"))
    assert len(published) == 1 and published[0].read_bytes() == collision
    assert not tuple(directory.glob("*.json"))


def test_prune_deleted_removes_only_backups_predating_an_erasure(tmp_path: Path) -> None:
    directory = tmp_path.resolve() / "backups"
    port = _BackupPort()
    before = TeamBackupService(port, clock=lambda: NOW).create(directory)
    port.inventory_value = TeamDatabaseInventory(
        21,
        COUNTS,
        (
            TeamBackupTableCount("checkpoint_deletions", 1),
            TeamBackupTableCount("knowledge_document_tombstones", 0),
        ),
    )
    after = TeamBackupService(port, clock=lambda: NOW + timedelta(seconds=1)).create(directory)

    result = TeamBackupService(port).prune_deleted(directory)

    assert result.backups_removed == 1 and result.files_removed == 2
    assert result.bytes_removed > before.manifest.size_bytes
    assert not before.artifact_path.exists() and not before.manifest_path.exists()
    assert after.artifact_path.is_file() and after.manifest_path.is_file()
    assert TeamBackupService(port).prune_deleted(directory).backups_removed == 0


def test_prune_deleted_cleans_an_interrupted_v1_backup(tmp_path: Path) -> None:
    directory = tmp_path.resolve() / "backups"
    port = _BackupPort()
    backup = TeamBackupService(port, clock=lambda: NOW).create(directory)
    value = backup.manifest.to_dict()
    value["format_version"] = TEAM_BACKUP_FORMAT_V1
    value.pop("erasure_counts")
    value["backup_id"] = str(
        TeamBackupManifest.identity(
            backup.manifest.created_at,
            backup.manifest.artifact_digest,
            backup.manifest.size_bytes,
        )
    )
    backup.manifest_path.write_text(
        TeamBackupManifest.from_dict(value).canonical_json(), encoding="utf-8"
    )
    backup.artifact_path.unlink()
    port.inventory_value = TeamDatabaseInventory(
        21,
        COUNTS,
        (
            TeamBackupTableCount("checkpoint_deletions", 1),
            TeamBackupTableCount("knowledge_document_tombstones", 0),
        ),
    )

    result = TeamBackupService(port).prune_deleted(directory)

    assert result.backups_removed == 1 and result.files_removed == 1
    assert not backup.manifest_path.exists()


def test_prune_deleted_fails_before_removing_a_valid_stale_backup(tmp_path: Path) -> None:
    directory = tmp_path.resolve() / "backups"
    port = _BackupPort()
    stale = TeamBackupService(port, clock=lambda: NOW).create(directory)
    port.inventory_value = TeamDatabaseInventory(
        21,
        COUNTS,
        (
            TeamBackupTableCount("checkpoint_deletions", 1),
            TeamBackupTableCount("knowledge_document_tombstones", 0),
        ),
    )
    invalid = directory / ("mnemo-team-v21-20260806T120001000000Z-" + "0" * 64 + ".dump.json")
    invalid.write_text("{}", encoding="utf-8")
    invalid.chmod(0o600)

    with pytest.raises(TeamBackupError, match="BACKUP_INVALID"):
        TeamBackupService(port).prune_deleted(directory)

    assert stale.artifact_path.is_file() and stale.manifest_path.is_file()


def test_prune_deleted_rejects_symlink_manifest_and_regressed_erasure_state(
    tmp_path: Path,
) -> None:
    directory = tmp_path.resolve() / "backups"
    port = _BackupPort()
    port.inventory_value = TeamDatabaseInventory(
        21,
        COUNTS,
        (
            TeamBackupTableCount("checkpoint_deletions", 1),
            TeamBackupTableCount("knowledge_document_tombstones", 0),
        ),
    )
    backup = TeamBackupService(port, clock=lambda: NOW).create(directory)
    port.inventory_value = TeamDatabaseInventory(21, COUNTS, ERASURES)

    with pytest.raises(TeamBackupError, match="DELETION_STATE_INVALID"):
        TeamBackupService(port).prune_deleted(directory)

    port.inventory_value = TeamDatabaseInventory(
        21,
        COUNTS,
        (
            TeamBackupTableCount("checkpoint_deletions", 1),
            TeamBackupTableCount("knowledge_document_tombstones", 0),
        ),
    )
    outside = tmp_path / "outside.json"
    outside.write_text(backup.manifest.canonical_json(), encoding="utf-8")
    outside.chmod(0o600)
    linked = directory / ("mnemo-team-v21-20260806T120002000000Z-" + "1" * 64 + ".dump.json")
    linked.symlink_to(outside)
    with pytest.raises(TeamBackupError, match="BACKUP_INVALID"):
        TeamBackupService(port).prune_deleted(directory)

    linked.unlink()
    directory.chmod(0o755)
    with pytest.raises(TeamBackupError, match="DIRECTORY_UNSAFE"):
        TeamBackupService(port).prune_deleted(directory)

    assert backup.artifact_path.is_file() and backup.manifest_path.is_file()
