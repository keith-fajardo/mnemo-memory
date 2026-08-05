"""Real PostgreSQL contract and RLS parity tests for the optional team adapter."""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from importlib import resources
from typing import cast
from uuid import uuid4

import pg8000.dbapi  # type: ignore[import-untyped]
import pytest

from mnemo_memory.packages.domain import (
    MembershipStatus,
    MemoryScope,
    OwnerId,
    ProjectId,
    ProjectMembership,
    ProjectRole,
    RequestId,
    ScopeLevel,
    TeamAuditAction,
    TeamAuditEvent,
    TeamProject,
    TeamProjectVisibility,
    TeamWorkspace,
    Visibility,
    WorkspaceId,
    WorkspaceMembership,
    WorkspaceRole,
)
from mnemo_memory.packages.policy import (
    TeamAuthorizationPolicy,
    TeamAuthorizationRequest,
    TeamOperation,
)
from mnemo_memory.packages.storage import (
    POSTGRES_TEAM_SCHEMA_VERSION,
    PostgreSQLConnection,
    PostgreSQLConnectionFactory,
    PostgreSQLTeamControlPlaneRepository,
    PostgreSQLTeamMigrationError,
    PostgreSQLTeamMigrationRunner,
    TeamControlPlaneNotFound,
)

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class PostgreSQLHarness:
    host: str
    port: int
    admin_user: str
    database: str
    runtime_role: str

    def connection(
        self, *, database: str | None = None, user: str | None = None
    ) -> PostgreSQLConnection:
        return cast(
            PostgreSQLConnection,
            pg8000.dbapi.connect(
                host=self.host,
                port=self.port,
                database=database or self.database,
                user=user or self.admin_user,
                timeout=5,
            ),
        )

    def admin_factory(self) -> PostgreSQLConnectionFactory:
        return lambda: self.connection()

    def runtime_factory(self) -> PostgreSQLConnectionFactory:
        return lambda: self.connection(user=self.runtime_role)


def _server_settings() -> tuple[str, int, str] | None:
    host = os.environ.get("MNEMO_TEST_POSTGRES_HOST")
    port = os.environ.get("MNEMO_TEST_POSTGRES_PORT")
    user = os.environ.get("MNEMO_TEST_POSTGRES_ADMIN_USER")
    if host is None or port is None or user is None:
        return None
    return host, int(port), user


def _execute_admin(
    host: str,
    port: int,
    user: str,
    sql: str,
    *,
    database: str = "postgres",
) -> None:
    connection = pg8000.dbapi.connect(host=host, port=port, database=database, user=user, timeout=5)
    connection.autocommit = True
    cursor = connection.cursor()
    try:
        cursor.execute(sql)
    finally:
        cursor.close()
        connection.close()


@pytest.fixture(scope="module")
def postgres_harness() -> Iterator[PostgreSQLHarness]:
    settings = _server_settings()
    if settings is None:
        pytest.skip("real PostgreSQL tests require explicit MNEMO_TEST_POSTGRES_* settings")
    host, port, admin_user = settings
    suffix = uuid4().hex[:12]
    database = f"mnemo_test_{suffix}"
    runtime_role = f"mnemo_runtime_{suffix}"
    _execute_admin(
        host,
        port,
        admin_user,
        f'CREATE ROLE "{runtime_role}" LOGIN NOSUPERUSER NOCREATEDB '
        "NOCREATEROLE NOINHERIT NOBYPASSRLS",
    )
    _execute_admin(host, port, admin_user, f'CREATE DATABASE "{database}"')
    harness = PostgreSQLHarness(host, port, admin_user, database, runtime_role)
    runner = PostgreSQLTeamMigrationRunner(harness.admin_factory())
    assert runner.migrate() == POSTGRES_TEAM_SCHEMA_VERSION
    assert runner.migrate() == POSTGRES_TEAM_SCHEMA_VERSION
    runner.provision_runtime_role(runtime_role)
    try:
        yield harness
    finally:
        _execute_admin(
            host,
            port,
            admin_user,
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = '{database}' AND pid <> pg_backend_pid()",
        )
        _execute_admin(host, port, admin_user, f'DROP DATABASE "{database}"')
        _execute_admin(host, port, admin_user, f'DROP ROLE "{runtime_role}"')


