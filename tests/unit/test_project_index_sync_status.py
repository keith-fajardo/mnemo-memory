from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mnemo_memory.apps.api.dashboard import _compare_source_observations
from mnemo_memory.connectors.automatic_memory.git_observation import GitSourceObservation
from mnemo_memory.connectors.dbt.manifest import DbtManifestParser, ManifestParseRequest
from mnemo_memory.packages.domain import (
    CodeStructureArtifact,
    DbtSnapshotId,
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.domain.dbt_manifest import DbtManifestArtifact
from mnemo_memory.packages.project_index import PythonSourceParser, PythonSourceParseRequest
from mnemo_memory.packages.storage import (
    ActiveSnapshotConflict,
    ReferenceKnowledgeDocumentRepository,
    ReferenceProjectIndexRepository,
    ReferenceSourceStructureRepository,
    SQLiteCheckpointRepository,
    SQLiteKnowledgeDocumentRepository,
    SQLiteMigrationError,
    SQLiteSourceStructureRepository,
)
from mnemo_memory.packages.storage import sqlite as sqlite_module

FIXTURE = Path(__file__).parents[1] / "fixtures" / "dbt" / "manifest-v12.json"
NOW = datetime(2026, 8, 5, 9, 30, tzinfo=UTC)
DIGEST = "sha256:" + "a" * 64
COMMIT = "b" * 40


def scope(project: str = "00000000-0000-4000-8002-000000000301") -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string("00000000-0000-4000-8000-000000000301"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string("00000000-0000-4000-8001-000000000301"),
        ProjectId.from_string(project),
    )


def source_artifact(root: Path) -> CodeStructureArtifact:
    root.mkdir()
    (root / "main.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    return PythonSourceParser().parse(PythonSourceParseRequest(scope(), root.resolve()))


def dbt_artifact(stamp: int = 0) -> DbtManifestArtifact:
    raw = FIXTURE.read_text(encoding="utf-8").replace("customer-stage", f"customer-stage-{stamp}")
    return DbtManifestParser().parse(
        raw,
        ManifestParseRequest(
            scope(),
            "fixtures/dbt/manifest-v12.json",
            NOW + timedelta(seconds=stamp),
        ),
    )


def test_reference_indexes_record_empty_and_idempotent_successes(tmp_path: Path) -> None:
    source_times = iter((NOW, NOW + timedelta(seconds=1)))
    source = ReferenceSourceStructureRepository(clock=lambda: next(source_times))
    parsed = source_artifact(tmp_path / "source")
    assert source.last_sync_at(scope()) is None
    source.store_and_activate(parsed)
    assert source.last_sync_at(scope()) == NOW
    source.store_and_activate(parsed)
    assert source.last_sync_at(scope()) == NOW + timedelta(seconds=1)

    knowledge = ReferenceKnowledgeDocumentRepository(clock=lambda: NOW)
    knowledge.apply_sync(scope(), (), ())
    assert knowledge.last_sync_at(scope()) == NOW

    dbt = ReferenceProjectIndexRepository(clock=lambda: NOW)
    stored = dbt.store_and_activate(dbt_artifact(), DbtSnapshotId.new())
    assert stored.idempotent is False
    assert dbt.last_sync_at(scope()) == NOW
    assert source.last_sync_at(scope("00000000-0000-4000-8002-000000000302")) is None


def test_sqlite_index_sync_status_is_atomic_scoped_and_persistent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "profile" / "mnemo.sqlite3"
    dbt = SQLiteCheckpointRepository(database, base_directory=tmp_path / "profile")
    dbt.migrate()
    source = SQLiteSourceStructureRepository(database, base_directory=tmp_path / "profile")
    knowledge = SQLiteKnowledgeDocumentRepository(database, base_directory=tmp_path / "profile")
    parsed = source_artifact(tmp_path / "source")
    observed = [NOW]
    monkeypatch.setattr(sqlite_module, "_timestamp", lambda: observed[0].isoformat())

    assert source.last_sync_at(scope()) is None
    assert knowledge.last_sync_at(scope()) is None
    assert dbt.last_sync_at(scope()) is None
    source.store_and_activate(parsed)
    assert source.last_sync_at(scope()) == NOW

    observed[0] = NOW + timedelta(seconds=1)
    source.store_and_activate(parsed)
    assert source.last_sync_at(scope()) == observed[0]
    observed[0] = NOW + timedelta(seconds=2)
    knowledge.apply_sync(scope(), (), ())
    assert knowledge.last_sync_at(scope()) == observed[0]
    observed[0] = NOW + timedelta(seconds=3)
    stored = dbt.store_and_activate(dbt_artifact(), DbtSnapshotId.new())
    assert dbt.last_sync_at(scope()) == observed[0]

    observed[0] = NOW + timedelta(seconds=4)
    with pytest.raises(ActiveSnapshotConflict):
        dbt.store_and_activate(dbt_artifact(1), DbtSnapshotId.new())
    assert dbt.last_sync_at(scope()) == NOW + timedelta(seconds=3)
    foreign = scope("00000000-0000-4000-8002-000000000302")
    assert dbt.last_sync_at(foreign) is None

    reopened = SQLiteCheckpointRepository(database, base_directory=tmp_path / "profile")
    assert reopened.last_sync_at(scope()) == NOW + timedelta(seconds=3)
    repeated = reopened.store_and_activate(
        dbt_artifact(),
        DbtSnapshotId.new(),
        expected_active_snapshot_id=stored.snapshot.snapshot_id,
    )
    assert repeated.idempotent is True
    assert reopened.last_sync_at(scope()) == NOW + timedelta(seconds=4)


def test_migration_28_rolls_back_and_retries_from_schema_27(tmp_path: Path) -> None:
    database = tmp_path / "migration.sqlite3"
    repository = SQLiteCheckpointRepository(database, base_directory=tmp_path)
    repository.migrate()
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER project_index_sync_scope_match_update")
        connection.execute("DROP TRIGGER project_index_sync_scope_match_insert")
        connection.execute("DROP TABLE project_index_sync_status")
        connection.execute("DROP TRIGGER checkpoint_deletion_prevents_resurrection")
        connection.execute("DROP TRIGGER checkpoint_aggregate_delete_requires_tombstone")
        connection.execute("DROP TRIGGER checkpoint_revision_delete_requires_tombstone")
        connection.execute("DROP TRIGGER checkpoint_event_delete_requires_tombstone")
        connection.execute("DROP TRIGGER checkpoint_observation_delete_requires_tombstone")
        connection.execute("DROP TABLE checkpoint_deletions")
        connection.execute("DELETE FROM schema_migrations WHERE version IN (28, 29, 30, 31)")

    assert repository.schema_version() == 27
    with pytest.raises(SQLiteMigrationError, match="injected migration failure"):
        repository.migrate(fail_after_version=28)
    assert repository.schema_version() == 27
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'project_index_sync_status'"
            ).fetchone()
            is None
        )

    repository.migrate()
    assert repository.schema_version() == 31


def test_source_staleness_requires_content_free_git_proof() -> None:
    clean = GitSourceObservation(DIGEST, COMMIT, None, False)
    dirty = GitSourceObservation(DIGEST, COMMIT, None, True)
    changed = GitSourceObservation(DIGEST, "c" * 40, COMMIT, False)

    assert _compare_source_observations(clean, clean) == "current"
    assert _compare_source_observations(clean, dirty) == "stale"
    assert _compare_source_observations(clean, changed) == "stale"
    assert _compare_source_observations(dirty, dirty) == "unknown"
    assert _compare_source_observations(None, clean) == "unknown"
