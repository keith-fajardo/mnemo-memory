"""Shared persistence contract for minimized supplemental dbt projections."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mnemo_memory.connectors.dbt import (
    DbtCatalogParser,
    DbtManifestParser,
    DbtRunResultsParser,
    DbtSourceFreshnessParser,
    DbtSupplementalParseRequest,
    ManifestParseRequest,
)
from mnemo_memory.packages.domain import (
    DbtCatalogArtifact,
    DbtRunResultsArtifact,
    DbtSnapshotId,
    DbtSourceFreshnessArtifact,
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    Visibility,
)
from mnemo_memory.packages.storage import (
    ReferenceProjectIndexRepository,
    SQLiteCheckpointRepository,
)
from mnemo_memory.packages.storage.contracts import (
    InvalidManifestSnapshotScope,
    ManifestSnapshotNotFound,
    ProjectIndexRepository,
    ProjectIndexStorageFailure,
    SupplementalArtifactConflict,
)
from mnemo_memory.packages.storage.sqlite import SQLiteMigrationError

FIXTURES = Path(__file__).parents[1] / "fixtures" / "dbt"


def scope(suffix: int = 1) -> MemoryScope:
    return MemoryScope(
        owner_id=OwnerId.from_string("00000000-0000-4000-8000-000000000011"),
        level=ScopeLevel.PROJECT,
        visibility=Visibility.PROJECT,
        project_id=ProjectId.from_string(f"00000000-0000-4000-8000-{suffix:012d}"),
    )


def manifest(item_scope: MemoryScope, *, later: bool = False):  # type: ignore[no-untyped-def]
    value = json.loads((FIXTURES / "manifest-v12.json").read_text())
    if later:
        value["nodes"]["model.mnemo_analytics.fct_orders"]["checksum"]["checksum"] = (
            "fact-orders-later"
        )
    return DbtManifestParser().parse(
        json.dumps(value),
        ManifestParseRequest(
            item_scope,
            "target/manifest.json",
            datetime(2026, 8, 5, tzinfo=UTC),
        ),
    )


def catalog(item_scope: MemoryScope, *, later: bool = False) -> DbtCatalogArtifact:
    value = json.loads((FIXTURES / "catalog-v1.json").read_text())
    if later:
        value["nodes"]["model.mnemo_analytics.fct_orders"]["columns"]["amount"]["type"] = "NUMERIC"
    return DbtCatalogParser().parse(
        json.dumps(value),
        DbtSupplementalParseRequest(
            item_scope,
            "target/catalog.json",
            datetime(2026, 8, 5, 1, 2, tzinfo=UTC)
            + (timedelta(minutes=1) if later else timedelta()),
        ),
    )


def run_results(item_scope: MemoryScope) -> DbtRunResultsArtifact:
    return DbtRunResultsParser().parse(
        (FIXTURES / "run-results-v6.json").read_bytes(),
        DbtSupplementalParseRequest(
            item_scope,
            "target/run_results.json",
            datetime(2026, 8, 5, 1, 2, tzinfo=UTC),
        ),
    )


def source_freshness(item_scope: MemoryScope) -> DbtSourceFreshnessArtifact:
    return DbtSourceFreshnessParser().parse(
        (FIXTURES / "sources-v3.json").read_bytes(),
        DbtSupplementalParseRequest(
            item_scope,
            "target/sources.json",
            datetime(2026, 8, 5, 2, 1, tzinfo=UTC),
        ),
    )


@pytest.fixture(params=("reference", "sqlite"))
def repository(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[ProjectIndexRepository]:
    if request.param == "reference":
        yield ReferenceProjectIndexRepository()
        return
    sqlite_repository = SQLiteCheckpointRepository(
        tmp_path / "supplemental.sqlite3", base_directory=tmp_path
    )
    sqlite_repository.migrate()
    yield sqlite_repository


def test_supplemental_storage_is_scoped_idempotent_versioned_and_manifest_bound(
    repository: ProjectIndexRepository,
) -> None:
    item_scope = scope()
    snapshot_id = DbtSnapshotId.new()
    repository.store_and_activate(manifest(item_scope), snapshot_id)
    assert repository.get_catalog_projection(item_scope, snapshot_id) is None
    assert repository.get_run_results_projection(item_scope, snapshot_id) is None
    assert repository.get_source_freshness_projection(item_scope, snapshot_id) is None

    first_catalog = catalog(item_scope)
    stored_catalog = repository.store_catalog_projection(item_scope, snapshot_id, first_catalog)
    assert stored_catalog.idempotent is False
    assert repository.get_catalog_projection(item_scope, snapshot_id) == first_catalog
    retry_catalog = repository.store_catalog_projection(item_scope, snapshot_id, first_catalog)
    assert retry_catalog.idempotent is True

    latest_catalog = catalog(item_scope, later=True)
    repository.store_catalog_projection(item_scope, snapshot_id, latest_catalog)
    assert repository.get_catalog_projection(item_scope, snapshot_id) == latest_catalog

    execution = run_results(item_scope)
    stored_results = repository.store_run_results_projection(item_scope, snapshot_id, execution)
    assert stored_results.idempotent is False
    assert repository.get_run_results_projection(item_scope, snapshot_id) == execution
    assert repository.store_run_results_projection(item_scope, snapshot_id, execution).idempotent

    freshness = source_freshness(item_scope)
    stored_freshness = repository.store_source_freshness_projection(
        item_scope, snapshot_id, freshness
    )
    assert stored_freshness.idempotent is False
    assert repository.get_source_freshness_projection(item_scope, snapshot_id) == freshness
    assert repository.store_source_freshness_projection(
        item_scope, snapshot_id, freshness
    ).idempotent

    other_scope = scope(2)
    with pytest.raises(InvalidManifestSnapshotScope):
        repository.store_catalog_projection(item_scope, snapshot_id, catalog(other_scope))
    with pytest.raises(ManifestSnapshotNotFound):
        repository.get_catalog_projection(other_scope, snapshot_id)


def test_supplemental_storage_rejects_resources_absent_from_exact_manifest(
    repository: ProjectIndexRepository,
) -> None:
    item_scope = scope()
    snapshot_id = DbtSnapshotId.new()
    repository.store_and_activate(manifest(item_scope), snapshot_id)
    value = json.loads((FIXTURES / "catalog-v1.json").read_text())
    relation = value["nodes"].pop("model.mnemo_analytics.fct_orders")
    relation["unique_id"] = "model.mnemo_analytics.unknown"
    value["nodes"]["model.mnemo_analytics.unknown"] = relation
    unknown = DbtCatalogParser().parse(
        json.dumps(value),
        DbtSupplementalParseRequest(
            item_scope,
            "target/catalog.json",
            datetime(2026, 8, 5, tzinfo=UTC),
        ),
    )
    with pytest.raises(SupplementalArtifactConflict):
        repository.store_catalog_projection(item_scope, snapshot_id, unknown)
    assert repository.get_catalog_projection(item_scope, snapshot_id) is None


def test_sqlite_supplemental_projection_is_minimized_durable_and_atomic(tmp_path: Path) -> None:
    database = tmp_path / "supplemental-durable.sqlite3"
    repository = SQLiteCheckpointRepository(database, base_directory=tmp_path)
    repository.migrate()
    item_scope = scope()
    snapshot_id = DbtSnapshotId.new()
    repository.store_and_activate(manifest(item_scope), snapshot_id)
    repository.store_catalog_projection(item_scope, snapshot_id, catalog(item_scope))
    repository.store_run_results_projection(item_scope, snapshot_id, run_results(item_scope))
    repository.store_source_freshness_projection(
        item_scope, snapshot_id, source_freshness(item_scope)
    )

    reopened = SQLiteCheckpointRepository(database, base_directory=tmp_path)
    assert reopened.get_catalog_projection(item_scope, snapshot_id) == catalog(item_scope)
    assert reopened.get_run_results_projection(item_scope, snapshot_id) == run_results(item_scope)
    assert reopened.get_source_freshness_projection(item_scope, snapshot_id) == source_freshness(
        item_scope
    )
    with sqlite3.connect(database) as connection:
        dump = "\n".join(connection.iterdump())
        assert "secret-that-must-not-be-retained" not in dump
        assert "private_table" not in dump
        assert "warehouse-owner" not in dump
        assert "private database error" not in dump
        assert "private_filter_expression" not in dump
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        connection.execute(
            "CREATE TRIGGER reject_catalog_column BEFORE INSERT ON dbt_catalog_columns "
            "BEGIN SELECT RAISE(ABORT, 'synthetic catalog failure'); END"
        )
    second_snapshot = DbtSnapshotId.new()
    repository.store_and_activate(
        manifest(item_scope, later=True),
        second_snapshot,
        expected_active_snapshot_id=snapshot_id,
    )
    with pytest.raises(ProjectIndexStorageFailure):
        repository.store_catalog_projection(item_scope, second_snapshot, catalog(item_scope))
    assert repository.get_catalog_projection(item_scope, second_snapshot) is None


def test_supplemental_migration_rolls_back_as_one_step(tmp_path: Path) -> None:
    database = tmp_path / "supplemental-migration.sqlite3"
    repository = SQLiteCheckpointRepository(database, base_directory=tmp_path)
    repository.migrate()
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE dbt_manifest_activations")
        connection.execute("DROP TABLE dbt_source_freshness_results")
        connection.execute("DROP TABLE dbt_source_freshness_artifacts")
        connection.execute("DROP TABLE dbt_run_result_timings")
        connection.execute("DROP TABLE dbt_run_results")
        connection.execute("DROP TABLE dbt_catalog_columns")
        connection.execute("DROP TABLE dbt_catalog_relations")
        connection.execute("DROP TABLE dbt_supplemental_artifacts")
        connection.execute("DELETE FROM schema_migrations WHERE version >= 14")

    with pytest.raises(SQLiteMigrationError, match="injected migration failure"):
        repository.migrate(fail_after_version=14)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone() == (13,)
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                "AND name = 'dbt_supplemental_artifacts'"
            ).fetchone()
            is None
        )
    repository.migrate()
    assert repository.schema_version() == 23


def test_source_freshness_migration_rolls_back_as_one_step(tmp_path: Path) -> None:
    database = tmp_path / "freshness-migration.sqlite3"
    repository = SQLiteCheckpointRepository(database, base_directory=tmp_path)
    repository.migrate()
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE dbt_manifest_activations")
        connection.execute("DROP TABLE dbt_source_freshness_results")
        connection.execute("DROP TABLE dbt_source_freshness_artifacts")
        connection.execute("DELETE FROM schema_migrations WHERE version >= 16")

    with pytest.raises(SQLiteMigrationError, match="injected migration failure"):
        repository.migrate(fail_after_version=16)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone() == (15,)
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                "AND name = 'dbt_source_freshness_artifacts'"
            ).fetchone()
            is None
        )
    repository.migrate()
    assert repository.schema_version() == 23
