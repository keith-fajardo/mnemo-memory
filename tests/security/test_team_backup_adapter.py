"""Team backup tooling keeps credentials out of commands and enforces backup authority."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

import pytest

from mnemo_memory.connectors.postgresql.backup import (
    NativeCommandResult,
    PostgreSQLBackupToolConfig,
    PostgreSQLNativeBackupAdapter,
)
from mnemo_memory.packages.application import TeamBackupError
from mnemo_memory.packages.storage import PostgreSQLConnection


class _Cursor:
    def __init__(self, *, superuser: bool = False, bypass_rls: bool = True) -> None:
        self.superuser = superuser
        self.bypass_rls = bypass_rls
        self.operation = ""

    def execute(self, operation: str, args: Sequence[object] | None = None) -> object:
        self.operation = operation
        return None

    def fetchone(self) -> Sequence[object] | None:
        if "rolsuper" in self.operation:
            return (self.superuser, self.bypass_rls)
        if "pg_export_snapshot" in self.operation:
            return ("00000003-0000001A-1",)
        if 'count(*) FROM mnemo_team."schema_migrations"' in self.operation:
            return (21,)
        if 'count(*) FROM mnemo_team."workspaces"' in self.operation:
            return (2,)
        return None

    def fetchall(self) -> Sequence[Sequence[object]]:
        if "SELECT version" in self.operation:
            return tuple((value,) for value in range(1, 22))
        if "pg_catalog.pg_tables" in self.operation:
            return (("schema_migrations",), ("workspaces",))
        return ()

    def close(self) -> None:
        pass


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self.autocommit = False
        self._cursor = cursor
        self.committed = False
        self.rolled_back = False

    def cursor(self) -> _Cursor:
        return self._cursor

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        pass


def _config() -> PostgreSQLBackupToolConfig:
    return PostgreSQLBackupToolConfig(
        "postgres.internal",
        5432,
        "mnemo",
        "mnemo_backup",
        "backup-password-must-not-leak",
    )


def test_native_backup_uses_exported_snapshot_tls_passfile_and_sanitized_commands(
    tmp_path: Path,
) -> None:
    connection = _Connection(_Cursor())
    calls: list[tuple[tuple[str, ...], dict[str, str]]] = []
    passfile_seen = False

    def run(arguments: Sequence[str], environment: Mapping[str, str]) -> NativeCommandResult:
        nonlocal passfile_seen
        args = tuple(arguments)
        env = dict(environment)
        calls.append((args, env))
        assert "backup-password-must-not-leak" not in " ".join(args)
        if args[0].endswith("pg_dump"):
            passfile = Path(env["PGPASSFILE"])
            passfile_seen = passfile.is_file() and passfile.stat().st_mode & 0o777 == 0o600
            output = Path(
                next(item.removeprefix("--file=") for item in args if item.startswith("--file="))
            )
            output.write_bytes(b"native custom archive")
            return NativeCommandResult(0)
        return NativeCommandResult(
            0,
            b"1; 0 0 SCHEMA - mnemo\n2; 0 0 SCHEMA - mnemo_team\n"
            b"3; 1259 1 TABLE mnemo_team schema_migrations mnemo_backup\n",
        )

    adapter = PostgreSQLNativeBackupAdapter(
        _config(),
        lambda database: cast(PostgreSQLConnection, connection),
        command_runner=run,
        pg_dump="/usr/bin/pg_dump",
        pg_restore="/usr/bin/pg_restore",
    )
    archive = tmp_path / "backup.dump"

    inventory = adapter.dump_snapshot(archive)
    adapter.validate_archive(archive)

    assert inventory.schema_version == 21
    assert tuple(item.row_count for item in inventory.table_counts) == (21, 2)
    assert connection.committed and not connection.rolled_back
    assert passfile_seen
    assert not any(path.name.startswith(".mnemo-pgpass") for path in tmp_path.iterdir())
    dump_args, dump_env = calls[0]
    assert "--snapshot=00000003-0000001A-1" in dump_args
    assert "--schema=mnemo_team" in dump_args
    assert not any(item.startswith("--extension=") for item in dump_args)
    assert dump_env["PGSSLMODE"] == "verify-full"
    assert dump_env["PGSSLROOTCERT"] == "system"


@pytest.mark.parametrize(("superuser", "bypass_rls"), [(True, True), (False, False)])
def test_backup_rejects_superuser_and_non_bypass_roles(
    tmp_path: Path, superuser: bool, bypass_rls: bool
) -> None:
    connection = _Connection(_Cursor(superuser=superuser, bypass_rls=bypass_rls))
    adapter = PostgreSQLNativeBackupAdapter(
        _config(),
        lambda database: cast(PostgreSQLConnection, connection),
        command_runner=lambda arguments, environment: NativeCommandResult(0),
        pg_dump="/usr/bin/pg_dump",
        pg_restore="/usr/bin/pg_restore",
    )

    with pytest.raises(TeamBackupError, match="BACKUP_ROLE_INVALID"):
        adapter.dump_snapshot(tmp_path / "backup.dump")

    assert connection.rolled_back and not connection.committed
