"""Source snapshot retention: keep what memory needs, delete the rest in short steps (spec §4)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mnemo_memory.apps.cli import main as cli
from mnemo_memory.connectors.automatic_memory.source_refresh import SourceRefreshLock
from mnemo_memory.packages.application.automatic_memory import (
    LocalMemoryProjectBindingStore,
    MemoryProjectBinding,
)
from mnemo_memory.packages.application.bootstrap import build_checkpoint_runtime
from mnemo_memory.packages.application.checkpoints import CreateCheckpoint
from mnemo_memory.packages.application.config import LocalConfig
from mnemo_memory.packages.application.unified_context import ContextSourceChangeQuery
from mnemo_memory.packages.domain import (
    CheckpointContent,
    CheckpointSourceObservation,
    CodeSnapshotId,
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    SourceId,
    SourceTrustClass,
    VerificationStatus,
)
from mnemo_memory.packages.project_index import (
    SourceImpactService,
    SourceStructureParser,
    SourceStructureParseRequest,
)
from mnemo_memory.packages.storage import (
    SourceSnapshotPrunePlan,
    SQLiteCheckpointRepository,
    SQLiteSourceStructureRepository,
)
from mnemo_memory.packages.storage.contracts import (
    SourceIndexStorageFailure,
    SourceSnapshotNotFound,
)
from mnemo_memory.packages.storage.sqlite import DEFAULT_KEPT_SOURCE_ACTIVATIONS


def _project(tmp_path: Path, name: str = "repo") -> tuple[Path, Path, MemoryProjectBinding]:
    project = tmp_path / name
    project.mkdir()
    (project / "client.py").write_text(
        "import service\n\n\ndef call():\n    return service.run()\n", encoding="utf-8"
    )
    data = tmp_path / "data"
    return project, data, LocalMemoryProjectBindingStore(data).enable(project)


def _repository(data: Path) -> SQLiteSourceStructureRepository:
    repository = SQLiteSourceStructureRepository(data / "mnemo.sqlite3")
    repository.migrate()
    return repository


def _store_version(
    repository: SQLiteSourceStructureRepository, binding: MemoryProjectBinding, version: int
) -> CodeSnapshotId:
    (binding.project_root / "service.py").write_text(
        f"def run():\n    return {version}\n", encoding="utf-8"
    )
    artifact = SourceStructureParser().parse(
        SourceStructureParseRequest(binding.scope, binding.project_root)
    )
    return repository.store_and_activate(artifact).snapshot.snapshot_id


def _seed(
    repository: SQLiteSourceStructureRepository, binding: MemoryProjectBinding, count: int
) -> list[CodeSnapshotId]:
    return [_store_version(repository, binding, version) for version in range(count)]


def _observe_from_checkpoint(
    data: Path, binding: MemoryProjectBinding, snapshot_id: CodeSnapshotId
) -> None:
    """Name one snapshot from a checkpoint revision, as the MCP save path does."""
    content = CheckpointContent(
        task_objective="Keep the co-observed source header.",
        completed_work=("Created a durable handoff.",),
        current_state="The exact source projection is attached as evidence.",
        remaining_work=(),
        decisions=("Do not infer a cause from a snapshot association.",),
        failures=(),
        blockers=(),
        relevant_files=("service.py",),
        relevant_artifacts=(),
        verification_performed=("focused test passed",),
        token_estimate=80,
    )
    evidence = EvidenceReference(
        EvidenceId.new(),
        SourceId.new(),
        EvidenceSourceType.CHECKPOINT,
        SourceTrustClass.USER_AUTHORED,
        "fixture://source-pruning/observation",
        "sha256:" + "e" * 64,
        EvidenceLocation("fixture://source-pruning/observation"),
        datetime(2026, 10, 3, tzinfo=UTC),
        VerificationStatus.VERIFIED,
    )
    with build_checkpoint_runtime(LocalConfig.defaults(data)) as runtime:
        view = runtime.checkpoint_service.create(
            CreateCheckpoint(binding.checkpoint_scope, content, (evidence,))
        )
        runtime.repository.append_checkpoint_source_observation(
            CheckpointSourceObservation(
                scope=view.aggregate.scope,
                checkpoint_id=view.aggregate.checkpoint_id,
                revision_id=view.revision.revision_id,
                source_snapshot_id=snapshot_id,
                observed_at=datetime(2026, 10, 3, tzinfo=UTC),
            )
        )


def _count(data: Path, table: str, snapshot_id: CodeSnapshotId) -> int:
    with sqlite3.connect(data / "mnemo.sqlite3") as connection:
        row = connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE snapshot_id = ?", (str(snapshot_id),)
        ).fetchone()
    return int(row[0])


def test_prune_keeps_the_active_snapshot_and_the_latest_17_activations(tmp_path: Path) -> None:
    _, data, binding = _project(tmp_path)
    repository = _repository(data)
    ids = _seed(repository, binding, 22)

    assert repository.prune_source_snapshots(binding.scope, max_snapshots=None) == 5

    for snapshot_id in ids[:5]:
        with pytest.raises(SourceSnapshotNotFound):
            repository.get_snapshot(binding.scope, snapshot_id)
        assert _count(data, "source_snapshot_activations", snapshot_id) == 0
    for snapshot_id in ids[5:]:
        assert repository.iter_symbols(binding.scope, snapshot_id)
    active = repository.get_active_snapshot(binding.scope)
    assert active is not None and active.snapshot_id == ids[-1]
    assert len(repository.list_activation_history(binding.scope, limit=100)) == 17
    assert repository.prune_source_snapshots(binding.scope, max_snapshots=None) == 0


def test_a_reactivated_old_snapshot_counts_as_a_recent_activation(tmp_path: Path) -> None:
    _, data, binding = _project(tmp_path)
    repository = _repository(data)
    ids = _seed(repository, binding, 20)
    assert _store_version(repository, binding, 0) == ids[0]  # back at version 0's digest

    assert repository.prune_source_snapshots(binding.scope, max_snapshots=None) == 3

    assert repository.iter_symbols(binding.scope, ids[0])
    for snapshot_id in ids[1:4]:
        with pytest.raises(SourceSnapshotNotFound):
            repository.get_snapshot(binding.scope, snapshot_id)


def test_a_checkpoint_named_snapshot_keeps_only_its_header(tmp_path: Path) -> None:
    _, data, binding = _project(tmp_path)
    repository = _repository(data)
    ids = _seed(repository, binding, 20)
    _observe_from_checkpoint(data, binding, ids[0])
    assert _count(data, "source_structure_edges", ids[0]) > 0

    assert repository.prune_source_snapshots(binding.scope, max_snapshots=None) == 3

    header = repository.get_snapshot(binding.scope, ids[0])
    assert header.symbol_count > 0
    for table in ("source_structure_files", "source_structure_symbols", "source_structure_edges"):
        assert _count(data, table, ids[0]) == 0
    assert _count(data, "source_snapshot_activations", ids[0]) == 1
    assert _count(data, "source_snapshot_activations", ids[1]) == 0
    assert repository.prune_source_snapshots(binding.scope, max_snapshots=None) == 0


def test_pruning_respects_foreign_keys_and_the_per_run_limit(tmp_path: Path) -> None:
    _, data, binding = _project(tmp_path)
    repository = _repository(data)
    ids = _seed(repository, binding, 27)

    assert repository.prune_source_snapshots(binding.scope, max_snapshots=4) == 4
    for snapshot_id in ids[:4]:  # oldest first
        with pytest.raises(SourceSnapshotNotFound):
            repository.get_snapshot(binding.scope, snapshot_id)
    assert repository.iter_symbols(binding.scope, ids[4])
    assert repository.prune_source_snapshots(binding.scope, max_snapshots=4) == 4
    assert repository.prune_source_snapshots(binding.scope, max_snapshots=4) == 2
    assert repository.prune_source_snapshots(binding.scope, max_snapshots=4) == 0

    assert (
        SQLiteCheckpointRepository(data / "mnemo.sqlite3").connection_settings()["foreign_keys"]
        == 1
    )
    with sqlite3.connect(data / "mnemo.sqlite3") as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_source_changes_and_latest_transition_still_work_after_pruning(tmp_path: Path) -> None:
    _, data, binding = _project(tmp_path)
    repository = _repository(data)
    _seed(repository, binding, 25)
    repository.prune_source_snapshots(binding.scope, max_snapshots=None)

    transition = repository.latest_transition(binding.scope)
    assert transition is not None
    history = repository.list_activation_history(binding.scope, limit=17)
    assert len(history) == 17
    service = SourceImpactService(repository)
    for index in range(len(history) - 1):
        diff = service.diff(
            binding.scope, history[index + 1].snapshot_id, history[index].snapshot_id
        )
        assert [item.relative_path for item in diff.modified_files] == ["service.py"]


def test_a_tree_back_at_a_header_only_digest_restores_its_map(tmp_path: Path) -> None:
    """Review focus 1: the old digest's header is reused, so its rows must come back."""
    _, data, binding = _project(tmp_path)
    repository = _repository(data)
    ids = _seed(repository, binding, 20)
    _observe_from_checkpoint(data, binding, ids[0])
    repository.prune_source_snapshots(binding.scope, max_snapshots=None)

    assert _store_version(repository, binding, 0) == ids[0]

    active = repository.get_active_snapshot(binding.scope)
    assert active is not None and active.snapshot_id == ids[0]
    assert len(repository.iter_symbols(binding.scope, ids[0])) == active.symbol_count > 0
    assert len(repository.iter_files(binding.scope, ids[0])) == active.file_count
    assert len(repository.iter_edges(binding.scope, ids[0])) == active.edge_count > 0


