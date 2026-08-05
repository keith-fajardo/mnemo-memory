"""Composition root for the local personal-profile lifecycle service."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from mnemo_memory.packages.application.checkpoints import CheckpointApplicationService
from mnemo_memory.packages.application.config import LocalConfig
from mnemo_memory.packages.application.dbt import (
    DbtCatalogParserPort,
    DbtManifestApplicationService,
    DbtManifestParserPort,
    DbtRunResultsParserPort,
    DbtSourceFreshnessParserPort,
)
from mnemo_memory.packages.application.knowledge import KnowledgeDocumentApplicationService
from mnemo_memory.packages.application.services import LifecycleService
from mnemo_memory.packages.domain import KnowledgeSyncPlanner
from mnemo_memory.packages.storage import (
    SQLiteCheckpointRepository,
    SQLiteKnowledgeDocumentRepository,
    SQLiteSourceStructureRepository,
)


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
        dbt_manifest_service: DbtManifestApplicationService | None = None,
        source_structure_repository: SQLiteSourceStructureRepository | None = None,
        knowledge_document_service: KnowledgeDocumentApplicationService | None = None,
        knowledge_document_repository: SQLiteKnowledgeDocumentRepository | None = None,
    ) -> None:
        self.config = config
        self.repository = repository
        self.checkpoint_service = checkpoint_service
        self.dbt_manifest_service = dbt_manifest_service
        self.source_structure_repository = source_structure_repository
        self.knowledge_document_service = knowledge_document_service
        self.knowledge_document_repository = knowledge_document_repository
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


def build_checkpoint_runtime(
    config: LocalConfig,
    *,
    dbt_parser: DbtManifestParserPort | None = None,
    dbt_catalog_parser: DbtCatalogParserPort | None = None,
    dbt_run_results_parser: DbtRunResultsParserPort | None = None,
    dbt_source_freshness_parser: DbtSourceFreshnessParserPort | None = None,
) -> CheckpointRuntime:
    """Open the configured SQLite profile, migrate it, and compose canonical use cases."""
    try:
        repository = SQLiteCheckpointRepository(
            config.database_path, base_directory=config.data_directory
        )
        repository.migrate()
        source_repository = SQLiteSourceStructureRepository(
            config.database_path, base_directory=config.data_directory
        )
        knowledge_repository = SQLiteKnowledgeDocumentRepository(
            config.database_path, base_directory=config.data_directory
        )
    except (OSError, ValueError, RuntimeError, sqlite3.DatabaseError) as error:
        raise LocalRuntimeError(
            "configured Mnemo storage is unavailable or incompatible"
        ) from error
    return CheckpointRuntime(
        config,
        repository,
        CheckpointApplicationService(
            repository,
            clock=lambda: datetime.now(UTC),
            event_repository=repository,
            approved_event_repository=repository,
        ),
        DbtManifestApplicationService(
            repository,
            dbt_parser,
            dbt_catalog_parser,
            dbt_run_results_parser,
            dbt_source_freshness_parser,
        )
        if any(
            parser is not None
            for parser in (
                dbt_parser,
                dbt_catalog_parser,
                dbt_run_results_parser,
                dbt_source_freshness_parser,
            )
        )
        else None,
        source_repository,
        KnowledgeDocumentApplicationService(
            knowledge_repository,
            clock=lambda: datetime.now(UTC),
            planner=KnowledgeSyncPlanner(),
        ),
        knowledge_repository,
    )
