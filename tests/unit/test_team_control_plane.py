"""Contract tests for atomic team authority state and payload-free audit history."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from mnemo_memory.packages.domain import (
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
from mnemo_memory.packages.storage import (
    ReferenceTeamControlPlaneRepository,
    TeamControlPlaneConflict,
    TeamControlPlaneInvalidMutation,
    TeamControlPlaneNotFound,
    TeamControlPlaneRepository,
)

NOW = datetime(2026, 8, 6, 9, 30, tzinfo=UTC)


def audit(
    workspace_id: WorkspaceId,
    actor_id: OwnerId,
    action: TeamAuditAction,
    *,
    project_id: ProjectId | None = None,
    subject_principal_id: OwnerId | None = None,
    request_id: RequestId | None = None,
    occurred_at: datetime = NOW,
) -> TeamAuditEvent:
    return TeamAuditEvent.for_mutation(
        request_id=request_id or RequestId.new(),
        workspace_id=workspace_id,
        actor_id=actor_id,
        action=action,
        occurred_at=occurred_at,
        project_id=project_id,
        subject_principal_id=subject_principal_id,
    )


def initialized_repository(
    *, owner_id: OwnerId | None = None, workspace_id: WorkspaceId | None = None
) -> tuple[ReferenceTeamControlPlaneRepository, TeamWorkspace, WorkspaceMembership]:
    repository = ReferenceTeamControlPlaneRepository()
    owner = owner_id or OwnerId.new()
    workspace = TeamWorkspace(workspace_id or WorkspaceId.new(), owner, NOW)
    membership = WorkspaceMembership(
        workspace.workspace_id,
        owner,
        WorkspaceRole.OWNER,
        MembershipStatus.ACTIVE,
    )
    event = audit(
        workspace.workspace_id,
        owner,
        TeamAuditAction.WORKSPACE_CREATED,
    )
    repository.create_workspace(workspace, membership, event)
    return repository, workspace, membership


def add_workspace_member(
    repository: TeamControlPlaneRepository,
    workspace: TeamWorkspace,
    principal_id: OwnerId,
    role: WorkspaceRole = WorkspaceRole.EDITOR,
) -> WorkspaceMembership:
    membership = WorkspaceMembership(
        workspace.workspace_id,
        principal_id,
        role,
        MembershipStatus.ACTIVE,
    )
    repository.set_workspace_membership(
        membership,
        expected=None,
        audit_event=audit(
            workspace.workspace_id,
            workspace.owner_id,
            TeamAuditAction.WORKSPACE_MEMBERSHIP_CHANGED,
            subject_principal_id=principal_id,
        ),
    )
    return membership


def test_team_workspace_and_audit_serialization_are_strict_and_payload_free() -> None:
    workspace = TeamWorkspace(WorkspaceId.new(), OwnerId.new(), NOW)
    event = audit(
        workspace.workspace_id,
        workspace.owner_id,
        TeamAuditAction.WORKSPACE_MEMBERSHIP_CHANGED,
        subject_principal_id=OwnerId.new(),
    )

    assert TeamWorkspace.from_dict(workspace.to_dict()) == workspace
    assert TeamAuditEvent.from_dict(event.to_dict()) == event
    assert (
        TeamAuditEvent.for_mutation(
            request_id=event.request_id,
            workspace_id=event.workspace_id,
            actor_id=event.actor_id,
            action=event.action,
            occurred_at=event.occurred_at,
            subject_principal_id=event.subject_principal_id,
        ).event_id
        == event.event_id
    )
    assert set(event.to_dict()) == {
        "event_id",
        "request_id",
        "workspace_id",
        "actor_id",
        "action",
        "occurred_at",
        "project_id",
        "subject_principal_id",
    }
    assert not {"content", "role", "status", "email", "token"} & set(event.to_dict())

    invalid = event.to_dict()
    invalid["payload"] = "not allowed"
    with pytest.raises(ValueError, match="fields are invalid"):
        TeamAuditEvent.from_dict(invalid)
    with pytest.raises(ValueError, match="project identity"):
        audit(
            workspace.workspace_id,
            workspace.owner_id,
            TeamAuditAction.PROJECT_CREATED,
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        TeamWorkspace(workspace.workspace_id, workspace.owner_id, NOW.replace(tzinfo=None))


def test_workspace_creation_is_atomic_idempotent_and_has_one_owner() -> None:
    repository = ReferenceTeamControlPlaneRepository()
    workspace = TeamWorkspace(WorkspaceId.new(), OwnerId.new(), NOW)
    owner = WorkspaceMembership(
        workspace.workspace_id,
        workspace.owner_id,
        WorkspaceRole.OWNER,
        MembershipStatus.ACTIVE,
    )
    event = audit(
        workspace.workspace_id,
        workspace.owner_id,
        TeamAuditAction.WORKSPACE_CREATED,
    )

    first = repository.create_workspace(workspace, owner, event)
    retry = repository.create_workspace(workspace, owner, event)
    assert not first.idempotent
    assert retry.idempotent
    assert repository.get_workspace(workspace.workspace_id) == workspace
    assert repository.get_workspace_membership(workspace.workspace_id, workspace.owner_id) == owner
    assert repository.list_audit_events(workspace.workspace_id).items == (event,)
    with pytest.raises(TeamControlPlaneConflict, match="request identity"):
        repository.create_workspace(
            workspace,
            replace(owner, status=MembershipStatus.SUSPENDED),
            event,
        )

    second_owner = WorkspaceMembership(
        workspace.workspace_id,
        OwnerId.new(),
        WorkspaceRole.OWNER,
        MembershipStatus.ACTIVE,
    )
    with pytest.raises(TeamControlPlaneInvalidMutation, match="ownership transfer"):
        repository.set_workspace_membership(
            second_owner,
            expected=None,
            audit_event=audit(
                workspace.workspace_id,
                workspace.owner_id,
                TeamAuditAction.WORKSPACE_MEMBERSHIP_CHANGED,
                subject_principal_id=second_owner.principal_id,
            ),
        )
    with pytest.raises(TeamControlPlaneConflict, match="already exists"):
        repository.create_workspace(
            workspace,
            owner,
            audit(
                workspace.workspace_id,
                workspace.owner_id,
                TeamAuditAction.WORKSPACE_CREATED,
            ),
        )
    assert repository.list_audit_events(workspace.workspace_id).items == (event,)


def test_workspace_membership_compare_and_set_rejects_stale_and_cross_workspace_state() -> None:
    repository, workspace, _ = initialized_repository()
    member_id = OwnerId.new()
    member = add_workspace_member(repository, workspace, member_id)
    suspended = replace(member, status=MembershipStatus.SUSPENDED)

    repository.set_workspace_membership(
        suspended,
        expected=member,
        audit_event=audit(
            workspace.workspace_id,
            workspace.owner_id,
            TeamAuditAction.WORKSPACE_MEMBERSHIP_CHANGED,
            subject_principal_id=member_id,
        ),
    )
    assert repository.get_workspace_membership(workspace.workspace_id, member_id) == suspended

    with pytest.raises(TeamControlPlaneConflict, match="changed since"):
        repository.set_workspace_membership(
            replace(member, role=WorkspaceRole.VIEWER),
            expected=member,
            audit_event=audit(
                workspace.workspace_id,
                workspace.owner_id,
                TeamAuditAction.WORKSPACE_MEMBERSHIP_CHANGED,
                subject_principal_id=member_id,
            ),
        )
    foreign = WorkspaceMembership(
        WorkspaceId.new(),
        OwnerId.new(),
        WorkspaceRole.VIEWER,
        MembershipStatus.ACTIVE,
    )
    with pytest.raises(TeamControlPlaneNotFound):
        repository.set_workspace_membership(
            foreign,
            expected=None,
            audit_event=audit(
                foreign.workspace_id,
                workspace.owner_id,
                TeamAuditAction.WORKSPACE_MEMBERSHIP_CHANGED,
                subject_principal_id=foreign.principal_id,
            ),
        )
    with pytest.raises(TeamControlPlaneNotFound):
        repository.get_workspace_membership(WorkspaceId.new(), member_id)


def test_workspace_ownership_transfer_updates_both_roles_atomically() -> None:
    repository, workspace, original_owner = initialized_repository()
    successor_id = OwnerId.new()
    successor = add_workspace_member(repository, workspace, successor_id, WorkspaceRole.EDITOR)
    event = audit(
        workspace.workspace_id,
        workspace.owner_id,
        TeamAuditAction.WORKSPACE_OWNERSHIP_TRANSFERRED,
        subject_principal_id=successor_id,
    )

    result = repository.transfer_workspace_ownership(
        workspace.workspace_id,
        expected_owner_id=workspace.owner_id,
        new_owner_id=successor_id,
        audit_event=event,
    )
    assert not result.idempotent
    assert repository.get_workspace(workspace.workspace_id).owner_id == successor_id
    assert repository.get_workspace_membership(
        workspace.workspace_id, original_owner.principal_id
    ) == replace(original_owner, role=WorkspaceRole.ADMIN)
    assert repository.get_workspace_membership(workspace.workspace_id, successor_id) == replace(
        successor, role=WorkspaceRole.OWNER
    )
    assert repository.transfer_workspace_ownership(
        workspace.workspace_id,
        expected_owner_id=workspace.owner_id,
        new_owner_id=successor_id,
        audit_event=event,
    ).idempotent

    stale_successor = OwnerId.new()
    with pytest.raises(TeamControlPlaneConflict, match="precondition"):
        repository.transfer_workspace_ownership(
            workspace.workspace_id,
            expected_owner_id=workspace.owner_id,
            new_owner_id=stale_successor,
            audit_event=audit(
                workspace.workspace_id,
                workspace.owner_id,
                TeamAuditAction.WORKSPACE_OWNERSHIP_TRANSFERRED,
                subject_principal_id=stale_successor,
            ),
        )


def test_project_creation_and_visibility_use_exact_workspace_and_stale_write_guards() -> None:
    repository, workspace, _ = initialized_repository()
    project = TeamProject(
        workspace.workspace_id,
        ProjectId.new(),
        workspace.owner_id,
        TeamProjectVisibility.PRIVATE,
    )
    created = audit(
        workspace.workspace_id,
        workspace.owner_id,
        TeamAuditAction.PROJECT_CREATED,
        project_id=project.project_id,
    )
    repository.create_project(project, created)
    assert repository.get_project(workspace.workspace_id, project.project_id) == project
    with pytest.raises(TeamControlPlaneNotFound):
        repository.get_project(WorkspaceId.new(), project.project_id)

    visible = replace(project, visibility=TeamProjectVisibility.WORKSPACE)
    repository.set_project_visibility(
        visible,
        expected=project,
        audit_event=audit(
            workspace.workspace_id,
            workspace.owner_id,
            TeamAuditAction.PROJECT_VISIBILITY_CHANGED,
            project_id=project.project_id,
        ),
    )
    with pytest.raises(TeamControlPlaneConflict, match="changed since"):
        repository.set_project_visibility(
            project,
            expected=project,
            audit_event=audit(
                workspace.workspace_id,
                workspace.owner_id,
                TeamAuditAction.PROJECT_VISIBILITY_CHANGED,
                project_id=project.project_id,
            ),
        )
    with pytest.raises(TeamControlPlaneInvalidMutation, match="identity or ownership"):
        repository.set_project_visibility(
            replace(visible, owner_id=OwnerId.new()),
            expected=visible,
            audit_event=audit(
                workspace.workspace_id,
                workspace.owner_id,
                TeamAuditAction.PROJECT_VISIBILITY_CHANGED,
                project_id=project.project_id,
            ),
        )


def test_project_membership_requires_exact_project_and_active_workspace_membership() -> None:
    repository, workspace, _ = initialized_repository()
    member_id = OwnerId.new()
    workspace_member = add_workspace_member(repository, workspace, member_id)
    project = TeamProject(
        workspace.workspace_id,
        ProjectId.new(),
        workspace.owner_id,
        TeamProjectVisibility.PRIVATE,
    )
    repository.create_project(
        project,
        audit(
            workspace.workspace_id,
            workspace.owner_id,
            TeamAuditAction.PROJECT_CREATED,
            project_id=project.project_id,
        ),
    )
    membership = ProjectMembership(
        workspace.workspace_id,
        project.project_id,
        member_id,
        ProjectRole.CONTRIBUTOR,
        MembershipStatus.ACTIVE,
    )
    repository.set_project_membership(
        membership,
        expected=None,
        audit_event=audit(
            workspace.workspace_id,
            workspace.owner_id,
            TeamAuditAction.PROJECT_MEMBERSHIP_CHANGED,
            project_id=project.project_id,
            subject_principal_id=member_id,
        ),
    )
    assert (
        repository.get_project_membership(workspace.workspace_id, project.project_id, member_id)
        == membership
    )

    suspended_workspace_member = replace(workspace_member, status=MembershipStatus.SUSPENDED)
    with pytest.raises(TeamControlPlaneInvalidMutation, match="project authority"):
        repository.set_workspace_membership(
            suspended_workspace_member,
            expected=workspace_member,
            audit_event=audit(
                workspace.workspace_id,
                workspace.owner_id,
                TeamAuditAction.WORKSPACE_MEMBERSHIP_CHANGED,
                subject_principal_id=member_id,
            ),
        )
    suspended_project_member = replace(membership, status=MembershipStatus.SUSPENDED)
    repository.set_project_membership(
        suspended_project_member,
        expected=membership,
        audit_event=audit(
            workspace.workspace_id,
            workspace.owner_id,
            TeamAuditAction.PROJECT_MEMBERSHIP_CHANGED,
            project_id=project.project_id,
            subject_principal_id=member_id,
        ),
    )
    assert (
        repository.get_project_membership(workspace.workspace_id, project.project_id, member_id)
        == suspended_project_member
    )
    repository.set_workspace_membership(
        suspended_workspace_member,
        expected=workspace_member,
        audit_event=audit(
            workspace.workspace_id,
            workspace.owner_id,
            TeamAuditAction.WORKSPACE_MEMBERSHIP_CHANGED,
            subject_principal_id=member_id,
        ),
    )
    with pytest.raises(TeamControlPlaneInvalidMutation, match="active workspace"):
        repository.set_project_membership(
            replace(suspended_project_member, status=MembershipStatus.ACTIVE),
            expected=suspended_project_member,
            audit_event=audit(
                workspace.workspace_id,
                workspace.owner_id,
                TeamAuditAction.PROJECT_MEMBERSHIP_CHANGED,
                project_id=project.project_id,
                subject_principal_id=member_id,
            ),
        )


def test_audit_reads_are_exact_bounded_and_do_not_disclose_another_workspace() -> None:
    repository, workspace, _ = initialized_repository()
    first_member, second_member = OwnerId.new(), OwnerId.new()
    add_workspace_member(repository, workspace, first_member)
    add_workspace_member(repository, workspace, second_member)

    first_page = repository.list_audit_events(workspace.workspace_id, limit=2)
    second_page = repository.list_audit_events(
        workspace.workspace_id,
        offset=first_page.next_offset or 0,
        limit=2,
    )
    assert len(first_page.items) == 2
    assert first_page.next_offset == 2
    assert len(second_page.items) == 1
    assert second_page.next_offset is None
    assert len({item.event_id for item in first_page.items + second_page.items}) == 3

    foreign_workspace = WorkspaceId.new()
    with pytest.raises(TeamControlPlaneNotFound):
        repository.get_audit_event(foreign_workspace, first_page.items[0].event_id)
    with pytest.raises(TeamControlPlaneNotFound):
        repository.list_audit_events(foreign_workspace)
    with pytest.raises(ValueError, match="between 1 and 100"):
        repository.list_audit_events(workspace.workspace_id, limit=101)


def test_competing_membership_updates_have_one_atomic_winner() -> None:
    repository, workspace, _ = initialized_repository()
    member_id = OwnerId.new()
    original = add_workspace_member(repository, workspace, member_id, WorkspaceRole.VIEWER)
    attempts = (
        replace(original, role=WorkspaceRole.EDITOR),
        replace(original, role=WorkspaceRole.ADMIN),
    )

    def apply(membership: WorkspaceMembership) -> str:
        try:
            repository.set_workspace_membership(
                membership,
                expected=original,
                audit_event=audit(
                    workspace.workspace_id,
                    workspace.owner_id,
                    TeamAuditAction.WORKSPACE_MEMBERSHIP_CHANGED,
                    subject_principal_id=member_id,
                ),
            )
        except TeamControlPlaneConflict:
            return "conflict"
        return "stored"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(apply, attempts))

    assert sorted(outcomes) == ["conflict", "stored"]
    assert repository.get_workspace_membership(workspace.workspace_id, member_id) in attempts
    assert len(repository.list_audit_events(workspace.workspace_id).items) == 3