def test_a_pinned_read_of_a_header_only_snapshot_is_not_found(tmp_path: Path) -> None:
    """Review focus 2: never diff against an emptied snapshot as if it were real."""
    _, data, binding = _project(tmp_path)
    repository = _repository(data)
    ids = _seed(repository, binding, 20)
    _observe_from_checkpoint(data, binding, ids[0])
    repository.prune_source_snapshots(binding.scope, max_snapshots=None)

    assert repository.get_snapshot(binding.scope, ids[0]).snapshot_id == ids[0]
    with pytest.raises(SourceSnapshotNotFound):
        repository.iter_symbols(binding.scope, ids[0])
    with pytest.raises(SourceSnapshotNotFound):
        repository.iter_files(binding.scope, ids[0])
    with pytest.raises(SourceSnapshotNotFound):
        repository.iter_edges(binding.scope, ids[0])
    with pytest.raises(SourceSnapshotNotFound):
        SourceImpactService(repository).diff(binding.scope, ids[0], ids[-1])


def test_a_snapshot_reactivated_after_listing_is_never_pruned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review focus 3: a concurrent save re-activates an old digest between list and delete."""
    _, data, binding = _project(tmp_path)
    repository = _repository(data)
    ids = _seed(repository, binding, 18)
    stale = repository._prune_candidates(binding.scope, 17)
    assert [candidate.snapshot_id for candidate in stale] == [str(ids[0])]
    _store_version(repository, binding, 0)
    monkeypatch.setattr(
        SQLiteSourceStructureRepository, "_prune_candidates", lambda self, scope, keep: stale
    )

    assert repository.prune_source_snapshots(binding.scope, max_snapshots=None) == 0
    assert repository.iter_symbols(binding.scope, ids[0])


def test_the_prune_plan_counts_without_changing_anything(tmp_path: Path) -> None:
    _, data, binding = _project(tmp_path)
    repository = _repository(data)
    ids = _seed(repository, binding, 20)
    _observe_from_checkpoint(data, binding, ids[0])

    plan = repository.plan_source_snapshot_prune(binding.scope)

    assert plan == SourceSnapshotPrunePlan(
        full_snapshots=2,
        header_only_snapshots=1,
        symbols=sum(_count(data, "source_structure_symbols", item) for item in ids[:3]),
        edges=sum(_count(data, "source_structure_edges", item) for item in ids[:3]),
    )
    assert plan.symbols > 0 and plan.edges > 0
    for snapshot_id in ids:
        assert repository.iter_symbols(binding.scope, snapshot_id)


def test_every_source_scope_is_listed_once(tmp_path: Path) -> None:
    _, data, first = _project(tmp_path, "first")
    second_root = tmp_path / "second"
    second_root.mkdir()
    (second_root / "client.py").write_text("x = 1\n", encoding="utf-8")
    second = LocalMemoryProjectBindingStore(data).enable(second_root)
    repository = _repository(data)
    _seed(repository, first, 2)
    _store_version(repository, second, 0)

    assert set(repository.list_source_scopes()) == {first.scope, second.scope}


def test_prune_bounds_must_be_positive(tmp_path: Path) -> None:
    _, data, binding = _project(tmp_path)
    repository = _repository(data)
    with pytest.raises(ValueError):
        repository.prune_source_snapshots(binding.scope, keep_activations=0, max_snapshots=None)
    with pytest.raises(ValueError):
        repository.prune_source_snapshots(binding.scope, max_snapshots=0)


def _accepts_transitions(maximum: int) -> bool:
    try:
        ContextSourceChangeQuery(maximum_transitions=maximum)
    except ValueError:
        return False
    return True


def test_kept_activations_cover_the_largest_source_changes_request() -> None:
    """N transitions need N + 1 activations' snapshots, so the one kept count must cover it."""
    largest = max(n for n in range(1, 101) if _accepts_transitions(n))

    assert largest == 16
    assert largest + 1 <= DEFAULT_KEPT_SOURCE_ACTIVATIONS


