"""Native PostgreSQL backup-tool adapter for the team operational profile."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast
from uuid import uuid4

from mnemo_memory.packages.application.team_backups import (
    TeamBackupError,
    TeamDatabaseInventory,
)
from mnemo_memory.packages.domain import TeamBackupTableCount
from mnemo_memory.packages.storage import PostgreSQLConnection

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_TABLE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


@dataclass(frozen=True, slots=True)
class PostgreSQLBackupToolConfig:
    host: str
    port: int
    source_database: str
    user: str
    password: str
    ssl_root_cert: str = "system"

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and _NAME.fullmatch(value)
            for value in (self.source_database, self.user)
        ):
            raise ValueError("PostgreSQL backup identity is invalid")
        if not isinstance(self.host, str) or not self.host or len(self.host) > 253:
            raise ValueError("PostgreSQL backup host is invalid")
        if (
            not isinstance(self.port, int)
            or isinstance(self.port, bool)
            or not 1 <= self.port <= 65535
        ):
            raise ValueError("PostgreSQL backup port is invalid")
        if (
            not isinstance(self.password, str)
            or not self.password
            or "\n" in self.password
            or "\r" in self.password
            or len(self.password.encode("utf-8")) > 4_096
        ):
            raise ValueError("PostgreSQL backup password is invalid")
        if not isinstance(self.ssl_root_cert, str) or not self.ssl_root_cert:
            raise ValueError("PostgreSQL backup trust root is invalid")


@dataclass(frozen=True, slots=True)
class NativeCommandResult:
    return_code: int
    stdout: bytes = b""


CommandRunner = Callable[[Sequence[str], Mapping[str, str]], NativeCommandResult]
DatabaseConnectionFactory = Callable[[str], PostgreSQLConnection]


class PostgreSQLNativeBackupAdapter:
    """Use pg_dump/pg_restore with one exported snapshot and no payload logging."""

    def __init__(
        self,
        config: PostgreSQLBackupToolConfig,
        connection_factory: DatabaseConnectionFactory,
        *,
        command_runner: CommandRunner | None = None,
        pg_dump: str | None = None,
        pg_restore: str | None = None,
    ) -> None:
        self._config = config
        self._connection_factory = connection_factory
        self._run = command_runner or _run_command
        self._pg_dump = pg_dump or _tool("pg_dump")
        self._pg_restore = pg_restore or _tool("pg_restore")

    @property
    def source_database(self) -> str:
        return self._config.source_database

    def dump_snapshot(self, destination: Path) -> TeamDatabaseInventory:
        connection = self._connect(self.source_database)
        cursor = connection.cursor()
        passfile = destination.parent / f".mnemo-pgpass-{uuid4()}.tmp"
        try:
            connection.autocommit = False
            cursor.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            self._require_backup_role(cursor)
            cursor.execute("SELECT pg_catalog.pg_export_snapshot()")
            row = cursor.fetchone()
            if row is None or not isinstance(row[0], str) or not row[0]:
                raise TeamBackupError("MNEMO_TEAM_BACKUP_FAILED")
            snapshot = row[0]
            inventory = self._inventory_with_cursor(cursor)
            self._write_passfile(passfile, self.source_database)
            result = self._run(
                (
                    self._pg_dump,
                    "--format=custom",
                    "--compress=6",
                    "--no-owner",
                    "--no-privileges",
                    "--schema=mnemo_team",
                    f"--snapshot={snapshot}",
                    f"--file={destination}",
                    *self._connection_arguments(self.source_database),
                ),
                self._command_environment(passfile),
            )
            if result.return_code != 0:
                raise TeamBackupError("MNEMO_TEAM_BACKUP_FAILED")
            connection.commit()
            return inventory
        except TeamBackupError:
            connection.rollback()
            raise
        except Exception as error:
            connection.rollback()
            raise TeamBackupError("MNEMO_TEAM_BACKUP_FAILED") from error
        finally:
            passfile.unlink(missing_ok=True)
            cursor.close()
            connection.close()

    def validate_archive(self, archive: Path) -> None:
        result = self._run((self._pg_restore, "--list", str(archive)), {})
        if (
            result.return_code != 0
            or len(result.stdout) > 2_000_000
            or b"SCHEMA - mnemo_team" not in result.stdout
            or b"TABLE mnemo_team schema_migrations" not in result.stdout
        ):
            raise TeamBackupError("MNEMO_TEAM_BACKUP_INVALID")

    def require_empty_target(self, target_database: str) -> None:
        self._require_database_name(target_database)
        connection = self._connect(target_database)
        cursor = connection.cursor()
        try:
            self._require_backup_role(cursor)
            cursor.execute("SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname = 'mnemo_team'")
            if cursor.fetchone() is not None:
                raise TeamBackupError("MNEMO_TEAM_RESTORE_TARGET_NOT_EMPTY")
        except TeamBackupError:
            raise
        except Exception as error:
            raise TeamBackupError("MNEMO_TEAM_RESTORE_TARGET_INVALID") from error
        finally:
            cursor.close()
            connection.close()

    def restore_archive(self, archive: Path, target_database: str) -> None:
        self._require_database_name(target_database)
        passfile = archive.parent / f".mnemo-pgpass-{uuid4()}.tmp"
        try:
            self._write_passfile(passfile, target_database)
            result = self._run(
                (
                    self._pg_restore,
                    "--exit-on-error",
                    "--single-transaction",
                    "--no-owner",
                    "--no-privileges",
                    f"--dbname={target_database}",
                    *self._connection_arguments(None),
                    str(archive),
                ),
                self._command_environment(passfile),
            )
            if result.return_code != 0:
                raise TeamBackupError("MNEMO_TEAM_RESTORE_FAILED")
        finally:
            passfile.unlink(missing_ok=True)

    def inventory(self, database: str) -> TeamDatabaseInventory:
        self._require_database_name(database)
        connection = self._connect(database)
        cursor = connection.cursor()
        try:
            self._require_backup_role(cursor)
            return self._inventory_with_cursor(cursor)
        except TeamBackupError:
            raise
        except Exception as error:
            raise TeamBackupError("MNEMO_TEAM_RESTORE_VERIFICATION_FAILED") from error
        finally:
            cursor.close()
            connection.close()

    def _connect(self, database: str) -> PostgreSQLConnection:
        try:
            return self._connection_factory(database)
        except Exception as error:
            raise TeamBackupError("MNEMO_TEAM_POSTGRES_UNAVAILABLE") from error

    @staticmethod
    def _require_backup_role(cursor: object) -> None:
        typed = cast("_Cursor", cursor)
        typed.execute(
            "SELECT rolsuper, rolbypassrls FROM pg_catalog.pg_roles WHERE rolname = current_user"
        )
        row = typed.fetchone()
        if row is None or bool(row[0]) or not bool(row[1]):
            raise TeamBackupError("MNEMO_TEAM_BACKUP_ROLE_INVALID")

    @staticmethod
    def _inventory_with_cursor(cursor: object) -> TeamDatabaseInventory:
        typed = cast("_Cursor", cursor)
        typed.execute("SELECT version FROM mnemo_team.schema_migrations ORDER BY version")
        versions = tuple(int(str(row[0])) for row in typed.fetchall())
        if not versions or versions != tuple(range(1, versions[-1] + 1)):
            raise TeamBackupError("MNEMO_TEAM_BACKUP_FAILED")
        typed.execute(
            "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = 'mnemo_team' "
            "ORDER BY tablename"
        )
        names = tuple(str(row[0]) for row in typed.fetchall())
        if not names or len(names) > 128 or any(_TABLE.fullmatch(name) is None for name in names):
            raise TeamBackupError("MNEMO_TEAM_BACKUP_FAILED")
        counts: list[TeamBackupTableCount] = []
        for name in names:
            typed.execute(f'SELECT count(*) FROM mnemo_team."{name}"')
            row = typed.fetchone()
            if row is None:
                raise TeamBackupError("MNEMO_TEAM_BACKUP_FAILED")
            counts.append(TeamBackupTableCount(name, int(str(row[0]))))
        return TeamDatabaseInventory(versions[-1], tuple(counts))

    def _write_passfile(self, path: Path, database: str) -> None:
        value = ":".join(
            _passfile_escape(item)
            for item in (
                self._config.host,
                str(self._config.port),
                database,
                self._config.user,
                self._config.password,
            )
        )
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            content = memoryview(f"{value}\n".encode())
            while content:
                written = os.write(descriptor, content)
                if written < 1:
                    raise OSError("PostgreSQL password file write did not progress")
                content = content[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _connection_arguments(self, database: str | None) -> tuple[str, ...]:
        values = (
            f"--host={self._config.host}",
            f"--port={self._config.port}",
            f"--username={self._config.user}",
        )
        if database is None:
            return values
        return (*values, f"--dbname={database}")

    def _command_environment(self, passfile: Path) -> dict[str, str]:
        return {
            "PGCONNECT_TIMEOUT": "5",
            "PGPASSFILE": str(passfile),
            "PGSSLMODE": "verify-full",
            "PGSSLROOTCERT": self._config.ssl_root_cert,
        }

    @staticmethod
    def _require_database_name(value: str) -> None:
        if not isinstance(value, str) or _NAME.fullmatch(value) is None:
            raise TeamBackupError("MNEMO_TEAM_RESTORE_TARGET_INVALID")


class _Cursor(Protocol):
    def execute(self, operation: str, args: Sequence[object] | None = None) -> object: ...

    def fetchone(self) -> Sequence[object] | None: ...

    def fetchall(self) -> Sequence[Sequence[object]]: ...


def _tool(name: str) -> str:
    value = shutil.which(name)
    if value is None or not Path(value).is_absolute():
        raise TeamBackupError("MNEMO_TEAM_BACKUP_TOOL_UNAVAILABLE")
    return value


def _run_command(arguments: Sequence[str], environment: Mapping[str, str]) -> NativeCommandResult:
    try:
        completed = subprocess.run(
            tuple(arguments),
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3600,
            check=False,
        )
        return NativeCommandResult(completed.returncode, completed.stdout)
    except (OSError, subprocess.SubprocessError) as error:
        raise TeamBackupError("MNEMO_TEAM_BACKUP_TOOL_FAILED") from error


def _passfile_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", "\\:")
