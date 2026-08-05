"""Cross-tenant tests for the pure team authorization kernel."""

from __future__ import annotations

from collections.abc import Iterable

import pytest

from mnemo_memory.packages.domain import (
    MembershipStatus,
    MemoryScope,
    OwnerId,
    ProjectId,
    ProjectMembership,
    ProjectRole,
    ScopeLevel,
    TeamProject,
    TeamProjectVisibility,
    Visibility,
    WorkspaceId,
    WorkspaceMembership,
    WorkspaceRole,
)
from mnemo_memory.packages.policy import (
    TeamAuthorizationDecision,
    TeamAuthorizationPolicy,
    TeamAuthorizationReason,
    TeamAuthorizationRequest,
    TeamOperation,
)

POLICY = TeamAuthorizationPolicy()


def workspace_membership(
    workspace_id: WorkspaceId,
    principal_id: OwnerId,
    role: WorkspaceRole,
    status: MembershipStatus = MembershipStatus.ACTIVE,
) -> WorkspaceMembership:
    return WorkspaceMembership(workspace_id, principal_id, role, status)


def project_scope(
    owner_id: OwnerId,
    workspace_id: WorkspaceId,
    project_id: ProjectId,
    visibility: Visibility = Visibility.PROJECT,
) -> MemoryScope:
    return MemoryScope(
        owner_id,
        ScopeLevel.PROJECT,
        visibility,
        workspace_id=workspace_id,
        project_id=project_id,
    )


def request(
    principal_id: OwnerId,
    operation: TeamOperation,
    scope: MemoryScope,
    membership: WorkspaceMembership | None,
    *,
    project: TeamProject | None = None,
    project_membership: ProjectMembership | None = None,
) -> TeamAuthorizationRequest:
    return TeamAuthorizationRequest(
        principal_id,
        operation,
        scope,
        membership,
        project,
        project_membership,
    )


def assert_allowed_operations(
    principal_id: OwnerId,
    scope: MemoryScope,
    membership: WorkspaceMembership,
    expected: Iterable[TeamOperation],
    *,
    project: TeamProject | None = None,
    project_membership: ProjectMembership | None = None,
) -> None:
    expected_set = frozenset(expected)
    for operation in TeamOperation:
        decision = POLICY.decide(
            request(
                principal_id,
                operation,
                scope,
                membership,
                project=project,
                project_membership=project_membership,
            )
        )
        assert decision.allowed is (operation in expected_set), (operation, decision)
        if not decision.allowed:
            assert decision.reason is TeamAuthorizationReason.ROLE_FORBIDDEN


