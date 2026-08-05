"""SQLite-specific integrity and atomic activation coverage for dbt snapshots."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mnemo_memory.connectors.dbt.manifest import DbtManifestParser, ManifestParseRequest
from mnemo_memory.packages.domain import (
    DbtSnapshotId,
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.domain.dbt_manifest import DbtManifestArtifact
from mnemo_memory.packages.storage import ActiveSnapshotConflict, SQLiteCheckpointRepository
from mnemo_memory.packages.storage.sqlite import SQLiteMigrationError

FIXTURE = Path(__file__).parents[1] / "fixtures" / "dbt" / "manifest-v12.json"


def scope() -> MemoryScope:
    return MemoryScope(
        owner_id=OwnerId.from_string("00000000-0000-4000-8000-000000000201"),
        level=ScopeLevel.PROJECT,
        visibility=Visibility.PROJECT,
        workspace_id=WorkspaceId.from_string("00000000-0000-4000-8001-000000000201"),
        project_id=ProjectId.from_string("00000000-0000-4000-8002-000000000201"),
    )


def artifact(*, stamp: int = 0) -> DbtManifestArtifact:
    raw = FIXTURE.read_text().replace("customer-stage", f"customer-stage-{stamp}")
    return DbtManifestParser().parse(
        raw,
        ManifestParseRequest(
            scope=scope(),
            source_identity="fixtures/dbt/manifest-v12.json",
            ingested_at=datetime(2026, 8, 2, tzinfo=UTC) + timedelta(seconds=stamp),
        ),
    )


def repository(tmp_path: Path) -> SQLiteCheckpointRepository:
    item = SQLiteCheckpointRepository(tmp_path / "snapshot.sqlite3", base_directory=tmp_path)
    item.migrate()
    return item


def test_snapshot_projection_reopens_and_has_foreign_key_integrity(tmp_path: Path) -> None:
    item = repository(tmp_path)
    stored = item.store_and_activate(artifact(), DbtSnapshotId.new())
    reopened = SQLiteCheckpointRepository(item.path, base_directory=tmp_path)
    restored = reopened.get_snapshot(scope(), stored.snapshot.snapshot_id)
    assert restored.metadata.content_digest == stored.snapshot.metadata.content_digest
    assert restored.node_count == stored.snapshot.node_count
    matches = reopened.find_nodes_by_original_file_path(
        scope(), stored.snapshot.snapshot_id, "models/marts/fct_orders.sql"
    )
    assert [str(node.unique_id) for node in matches] == ["model.mnemo_analytics.fct_orders"]
    exposure = reopened.find_nodes_by_original_file_path(
        scope(), stored.snapshot.snapshot_id, "models/exposures.yml"
    )
    assert [node.resource_type.value for node in exposure] == ["exposure"]
    affected = reopened.direct_downstream(
        scope(),
        stored.snapshot.snapshot_id,
        next(
            node.unique_id
            for node in reopened.iter_nodes(scope(), stored.snapshot.snapshot_id)
            if str(node.unique_id) == "model.mnemo_analytics.mart_customer_value"
        ),
    )
    assert {str(edge.child_id) for edge in affected} == {
        "exposure.mnemo_analytics.order_dashboard",
        "semantic_model.mnemo_analytics.customer_value",
    }
    metric = reopened.find_nodes_by_original_file_path(
        scope(), stored.snapshot.snapshot_id, "models/metrics.yml"
    )
    assert [node.resource_type.value for node in metric] == ["metric"]
    macro_edges = [
        edge
        for edge in reopened.iter_edges(scope(), stored.snapshot.snapshot_id)
        if edge.edge_type.value == "dbt_macro_dependency"
    ]
    assert len(macro_edges) == 2
    assert any(
        str(edge.child_id) == "model.mnemo_analytics.mart_customer_value" for edge in macro_edges
    )
    assert (
        reopened.find_nodes_by_original_file_path(
            scope(), stored.snapshot.snapshot_id, "models/not-recorded.sql"
        )
        == ()
    )
    with sqlite3.connect(item.path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        assert "private-macro-body" not in "\n".join(connection.iterdump())
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO dbt_manifest_edges VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(stored.snapshot.snapshot_id),
                    "missing",
                    "also-missing",
                    "dbt_dependency",
                    "0" * 64,
                    "{}",
                ),
            )


def test_v15_edge_constraint_upgrade_rolls_back_atomically(tmp_path: Path) -> None:
    item = repository(tmp_path)
    with sqlite3.connect(item.path) as connection:
        connection.execute("DROP TABLE dbt_manifest_activations")
        connection.execute("DROP TABLE dbt_source_freshness_results")
        connection.execute("DROP TABLE dbt_source_freshness_artifacts")
        connection.execute("DELETE FROM schema_migrations WHERE version >= 15")
        connection.execute("ALTER TABLE dbt_manifest_edges RENAME TO dbt_manifest_edges_v15")
        connection.execute(
            "CREATE TABLE dbt_manifest_edges ("
            "snapshot_id TEXT NOT NULL "
            "REFERENCES dbt_manifest_snapshots(snapshot_id) ON DELETE RESTRICT,"
            "parent_unique_id TEXT NOT NULL, child_unique_id TEXT NOT NULL,"
            "edge_type TEXT NOT NULL CHECK (edge_type = 'dbt_dependency'),"
            "artifact_digest TEXT NOT NULL CHECK (length(artifact_digest) = 64),"
            "evidence_json TEXT NOT NULL,"
            "PRIMARY KEY (snapshot_id, parent_unique_id, child_unique_id, edge_type),"
            "FOREIGN KEY (snapshot_id, parent_unique_id) "
            "REFERENCES dbt_manifest_nodes(snapshot_id, unique_id) ON DELETE RESTRICT,"
            "FOREIGN KEY (snapshot_id, child_unique_id) "
            "REFERENCES dbt_manifest_nodes(snapshot_id, unique_id) ON DELETE RESTRICT)"
        )
        connection.execute("INSERT INTO dbt_manifest_edges SELECT * FROM dbt_manifest_edges_v15")
        connection.execute("DROP TABLE dbt_manifest_edges_v15")

    assert item.schema_version() == 14
    with pytest.raises(SQLiteMigrationError):
        item.migrate(fail_after_version=15)
    assert item.schema_version() == 14
    with sqlite3.connect(item.path) as connection:
        sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'dbt_manifest_edges'"
        ).fetchone()[0]
    assert "dbt_macro_dependency" not in sql

    item.migrate()
    assert item.schema_version() == 22


def test_stale_expected_activation_rolls_back_losing_snapshot(tmp_path: Path) -> None:
    first_repository = repository(tmp_path)
    initial = first_repository.store_and_activate(artifact(), DbtSnapshotId.new())
    second_repository = SQLiteCheckpointRepository(first_repository.path, base_directory=tmp_path)
    winner = first_repository.store_and_activate(
        artifact(stamp=1),
        DbtSnapshotId.new(),
        expected_active_snapshot_id=initial.snapshot.snapshot_id,
    )
    with pytest.raises(ActiveSnapshotConflict):
        second_repository.store_and_activate(
            artifact(stamp=2),
            DbtSnapshotId.new(),
            expected_active_snapshot_id=initial.snapshot.snapshot_id,
        )
    assert second_repository.get_active_snapshot(scope()).snapshot_id == winner.snapshot.snapshot_id  # type: ignore[union-attr]
    assert len(second_repository.list_snapshots(scope()).items) == 2


def test_dbt_activation_history_migration_rolls_back_as_one_step(tmp_path: Path) -> None:
    item = repository(tmp_path)
    stored = item.store_and_activate(artifact(), DbtSnapshotId.new())
    with sqlite3.connect(item.path) as connection:
        connection.execute("DROP TABLE event_outbox")
        connection.execute("DROP TABLE dbt_manifest_activations")
        connection.execute("DELETE FROM schema_migrations WHERE version >= 17")

    with pytest.raises(SQLiteMigrationError, match="injected migration failure"):
        item.migrate(fail_after_version=17)

    assert item.schema_version() == 16
    with sqlite3.connect(item.path) as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                "AND name = 'dbt_manifest_activations'"
            ).fetchone()
            is None
        )
    item.migrate()
    assert item.schema_version() == 22
    assert item.latest_transition(scope()) is None
    changed = item.store_and_activate(
        artifact(stamp=1),
        DbtSnapshotId.new(),
        expected_active_snapshot_id=stored.snapshot.snapshot_id,
    )
    transition = item.latest_transition(scope())
    assert transition is not None
    assert [value.snapshot_id for value in transition] == [
        stored.snapshot.snapshot_id,
        changed.snapshot.snapshot_id,
    ]