def _audit(
    workspace_id: WorkspaceId,
    actor_id: OwnerId,
    action: TeamAuditAction,
    *,
    project_id: ProjectId | None = None,
    subject_principal_id: OwnerId | None = None,
    request_id: RequestId | None = None,
) -> TeamAuditEvent:
    return TeamAuditEvent.for_mutation(
        request_id=request_id or RequestId.new(),
        workspace_id=workspace_id,
        actor_id=actor_id,
        action=action,
        occurred_at=NOW,
        project_id=project_id,
        subject_principal_id=subject_principal_id,
    )


def _repository(
    harness: PostgreSQLHarness, principal_id: OwnerId, workspace_id: WorkspaceId
) -> PostgreSQLTeamControlPlaneRepository:
    return PostgreSQLTeamControlPlaneRepository(
        harness.runtime_factory(), principal_id=principal_id, workspace_id=workspace_id
    )


def _create_workspace(
    harness: PostgreSQLHarness,
) -> tuple[PostgreSQLTeamControlPlaneRepository, TeamWorkspace, WorkspaceMembership]:
    owner_id, workspace_id = OwnerId.new(), WorkspaceId.new()
    repository = _repository(harness, owner_id, workspace_id)
    workspace = TeamWorkspace(workspace_id, owner_id, NOW)
    owner = WorkspaceMembership(
        workspace_id, owner_id, WorkspaceRole.OWNER, MembershipStatus.ACTIVE
    )
    event = _audit(workspace_id, owner_id, TeamAuditAction.WORKSPACE_CREATED)
    first = repository.create_workspace(workspace, owner, event)
    assert not first.idempotent
    assert repository.create_workspace(workspace, owner, event).idempotent
    return repository, workspace, owner


def _add_workspace_member(
    repository: PostgreSQLTeamControlPlaneRepository,
    workspace: TeamWorkspace,
    principal_id: OwnerId,
    role: WorkspaceRole,
) -> WorkspaceMembership:
    membership = WorkspaceMembership(
        workspace.workspace_id, principal_id, role, MembershipStatus.ACTIVE
    )
    repository.set_workspace_membership(
        membership,
        expected=None,
        audit_event=_audit(
            workspace.workspace_id,
            workspace.owner_id,
            TeamAuditAction.WORKSPACE_MEMBERSHIP_CHANGED,
            subject_principal_id=principal_id,
        ),
    )
    return membership


def test_postgres_migration_is_packaged_restrictive_and_rolls_back(
    postgres_harness: PostgreSQLHarness,
) -> None:
    sql = (
        resources.files("mnemo_memory")
        .joinpath("resources", "postgres_migrations", "0001_team_control_plane.sql")
        .read_text(encoding="utf-8")
    )
    assert sql.count("FORCE ROW LEVEL SECURITY") == 5
    assert "REVOKE ALL ON ALL TABLES IN SCHEMA mnemo_team FROM PUBLIC" in sql
    assert "BYPASSRLS" not in sql
    assert "GRANT ALL" not in sql

    suffix = uuid4().hex[:12]
    database = f"mnemo_rollback_{suffix}"
    _execute_admin(
        postgres_harness.host,
        postgres_harness.port,
        postgres_harness.admin_user,
        f'CREATE DATABASE "{database}"',
    )

    def factory() -> PostgreSQLConnection:
        return postgres_harness.connection(database=database)

    try:
        with pytest.raises(PostgreSQLTeamMigrationError, match="injected"):
            PostgreSQLTeamMigrationRunner(factory, fail_migration_at=1).migrate()
        connection = factory()
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT to_regnamespace('mnemo_team')")
            row = cursor.fetchone()
            assert row is not None and row[0] is None
        finally:
            cursor.close()
            connection.close()
    finally:
        _execute_admin(
            postgres_harness.host,
            postgres_harness.port,
            postgres_harness.admin_user,
            f'DROP DATABASE "{database}"',
        )


