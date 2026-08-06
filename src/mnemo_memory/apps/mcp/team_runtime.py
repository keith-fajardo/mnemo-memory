"""Secret-safe installed entry point for the loopback team MCP service."""

from __future__ import annotations

import logging
import os
import re
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from mcp.server.fastmcp import FastMCP

from mnemo_memory.apps.mcp.team import PostgreSQLTeamMcpPortFactory, create_team_server
from mnemo_memory.connectors.filesystem.secure_file import (
    OwnedFileReadError,
    read_bounded_owned_file,
)
from mnemo_memory.connectors.oauth import JwtVerifierConfig, MnemoJwtTokenVerifier
from mnemo_memory.packages.application import TeamRequestRateLimit, TeamRequestRateLimiter
from mnemo_memory.packages.storage import PostgreSQLConnection, PostgreSQLConnectionFactory

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:-]{0,252}$")


class TeamServiceConfigurationError(RuntimeError):
    """Content-free team service startup failure safe for logs."""


@dataclass(frozen=True, slots=True)
class TeamServiceConfig:
    database_host: str
    database_port: int
    database_name: str
    database_user: str
    database_password_file: Path
    oauth_public_key_file: Path
    oauth_issuer: str
    resource_server_url: str
    oauth_algorithm: str = "RS256"
    required_scopes: tuple[str, ...] = ("mnemo:context",)
    http_port: int = 8766
    rate_limit_requests: int = 120
    rate_limit_window_seconds: int = 60
    rate_limit_identities: int = 10_000

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> TeamServiceConfig:
        try:
            scopes = tuple(environment.get("MNEMO_TEAM_REQUIRED_SCOPES", "mnemo:context").split())
            config = cls(
                database_host=environment["MNEMO_TEAM_DB_HOST"],
                database_port=_port(environment.get("MNEMO_TEAM_DB_PORT", "5432")),
                database_name=environment["MNEMO_TEAM_DB_NAME"],
                database_user=environment["MNEMO_TEAM_DB_USER"],
                database_password_file=_absolute_path(environment["MNEMO_TEAM_DB_PASSWORD_FILE"]),
                oauth_public_key_file=_absolute_path(
                    environment["MNEMO_TEAM_OAUTH_PUBLIC_KEY_FILE"]
                ),
                oauth_issuer=environment["MNEMO_TEAM_OAUTH_ISSUER"],
                resource_server_url=environment["MNEMO_TEAM_RESOURCE_URL"],
                oauth_algorithm=environment.get("MNEMO_TEAM_OAUTH_ALGORITHM", "RS256"),
                required_scopes=scopes,
                http_port=_port(environment.get("MNEMO_TEAM_HTTP_PORT", "8766")),
                rate_limit_requests=_positive_int(
                    environment.get("MNEMO_TEAM_RATE_LIMIT_REQUESTS", "120")
                ),
                rate_limit_window_seconds=_positive_int(
                    environment.get("MNEMO_TEAM_RATE_LIMIT_WINDOW_SECONDS", "60")
                ),
                rate_limit_identities=_positive_int(
                    environment.get("MNEMO_TEAM_RATE_LIMIT_IDENTITIES", "10000")
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise TeamServiceConfigurationError("MNEMO_TEAM_CONFIG_INVALID") from error
        config.validate()
        return config

    def validate(self) -> None:
        if not _HOST.fullmatch(self.database_host):
            raise TeamServiceConfigurationError("MNEMO_TEAM_CONFIG_INVALID")
        if not _NAME.fullmatch(self.database_name) or not _NAME.fullmatch(self.database_user):
            raise TeamServiceConfigurationError("MNEMO_TEAM_CONFIG_INVALID")
        try:
            JwtVerifierConfig(
                self.oauth_issuer,
                self.resource_server_url,
                "-----BEGIN PUBLIC KEY-----\nplaceholder\n-----END PUBLIC KEY-----\n",
                self.required_scopes,
                self.oauth_algorithm,
            )
        except (TypeError, ValueError) as error:
            raise TeamServiceConfigurationError("MNEMO_TEAM_CONFIG_INVALID") from error
        try:
            TeamRequestRateLimit(
                self.rate_limit_requests,
                self.rate_limit_window_seconds,
                self.rate_limit_identities,
            )
        except ValueError as error:
            raise TeamServiceConfigurationError("MNEMO_TEAM_CONFIG_INVALID") from error


def build_team_service(config: TeamServiceConfig) -> FastMCP:
    """Read bounded files once, require PostgreSQL TLS, and create the inert HTTP server."""
    try:
        password = read_bounded_owned_file(
            config.database_password_file, maximum_bytes=4_096, owner_only=True
        ).strip()
    except OwnedFileReadError as error:
        raise TeamServiceConfigurationError("MNEMO_TEAM_SECRET_UNAVAILABLE") from error
    if not password or "\n" in password or "\r" in password:
        raise TeamServiceConfigurationError("MNEMO_TEAM_SECRET_UNAVAILABLE")
    try:
        public_key = read_bounded_owned_file(
            config.oauth_public_key_file, maximum_bytes=16_384, owner_only=False
        )
    except OwnedFileReadError as error:
        raise TeamServiceConfigurationError("MNEMO_TEAM_PUBLIC_KEY_UNAVAILABLE") from error
    try:
        verifier = MnemoJwtTokenVerifier(
            JwtVerifierConfig(
                config.oauth_issuer,
                config.resource_server_url,
                public_key,
                config.required_scopes,
                config.oauth_algorithm,
            )
        )
    except (TypeError, ValueError) as error:
        raise TeamServiceConfigurationError("MNEMO_TEAM_PUBLIC_KEY_UNAVAILABLE") from error

    return create_team_server(
        PostgreSQLTeamMcpPortFactory(_postgres_connection_factory(config, password)),
        token_verifier=verifier,
        issuer_url=config.oauth_issuer,
        resource_server_url=config.resource_server_url,
        required_scopes=config.required_scopes,
        http_port=config.http_port,
        rate_limiter=TeamRequestRateLimiter(
            TeamRequestRateLimit(
                config.rate_limit_requests,
                config.rate_limit_window_seconds,
                config.rate_limit_identities,
            )
        ),
    )


def _postgres_connection_factory(
    config: TeamServiceConfig, password: str
) -> PostgreSQLConnectionFactory:
    tls = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2

    def connect() -> PostgreSQLConnection:
        try:
            import pg8000.dbapi  # type: ignore[import-untyped]

            return cast(
                PostgreSQLConnection,
                pg8000.dbapi.connect(
                    user=config.database_user,
                    password=password,
                    host=config.database_host,
                    port=config.database_port,
                    database=config.database_name,
                    ssl_context=tls,
                    timeout=5,
                    application_name="mnemo-team-mcp",
                ),
            )
        except Exception as error:
            raise TeamServiceConfigurationError("MNEMO_TEAM_POSTGRES_UNAVAILABLE") from error

    return connect


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        config = TeamServiceConfig.from_environment(os.environ)
        build_team_service(config).run(transport="streamable-http")
    except TeamServiceConfigurationError as error:
        logging.error("%s", error)
        raise SystemExit(2) from error


def _port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65_535:
        raise ValueError("port is out of range")
    return port


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise ValueError("positive integer is required")
    return parsed


def _absolute_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("team secret paths must be absolute")
    return path