runner = CliRunner()


def _footprint(data: Path) -> int:
    total = 0
    for name in ("mnemo.sqlite3", "mnemo.sqlite3-wal"):
        path = data / name
        if path.exists():
            total += path.stat().st_size
    return total


def _widen(project: Path) -> None:
    """Enough stable symbols that pruning 13 snapshots frees many database pages."""
    for module in range(12):
        body = "\n\n".join(
            f"def helper_{module}_{index}():\n    return {index}" for index in range(25)
        )
        (project / f"module_{module}.py").write_text(body + "\n", encoding="utf-8")


def test_a_dry_run_prints_counts_and_changes_nothing(tmp_path: Path) -> None:
    project, data, binding = _project(tmp_path)
    repository = _repository(data)
    ids = _seed(repository, binding, 20)

    result = runner.invoke(
        cli.app,
        [
            "maintenance",
            "prune-source",
            "--project-root",
            str(project),
            "--dry-run",
            "--data-dir",
            str(data),
        ],
    )

    assert result.exit_code == 0, result.output
    shown = json.loads(result.output)
    assert shown == {
        "dry_run": True,
        "projects": 1,
        "full_snapshots": 3,
        "header_only_snapshots": 0,
        "symbols": sum(_count(data, "source_structure_symbols", item) for item in ids[:3]),
        "edges": sum(_count(data, "source_structure_edges", item) for item in ids[:3]),
        "database_bytes": shown["database_bytes"],
    }
    assert shown["database_bytes"] > 0
    for snapshot_id in ids:
        assert repository.iter_symbols(binding.scope, snapshot_id)


