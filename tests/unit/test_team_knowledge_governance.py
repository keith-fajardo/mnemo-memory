"""Team knowledge governance stays content-free and conflict-bound."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from mnemo_memory.apps.mcp.team import PostgreSQLTeamMcpPort
from mnemo_memory.packages.application import (
    ApproveTeamKnowledgeSource,
    TeamKnowledgeGovernanceApplicationService,
)
from mnemo_memory.packages.domain import (
    KnowledgeDocumentId,
    KnowledgeDocumentRevisionId,
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    TeamKnowledgeSourceApproval,
    TeamKnowledgeSourceStatus,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.storage import TeamKnowledgeSourceApprovalResult

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def _scope() -> MemoryScope:
    return MemoryScope(
        OwnerId.new(),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        workspace_id=WorkspaceId.new(),
        project_id=ProjectId.new(),
    )


class _GovernanceRepository:
    def __init__(self, status: TeamKnowledgeSourceStatus) -> None:
        self.status = status
        self.approval: TeamKnowledgeSourceApproval | None = None

    def list_team_knowledge_sources(
        self, scope: MemoryScope, *, limit: int = 100
    ) -> tuple[TeamKnowledgeSourceStatus, ...]:
        assert scope == self.status.scope and limit == 10
        return (replace(self.status, approval=self.approval),)

    def approve_team_knowledge_source(
        self, approval: TeamKnowledgeSourceApproval
    ) -> TeamKnowledgeSourceApprovalResult:
        if self.approval is None:
            self.approval = approval
            return TeamKnowledgeSourceApprovalResult(approval, False)
        assert self.approval.same_intent(approval)
        return TeamKnowledgeSourceApprovalResult(self.approval, True)


class _ContextPort:
    def get_context(self, request: dict[str, object]) -> dict[str, object]:
        return {}

    def structural_lookup(self, request: dict[str, object]) -> dict[str, object]:
        return {}

    def dbt_structure(self, request: dict[str, object]) -> dict[str, object]:
        return {}

    def list_skills(self, request: dict[str, object]) -> dict[str, object]:
        return {}

    def get_skill(self, request: dict[str, object]) -> dict[str, object]:
        return {}

    def save_checkpoint(self, request: dict[str, object]) -> dict[str, object]:
        return {}

    def extract_episodic(self, request: dict[str, object]) -> dict[str, object]:
        return {"status": "extraction_disabled"}

    def submit_episodic_candidates(self, request: dict[str, object]) -> dict[str, object]:
        return {"status": "extraction_disabled"}


def test_source_approval_round_trips_and_rejects_identity_tampering() -> None:
    scope = _scope()
    approval = TeamKnowledgeSourceApproval.create(
        scope=scope,
        document_id=KnowledgeDocumentId.new(),
        expected_revision_id=KnowledgeDocumentRevisionId.new(),
        approved_by_id=OwnerId.new(),
        source_action_key="review:decision-source",
        approved_at=NOW,
    )

    assert TeamKnowledgeSourceApproval.from_dict(approval.to_dict()) == approval
    with pytest.raises(ValueError, match="identity"):
        replace(approval, approval_id=type(approval.approval_id).new())


def test_application_and_team_transport_return_only_source_governance_metadata() -> None:
    scope = _scope()
    document_id = KnowledgeDocumentId.new()
    revision_id = KnowledgeDocumentRevisionId.new()
    author = OwnerId.new()
    status = TeamKnowledgeSourceStatus(
        scope,
        document_id,
        "Architecture/decision.md",
        revision_id,
        2,
        scope.owner_id,
        True,
        author,
        True,
        None,
    )
    repository = _GovernanceRepository(status)
    service = TeamKnowledgeGovernanceApplicationService(repository, clock=lambda: NOW)
    transport = PostgreSQLTeamMcpPort(
        _ContextPort(),
        service,
        principal_id=author,
        workspace_id=scope.workspace_id or WorkspaceId.new(),
    )
    request: dict[str, object] = {
        "owner_id": str(scope.owner_id),
        "workspace_id": str(scope.workspace_id),
        "project_id": str(scope.project_id),
        "visibility": scope.visibility.value,
        "limit": 10,
    }

    listed = transport.list_knowledge_sources(request)
    assert listed == {"sources": [status.to_dict()]}
    assert "content" not in str(listed).lower()
    approved = transport.approve_knowledge_source(
        {
            **request,
            "document_id": str(document_id),
            "expected_revision_id": str(revision_id),
            "source_action_key": "approve-source",
        }
    )
    assert approved["idempotent"] is False
    assert (
        transport.approve_knowledge_source(
            {
                **request,
                "document_id": str(document_id),
                "expected_revision_id": str(revision_id),
                "source_action_key": "approve-source",
            }
        )["idempotent"]
        is True
    )
    assert service.list_sources(scope, limit=10)[0].approved


def test_approval_command_keeps_the_exact_expected_revision() -> None:
    scope = _scope()
    status = TeamKnowledgeSourceStatus(
        scope,
        KnowledgeDocumentId.new(),
        "runbook.md",
        KnowledgeDocumentRevisionId.new(),
        1,
        scope.owner_id,
        True,
        scope.owner_id,
        True,
        None,
    )
    repository = _GovernanceRepository(status)
    service = TeamKnowledgeGovernanceApplicationService(repository, clock=lambda: NOW)

    result = service.approve_source(
        ApproveTeamKnowledgeSource(
            scope,
            status.document_id,
            status.current_revision_id,
            scope.owner_id,
            "approve-runbook",
        )
    )

    assert result.approval.expected_revision_id == status.current_revision_id
    assert result.approval.approved_by_id == scope.owner_id
