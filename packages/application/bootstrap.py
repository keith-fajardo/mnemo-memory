"""Composition root for the local personal-profile lifecycle service."""

from __future__ import annotations

from packages.application.config import LocalConfig
from packages.application.services import LifecycleService
from packages.storage import SQLiteCheckpointRepository


def build_lifecycle_service(config: LocalConfig) -> LifecycleService:
    repository = SQLiteCheckpointRepository(
        config.database_path, base_directory=config.data_directory
    )
    return LifecycleService(config, repository.migrate, repository.schema_version)
