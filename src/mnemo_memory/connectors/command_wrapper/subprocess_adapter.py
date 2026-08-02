"""Local subprocess implementation for the generic command-wrapper contract."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from mnemo_memory.packages.application.command_wrapper import (
    Clock,
    CommandContext,
    CommandFailure,
    CommandFailureCode,
    CommandResult,
)


class ChildProcess(Protocol):
    def wait(self, timeout: float | None = None) -> int: ...

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


PopenFactory = Callable[..., ChildProcess]


def utc_now() -> datetime:
    return datetime.now(UTC)


class LocalExecutableResolver:
    """Resolve a real executable without executing a shell or following wrapper recursion."""

    def __init__(self, which: Callable[[str], str | None] = shutil.which) -> None:
        self._which = which

    def resolve(self, executable: str | Path, wrapper_executable: Path | None = None) -> Path:
        raw = str(executable)
        candidate = Path(raw)
        if candidate.is_absolute():
            found = candidate
        else:
            resolved_from_path = self._which(raw)
            if resolved_from_path is None:
                raise CommandFailure(CommandFailureCode.COMMAND_NOT_FOUND)
            found = Path(resolved_from_path)
        if not found.exists():
            raise CommandFailure(CommandFailureCode.COMMAND_NOT_FOUND)
        if not found.is_file() or not os.access(found, os.X_OK):
            raise CommandFailure(CommandFailureCode.COMMAND_NOT_EXECUTABLE)
        resolved = found.resolve(strict=True)
        if wrapper_executable is not None and resolved == wrapper_executable.resolve(strict=True):
            raise CommandFailure(CommandFailureCode.COMMAND_RECURSION)
        return resolved


class SubprocessExecutor:
    """Runs a validated command using inherited stdio and bounded interruption cleanup."""

    def __init__(
        self,
        clock: Clock = utc_now,
        popen: PopenFactory = subprocess.Popen,
        graceful_shutdown_seconds: float = 5.0,
    ) -> None:
        if graceful_shutdown_seconds <= 0:
            raise ValueError("MNEMO_COMMAND_INVALID_GRACE_PERIOD")
        self._clock = clock
        self._popen = popen
        self._graceful_shutdown_seconds = graceful_shutdown_seconds

    def execute(self, context: CommandContext) -> CommandResult:
        process: ChildProcess | None = None
        try:
            # No stdio/environment parameters: child streams and environment are inherited.
            process = self._popen(
                [str(context.executable), *context.arguments],
                cwd=str(context.working_directory),
                shell=False,
            )
            exit_code = process.wait()
            return CommandResult(True, exit_code, False, context.started_at, self._clock())
        except KeyboardInterrupt:
            if process is not None:
                self._reap_interrupted_process(process)
            return CommandResult(True, 130, True, context.started_at, self._clock())
        except (OSError, subprocess.SubprocessError) as error:
            if process is not None and process.poll() is None:
                self._terminate_and_reap(process)
            raise CommandFailure(CommandFailureCode.COMMAND_LAUNCH_FAILED) from error

    def _reap_interrupted_process(self, process: ChildProcess) -> None:
        if process.poll() is not None:
            return
        self._terminate_and_reap(process)

    def _terminate_and_reap(self, process: ChildProcess) -> None:
        try:
            process.terminate()
            process.wait(timeout=self._graceful_shutdown_seconds)
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
        try:
            process.kill()
            process.wait(timeout=self._graceful_shutdown_seconds)
        except (OSError, subprocess.TimeoutExpired):
            # The caller receives a sanitized result/error; no exception text is surfaced.
            return


__all__ = [
    "ChildProcess",
    "LocalExecutableResolver",
    "PopenFactory",
    "SubprocessExecutor",
    "utc_now",
]
