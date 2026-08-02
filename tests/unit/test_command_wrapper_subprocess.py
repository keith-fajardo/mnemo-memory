from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mnemo_memory.connectors.command_wrapper.subprocess_adapter import (
    LocalExecutableResolver,
    SubprocessExecutor,
)
from mnemo_memory.packages.application.command_wrapper import (
    CommandContext,
    CommandFailure,
    CommandFailureCode,
)

NOW = datetime(2026, 8, 3, tzinfo=UTC)


def make_executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return path


def test_resolver_supports_path_absolute_symlink_spaces_and_unicode(tmp_path: Path) -> None:
    real = make_executable(tmp_path / "tool Δ space")
    link = tmp_path / "linked tool"
    link.symlink_to(real)
    resolver = LocalExecutableResolver(which=lambda _: str(link))

    assert resolver.resolve("tool") == real.resolve()
    assert resolver.resolve(real) == real.resolve()
    assert resolver.resolve(link) == real.resolve()


def test_resolver_rejects_missing_directory_and_non_executable(tmp_path: Path) -> None:
    resolver = LocalExecutableResolver(which=lambda _: None)
    with pytest.raises(CommandFailure) as missing:
        resolver.resolve("not-present")
    assert missing.value.code is CommandFailureCode.COMMAND_NOT_FOUND
    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(CommandFailure) as not_executable:
        resolver.resolve(directory)
    assert not_executable.value.code is CommandFailureCode.COMMAND_NOT_EXECUTABLE
    file = tmp_path / "not executable"
    file.write_text("x")
    file.chmod(0o644)
    with pytest.raises(CommandFailure) as permission:
        resolver.resolve(file)
    assert permission.value.code is CommandFailureCode.COMMAND_NOT_EXECUTABLE


def test_resolver_prevents_direct_and_symlinked_wrapper_recursion(tmp_path: Path) -> None:
    wrapper = make_executable(tmp_path / "mnemo-memory")
    alias = tmp_path / "mnemo alias"
    alias.symlink_to(wrapper)
    resolver = LocalExecutableResolver()
    for candidate in (wrapper, alias):
        with pytest.raises(CommandFailure) as recursion:
            resolver.resolve(candidate, wrapper)
        assert recursion.value.code is CommandFailureCode.COMMAND_RECURSION


class InterruptingProcess:
    def __init__(self) -> None:
        self.actions: list[str] = []
        self.running = True

    def wait(self, timeout: float | None = None) -> int:
        self.actions.append(f"wait:{timeout}")
        if timeout is None:
            raise KeyboardInterrupt
        self.running = False
        return 143

    def poll(self) -> int | None:
        return None if self.running else 143

    def terminate(self) -> None:
        self.actions.append("terminate")

    def kill(self) -> None:
        self.actions.append("kill")


class StubbornProcess(InterruptingProcess):
    def wait(self, timeout: float | None = None) -> int:
        self.actions.append(f"wait:{timeout}")
        if timeout is None:
            raise KeyboardInterrupt
        if self.actions.count("kill") == 0:
            raise TimeoutError
        self.running = False
        return 137


def context(tmp_path: Path) -> CommandContext:
    return CommandContext(Path(sys.executable).resolve(), (), tmp_path, "dbt", "test-id", NOW)


def test_subprocess_adapter_uses_argv_cwd_shell_false_and_reaps_interrupt(tmp_path: Path) -> None:
    process = InterruptingProcess()
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def popen(*args: object, **kwargs: object) -> InterruptingProcess:
        calls.append((args, kwargs))
        return process

    outcome = SubprocessExecutor(lambda: NOW, popen, graceful_shutdown_seconds=0.1).execute(
        context(tmp_path)
    )
    assert outcome.interrupted is True
    assert outcome.exit_code == 130
    assert process.actions == ["wait:None", "terminate", "wait:0.1"]
    assert calls[0][0][0] == [str(Path(sys.executable).resolve())]
    assert calls[0][1] == {"cwd": str(tmp_path), "shell": False}


def test_subprocess_adapter_kills_after_bounded_graceful_timeout(tmp_path: Path) -> None:
    process = StubbornProcess()
    outcome = SubprocessExecutor(lambda: NOW, lambda *_args, **_kwargs: process, 0.1).execute(
        context(tmp_path)
    )
    assert outcome.interrupted is True
    assert process.actions == ["wait:None", "terminate", "wait:0.1", "kill", "wait:0.1"]


def test_real_subprocess_preserves_argv_working_directory_output_exit_and_no_shell(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    script = tmp_path / "emit args.py"
    script.write_text(
        "import os, pathlib, sys\n"
        "print('ARGS=' + repr(sys.argv[1:]))\n"
        "print('CWD=' + pathlib.Path.cwd().name)\n"
        "pathlib.Path('observed.txt').write_text('ok')\n"
        "raise SystemExit(19)\n"
    )
    working = tmp_path / "working Δ directory"
    working.mkdir()
    command = CommandContext(
        Path(sys.executable).resolve(),
        (str(script), "two words", "; echo never-runs"),
        working,
        "dbt",
        "integration-test",
        NOW,
    )
    result = SubprocessExecutor(lambda: NOW).execute(command)
    captured = capfd.readouterr()
    assert result.exit_code == 19
    assert result.started is True
    assert "ARGS=['two words', '; echo never-runs']" in captured.out
    assert "CWD=working Δ directory" in captured.out
    assert "never-runs\n" not in captured.out
    assert (working / "observed.txt").read_text() == "ok"
    assert os.environ.get("MNEMO_COMMAND_WRAPPER_TEST") is None