def test_postgres_repository_is_atomic_durable_and_exactly_scoped(
    postgres_harness: PostgreSQLHarness,
) -> None:
    owner_repository, workspace, owner = _create_workspace(postgres_harness)
    admin_id, editor_id, viewer_id = OwnerId.new(), OwnerId.new(), OwnerId.new()
    admin = _add_workspace_member(owner_repository, workspace, admin_id, WorkspaceRole.ADMIN)
    _add_workspace_member(owner_repository, workspace, editor_id, WorkspaceRole.EDITOR)
    _add_workspace_member(owner_repository, workspace, viewer_id, WorkspaceRole.VIEWER)

    project = TeamProject(
        workspace.workspace_id,
        ProjectId.new(),
        workspace.owner_id,
        TeamProjectVisibility.PRIVATE,
    )
    owner_repository.create_project(
        project,
        _audit(
            workspace.workspace_id,
            workspace.owner_id,
            TeamAuditAction.PROJECT_CREATED,
            project_id=project.project_id,
        ),
    )
    project_member = ProjectMembership(
        workspace.workspace_id,
        project.project_id,
        editor_id,
        ProjectRole.MAINTAINER,
        MembershipStatus.ACTIVE,
    )
    owner_repository.set_project_membership(
        project_member,
        expected=None,
        audit_event=_audit(
            workspace.workspace_id,
            workspace.owner_id,
            TeamAuditAction.PROJECT_MEMBERSHIP_CHANGED,
            project_id=project.project_id,
            subject_principal_id=editor_id,
        ),
    )
    editor_repository = _repository(postgres_harness, editor_id, workspace.workspace_id)
    assert editor_repository.get_project(workspace.workspace_id, project.project_id) == project
    viewer_repository = _repository(postgres_harness, viewer_id, workspace.workspace_id)
    with pytest.raises(TeamControlPlaneNotFound):
        viewer_repository.get_project(workspace.workspace_id, project.project_id)

    transfer = _audit(
        workspace.workspace_id,
        workspace.owner_id,
        TeamAuditAction.WORKSPACE_OWNERSHIP_TRANSFERRED,
        subject_principal_id=admin_id,
    )
    owner_repository.transfer_workspace_ownership(
        workspace.workspace_id,
        expected_owner_id=workspace.owner_id,
        new_owner_id=admin_id,
        audit_event=transfer,
    )
    assert owner_repository.transfer_workspace_ownership(
        workspace.workspace_id,
        expected_owner_id=workspace.owner_id,
        new_owner_id=admin_id,
        audit_event=transfer,
    ).idempotent
    assert owner_repository.get_workspace_membership(
        workspace.workspace_id, owner.principal_id
    ) == replace(owner, role=WorkspaceRole.ADMIN)
    assert owner_repository.get_workspace_membership(workspace.workspace_id, admin_id) == replace(
        admin, role=WorkspaceRole.OWNER
    )
    assert owner_repository.list_audit_events(workspace.workspace_id, limit=3).next_offset == 3

    foreign_repository, foreign_workspace, _ = _create_workspace(postgres_harness)
    with pytest.raises(TeamControlPlaneNotFound):
        owner_repository.get_workspace(foreign_workspace.workspace_id)
    assert foreign_repository.get_workspace(foreign_workspace.workspace_id) == foreign_workspace


def _database_authorized(
    harness: PostgreSQLHarness,
    principal_id: OwnerId,
    workspace_id: WorkspaceId,
    operation: TeamOperation,
    *,
    project_id: ProjectId | None = None,
    item_owner_id: OwnerId | None = None,
    item_visibility: Visibility = Visibility.WORKSPACE,
) -> bool:
    connection = harness.runtime_factory()()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT set_config('mnemo.principal_id', %s, true), "
            "set_config('mnemo.workspace_id', %s, true), "
            "set_config('mnemo.operation', %s, true)",
            (str(principal_id), str(workspace_id), operation.value),
        )
        cursor.execute(
            "SELECT mnemo_team.authorized(CAST(%s AS uuid), CAST(%s AS uuid), "
            "CAST(%s AS uuid), %s)",
            (
                str(workspace_id),
                None if project_id is None else str(project_id),
                None if item_owner_id is None else str(item_owner_id),
                item_visibility.value,
            ),
        )
        row = cursor.fetchone()
        assert row is not None
        return bool(row[0])
    finally:
        connection.rollback()
        cursor.close()
        connection.close()


