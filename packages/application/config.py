"""Strict local personal-profile configuration."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

CONFIG_FIELDS = {
    "data_directory",
    "database_path",
    "host",
    "port",
    "log_level",
    "state_path",
    "profile",
}
ENV_PREFIX = "MNEMO_"


def _safe_path(value: str, base: Path) -> Path:
    path = Path(value)
    resolved = (base / path).resolve() if not path.is_absolute() else path.resolve()
    if base.resolve() not in resolved.parents and resolved != base.resolve():
        raise ValueError("configured path escapes the Mnemo data directory")
    return resolved


def _loopback(host: str) -> None:
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("local personal profile may bind only to loopback")


@dataclass(frozen=True, slots=True)
class LocalConfig:
    data_directory: Path
    database_path: Path
    host: str = "127.0.0.1"
    port: int = 8765
    log_level: str = "INFO"
    state_path: Path | None = None
    profile: str = "personal"

    def __post_init__(self) -> None:
        _loopback(self.host)
        if not isinstance(self.port, int) or not 1 <= self.port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        if self.profile != "personal":
            raise ValueError("Issue 6 supports only the personal profile")
        base = self.data_directory.resolve()
        object.__setattr__(self, "data_directory", base)
        object.__setattr__(self, "database_path", _safe_path(str(self.database_path), base))
        state = self.state_path or base / "process-state.json"
        object.__setattr__(self, "state_path", _safe_path(str(state), base))

    @property
    def config_path(self) -> Path:
        return self.data_directory / "config.json"

    def to_dict(self) -> dict[str, str | int]:
        return {
            "data_directory": str(self.data_directory),
            "database_path": str(self.database_path),
            "host": self.host,
            "port": self.port,
            "log_level": self.log_level,
            "state_path": str(self.state_path),
            "profile": self.profile,
        }

    @classmethod
    def defaults(cls, data_directory: Path) -> LocalConfig:
        base = data_directory.resolve()
        return cls(base, base / "mnemo.sqlite3")

    @classmethod
    def load(cls, config_path: Path) -> LocalConfig:
        data = json.loads(config_path.read_text())
        if not isinstance(data, dict) or set(data) != CONFIG_FIELDS:
            raise ValueError("local configuration has unknown or missing fields")
        return cls(
            data_directory=Path(str(data["data_directory"])),
            database_path=Path(str(data["database_path"])),
            host=str(data["host"]),
            port=int(data["port"]),
            log_level=str(data["log_level"]),
            state_path=Path(str(data["state_path"])),
            profile=str(data["profile"]),
        )

    @classmethod
    def from_environment(cls, default_directory: Path) -> LocalConfig:
        directory = Path(os.environ.get(f"{ENV_PREFIX}DATA_DIR", default_directory))
        return cls.defaults(directory)
