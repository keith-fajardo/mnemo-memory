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


def _create_deletable_knowledge(
    host: str, port: int, database: str, admin_user: str
) -> tuple[str, str, str, str, str]:
    owner_id = str(uuid4())
    workspace_id = str(uuid4())
    project_id = str(uuid4())
    document_id = str(uuid4())
    revision_id = str(uuid4())
    digest = "sha256:" + "a" * 64
    connection = pg8000.dbapi.connect(
        host=host, port=port, database=database, user=admin_user, timeout=5
    )
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT set_config('mnemo.principal_id', %s, true), "
            "set_config('mnemo.workspace_id', %s, true), "
            "set_config('mnemo.operation', 'contribute', true)",
            (owner_id, workspace_id),
        )
        cursor.execute(
            "INSERT INTO mnemo_team.workspaces(workspace_id, owner_id, created_at) "
            "VALUES (CAST(%s AS uuid), CAST(%s AS uuid), CURRENT_TIMESTAMP)",
            (workspace_id, owner_id),
        )
        cursor.execute(
            "INSERT INTO mnemo_team.workspace_memberships"
            "(workspace_id, principal_id, role, status) "
            "VALUES (CAST(%s AS uuid), CAST(%s AS uuid), 'owner', 'active')",
            (workspace_id, owner_id),
        )
        cursor.execute(
            "INSERT INTO mnemo_team.projects"
            "(workspace_id, project_id, owner_id, visibility) "
            "VALUES (CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), 'private')",
            (workspace_id, project_id, owner_id),
        )
        cursor.execute(
            "INSERT INTO mnemo_team.knowledge_document_sources"
            "(workspace_id, project_id, owner_id, visibility, document_id, relative_path, "
            "content_digest, current_revision_id, is_deleted, created_at, source_owner_id, "
            "source_owner_authenticated) VALUES (CAST(%s AS uuid), CAST(%s AS uuid), "
            "CAST(%s AS uuid), 'project', CAST(%s AS uuid), 'private/deletable.md', %s, "
            "NULL, false, CURRENT_TIMESTAMP, CAST(%s AS uuid), true)",
            (workspace_id, project_id, owner_id, document_id, digest, owner_id),
        )
        cursor.execute(
            "INSERT INTO mnemo_team.knowledge_document_revisions"
            "(workspace_id, project_id, owner_id, visibility, revision_id, document_id, "
            "revision_number, predecessor_revision_id, source_kind, relative_path, "
            "content_digest, title, frontmatter_json, created_at, authored_by_id, "
            "author_authenticated) VALUES (CAST(%s AS uuid), CAST(%s AS uuid), "
            "CAST(%s AS uuid), 'project', CAST(%s AS uuid), CAST(%s AS uuid), 1, NULL, "
            "'markdown', 'private/deletable.md', %s, 'Sensitive backup payload', "
            "CAST('{}' AS jsonb), CURRENT_TIMESTAMP, CAST(%s AS uuid), true)",
            (workspace_id, project_id, owner_id, revision_id, document_id, digest, owner_id),
        )
        cursor.execute(
            "INSERT INTO mnemo_team.knowledge_document_sections"
            "(workspace_id, project_id, owner_id, visibility, revision_id, section_index, "
            "heading, heading_level, content) VALUES (CAST(%s AS uuid), CAST(%s AS uuid), "
            "CAST(%s AS uuid), 'project', CAST(%s AS uuid), 0, 'Secret', 1, "
            "'payload removed after backup')",
            (workspace_id, project_id, owner_id, revision_id),
        )
        cursor.execute(
            "UPDATE mnemo_team.knowledge_document_sources SET current_revision_id = "
            "CAST(%s AS uuid) WHERE workspace_id = CAST(%s AS uuid) "
            "AND document_id = CAST(%s AS uuid)",
            (revision_id, workspace_id, document_id),
        )
        connection.commit()
        return workspace_id, project_id, owner_id, document_id, digest
    finally:
        cursor.close()
        connection.close()


def _delete_knowledge(
    host: str,
    port: int,
    database: str,
    admin_user: str,
    identity: tuple[str, str, str, str, str],
) -> None:
    workspace_id, project_id, owner_id, document_id, digest = identity
    connection = pg8000.dbapi.connect(
        host=host, port=port, database=database, user=admin_user, timeout=5
    )
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT set_config('mnemo.principal_id', %s, true), "
            "set_config('mnemo.workspace_id', %s, true), "
            "set_config('mnemo.operation', 'contribute', true)",
            (owner_id, workspace_id),
        )
        cursor.execute(
            "UPDATE mnemo_team.knowledge_document_sources SET current_revision_id = NULL, "
            "is_deleted = true, deleted_at = CURRENT_TIMESTAMP "
            "WHERE workspace_id = CAST(%s AS uuid) AND document_id = CAST(%s AS uuid)",
            (workspace_id, document_id),
        )
        cursor.execute(
            "INSERT INTO mnemo_team.knowledge_document_tombstones"
            "(workspace_id, project_id, owner_id, visibility, document_id, relative_path, "
            "content_digest, deleted_at) VALUES (CAST(%s AS uuid), CAST(%s AS uuid), "
            "CAST(%s AS uuid), 'project', CAST(%s AS uuid), 'private/deletable.md', %s, "
            "CURRENT_TIMESTAMP)",
            (workspace_id, project_id, owner_id, document_id, digest),
        )
        cursor.execute(
            "DELETE FROM mnemo_team.knowledge_document_revisions "
            "WHERE workspace_id = CAST(%s AS uuid) AND document_id = CAST(%s AS uuid)",
            (workspace_id, document_id),
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()


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
        deletion_identity = _create_deletable_knowledge(host, port, source_database, admin_user)
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
        _delete_knowledge(host, port, source_database, admin_user, deletion_identity)
        current = service.create((tmp_path / "team-backups").resolve())

        pruned = service.prune_deleted((tmp_path / "team-backups").resolve())

        assert pruned.backups_removed == 1 and pruned.files_removed == 2
        assert not backup.artifact_path.exists() and not backup.manifest_path.exists()
        assert current.artifact_path.is_file() and current.manifest_path.is_file()
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
