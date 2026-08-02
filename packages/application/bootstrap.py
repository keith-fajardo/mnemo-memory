"""Composition root for the local personal-profile lifecycle service."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from packages.application.checkpoints import CheckpointApplicationService
from packages.application.config import LocalConfig
from packages.application.services import LifecycleService
from packages.storage import SQLiteCheckpointRepository


def build_lifecycle_service(config: LocalConfig) -> LifecycleService:
    repository = SQLiteCheckpointRepository(
        config.database_path, base_directory=config.data_directory
    )
    return LifecycleService(config, repository.migrate, repository.schema_version)


class CheckpointRuntime:
    """Closeable composition of the local repository and checkpoint application service."""

    def __init__(
        self,
        config: LocalConfig,
        repository: SQLiteCheckpointRepository,
        checkpoint_service: CheckpointApplicationService,
    ) -> None:
        self.config = config
        self.repository = repository
        self.checkpoint_service = checkpoint_service
        self._closed = False

    def close(self) -> None:
        # The SQLite adapter opens scoped connections per operation; retain an explicit lifecycle
        # boundary so future adapters can release long-lived resources here.
        self._closed = True

    def __enter__(self) -> CheckpointRuntime:
        if self._closed:
            raise RuntimeError("checkpoint runtime is closed")
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class LocalRuntimeError(RuntimeError):
    """Safe error composing the configured local checkpoint runtime."""


def build_checkpoint_runtime(config: LocalConfig) -> CheckpointRuntime:
    """Open the configured SQLite profile, migrate it, and compose canonical use cases."""
    try:
        repository = SQLiteCheckpointRepository(
            config.database_path, base_directory=config.data_directory
        )
        repository.migrate()
    except (OSError, ValueError, RuntimeError, sqlite3.DatabaseError) as error:
        raise LocalRuntimeError(
            "configured Mnemo storage is unavailable or incompatible"
        ) from error
    return CheckpointRuntime(
        config,
        repository,
        CheckpointApplicationService(repository, clock=lambda: datetime.now(UTC)),
    )