def test_team_membership_project_and_decision_serialization_are_strict() -> None:
    workspace_id, project_id, principal_id = WorkspaceId.new(), ProjectId.new(), OwnerId.new()
    membership = workspace_membership(workspace_id, principal_id, WorkspaceRole.ADMIN)
    project_membership = ProjectMembership(
        workspace_id,
        project_id,
        principal_id,
        ProjectRole.MAINTAINER,
        MembershipStatus.ACTIVE,
    )
    project = TeamProject(workspace_id, project_id, principal_id, TeamProjectVisibility.PRIVATE)
    decision = TeamAuthorizationDecision(True, TeamAuthorizationReason.WORKSPACE_ROLE_ALLOWED)

    assert WorkspaceMembership.from_dict(membership.to_dict()) == membership
    assert ProjectMembership.from_dict(project_membership.to_dict()) == project_membership
    assert TeamProject.from_dict(project.to_dict()) == project
    assert TeamAuthorizationDecision.from_dict(decision.to_dict()) == decision

    invalid = membership.to_dict()
    invalid["unknown"] = "field"
    with pytest.raises(ValueError, match="fields are invalid"):
        WorkspaceMembership.from_dict(invalid)
    wrong_type = membership.to_dict()
    wrong_type["workspace_id"] = 42  # type: ignore[assignment]
    with pytest.raises(TypeError, match="workspace_id must be a string"):
        WorkspaceMembership.from_dict(wrong_type)
    with pytest.raises(ValueError):
        TeamAuthorizationDecision.from_dict({"allowed": True, "reason": "wildcard"})


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (WorkspaceRole.OWNER, frozenset(TeamOperation)),
        (
            WorkspaceRole.ADMIN,
            frozenset(TeamOperation) - {TeamOperation.MANAGE_WORKSPACE},
        ),
        (
            WorkspaceRole.EDITOR,
            frozenset({TeamOperation.READ, TeamOperation.CONTRIBUTE}),
        ),
        (WorkspaceRole.VIEWER, frozenset({TeamOperation.READ})),
    ],
)
def test_workspace_role_matrix_is_closed(
    role: WorkspaceRole, expected: frozenset[TeamOperation]
) -> None:
    principal_id, workspace_id = OwnerId.new(), WorkspaceId.new()
    scope = MemoryScope(
        principal_id,
        ScopeLevel.WORKSPACE,
        Visibility.WORKSPACE,
        workspace_id=workspace_id,
    )
    assert_allowed_operations(
        principal_id,
        scope,
        workspace_membership(workspace_id, principal_id, role),
        expected,
    )


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (
            ProjectRole.MAINTAINER,
            frozenset(
                {
                    TeamOperation.READ,
                    TeamOperation.CONTRIBUTE,
                    TeamOperation.MANAGE_PROJECT,
                    TeamOperation.APPROVE_SOURCE,
                }
            ),
        ),
        (
            ProjectRole.CONTRIBUTOR,
            frozenset({TeamOperation.READ, TeamOperation.CONTRIBUTE}),
        ),
        (ProjectRole.VIEWER, frozenset({TeamOperation.READ})),
    ],
)
def test_private_project_role_matrix_is_closed(
    role: ProjectRole, expected: frozenset[TeamOperation]
) -> None:
    principal_id, workspace_id, project_id = OwnerId.new(), WorkspaceId.new(), ProjectId.new()
    scope = project_scope(principal_id, workspace_id, project_id)
    membership = workspace_membership(workspace_id, principal_id, WorkspaceRole.EDITOR)
    project = TeamProject(workspace_id, project_id, OwnerId.new(), TeamProjectVisibility.PRIVATE)
    project_member = ProjectMembership(
        workspace_id, project_id, principal_id, role, MembershipStatus.ACTIVE
    )
    project_operations = expected | frozenset()
    for operation in TeamOperation:
        decision = POLICY.decide(
            request(
                principal_id,
                operation,
                scope,
                membership,
                project=project,
                project_membership=project_member,
            )
        )
        if operation in {TeamOperation.MANAGE_MEMBERSHIP, TeamOperation.MANAGE_WORKSPACE}:
            assert decision == TeamAuthorizationDecision(
                False, TeamAuthorizationReason.ROLE_FORBIDDEN
            )
        else:
            assert decision.allowed is (operation in project_operations)


