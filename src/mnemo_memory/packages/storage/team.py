"""Storage-neutral team control-plane contract and atomic reference adapter."""

from __future__ import annotations

from dataclasses import dataclass, replace
from threading import RLock
from typing import Protocol

from mnemo_memory.packages.domain import (
    EventId,
    MembershipStatus,
    OwnerId,
    ProjectId,
    ProjectMembership,
    RequestId,
    TeamAuditAction,
    TeamAuditEvent,
    TeamProject,
    TeamWorkspace,
    WorkspaceId,
    WorkspaceMembership,
    WorkspaceRole,
)


class TeamControlPlaneError(Exception):
    """Safe storage-neutral outcome for team authority state."""


class TeamControlPlaneNotFound(TeamControlPlaneError):
    pass


class TeamControlPlaneConflict(TeamControlPlaneError):
    pass


class TeamControlPlaneInvalidMutation(TeamControlPlaneError):
    pass


class TeamControlPlaneStorageFailure(TeamControlPlaneError):
    pass


@dataclass(frozen=True, slots=True)
class TeamMutationResult:
    audit_event: TeamAuditEvent
    idempotent: bool


@dataclass(frozen=True, slots=True)
class TeamAuditPage:
    items: tuple[TeamAuditEvent, ...]
    next_offset: int | None

    def __post_init__(self) -> None:
        items = tuple(self.items)
        if any(not isinstance(item, TeamAuditEvent) for item in items):
            raise TypeError("team audit page items must be TeamAuditEvent values")
        if self.next_offset is not None and self.next_offset < 1:
            raise ValueError("team audit next offset must be positive")
        object.__setattr__(self, "items", items)


class TeamControlPlaneRepository(Protocol):
    def create_workspace(
        self,
        workspace: TeamWorkspace,
        owner_membership: WorkspaceMembership,
        audit_event: TeamAuditEvent,
    ) -> TeamMutationResult: ...

    def get_workspace(self, workspace_id: WorkspaceId) -> TeamWorkspace: ...

    def get_workspace_membership(
        self, workspace_id: WorkspaceId, principal_id: OwnerId
    ) -> WorkspaceMembership: ...

    def set_workspace_membership(
        self,
        membership: WorkspaceMembership,
        *,
        expected: WorkspaceMembership | None,
        audit_event: TeamAuditEvent,
    ) -> TeamMutationResult: ...

    def transfer_workspace_ownership(
        self,
        workspace_id: WorkspaceId,
        *,
        expected_owner_id: OwnerId,
        new_owner_id: OwnerId,
        audit_event: TeamAuditEvent,
    ) -> TeamMutationResult: ...

    def create_project(
        self, project: TeamProject, audit_event: TeamAuditEvent
    ) -> TeamMutationResult: ...

    def get_project(self, workspace_id: WorkspaceId, project_id: ProjectId) -> TeamProject: ...

    def set_project_visibility(
        self,
        project: TeamProject,
        *,
        expected: TeamProject,
        audit_event: TeamAuditEvent,
    ) -> TeamMutationResult: ...

    def get_project_membership(
        self, workspace_id: WorkspaceId, project_id: ProjectId, principal_id: OwnerId
    ) -> ProjectMembership: ...

    def set_project_membership(
        self,
        membership: ProjectMembership,
        *,
        expected: ProjectMembership | None,
        audit_event: TeamAuditEvent,
    ) -> TeamMutationResult: ...

    def get_audit_event(self, workspace_id: WorkspaceId, event_id: EventId) -> TeamAuditEvent: ...

    def list_audit_events(
        self, workspace_id: WorkspaceId, *, offset: int = 0, limit: int = 50
    ) -> TeamAuditPage: ...