def test_the_command_refuses_while_a_refresh_worker_holds_the_lock(tmp_path: Path) -> None:
    project, data, binding = _project(tmp_path)
    repository = _repository(data)
    ids = _seed(repository, binding, 20)

    with SourceRefreshLock(data).hold() as held:
        assert held
        result = runner.invoke(
            cli.app,
            [
                "maintenance",
                "prune-source",
                "--project-root",
                str(project),
                "--data-dir",
                str(data),
            ],
        )

    assert result.exit_code == 2
    assert "MNEMO_SOURCE_REFRESH_RUNNING" in result.output
    assert repository.iter_symbols(binding.scope, ids[0])


def test_compact_refuses_when_free_space_is_below_the_database_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, data, binding = _project(tmp_path)
    repository = _repository(data)
    ids = _seed(repository, binding, 20)
    monkeypatch.setattr(cli, "_free_disk_bytes", lambda directory: 0)

    result = runner.invoke(
        cli.app,
        [
            "maintenance",
            "prune-source",
            "--project-root",
            str(project),
            "--compact",
            "--data-dir",
            str(data),
        ],
    )

    assert result.exit_code == 2
    assert "MNEMO_COMPACT_INSUFFICIENT_DISK_SPACE" in result.output
    assert repository.iter_symbols(binding.scope, ids[0])  # refused before pruning anything


