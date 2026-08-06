"""OAuth-protected, loopback-hosted Streamable HTTP MCP team service."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol, cast

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from pydantic import AnyHttpUrl

from mnemo_memory.apps.mcp.server import create_server
from mnemo_memory.packages.application.checkpoints import CheckpointApplicationService
from mnemo_memory.packages.application.dbt import DbtManifestApplicationService
from mnemo_memory.packages.application.mcp_durable import DurableMcpContextPort
from mnemo_memory.packages.application.mcp_port import McpContextPort
from mnemo_memory.packages.application.team_knowledge import (
    ApproveTeamKnowledgeSource,
    TeamKnowledgeGovernanceApplicationError,
    TeamKnowledgeGovernanceApplicationService,
    TeamKnowledgeGovernanceConflict,
    TeamKnowledgeGovernanceInvalidScope,
)
from mnemo_memory.packages.application.team_rate_limits import TeamRequestRateLimiter
from mnemo_memory.packages.application.unified_context import UnifiedContextService
from mnemo_memory.packages.context_engine import UnifiedContextEngine
from mnemo_memory.packages.domain import (
    KnowledgeDocumentId,
    KnowledgeDocumentRevisionId,
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.skills_registry import (
    KnowledgeDocumentProcedureRegistry,
    KnowledgeDocumentSkillRegistry,
)
from mnemo_memory.packages.storage import (
    PostgreSQLApprovedEpisodicEventRepository,
    PostgreSQLCheckpointRepository,
    PostgreSQLConnectionFactory,
    PostgreSQLEpisodicMemoryRepository,
    PostgreSQLKnowledgeDocumentRepository,
    PostgreSQLProjectIndexRepository,
    PostgreSQLSourceStructureRepository,
)


class TeamMcpPort(McpContextPort, Protocol):
    def list_knowledge_sources(self, request: dict[str, object]) -> dict[str, object]: ...

    def approve_knowledge_source(self, request: dict[str, object]) -> dict[str, object]: ...


class TeamMcpPortFactory(Protocol):
    def __call__(self, principal_id: OwnerId, workspace_id: WorkspaceId) -> McpContextPort: ...


class PostgreSQLTeamMcpPort:
    """Team context plus content-free source-governance transport operations."""

    def __init__(
        self,
        context: McpContextPort,
        governance: TeamKnowledgeGovernanceApplicationService,
        *,
        principal_id: OwnerId,
        workspace_id: WorkspaceId,
    ) -> None:
        self._context = context
        self._governance = governance
        self._principal_id = principal_id
        self._workspace_id = workspace_id

    def get_context(self, request: dict[str, object]) -> dict[str, object]:
        return self._context.get_context(request)

    def list_skills(self, request: dict[str, object]) -> dict[str, object]:
        return self._context.list_skills(request)

    def get_skill(self, request: dict[str, object]) -> dict[str, object]:
        return self._context.get_skill(request)

    def save_checkpoint(self, request: dict[str, object]) -> dict[str, object]:
        return self._context.save_checkpoint(request)

    def list_knowledge_sources(self, request: dict[str, object]) -> dict[str, object]:
        try:
            scope = self._scope(request)
            limit = request.get("limit", 100)
            if not isinstance(limit, int) or isinstance(limit, bool):
                raise ValueError
            return {
                "sources": [
                    item.to_dict() for item in self._governance.list_sources(scope, limit=limit)
                ]
            }
        except TeamKnowledgeGovernanceInvalidScope:
            raise ValueError("MNEMO_INVALID_SCOPE: team knowledge scope is invalid") from None
        except TeamKnowledgeGovernanceConflict:
            raise ValueError(
                "MNEMO_KNOWLEDGE_GOVERNANCE_CONFLICT: source request conflicts"
            ) from None
        except TeamKnowledgeGovernanceApplicationError:
            raise ValueError("MNEMO_STORAGE_UNAVAILABLE: team knowledge is unavailable") from None
        except (TypeError, ValueError):
            raise ValueError("MNEMO_INVALID_REQUEST: source request is invalid") from None

    def approve_knowledge_source(self, request: dict[str, object]) -> dict[str, object]:
        try:
            scope = self._scope(request)
            document = request.get("document_id")
            revision = request.get("expected_revision_id")
            action_key = request.get("source_action_key")
            if not all(isinstance(value, str) for value in (document, revision, action_key)):
                raise ValueError
            result = self._governance.approve_source(
                ApproveTeamKnowledgeSource(
                    scope,
                    KnowledgeDocumentId.from_string(str(document)),
                    KnowledgeDocumentRevisionId.from_string(str(revision)),
                    self._principal_id,
                    str(action_key),
                )
            )
            return {"approval": result.approval.to_dict(), "idempotent": result.idempotent}
        except TeamKnowledgeGovernanceInvalidScope:
            raise ValueError("MNEMO_INVALID_SCOPE: team knowledge scope is invalid") from None
        except TeamKnowledgeGovernanceConflict:
            raise ValueError(
                "MNEMO_KNOWLEDGE_GOVERNANCE_CONFLICT: source approval conflicts"
            ) from None
        except TeamKnowledgeGovernanceApplicationError:
            raise ValueError("MNEMO_STORAGE_UNAVAILABLE: team knowledge is unavailable") from None
        except (TypeError, ValueError):
            raise ValueError("MNEMO_INVALID_REQUEST: source approval is invalid") from None

    def _scope(self, request: dict[str, object]) -> MemoryScope:
        owner = request.get("owner_id")
        workspace = request.get("workspace_id")
        project = request.get("project_id")
        visibility = request.get("visibility")
        if not all(isinstance(value, str) for value in (owner, workspace, project, visibility)):
            raise ValueError
        workspace_id = WorkspaceId.from_string(str(workspace))
        if workspace_id != self._workspace_id:
            raise TeamKnowledgeGovernanceInvalidScope
        return MemoryScope(
            OwnerId.from_string(str(owner)),
            ScopeLevel.PROJECT,
            Visibility(str(visibility)),
            workspace_id=workspace_id,
            project_id=ProjectId.from_string(str(project)),
        )


class PostgreSQLTeamMcpPortFactory:
    """Compose team repositories after authentication fixes principal and workspace."""

    def __init__(
        self,
        connection_factory: PostgreSQLConnectionFactory,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._connection_factory = connection_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    def __call__(self, principal_id: OwnerId, workspace_id: WorkspaceId) -> TeamMcpPort:
        checkpoints = PostgreSQLCheckpointRepository(
            self._connection_factory,
            principal_id=principal_id,
            workspace_id=workspace_id,
        )
        approved = PostgreSQLApprovedEpisodicEventRepository(
            self._connection_factory,
            principal_id=principal_id,
            workspace_id=workspace_id,
        )
        checkpoint_service = CheckpointApplicationService(
            checkpoints,
            clock=self._clock,
            event_repository=checkpoints,
            approved_event_repository=approved,
        )
        dbt = PostgreSQLProjectIndexRepository(
            self._connection_factory,
            principal_id=principal_id,
            workspace_id=workspace_id,
        )
        source = PostgreSQLSourceStructureRepository(
            self._connection_factory,
            principal_id=principal_id,
            workspace_id=workspace_id,
        )
        knowledge = PostgreSQLKnowledgeDocumentRepository(
            self._connection_factory,
            principal_id=principal_id,
            workspace_id=workspace_id,
        )
        episodic = PostgreSQLEpisodicMemoryRepository(
            self._connection_factory,
            principal_id=principal_id,
            workspace_id=workspace_id,
        )
        skills = KnowledgeDocumentSkillRegistry(knowledge)
        context = DurableMcpContextPort(
            checkpoint_service,
            UnifiedContextEngine(
                UnifiedContextService(
                    checkpoint_service,
                    DbtManifestApplicationService(dbt),
                    source,
                    checkpoints,
                    knowledge,
                    procedures=KnowledgeDocumentProcedureRegistry(knowledge),
                    skills=skills,
                ),
                episodic,
            ),
            skills=skills,
        )
        return PostgreSQLTeamMcpPort(
            context,
            TeamKnowledgeGovernanceApplicationService(knowledge, clock=self._clock),
            principal_id=principal_id,
            workspace_id=workspace_id,
        )


class AuthenticatedTeamMcpPort:
    """Bind every tool call to the verified subject and explicit request workspace."""

    def __init__(
        self,
        factory: TeamMcpPortFactory,
        *,
        access_token_loader: Callable[[], AccessToken | None] = get_access_token,
        rate_limiter: TeamRequestRateLimiter | None = None,
    ) -> None:
        self._factory = factory
        self._access_token_loader = access_token_loader
        self._rate_limiter = rate_limiter

    def get_context(self, request: dict[str, object]) -> dict[str, object]:
        return self._port(request).get_context(request)

    def list_skills(self, request: dict[str, object]) -> dict[str, object]:
        return self._port(request).list_skills(request)

    def get_skill(self, request: dict[str, object]) -> dict[str, object]:
        return self._port(request).get_skill(request)

    def save_checkpoint(self, request: dict[str, object]) -> dict[str, object]:
        return self._port(request).save_checkpoint(request)

    def list_knowledge_sources(self, request: dict[str, object]) -> dict[str, object]:
        return cast(TeamMcpPort, self._port(request)).list_knowledge_sources(request)

    def approve_knowledge_source(self, request: dict[str, object]) -> dict[str, object]:
        return cast(
            TeamMcpPort, self._port(request, required_scope="mnemo:knowledge:approve")
        ).approve_knowledge_source(request)

    def _port(
        self, request: dict[str, object], *, required_scope: str | None = None
    ) -> McpContextPort:
        token = self._access_token_loader()
        if token is None or token.subject is None:
            raise ValueError("MNEMO_AUTH_REQUIRED: verified OAuth subject is required")
        if required_scope is not None and required_scope not in token.scopes:
            raise ValueError("MNEMO_SCOPE_REQUIRED: OAuth scope is required")
        workspace = request.get("workspace_id")
        if not isinstance(workspace, str):
            raise ValueError("MNEMO_INVALID_SCOPE: explicit workspace_id is required")
        try:
            principal_id = OwnerId.from_string(token.subject)
            workspace_id = WorkspaceId.from_string(workspace)
        except (TypeError, ValueError) as error:
            raise ValueError("MNEMO_INVALID_SCOPE: authenticated scope is invalid") from error
        if self._rate_limiter is not None:
            self._rate_limiter.require(principal_id, workspace_id)
        return self._factory(principal_id, workspace_id)


def create_team_server(
    factory: TeamMcpPortFactory,
    *,
    token_verifier: TokenVerifier,
    issuer_url: str,
    resource_server_url: str,
    required_scopes: tuple[str, ...] = ("mnemo:context",),
    http_port: int = 8766,
    rate_limiter: TeamRequestRateLimiter | None = None,
) -> FastMCP:
    """Create an authenticated server that remains loopback-only until a TLS proxy is configured."""
    auth = AuthSettings(
        issuer_url=AnyHttpUrl(issuer_url),
        resource_server_url=AnyHttpUrl(resource_server_url),
        required_scopes=list(required_scopes),
    )
    authenticated = AuthenticatedTeamMcpPort(factory, rate_limiter=rate_limiter)
    return create_server(
        authenticated,
        team_knowledge_port=authenticated,
        auth=auth,
        token_verifier=token_verifier,
        host="127.0.0.1",
        http_port=http_port,
        stateless_http=True,
        json_response=True,
    )
