"""Skip the full source-structure parse when the working tree is unchanged.

A stat-only fingerprint plus a small sidecar cache file lets ``refresh_registered_project_source``
avoid a full parse/store round trip when nothing has changed since the last capture. This is an
optimization only: it must never change what an unchanged run returns, and it must never turn a
change into a stale result.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from mnemo_memory.connectors.automatic_memory.source_observation import (
    refresh_registered_project_source,
    source_snapshot_is_fresh,
)
from mnemo_memory.packages.application.automatic_memory import (
    LocalMemoryProjectBindingStore,
    MemoryProjectBinding,
)
from mnemo_memory.packages.domain import CodeSnapshotId
from mnemo_memory.packages.project_index import SourceStructureParser, SourceStructureParseRequest
from mnemo_memory.packages.storage import SQLiteSourceStructureRepository
from mnemo_memory.packages.storage.contracts import SourceStructureRepository


@pytest.fixture
def binding_and_repo(
    tmp_path: Path,
) -> Iterator[tuple[MemoryProjectBinding, SourceStructureRepository]]:
    project = tmp_path / "repo"
    project.mkdir()
    (project / "service.py").write_text("def reconcile():\n    return True\n", encoding="utf-8")
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    repository = SQLiteSourceStructureRepository(data / "mnemo.sqlite3", base_directory=data)
    repository.migrate()
    yield binding, repository


def _install_counting_parse(monkeypatch: pytest.MonkeyPatch, calls: dict[str, int]) -> None:
    real_parse = SourceStructureParser.parse

    def counting_parse(
        self: SourceStructureParser,
        request: SourceStructureParseRequest,
    ) -> object:
        calls["n"] += 1
        return real_parse(self, request)

    monkeypatch.setattr(SourceStructureParser, "parse", counting_parse)


def test_second_refresh_skips_parse_when_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    binding_and_repo: tuple[MemoryProjectBinding, SourceStructureRepository],
) -> None:
    binding, repo = binding_and_repo
    cache_dir = tmp_path / "scan-cache"

    calls = {"n": 0}
    _install_counting_parse(monkeypatch, calls)

    first = refresh_registered_project_source(binding, repo, cache_dir=cache_dir)
    second = refresh_registered_project_source(binding, repo, cache_dir=cache_dir)

    assert first is not None and second is not None
    assert second.snapshot_id == first.snapshot_id
    assert calls["n"] == 1  # second run skipped the parse


def test_refresh_reparses_after_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    binding_and_repo: tuple[MemoryProjectBinding, SourceStructureRepository],
) -> None:
    binding, repo = binding_and_repo
    cache_dir = tmp_path / "scan-cache"
    refresh_registered_project_source(binding, repo, cache_dir=cache_dir)
    (binding.project_root / "added.py").write_text("z = 3\n")

    calls = {"n": 0}
    _install_counting_parse(monkeypatch, calls)

    second = refresh_registered_project_source(binding, repo, cache_dir=cache_dir)

    assert second is not None
    assert calls["n"] == 1  # change forced a re-parse


def test_no_cache_dir_still_refreshes_every_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    binding_and_repo: tuple[MemoryProjectBinding, SourceStructureRepository],
) -> None:
    """Backward compatibility: omitting cache_dir keeps the old always-parse behavior."""
    binding, repo = binding_and_repo

    calls = {"n": 0}
    _install_counting_parse(monkeypatch, calls)

    refresh_registered_project_source(binding, repo)
    refresh_registered_project_source(binding, repo)

    assert calls["n"] == 2


def test_freshness_matches_only_the_cached_fingerprint_and_the_active_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    binding_and_repo: tuple[MemoryProjectBinding, SourceStructureRepository],
) -> None:
    binding, repo = binding_and_repo
    cache_dir = tmp_path / "scan-cache"
    assert not source_snapshot_is_fresh(binding, None, cache_dir=cache_dir)
    stored = refresh_registered_project_source(binding, repo, cache_dir=cache_dir)
    assert stored is not None
    calls = {"n": 0}
    _install_counting_parse(monkeypatch, calls)

    assert source_snapshot_is_fresh(binding, stored.snapshot_id, cache_dir=cache_dir)
    assert not source_snapshot_is_fresh(binding, CodeSnapshotId.new(), cache_dir=cache_dir)
    (binding.project_root / "added.py").write_text("z = 3\n", encoding="utf-8")
    assert not source_snapshot_is_fresh(binding, stored.snapshot_id, cache_dir=cache_dir)
    assert calls["n"] == 0  # the check never parses


def test_freshness_treats_an_unreadable_tree_or_missing_cache_as_stale(
    tmp_path: Path,
    binding_and_repo: tuple[MemoryProjectBinding, SourceStructureRepository],
) -> None:
    binding, repo = binding_and_repo
    cache_dir = tmp_path / "scan-cache"
    stored = refresh_registered_project_source(binding, repo, cache_dir=cache_dir)
    assert stored is not None

    def unreadable(_: Path) -> str:
        raise OSError("tree is unreadable")

    assert not source_snapshot_is_fresh(
        binding, stored.snapshot_id, cache_dir=cache_dir, fingerprint=unreadable
    )
    assert not source_snapshot_is_fresh(
        binding, stored.snapshot_id, cache_dir=tmp_path / "other-cache"
    )
