"""Optional PostgreSQL adapter for the team control-plane repository."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime
from importlib import resources
from typing import Protocol, cast

from mnemo_memory.packages.domain import (
    EventId,
    MembershipStatus,
    OwnerId,
    ProjectId,
    ProjectMembership,
    ProjectRole,
    RequestId,
    TeamAuditAction,
    TeamAuditEvent,
    TeamProject,
    TeamProjectVisibility,
    TeamWorkspace,
    WorkspaceId,
    WorkspaceMembership,
    WorkspaceRole,
)
from mnemo_memory.packages.policy import TeamOperation

from .team import (
    TeamAuditPage,
    TeamControlPlaneConflict,
    TeamControlPlaneError,
    TeamControlPlaneInvalidMutation,
    TeamControlPlaneNotFound,
    TeamControlPlaneStorageFailure,
    TeamMutationResult,
)

POSTGRES_TEAM_SCHEMA_VERSION = 21
_POSTGRES_TEAM_MIGRATIONS = (
    (1, "0001_team_control_plane.sql"),
    (2, "0002_team_knowledge.sql"),
    (3, "0003_team_checkpoints.sql"),
    (4, "0004_team_task_events_outbox.sql"),
    (5, "0005_team_approved_episodic_events.sql"),
    (6, "0006_team_episodic_candidates.sql"),
    (7, "0007_team_episodic_governance.sql"),
    (8, "0008_team_episodic_retention.sql"),
    (9, "0009_team_task_activity_retention.sql"),
    (10, "0010_team_episodic_deletions.sql"),
    (11, "0011_team_source_structure.sql"),
    (12, "0012_team_checkpoint_source_observations.sql"),
    (13, "0013_team_dbt_manifest.sql"),
    (14, "0014_team_dbt_supplemental.sql"),
    (15, "0015_team_imported_episodic_lifecycle.sql"),
    (16, "0016_team_imported_approved_events.sql"),
    (17, "0017_team_imported_knowledge.sql"),
    (18, "0018_team_checkpoint_expiry.sql"),
    (19, "0019_team_checkpoint_deletions.sql"),
    (20, "0020_team_checkpoint_deletion_import.sql"),
    (21, "0021_team_knowledge_governance.sql"),
)
_ROLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}\Z")


class PostgreSQLTeamMigrationError(RuntimeError):
    pass


class PostgreSQLTeamSchemaTooNewError(PostgreSQLTeamMigrationError):
    pass


class PostgreSQLCursor(Protocol):
    rowcount: int

    def execute(self, operation: str, args: Sequence[object] | None = None) -> object: ...

    def fetchone(self) -> Sequence[object] | None: ...

    def fetchall(self) -> Sequence[Sequence[object]]: ...

    def close(self) -> None: ...


class PostgreSQLConnection(Protocol):
    autocommit: bool

    def cursor(self) -> PostgreSQLCursor: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


PostgreSQLConnectionFactory = Callable[[], PostgreSQLConnection]


def _migration_text(name: str) -> str:
    return (
        resources.files("mnemo_memory")
        .joinpath("resources", "postgres_migrations", name)
        .read_text(encoding="utf-8")
    )


def _sqlstate(error: Exception) -> str | None:
    for value in error.args:
        if isinstance(value, Mapping):
            state = value.get("C")
            if isinstance(state, str):
                return state
    return None


def _translated_database_error(error: Exception) -> TeamControlPlaneError:
    state = _sqlstate(error)
    if state == "42501":
        return TeamControlPlaneInvalidMutation("team database denied the operation")
    if state is not None and (state.startswith("23") or state in {"40001", "40P01"}):
        return TeamControlPlaneConflict("team database rejected conflicting state")
    return TeamControlPlaneStorageFailure("team database operation failed")


def _role_identifier(value: str) -> str:
    if not isinstance(value, str) or _ROLE_NAME.fullmatch(value) is None:
        raise ValueError("PostgreSQL runtime role name is invalid")
    return f'"{value}"'


class PostgreSQLTeamMigrationRunner:
    """Apply the forward-only team schema and grant one least-privilege runtime role."""

    def __init__(
        self,
        connection_factory: PostgreSQLConnectionFactory,
        *,
        fail_migration_at: int | None = None,
    ) -> None:
        self._connection_factory = connection_factory
        self._fail_migration_at = fail_migration_at

    def migrate(self) -> int:
        connection = self._connect()
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT to_regclass('mnemo_team.schema_migrations')")
            row = cursor.fetchone()
            exists = row is not None and row[0] is not None
            versions: tuple[int, ...] = ()
            if exists:
                cursor.execute("SELECT version FROM mnemo_team.schema_migrations ORDER BY version")
                versions = tuple(int(str(row[0])) for row in cursor.fetchall())
                if versions and versions[-1] > POSTGRES_TEAM_SCHEMA_VERSION:
                    raise PostgreSQLTeamSchemaTooNewError(
                        "PostgreSQL team schema is newer than this application"
                    )
                if versions != tuple(range(1, len(versions) + 1)):
                    raise PostgreSQLTeamMigrationError(
                        "PostgreSQL team migration ledger is incomplete"
                    )
            current_version = versions[-1] if versions else 0
            for version, name in _POSTGRES_TEAM_MIGRATIONS:
                if version <= current_version:
                    continue
                cursor.execute(_migration_text(name))
                if self._fail_migration_at == version:
                    raise PostgreSQLTeamMigrationError("injected PostgreSQL migration failure")
            connection.commit()
            return POSTGRES_TEAM_SCHEMA_VERSION
        except PostgreSQLTeamMigrationError:
            connection.rollback()
            raise
        except Exception as error:
            connection.rollback()
            raise PostgreSQLTeamMigrationError("PostgreSQL team migration failed") from error
        finally:
            cursor.close()
            connection.close()

    def provision_runtime_role(self, role_name: str) -> None:
        role = _role_identifier(role_name)
        connection = self._connect()
        cursor = connection.cursor()
        try:
            cursor.execute(
                "SELECT rolsuper, rolbypassrls, rolname = current_user "
                "FROM pg_catalog.pg_roles WHERE rolname = %s",
                (role_name,),
            )
            row = cursor.fetchone()
            if row is None:
                raise PostgreSQLTeamMigrationError("PostgreSQL runtime role does not exist")
            if bool(row[0]) or bool(row[1]) or bool(row[2]):
                raise PostgreSQLTeamMigrationError(
                    "PostgreSQL runtime role must be non-owner, non-superuser, and non-BYPASSRLS"
                )
            statements = (
                f"GRANT USAGE ON SCHEMA mnemo_team TO {role}",
                f"GRANT SELECT, INSERT, UPDATE ON mnemo_team.workspaces TO {role}",
                f"GRANT SELECT, INSERT, UPDATE ON mnemo_team.workspace_memberships TO {role}",
                f"GRANT SELECT, INSERT, UPDATE ON mnemo_team.projects TO {role}",
                f"GRANT SELECT, INSERT, UPDATE ON mnemo_team.project_memberships TO {role}",
                f"GRANT SELECT, INSERT ON mnemo_team.audit_events TO {role}",
                f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA mnemo_team TO {role}",
                f"GRANT EXECUTE ON FUNCTION mnemo_team.current_uuid(text) TO {role}",
                f"GRANT EXECUTE ON FUNCTION mnemo_team.current_principal() TO {role}",
                f"GRANT EXECUTE ON FUNCTION mnemo_team.current_workspace() TO {role}",
                f"GRANT EXECUTE ON FUNCTION mnemo_team.current_operation() TO {role}",
                "GRANT EXECUTE ON FUNCTION "
                f"mnemo_team.workspace_role_allowed(text, text) TO {role}",
                f"GRANT EXECUTE ON FUNCTION mnemo_team.project_role_allowed(text, text) TO {role}",
                "GRANT EXECUTE ON FUNCTION "
                f"mnemo_team.is_active_workspace_member(uuid, uuid) TO {role}",
                "GRANT EXECUTE ON FUNCTION "
                f"mnemo_team.authorized(uuid, uuid, uuid, text) TO {role}",
                f"GRANT SELECT, INSERT, UPDATE ON mnemo_team.knowledge_sync_status TO {role}",
                "GRANT SELECT, INSERT, UPDATE, DELETE ON "
                f"mnemo_team.knowledge_document_sources TO {role}",
                "GRANT SELECT, INSERT, DELETE ON "
                f"mnemo_team.knowledge_document_revisions TO {role}",
                f"GRANT SELECT, INSERT, DELETE ON mnemo_team.knowledge_document_sections TO {role}",
                f"GRANT SELECT, INSERT, DELETE ON mnemo_team.knowledge_document_links TO {role}",
                "GRANT SELECT, INSERT, UPDATE ON "
                f"mnemo_team.knowledge_document_tombstones TO {role}",
                "GRANT SELECT, INSERT, UPDATE, DELETE ON "
                f"mnemo_team.knowledge_section_embeddings TO {role}",
                f"GRANT SELECT, INSERT ON mnemo_team.knowledge_source_approvals TO {role}",
                "GRANT EXECUTE ON FUNCTION "
                f"mnemo_team.can_approve_knowledge_source(uuid, uuid) TO {role}",
                f"GRANT SELECT, INSERT, UPDATE ON mnemo_team.checkpoint_aggregates TO {role}",
                f"GRANT DELETE ON mnemo_team.checkpoint_aggregates TO {role}",
                f"GRANT SELECT, INSERT, DELETE ON mnemo_team.checkpoint_revisions TO {role}",
                f"GRANT SELECT, INSERT, DELETE ON mnemo_team.checkpoint_lifecycle_events TO {role}",
                f"GRANT SELECT, INSERT ON mnemo_team.checkpoint_deletions TO {role}",
                f"GRANT SELECT, INSERT, DELETE ON mnemo_team.task_activity_events TO {role}",
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON mnemo_team.event_outbox TO {role}",
                f"GRANT SELECT, INSERT, DELETE ON mnemo_team.approved_episodic_events TO {role}",
                f"GRANT SELECT, INSERT ON mnemo_team.approved_episodic_event_governance TO {role}",
                f"GRANT SELECT, INSERT ON mnemo_team.approved_episodic_event_pin_actions TO {role}",
                f"GRANT SELECT, INSERT, DELETE ON mnemo_team.episodic_memory_candidates TO {role}",
                f"GRANT SELECT, INSERT, DELETE ON mnemo_team.episodic_candidate_reviews TO {role}",
                f"GRANT SELECT, INSERT, DELETE ON mnemo_team.active_episodic_memories TO {role}",
                f"GRANT SELECT, INSERT, DELETE ON mnemo_team.episodic_memory_governance TO {role}",
                f"GRANT SELECT, INSERT ON mnemo_team.episodic_memory_expirations TO {role}",
                f"GRANT SELECT, INSERT ON mnemo_team.episodic_memory_purges TO {role}",
                f"GRANT SELECT, INSERT ON mnemo_team.task_activity_event_expirations TO {role}",
                f"GRANT SELECT, INSERT ON mnemo_team.task_activity_event_purges TO {role}",
                f"GRANT SELECT, INSERT ON mnemo_team.task_activity_event_deletions TO {role}",
                f"GRANT SELECT, INSERT ON mnemo_team.episodic_memory_deletions TO {role}",
                f"GRANT SELECT, INSERT ON mnemo_team.source_structure_snapshots TO {role}",
                f"GRANT UPDATE (is_active) ON mnemo_team.source_structure_snapshots TO {role}",
                f"GRANT SELECT, INSERT ON mnemo_team.source_structure_files TO {role}",
                f"GRANT SELECT, INSERT ON mnemo_team.source_structure_symbols TO {role}",
                f"GRANT SELECT, INSERT ON mnemo_team.source_structure_edges TO {role}",
                f"GRANT SELECT, INSERT ON mnemo_team.source_snapshot_activations TO {role}",
                f"GRANT SELECT, INSERT ON mnemo_team.source_structure_sync_status TO {role}",
                f"GRANT UPDATE (last_synced_at) ON "
                f"mnemo_team.source_structure_sync_status TO {role}",
                "GRANT SELECT, INSERT, DELETE ON "
                f"mnemo_team.checkpoint_source_observations TO {role}",
                f"GRANT SELECT, INSERT ON mnemo_team.dbt_manifest_snapshots TO {role}",
                f"GRANT UPDATE (is_active) ON mnemo_team.dbt_manifest_snapshots TO {role}",
                f"GRANT SELECT, INSERT ON mnemo_team.dbt_manifest_nodes TO {role}",
                f"GRANT SELECT, INSERT ON mnemo_team.dbt_lineage_edges TO {role}",
                f"GRANT SELECT, INSERT ON mnemo_team.dbt_manifest_activations TO {role}",
                f"GRANT SELECT, INSERT ON mnemo_team.dbt_manifest_sync_status TO {role}",
                f"GRANT UPDATE (last_synced_at) ON mnemo_team.dbt_manifest_sync_status TO {role}",
                f"GRANT SELECT, INSERT ON mnemo_team.dbt_supplemental_artifacts TO {role}",
                f"GRANT UPDATE (is_active) ON mnemo_team.dbt_supplemental_artifacts TO {role}",
                f"GRANT SELECT, INSERT ON mnemo_team.dbt_supplemental_resources TO {role}",
                f"GRANT SELECT, INSERT ON mnemo_team.imported_episodic_lifecycle TO {role}",
                f"GRANT SELECT, INSERT ON mnemo_team.imported_knowledge_deletions TO {role}",
            )
            for statement in statements:
                cursor.execute(statement)
            connection.commit()
        except PostgreSQLTeamMigrationError:
            connection.rollback()
            raise
        except Exception as error:
            connection.rollback()
            raise PostgreSQLTeamMigrationError(
                "PostgreSQL runtime role provisioning failed"
            ) from error
        finally:
            cursor.close()
            connection.close()

    def _connect(self) -> PostgreSQLConnection:
        try:
            connection = self._connection_factory()
            connection.autocommit = False
            return connection
        except Exception as error:
            raise PostgreSQLTeamMigrationError("PostgreSQL migration connection failed") from error


class PostgreSQLTeamControlPlaneRepository:
    """One exact principal/workspace-bound PostgreSQL repository session factory."""

    def __init__(
        self,
        connection_factory: PostgreSQLConnectionFactory,
        *,
        principal_id: OwnerId,
        workspace_id: WorkspaceId,
        statement_timeout_ms: int = 5000,
    ) -> None:
        if not isinstance(principal_id, OwnerId):
            raise TypeError("principal_id must be an OwnerId")
        if not isinstance(workspace_id, WorkspaceId):
            raise TypeError("workspace_id must be a WorkspaceId")
        if (
            not isinstance(statement_timeout_ms, int)
            or isinstance(statement_timeout_ms, bool)
            or not 1 <= statement_timeout_ms <= 60_000
        ):
            raise ValueError("statement_timeout_ms must be between 1 and 60000")
        self._connection_factory = connection_factory
        self._principal_id = principal_id
        self._workspace_id = workspace_id
        self._statement_timeout_ms = statement_timeout_ms

    def create_workspace(
        self,
        workspace: TeamWorkspace,
        owner_membership: WorkspaceMembership,
        audit_event: TeamAuditEvent,
    ) -> TeamMutationResult:
        if not isinstance(workspace, TeamWorkspace):
            raise TypeError("workspace must be a TeamWorkspace")
        if not isinstance(owner_membership, WorkspaceMembership):
            raise TypeError("owner_membership must be a WorkspaceMembership")
        self._require_workspace(workspace.workspace_id)
        self._require_audit(audit_event, TeamAuditAction.WORKSPACE_CREATED)
        if (
            workspace.owner_id != self._principal_id
            or workspace.created_at != audit_event.occurred_at
            or owner_membership.workspace_id != workspace.workspace_id
            or owner_membership.principal_id != workspace.owner_id
            or owner_membership.role is not WorkspaceRole.OWNER
            or owner_membership.status is not MembershipStatus.ACTIVE
        ):
            raise TeamControlPlaneInvalidMutation(
                "workspace creation requires its exact active owner membership and audit"
            )
        fingerprint = _fingerprint("create_workspace", workspace, owner_membership)
        with self._transaction(TeamOperation.MANAGE_WORKSPACE) as cursor:
            if self._existing_mutation(cursor, audit_event, fingerprint):
                return TeamMutationResult(audit_event, True)
            cursor.execute(
                "INSERT INTO mnemo_team.workspaces(workspace_id, owner_id, created_at) "
                "VALUES (CAST(%s AS uuid), CAST(%s AS uuid), %s)",
                (str(workspace.workspace_id), str(workspace.owner_id), workspace.created_at),
            )
            cursor.execute(
                "INSERT INTO mnemo_team.workspace_memberships"
                "(workspace_id, principal_id, role, status) "
                "VALUES (CAST(%s AS uuid), CAST(%s AS uuid), %s, %s)",
                (
                    str(owner_membership.workspace_id),
                    str(owner_membership.principal_id),
                    owner_membership.role.value,
                    owner_membership.status.value,
                ),
            )
            self._insert_audit(cursor, audit_event, fingerprint)
            return TeamMutationResult(audit_event, False)

    def get_workspace(self, workspace_id: WorkspaceId) -> TeamWorkspace:
        self._require_workspace(workspace_id)
        with self._transaction(TeamOperation.READ) as cursor:
            workspace = self._select_workspace(cursor, workspace_id)
            if workspace is None:
                raise TeamControlPlaneNotFound("team workspace was not found")
            return workspace

    def get_workspace_membership(
        self, workspace_id: WorkspaceId, principal_id: OwnerId
    ) -> WorkspaceMembership:
        self._require_workspace(workspace_id)
        if not isinstance(principal_id, OwnerId):
            raise TypeError("principal_id must be an OwnerId")
        with self._transaction(TeamOperation.READ) as cursor:
            membership = self._select_workspace_membership(cursor, workspace_id, principal_id)
            if membership is None:
                raise TeamControlPlaneNotFound("workspace membership was not found")
            return membership

    def set_workspace_membership(
        self,
        membership: WorkspaceMembership,
        *,
        expected: WorkspaceMembership | None,
        audit_event: TeamAuditEvent,
    ) -> TeamMutationResult:
        if not isinstance(membership, WorkspaceMembership):
            raise TypeError("membership must be a WorkspaceMembership")
        if expected is not None and not isinstance(expected, WorkspaceMembership):
            raise TypeError("expected must be a WorkspaceMembership")
        self._require_workspace(membership.workspace_id)
        self._require_audit(
            audit_event,
            TeamAuditAction.WORKSPACE_MEMBERSHIP_CHANGED,
            subject_principal_id=membership.principal_id,
        )
        fingerprint = _fingerprint("set_workspace_membership", membership, expected)
        with self._transaction(TeamOperation.MANAGE_MEMBERSHIP) as cursor:
            if self._existing_mutation(cursor, audit_event, fingerprint):
                return TeamMutationResult(audit_event, True)
            workspace = self._select_workspace(cursor, membership.workspace_id, for_update=True)
            if workspace is None:
                raise TeamControlPlaneNotFound("team workspace was not found")
            if (
                membership.principal_id == workspace.owner_id
                or membership.role is WorkspaceRole.OWNER
            ):
                raise TeamControlPlaneInvalidMutation(
                    "workspace owner changes require atomic ownership transfer"
                )
            current = self._select_workspace_membership(
                cursor, membership.workspace_id, membership.principal_id, for_update=True
            )
            _require_expected(current, expected, "workspace membership")
            if current is None and membership.status is not MembershipStatus.ACTIVE:
                raise TeamControlPlaneInvalidMutation(
                    "a new workspace membership must start active"
                )
            if current == membership:
                raise TeamControlPlaneInvalidMutation("workspace membership mutation is a no-op")
            self._insert_audit(cursor, audit_event, fingerprint)
            if current is None:
                cursor.execute(
                    "INSERT INTO mnemo_team.workspace_memberships"
                    "(workspace_id, principal_id, role, status) "
                    "VALUES (CAST(%s AS uuid), CAST(%s AS uuid), %s, %s)",
                    (
                        str(membership.workspace_id),
                        str(membership.principal_id),
                        membership.role.value,
                        membership.status.value,
                    ),
                )
            else:
                cursor.execute(
                    "UPDATE mnemo_team.workspace_memberships SET role = %s, status = %s "
                    "WHERE workspace_id = CAST(%s AS uuid) "
                    "AND principal_id = CAST(%s AS uuid)",
                    (
                        membership.role.value,
                        membership.status.value,
                        str(membership.workspace_id),
                        str(membership.principal_id),
                    ),
                )
                if cursor.rowcount != 1:
                    raise TeamControlPlaneConflict("workspace membership changed since it was read")
            return TeamMutationResult(audit_event, False)

    def transfer_workspace_ownership(
        self,
        workspace_id: WorkspaceId,
        *,
        expected_owner_id: OwnerId,
        new_owner_id: OwnerId,
        audit_event: TeamAuditEvent,
    ) -> TeamMutationResult:
        self._require_workspace(workspace_id)
        if not isinstance(expected_owner_id, OwnerId) or not isinstance(new_owner_id, OwnerId):
            raise TypeError("workspace owner identities must be OwnerId values")
        self._require_audit(
            audit_event,
            TeamAuditAction.WORKSPACE_OWNERSHIP_TRANSFERRED,
            subject_principal_id=new_owner_id,
        )
        fingerprint = _fingerprint(
            "transfer_workspace_ownership", workspace_id, expected_owner_id, new_owner_id
        )
        with self._transaction(TeamOperation.MANAGE_WORKSPACE) as cursor:
            if self._existing_mutation(cursor, audit_event, fingerprint):
                return TeamMutationResult(audit_event, True)
            workspace = self._select_workspace(cursor, workspace_id, for_update=True)
            successor = self._select_workspace_membership(
                cursor, workspace_id, new_owner_id, for_update=True
            )
            current_owner = self._select_workspace_membership(
                cursor, workspace_id, expected_owner_id, for_update=True
            )
            if (
                workspace is None
                or workspace.owner_id != expected_owner_id
                or expected_owner_id != self._principal_id
                or new_owner_id == expected_owner_id
            ):
                raise TeamControlPlaneConflict("workspace ownership precondition failed")
            if (
                current_owner is None
                or current_owner.role is not WorkspaceRole.OWNER
                or current_owner.status is not MembershipStatus.ACTIVE
                or successor is None
                or successor.role is WorkspaceRole.OWNER
                or successor.status is not MembershipStatus.ACTIVE
            ):
                raise TeamControlPlaneInvalidMutation(
                    "ownership transfer requires one current owner and one active member"
                )
            self._insert_audit(cursor, audit_event, fingerprint)
            cursor.execute(
                "UPDATE mnemo_team.workspaces SET owner_id = CAST(%s AS uuid) "
                "WHERE workspace_id = CAST(%s AS uuid)",
                (str(new_owner_id), str(workspace_id)),
            )
            cursor.execute(
                "UPDATE mnemo_team.workspace_memberships "
                "SET role = CASE WHEN principal_id = CAST(%s AS uuid) "
                "THEN 'owner' ELSE 'admin' END "
                "WHERE workspace_id = CAST(%s AS uuid) "
                "AND principal_id IN (CAST(%s AS uuid), CAST(%s AS uuid))",
                (
                    str(new_owner_id),
                    str(workspace_id),
                    str(expected_owner_id),
                    str(new_owner_id),
                ),
            )
            if cursor.rowcount != 2:
                raise TeamControlPlaneConflict("workspace ownership membership state changed")
            return TeamMutationResult(audit_event, False)

    def create_project(
        self, project: TeamProject, audit_event: TeamAuditEvent
    ) -> TeamMutationResult:
        if not isinstance(project, TeamProject):
            raise TypeError("project must be a TeamProject")
        self._require_workspace(project.workspace_id)
        self._require_audit(
            audit_event, TeamAuditAction.PROJECT_CREATED, project_id=project.project_id
        )
        fingerprint = _fingerprint("create_project", project)
        with self._transaction(TeamOperation.MANAGE_PROJECT) as cursor:
            if self._existing_mutation(cursor, audit_event, fingerprint):
                return TeamMutationResult(audit_event, True)
            owner = self._select_workspace_membership(
                cursor, project.workspace_id, project.owner_id
            )
            if owner is None or owner.status is not MembershipStatus.ACTIVE:
                raise TeamControlPlaneInvalidMutation(
                    "project owner must be an active workspace member"
                )
            cursor.execute(
                "INSERT INTO mnemo_team.projects(workspace_id, project_id, owner_id, visibility) "
                "VALUES (CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s)",
                (
                    str(project.workspace_id),
                    str(project.project_id),
                    str(project.owner_id),
                    project.visibility.value,
                ),
            )
            self._insert_audit(cursor, audit_event, fingerprint)
            return TeamMutationResult(audit_event, False)

    def get_project(self, workspace_id: WorkspaceId, project_id: ProjectId) -> TeamProject:
        self._require_workspace(workspace_id)
        if not isinstance(project_id, ProjectId):
            raise TypeError("project_id must be a ProjectId")
        with self._transaction(TeamOperation.READ) as cursor:
            project = self._select_project(cursor, workspace_id, project_id)
            if project is None:
                raise TeamControlPlaneNotFound("team project was not found")
            return project

    def set_project_visibility(
        self,
        project: TeamProject,
        *,
        expected: TeamProject,
        audit_event: TeamAuditEvent,
    ) -> TeamMutationResult:
        if not isinstance(project, TeamProject) or not isinstance(expected, TeamProject):
            raise TypeError("project and expected must be TeamProject values")
        self._require_workspace(project.workspace_id)
        self._require_audit(
            audit_event,
            TeamAuditAction.PROJECT_VISIBILITY_CHANGED,
            project_id=project.project_id,
        )
        fingerprint = _fingerprint("set_project_visibility", project, expected)
        with self._transaction(TeamOperation.MANAGE_PROJECT) as cursor:
            if self._existing_mutation(cursor, audit_event, fingerprint):
                return TeamMutationResult(audit_event, True)
            current = self._select_project(
                cursor, project.workspace_id, project.project_id, for_update=True
            )
            if current is None:
                raise TeamControlPlaneNotFound("team project was not found")
            _require_expected(current, expected, "team project")
            if (
                project.workspace_id != expected.workspace_id
                or project.project_id != expected.project_id
                or project.owner_id != expected.owner_id
            ):
                raise TeamControlPlaneInvalidMutation(
                    "project visibility mutation cannot change identity or ownership"
                )
            if project == expected:
                raise TeamControlPlaneInvalidMutation("project visibility mutation is a no-op")
            self._insert_audit(cursor, audit_event, fingerprint)
            cursor.execute(
                "UPDATE mnemo_team.projects SET visibility = %s "
                "WHERE workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid)",
                (project.visibility.value, str(project.workspace_id), str(project.project_id)),
            )
            if cursor.rowcount != 1:
                raise TeamControlPlaneConflict("team project changed since it was read")
            return TeamMutationResult(audit_event, False)

    def get_project_membership(
        self, workspace_id: WorkspaceId, project_id: ProjectId, principal_id: OwnerId
    ) -> ProjectMembership:
        self._require_workspace(workspace_id)
        if not isinstance(project_id, ProjectId) or not isinstance(principal_id, OwnerId):
            raise TypeError("project and principal identities are invalid")
        with self._transaction(TeamOperation.READ) as cursor:
            membership = self._select_project_membership(
                cursor, workspace_id, project_id, principal_id
            )
            if membership is None:
                raise TeamControlPlaneNotFound("project membership was not found")
            return membership

    def set_project_membership(
        self,
        membership: ProjectMembership,
        *,
        expected: ProjectMembership | None,
        audit_event: TeamAuditEvent,
    ) -> TeamMutationResult:
        if not isinstance(membership, ProjectMembership):
            raise TypeError("membership must be a ProjectMembership")
        if expected is not None and not isinstance(expected, ProjectMembership):
            raise TypeError("expected must be a ProjectMembership")
        self._require_workspace(membership.workspace_id)
        self._require_audit(
            audit_event,
            TeamAuditAction.PROJECT_MEMBERSHIP_CHANGED,
            project_id=membership.project_id,
            subject_principal_id=membership.principal_id,
        )
        fingerprint = _fingerprint("set_project_membership", membership, expected)
        with self._transaction(TeamOperation.MANAGE_PROJECT) as cursor:
            if self._existing_mutation(cursor, audit_event, fingerprint):
                return TeamMutationResult(audit_event, True)
            project = self._select_project(cursor, membership.workspace_id, membership.project_id)
            if project is None:
                raise TeamControlPlaneNotFound("team project was not found")
            workspace_membership = self._select_workspace_membership(
                cursor, membership.workspace_id, membership.principal_id
            )
            if workspace_membership is None:
                raise TeamControlPlaneInvalidMutation("project member must belong to the workspace")
            current = self._select_project_membership(
                cursor,
                membership.workspace_id,
                membership.project_id,
                membership.principal_id,
                for_update=True,
            )
            _require_expected(current, expected, "project membership")
            if current is None and membership.status is not MembershipStatus.ACTIVE:
                raise TeamControlPlaneInvalidMutation("a new project membership must start active")
            if (
                membership.status is MembershipStatus.ACTIVE
                and workspace_membership.status is not MembershipStatus.ACTIVE
            ):
                raise TeamControlPlaneInvalidMutation(
                    "an active project membership requires active workspace membership"
                )
            if current == membership:
                raise TeamControlPlaneInvalidMutation("project membership mutation is a no-op")
            self._insert_audit(cursor, audit_event, fingerprint)
            if current is None:
                cursor.execute(
                    "INSERT INTO mnemo_team.project_memberships"
                    "(workspace_id, project_id, principal_id, role, status) "
                    "VALUES (CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, %s)",
                    (
                        str(membership.workspace_id),
                        str(membership.project_id),
                        str(membership.principal_id),
                        membership.role.value,
                        membership.status.value,
                    ),
                )
            else:
                cursor.execute(
                    "UPDATE mnemo_team.project_memberships SET role = %s, status = %s "
                    "WHERE workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                    "AND principal_id = CAST(%s AS uuid)",
                    (
                        membership.role.value,
                        membership.status.value,
                        str(membership.workspace_id),
                        str(membership.project_id),
                        str(membership.principal_id),
                    ),
                )
                if cursor.rowcount != 1:
                    raise TeamControlPlaneConflict("project membership changed since it was read")
            return TeamMutationResult(audit_event, False)

    def get_audit_event(self, workspace_id: WorkspaceId, event_id: EventId) -> TeamAuditEvent:
        self._require_workspace(workspace_id)
        if not isinstance(event_id, EventId):
            raise TypeError("event_id must be an EventId")
        with self._transaction(TeamOperation.READ) as cursor:
            cursor.execute(
                _AUDIT_SELECT
                + " WHERE workspace_id = CAST(%s AS uuid) AND event_id = CAST(%s AS uuid)",
                (str(workspace_id), str(event_id)),
            )
            row = cursor.fetchone()
            if row is None:
                raise TeamControlPlaneNotFound("team audit event was not found")
            return _audit_from_row(row)[0]

    def list_audit_events(
        self, workspace_id: WorkspaceId, *, offset: int = 0, limit: int = 50
    ) -> TeamAuditPage:
        self._require_workspace(workspace_id)
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ValueError("team audit offset must be a non-negative integer")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("team audit limit must be between 1 and 100")
        with self._transaction(TeamOperation.READ) as cursor:
            if self._select_workspace(cursor, workspace_id) is None:
                raise TeamControlPlaneNotFound("team workspace was not found")
            cursor.execute(
                _AUDIT_SELECT + " WHERE workspace_id = CAST(%s AS uuid) "
                "ORDER BY audit_sequence LIMIT %s OFFSET %s",
                (str(workspace_id), limit + 1, offset),
            )
            rows = tuple(cursor.fetchall())
            items = tuple(_audit_from_row(row)[0] for row in rows[:limit])
            return TeamAuditPage(items, offset + limit if len(rows) > limit else None)

    @contextmanager
    def _transaction(self, operation: TeamOperation) -> Iterator[PostgreSQLCursor]:
        try:
            connection = self._connection_factory()
            connection.autocommit = False
        except Exception as error:
            raise TeamControlPlaneStorageFailure("team database connection failed") from error
        cursor = connection.cursor()
        try:
            cursor.execute(
                "SELECT set_config('mnemo.principal_id', %s, true), "
                "set_config('mnemo.workspace_id', %s, true), "
                "set_config('mnemo.operation', %s, true), "
                "set_config('statement_timeout', %s, true)",
                (
                    str(self._principal_id),
                    str(self._workspace_id),
                    operation.value,
                    str(self._statement_timeout_ms),
                ),
            )
            yield cursor
            connection.commit()
        except TeamControlPlaneError:
            connection.rollback()
            raise
        except Exception as error:
            connection.rollback()
            raise _translated_database_error(error) from error
        finally:
            cursor.close()
            connection.close()

    def _existing_mutation(
        self, cursor: PostgreSQLCursor, audit_event: TeamAuditEvent, fingerprint: str
    ) -> bool:
        cursor.execute(
            _AUDIT_SELECT
            + " WHERE workspace_id = CAST(%s AS uuid) AND request_id = CAST(%s AS uuid)",
            (str(audit_event.workspace_id), str(audit_event.request_id)),
        )
        row = cursor.fetchone()
        if row is None:
            return False
        existing, existing_fingerprint = _audit_from_row(row)
        if existing != audit_event or existing_fingerprint != fingerprint:
            raise TeamControlPlaneConflict("team mutation request identity conflicts")
        return True

    @staticmethod
    def _insert_audit(
        cursor: PostgreSQLCursor, audit_event: TeamAuditEvent, fingerprint: str
    ) -> None:
        cursor.execute(
            "INSERT INTO mnemo_team.audit_events"
            "(event_id, request_id, workspace_id, actor_id, action, occurred_at, "
            "project_id, subject_principal_id, mutation_fingerprint) VALUES ("
            "CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), "
            "%s, %s, CAST(%s AS uuid), CAST(%s AS uuid), %s)",
            (
                str(audit_event.event_id),
                str(audit_event.request_id),
                str(audit_event.workspace_id),
                str(audit_event.actor_id),
                audit_event.action.value,
                audit_event.occurred_at,
                None if audit_event.project_id is None else str(audit_event.project_id),
                (
                    None
                    if audit_event.subject_principal_id is None
                    else str(audit_event.subject_principal_id)
                ),
                fingerprint,
            ),
        )

    def _require_workspace(self, workspace_id: WorkspaceId) -> None:
        if not isinstance(workspace_id, WorkspaceId):
            raise TypeError("workspace_id must be a WorkspaceId")
        if workspace_id != self._workspace_id:
            raise TeamControlPlaneNotFound("team workspace was not found")

    def _require_audit(
        self,
        audit_event: TeamAuditEvent,
        action: TeamAuditAction,
        *,
        project_id: ProjectId | None = None,
        subject_principal_id: OwnerId | None = None,
    ) -> None:
        if not isinstance(audit_event, TeamAuditEvent):
            raise TypeError("audit_event must be a TeamAuditEvent")
        if (
            audit_event.workspace_id != self._workspace_id
            or audit_event.actor_id != self._principal_id
            or audit_event.action is not action
            or audit_event.project_id != project_id
            or audit_event.subject_principal_id != subject_principal_id
        ):
            raise TeamControlPlaneInvalidMutation(
                "team audit event does not match the requested mutation"
            )

    @staticmethod
    def _select_workspace(
        cursor: PostgreSQLCursor, workspace_id: WorkspaceId, *, for_update: bool = False
    ) -> TeamWorkspace | None:
        cursor.execute(
            "SELECT workspace_id::text, owner_id::text, created_at "
            "FROM mnemo_team.workspaces WHERE workspace_id = CAST(%s AS uuid)"
            + (" FOR UPDATE" if for_update else ""),
            (str(workspace_id),),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return TeamWorkspace(
            WorkspaceId.from_string(str(row[0])),
            OwnerId.from_string(str(row[1])),
            cast(datetime, row[2]),
        )

    @staticmethod
    def _select_workspace_membership(
        cursor: PostgreSQLCursor,
        workspace_id: WorkspaceId,
        principal_id: OwnerId,
        *,
        for_update: bool = False,
    ) -> WorkspaceMembership | None:
        cursor.execute(
            "SELECT workspace_id::text, principal_id::text, role, status "
            "FROM mnemo_team.workspace_memberships "
            "WHERE workspace_id = CAST(%s AS uuid) AND principal_id = CAST(%s AS uuid)"
            + (" FOR UPDATE" if for_update else ""),
            (str(workspace_id), str(principal_id)),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return WorkspaceMembership(
            WorkspaceId.from_string(str(row[0])),
            OwnerId.from_string(str(row[1])),
            WorkspaceRole(str(row[2])),
            MembershipStatus(str(row[3])),
        )

    @staticmethod
    def _select_project(
        cursor: PostgreSQLCursor,
        workspace_id: WorkspaceId,
        project_id: ProjectId,
        *,
        for_update: bool = False,
    ) -> TeamProject | None:
        cursor.execute(
            "SELECT workspace_id::text, project_id::text, owner_id::text, visibility "
            "FROM mnemo_team.projects WHERE workspace_id = CAST(%s AS uuid) "
            "AND project_id = CAST(%s AS uuid)" + (" FOR UPDATE" if for_update else ""),
            (str(workspace_id), str(project_id)),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return TeamProject(
            WorkspaceId.from_string(str(row[0])),
            ProjectId.from_string(str(row[1])),
            OwnerId.from_string(str(row[2])),
            TeamProjectVisibility(str(row[3])),
        )

    @staticmethod
    def _select_project_membership(
        cursor: PostgreSQLCursor,
        workspace_id: WorkspaceId,
        project_id: ProjectId,
        principal_id: OwnerId,
        *,
        for_update: bool = False,
    ) -> ProjectMembership | None:
        cursor.execute(
            "SELECT workspace_id::text, project_id::text, principal_id::text, role, status "
            "FROM mnemo_team.project_memberships WHERE workspace_id = CAST(%s AS uuid) "
            "AND project_id = CAST(%s AS uuid) AND principal_id = CAST(%s AS uuid)"
            + (" FOR UPDATE" if for_update else ""),
            (str(workspace_id), str(project_id), str(principal_id)),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return ProjectMembership(
            WorkspaceId.from_string(str(row[0])),
            ProjectId.from_string(str(row[1])),
            OwnerId.from_string(str(row[2])),
            ProjectRole(str(row[3])),
            MembershipStatus(str(row[4])),
        )


_AUDIT_SELECT = (
    "SELECT event_id::text, request_id::text, workspace_id::text, actor_id::text, "
    "action, occurred_at, project_id::text, subject_principal_id::text, "
    "mutation_fingerprint, audit_sequence FROM mnemo_team.audit_events"
)


def _audit_from_row(row: Sequence[object]) -> tuple[TeamAuditEvent, str]:
    event = TeamAuditEvent(
        EventId.from_string(str(row[0])),
        RequestId.from_string(str(row[1])),
        WorkspaceId.from_string(str(row[2])),
        OwnerId.from_string(str(row[3])),
        TeamAuditAction(str(row[4])),
        cast(datetime, row[5]),
        None if row[6] is None else ProjectId.from_string(str(row[6])),
        None if row[7] is None else OwnerId.from_string(str(row[7])),
    )
    return event, str(row[8])


def _fingerprint(kind: str, *values: object) -> str:
    normalized: list[object] = [kind]
    for value in values:
        if value is None:
            normalized.append(None)
        elif hasattr(value, "to_dict"):
            normalized.append(cast(object, value.to_dict()))
        else:
            normalized.append(str(value))
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _require_expected[ValueT](current: ValueT | None, expected: ValueT | None, name: str) -> None:
    if current != expected:
        raise TeamControlPlaneConflict(f"{name} changed since it was read")
