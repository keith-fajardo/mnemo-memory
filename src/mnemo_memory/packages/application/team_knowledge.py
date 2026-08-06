"""Application boundary for content-free team knowledge source governance."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from mnemo_memory.packages.domain import (
    KnowledgeDocumentId,
    KnowledgeDocumentRevisionId,
    MemoryScope,
    OwnerId,
    TeamKnowledgeSourceApproval,
    TeamKnowledgeSourceStatus,
)
from mnemo_memory.packages.storage import (
    InvalidKnowledgeDocumentScope,
    KnowledgeDocumentConflict,
    KnowledgeDocumentStorageFailure,
    TeamKnowledgeGovernanceRepository,
    TeamKnowledgeSourceApprovalResult,
)


class TeamKnowledgeGovernanceApplicationError(Exception):
    """Stable payload-free team knowledge governance outcome."""


class TeamKnowledgeGovernanceInvalidScope(TeamKnowledgeGovernanceApplicationError):
    pass


class TeamKnowledgeGovernanceConflict(TeamKnowledgeGovernanceApplicationError):
    pass


class TeamKnowledgeGovernanceStorageFailure(TeamKnowledgeGovernanceApplicationError):
    pass


@dataclass(frozen=True, slots=True)
class ApproveTeamKnowledgeSource:
    scope: MemoryScope
    document_id: KnowledgeDocumentId
    expected_revision_id: KnowledgeDocumentRevisionId
    actor_id: OwnerId
    source_action_key: str


class TeamKnowledgeGovernanceApplicationService:
    def __init__(
        self,
        repository: TeamKnowledgeGovernanceRepository,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._clock = clock

    def list_sources(
        self, scope: MemoryScope, *, limit: int = 100
    ) -> tuple[TeamKnowledgeSourceStatus, ...]:
        try:
            return self._repository.list_team_knowledge_sources(scope, limit=limit)
        except InvalidKnowledgeDocumentScope as error:
            raise TeamKnowledgeGovernanceInvalidScope("team knowledge scope is invalid") from error
        except KnowledgeDocumentConflict as error:
            raise TeamKnowledgeGovernanceConflict(
                "team knowledge source request conflicts"
            ) from error
        except KnowledgeDocumentStorageFailure as error:
            raise TeamKnowledgeGovernanceStorageFailure(
                "team knowledge storage is unavailable"
            ) from error

    def approve_source(
        self, command: ApproveTeamKnowledgeSource
    ) -> TeamKnowledgeSourceApprovalResult:
        if not isinstance(command, ApproveTeamKnowledgeSource):
            raise TypeError("command must be an ApproveTeamKnowledgeSource")
        approval = TeamKnowledgeSourceApproval.create(
            scope=command.scope,
            document_id=command.document_id,
            expected_revision_id=command.expected_revision_id,
            approved_by_id=command.actor_id,
            source_action_key=command.source_action_key,
            approved_at=self._clock(),
        )
        try:
            return self._repository.approve_team_knowledge_source(approval)
        except InvalidKnowledgeDocumentScope as error:
            raise TeamKnowledgeGovernanceInvalidScope("team knowledge scope is invalid") from error
        except KnowledgeDocumentConflict as error:
            raise TeamKnowledgeGovernanceConflict(
                "team knowledge source approval conflicts"
            ) from error
        except KnowledgeDocumentStorageFailure as error:
            raise TeamKnowledgeGovernanceStorageFailure(
                "team knowledge storage is unavailable"
            ) from error
