"""Deny-by-default team authorization evaluated before any storage read."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Self

from mnemo_memory.packages.domain import (
    MembershipStatus,
    MemoryScope,
    OwnerId,
    ProjectMembership,
    ProjectRole,
    TeamProject,
    TeamProjectVisibility,
    Visibility,
    WorkspaceMembership,
    WorkspaceRole,
)


class TeamOperation(StrEnum):
    READ = "read"
    CONTRIBUTE = "contribute"
    MANAGE_PROJECT = "manage_project"
    MANAGE_MEMBERSHIP = "manage_membership"
    MANAGE_WORKSPACE = "manage_workspace"
    APPROVE_SOURCE = "approve_source"


class TeamAuthorizationReason(StrEnum):
    WORKSPACE_ROLE_ALLOWED = "workspace_role_allowed"
    PROJECT_ROLE_ALLOWED = "project_role_allowed"
    TEAM_SCOPE_REQUIRED = "team_scope_required"
    WORKSPACE_MEMBERSHIP_REQUIRED = "workspace_membership_required"
    WORKSPACE_MEMBERSHIP_MISMATCH = "workspace_membership_mismatch"
    WORKSPACE_MEMBERSHIP_INACTIVE = "workspace_membership_inactive"
    OWNER_VISIBILITY_REQUIRED = "owner_visibility_required"
    PROJECT_REQUIRED = "project_required"
    PROJECT_MISMATCH = "project_mismatch"
    PROJECT_MEMBERSHIP_REQUIRED = "project_membership_required"
    PROJECT_MEMBERSHIP_MISMATCH = "project_membership_mismatch"
    PROJECT_MEMBERSHIP_INACTIVE = "project_membership_inactive"
    ROLE_FORBIDDEN = "role_forbidden"


@dataclass(frozen=True, slots=True)
class TeamAuthorizationDecision:
    allowed: bool
    reason: TeamAuthorizationReason

    def to_dict(self) -> dict[str, object]:
        return {"allowed": self.allowed, "reason": self.reason.value}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        if set(value) != {"allowed", "reason"}:
            raise ValueError("team authorization decision fields are invalid")
        allowed, reason = value["allowed"], value["reason"]
        if not isinstance(allowed, bool):
            raise TypeError("allowed must be a boolean")
        if not isinstance(reason, str):
            raise TypeError("reason must be a string")
        return cls(allowed, TeamAuthorizationReason(reason))


@dataclass(frozen=True, slots=True)
class TeamAuthorizationRequest:
    principal_id: OwnerId
    operation: TeamOperation
    scope: MemoryScope
    workspace_membership: WorkspaceMembership | None
    project: TeamProject | None = None
    project_membership: ProjectMembership | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.principal_id, OwnerId):
            raise TypeError("principal_id must be an OwnerId")
        if not isinstance(self.operation, TeamOperation):
            raise TypeError("operation must be a TeamOperation")
        if not isinstance(self.scope, MemoryScope):
            raise TypeError("scope must be a MemoryScope")
        if self.workspace_membership is not None and not isinstance(
            self.workspace_membership, WorkspaceMembership
        ):
            raise TypeError("workspace_membership must be a WorkspaceMembership")
        if self.project is not None and not isinstance(self.project, TeamProject):
            raise TypeError("project must be a TeamProject")
        if self.project_membership is not None and not isinstance(
            self.project_membership, ProjectMembership
        ):
            raise TypeError("project_membership must be a ProjectMembership")


_WORKSPACE_PERMISSIONS = {
    WorkspaceRole.OWNER: frozenset(TeamOperation),
    WorkspaceRole.ADMIN: frozenset(TeamOperation) - {TeamOperation.MANAGE_WORKSPACE},
    WorkspaceRole.EDITOR: frozenset({TeamOperation.READ, TeamOperation.CONTRIBUTE}),
    WorkspaceRole.VIEWER: frozenset({TeamOperation.READ}),
}
_PROJECT_PERMISSIONS = {
    ProjectRole.MAINTAINER: frozenset(
        {
            TeamOperation.READ,
            TeamOperation.CONTRIBUTE,
            TeamOperation.MANAGE_PROJECT,
            TeamOperation.APPROVE_SOURCE,
        }
    ),
    ProjectRole.CONTRIBUTOR: frozenset({TeamOperation.READ, TeamOperation.CONTRIBUTE}),
    ProjectRole.VIEWER: frozenset({TeamOperation.READ}),
}
_WORKSPACE_ONLY = frozenset({TeamOperation.MANAGE_MEMBERSHIP, TeamOperation.MANAGE_WORKSPACE})


class TeamAuthorizationPolicy:
    """Authorize one exact request without retrieving or inferring missing membership."""

    def decide(self, request: TeamAuthorizationRequest) -> TeamAuthorizationDecision:
        workspace_id = request.scope.workspace_id
        if workspace_id is None:
            return self._deny(TeamAuthorizationReason.TEAM_SCOPE_REQUIRED)
        membership = request.workspace_membership
        if membership is None:
            return self._deny(TeamAuthorizationReason.WORKSPACE_MEMBERSHIP_REQUIRED)
        if (
            membership.workspace_id != workspace_id
            or membership.principal_id != request.principal_id
        ):
            return self._deny(TeamAuthorizationReason.WORKSPACE_MEMBERSHIP_MISMATCH)
        if membership.status is not MembershipStatus.ACTIVE:
            return self._deny(TeamAuthorizationReason.WORKSPACE_MEMBERSHIP_INACTIVE)
        if (
            request.scope.visibility is Visibility.OWNER
            and request.scope.owner_id != request.principal_id
        ):
            return self._deny(TeamAuthorizationReason.OWNER_VISIBILITY_REQUIRED)

        workspace_allowed = request.operation in _WORKSPACE_PERMISSIONS[membership.role]
        if request.operation in _WORKSPACE_ONLY:
            return self._workspace_decision(workspace_allowed)

        project_id = request.scope.project_id
        if project_id is None:
            return self._workspace_decision(workspace_allowed)
        project = request.project
        if project is None:
            return self._deny(TeamAuthorizationReason.PROJECT_REQUIRED)
        if project.workspace_id != workspace_id or project.project_id != project_id:
            return self._deny(TeamAuthorizationReason.PROJECT_MISMATCH)

        if membership.role in {WorkspaceRole.OWNER, WorkspaceRole.ADMIN}:
            return self._workspace_decision(workspace_allowed)
        if project.owner_id == request.principal_id:
            return self._workspace_decision(workspace_allowed)
        if project.visibility is TeamProjectVisibility.WORKSPACE:
            return self._workspace_decision(workspace_allowed)

        project_membership = request.project_membership
        if project_membership is None:
            return self._deny(TeamAuthorizationReason.PROJECT_MEMBERSHIP_REQUIRED)
        if (
            project_membership.workspace_id != workspace_id
            or project_membership.project_id != project_id
            or project_membership.principal_id != request.principal_id
        ):
            return self._deny(TeamAuthorizationReason.PROJECT_MEMBERSHIP_MISMATCH)
        if project_membership.status is not MembershipStatus.ACTIVE:
            return self._deny(TeamAuthorizationReason.PROJECT_MEMBERSHIP_INACTIVE)
        if request.operation not in _PROJECT_PERMISSIONS[project_membership.role]:
            return self._deny(TeamAuthorizationReason.ROLE_FORBIDDEN)
        return TeamAuthorizationDecision(True, TeamAuthorizationReason.PROJECT_ROLE_ALLOWED)

    @staticmethod
    def _workspace_decision(allowed: bool) -> TeamAuthorizationDecision:
        if not allowed:
            return TeamAuthorizationPolicy._deny(TeamAuthorizationReason.ROLE_FORBIDDEN)
        return TeamAuthorizationDecision(True, TeamAuthorizationReason.WORKSPACE_ROLE_ALLOWED)

    @staticmethod
    def _deny(reason: TeamAuthorizationReason) -> TeamAuthorizationDecision:
        return TeamAuthorizationDecision(False, reason)
