"""OAuth authentication must bind team MCP calls before repository composition."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from mcp.server.auth.provider import AccessToken

from mnemo_memory.apps.mcp.team import AuthenticatedTeamMcpPort, create_team_server
from mnemo_memory.connectors.oauth import JwtVerifierConfig, MnemoJwtTokenVerifier
from mnemo_memory.packages.application import TeamRequestRateLimit, TeamRequestRateLimiter
from mnemo_memory.packages.application.mcp_port import McpContextPort
from mnemo_memory.packages.domain import OwnerId, WorkspaceId

ISSUER = "https://identity.example.test"
AUDIENCE = "https://memory.example.test/mcp"


class _Port:
    def get_context(self, request: dict[str, object]) -> dict[str, object]:
        return {"operation": "get_context", "workspace_id": request["workspace_id"]}

    def structural_lookup(self, request: dict[str, object]) -> dict[str, object]:
        return {"operation": "structural_lookup"}

    def list_skills(self, request: dict[str, object]) -> dict[str, object]:
        return {"operation": "list_skills"}

    def get_skill(self, request: dict[str, object]) -> dict[str, object]:
        return {"operation": "get_skill"}

    def save_checkpoint(self, request: dict[str, object]) -> dict[str, object]:
        return {"operation": "save_checkpoint"}

    def extract_episodic(self, request: dict[str, object]) -> dict[str, object]:
        return {"status": "extraction_disabled"}

    def submit_episodic_candidates(self, request: dict[str, object]) -> dict[str, object]:
        return {"status": "extraction_disabled"}

    def list_knowledge_sources(self, request: dict[str, object]) -> dict[str, object]:
        return {"operation": "list_knowledge_sources"}

    def approve_knowledge_source(self, request: dict[str, object]) -> dict[str, object]:
        return {"operation": "approve_knowledge_source"}


class _Factory:
    def __init__(self) -> None:
        self.calls: list[tuple[OwnerId, WorkspaceId]] = []

    def __call__(self, principal_id: OwnerId, workspace_id: WorkspaceId) -> McpContextPort:
        self.calls.append((principal_id, workspace_id))
        return _Port()


@pytest.fixture
def jwt_keys() -> tuple[str, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private_key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


def _claims(principal: OwnerId) -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "aud": AUDIENCE,
        "client_id": "codex-team-client",
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        "iat": int((now - timedelta(seconds=1)).timestamp()),
        "iss": ISSUER,
        "scope": "mnemo:context mnemo:checkpoint",
        "sub": str(principal),
    }


def _encode(private_pem: str, claims: dict[str, object]) -> str:
    return jwt.encode(claims, private_pem, algorithm="RS256")


def test_verified_subject_and_explicit_workspace_bind_the_repository_factory() -> None:
    principal, workspace = OwnerId.new(), WorkspaceId.new()
    factory = _Factory()
    access_token = AccessToken(
        token="not-retained-by-the-port",
        client_id="codex-team-client",
        scopes=["mnemo:context"],
        subject=str(principal),
    )
    port = AuthenticatedTeamMcpPort(factory, access_token_loader=lambda: access_token)

    result = port.get_context({"workspace_id": str(workspace)})

    assert result == {"operation": "get_context", "workspace_id": str(workspace)}
    assert factory.calls == [(principal, workspace)]


@pytest.mark.parametrize("payload", [{}, {"workspace_id": "not-a-uuid"}])
def test_invalid_scope_fails_before_repository_composition(payload: dict[str, object]) -> None:
    factory = _Factory()
    token = AccessToken(
        token="opaque",
        client_id="client",
        scopes=["mnemo:context"],
        subject=str(OwnerId.new()),
    )

    with pytest.raises(ValueError, match="MNEMO_INVALID_SCOPE"):
        AuthenticatedTeamMcpPort(factory, access_token_loader=lambda: token).get_context(payload)

    assert factory.calls == []


def test_missing_authentication_fails_before_repository_composition() -> None:
    factory = _Factory()
    with pytest.raises(ValueError, match="MNEMO_AUTH_REQUIRED"):
        AuthenticatedTeamMcpPort(factory, access_token_loader=lambda: None).get_context(
            {"workspace_id": str(WorkspaceId.new())}
        )
    assert factory.calls == []


def test_rate_limit_runs_after_auth_scope_and_before_repository_composition() -> None:
    principal, workspace = OwnerId.new(), WorkspaceId.new()
    factory = _Factory()
    token = AccessToken(
        token="opaque",
        client_id="client",
        scopes=["mnemo:context"],
        subject=str(principal),
    )
    limiter = TeamRequestRateLimiter(TeamRequestRateLimit(1, 60, 10), timer=lambda: 1.0)
    port = AuthenticatedTeamMcpPort(
        factory, access_token_loader=lambda: token, rate_limiter=limiter
    )

    port.get_context({"workspace_id": str(workspace)})
    with pytest.raises(ValueError, match="MNEMO_RATE_LIMITED"):
        port.get_context({"workspace_id": str(workspace)})

    assert factory.calls == [(principal, workspace)]

    invalid_factory = _Factory()
    invalid = AuthenticatedTeamMcpPort(
        invalid_factory, access_token_loader=lambda: None, rate_limiter=limiter
    )
    with pytest.raises(ValueError, match="MNEMO_AUTH_REQUIRED"):
        invalid.get_context({"workspace_id": str(workspace)})
    assert invalid_factory.calls == []


def test_source_approval_requires_a_dedicated_oauth_scope_before_composition() -> None:
    principal, workspace = OwnerId.new(), WorkspaceId.new()
    factory = _Factory()
    context_only = AccessToken(
        token="opaque",
        client_id="client",
        scopes=["mnemo:context"],
        subject=str(principal),
    )
    request: dict[str, object] = {"workspace_id": str(workspace)}

    with pytest.raises(ValueError, match="MNEMO_SCOPE_REQUIRED"):
        AuthenticatedTeamMcpPort(
            factory, access_token_loader=lambda: context_only
        ).approve_knowledge_source(request)
    assert factory.calls == []

    approver = AccessToken(
        token="opaque",
        client_id="client",
        scopes=["mnemo:context", "mnemo:knowledge:approve"],
        subject=str(principal),
    )
    assert AuthenticatedTeamMcpPort(
        factory, access_token_loader=lambda: approver
    ).approve_knowledge_source(request) == {"operation": "approve_knowledge_source"}
    assert factory.calls == [(principal, workspace)]


def test_pinned_jwt_verifier_accepts_only_exact_signed_oauth_claims(
    jwt_keys: tuple[str, str],
) -> None:
    private_pem, public_pem = jwt_keys
    principal = OwnerId.new()
    verifier = MnemoJwtTokenVerifier(JwtVerifierConfig(ISSUER, AUDIENCE, public_pem))

    verified = asyncio.run(verifier.verify_token(_encode(private_pem, _claims(principal))))

    assert verified is not None
    assert verified.subject == str(principal)
    assert verified.client_id == "codex-team-client"
    assert verified.resource == AUDIENCE
    assert verified.scopes == ["mnemo:checkpoint", "mnemo:context"]
    assert verified.claims == {"iss": ISSUER}


@pytest.mark.parametrize(
    ("claim", "value"),
    [
        ("iss", "https://other.example.test"),
        ("aud", "https://other.example.test/mcp"),
        ("scope", "mnemo:checkpoint"),
        ("sub", "not-a-principal"),
        ("client_id", "bad client"),
    ],
)
def test_pinned_jwt_verifier_rejects_untrusted_claims(
    jwt_keys: tuple[str, str], claim: str, value: str
) -> None:
    private_pem, public_pem = jwt_keys
    claims = _claims(OwnerId.new())
    claims[claim] = value
    verifier = MnemoJwtTokenVerifier(JwtVerifierConfig(ISSUER, AUDIENCE, public_pem))

    assert asyncio.run(verifier.verify_token(_encode(private_pem, claims))) is None


def test_pinned_jwt_verifier_rejects_another_signing_key(
    jwt_keys: tuple[str, str],
) -> None:
    _, public_pem = jwt_keys
    other_private, _ = _new_key_pair()
    verifier = MnemoJwtTokenVerifier(JwtVerifierConfig(ISSUER, AUDIENCE, public_pem))

    assert (
        asyncio.run(verifier.verify_token(_encode(other_private, _claims(OwnerId.new())))) is None
    )


def test_streamable_http_route_requires_bearer_authentication(
    jwt_keys: tuple[str, str],
) -> None:
    private_pem, public_pem = jwt_keys
    verifier = MnemoJwtTokenVerifier(JwtVerifierConfig(ISSUER, AUDIENCE, public_pem))
    server = create_team_server(
        _Factory(),
        token_verifier=verifier,
        issuer_url=ISSUER,
        resource_server_url=AUDIENCE,
    )

    with TestClient(server.streamable_http_app()) as client:
        unauthenticated = client.post("/mcp", json={})
        authenticated = client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {_encode(private_pem, _claims(OwnerId.new()))}"},
            json={},
        )

    assert unauthenticated.status_code == 401
    assert authenticated.status_code != 401
    assert server.settings.host == "127.0.0.1"
    assert server.settings.stateless_http is True
    assert set(server._tool_manager._tools) == {
        "get_context",
        "list_skills",
        "get_skill",
        "explain_context",
        "save_checkpoint",
        "structural_lookup",
        "list_knowledge_sources",
        "approve_knowledge_source",
    }


def test_structural_lookup_fails_open_instead_of_requiring_workspace_scope() -> None:
    # The structural_lookup tool surface is the minimal {kind,target,limit}; it never carries a
    # workspace_id. Delegating into the workspace-requiring _port path would raise
    # MNEMO_INVALID_SCOPE on every team call, so the team port must fail open to the empty shape.
    principal = OwnerId.new()
    empty: dict[str, object] = {
        "kind": "define",
        "query": "CurrentService",
        "snapshot_id": None,
        "truncated": False,
        "hits": [],
    }

    # Even with a fully valid authenticated token but no workspace_id in the request, the
    # token-binding path is bypassed safely: no raise, and no per-request port is ever built.
    authenticated_factory = _Factory()
    authenticated = AccessToken(
        token="opaque",
        client_id="client",
        scopes=["mnemo:context"],
        subject=str(principal),
    )
    port = AuthenticatedTeamMcpPort(
        authenticated_factory, access_token_loader=lambda: authenticated
    )
    assert port.structural_lookup({"kind": "define", "target": "CurrentService"}) == empty
    # An unknown kind stays fail-open too, echoing the requested kind like the local port.
    assert port.structural_lookup({"kind": "grep", "target": "x"})["hits"] == []
    # Short-circuited before the workspace guard: no connection/port composition occurred.
    assert authenticated_factory.calls == []

    # And with no token at all it still never raises MNEMO_AUTH_REQUIRED / MNEMO_INVALID_SCOPE.
    anonymous_factory = _Factory()
    anonymous = AuthenticatedTeamMcpPort(anonymous_factory, access_token_loader=lambda: None)
    assert anonymous.structural_lookup({"kind": "callers", "target": "y"})["hits"] == []
    assert anonymous_factory.calls == []


def _new_key_pair() -> tuple[str, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private_key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem
