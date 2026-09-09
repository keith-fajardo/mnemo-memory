"""CLI-mediated Claude Code user-scope MCP registration."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

SERVER_NAME = "mnemo-memory"


@dataclass(frozen=True)
class ClaudeMcpManager:
    executable: str
    mnemo_executable: Path
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run
    environment: dict[str, str] | None = None
    tool_profile: Literal["full", "compact"] = "full"

    @classmethod
    def discover(cls, launcher: Path) -> ClaudeMcpManager:
        if not launcher.is_absolute():
            raise ValueError("MNEMO_LAUNCHER_NOT_ABSOLUTE")
        executable = shutil.which("claude")
        if executable is None:
            raise ValueError("MNEMO_CLAUDE_NOT_INSTALLED")
        return cls(executable, launcher)

    @property
    def command(self) -> list[str]:
        command = [str(self.mnemo_executable), "mcp", "serve", "--stdio"]
        if self.tool_profile == "compact":
            command.extend(("--profile", "compact"))
        return command

    def inspect(self) -> str | None:
        result = self.run(
            [self.executable, "mcp", "get", SERVER_NAME],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env=self.environment,
        )
        return result.stdout if result.returncode == 0 else None

    def is_owned(self, detail: str) -> bool:
        compact = "mcp serve --stdio --profile compact"
        legacy_full = "mcp serve --stdio"
        return str(self.mnemo_executable) in detail and (
            compact in detail or (legacy_full in detail and "--profile" not in detail)
        )

    def has_selected_profile(self, detail: str) -> bool:
        if not self.is_owned(detail):
            return False
        compact = "--profile compact" in detail
        return compact if self.tool_profile == "compact" else not compact

    def connect(self, dry_run: bool = False) -> dict[str, object]:
        existing = self.inspect()
        if existing is not None:
            if self.is_owned(existing):
                if not self.has_selected_profile(existing):
                    raise ValueError("MNEMO_CLAUDE_PROFILE_MISMATCH")
                return {"status": "connected", "changed": False, "server": SERVER_NAME}
            raise ValueError("MNEMO_CLAUDE_CONFLICT")
        if dry_run:
            return {"status": "dry-run", "changed": False, "command": self.command}
        result = self.run(
            [self.executable, "mcp", "add", "--scope", "user", SERVER_NAME, "--", *self.command],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            env=self.environment,
        )
        if result.returncode != 0:
            raise ValueError("MNEMO_CLAUDE_REGISTRATION_FAILED")
        detail = self.inspect()
        if detail is None or not self.is_owned(detail):
            raise ValueError("MNEMO_CLAUDE_READBACK_FAILED")
        return {"status": "connected", "changed": True, "server": SERVER_NAME}

    def disconnect(self, dry_run: bool = False) -> dict[str, object]:
        detail = self.inspect()
        if detail is None:
            return {"status": "disconnected", "changed": False, "server": SERVER_NAME}
        if not self.is_owned(detail):
            raise ValueError("MNEMO_CLAUDE_UNRECOGNIZED_ENTRY")
        if dry_run:
            return {"status": "dry-run", "changed": False, "server": SERVER_NAME}
        result = self.run(
            [self.executable, "mcp", "remove", "--scope", "user", SERVER_NAME],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            env=self.environment,
        )
        if result.returncode != 0:
            raise ValueError("MNEMO_CLAUDE_DISCONNECT_FAILED")
        return {"status": "disconnected", "changed": True, "server": SERVER_NAME}
