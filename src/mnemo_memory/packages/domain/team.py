"""Pure team-workspace identities used by deterministic authorization policy."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID, uuid5

from .identifiers import EventId, Identifier, OwnerId, ProjectId, RequestId, WorkspaceId

_TEAM_AUDIT_NAMESPACE = UUID("a3fa455b-8548-479a-a1c9-f99ac51622a3")


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


def _optional_identifier[IdentifierT: Identifier](
    value: object, kind: type[IdentifierT], name: str
) -> IdentifierT | None:
    if value is None:
        return None
    return _identifier(value, kind, name)


def _require_aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _datetime(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{name} must be a valid ISO-8601 timestamp") from error
    _require_aware(parsed, name)
    return parsed


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


class TeamAuditAction(StrEnum):
    WORKSPACE_CREATED = "workspace_created"
    WORKSPACE_MEMBERSHIP_CHANGED = "workspace_membership_changed"
    WORKSPACE_OWNERSHIP_TRANSFERRED = "workspace_ownership_transferred"
    PROJECT_CREATED = "project_created"
    PROJECT_VISIBILITY_CHANGED = "project_visibility_changed"
    PROJECT_MEMBERSHIP_CHANGED = "project_membership_changed"


@dataclass(frozen=True, slots=True)
class TeamWorkspace:
    workspace_id: WorkspaceId
    owner_id: OwnerId
    created_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.workspace_id, WorkspaceId):
            raise TypeError("workspace_id must be a WorkspaceId")
        if not isinstance(self.owner_id, OwnerId):
            raise TypeError("owner_id must be an OwnerId")
        _require_aware(self.created_at, "created_at")

    def to_dict(self) -> dict[str, str]:
        return {
            "workspace_id": str(self.workspace_id),
            "owner_id": str(self.owner_id),
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        _strict_fields(value, {"workspace_id", "owner_id", "created_at"}, "workspace")
        return cls(
            workspace_id=_identifier(value["workspace_id"], WorkspaceId, "workspace_id"),
            owner_id=_identifier(value["owner_id"], OwnerId, "owner_id"),
            created_at=_datetime(value["created_at"], "created_at"),
        )


@dataclass(frozen=True, slots=True)
class TeamAuditEvent:
    """Payload-free record of one committed team authority mutation."""

    event_id: EventId
    request_id: RequestId
    workspace_id: WorkspaceId
    actor_id: OwnerId
    action: TeamAuditAction
    occurred_at: datetime
    project_id: ProjectId | None = None
    subject_principal_id: OwnerId | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, EventId):
            raise TypeError("event_id must be an EventId")
        if not isinstance(self.request_id, RequestId):
            raise TypeError("request_id must be a RequestId")
        if not isinstance(self.workspace_id, WorkspaceId):
            raise TypeError("workspace_id must be a WorkspaceId")
        if not isinstance(self.actor_id, OwnerId):
            raise TypeError("actor_id must be an OwnerId")
        if not isinstance(self.action, TeamAuditAction):
            raise TypeError("action must be a TeamAuditAction")
        _require_aware(self.occurred_at, "occurred_at")
        if self.project_id is not None and not isinstance(self.project_id, ProjectId):
            raise TypeError("project_id must be a ProjectId")
        if self.subject_principal_id is not None and not isinstance(
            self.subject_principal_id, OwnerId
        ):
            raise TypeError("subject_principal_id must be an OwnerId")
        self._require_action_shape()

    def _require_action_shape(self) -> None:
        project_action = self.action in {
            TeamAuditAction.PROJECT_CREATED,
            TeamAuditAction.PROJECT_VISIBILITY_CHANGED,
            TeamAuditAction.PROJECT_MEMBERSHIP_CHANGED,
        }
        membership_action = self.action in {
            TeamAuditAction.WORKSPACE_MEMBERSHIP_CHANGED,
            TeamAuditAction.WORKSPACE_OWNERSHIP_TRANSFERRED,
            TeamAuditAction.PROJECT_MEMBERSHIP_CHANGED,
        }
        if (self.project_id is not None) is not project_action:
            raise ValueError("team audit project identity does not match its action")
        if (self.subject_principal_id is not None) is not membership_action:
            raise ValueError("team audit subject identity does not match its action")

    @classmethod
    def for_mutation(
        cls,
        *,
        request_id: RequestId,
        workspace_id: WorkspaceId,
        actor_id: OwnerId,
        action: TeamAuditAction,
        occurred_at: datetime,
        project_id: ProjectId | None = None,
        subject_principal_id: OwnerId | None = None,
    ) -> Self:
        identity = EventId(uuid5(_TEAM_AUDIT_NAMESPACE, f"{workspace_id}:{request_id}"))
        return cls(
            identity,
            request_id,
            workspace_id,
            actor_id,
            action,
            occurred_at,
            project_id,
            subject_principal_id,
        )

    def to_dict(self) -> dict[str, str | None]:
        return {
            "event_id": str(self.event_id),
            "request_id": str(self.request_id),
            "workspace_id": str(self.workspace_id),
            "actor_id": str(self.actor_id),
            "action": self.action.value,
            "occurred_at": self.occurred_at.isoformat(),
            "project_id": None if self.project_id is None else str(self.project_id),
            "subject_principal_id": (
                None if self.subject_principal_id is None else str(self.subject_principal_id)
            ),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        _strict_fields(
            value,
            {
                "event_id",
                "request_id",
                "workspace_id",
                "actor_id",
                "action",
                "occurred_at",
                "project_id",
                "subject_principal_id",
            },
            "team audit event",
        )
        return cls(
            event_id=_identifier(value["event_id"], EventId, "event_id"),
            request_id=_identifier(value["request_id"], RequestId, "request_id"),
            workspace_id=_identifier(value["workspace_id"], WorkspaceId, "workspace_id"),
            actor_id=_identifier(value["actor_id"], OwnerId, "actor_id"),
            action=TeamAuditAction(_string(value["action"], "action")),
            occurred_at=_datetime(value["occurred_at"], "occurred_at"),
            project_id=_optional_identifier(value["project_id"], ProjectId, "project_id"),
            subject_principal_id=_optional_identifier(
                value["subject_principal_id"], OwnerId, "subject_principal_id"
            ),
        )


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
