"""Pure team-workspace identities used by deterministic authorization policy."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Self

from .identifiers import Identifier, OwnerId, ProjectId, WorkspaceId


def _strict_fields(value: Mapping[str, object], expected: set[str], name: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ValueError(
            f"{name} fields are invalid; unknown={sorted(actual - expected)}, "
            f"missing={sorted(expected - actual)}"
        )


def _identifier[IdentifierT: Identifier](
    value: object, kind: type[IdentifierT], name: str
) -> IdentifierT:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    return kind.from_string(value)


def _string(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    return value


class MembershipStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class WorkspaceRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"


class ProjectRole(StrEnum):
    MAINTAINER = "maintainer"
    CONTRIBUTOR = "contributor"
    VIEWER = "viewer"


class TeamProjectVisibility(StrEnum):
    PRIVATE = "private"
    WORKSPACE = "workspace"


@dataclass(frozen=True, slots=True)
class WorkspaceMembership:
    workspace_id: WorkspaceId
    principal_id: OwnerId
    role: WorkspaceRole
    status: MembershipStatus

    def __post_init__(self) -> None:
        if not isinstance(self.workspace_id, WorkspaceId):
            raise TypeError("workspace_id must be a WorkspaceId")
        if not isinstance(self.principal_id, OwnerId):
            raise TypeError("principal_id must be an OwnerId")
        if not isinstance(self.role, WorkspaceRole):
            raise TypeError("role must be a WorkspaceRole")
        if not isinstance(self.status, MembershipStatus):
            raise TypeError("status must be a MembershipStatus")

    def to_dict(self) -> dict[str, str]:
        return {
            "workspace_id": str(self.workspace_id),
            "principal_id": str(self.principal_id),
            "role": self.role.value,
            "status": self.status.value,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        _strict_fields(value, {"workspace_id", "principal_id", "role", "status"}, "membership")
        return cls(
            workspace_id=_identifier(value["workspace_id"], WorkspaceId, "workspace_id"),
            principal_id=_identifier(value["principal_id"], OwnerId, "principal_id"),
            role=WorkspaceRole(_string(value["role"], "role")),
            status=MembershipStatus(_string(value["status"], "status")),
        )


@dataclass(frozen=True, slots=True)
class ProjectMembership:
    workspace_id: WorkspaceId
    project_id: ProjectId
    principal_id: OwnerId
    role: ProjectRole
    status: MembershipStatus

    def __post_init__(self) -> None:
        if not isinstance(self.workspace_id, WorkspaceId):
            raise TypeError("workspace_id must be a WorkspaceId")
        if not isinstance(self.project_id, ProjectId):
            raise TypeError("project_id must be a ProjectId")
        if not isinstance(self.principal_id, OwnerId):
            raise TypeError("principal_id must be an OwnerId")
        if not isinstance(self.role, ProjectRole):
            raise TypeError("role must be a ProjectRole")
        if not isinstance(self.status, MembershipStatus):
            raise TypeError("status must be a MembershipStatus")

    def to_dict(self) -> dict[str, str]:
        return {
            "workspace_id": str(self.workspace_id),
            "project_id": str(self.project_id),
            "principal_id": str(self.principal_id),
            "role": self.role.value,
            "status": self.status.value,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        _strict_fields(
            value,
            {"workspace_id", "project_id", "principal_id", "role", "status"},
            "project membership",
        )
        return cls(
            workspace_id=_identifier(value["workspace_id"], WorkspaceId, "workspace_id"),
            project_id=_identifier(value["project_id"], ProjectId, "project_id"),
            principal_id=_identifier(value["principal_id"], OwnerId, "principal_id"),
            role=ProjectRole(_string(value["role"], "role")),
            status=MembershipStatus(_string(value["status"], "status")),
        )


@dataclass(frozen=True, slots=True)
class TeamProject:
    workspace_id: WorkspaceId
    project_id: ProjectId
    owner_id: OwnerId
    visibility: TeamProjectVisibility

    def __post_init__(self) -> None:
        if not isinstance(self.workspace_id, WorkspaceId):
            raise TypeError("workspace_id must be a WorkspaceId")
        if not isinstance(self.project_id, ProjectId):
            raise TypeError("project_id must be a ProjectId")
        if not isinstance(self.owner_id, OwnerId):
            raise TypeError("owner_id must be an OwnerId")
        if not isinstance(self.visibility, TeamProjectVisibility):
            raise TypeError("visibility must be a TeamProjectVisibility")

    def to_dict(self) -> dict[str, str]:
        return {
            "workspace_id": str(self.workspace_id),
            "project_id": str(self.project_id),
            "owner_id": str(self.owner_id),
            "visibility": self.visibility.value,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        _strict_fields(value, {"workspace_id", "project_id", "owner_id", "visibility"}, "project")
        return cls(
            workspace_id=_identifier(value["workspace_id"], WorkspaceId, "workspace_id"),
            project_id=_identifier(value["project_id"], ProjectId, "project_id"),
            owner_id=_identifier(value["owner_id"], OwnerId, "owner_id"),
            visibility=TeamProjectVisibility(_string(value["visibility"], "visibility")),
        )
