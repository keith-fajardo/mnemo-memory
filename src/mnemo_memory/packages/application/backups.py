"""Verified, non-overwriting SQLite backups for the local personal profile."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote
from uuid import UUID, uuid4

from .config import LocalConfig


class PersonalBackupError(RuntimeError):
    """Safe failure while creating or validating a personal backup."""


@dataclass(frozen=True, slots=True)
class PersonalBackupResult:
    backup_path: Path
    schema_version: int
    created_at: datetime
    content_digest: str
    size_bytes: int
    reused: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "backup_path": str(self.backup_path),
            "schema_version": self.schema_version,
            "created_at": self.created_at.isoformat(),
            "content_digest": self.content_digest,
            "size_bytes": self.size_bytes,
            "reused": self.reused,
        }


class PersonalBackupService:
    """Create one coherent, integrity-checked copy without mutating the live store."""

    def __init__(
        self,
        config: LocalConfig,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        copy_database: Callable[[Path, Path], None] | None = None,
        temporary_id: Callable[[], UUID] = uuid4,
    ) -> None:
        self._config = config
        self._clock = clock
        self._copy_database = copy_database or _copy_sqlite_database
        self._temporary_id = temporary_id

    def create(self) -> PersonalBackupResult:
        source = self._config.database_path
        created_at = self._clock()
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise PersonalBackupError("backup clock must be timezone-aware")
        created_at = created_at.astimezone(UTC)
        if not source.exists() or not source.is_file() or source.is_symlink():
            raise PersonalBackupError("initialized personal database is unavailable")
        directory = self._config.data_directory / "backups"
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            raise PersonalBackupError("backup directory is unsafe")
        try:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(directory, 0o700)
        except OSError as error:
            raise PersonalBackupError("backup directory is unavailable") from error
        temporary = directory / f".mnemo-backup-{self._temporary_id()}.tmp"
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            self._copy_database(source, temporary)
            schema_version = _validate_sqlite_backup(temporary)
            content_digest = _file_digest(temporary)
            timestamp = created_at.strftime("%Y%m%dT%H%M%S%fZ")
            filename = (
                f"mnemo-v{schema_version}-{timestamp}-{content_digest.removeprefix('sha256:')}"
                ".sqlite3"
            )
            destination = directory / filename
            if destination.exists() or destination.is_symlink():
                if (
                    not destination.is_symlink()
                    and destination.is_file()
                    and _file_digest(destination) == content_digest
                    and _validate_sqlite_backup(destination) == schema_version
                ):
                    _remove_temporary_database(temporary)
                    return PersonalBackupResult(
                        destination,
                        schema_version,
                        created_at,
                        content_digest,
                        destination.stat().st_size,
                        True,
                    )
                raise PersonalBackupError("backup destination already exists")
            if any(path.exists() for path in _sqlite_sidecars(temporary)):
                raise PersonalBackupError("backup candidate retained a SQLite sidecar")
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
            os.chmod(destination, 0o600)
            _fsync_directory(directory)
            return PersonalBackupResult(
                destination,
                schema_version,
                created_at,
                content_digest,
                destination.stat().st_size,
                False,
            )
        except PersonalBackupError:
            _remove_temporary_database(temporary)
            raise
        except (OSError, sqlite3.Error, ValueError) as error:
            _remove_temporary_database(temporary)
            raise PersonalBackupError("personal database backup failed") from error


def _copy_sqlite_database(source: Path, destination: Path) -> None:
    with sqlite3.connect(_read_only_uri(source), uri=True) as source_connection:
        source_connection.execute("PRAGMA query_only = ON")
        with sqlite3.connect(destination) as destination_connection:
            source_connection.backup(destination_connection)
            destination_connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            destination_connection.execute("PRAGMA journal_mode = DELETE")


def _validate_sqlite_backup(path: Path) -> int:
    with sqlite3.connect(_read_only_uri(path), uri=True) as connection:
        integrity = tuple(str(row[0]) for row in connection.execute("PRAGMA integrity_check"))
        if integrity != ("ok",):
            raise PersonalBackupError("backup integrity validation failed")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise PersonalBackupError("backup foreign-key validation failed")
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
        ).fetchone()
        if table is None:
            raise PersonalBackupError("backup has no schema history")
        row = connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
        ).fetchone()
        if row is None or isinstance(row[0], bool) or not isinstance(row[0], int) or row[0] < 1:
            raise PersonalBackupError("backup schema version is invalid")
        return int(row[0])


def _read_only_uri(path: Path) -> str:
    return f"file:{quote(str(path), safe='/:')}?mode=ro"


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sqlite_sidecars(path: Path) -> tuple[Path, ...]:
    return tuple(Path(f"{path}{suffix}") for suffix in ("-journal", "-shm", "-wal"))


def _remove_temporary_database(path: Path) -> None:
    path.unlink(missing_ok=True)
    for sidecar in _sqlite_sidecars(path):
        sidecar.unlink(missing_ok=True)
