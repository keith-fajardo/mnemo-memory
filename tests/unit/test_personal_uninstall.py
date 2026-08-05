"""Safe uv/pipx uninstall orchestration coverage."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import cast

import pytest
from typer.testing import CliRunner

import mnemo_memory.apps.cli.main as cli
import mnemo_memory.packages.application.uninstalls as uninstall_module
from mnemo_memory.apps.cli.main import app
from mnemo_memory.packages.application import (
    InstallationManager,
    LocalConfig,
    PersonalUninstallError,
    PersonalUninstallService,
)


class _Lifecycle:
    def __init__(self, log: list[object], *, running: bool = False) -> None:
        self.log = log
        self.running = running

    def status(self) -> dict[str, object]:
        self.log.append("status")
        return {
            "initialized": True,
            "schema_version": 27,
            "running": self.running,
            "process": {"pid": 4242} if self.running else None,
        }

    def stop(self) -> dict[str, object]:
        self.log.append("stop")
        self.running = False
        return {"running": False, "stopped": True}


def _manager_environment(tmp_path: Path, manager: InstallationManager) -> tuple[Path, Path]:
    environment = tmp_path / f"{manager.value}-environment"
    environment.mkdir()
    marker = "uv-receipt.toml" if manager is InstallationManager.UV else "pipx_metadata.json"
    (environment / marker).write_text("owned", encoding="utf-8")
    executable = tmp_path / manager.value
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o700)
    return environment, executable


def _recognized_profile(tmp_path: Path) -> LocalConfig:
    config = LocalConfig.defaults(tmp_path / "profile")
    config.data_directory.mkdir()
    config.config_path.write_text(
        json.dumps(config.to_dict(), sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    config.database_path.write_bytes(b"recognized Mnemo database")
    return config


@pytest.mark.parametrize(
    ("manager", "expected"),
    [
        (
            InstallationManager.UV,
            ("tool", "uninstall", "mnemo-unified-context"),
        ),
        (
            InstallationManager.PIPX,
            ("uninstall", "mnemo-unified-context"),
        ),
    ],
)
def test_uninstall_uses_owning_manager_and_preserves_data_by_default(
    tmp_path: Path,
    manager: InstallationManager,
    expected: tuple[str, ...],
) -> None:
    log: list[object] = []
    config = _recognized_profile(tmp_path)
    environment, executable = _manager_environment(tmp_path, manager)

    def clean() -> dict[str, str]:
        log.append("clean")
        return {"codex_mcp": "removed", "claude-code_mcp": "absent"}

    def run(command: tuple[str, ...]) -> int:
        log.append(command)
        return 0

    result = PersonalUninstallService(
        config,
        integration_cleaner=clean,
        lifecycle=_Lifecycle(log),
        environment_root=environment,
        executable_resolver=lambda name: str(executable) if name == manager.value else None,
        command_runner=run,
    ).uninstall()

    assert result.manager is manager
    assert result.data_deleted is False
    assert config.data_directory.is_dir()
    assert log == ["status", "clean", (str(executable), *expected)]
    assert result.to_dict()["data"] == {
        "deleted": False,
        "preserved": True,
        "path": str(config.data_directory),
    }


def test_confirmed_data_deletion_occurs_only_after_package_removal(tmp_path: Path) -> None:
    log: list[object] = []
    config = _recognized_profile(tmp_path)
    environment, executable = _manager_environment(tmp_path, InstallationManager.UV)

    def run(command: tuple[str, ...]) -> int:
        assert config.data_directory.is_dir()
        log.append(command)
        return 0

    def remove(path: Path) -> None:
        log.append(("remove-data", path))
        assert cast(tuple[str, ...], log[-2])[1:3] == ("tool", "uninstall")
        shutil.rmtree(path)

    result = PersonalUninstallService(
        config,
        lifecycle=_Lifecycle(log),
        environment_root=environment,
        executable_resolver=lambda _: str(executable),
        command_runner=run,
        data_remover=remove,
    ).uninstall(delete_data=True)

    assert result.data_deleted is True
    assert not config.data_directory.exists()
    assert result.to_dict()["data"] == {
        "deleted": True,
        "preserved": False,
        "path": str(config.data_directory),
    }


def test_delete_data_rejects_unrecognized_target_before_any_change(tmp_path: Path) -> None:
    log: list[object] = []
    config = LocalConfig.defaults(tmp_path / "unrecognized")
    config.data_directory.mkdir()
    (config.data_directory / "user-file.txt").write_text("keep", encoding="utf-8")
    environment, executable = _manager_environment(tmp_path, InstallationManager.UV)

    with pytest.raises(PersonalUninstallError) as failure:
        PersonalUninstallService(
            config,
            integration_cleaner=lambda: pytest.fail("cleanup must not run"),
            lifecycle=_Lifecycle(log),
            environment_root=environment,
            executable_resolver=lambda _: str(executable),
            command_runner=lambda _: pytest.fail("package removal must not run"),
        ).uninstall(delete_data=True)

    assert failure.value.code == "MNEMO_UNINSTALL_DATA_TARGET_UNRECOGNIZED"
    assert (config.data_directory / "user-file.txt").read_text(encoding="utf-8") == "keep"
    assert log == []


def test_delete_data_rejects_the_current_directory_even_when_it_looks_recognized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _recognized_profile(tmp_path)
    environment, executable = _manager_environment(tmp_path, InstallationManager.UV)
    monkeypatch.chdir(config.data_directory)

    with pytest.raises(PersonalUninstallError) as failure:
        PersonalUninstallService(
            config,
            lifecycle=_Lifecycle([]),
            environment_root=environment,
            executable_resolver=lambda _: str(executable),
            command_runner=lambda _: pytest.fail("package removal must not run"),
        ).uninstall(delete_data=True)

    assert failure.value.code == "MNEMO_UNINSTALL_DATA_TARGET_UNSAFE"
    assert config.config_path.is_file()


def test_unsupported_or_missing_manager_has_no_side_effects(tmp_path: Path) -> None:
    config = _recognized_profile(tmp_path)
    log: list[object] = []
    with pytest.raises(PersonalUninstallError) as unsupported:
        PersonalUninstallService(
            config,
            integration_cleaner=lambda: pytest.fail("cleanup must not run"),
            lifecycle=_Lifecycle(log),
            environment_root=tmp_path / "unowned",
        ).uninstall()
    assert unsupported.value.code == "MNEMO_UNINSTALL_ENVIRONMENT_UNSUPPORTED"
    assert log == []

    environment, _ = _manager_environment(tmp_path, InstallationManager.UV)
    with pytest.raises(PersonalUninstallError) as unavailable:
        PersonalUninstallService(
            config,
            integration_cleaner=lambda: pytest.fail("cleanup must not run"),
            lifecycle=_Lifecycle(log),
            environment_root=environment,
            executable_resolver=lambda _: None,
        ).uninstall()
    assert unavailable.value.code == "MNEMO_UNINSTALL_MANAGER_UNAVAILABLE"
    assert log == []


@pytest.mark.parametrize("failure_stage", ["cleanup", "package"])
def test_pre_removal_failure_restores_a_previously_running_service(
    tmp_path: Path, failure_stage: str
) -> None:
    config = _recognized_profile(tmp_path)
    environment, executable = _manager_environment(tmp_path, InstallationManager.UV)
    log: list[object] = []

    def clean() -> dict[str, str]:
        log.append("clean")
        if failure_stage == "cleanup":
            raise ValueError("private client detail")
        return {"codex_mcp": "removed"}

    def run(command: tuple[str, ...]) -> int:
        log.append(command)
        if len(command) > 1 and command[1] == "tool" and failure_stage == "package":
            return 1
        return 0

    with pytest.raises(PersonalUninstallError) as failure:
        PersonalUninstallService(
            config,
            integration_cleaner=clean,
            lifecycle=_Lifecycle(log, running=True),
            environment_root=environment,
            python_executable=tmp_path / "python",
            executable_resolver=lambda _: str(executable),
            command_runner=run,
            pid_alive=lambda _: False,
        ).uninstall()

    expected_code = (
        "MNEMO_UNINSTALL_INTEGRATION_CLEANUP_FAILED"
        if failure_stage == "cleanup"
        else "MNEMO_UNINSTALL_PACKAGE_REMOVE_FAILED"
    )
    assert failure.value.code == expected_code
    assert failure.value.application_removed is False
    assert failure.value.prior_service_restored is True
    assert cast(tuple[str, ...], log[-1])[3] == "start"
    assert config.data_directory.is_dir()


def test_stop_timeout_prevents_cleanup_and_package_removal(tmp_path: Path) -> None:
    config = _recognized_profile(tmp_path)
    environment, executable = _manager_environment(tmp_path, InstallationManager.UV)
    log: list[object] = []
    ticks = iter((0.0, 6.0))

    with pytest.raises(PersonalUninstallError) as failure:
        PersonalUninstallService(
            config,
            integration_cleaner=lambda: pytest.fail("cleanup must not run"),
            lifecycle=_Lifecycle(log, running=True),
            environment_root=environment,
            executable_resolver=lambda _: str(executable),
            command_runner=lambda _: pytest.fail("package removal must not run"),
            pid_alive=lambda _: True,
            monotonic=lambda: next(ticks),
            sleep=lambda _: None,
        ).uninstall()

    assert failure.value.code == "MNEMO_UNINSTALL_STOP_TIMEOUT"
    assert log == ["status", "stop"]


def test_data_delete_failure_truthfully_reports_application_was_removed(tmp_path: Path) -> None:
    config = _recognized_profile(tmp_path)
    environment, executable = _manager_environment(tmp_path, InstallationManager.UV)

    def fail_remove(_: Path) -> None:
        raise OSError("private deletion detail")

    with pytest.raises(PersonalUninstallError) as failure:
        PersonalUninstallService(
            config,
            lifecycle=_Lifecycle([]),
            environment_root=environment,
            executable_resolver=lambda _: str(executable),
            command_runner=lambda _: 0,
            data_remover=fail_remove,
        ).uninstall(delete_data=True)

    assert failure.value.code == "MNEMO_UNINSTALL_DATA_DELETE_FAILED"
    assert failure.value.application_removed is True
    assert config.data_directory.is_dir()


def test_integration_cleanup_removes_only_owned_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = (tmp_path / "mnemo-memory").resolve()
    launcher.write_text("launcher", encoding="utf-8")
    data = (tmp_path / "data").resolve()
    hook_calls: list[str] = []
    disconnected: list[str] = []

    monkeypatch.setattr(cli, "client_home", lambda client: tmp_path / client)

    def disable_hooks(client: str, *_: object) -> bool:
        hook_calls.append(client)
        return client == "codex"

    monkeypatch.setattr(cli, "disable_client_hooks", disable_hooks)
    monkeypatch.setattr("mnemo_memory.apps.cli.main.shutil.which", lambda name: f"/tools/{name}")

    class _Codex:
        def __init__(self, *_: object) -> None:
            pass

        def inspect(self) -> dict[str, object]:
            return {"command": "foreign"}

        def is_owned(self, _: object) -> bool:
            return False

        def disconnect(self) -> None:
            pytest.fail("foreign Codex entry must be preserved")

    class _Claude:
        def __init__(self, *_: object) -> None:
            pass

        def inspect(self) -> str:
            return "owned"

        def is_owned(self, _: object) -> bool:
            return True

        def disconnect(self) -> None:
            disconnected.append("claude-code")

    monkeypatch.setattr(cli, "CodexMcpManager", _Codex)
    monkeypatch.setattr(cli, "ClaudeMcpManager", _Claude)

    result = cli._cleanup_owned_integrations(launcher, data)

    assert hook_calls == ["codex", "claude-code"]
    assert disconnected == ["claude-code"]
    assert result == {
        "codex_hooks": "removed",
        "claude-code_hooks": "absent",
        "codex_mcp": "preserved_unrecognized",
        "claude-code_mcp": "removed",
    }


def test_default_runner_discards_package_manager_output(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}

    def run(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout=b"private", stderr=b"secret")

    monkeypatch.setattr(subprocess, "run", run)
    assert uninstall_module._run_silent_command(("uv", "tool", "uninstall", "package")) == 0
    assert observed["stdin"] is subprocess.DEVNULL
    assert observed["stdout"] is subprocess.DEVNULL
    assert observed["stderr"] is subprocess.DEVNULL
    assert observed["timeout"] == 600


def test_uninstall_cli_requires_explicit_data_deletion_confirmation(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["uninstall", "--delete-data", "--data-dir", str(tmp_path / "profile")],
    )

    assert result.exit_code == 2
    assert "MNEMO_UNINSTALL_DATA_DELETE_REQUIRES_YES" in result.output


def test_uninstall_cli_reports_bounded_unsupported_environment(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["uninstall", "--yes", "--data-dir", str(tmp_path / "profile")],
    )

    assert result.exit_code == 1
    assert json.loads(result.output) == {
        "application_removed": False,
        "code": "MNEMO_UNINSTALL_ENVIRONMENT_UNSUPPORTED",
        "manager": None,
        "prior_service_restored": False,
        "status": "failed",
    }
    assert str(Path.home()) not in result.output
