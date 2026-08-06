"""Team backup administration uses strict configuration and owner-only secrets."""

from __future__ import annotations

import ssl
from pathlib import Path
from typing import cast

import pg8000.dbapi  # type: ignore[import-untyped]
import pytest

from mnemo_memory.apps.cli.team_admin import (
    TeamAdminConfigurationError,
    TeamBackupRuntimeConfig,
    build_team_backup_service,
)
from mnemo_memory.packages.storage import PostgreSQLConnection


def _environment(password: Path) -> dict[str, str]:
    return {
        "MNEMO_TEAM_DB_HOST": "postgres.internal",
        "MNEMO_TEAM_DB_PORT": "5432",
        "MNEMO_TEAM_DB_NAME": "mnemo",
        "MNEMO_TEAM_BACKUP_DB_USER": "mnemo_backup",
        "MNEMO_TEAM_BACKUP_DB_PASSWORD_FILE": str(password),
    }


def test_backup_runtime_uses_owner_only_secret_and_verifying_tls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    password = tmp_path / "backup-password"
    password.write_text("backup-secret\n", encoding="utf-8")
    password.chmod(0o600)
    config = TeamBackupRuntimeConfig.from_environment(_environment(password))
    captured: dict[str, object] = {}
    sentinel = cast(PostgreSQLConnection, object())

    def fake_connect(**values: object) -> PostgreSQLConnection:
        captured.update(values)
        return sentinel

    monkeypatch.setattr(pg8000.dbapi, "connect", fake_connect)
    service = build_team_backup_service(config)
    port = service._port
    connection = port._connection_factory("restore_drill")  # type: ignore[attr-defined]

    assert connection is sentinel
    assert captured["password"] == "backup-secret"
    assert captured["database"] == "restore_drill"
    tls = captured["ssl_context"]
    assert isinstance(tls, ssl.SSLContext)
    assert tls.verify_mode is ssl.CERT_REQUIRED
    assert tls.check_hostname is True
    assert tls.minimum_version >= ssl.TLSVersion.TLSv1_2


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604])
def test_backup_runtime_rejects_shared_password_files(tmp_path: Path, mode: int) -> None:
    password = tmp_path / "backup-password"
    password.write_text("must-not-leak", encoding="utf-8")
    password.chmod(mode)
    config = TeamBackupRuntimeConfig.from_environment(_environment(password))

    with pytest.raises(TeamAdminConfigurationError) as raised:
        build_team_backup_service(config)

    assert str(raised.value) == "MNEMO_TEAM_BACKUP_SECRET_UNAVAILABLE"
    assert "must-not-leak" not in str(raised.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("MNEMO_TEAM_DB_HOST", "host with spaces"),
        ("MNEMO_TEAM_DB_PORT", "0"),
        ("MNEMO_TEAM_DB_NAME", "bad/name"),
        ("MNEMO_TEAM_BACKUP_DB_USER", "bad user"),
        ("MNEMO_TEAM_BACKUP_DB_PASSWORD_FILE", "relative-password"),
        ("MNEMO_TEAM_BACKUP_SSL_ROOT_CERT_FILE", "relative-ca"),
    ],
)
def test_backup_runtime_rejects_invalid_configuration(
    tmp_path: Path, field: str, value: str
) -> None:
    password = tmp_path / "backup-password"
    password.write_text("secret", encoding="utf-8")
    password.chmod(0o600)
    environment = _environment(password)
    environment[field] = value

    with pytest.raises(TeamAdminConfigurationError, match="BACKUP_CONFIG_INVALID"):
        TeamBackupRuntimeConfig.from_environment(environment)


def test_backup_runtime_hides_database_connection_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    password = tmp_path / "backup-password"
    password.write_text("do-not-disclose", encoding="utf-8")
    password.chmod(0o600)
    config = TeamBackupRuntimeConfig.from_environment(_environment(password))

    def unavailable(**_: object) -> object:
        raise RuntimeError("database payload and do-not-disclose")

    monkeypatch.setattr(pg8000.dbapi, "connect", unavailable)
    service = build_team_backup_service(config)
    port = service._port

    with pytest.raises(TeamAdminConfigurationError) as raised:
        port._connection_factory("mnemo")  # type: ignore[attr-defined]

    assert str(raised.value) == "MNEMO_TEAM_POSTGRES_UNAVAILABLE"
    assert "database payload" not in str(raised.value)
    assert "do-not-disclose" not in str(raised.value)
