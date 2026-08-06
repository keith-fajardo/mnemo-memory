"""Real PostgreSQL custom-format backup and verified restore drill."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast
from uuid import uuid4

import pg8000.dbapi  # type: ignore[import-untyped]
import pytest

from mnemo_memory.connectors.postgresql.backup import (
    NativeCommandResult,
    PostgreSQLBackupToolConfig,
    PostgreSQLNativeBackupAdapter,
)
from mnemo_memory.packages.application import TeamBackupService
from mnemo_memory.packages.storage import (
    POSTGRES_TEAM_SCHEMA_VERSION,
    PostgreSQLConnection,
    PostgreSQLTeamMigrationRunner,
)


def _settings() -> tuple[str, int, str] | None:
    host = os.environ.get("MNEMO_TEST_POSTGRES_HOST")
    port = os.environ.get("MNEMO_TEST_POSTGRES_PORT")
    user = os.environ.get("MNEMO_TEST_POSTGRES_ADMIN_USER")
    if host is None or port is None or user is None:
        return None
    return host, int(port), user


def _connection(host: str, port: int, database: str, user: str) -> PostgreSQLConnection:
    return cast(
        PostgreSQLConnection,
        pg8000.dbapi.connect(
            host=host,
            port=port,
            database=database,
            user=user,
            timeout=5,
        ),
    )


def _admin(host: str, port: int, user: str, sql: str, *, database: str = "postgres") -> None:
    connection = pg8000.dbapi.connect(host=host, port=port, database=database, user=user, timeout=5)
    connection.autocommit = True
    cursor = connection.cursor()
    try:
        cursor.execute(sql)
    finally:
        cursor.close()
        connection.close()


def _local_command(arguments: Sequence[str], environment: Mapping[str, str]) -> NativeCommandResult:
    test_environment = {**os.environ, **environment, "PGSSLMODE": "disable"}
    test_environment.pop("PGSSLROOTCERT", None)
    completed = subprocess.run(
        tuple(arguments),
        env=test_environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=60,
    )
    return NativeCommandResult(completed.returncode, completed.stdout)


def test_real_team_backup_restores_exact_schema_ledger_and_table_counts(tmp_path: Path) -> None:
    settings = _settings()
    if settings is None:
        pytest.skip("real PostgreSQL tests require explicit MNEMO_TEST_POSTGRES_* settings")
    host, port, admin_user = settings
    suffix = uuid4().hex[:12]
    source_database = f"mnemo_backup_source_{suffix}"
    target_database = f"mnemo_backup_restore_{suffix}"
    backup_role = f"mnemo_backup_{suffix}"
    _admin(
        host,
        port,
        admin_user,
        f'CREATE ROLE "{backup_role}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE INHERIT BYPASSRLS',
    )
    try:
        _admin(host, port, admin_user, f'GRANT pg_read_all_data TO "{backup_role}"')
        _admin(host, port, admin_user, f'CREATE DATABASE "{source_database}"')
        _admin(
            host,
            port,
            admin_user,
            f'CREATE DATABASE "{target_database}" OWNER "{backup_role}"',
        )

        def source_factory() -> PostgreSQLConnection:
            return _connection(host, port, source_database, admin_user)

        assert (
            PostgreSQLTeamMigrationRunner(source_factory).migrate() == POSTGRES_TEAM_SCHEMA_VERSION
        )
        _admin(
            host,
            port,
            admin_user,
            "CREATE EXTENSION IF NOT EXISTS vector",
            database=target_database,
        )
        adapter = PostgreSQLNativeBackupAdapter(
            PostgreSQLBackupToolConfig(
                host,
                port,
                source_database,
                backup_role,
                "unused-by-isolated-trust-server",
            ),
            lambda database: _connection(host, port, database, backup_role),
            command_runner=_local_command,
        )
        service = TeamBackupService(adapter)

        backup = service.create((tmp_path / "team-backups").resolve())
        restored = service.restore_drill(backup.manifest_path, target_database=target_database)

        assert backup.manifest.schema_version == POSTGRES_TEAM_SCHEMA_VERSION
        assert restored.schema_version == POSTGRES_TEAM_SCHEMA_VERSION
        assert restored.table_count == len(backup.manifest.table_counts)
        assert restored.row_count == sum(item.row_count for item in backup.manifest.table_counts)
    finally:
        for database in (target_database, source_database):
            _admin(
                host,
                port,
                admin_user,
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{database}' AND pid <> pg_backend_pid()",
            )
            _admin(host, port, admin_user, f'DROP DATABASE IF EXISTS "{database}"')
        _admin(host, port, admin_user, f'DROP ROLE IF EXISTS "{backup_role}"')
