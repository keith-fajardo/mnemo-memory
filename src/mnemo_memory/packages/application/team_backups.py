"""Verified, non-overwriting PostgreSQL team backup and restore-drill workflow."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Protocol
from uuid import UUID, uuid4

from mnemo_memory.packages.domain import (
    TEAM_BACKUP_FORMAT_V1,
    TeamBackupManifest,
    TeamBackupTableCount,
)


class TeamBackupError(RuntimeError):
    """Stable, content-free team backup or restore outcome."""


@dataclass(frozen=True, slots=True)
class TeamDatabaseInventory:
    schema_version: int
    table_counts: tuple[TeamBackupTableCount, ...]
    erasure_counts: tuple[TeamBackupTableCount, ...] = ()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.schema_version, int)
            or isinstance(self.schema_version, bool)
            or self.schema_version < 1
        ):
            raise ValueError("team database schema version is invalid")
        counts = tuple(self.table_counts)
        if not counts or counts != tuple(sorted(counts)):
            raise ValueError("team database inventory is invalid")
        object.__setattr__(self, "table_counts", counts)
        erasures = tuple(self.erasure_counts)
        if (
            erasures != tuple(sorted(erasures))
            or len({item.table_name for item in erasures}) != len(erasures)
            or any(
                item.table_name not in {count.table_name for count in counts} for item in erasures
            )
        ):
            raise ValueError("team database erasure inventory is invalid")
        object.__setattr__(self, "erasure_counts", erasures)


class TeamDatabaseBackupPort(Protocol):
    @property
    def source_database(self) -> str: ...

    def dump_snapshot(self, destination: Path) -> TeamDatabaseInventory: ...

    def validate_archive(self, archive: Path) -> None: ...

    def require_empty_target(self, target_database: str) -> None: ...

    def restore_archive(self, archive: Path, target_database: str) -> None: ...

    def inventory(self, database: str) -> TeamDatabaseInventory: ...


@dataclass(frozen=True, slots=True)
class TeamBackupResult:
    manifest_path: Path
    artifact_path: Path
    manifest: TeamBackupManifest

    def to_dict(self) -> dict[str, object]:
        return {
            "manifest_path": str(self.manifest_path),
            "artifact_path": str(self.artifact_path),
            "backup_id": str(self.manifest.backup_id),
            "created_at": self.manifest.created_at.isoformat(),
            "schema_version": self.manifest.schema_version,
            "artifact_digest": self.manifest.artifact_digest,
            "size_bytes": self.manifest.size_bytes,
            "table_count": len(self.manifest.table_counts),
        }


@dataclass(frozen=True, slots=True)
class TeamRestoreDrillResult:
    backup_id: str
    target_database: str
    schema_version: int
    table_count: int
    row_count: int
    duration_ms: int

    def to_dict(self) -> dict[str, object]:
        return {
            "backup_id": self.backup_id,
            "target_database": self.target_database,
            "schema_version": self.schema_version,
            "table_count": self.table_count,
            "row_count": self.row_count,
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True, slots=True)
class TeamBackupDeletionResult:
    backups_removed: int
    files_removed: int
    bytes_removed: int

    def to_dict(self) -> dict[str, object]:
        return {
            "backups_removed": self.backups_removed,
            "files_removed": self.files_removed,
            "bytes_removed": self.bytes_removed,
        }


class TeamBackupService:
    def __init__(
        self,
        port: TeamDatabaseBackupPort,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        temporary_id: Callable[[], UUID] = uuid4,
        timer: Callable[[], float] = monotonic,
    ) -> None:
        self._port = port
        self._clock = clock
        self._temporary_id = temporary_id
        self._timer = timer

    def create(self, output_directory: Path) -> TeamBackupResult:
        directory = _private_directory(output_directory)
        created_at = self._clock()
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise TeamBackupError("MNEMO_TEAM_BACKUP_FAILED")
        created_at = created_at.astimezone(UTC)
        temporary = directory / f".mnemo-team-backup-{self._temporary_id()}.tmp"
        manifest_temporary = directory / f"{temporary.name}.json"
        artifact: Path | None = None
        manifest_path: Path | None = None
        artifact_published = False
        manifest_published = False
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            inventory = self._port.dump_snapshot(temporary)
            self._port.validate_archive(temporary)
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            size = temporary.stat().st_size
            if size < 1:
                raise TeamBackupError("MNEMO_TEAM_BACKUP_FAILED")
            digest = _file_digest(temporary)
            timestamp = created_at.strftime("%Y%m%dT%H%M%S%fZ")
            artifact_name = (
                f"mnemo-team-v{inventory.schema_version}-{timestamp}-"
                f"{digest.removeprefix('sha256:')}.dump"
            )
            artifact = directory / artifact_name
            manifest_path = directory / f"{artifact_name}.json"
            if artifact.exists() or artifact.is_symlink() or manifest_path.exists():
                raise TeamBackupError("MNEMO_TEAM_BACKUP_CONFLICT")
            manifest = TeamBackupManifest.create(
                created_at=created_at,
                schema_version=inventory.schema_version,
                artifact_name=artifact_name,
                artifact_digest=digest,
                size_bytes=size,
                table_counts=inventory.table_counts,
                erasure_counts=inventory.erasure_counts,
            )
            _write_private_file(manifest_temporary, manifest.canonical_json().encode("utf-8"))
            _publish_without_overwrite(temporary, artifact)
            artifact_published = True
            _publish_without_overwrite(manifest_temporary, manifest_path)
            manifest_published = True
            _fsync_directory(directory)
            return TeamBackupResult(manifest_path, artifact, manifest)
        except TeamBackupError:
            _remove(temporary, manifest_temporary)
            if manifest_published and manifest_path is not None:
                _remove(manifest_path)
            if artifact_published and artifact is not None:
                _remove(artifact)
            raise
        except FileExistsError as error:
            _remove(temporary, manifest_temporary)
            if manifest_published and manifest_path is not None:
                _remove(manifest_path)
            if artifact_published and artifact is not None:
                _remove(artifact)
            raise TeamBackupError("MNEMO_TEAM_BACKUP_CONFLICT") from error
        except (OSError, ValueError) as error:
            _remove(temporary, manifest_temporary)
            if manifest_published and manifest_path is not None:
                _remove(manifest_path)
            if artifact_published and artifact is not None:
                _remove(artifact)
            raise TeamBackupError("MNEMO_TEAM_BACKUP_FAILED") from error

    def restore_drill(self, manifest_path: Path, *, target_database: str) -> TeamRestoreDrillResult:
        try:
            if target_database == self._port.source_database:
                raise TeamBackupError("MNEMO_TEAM_RESTORE_TARGET_INVALID")
            manifest = _read_manifest(manifest_path)
            artifact = manifest_path.parent / manifest.artifact_name
            _require_private_regular_file(artifact)
            if (
                artifact.stat().st_size != manifest.size_bytes
                or _file_digest(artifact) != manifest.artifact_digest
            ):
                raise TeamBackupError("MNEMO_TEAM_BACKUP_INVALID")
            self._port.validate_archive(artifact)
            self._port.require_empty_target(target_database)
            started = self._timer()
            self._port.restore_archive(artifact, target_database)
            restored = self._port.inventory(target_database)
            duration_ms = max(0, round((self._timer() - started) * 1000))
            if (
                restored.schema_version != manifest.schema_version
                or restored.table_counts != manifest.table_counts
            ):
                raise TeamBackupError("MNEMO_TEAM_RESTORE_VERIFICATION_FAILED")
            return TeamRestoreDrillResult(
                str(manifest.backup_id),
                target_database,
                manifest.schema_version,
                len(manifest.table_counts),
                sum(item.row_count for item in manifest.table_counts),
                duration_ms,
            )
        except TeamBackupError:
            raise
        except (OSError, ValueError) as error:
            raise TeamBackupError("MNEMO_TEAM_RESTORE_FAILED") from error

    def prune_deleted(self, backup_directory: Path) -> TeamBackupDeletionResult:
        directory = _existing_private_directory(backup_directory)
        manifests = _backup_manifests(directory)
        try:
            current = self._port.inventory(self._port.source_database)
            candidates: list[tuple[Path, Path | None, int]] = []
            for manifest_path in manifests:
                manifest = _read_manifest(manifest_path)
                if manifest_path.name != f"{manifest.artifact_name}.json":
                    raise TeamBackupError("MNEMO_TEAM_BACKUP_INVALID")
                stale = _predates_erasure(manifest, current)
                artifact = manifest_path.parent / manifest.artifact_name
                if artifact.exists() or artifact.is_symlink():
                    _require_private_regular_file(artifact)
                    if (
                        artifact.stat().st_size != manifest.size_bytes
                        or _file_digest(artifact) != manifest.artifact_digest
                    ):
                        raise TeamBackupError("MNEMO_TEAM_BACKUP_INVALID")
                    self._port.validate_archive(artifact)
                    artifact_value: Path | None = artifact
                elif stale:
                    artifact_value = None
                else:
                    raise TeamBackupError("MNEMO_TEAM_BACKUP_INVALID")
                if stale:
                    candidates.append(
                        (
                            manifest_path,
                            artifact_value,
                            manifest_path.stat().st_size
                            + (0 if artifact_value is None else manifest.size_bytes),
                        )
                    )

            removed_files = 0
            removed_bytes = 0
            for manifest_path, candidate_artifact, size in candidates:
                if candidate_artifact is not None:
                    candidate_artifact.unlink()
                    removed_files += 1
                    _fsync_directory(directory)
                manifest_path.unlink()
                removed_files += 1
                removed_bytes += size
                _fsync_directory(directory)
            return TeamBackupDeletionResult(len(candidates), removed_files, removed_bytes)
        except TeamBackupError:
            raise
        except (OSError, ValueError) as error:
            raise TeamBackupError("MNEMO_TEAM_BACKUP_DELETION_FAILED") from error


def _private_directory(path: Path) -> Path:
    if not isinstance(path, Path) or not path.is_absolute() or path.is_symlink():
        raise TeamBackupError("MNEMO_TEAM_BACKUP_DIRECTORY_UNSAFE")
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not path.is_dir() or path.stat().st_uid != os.getuid():
            raise OSError
        os.chmod(path, 0o700)
        return path
    except OSError as error:
        raise TeamBackupError("MNEMO_TEAM_BACKUP_DIRECTORY_UNSAFE") from error


def _existing_private_directory(path: Path) -> Path:
    if not isinstance(path, Path) or not path.is_absolute() or path.is_symlink():
        raise TeamBackupError("MNEMO_TEAM_BACKUP_DIRECTORY_UNSAFE")
    try:
        metadata = path.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise OSError
        return path
    except OSError as error:
        raise TeamBackupError("MNEMO_TEAM_BACKUP_DIRECTORY_UNSAFE") from error


_BACKUP_MANIFEST_NAME = re.compile(
    r"^mnemo-team-v[1-9][0-9]*-[0-9]{8}T[0-9]{12}Z-[0-9a-f]{64}\.dump\.json$"
)


def _backup_manifests(directory: Path) -> tuple[Path, ...]:
    try:
        manifests: list[Path] = []
        for count, path in enumerate(directory.iterdir(), start=1):
            if count > 4_096:
                raise TeamBackupError("MNEMO_TEAM_BACKUP_DIRECTORY_UNSAFE")
            if _BACKUP_MANIFEST_NAME.fullmatch(path.name):
                manifests.append(path)
                if len(manifests) > 1_000:
                    raise TeamBackupError("MNEMO_TEAM_BACKUP_DIRECTORY_UNSAFE")
        return tuple(sorted(manifests))
    except OSError as error:
        raise TeamBackupError("MNEMO_TEAM_BACKUP_DIRECTORY_UNSAFE") from error


def _predates_erasure(manifest: TeamBackupManifest, current: TeamDatabaseInventory) -> bool:
    if current.schema_version < manifest.schema_version:
        raise TeamBackupError("MNEMO_TEAM_BACKUP_DELETION_STATE_INVALID")
    current_counts = {item.table_name: item.row_count for item in current.erasure_counts}
    if manifest.format_version == TEAM_BACKUP_FORMAT_V1:
        return any(current_counts.values())
    backup_counts = {item.table_name: item.row_count for item in manifest.erasure_counts}
    if any(current_counts.get(name, 0) < count for name, count in backup_counts.items()):
        raise TeamBackupError("MNEMO_TEAM_BACKUP_DELETION_STATE_INVALID")
    return any(count > backup_counts.get(name, 0) for name, count in current_counts.items())


def _read_manifest(path: Path) -> TeamBackupManifest:
    _require_private_regular_file(path)
    if path.stat().st_size > 65_536:
        raise TeamBackupError("MNEMO_TEAM_BACKUP_INVALID")
    try:
        return TeamBackupManifest.from_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, TypeError, ValueError) as error:
        raise TeamBackupError("MNEMO_TEAM_BACKUP_INVALID") from error


def _require_private_regular_file(path: Path) -> None:
    try:
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise OSError
    except OSError as error:
        raise TeamBackupError("MNEMO_TEAM_BACKUP_INVALID") from error


def _write_private_file(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        _write_all(descriptor, content)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written < 1:
            raise OSError("private file write did not progress")
        view = view[written:]


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


def _publish_without_overwrite(source: Path, destination: Path) -> None:
    os.link(source, destination, follow_symlinks=False)
    try:
        source.unlink()
    except OSError:
        destination.unlink(missing_ok=True)
        raise


def _remove(*paths: Path) -> None:
    for path in paths:
        path.unlink(missing_ok=True)
