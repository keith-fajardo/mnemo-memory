"""The background code-map refresh: one worker at a time, bounded parses, silent (spec §3)."""

from __future__ import annotations

import itertools
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from mnemo_memory.apps.cli import source_refresh_entry
from mnemo_memory.connectors.automatic_memory import source_refresh
from mnemo_memory.connectors.automatic_memory.git_observation import (
    GitObservationStore,
    GitSourceObserver,
)
from mnemo_memory.connectors.automatic_memory.source_observation import source_snapshot_is_fresh
from mnemo_memory.connectors.automatic_memory.source_refresh import (
    SourceRefreshLock,
    SourceRefreshOutcome,
    run_source_refresh,
)
from mnemo_memory.packages.application.automatic_memory import (
    LocalMemoryProjectBindingStore,
    MemoryProjectBinding,
)
from mnemo_memory.packages.project_index import SourceStructureParser, SourceStructureParseRequest
from mnemo_memory.packages.storage import SQLiteSourceStructureRepository

_NO_GIT = GitSourceObserver(lambda arguments, root: None)


def _project(tmp_path: Path, name: str = "repo") -> tuple[Path, Path, MemoryProjectBinding]:
    project = tmp_path / name
    project.mkdir(parents=True)
    (project / "service.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    data = tmp_path / "data"
    return project, data, LocalMemoryProjectBindingStore(data).enable(project)


def _record_parses(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    parsed: list[str] = []
    real = SourceStructureParser.parse

    def recording(self: SourceStructureParser, request: SourceStructureParseRequest) -> Any:
        parsed.append("parse")
        return real(self, request)

    monkeypatch.setattr(SourceStructureParser, "parse", recording)
    return parsed


def test_the_worker_parses_stores_and_writes_the_scan_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, data, binding = _project(tmp_path)
    parsed = _record_parses(monkeypatch)

    assert run_source_refresh(data, project, git_observer=_NO_GIT) == SourceRefreshOutcome(
        ran=True, parses=1, pruned=0
    )

    assert parsed == ["parse"]
    active = SQLiteSourceStructureRepository(data / "mnemo.sqlite3").get_active_snapshot(
        binding.scope
    )
    assert active is not None and active.symbol_count == 2
    cache = (data / "scan-cache" / f"{binding.scope.project_id}.txt").read_text(encoding="utf-8")
    assert cache.endswith(str(active.snapshot_id))
    assert source_snapshot_is_fresh(binding, active.snapshot_id, cache_dir=data / "scan-cache")
    assert run_source_refresh(data, project, git_observer=_NO_GIT).parses == 0  # already fresh
    assert parsed == ["parse"]


def test_a_second_worker_leaves_at_once_while_the_lock_is_held(tmp_path: Path) -> None:
    project, data, _ = _project(tmp_path)
    with SourceRefreshLock(data).hold() as held:
        assert held
        assert SourceRefreshLock(data).running()
        assert run_source_refresh(data, project, git_observer=_NO_GIT) == SourceRefreshOutcome(
            ran=False
        )
    assert not (data / "mnemo.sqlite3").exists()
    assert not SourceRefreshLock(data).running()


def test_the_worker_re_checks_a_tree_that_changed_during_the_parse(tmp_path: Path) -> None:
    project, data, _ = _project(tmp_path)
    fingerprints = iter(["sha256:a", "sha256:b", "sha256:b", "sha256:b"])

    outcome = run_source_refresh(
        data, project, git_observer=_NO_GIT, fingerprint=lambda root: next(fingerprints)
    )

    assert outcome.parses == 2


def test_the_worker_parses_at_most_three_times_per_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, data, _ = _project(tmp_path)
    parsed = _record_parses(monkeypatch)
    counter = itertools.count()

    outcome = run_source_refresh(
        data, project, git_observer=_NO_GIT, fingerprint=lambda root: f"sha256:{next(counter)}"
    )

    assert outcome.parses == 3
    assert parsed == ["parse", "parse", "parse"]


def test_the_worker_records_git_state_for_the_new_digest(tmp_path: Path) -> None:
    project, data, binding = _project(tmp_path)
    commit = "a" * 40
    answers: dict[tuple[str, ...], str] = {
        ("rev-parse", "--is-inside-work-tree"): "true",
        ("rev-parse", "--verify", "HEAD"): commit,
        ("status", "--porcelain=v1", "-z"): "",
    }

    run_source_refresh(
        data,
        project,
        git_observer=GitSourceObserver(lambda arguments, root: answers.get(arguments)),
    )

    active = SQLiteSourceStructureRepository(data / "mnemo.sqlite3").get_active_snapshot(
        binding.scope
    )
    assert active is not None
    observation = GitObservationStore(data).get(binding.scope, active.source_digest)
    assert observation is not None
    assert (observation.commit_id, observation.dirty) == (commit, False)


def test_the_worker_prunes_one_bounded_step(tmp_path: Path) -> None:
    project, data, binding = _project(tmp_path)
    repository = SQLiteSourceStructureRepository(data / "mnemo.sqlite3")
    repository.migrate()
    for version in range(24):
        (project / "service.py").write_text(f"def run():\n    return {version}\n", encoding="utf-8")
        repository.store_and_activate(
            SourceStructureParser().parse(SourceStructureParseRequest(binding.scope, project))
        )

    outcome = run_source_refresh(data, project, git_observer=_NO_GIT)

    assert outcome.pruned == 4
    assert repository.prune_source_snapshots(binding.scope, max_snapshots=None) == 3


def test_a_worker_for_a_disabled_or_deleted_project_does_nothing(tmp_path: Path) -> None:
    """Review focus 5: the project changed between the hook and the worker."""
    project, data, _ = _project(tmp_path)
    assert LocalMemoryProjectBindingStore(data).disable(project)
    assert run_source_refresh(data, project, git_observer=_NO_GIT) == SourceRefreshOutcome(ran=True)
    gone = tmp_path / "gone"
    gone.mkdir()
    LocalMemoryProjectBindingStore(data).enable(gone)
    gone.rmdir()
    assert run_source_refresh(data, gone, git_observer=_NO_GIT) == SourceRefreshOutcome(ran=True)
    assert not (data / "mnemo.sqlite3").exists()


def test_the_entry_runs_the_worker_for_exact_arguments_in_either_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review focus 4: spaces, quotes and non-ASCII survive as one argument each."""
    data = tmp_path / "Application Support" / "it's mnemo"
    project = tmp_path / 'repo Ω "quoted"'
    ran: list[tuple[Path, Path]] = []

    def record(data_directory: Path, project_root: Path) -> SourceRefreshOutcome:
        ran.append((data_directory, project_root))
        return SourceRefreshOutcome(ran=True)

    monkeypatch.setattr(source_refresh, "run_source_refresh", record)

    assert source_refresh_entry.main(["--data-dir", str(data), "--project-root", str(project)]) == 0
    assert source_refresh_entry.main(["--project-root", str(project), "--data-dir", str(data)]) == 0
    assert ran == [(data, project), (data, project)]


def test_the_entry_is_silent_and_exits_zero_on_bad_arguments_and_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran: list[Path] = []

    def broken(data_directory: Path, project_root: Path) -> SourceRefreshOutcome:
        ran.append(data_directory)
        raise RuntimeError("synthetic worker failure")

    monkeypatch.setattr(source_refresh, "run_source_refresh", broken)

    assert (
        source_refresh_entry.main(["--data-dir", str(tmp_path), "--project-root", str(tmp_path)])
        == 0
    )
    assert (
        source_refresh_entry.main(["--data-dir", "relative", "--project-root", str(tmp_path)]) == 0
    )
    assert (
        source_refresh_entry.main(["--data-dir", str(tmp_path), "--data-dir", str(tmp_path)]) == 0
    )
    assert source_refresh_entry.main(["--unknown", "x"]) == 0
    assert source_refresh_entry.main([]) == 0
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "")
    assert ran == [tmp_path]


def test_the_entry_runs_the_real_worker_end_to_end(tmp_path: Path) -> None:
    project, data, binding = _project(tmp_path)

    assert source_refresh_entry.main(["--data-dir", str(data), "--project-root", str(project)]) == 0

    active = SQLiteSourceStructureRepository(data / "mnemo.sqlite3").get_active_snapshot(
        binding.scope
    )
    assert active is not None


def test_the_entry_loads_neither_typer_nor_the_cli_main_module() -> None:
    """The one real subprocess in this feature's tests: the worker's import cost stays light."""
    code = (
        "import mnemo_memory.apps.cli.source_refresh_entry, "
        "mnemo_memory.connectors.automatic_memory.source_refresh, sys; "
        "print('typer' in sys.modules, 'mnemo_memory.apps.cli.main' in sys.modules)"
    )
    done = subprocess.run(
        [sys.executable, "-P", "-c", code], capture_output=True, text=True, check=True
    )
    assert done.stdout.strip() == "False False"
