"""Verified personal SQLite backup and recovery coverage."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mnemo_memory.apps.cli.main import app
from mnemo_memory.packages.application import (
    LocalConfig,
    PersonalBackupError,
    PersonalBackupService,
    RecordApprovedEpisodicEvent,
    RetractApprovedEpisodicEvent,
    build_checkpoint_runtime,
    build_lifecycle_service,
)
from mnemo_memory.packages.application.automatic_memory import LocalMemoryProjectBindingStore
from mnemo_memory.packages.domain import (
    ApprovedEventKind,
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    SourceId,
    SourceTrustClass,
    VerificationStatus,
)
from mnemo_memory.packages.storage import SQLiteCheckpointRepository

NOW = datetime(2026, 8, 5, 14, 30, 12, 345678, tzinfo=UTC)


def _evidence(seed: str, *, user: bool = False) -> EvidenceReference:
    return EvidenceReference(
        EvidenceId.new(),
        SourceId.new(),
        EvidenceSourceType.USER_CORRECTION if user else EvidenceSourceType.TOOL_RESULT,
        SourceTrustClass.USER_CORRECTION if user else SourceTrustClass.VERIFIED_TOOL_RESULT,
        f"fixture://personal-backup/{seed}",
        "sha256:" + ("b" if user else "a") * 64,
        EvidenceLocation(f"fixture://personal-backup/{seed}"),
        NOW,
        VerificationStatus.VERIFIED,
    )


def test_backup_is_private_digest_named_and_restores_pre_mutation_state(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config = LocalConfig.defaults(tmp_path / "profile")
    build_lifecycle_service(config).initialize()
    binding = LocalMemoryProjectBindingStore(config.data_directory).enable(project)
    with build_checkpoint_runtime(config) as runtime:
        event = runtime.checkpoint_service.record_approved_event(
            RecordApprovedEpisodicEvent(
                binding.checkpoint_scope,
                ApprovedEventKind.DECISION,
                "Retain the verified recovery decision.",
                "backup:decision",
                (_evidence("decision"),),
            )
        ).event

    service = PersonalBackupService(config, clock=lambda: NOW)
    first = service.create()
    repeated = service.create()

    assert first.reused is False
    assert repeated.reused is True
    assert repeated.backup_path == first.backup_path
    assert first.schema_version == 32
    assert first.created_at == NOW
    assert first.backup_path.parent == config.data_directory / "backups"
    assert first.backup_path.name == (
        f"mnemo-v32-20260805T143012345678Z-{first.content_digest.removeprefix('sha256:')}.sqlite3"
    )
    assert first.content_digest == (
        "sha256:" + hashlib.sha256(first.backup_path.read_bytes()).hexdigest()
    )
    assert first.size_bytes == first.backup_path.stat().st_size
    assert first.backup_path.stat().st_mode & 0o777 == 0o600
    assert first.backup_path.parent.stat().st_mode & 0o777 == 0o700

    with build_checkpoint_runtime(config) as runtime:
        runtime.checkpoint_service.retract_approved_event(
            RetractApprovedEpisodicEvent(
                binding.checkpoint_scope,
                event.event_id,
                "Remove the live payload after the recovery point.",
                "backup:retract",
                (_evidence("retract", user=True),),
            )
        )
    live = SQLiteCheckpointRepository(
        config.database_path, base_directory=config.data_directory
    ).get_approved_event_record(binding.checkpoint_scope, event.event_id)
    restored = SQLiteCheckpointRepository(
        first.backup_path, base_directory=config.data_directory
    ).get_approved_event_record(binding.checkpoint_scope, event.event_id)
    assert live.event is None
    assert restored.event is not None
    assert restored.event.summary == "Retain the verified recovery decision."


def test_backup_rejects_unsafe_state_and_removes_partial_copy(tmp_path: Path) -> None:
    config = LocalConfig.defaults(tmp_path / "profile")
    with pytest.raises(PersonalBackupError):
        PersonalBackupService(config, clock=lambda: NOW).create()

    build_lifecycle_service(config).initialize()
    live_digest = hashlib.sha256(config.database_path.read_bytes()).hexdigest()

    def fail_copy(_: Path, destination: Path) -> None:
        destination.write_bytes(b"partial private payload")
        raise OSError("private copy detail")

    with pytest.raises(PersonalBackupError, match="backup failed"):
        PersonalBackupService(config, clock=lambda: NOW, copy_database=fail_copy).create()
    assert list((config.data_directory / "backups").iterdir()) == []
    assert hashlib.sha256(config.database_path.read_bytes()).hexdigest() == live_digest

    (config.data_directory / "backups").rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (config.data_directory / "backups").symlink_to(outside, target_is_directory=True)
    with pytest.raises(PersonalBackupError, match="unsafe"):
        PersonalBackupService(config, clock=lambda: NOW).create()
    assert list(outside.iterdir()) == []


def test_backup_never_overwrites_a_conflicting_digest_named_artifact(tmp_path: Path) -> None:
    config = LocalConfig.defaults(tmp_path / "profile")
    build_lifecycle_service(config).initialize()
    service = PersonalBackupService(config, clock=lambda: NOW)
    first = service.create()
    first.backup_path.write_bytes(b"conflicting user-held backup")

    with pytest.raises(PersonalBackupError, match="already exists"):
        service.create()

    assert first.backup_path.read_bytes() == b"conflicting user-held backup"
    assert not any(
        path.name.startswith(".mnemo-backup-") for path in first.backup_path.parent.iterdir()
    )


def test_backup_cli_reports_only_recovery_metadata_and_safe_failure(tmp_path: Path) -> None:
    data_dir = tmp_path / "profile"
    build_lifecycle_service(LocalConfig.defaults(data_dir)).initialize()
    result = CliRunner().invoke(app, ["backup", "--data-dir", str(data_dir)])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["schema_version"] == 32
    assert payload["content_digest"].startswith("sha256:")
    assert Path(payload["backup_path"]).is_file()
    assert "memory" not in payload

    corrupt_dir = tmp_path / "corrupt"
    corrupt_dir.mkdir()
    config = LocalConfig.defaults(corrupt_dir)
    config.config_path.write_text(json.dumps(config.to_dict()))
    config.database_path.write_bytes(b"not a sqlite database; private detail")
    os.chmod(config.database_path, 0o600)
    failed = CliRunner().invoke(app, ["backup", "--data-dir", str(corrupt_dir)])
    assert failed.exit_code != 0
    assert "MNEMO_BACKUP_FAILED" in failed.output
    assert "private detail" not in failed.output
