"""Private, content-free personal diagnostic bundles."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import sqlite3
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote
from uuid import UUID, uuid4

from .config import LocalConfig
from .services import APP_VERSION
from .settings import PersonalSettingsError, PersonalSettingsStore

_CLIENT_STATUSES = frozenset({"available", "connected", "not_installed", "unavailable"})
_SAFE_RUNTIME_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._ -]{0,63}\Z")


class PersonalDiagnosticError(RuntimeError):
    """Safe diagnostic-bundle failure without local payload or path details."""


@dataclass(frozen=True, slots=True)
class DiagnosticClientStatus:
    available: bool
    connected: bool
    status: str

    def __post_init__(self) -> None:
        if not isinstance(self.available, bool) or not isinstance(self.connected, bool):
            raise TypeError("diagnostic client flags must be booleans")
        if self.status not in _CLIENT_STATUSES:
            raise ValueError("diagnostic client status is invalid")
        if self.status == "connected" and (not self.available or not self.connected):
            raise ValueError("connected diagnostic client status is inconsistent")
        if self.status == "available" and (not self.available or self.connected):
            raise ValueError("available diagnostic client status is inconsistent")
        if self.status == "not_installed" and (self.available or self.connected):
            raise ValueError("missing diagnostic client status is inconsistent")
        if self.status == "unavailable" and self.connected:
            raise ValueError("unavailable diagnostic client status is inconsistent")

    def to_dict(self) -> dict[str, object]:
        return {
            "available": self.available,
            "connected": self.connected,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class PersonalDiagnosticContext:
    codex: DiagnosticClientStatus
    claude_code: DiagnosticClientStatus
    project_registered: bool | None

    def __post_init__(self) -> None:
        if self.project_registered is not None and not isinstance(self.project_registered, bool):
            raise TypeError("diagnostic project status must be a boolean or null")

    @classmethod
    def unavailable(cls) -> PersonalDiagnosticContext:
        unavailable = DiagnosticClientStatus(False, False, "unavailable")
        return cls(unavailable, unavailable, None)

    def to_dict(self) -> dict[str, object]:
        return {
            "clients": {
                "claude_code": self.claude_code.to_dict(),
                "codex": self.codex.to_dict(),
            },
            "project": {"registered": self.project_registered},
        }


@dataclass(frozen=True, slots=True)
class PersonalDiagnosticResult:
    bundle_path: Path
    created_at: datetime
    manifest_digest: str
    archive_digest: str
    size_bytes: int
    reused: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "status": "created",
            "bundle_path": str(self.bundle_path),
            "created_at": self.created_at.isoformat(),
            "manifest_digest": self.manifest_digest,
            "archive_digest": self.archive_digest,
            "size_bytes": self.size_bytes,
            "reused": self.reused,
        }


class PersonalDiagnosticService:
    """Publish one verifiable archive containing only a closed safe manifest."""

    def __init__(
        self,
        config: LocalConfig,
        *,
        context: PersonalDiagnosticContext | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        temporary_id: Callable[[], UUID] = uuid4,
        runtime_metadata: Callable[[], dict[str, str]] | None = None,
    ) -> None:
        self._config = config
        self._context = context or PersonalDiagnosticContext.unavailable()
        self._clock = clock
        self._temporary_id = temporary_id
        self._runtime_metadata = runtime_metadata or _runtime_metadata

    def create(self) -> PersonalDiagnosticResult:
        created_at = self._clock()
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise PersonalDiagnosticError("diagnostic clock must be timezone-aware")
        created_at = created_at.astimezone(UTC)
        manifest, manifest_digest = self._manifest(created_at)
        manifest_bytes = _canonical_json(manifest)
        directory = self._config.data_directory / "diagnostics"
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            raise PersonalDiagnosticError("diagnostic directory is unsafe")
        try:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(directory, 0o700)
        except OSError as error:
            raise PersonalDiagnosticError("diagnostic directory is unavailable") from error
        temporary = directory / f".mnemo-diagnostics-{self._temporary_id()}.tmp"
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            _write_archive(temporary, manifest_bytes)
            _verify_archive(temporary, manifest_bytes, manifest_digest)
            archive_digest = _file_digest(temporary)
            timestamp = created_at.strftime("%Y%m%dT%H%M%S%fZ")
            destination = directory / (
                f"mnemo-diagnostics-{timestamp}-{archive_digest.removeprefix('sha256:')}.zip"
            )
            if destination.exists() or destination.is_symlink():
                if (
                    not destination.is_symlink()
                    and destination.is_file()
                    and _file_digest(destination) == archive_digest
                ):
                    _verify_archive(destination, manifest_bytes, manifest_digest)
                    temporary.unlink(missing_ok=True)
                    return PersonalDiagnosticResult(
                        destination,
                        created_at,
                        manifest_digest,
                        archive_digest,
                        destination.stat().st_size,
                        True,
                    )
                raise PersonalDiagnosticError("diagnostic destination already exists")
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            try:
                os.link(temporary, destination)
            except FileExistsError as error:
                raise PersonalDiagnosticError("diagnostic destination already exists") from error
            temporary.unlink()
            os.chmod(destination, 0o600)
            _fsync_directory(directory)
            return PersonalDiagnosticResult(
                destination,
                created_at,
                manifest_digest,
                archive_digest,
                destination.stat().st_size,
                False,
            )
        except PersonalDiagnosticError:
            temporary.unlink(missing_ok=True)
            raise
        except (OSError, ValueError, TypeError, zipfile.BadZipFile) as error:
            temporary.unlink(missing_ok=True)
            raise PersonalDiagnosticError("diagnostic bundle creation failed") from error

    def _manifest(self, created_at: datetime) -> tuple[dict[str, object], str]:
        runtime = self._runtime_metadata()
        if set(runtime) != {"python", "system", "machine"} or not all(
            isinstance(value, str) and _SAFE_RUNTIME_VALUE.fullmatch(value) is not None
            for value in runtime.values()
        ):
            raise PersonalDiagnosticError("diagnostic runtime metadata is invalid")
        storage = _storage_health(self._config.database_path)
        lifecycle = _lifecycle_health(self._config, storage.get("schema_version"))
        payload: dict[str, object] = {
            "format": "mnemo.personal-diagnostics.v1",
            "created_at": created_at.isoformat(),
            "runtime": {"mnemo": APP_VERSION, **runtime},
            "lifecycle": lifecycle,
            "storage": storage,
            "settings": {"status": _settings_status(self._config.data_directory)},
            **self._context.to_dict(),
            "privacy": {
                "content_included": False,
                "credentials_included": False,
                "environment_included": False,
                "identifiers_included": False,
                "logs_included": False,
                "paths_included": False,
                "subprocess_output_included": False,
            },
        }
        digest = "sha256:" + hashlib.sha256(_canonical_json(payload)).hexdigest()
        return {**payload, "manifest_digest": digest}, digest


def _runtime_metadata() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "system": platform.system() or "unknown",
        "machine": platform.machine() or "unknown",
    }


def _lifecycle_health(config: LocalConfig, schema_version: object) -> dict[str, object]:
    initialized = config.config_path.is_file() and not config.config_path.is_symlink()
    running: bool | None = None
    state_path = config.state_path
    try:
        if (
            state_path is not None
            and state_path.is_file()
            and not state_path.is_symlink()
            and state_path.stat().st_size <= 4_096
        ):
            value = json.loads(state_path.read_text(encoding="utf-8"))
            pid = value.get("pid") if isinstance(value, dict) else None
            running = (
                _pid_exists(pid) if isinstance(pid, int) and not isinstance(pid, bool) else None
            )
        elif state_path is not None and not state_path.exists():
            running = False
    except (OSError, ValueError, json.JSONDecodeError):
        running = None
    return {
        "initialized": initialized,
        "running": running,
        "schema_version": (
            schema_version
            if isinstance(schema_version, int) and not isinstance(schema_version, bool)
            else None
        ),
    }


def _storage_health(path: Path) -> dict[str, object]:
    if not path.exists():
        return {
            "status": "not_initialized",
            "integrity": None,
            "foreign_keys": None,
            "schema_version": None,
        }
    if not path.is_file() or path.is_symlink():
        return {
            "status": "unavailable",
            "integrity": None,
            "foreign_keys": None,
            "schema_version": None,
        }
    try:
        with sqlite3.connect(_read_only_uri(path), uri=True) as connection:
            connection.execute("PRAGMA query_only = ON")
            integrity = tuple(str(row[0]) for row in connection.execute("PRAGMA integrity_check"))
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchone() is None
            has_schema = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
            ).fetchone()
            schema_row = (
                connection.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
                ).fetchone()
                if has_schema is not None
                else None
            )
            schema_version = (
                schema_row[0]
                if schema_row is not None
                and isinstance(schema_row[0], int)
                and not isinstance(schema_row[0], bool)
                and schema_row[0] > 0
                else None
            )
        healthy = integrity == ("ok",) and foreign_keys
        return {
            "status": "healthy" if healthy else "unhealthy",
            "integrity": integrity == ("ok",),
            "foreign_keys": foreign_keys,
            "schema_version": schema_version,
        }
    except sqlite3.Error:
        return {
            "status": "unavailable",
            "integrity": None,
            "foreign_keys": None,
            "schema_version": None,
        }


def _settings_status(data_directory: Path) -> str:
    try:
        PersonalSettingsStore(data_directory).load()
    except (OSError, ValueError, PersonalSettingsError):
        return "unavailable"
    return "available"


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def _write_archive(path: Path, manifest: bytes) -> None:
    info = zipfile.ZipInfo("manifest.json", date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(info, manifest)


def _verify_archive(path: Path, expected_manifest: bytes, expected_digest: str) -> None:
    with zipfile.ZipFile(path, "r") as archive:
        if archive.namelist() != ["manifest.json"]:
            raise PersonalDiagnosticError("diagnostic archive layout is invalid")
        manifest = archive.read("manifest.json")
    if manifest != expected_manifest:
        raise PersonalDiagnosticError("diagnostic manifest content is invalid")
    try:
        value = json.loads(manifest)
        included_digest = value.pop("manifest_digest")
    except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise PersonalDiagnosticError("diagnostic manifest is invalid") from error
    actual = "sha256:" + hashlib.sha256(_canonical_json(value)).hexdigest()
    if included_digest != expected_digest or actual != expected_digest:
        raise PersonalDiagnosticError("diagnostic manifest digest is invalid")


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _read_only_uri(path: Path) -> str:
    return f"file:{quote(str(path), safe='/:')}?mode=ro"


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