def test_compact_shrinks_a_seeded_database_file(tmp_path: Path) -> None:
    project, data, binding = _project(tmp_path)
    _widen(project)
    repository = _repository(data)
    _seed(repository, binding, 30)
    before = _footprint(data)

    result = runner.invoke(
        cli.app,
        [
            "maintenance",
            "prune-source",
            "--project-root",
            str(project),
            "--compact",
            "--data-dir",
            str(data),
        ],
    )

    assert result.exit_code == 0, result.output
    shown = json.loads(result.output)
    assert shown["pruned_snapshots"] == 13
    assert shown["compacted"] is True
    assert shown["database_bytes_after"] < shown["database_bytes_before"]
    assert _footprint(data) < before


def test_a_failed_compaction_still_reports_what_was_pruned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, data, binding = _project(tmp_path)
    repository = _repository(data)
    ids = _seed(repository, binding, 20)

    def busy(self: SQLiteSourceStructureRepository) -> None:
        raise SourceIndexStorageFailure("source index compaction failed")

    monkeypatch.setattr(SQLiteSourceStructureRepository, "vacuum", busy)

    result = runner.invoke(
        cli.app,
        [
            "maintenance",
            "prune-source",
            "--project-root",
            str(project),
            "--compact",
            "--data-dir",
            str(data),
        ],
    )

    assert result.exit_code == 1
    shown = json.loads(result.output)
    assert shown == {
        "dry_run": False,
        "projects": 1,
        "pruned_snapshots": 3,
        "compacted": False,
        "error": "MNEMO_COMPACT_UNAVAILABLE",
        "database_bytes_before": shown["database_bytes_before"],
        "database_bytes_after": shown["database_bytes_after"],
    }
    assert str(project) not in result.output
    assert "compaction failed" not in result.output
    with pytest.raises(SourceSnapshotNotFound):
        repository.iter_symbols(binding.scope, ids[0])  # the prune itself stays done


def test_all_projects_prunes_every_scope(tmp_path: Path) -> None:
    _, data, first = _project(tmp_path, "first")
    second_root = tmp_path / "second"
    second_root.mkdir()
    (second_root / "client.py").write_text(
        "import service\n\n\ndef call():\n    return service.run()\n", encoding="utf-8"
    )
    second = LocalMemoryProjectBindingStore(data).enable(second_root)
    repository = _repository(data)
    _seed(repository, first, 19)
    _seed(repository, second, 19)

    result = runner.invoke(
        cli.app, ["maintenance", "prune-source", "--all-projects", "--data-dir", str(data)]
    )

    assert result.exit_code == 0, result.output
    shown = json.loads(result.output)
    assert (shown["projects"], shown["pruned_snapshots"], shown["compacted"]) == (2, 4, False)


def test_conflicting_or_unknown_projects_are_refused(tmp_path: Path) -> None:
    project, data, binding = _project(tmp_path)
    _seed(_repository(data), binding, 2)
    unknown = tmp_path / "unknown"
    unknown.mkdir()

    both = runner.invoke(
        cli.app,
        [
            "maintenance",
            "prune-source",
            "--project-root",
            str(project),
            "--all-projects",
            "--data-dir",
            str(data),
        ],
    )
    missing = runner.invoke(
        cli.app,
        ["maintenance", "prune-source", "--project-root", str(unknown), "--data-dir", str(data)],
    )

    assert both.exit_code == 2 and "MNEMO_PRUNE_SOURCE_ARGUMENTS_INVALID" in both.output
    assert missing.exit_code == 2 and "MNEMO_MEMORY_PROJECT_NOT_ENABLED" in missing.output
