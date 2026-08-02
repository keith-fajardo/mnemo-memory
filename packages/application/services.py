"""Framework-independent local lifecycle use cases."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from packages.application.config import LocalConfig

APP_VERSION = "0.1.0"


class LifecycleService:
    def __init__(
        self, config: LocalConfig, migrate: Callable[[], None], schema_version: Callable[[], int]
    ) -> None:
        self.config = config
        self._migrate = migrate
        self._schema_version = schema_version

    def initialize(self) -> dict[str, object]:
        self.config.data_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        created = not self.config.config_path.exists()
        if created:
            self._write_json(self.config.config_path, self.config.to_dict())
        self._migrate()
        return {
            "profile": self.config.profile,
            "data_directory": str(self.config.data_directory),
            "database_path": str(self.config.database_path),
            "initialized": created,
            "schema_version": self._schema_version(),
        }

    def status(self) -> dict[str, object]:
        state = self._read_state()
        running = state is not None and _pid_exists(cast(int, state["pid"]))
        return {
            "profile": self.config.profile,
            "initialized": self.config.config_path.exists(),
            "schema_version": self._schema_version(),
            "running": running,
            "process": state if running else None,
            "version": APP_VERSION,
        }

    def start(self) -> dict[str, object]:
        self.initialize()
        current = self.status()
        if current["running"]:
            return current
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "apps.api.server",
                "--data-dir",
                str(self.config.data_directory),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        state = {
            "pid": process.pid,
            "started_at": datetime.now(UTC).isoformat(),
            "host": self.config.host,
            "port": self.config.port,
        }
        self._write_json(self._state_path(), state)
        return {**self.status(), "started": True}

    def stop(self) -> dict[str, object]:
        state = self._read_state()
        if state is None or not _pid_exists(cast(int, state["pid"])):
            self._state_path().unlink(missing_ok=True)
            return {"running": False, "stopped": False}
        os.kill(cast(int, state["pid"]), signal.SIGTERM)
        self._state_path().unlink(missing_ok=True)
        return {"running": False, "stopped": True}

    def _read_state(self) -> dict[str, int | str] | None:
        state_path = self._state_path()
        if not state_path.exists():
            return None
        value = json.loads(state_path.read_text())
        if not isinstance(value, dict) or {"pid", "started_at", "host", "port"} != set(value):
            return None
        if not isinstance(value["pid"], int):
            return None
        return cast(dict[str, int | str], value)

    def _state_path(self) -> Path:
        assert self.config.state_path is not None
        return self.config.state_path

    @staticmethod
    def _write_json(path: Path, value: dict[str, object] | dict[str, str | int]) -> None:
        path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")))
        with suppress(OSError):
            os.chmod(path, 0o600)


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
