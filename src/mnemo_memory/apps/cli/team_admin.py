"""Secret-safe operator commands for team backup and restore drills."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import ssl
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from mnemo_memory.connectors.filesystem.secure_file import (
    OwnedFileReadError,
    read_bounded_owned_file,
)
from mnemo_memory.connectors.postgresql.backup import (
    PostgreSQLBackupToolConfig,
    PostgreSQLNativeBackupAdapter,
)
from mnemo_memory.packages.application import TeamBackupError, TeamBackupService
from mnemo_memory.packages.storage import PostgreSQLConnection

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:-]{0,252}$")


class TeamAdminConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TeamBackupRuntimeConfig:
    database_host: str
    database_port: int
    source_database: str
    backup_user: str
    backup_password_file: Path
    ssl_root_cert_file: Path | None = None

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> TeamBackupRuntimeConfig:
        try:
            root = environment.get("MNEMO_TEAM_BACKUP_SSL_ROOT_CERT_FILE")
            config = cls(
                database_host=environment["MNEMO_TEAM_DB_HOST"],
                database_port=_port(environment.get("MNEMO_TEAM_DB_PORT", "5432")),
                source_database=environment["MNEMO_TEAM_DB_NAME"],
                backup_user=environment["MNEMO_TEAM_BACKUP_DB_USER"],
                backup_password_file=_absolute_path(
                    environment["MNEMO_TEAM_BACKUP_DB_PASSWORD_FILE"]
                ),
                ssl_root_cert_file=None if root is None else _absolute_path(root),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise TeamAdminConfigurationError("MNEMO_TEAM_BACKUP_CONFIG_INVALID") from error
        if (
            _HOST.fullmatch(config.database_host) is None
            or _NAME.fullmatch(config.source_database) is None
            or _NAME.fullmatch(config.backup_user) is None
        ):
            raise TeamAdminConfigurationError("MNEMO_TEAM_BACKUP_CONFIG_INVALID")
        return config


def build_team_backup_service(config: TeamBackupRuntimeConfig) -> TeamBackupService:
    try:
        password = read_bounded_owned_file(
            config.backup_password_file, maximum_bytes=4_096, owner_only=True
        ).strip()
        if not password or "\n" in password or "\r" in password:
            raise OwnedFileReadError
        root_content: str | None = None
        root_value = "system"
        if config.ssl_root_cert_file is not None:
            root_content = read_bounded_owned_file(
                config.ssl_root_cert_file, maximum_bytes=65_536, owner_only=False
            )
            root_value = str(config.ssl_root_cert_file)
    except OwnedFileReadError as error:
        raise TeamAdminConfigurationError("MNEMO_TEAM_BACKUP_SECRET_UNAVAILABLE") from error

    tls = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cadata=root_content)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2

    def connect(database: str) -> PostgreSQLConnection:
        try:
            import pg8000.dbapi  # type: ignore[import-untyped]

            return cast(
                PostgreSQLConnection,
                pg8000.dbapi.connect(
                    user=config.backup_user,
                    password=password,
                    host=config.database_host,
                    port=config.database_port,
                    database=database,
                    ssl_context=tls,
                    timeout=5,
                    application_name="mnemo-team-backup",
                ),
            )
        except Exception as error:
            raise TeamAdminConfigurationError("MNEMO_TEAM_POSTGRES_UNAVAILABLE") from error

    adapter = PostgreSQLNativeBackupAdapter(
        PostgreSQLBackupToolConfig(
            config.database_host,
            config.database_port,
            config.source_database,
            config.backup_user,
            password,
            root_value,
        ),
        connect,
    )
    return TeamBackupService(adapter)


def main(arguments: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="mnemo-memory-team-admin")
    subcommands = parser.add_subparsers(dest="command", required=True)
    backup = subcommands.add_parser("backup")
    backup.add_argument("--output-dir", type=Path, required=True)
    restore = subcommands.add_parser("restore-drill")
    restore.add_argument("--manifest", type=Path, required=True)
    restore.add_argument("--target-database", required=True)
    prune = subcommands.add_parser("prune-deleted")
    prune.add_argument("--backup-dir", type=Path, required=True)
    parsed = parser.parse_args(arguments)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        service = build_team_backup_service(TeamBackupRuntimeConfig.from_environment(os.environ))
        if parsed.command == "backup":
            payload = service.create(parsed.output_dir).to_dict()
        elif parsed.command == "restore-drill":
            payload = service.restore_drill(
                parsed.manifest, target_database=parsed.target_database
            ).to_dict()
        else:
            payload = service.prune_deleted(parsed.backup_dir).to_dict()
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    except (TeamAdminConfigurationError, TeamBackupError) as error:
        logging.error("%s", error)
        raise SystemExit(2) from error


def _port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65_535:
        raise ValueError
    return port


def _absolute_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError
    return path