class ReferenceTeamControlPlaneRepository:
    """Thread-safe in-memory executable specification for team authority mutations."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._workspaces: dict[WorkspaceId, TeamWorkspace] = {}
        self._workspace_memberships: dict[tuple[WorkspaceId, OwnerId], WorkspaceMembership] = {}
        self._projects: dict[tuple[WorkspaceId, ProjectId], TeamProject] = {}
        self._project_memberships: dict[
            tuple[WorkspaceId, ProjectId, OwnerId], ProjectMembership
        ] = {}
        self._audit_events: dict[EventId, TeamAuditEvent] = {}
        self._audit_order: dict[WorkspaceId, list[EventId]] = {}
        self._requests: dict[tuple[WorkspaceId, RequestId], tuple[EventId, tuple[object, ...]]] = {}

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
        with self._lock:
            fingerprint = ("create_workspace", workspace, owner_membership)
            if self._prepare_audit(
                audit_event,
                TeamAuditAction.WORKSPACE_CREATED,
                workspace.workspace_id,
                mutation_fingerprint=fingerprint,
            ):
                return TeamMutationResult(audit_event, True)
            if (
                owner_membership.workspace_id != workspace.workspace_id
                or owner_membership.principal_id != workspace.owner_id
                or owner_membership.role is not WorkspaceRole.OWNER
                or owner_membership.status is not MembershipStatus.ACTIVE
                or audit_event.actor_id != workspace.owner_id
                or audit_event.occurred_at != workspace.created_at
            ):
                raise TeamControlPlaneInvalidMutation(
                    "workspace creation requires its exact active owner membership and audit"
                )
            if workspace.workspace_id in self._workspaces:
                raise TeamControlPlaneConflict("workspace identity already exists")

            workspaces = dict(self._workspaces)
            memberships = dict(self._workspace_memberships)
            workspaces[workspace.workspace_id] = workspace
            memberships[(workspace.workspace_id, workspace.owner_id)] = owner_membership
            self._workspaces = workspaces
            self._workspace_memberships = memberships
            self._commit_audit(audit_event, fingerprint)
            return TeamMutationResult(audit_event, False)

    def get_workspace(self, workspace_id: WorkspaceId) -> TeamWorkspace:
        self._require_identifier(workspace_id, WorkspaceId, "workspace_id")
        with self._lock:
            workspace = self._workspaces.get(workspace_id)
            if workspace is None:
                raise TeamControlPlaneNotFound("team workspace was not found")
            return workspace

    def get_workspace_membership(
        self, workspace_id: WorkspaceId, principal_id: OwnerId
    ) -> WorkspaceMembership:
        self._require_identifier(workspace_id, WorkspaceId, "workspace_id")
        self._require_identifier(principal_id, OwnerId, "principal_id")
        with self._lock:
            membership = self._workspace_memberships.get((workspace_id, principal_id))
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
        with self._lock:
            fingerprint = ("set_workspace_membership", membership, expected)
            if self._prepare_audit(
                audit_event,
                TeamAuditAction.WORKSPACE_MEMBERSHIP_CHANGED,
                membership.workspace_id,
                subject_principal_id=membership.principal_id,
                mutation_fingerprint=fingerprint,
            ):
                return TeamMutationResult(audit_event, True)
            workspace = self._workspaces.get(membership.workspace_id)
            if workspace is None:
                raise TeamControlPlaneNotFound("team workspace was not found")
            if (
                membership.principal_id == workspace.owner_id
                or membership.role is WorkspaceRole.OWNER
            ):
                raise TeamControlPlaneInvalidMutation(
                    "workspace owner changes require atomic ownership transfer"
                )
            key = (membership.workspace_id, membership.principal_id)
            current = self._workspace_memberships.get(key)
            self._require_expected(current, expected, "workspace membership")
            if current is None and membership.status is not MembershipStatus.ACTIVE:
                raise TeamControlPlaneInvalidMutation(
                    "a new workspace membership must start active"
                )
            if membership.status is MembershipStatus.SUSPENDED and (
                any(
                    project.owner_id == membership.principal_id
                    and project.workspace_id == membership.workspace_id
                    for project in self._projects.values()
                )
                or any(
                    project_membership.workspace_id == membership.workspace_id
                    and project_membership.principal_id == membership.principal_id
                    and project_membership.status is MembershipStatus.ACTIVE
                    for project_membership in self._project_memberships.values()
                )
            ):
                raise TeamControlPlaneInvalidMutation(
                    "workspace membership cannot be suspended while project authority is active"
                )
            if current == membership:
                raise TeamControlPlaneInvalidMutation("workspace membership mutation is a no-op")

            memberships = dict(self._workspace_memberships)
            memberships[key] = membership
            self._workspace_memberships = memberships
            self._commit_audit(audit_event, fingerprint)
            return TeamMutationResult(audit_event, False)

    def transfer_workspace_ownership(
        self,
        workspace_id: WorkspaceId,
        *,
        expected_owner_id: OwnerId,
        new_owner_id: OwnerId,
        audit_event: TeamAuditEvent,
    ) -> TeamMutationResult:
        self._require_identifier(workspace_id, WorkspaceId, "workspace_id")
        self._require_identifier(expected_owner_id, OwnerId, "expected_owner_id")
        self._require_identifier(new_owner_id, OwnerId, "new_owner_id")
        with self._lock:
            fingerprint = (
                "transfer_workspace_ownership",
                workspace_id,
                expected_owner_id,
                new_owner_id,
            )
            if self._prepare_audit(
                audit_event,
                TeamAuditAction.WORKSPACE_OWNERSHIP_TRANSFERRED,
                workspace_id,
                subject_principal_id=new_owner_id,
                mutation_fingerprint=fingerprint,
            ):
                return TeamMutationResult(audit_event, True)
            workspace = self._workspaces.get(workspace_id)
            if workspace is None:
                raise TeamControlPlaneNotFound("team workspace was not found")
            if (
                workspace.owner_id != expected_owner_id
                or audit_event.actor_id != expected_owner_id
                or new_owner_id == expected_owner_id
            ):
                raise TeamControlPlaneConflict("workspace ownership precondition failed")
            current_owner = self._workspace_memberships.get((workspace_id, expected_owner_id))
            new_owner = self._workspace_memberships.get((workspace_id, new_owner_id))
            if (
                current_owner is None
                or current_owner.role is not WorkspaceRole.OWNER
                or current_owner.status is not MembershipStatus.ACTIVE
                or new_owner is None
                or new_owner.status is not MembershipStatus.ACTIVE
                or new_owner.role is WorkspaceRole.OWNER
            ):
                raise TeamControlPlaneInvalidMutation(
                    "ownership transfer requires one current owner and one active member"
                )

            workspaces = dict(self._workspaces)
            memberships = dict(self._workspace_memberships)
            workspaces[workspace_id] = replace(workspace, owner_id=new_owner_id)
            memberships[(workspace_id, expected_owner_id)] = replace(
                current_owner, role=WorkspaceRole.ADMIN
            )
            memberships[(workspace_id, new_owner_id)] = replace(new_owner, role=WorkspaceRole.OWNER)
            self._workspaces = workspaces
            self._workspace_memberships = memberships
            self._commit_audit(audit_event, fingerprint)
            return TeamMutationResult(audit_event, False)

    def create_project(
        self, project: TeamProject, audit_event: TeamAuditEvent
    ) -> TeamMutationResult:
        if not isinstance(project, TeamProject):
            raise TypeError("project must be a TeamProject")
        with self._lock:
            fingerprint = ("create_project", project)
            if self._prepare_audit(
                audit_event,
                TeamAuditAction.PROJECT_CREATED,
                project.workspace_id,
                project_id=project.project_id,
                mutation_fingerprint=fingerprint,
            ):
                return TeamMutationResult(audit_event, True)
            if project.workspace_id not in self._workspaces:
                raise TeamControlPlaneNotFound("team workspace was not found")
            owner = self._workspace_memberships.get((project.workspace_id, project.owner_id))
            if owner is None or owner.status is not MembershipStatus.ACTIVE:
                raise TeamControlPlaneInvalidMutation(
                    "project owner must be an active workspace member"
                )
            key = (project.workspace_id, project.project_id)
            if key in self._projects:
                raise TeamControlPlaneConflict("team project identity already exists")

            projects = dict(self._projects)
            projects[key] = project
            self._projects = projects
            self._commit_audit(audit_event, fingerprint)
            return TeamMutationResult(audit_event, False)

    def get_project(self, workspace_id: WorkspaceId, project_id: ProjectId) -> TeamProject:
        self._require_identifier(workspace_id, WorkspaceId, "workspace_id")
        self._require_identifier(project_id, ProjectId, "project_id")
        with self._lock:
            project = self._projects.get((workspace_id, project_id))
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
        with self._lock:
            fingerprint = ("set_project_visibility", project, expected)
            if self._prepare_audit(
                audit_event,
                TeamAuditAction.PROJECT_VISIBILITY_CHANGED,
                project.workspace_id,
                project_id=project.project_id,
                mutation_fingerprint=fingerprint,
            ):
                return TeamMutationResult(audit_event, True)
            key = (project.workspace_id, project.project_id)
            current = self._projects.get(key)
            if current is None:
                raise TeamControlPlaneNotFound("team project was not found")
            if current != expected:
                raise TeamControlPlaneConflict("team project changed since it was read")
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

            projects = dict(self._projects)
            projects[key] = project
            self._projects = projects
            self._commit_audit(audit_event, fingerprint)
            return TeamMutationResult(audit_event, False)

    def get_project_membership(
        self, workspace_id: WorkspaceId, project_id: ProjectId, principal_id: OwnerId
    ) -> ProjectMembership:
        self._require_identifier(workspace_id, WorkspaceId, "workspace_id")
        self._require_identifier(project_id, ProjectId, "project_id")
        self._require_identifier(principal_id, OwnerId, "principal_id")
        with self._lock:
            membership = self._project_memberships.get((workspace_id, project_id, principal_id))
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
        with self._lock:
            fingerprint = ("set_project_membership", membership, expected)
            if self._prepare_audit(
                audit_event,
                TeamAuditAction.PROJECT_MEMBERSHIP_CHANGED,
                membership.workspace_id,
                project_id=membership.project_id,
                subject_principal_id=membership.principal_id,
                mutation_fingerprint=fingerprint,
            ):
                return TeamMutationResult(audit_event, True)
            if (membership.workspace_id, membership.project_id) not in self._projects:
                raise TeamControlPlaneNotFound("team project was not found")
            workspace_membership = self._workspace_memberships.get(
                (membership.workspace_id, membership.principal_id)
            )
            if workspace_membership is None:
                raise TeamControlPlaneInvalidMutation("project member must belong to the workspace")
            key = (membership.workspace_id, membership.project_id, membership.principal_id)
            current = self._project_memberships.get(key)
            self._require_expected(current, expected, "project membership")
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

            memberships = dict(self._project_memberships)
            memberships[key] = membership
            self._project_memberships = memberships
            self._commit_audit(audit_event, fingerprint)
            return TeamMutationResult(audit_event, False)

    def get_audit_event(self, workspace_id: WorkspaceId, event_id: EventId) -> TeamAuditEvent:
        self._require_identifier(workspace_id, WorkspaceId, "workspace_id")
        self._require_identifier(event_id, EventId, "event_id")
        with self._lock:
            event = self._audit_events.get(event_id)
            if event is None or event.workspace_id != workspace_id:
                raise TeamControlPlaneNotFound("team audit event was not found")
            return event

    def list_audit_events(
        self, workspace_id: WorkspaceId, *, offset: int = 0, limit: int = 50
    ) -> TeamAuditPage:
        self._require_identifier(workspace_id, WorkspaceId, "workspace_id")
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ValueError("team audit offset must be a non-negative integer")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("team audit limit must be between 1 and 100")
        with self._lock:
            if workspace_id not in self._workspaces:
                raise TeamControlPlaneNotFound("team workspace was not found")
            ordered = self._audit_order.get(workspace_id, [])
            items = tuple(
                self._audit_events[event_id] for event_id in ordered[offset : offset + limit]
            )
            next_offset = offset + limit if offset + limit < len(ordered) else None
            return TeamAuditPage(items, next_offset)

    def _prepare_audit(
        self,
        audit_event: TeamAuditEvent,
        action: TeamAuditAction,
        workspace_id: WorkspaceId,
        *,
        project_id: ProjectId | None = None,
        subject_principal_id: OwnerId | None = None,
        mutation_fingerprint: tuple[object, ...],
    ) -> bool:
        if not isinstance(audit_event, TeamAuditEvent):
            raise TypeError("audit_event must be a TeamAuditEvent")
        if (
            audit_event.action is not action
            or audit_event.workspace_id != workspace_id
            or audit_event.project_id != project_id
            or audit_event.subject_principal_id != subject_principal_id
        ):
            raise TeamControlPlaneInvalidMutation(
                "team audit event does not match the requested mutation"
            )
        request_key = (workspace_id, audit_event.request_id)
        existing_request = self._requests.get(request_key)
        if existing_request is not None:
            existing_id, existing_fingerprint = existing_request
            existing = self._audit_events[existing_id]
            if existing != audit_event or existing_fingerprint != mutation_fingerprint:
                raise TeamControlPlaneConflict("team mutation request identity conflicts")
            return True
        if audit_event.event_id in self._audit_events:
            raise TeamControlPlaneConflict("team audit event identity conflicts")
        return False

    def _commit_audit(
        self, audit_event: TeamAuditEvent, mutation_fingerprint: tuple[object, ...]
    ) -> None:
        events = dict(self._audit_events)
        order = {workspace_id: list(items) for workspace_id, items in self._audit_order.items()}
        requests = dict(self._requests)
        events[audit_event.event_id] = audit_event
        order.setdefault(audit_event.workspace_id, []).append(audit_event.event_id)
        requests[(audit_event.workspace_id, audit_event.request_id)] = (
            audit_event.event_id,
            mutation_fingerprint,
        )
        self._audit_events = events
        self._audit_order = order
        self._requests = requests

    @staticmethod
    def _require_expected[ValueT](
        current: ValueT | None, expected: ValueT | None, name: str
    ) -> None:
        if current != expected:
            raise TeamControlPlaneConflict(f"{name} changed since it was read")

    @staticmethod
    def _require_identifier[IdentifierT](value: object, kind: type[IdentifierT], name: str) -> None:
        if not isinstance(value, kind):
            raise TypeError(f"{name} must be a {kind.__name__}")
