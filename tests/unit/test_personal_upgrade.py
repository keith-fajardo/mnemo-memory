"""Backup-gated uv/pipx upgrade orchestration coverage."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from typer.testing import CliRunner

import mnemo_memory.packages.application.upgrades as upgrade_module
from mnemo_memory.apps.cli.main import app
from mnemo_memory.packages.application import (
    InstallationManager,
    LocalConfig,
    PersonalBackupError,
    PersonalBackupResult,
    PersonalUpgradeError,
    PersonalUpgradeService,
    RecordApprovedEpisodicEvent,
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

NOW = datetime(2026, 8, 5, 16, 0, tzinfo=UTC)


class _Backup:
    def __init__(self, result: PersonalBackupResult, log: list[object]) -> None:
        self.result = result
        self.log = log

    def create(self) -> PersonalBackupResult:
        self.log.append("backup")
        return self.result


class _FailingBackup:
    def create(self) -> PersonalBackupResult:
        raise PersonalBackupError("private backup detail")


class _Lifecycle:
    def __init__(
        self, log: list[object], *, running: bool = False, initialized: bool = True
    ) -> None:
        self.log = log
        self.running = running
        self.initialized = initialized

    def status(self) -> dict[str, object]:
        self.log.append("status")
        return {
            "initialized": self.initialized,
            "schema_version": 27 if self.initialized else 0,
            "running": self.running,
            "process": {"pid": 4242} if self.running else None,
        }

    def stop(self) -> dict[str, object]:
        self.log.append("stop")
        self.running = False
        return {"running": False, "stopped": True}


class _MalformedRunningLifecycle(_Lifecycle):
    def status(self) -> dict[str, object]:
        self.log.append("status")
        return {
            "initialized": True,
            "schema_version": 27,
            "running": True,
            "process": {"pid": "private-invalid-pid"},
        }


def _backup(tmp_path: Path) -> PersonalBackupResult:
    path = tmp_path / "backup.sqlite3"
    path.write_bytes(b"verified backup")
    return PersonalBackupResult(path, 27, NOW, "sha256:" + "a" * 64, path.stat().st_size, False)


def _manager_environment(tmp_path: Path, manager: InstallationManager) -> tuple[Path, Path]:
    environment = tmp_path / "environment"
    environment.mkdir()
    marker = "uv-receipt.toml" if manager is InstallationManager.UV else "pipx_metadata.json"
    (environment / marker).write_text("owned")
    executable = tmp_path / manager.value
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    return environment, executable


def _evidence() -> EvidenceReference:
    return EvidenceReference(
        EvidenceId.new(),
        SourceId.new(),
        EvidenceSourceType.TOOL_RESULT,
        SourceTrustClass.VERIFIED_TOOL_RESULT,
        "fixture://upgrade-recovery",
        "sha256:" + "a" * 64,
        EvidenceLocation("fixture://upgrade-recovery"),
        NOW,
        VerificationStatus.VERIFIED,
    )


def test_uv_upgrade_backs_up_first_validates_with_new_cli_and_preserves_stopped_state(
    tmp_path: Path,
) -> None:
    log: list[object] = []
    config = LocalConfig.defaults(tmp_path / "profile")
    environment, executable = _manager_environment(tmp_path, InstallationManager.UV)

    def run(command: tuple[str, ...]) -> int:
        log.append(command)
        return 0

    result = PersonalUpgradeService(
        config,
        backup_service=_Backup(_backup(tmp_path), log),
        lifecycle=_Lifecycle(log),
        environment_root=environment,
        python_executable=tmp_path / "python",
        executable_resolver=lambda name: str(executable) if name == "uv" else None,
        command_runner=run,
    ).upgrade()

    assert result.manager is InstallationManager.UV
    assert result.service_was_running is False
    assert result.service_restarted is False
    assert log == [
        "status",
        "backup",
        (str(executable), "tool", "upgrade", "mnemo-unified-context"),
        (
            str(tmp_path / "python"),
            "-m",
            "mnemo_memory.cli",
            "init",
            "--data-dir",
            str(config.data_directory),
        ),
    ]


def test_pipx_upgrade_stops_then_restores_a_running_service(tmp_path: Path) -> None:
    log: list[object] = []
    config = LocalConfig.defaults(tmp_path / "profile")
    environment, executable = _manager_environment(tmp_path, InstallationManager.PIPX)

    def run(command: tuple[str, ...]) -> int:
        log.append(command)
        return 0

    result = PersonalUpgradeService(
        config,
        backup_service=_Backup(_backup(tmp_path), log),
        lifecycle=_Lifecycle(log, running=True),
        environment_root=environment,
        python_executable=tmp_path / "python",
        executable_resolver=lambda name: str(executable) if name == "pipx" else None,
        command_runner=run,
        pid_alive=lambda _: False,
    ).upgrade()

    assert result.manager is InstallationManager.PIPX
    assert result.service_was_running is True
    assert result.service_restarted is True
    assert log[:3] == ["status", "backup", "stop"]
    assert cast(tuple[str, ...], log[3]) == (
        str(executable),
        "upgrade",
        "mnemo-unified-context",
    )
    assert cast(tuple[str, ...], log[-1])[3] == "start"


def test_upgrade_fails_before_backup_for_unowned_missing_or_uninitialized_state(
    tmp_path: Path,
) -> None:
    config = LocalConfig.defaults(tmp_path / "profile")
    log: list[object] = []
    with pytest.raises(PersonalUpgradeError) as unsupported:
        PersonalUpgradeService(
            config,
            backup_service=_Backup(_backup(tmp_path), log),
            lifecycle=_Lifecycle(log),
            environment_root=tmp_path / "unowned",
        ).upgrade()
    assert unsupported.value.code == "MNEMO_UPGRADE_ENVIRONMENT_UNSUPPORTED"
    assert log == []

    environment, _ = _manager_environment(tmp_path, InstallationManager.UV)
    with pytest.raises(PersonalUpgradeError) as unavailable:
        PersonalUpgradeService(
            config,
            backup_service=_Backup(_backup(tmp_path), log),
            lifecycle=_Lifecycle(log),
            environment_root=environment,
            executable_resolver=lambda _: None,
        ).upgrade()
    assert unavailable.value.code == "MNEMO_UPGRADE_MANAGER_UNAVAILABLE"
    assert log == []

    executable = tmp_path / "uv"
    with pytest.raises(PersonalUpgradeError) as uninitialized:
        PersonalUpgradeService(
            config,
            backup_service=_Backup(_backup(tmp_path), log),
            lifecycle=_Lifecycle(log, initialized=False),
            environment_root=environment,
            executable_resolver=lambda _: str(executable),
        ).upgrade()
    assert uninitialized.value.code == "MNEMO_UPGRADE_STORE_NOT_INITIALIZED"
    assert log == ["status"]


def test_upgrade_failure_paths_keep_backup_and_bound_service_state(tmp_path: Path) -> None:
    config = LocalConfig.defaults(tmp_path / "profile")
    environment, executable = _manager_environment(tmp_path, InstallationManager.UV)

    with pytest.raises(PersonalUpgradeError) as backup_failed:
        PersonalUpgradeService(
            config,
            backup_service=_FailingBackup(),
            lifecycle=_Lifecycle([]),
            environment_root=environment,
            executable_resolver=lambda _: str(executable),
        ).upgrade()
    assert backup_failed.value.code == "MNEMO_UPGRADE_BACKUP_FAILED"
    assert backup_failed.value.backup is None

    install_log: list[object] = []
    install_calls = 0

    def fail_install(command: tuple[str, ...]) -> int:
        nonlocal install_calls
        install_calls += 1
        install_log.append(command)
        return 1 if install_calls == 1 else 0

    with pytest.raises(PersonalUpgradeError) as install_failed:
        PersonalUpgradeService(
            config,
            backup_service=_Backup(_backup(tmp_path), install_log),
            lifecycle=_Lifecycle(install_log, running=True),
            environment_root=environment,
            python_executable=tmp_path / "python",
            executable_resolver=lambda _: str(executable),
            command_runner=fail_install,
            pid_alive=lambda _: False,
        ).upgrade()
    assert install_failed.value.code == "MNEMO_UPGRADE_INSTALL_FAILED"
    assert install_failed.value.backup is not None
    assert install_failed.value.prior_service_restored is True
    assert cast(tuple[str, ...], install_log[-1])[3] == "start"

    validation_log: list[object] = []
    validation_calls = 0

    def fail_validation(command: tuple[str, ...]) -> int:
        nonlocal validation_calls
        validation_calls += 1
        validation_log.append(command)
        return 0 if validation_calls == 1 else 1

    with pytest.raises(PersonalUpgradeError) as validation_failed:
        PersonalUpgradeService(
            config,
            backup_service=_Backup(_backup(tmp_path), validation_log),
            lifecycle=_Lifecycle(validation_log, running=True),
            environment_root=environment,
            python_executable=tmp_path / "python",
            executable_resolver=lambda _: str(executable),
            command_runner=fail_validation,
            pid_alive=lambda _: False,
        ).upgrade()
    assert validation_failed.value.code == "MNEMO_UPGRADE_VALIDATION_FAILED"
    assert validation_failed.value.backup is not None
    assert not any(
        isinstance(item, tuple) and len(item) > 3 and item[3] == "start" for item in validation_log
    )


def test_failed_upgrade_preserves_live_data_and_a_readable_recovery_copy(tmp_path: Path) -> None:
    project = tmp_path / "sample project"
    project.mkdir()
    config = LocalConfig.defaults(tmp_path / "profile")
    lifecycle = build_lifecycle_service(config)
    lifecycle.initialize()
    binding = LocalMemoryProjectBindingStore(config.data_directory).enable(project)
    with build_checkpoint_runtime(config) as runtime:
        event = runtime.checkpoint_service.record_approved_event(
            RecordApprovedEpisodicEvent(
                binding.checkpoint_scope,
                ApprovedEventKind.DECISION,
                "Keep this canonical decision through upgrade recovery.",
                "upgrade-recovery:decision",
                (_evidence(),),
            )
        ).event

    environment, executable = _manager_environment(tmp_path, InstallationManager.UV)
    with pytest.raises(PersonalUpgradeError) as failure:
        PersonalUpgradeService(
            config,
            lifecycle=lifecycle,
            environment_root=environment,
            executable_resolver=lambda _: str(executable),
            command_runner=lambda _: 1,
        ).upgrade()

    assert failure.value.code == "MNEMO_UPGRADE_INSTALL_FAILED"
    backup = failure.value.backup
    assert backup is not None and backup.backup_path.is_file()
    live = SQLiteCheckpointRepository(
        config.database_path, base_directory=config.data_directory
    ).get_approved_event_record(binding.checkpoint_scope, event.event_id)
    recovered = SQLiteCheckpointRepository(
        backup.backup_path, base_directory=config.data_directory
    ).get_approved_event_record(binding.checkpoint_scope, event.event_id)
    assert live.event is not None
    assert recovered.event is not None
    assert live.event.to_dict() == recovered.event.to_dict() == event.to_dict()


def test_upgrade_stop_timeout_never_invokes_installer(tmp_path: Path) -> None:
    config = LocalConfig.defaults(tmp_path / "profile")
    environment, executable = _manager_environment(tmp_path, InstallationManager.UV)
    log: list[object] = []
    ticks = iter((0.0, 6.0))

    def record(command: tuple[str, ...]) -> int:
        log.append(command)
        return 0

    with pytest.raises(PersonalUpgradeError) as failure:
        PersonalUpgradeService(
            config,
            backup_service=_Backup(_backup(tmp_path), log),
            lifecycle=_Lifecycle(log, running=True),
            environment_root=environment,
            executable_resolver=lambda _: str(executable),
            command_runner=record,
            pid_alive=lambda _: True,
            monotonic=lambda: next(ticks),
            sleep=lambda _: None,
        ).upgrade()

    assert failure.value.code == "MNEMO_UPGRADE_STOP_TIMEOUT"
    assert failure.value.backup is not None
    assert log == ["status", "backup", "stop"]


def test_upgrade_malformed_running_state_fails_after_backup_without_stopping(
    tmp_path: Path,
) -> None:
    config = LocalConfig.defaults(tmp_path / "profile")
    environment, executable = _manager_environment(tmp_path, InstallationManager.UV)
    log: list[object] = []

    with pytest.raises(PersonalUpgradeError) as failure:
        PersonalUpgradeService(
            config,
            backup_service=_Backup(_backup(tmp_path), log),
            lifecycle=_MalformedRunningLifecycle(log, running=True),
            environment_root=environment,
            executable_resolver=lambda _: str(executable),
            command_runner=lambda _: pytest.fail("installer must not run"),
        ).upgrade()

    assert failure.value.code == "MNEMO_UPGRADE_PROCESS_STATE_INVALID"
    assert failure.value.backup is not None
    assert log == ["status", "backup"]


def test_default_runner_discards_installer_output(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}

    def run(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout=b"private", stderr=b"secret")

    monkeypatch.setattr(subprocess, "run", run)
    assert upgrade_module._run_silent_command(("uv", "tool", "upgrade", "package")) == 0
    assert observed["stdin"] is subprocess.DEVNULL
    assert observed["stdout"] is subprocess.DEVNULL
    assert observed["stderr"] is subprocess.DEVNULL
    assert observed["timeout"] == 600


def test_upgrade_cli_reports_a_bounded_unsupported_environment(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["upgrade", "--data-dir", str(tmp_path / "profile")])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload == {
        "backup": None,
        "code": "MNEMO_UPGRADE_ENVIRONMENT_UNSUPPORTED",
        "prior_service_restored": False,
        "status": "failed",
    }
    assert str(Path.home()) not in result.output

    invalid = CliRunner().invoke(app, ["upgrade", "--data-dir", "relative-profile"])
    assert invalid.exit_code == 1
    assert json.loads(invalid.output)["code"] == "MNEMO_UPGRADE_CONFIGURATION_INVALID"