def test_cross_tenant_and_inactive_claims_fail_before_role_evaluation() -> None:
    principal_id, other_principal = OwnerId.new(), OwnerId.new()
    workspace_id, other_workspace = WorkspaceId.new(), WorkspaceId.new()
    project_id, other_project = ProjectId.new(), ProjectId.new()
    scope = project_scope(principal_id, workspace_id, project_id)
    membership = workspace_membership(workspace_id, principal_id, WorkspaceRole.EDITOR)
    project = TeamProject(workspace_id, project_id, other_principal, TeamProjectVisibility.PRIVATE)

    cases = (
        (
            request(
                principal_id,
                TeamOperation.READ,
                MemoryScope(principal_id, ScopeLevel.PERSONAL, Visibility.OWNER),
                None,
            ),
            TeamAuthorizationReason.TEAM_SCOPE_REQUIRED,
        ),
        (
            request(principal_id, TeamOperation.READ, scope, None),
            TeamAuthorizationReason.WORKSPACE_MEMBERSHIP_REQUIRED,
        ),
        (
            request(
                principal_id,
                TeamOperation.READ,
                scope,
                workspace_membership(other_workspace, principal_id, WorkspaceRole.OWNER),
            ),
            TeamAuthorizationReason.WORKSPACE_MEMBERSHIP_MISMATCH,
        ),
        (
            request(
                principal_id,
                TeamOperation.READ,
                scope,
                workspace_membership(workspace_id, other_principal, WorkspaceRole.OWNER),
            ),
            TeamAuthorizationReason.WORKSPACE_MEMBERSHIP_MISMATCH,
        ),
        (
            request(
                principal_id,
                TeamOperation.READ,
                scope,
                workspace_membership(
                    workspace_id,
                    principal_id,
                    WorkspaceRole.OWNER,
                    MembershipStatus.SUSPENDED,
                ),
            ),
            TeamAuthorizationReason.WORKSPACE_MEMBERSHIP_INACTIVE,
        ),
        (
            request(principal_id, TeamOperation.READ, scope, membership),
            TeamAuthorizationReason.PROJECT_REQUIRED,
        ),
        (
            request(
                principal_id,
                TeamOperation.READ,
                scope,
                membership,
                project=TeamProject(
                    other_workspace,
                    project_id,
                    principal_id,
                    TeamProjectVisibility.PRIVATE,
                ),
            ),
            TeamAuthorizationReason.PROJECT_MISMATCH,
        ),
        (
            request(
                principal_id,
                TeamOperation.READ,
                scope,
                membership,
                project=TeamProject(
                    workspace_id,
                    other_project,
                    principal_id,
                    TeamProjectVisibility.PRIVATE,
                ),
            ),
            TeamAuthorizationReason.PROJECT_MISMATCH,
        ),
        (
            request(
                principal_id,
                TeamOperation.READ,
                scope,
                membership,
                project=project,
            ),
            TeamAuthorizationReason.PROJECT_MEMBERSHIP_REQUIRED,
        ),
        (
            request(
                principal_id,
                TeamOperation.READ,
                scope,
                membership,
                project=project,
                project_membership=ProjectMembership(
                    workspace_id,
                    other_project,
                    principal_id,
                    ProjectRole.MAINTAINER,
                    MembershipStatus.ACTIVE,
                ),
            ),
            TeamAuthorizationReason.PROJECT_MEMBERSHIP_MISMATCH,
        ),
        (
            request(
                principal_id,
                TeamOperation.READ,
                scope,
                membership,
                project=project,
                project_membership=ProjectMembership(
                    workspace_id,
                    project_id,
                    principal_id,
                    ProjectRole.MAINTAINER,
                    MembershipStatus.SUSPENDED,
                ),
            ),
            TeamAuthorizationReason.PROJECT_MEMBERSHIP_INACTIVE,
        ),
    )
    for attempted, reason in cases:
        assert POLICY.decide(attempted) == TeamAuthorizationDecision(False, reason)


def test_owner_only_items_and_private_projects_have_no_admin_visibility_bypass() -> None:
    item_owner, admin = OwnerId.new(), OwnerId.new()
    workspace_id, project_id = WorkspaceId.new(), ProjectId.new()
    scope = project_scope(item_owner, workspace_id, project_id, Visibility.OWNER)
    project = TeamProject(workspace_id, project_id, item_owner, TeamProjectVisibility.PRIVATE)

    denied = POLICY.decide(
        request(
            admin,
            TeamOperation.READ,
            scope,
            workspace_membership(workspace_id, admin, WorkspaceRole.OWNER),
            project=project,
        )
    )
    assert denied == TeamAuthorizationDecision(
        False, TeamAuthorizationReason.OWNER_VISIBILITY_REQUIRED
    )

    owner_membership = workspace_membership(workspace_id, item_owner, WorkspaceRole.VIEWER)
    assert POLICY.decide(
        request(item_owner, TeamOperation.READ, scope, owner_membership, project=project)
    ).allowed
    assert POLICY.decide(
        request(item_owner, TeamOperation.CONTRIBUTE, scope, owner_membership, project=project)
    ) == TeamAuthorizationDecision(False, TeamAuthorizationReason.ROLE_FORBIDDEN)


def test_workspace_visible_project_uses_workspace_role_without_project_grant() -> None:
    editor, workspace_id, project_id = OwnerId.new(), WorkspaceId.new(), ProjectId.new()
    scope = project_scope(editor, workspace_id, project_id)
    project = TeamProject(workspace_id, project_id, OwnerId.new(), TeamProjectVisibility.WORKSPACE)
    membership = workspace_membership(workspace_id, editor, WorkspaceRole.EDITOR)

    read = request(editor, TeamOperation.READ, scope, membership, project=project)
    contribute = request(editor, TeamOperation.CONTRIBUTE, scope, membership, project=project)
    manage = request(editor, TeamOperation.MANAGE_PROJECT, scope, membership, project=project)
    assert POLICY.decide(read) == POLICY.decide(read)
    assert POLICY.decide(read).allowed
    assert POLICY.decide(contribute).allowed
    assert POLICY.decide(manage) == TeamAuthorizationDecision(
        False, TeamAuthorizationReason.ROLE_FORBIDDEN
    )
