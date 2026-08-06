"""Team service startup keeps secrets out of arguments and requires TLS-safe inputs."""

from __future__ import annotations

import ssl
from pathlib import Path

import pg8000.dbapi  # type: ignore[import-untyped]
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from mnemo_memory.apps.mcp.team_runtime import (
    TeamServiceConfig,
    TeamServiceConfigurationError,
    _postgres_connection_factory,
    build_team_service,
)


def _files(tmp_path: Path) -> tuple[Path, Path]:
    password = tmp_path / "database-password"
    password.write_text("correct horse battery staple\n", encoding="utf-8")
    password.chmod(0o600)
    public_key = tmp_path / "oauth-public.pem"
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    public_key.chmod(0o644)
    return password, public_key


def _environment(password: Path, public_key: Path) -> dict[str, str]:
    return {
        "MNEMO_TEAM_DB_HOST": "postgres.internal",
        "MNEMO_TEAM_DB_NAME": "mnemo",
        "MNEMO_TEAM_DB_PASSWORD_FILE": str(password),
        "MNEMO_TEAM_DB_USER": "mnemo_runtime",
        "MNEMO_TEAM_OAUTH_ISSUER": "https://identity.example.test",
        "MNEMO_TEAM_OAUTH_PUBLIC_KEY_FILE": str(public_key),
        "MNEMO_TEAM_RESOURCE_URL": "https://memory.example.test/mcp",
    }


def test_valid_file_backed_configuration_builds_only_a_loopback_server(tmp_path: Path) -> None:
    password, public_key = _files(tmp_path)
    config = TeamServiceConfig.from_environment(_environment(password, public_key))

    server = build_team_service(config)

    assert server.settings.host == "127.0.0.1"
    assert server.settings.port == 8766
    assert server.settings.stateless_http is True
    assert config.required_scopes == ("mnemo:context",)


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604])
def test_database_password_rejects_group_or_other_access(tmp_path: Path, mode: int) -> None:
    password, public_key = _files(tmp_path)
    password.chmod(mode)
    config = TeamServiceConfig.from_environment(_environment(password, public_key))

    with pytest.raises(TeamServiceConfigurationError) as raised:
        build_team_service(config)

    assert str(raised.value) == "MNEMO_TEAM_SECRET_UNAVAILABLE"
    assert str(password) not in str(raised.value)
    assert "correct horse" not in str(raised.value)


def test_secret_and_public_key_symlinks_are_rejected(tmp_path: Path) -> None:
    password, public_key = _files(tmp_path)
    linked_password = tmp_path / "linked-password"
    linked_password.symlink_to(password)
    config = TeamServiceConfig.from_environment(_environment(linked_password, public_key))
    with pytest.raises(TeamServiceConfigurationError, match="MNEMO_TEAM_SECRET_UNAVAILABLE"):
        build_team_service(config)

    linked_key = tmp_path / "linked-key"
    linked_key.symlink_to(public_key)
    config = TeamServiceConfig.from_environment(_environment(password, linked_key))
    with pytest.raises(TeamServiceConfigurationError, match="MNEMO_TEAM_PUBLIC_KEY_UNAVAILABLE"):
        build_team_service(config)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("MNEMO_TEAM_DB_HOST", "host with spaces"),
        ("MNEMO_TEAM_DB_PORT", "0"),
        ("MNEMO_TEAM_DB_PASSWORD_FILE", "relative-secret"),
        ("MNEMO_TEAM_HTTP_PORT", "70000"),
        ("MNEMO_TEAM_OAUTH_ISSUER", "http://identity.example.test"),
        ("MNEMO_TEAM_RESOURCE_URL", "https://user:pass@memory.example.test/mcp"),
        ("MNEMO_TEAM_REQUIRED_SCOPES", ""),
    ],
)
def test_invalid_non_secret_configuration_fails_closed(
    tmp_path: Path, field: str, value: str
) -> None:
    password, public_key = _files(tmp_path)
    environment = _environment(password, public_key)
    environment[field] = value

    with pytest.raises(TeamServiceConfigurationError, match="MNEMO_TEAM_CONFIG_INVALID"):
        TeamServiceConfig.from_environment(environment)


def test_postgres_factory_always_supplies_certificate_verifying_tls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    password_file, public_key = _files(tmp_path)
    config = TeamServiceConfig.from_environment(_environment(password_file, public_key))
    captured: dict[str, object] = {}
    sentinel = object()

    def fake_connect(**kwargs: object) -> object:
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(pg8000.dbapi, "connect", fake_connect)

    connection = _postgres_connection_factory(config, "runtime-password")()

    assert connection is sentinel
    tls = captured["ssl_context"]
    assert isinstance(tls, ssl.SSLContext)
    assert tls.verify_mode is ssl.CERT_REQUIRED
    assert tls.check_hostname is True
    assert tls.minimum_version >= ssl.TLSVersion.TLSv1_2
    assert captured["password"] == "runtime-password"
    assert captured["timeout"] == 5


def test_missing_optional_postgres_driver_has_a_content_free_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    password_file, public_key = _files(tmp_path)
    config = TeamServiceConfig.from_environment(_environment(password_file, public_key))

    def unavailable(**_: object) -> object:
        raise RuntimeError("driver detail including secret")

    monkeypatch.setattr(pg8000.dbapi, "connect", unavailable)
    with pytest.raises(TeamServiceConfigurationError) as raised:
        _postgres_connection_factory(config, "do-not-disclose")()

    assert str(raised.value) == "MNEMO_TEAM_POSTGRES_UNAVAILABLE"
    assert "driver detail" not in str(raised.value)
    assert "do-not-disclose" not in str(raised.value)
