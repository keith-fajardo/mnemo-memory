"""Safe removal of supported isolated personal installations."""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from .config import LocalConfig
from .upgrades import InstallationManager

_PACKAGE_NAME = "mnemo-unified-context"


class PersonalUninstallError(RuntimeError):
    """Bounded uninstall failure without client or package-manager output."""

    def __init__(
        self,
        code: str,
        *,
        manager: InstallationManager | None = None,
        application_removed: bool = False,
        prior_service_restored: bool = False,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.manager = manager
        self.application_removed = application_removed
        self.prior_service_restored = prior_service_restored

    def to_dict(self) -> dict[str, object]:
        return {
            "status": "failed",
            "code": self.code,
            "manager": None if self.manager is None else self.manager.value,
            "application_removed": self.application_removed,
            "prior_service_restored": self.prior_service_restored,
        }


@dataclass(frozen=True, slots=True)
class PersonalUninstallResult:
    manager: InstallationManager
    service_was_running: bool
    integrations: dict[str, str]
    data_directory: Path
    data_deleted: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "status": "uninstalled",
            "manager": self.manager.value,
            "application_removed": True,
            "service_was_running": self.service_was_running,
            "integrations": dict(self.integrations),
            "data": {
                "deleted": self.data_deleted,
                "preserved": not self.data_deleted,
                "path": str(self.data_directory),
            },
        }


class _Lifecycle(Protocol):
    def status(self) -> dict[str, object]: ...

    def stop(self) -> dict[str, object]: ...


class PersonalUninstallService:
    """Remove the installed application while preserving personal data by default."""

    def __init__(
        self,
        config: LocalConfig,
        *,
        integration_cleaner: Callable[[], dict[str, str]] | None = None,
        lifecycle: _Lifecycle | None = None,
        environment_root: Path | None = None,
        python_executable: Path | None = None,
        executable_resolver: Callable[[str], str | None] = shutil.which,
        command_runner: Callable[[tuple[str, ...]], int] | None = None,
        pid_alive: Callable[[int], bool] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        data_remover: Callable[[Path], None] = shutil.rmtree,
    ) -> None:
        self._config = config
        self._clean_integrations = integration_cleaner or (lambda: {})
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
        self._remove_data = data_remover

    def uninstall(self, *, delete_data: bool = False) -> PersonalUninstallResult:
        manager = _installation_manager(self._environment_root)
        manager_path = self._manager_executable(manager)
        if delete_data:
            self._validate_data_target()
        try:
            status = self._lifecycle.status()
        except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
            raise PersonalUninstallError(
                "MNEMO_UNINSTALL_STORE_UNAVAILABLE", manager=manager
            ) from error
        was_running = status.get("running") is True
        if was_running:
            try:
                pid = _running_pid(status)
                self._lifecycle.stop()
            except PersonalUninstallError as error:
                raise PersonalUninstallError(error.code, manager=manager) from error
            except (OSError, RuntimeError, ValueError) as error:
                raise PersonalUninstallError(
                    "MNEMO_UNINSTALL_STOP_FAILED", manager=manager
                ) from error
            if not self._await_stopped(pid):
                raise PersonalUninstallError("MNEMO_UNINSTALL_STOP_TIMEOUT", manager=manager)
        try:
            integrations = self._clean_integrations()
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            restored = self._restart_after_failure(was_running)
            raise PersonalUninstallError(
                "MNEMO_UNINSTALL_INTEGRATION_CLEANUP_FAILED",
                manager=manager,
                prior_service_restored=restored,
            ) from error
        if not _valid_integration_result(integrations):
            restored = self._restart_after_failure(was_running)
            raise PersonalUninstallError(
                "MNEMO_UNINSTALL_INTEGRATION_CLEANUP_FAILED",
                manager=manager,
                prior_service_restored=restored,
            )
        if self._run(self._manager_command(manager, manager_path)) != 0:
            restored = self._restart_after_failure(was_running)
            raise PersonalUninstallError(
                "MNEMO_UNINSTALL_PACKAGE_REMOVE_FAILED",
                manager=manager,
                prior_service_restored=restored,
            )
        if delete_data:
            try:
                self._validate_data_target()
                self._remove_data(self._config.data_directory)
            except (OSError, RuntimeError, ValueError) as error:
                raise PersonalUninstallError(
                    "MNEMO_UNINSTALL_DATA_DELETE_FAILED",
                    manager=manager,
                    application_removed=True,
                ) from error
        return PersonalUninstallResult(
            manager,
            was_running,
            integrations,
            self._config.data_directory,
            delete_data,
        )

    def _manager_executable(self, manager: InstallationManager) -> Path:
        resolved = self._resolve_executable(manager.value)
        if resolved is None:
            raise PersonalUninstallError("MNEMO_UNINSTALL_MANAGER_UNAVAILABLE", manager=manager)
        path = Path(resolved).expanduser().resolve()
        if not path.is_file() or not os.access(path, os.X_OK):
            raise PersonalUninstallError("MNEMO_UNINSTALL_MANAGER_UNAVAILABLE", manager=manager)
        return path

    @staticmethod
    def _manager_command(manager: InstallationManager, executable: Path) -> tuple[str, ...]:
        if manager is InstallationManager.UV:
            return (str(executable), "tool", "uninstall", _PACKAGE_NAME)
        return (str(executable), "uninstall", _PACKAGE_NAME)

    def _validate_data_target(self) -> None:
        target = self._config.data_directory
        home = Path.home().resolve()
        working_directory = Path.cwd().resolve()
        dangerous = {Path(target.anchor), home, working_directory}
        if (
            target in dangerous
            or target in home.parents
            or target in working_directory.parents
            or not target.is_absolute()
        ):
            raise PersonalUninstallError("MNEMO_UNINSTALL_DATA_TARGET_UNSAFE")
        if not target.is_dir() or target.is_symlink():
            raise PersonalUninstallError("MNEMO_UNINSTALL_DATA_TARGET_UNSAFE")
        config_path = self._config.config_path
        database_path = self._config.database_path
        if (
            not config_path.is_file()
            or config_path.is_symlink()
            or not database_path.is_file()
            or database_path.is_symlink()
        ):
            raise PersonalUninstallError("MNEMO_UNINSTALL_DATA_TARGET_UNRECOGNIZED")
        try:
            stored = LocalConfig.load(config_path)
        except (OSError, ValueError) as error:
            raise PersonalUninstallError("MNEMO_UNINSTALL_DATA_TARGET_UNRECOGNIZED") from error
        if stored != self._config:
            raise PersonalUninstallError("MNEMO_UNINSTALL_DATA_TARGET_UNRECOGNIZED")

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

    def _cli_command(self, action: str) -> tuple[str, ...]:
        return (
            str(self._python_executable),
            "-m",
            "mnemo_memory.cli",
            action,
            "--data-dir",
            str(self._config.data_directory),
        )


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
        raise PersonalUninstallError("MNEMO_UNINSTALL_ENVIRONMENT_UNSUPPORTED")
    return owners[0]


def _running_pid(status: dict[str, object]) -> int:
    process = status.get("process")
    if not isinstance(process, dict) or not _positive_int(process.get("pid")):
        raise PersonalUninstallError("MNEMO_UNINSTALL_PROCESS_STATE_INVALID")
    return cast(int, process["pid"])


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _valid_integration_result(value: object) -> bool:
    return isinstance(value, dict) and all(
        isinstance(name, str) and isinstance(status, str) and name and status
        for name, status in value.items()
    )


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
