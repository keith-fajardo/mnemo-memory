"""OAuth-protected, loopback-hosted Streamable HTTP MCP team service."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

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
from mnemo_memory.packages.application.unified_context import UnifiedContextService
from mnemo_memory.packages.context_engine import UnifiedContextEngine
from mnemo_memory.packages.domain import OwnerId, WorkspaceId
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


class TeamMcpPortFactory(Protocol):
    def __call__(self, principal_id: OwnerId, workspace_id: WorkspaceId) -> McpContextPort: ...


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

    def __call__(self, principal_id: OwnerId, workspace_id: WorkspaceId) -> McpContextPort:
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
        return DurableMcpContextPort(
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


class AuthenticatedTeamMcpPort:
    """Bind every tool call to the verified subject and explicit request workspace."""

    def __init__(
        self,
        factory: TeamMcpPortFactory,
        *,
        access_token_loader: Callable[[], AccessToken | None] = get_access_token,
    ) -> None:
        self._factory = factory
        self._access_token_loader = access_token_loader

    def get_context(self, request: dict[str, object]) -> dict[str, object]:
        return self._port(request).get_context(request)

    def list_skills(self, request: dict[str, object]) -> dict[str, object]:
        return self._port(request).list_skills(request)

    def get_skill(self, request: dict[str, object]) -> dict[str, object]:
        return self._port(request).get_skill(request)

    def save_checkpoint(self, request: dict[str, object]) -> dict[str, object]:
        return self._port(request).save_checkpoint(request)

    def _port(self, request: dict[str, object]) -> McpContextPort:
        token = self._access_token_loader()
        if token is None or token.subject is None:
            raise ValueError("MNEMO_AUTH_REQUIRED: verified OAuth subject is required")
        workspace = request.get("workspace_id")
        if not isinstance(workspace, str):
            raise ValueError("MNEMO_INVALID_SCOPE: explicit workspace_id is required")
        try:
            principal_id = OwnerId.from_string(token.subject)
            workspace_id = WorkspaceId.from_string(workspace)
        except (TypeError, ValueError) as error:
            raise ValueError("MNEMO_INVALID_SCOPE: authenticated scope is invalid") from error
        return self._factory(principal_id, workspace_id)


def create_team_server(
    factory: TeamMcpPortFactory,
    *,
    token_verifier: TokenVerifier,
    issuer_url: str,
    resource_server_url: str,
    required_scopes: tuple[str, ...] = ("mnemo:context",),
    http_port: int = 8766,
) -> FastMCP:
    """Create an authenticated server that remains loopback-only until a TLS proxy is configured."""
    auth = AuthSettings(
        issuer_url=AnyHttpUrl(issuer_url),
        resource_server_url=AnyHttpUrl(resource_server_url),
        required_scopes=list(required_scopes),
    )
    return create_server(
        AuthenticatedTeamMcpPort(factory),
        auth=auth,
        token_verifier=token_verifier,
        host="127.0.0.1",
        http_port=http_port,
        stateless_http=True,
        json_response=True,
    )