def test_database_authorization_matches_python_policy_and_denies_cross_tenant(
    postgres_harness: PostgreSQLHarness,
) -> None:
    owner_repository, workspace, _ = _create_workspace(postgres_harness)
    policy = TeamAuthorizationPolicy()
    role_memberships: list[WorkspaceMembership] = []
    for role in (WorkspaceRole.ADMIN, WorkspaceRole.EDITOR, WorkspaceRole.VIEWER):
        principal_id = OwnerId.new()
        role_memberships.append(
            _add_workspace_member(owner_repository, workspace, principal_id, role)
        )
    role_memberships.append(
        owner_repository.get_workspace_membership(workspace.workspace_id, workspace.owner_id)
    )
    scope = MemoryScope(
        workspace.owner_id,
        ScopeLevel.WORKSPACE,
        Visibility.WORKSPACE,
        workspace_id=workspace.workspace_id,
    )
    for membership in role_memberships:
        for operation in TeamOperation:
            expected = policy.decide(
                TeamAuthorizationRequest(
                    membership.principal_id,
                    operation,
                    scope,
                    membership,
                )
            ).allowed
            actual = _database_authorized(
                postgres_harness,
                membership.principal_id,
                workspace.workspace_id,
                operation,
            )
            assert actual is expected, (membership.role, operation, actual, expected)

    private_project = TeamProject(
        workspace.workspace_id,
        ProjectId.new(),
        workspace.owner_id,
        TeamProjectVisibility.PRIVATE,
    )
    owner_repository.create_project(
        private_project,
        _audit(
            workspace.workspace_id,
            workspace.owner_id,
            TeamAuditAction.PROJECT_CREATED,
            project_id=private_project.project_id,
        ),
    )
    for project_role in ProjectRole:
        principal_id = OwnerId.new()
        workspace_membership = _add_workspace_member(
            owner_repository, workspace, principal_id, WorkspaceRole.EDITOR
        )
        project_membership = ProjectMembership(
            workspace.workspace_id,
            private_project.project_id,
            principal_id,
            project_role,
            MembershipStatus.ACTIVE,
        )
        owner_repository.set_project_membership(
            project_membership,
            expected=None,
            audit_event=_audit(
                workspace.workspace_id,
                workspace.owner_id,
                TeamAuditAction.PROJECT_MEMBERSHIP_CHANGED,
                project_id=private_project.project_id,
                subject_principal_id=principal_id,
            ),
        )
        project_scope = MemoryScope(
            workspace.owner_id,
            ScopeLevel.PROJECT,
            Visibility.PROJECT,
            workspace_id=workspace.workspace_id,
            project_id=private_project.project_id,
        )
        for operation in TeamOperation:
            expected = policy.decide(
                TeamAuthorizationRequest(
                    principal_id,
                    operation,
                    project_scope,
                    workspace_membership,
                    private_project,
                    project_membership,
                )
            ).allowed
            actual = _database_authorized(
                postgres_harness,
                principal_id,
                workspace.workspace_id,
                operation,
                project_id=private_project.project_id,
                item_visibility=Visibility.PROJECT,
            )
            assert actual is expected, (project_role, operation, actual, expected)

    admin_membership = next(item for item in role_memberships if item.role is WorkspaceRole.ADMIN)
    assert not _database_authorized(
        postgres_harness,
        admin_membership.principal_id,
        workspace.workspace_id,
        TeamOperation.READ,
        item_owner_id=workspace.owner_id,
        item_visibility=Visibility.OWNER,
    )

    foreign_repository, foreign_workspace, _ = _create_workspace(postgres_harness)
    connection = postgres_harness.runtime_factory()()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT set_config('mnemo.principal_id', %s, true), "
            "set_config('mnemo.workspace_id', %s, true), "
            "set_config('mnemo.operation', 'read', true)",
            (str(workspace.owner_id), str(foreign_workspace.workspace_id)),
        )
        cursor.execute("SELECT count(*) FROM mnemo_team.workspaces")
        row = cursor.fetchone()
        assert row is not None and row[0] == 0
        connection.rollback()
        cursor.execute("SELECT count(*) FROM mnemo_team.workspaces")
        row = cursor.fetchone()
        assert row is not None and row[0] == 0
        cursor.execute(
            "SELECT set_config('mnemo.principal_id', 'not-a-uuid', true), "
            "set_config('mnemo.workspace_id', %s, true), "
            "set_config('mnemo.operation', 'not-an-operation', true)",
            (str(workspace.workspace_id),),
        )
        cursor.execute("SELECT count(*) FROM mnemo_team.workspaces")
        row = cursor.fetchone()
        assert row is not None and row[0] == 0
    finally:
        connection.rollback()
        cursor.close()
        connection.close()
    assert foreign_repository.get_workspace(foreign_workspace.workspace_id) == foreign_workspace
