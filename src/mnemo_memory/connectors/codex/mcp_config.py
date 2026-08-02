"""Safe, CLI-mediated ownership of Mnemo's one Codex MCP registration."""

from __future__ import annotations

import json
import shutil
import subprocess
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

SERVER_NAME = "mnemo-memory"


@dataclass(frozen=True)
class CodexMcpManager:
    codex_executable: str
    mnemo_executable: Path
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run
    environment: dict[str, str] | None = None

    @classmethod
    def discover(cls, mnemo_executable: Path) -> CodexMcpManager:
        codex = shutil.which("codex")
        if codex is None:
            raise ValueError("MNEMO_CODEX_NOT_INSTALLED")
        if not mnemo_executable.is_absolute():
            raise ValueError("MNEMO_LAUNCHER_NOT_ABSOLUTE")
        return cls(codex, mnemo_executable)

    @property
    def command(self) -> list[str]:
        return [str(self.mnemo_executable), "mcp", "serve", "--stdio"]

    def inspect(self) -> dict[str, object] | None:
        result = self.run(
            [self.codex_executable, "mcp", "get", SERVER_NAME, "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env=self.environment,
        )
        if result.returncode != 0:
            return None
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            raise ValueError("MNEMO_CODEX_INVALID_ENTRY")
        return value

    def is_owned(self, entry: dict[str, object]) -> bool:
        transport = entry.get("transport")
        if isinstance(transport, dict):
            entry = transport
        return (
            _same_launcher(entry.get("command"), self.mnemo_executable)
            and entry.get("args") == self.command[1:]
        )

    def connect(self, dry_run: bool = False) -> dict[str, object]:
        existing = self.inspect()
        if existing is not None:
            if self.is_owned(existing):
                return {"status": "connected", "changed": False, "server": SERVER_NAME}
            raise ValueError("MNEMO_CODEX_CONFLICT")
        if dry_run:
            return {
                "status": "dry-run",
                "changed": False,
                "server": SERVER_NAME,
                "command": self.command,
            }
        result = self.run(
            [self.codex_executable, "mcp", "add", SERVER_NAME, "--", *self.command],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            env=self.environment,
        )
        if result.returncode != 0:
            raise ValueError("MNEMO_CODEX_REGISTRATION_FAILED")
        stored = self.inspect()
        if stored is None or not self.is_owned(stored):
            raise ValueError(
                "MNEMO_CODEX_READBACK_FAILED: run mnemo disconnect codex after inspection"
            )
        return {"status": "connected", "changed": True, "server": SERVER_NAME}

    def disconnect(self, dry_run: bool = False) -> dict[str, object]:
        existing = self.inspect()
        if existing is None:
            return {"status": "disconnected", "changed": False, "server": SERVER_NAME}
        if not self.is_owned(existing):
            raise ValueError("MNEMO_CODEX_UNRECOGNIZED_ENTRY")
        if dry_run:
            return {"status": "dry-run", "changed": False, "server": SERVER_NAME}
        result = self.run(
            [self.codex_executable, "mcp", "remove", SERVER_NAME],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            env=self.environment,
        )
        if result.returncode != 0:
            raise ValueError("MNEMO_CODEX_DISCONNECT_FAILED")
        return {"status": "disconnected", "changed": True, "server": SERVER_NAME}


def _same_launcher(value: object, expected: Path) -> bool:
    if not isinstance(value, str):
        return False
    # macOS can round-trip user-created Unicode paths in a different normalization form.
    return unicodedata.normalize("NFC", str(Path(value).resolve())) == unicodedata.normalize(
        "NFC", str(expected.resolve())
    )
