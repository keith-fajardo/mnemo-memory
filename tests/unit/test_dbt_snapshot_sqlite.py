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
    assert (
        reopened.find_nodes_by_original_file_path(
            scope(), stored.snapshot.snapshot_id, "models/not-recorded.sql"
        )
        == ()
    )
    with sqlite3.connect(item.path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
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
