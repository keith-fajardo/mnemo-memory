"""Backup-gated upgrades for supported isolated personal installations."""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, cast

from .backups import PersonalBackupError, PersonalBackupResult, PersonalBackupService
from .config import LocalConfig

_PACKAGE_NAME = "mnemo-unified-context"


class InstallationManager(StrEnum):
    UV = "uv"
    PIPX = "pipx"


class PersonalUpgradeError(RuntimeError):
    """Bounded upgrade failure with an optional verified recovery artifact."""

    def __init__(
        self,
        code: str,
        *,
        backup: PersonalBackupResult | None = None,
        prior_service_restored: bool = False,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.backup = backup
        self.prior_service_restored = prior_service_restored

    def to_dict(self) -> dict[str, object]:
        return {
            "status": "failed",
            "code": self.code,
            "backup": None if self.backup is None else self.backup.to_dict(),
            "prior_service_restored": self.prior_service_restored,
        }


@dataclass(frozen=True, slots=True)
class PersonalUpgradeResult:
    manager: InstallationManager
    backup: PersonalBackupResult
    service_was_running: bool
    service_restarted: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "status": "upgraded",
            "manager": self.manager.value,
            "backup": self.backup.to_dict(),
            "service_was_running": self.service_was_running,
            "service_restarted": self.service_restarted,
        }


class _BackupCreator(Protocol):
    def create(self) -> PersonalBackupResult: ...


class _Lifecycle(Protocol):
    def status(self) -> dict[str, object]: ...

    def stop(self) -> dict[str, object]: ...


class PersonalUpgradeService:
    """Upgrade through the owning tool manager only after publishing a verified backup."""

    def __init__(
        self,
        config: LocalConfig,
        *,
        backup_service: _BackupCreator | None = None,
        lifecycle: _Lifecycle | None = None,
        environment_root: Path | None = None,
        python_executable: Path | None = None,
        executable_resolver: Callable[[str], str | None] = shutil.which,
        command_runner: Callable[[tuple[str, ...]], int] | None = None,
        pid_alive: Callable[[int], bool] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config
        self._backup = backup_service or PersonalBackupService(config)
        if lifecycle is None:
            from .bootstrap import build_lifecycle_service

            lifecycle = build_lifecycle_service(config)
        self._lifecycle = lifecycle
        self._environment_root = (environment_root or Path(sys.prefix)).resolve()
        self._python_executable = (python_executable or Path(sys.executable)).resolve()
        self._resolve_executable = executable_resolver
        self._run_command = command_runner or _run_silent_command
        self._pid_alive = pid_alive or _pid_exists
        self._monotonic = monotonic
        self._sleep = sleep

    def upgrade(self) -> PersonalUpgradeResult:
        manager = _installation_manager(self._environment_root)
        manager_path = self._manager_executable(manager)
        try:
            status = self._lifecycle.status()
        except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
            raise PersonalUpgradeError("MNEMO_UPGRADE_STORE_UNAVAILABLE") from error
        if status.get("initialized") is not True or not _positive_int(status.get("schema_version")):
            raise PersonalUpgradeError("MNEMO_UPGRADE_STORE_NOT_INITIALIZED")
        try:
            backup = self._backup.create()
        except PersonalBackupError as error:
            raise PersonalUpgradeError("MNEMO_UPGRADE_BACKUP_FAILED") from error
        was_running = status.get("running") is True
        if was_running:
            try:
                pid = _running_pid(status)
            except PersonalUpgradeError as error:
                raise PersonalUpgradeError(error.code, backup=backup) from error
            try:
                self._lifecycle.stop()
            except (OSError, RuntimeError, ValueError) as error:
                raise PersonalUpgradeError("MNEMO_UPGRADE_STOP_FAILED", backup=backup) from error
            if not self._await_stopped(pid):
                raise PersonalUpgradeError("MNEMO_UPGRADE_STOP_TIMEOUT", backup=backup)
        install_code = self._run(self._manager_command(manager, manager_path))
        if install_code != 0:
            restored = self._restart_after_failure(was_running)
            raise PersonalUpgradeError(
                "MNEMO_UPGRADE_INSTALL_FAILED",
                backup=backup,
                prior_service_restored=restored,
            )
        if self._run(self._cli_command("init")) != 0:
            raise PersonalUpgradeError("MNEMO_UPGRADE_VALIDATION_FAILED", backup=backup)
        restarted = False
        if was_running:
            if self._run(self._cli_command("start")) != 0:
                raise PersonalUpgradeError("MNEMO_UPGRADE_RESTART_FAILED", backup=backup)
            restarted = True
        return PersonalUpgradeResult(manager, backup, was_running, restarted)

    def _manager_executable(self, manager: InstallationManager) -> Path:
        resolved = self._resolve_executable(manager.value)
        if resolved is None:
            raise PersonalUpgradeError("MNEMO_UPGRADE_MANAGER_UNAVAILABLE")
        path = Path(resolved).expanduser().resolve()
        if not path.is_file() or not os.access(path, os.X_OK):
            raise PersonalUpgradeError("MNEMO_UPGRADE_MANAGER_UNAVAILABLE")
        return path

    @staticmethod
    def _manager_command(manager: InstallationManager, executable: Path) -> tuple[str, ...]:
        if manager is InstallationManager.UV:
            return (str(executable), "tool", "upgrade", _PACKAGE_NAME)
        return (str(executable), "upgrade", _PACKAGE_NAME)

    def _cli_command(self, action: str) -> tuple[str, ...]:
        return (
            str(self._python_executable),
            "-m",
            "mnemo_memory.cli",
            action,
            "--data-dir",
            str(self._config.data_directory),
        )

    def _run(self, command: tuple[str, ...]) -> int:
        try:
            result = self._run_command(command)
        except (OSError, subprocess.SubprocessError, ValueError):
            return 1
        return result if isinstance(result, int) and not isinstance(result, bool) else 1

    def _await_stopped(self, pid: int) -> bool:
        deadline = self._monotonic() + 5.0
        while self._pid_alive(pid):
            if self._monotonic() >= deadline:
                return False
            self._sleep(0.05)
        return True

    def _restart_after_failure(self, was_running: bool) -> bool:
        return not was_running or self._run(self._cli_command("start")) == 0


def _installation_manager(environment_root: Path) -> InstallationManager:
    markers = {
        InstallationManager.UV: environment_root / "uv-receipt.toml",
        InstallationManager.PIPX: environment_root / "pipx_metadata.json",
    }
    owners = tuple(
        manager
        for manager, marker in markers.items()
        if marker.exists() and marker.is_file() and not marker.is_symlink()
    )
    if len(owners) != 1:
        raise PersonalUpgradeError("MNEMO_UPGRADE_ENVIRONMENT_UNSUPPORTED")
    return owners[0]


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _running_pid(status: dict[str, object]) -> int:
    process = status.get("process")
    if not isinstance(process, dict) or not _positive_int(process.get("pid")):
        raise PersonalUpgradeError("MNEMO_UPGRADE_PROCESS_STATE_INVALID")
    return cast(int, process["pid"])


def _run_silent_command(command: tuple[str, ...]) -> int:
    completed = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=600,
    )
    return completed.returncode


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
