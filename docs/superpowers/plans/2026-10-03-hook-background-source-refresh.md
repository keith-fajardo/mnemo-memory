# Hook Background Source Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** No lifecycle hook ever parses or stores source structure. A stale code map is refreshed by a detached background worker, old snapshots are pruned in small steps, and a one-off maintenance command shrinks an existing database.

**Architecture:** The hook keeps only the stat-only freshness check (fingerprint vs. scan cache vs. active snapshot id). When the map is stale it returns at once with `refresh_pending=True` and, after its output is built, starts `python -P -m mnemo_memory.apps.cli.source_refresh_entry` detached through an injected starter. The worker (`connectors/automatic_memory/source_refresh.py`) holds a non-blocking `flock`, parses, stores and activates (re-checking up to 3 parses), records Git state through a helper shared with the hook, and prunes at most 4 snapshots through a new `SQLiteSourceStructureRepository.prune_source_snapshots`. `mnemo-memory maintenance prune-source` prunes without a limit and can `VACUUM`.

**Tech Stack:** Python 3.12, standard library only (`fcntl`, `sqlite3`, `subprocess`, `shutil`), Typer CLI, pytest, mypy strict, ruff.

**Spec:** `docs/superpowers/specs/2026-10-03-hook-background-source-refresh-design.md` (approved 2026-10-03). Executors read the spec and this plan together.

## Global Constraints

- **Toolchain:** Python 3.12, mypy strict over `src`, `tests` and `scripts` (`warn_unreachable = true`), ruff line length 100 (rule sets B, E, F, I, RUF, SIM, UP), standard library only. No new dependencies.
- **Layer rules** (`scripts/check_architecture.py`): `packages/application` may import only `packages/domain` and `packages/storage` (so never `model_gateway` or `telemetry`); `packages/storage` may import only `packages/domain` and `packages/policy`; an app may not import another app; a connector may not import any app.
- **Tests never open the user's real data directory** (`~/Library/Application Support/Mnemo`). Every test uses a `tmp_path` data directory and fake spawners. No test starts a real detached process, except the one subprocess import-cost test of the entry module.
- **Hooks fail open.** No new failure may block a client session or print a path or source text.
- **Byte-identical output when fresh:** on a fresh map the hook's output is unchanged; the existing hook contract and lifecycle tests keep passing (tests that relied on the hook parsing a *changed* tree are updated in Task 3, as listed there).
- **Fixed notice text** (spec §2), exactly: `Code map refresh running in background; structural change details may lag one prompt.`
- **Spawn argv** (spec §2), exactly: `[sys.executable, "-P", "-m", "mnemo_memory.apps.cli.source_refresh_entry", "--data-dir", <dir>, "--project-root", <root>]`, `start_new_session=True`, DEVNULL stdin/stdout/stderr, `close_fds=True`, no shell, no `env`.
- **Lock file:** `<data_dir>/.source-refresh.lock`. **Retention:** keep the active snapshot and every snapshot among the newest **17** activations; worker deletes at most **4** snapshots per run; at most **3** parses per worker run.
- **Commits:** `git add` new files, then `git commit -m "<subject>" -m "$CO_AUTHOR" -- <paths>`, where `CO_AUTHOR` is set to your own session's `Co-Authored-By:` line. Never stage `reports/` or `research_notes/`.

## Review Focus

1. **A working tree that returns to a digest whose snapshot was pruned to header-only** (for example `git checkout` back to a commit a checkpoint observed): the `UNIQUE (owner, workspace, project, source_digest)` constraint makes the store reuse that header. Expected: the map is restored from the new parse; an empty map is never activated. Test: Task 1, `test_a_tree_back_at_a_header_only_digest_restores_its_map`.
2. **A pinned read of a header-only snapshot** (explicit `before_snapshot_id`/`after_snapshot_id` in `source_changes`, `source diff`, or a pinned `snapshot_id`): expected `SourceSnapshotNotFound`, never a false "everything was removed" diff. Test: Task 1, `test_a_pinned_read_of_a_header_only_snapshot_is_not_found`.
3. **An MCP `save_checkpoint` re-activates an old digest while the worker prunes** (the MCP path parses synchronously and is out of scope, so it can run concurrently): expected the re-check under the write lock keeps that snapshot. Test: Task 1, `test_a_snapshot_reactivated_after_listing_is_never_pruned`.
4. **Data or project paths with spaces, quotes or non-ASCII** (`Application Support`, `it's`, `Ω`): expected one argv element each, no shell, and the entry reads them back exactly. Tests: Task 2, `test_the_entry_runs_the_worker_for_exact_arguments_in_either_order`; Task 3, `test_the_start_is_detached_and_passes_each_path_as_one_argument`.
5. **A worker started for a project that was disabled or deleted before it ran:** expected a silent no-op (no database created, nothing parsed or pruned). Test: Task 2, `test_a_worker_for_a_disabled_or_deleted_project_does_nothing`.

## Decisions where the spec is silent or ambiguous

- **D1. No caller asks for the latest transition.** Every hook call site keeps `include_latest_transition=False`, exactly as today (SessionStart says so explicitly; the other three use the default). Because the hook never stores a snapshot any more, hook output no longer carries the structural change summary, static impact cues or dbt cues. The `include_latest_transition=True` path is kept and tested directly. Agents get the details through `source_changes`, which the notices already name.
- **D2. A stale map's digest is withheld.** With `refresh_pending=True` the result's `digest` is `None`, so the SessionStart text never says "include this exact current_source_digest to prove the refreshed source snapshot is current" for a snapshot the hook cannot prove current. Git observation is also skipped, which is what makes the PostToolUse baseline fail closed.
- **D3. The pending signal reaches `handle` through a per-call list**, not a new `_refresh_source_structure` parameter. This keeps the method's signature, which an existing test monkeypatches.
- **D4. Header-only snapshots are guarded.** The store re-hydrates a header-only snapshot when its digest comes back, and every row read of a header-only snapshot raises `SourceSnapshotNotFound`. Without this, pruning would make two existing paths wrong (Review Focus 1 and 2).
- **D5. The test suite turns real spawning off with `MNEMO_DISABLE_BACKGROUND_SOURCE_REFRESH=1`**, set by an autouse fixture in a new `tests/conftest.py`. Several existing tests run the hook command in a subprocess, where monkeypatching cannot reach. The starter reads the variable at call time, so it also works as a user opt-out (documented in Task 5).
- **D6. `--compact` checks free space before pruning**, so a refusal changes nothing. Neither `--project-root` nor `--all-projects` means the project at the current directory. Both together is an error.

## File Structure

| File | Responsibility |
|---|---|
| `src/mnemo_memory/packages/storage/sqlite.py` (modify) | `prune_source_snapshots`, `plan_source_snapshot_prune`, `list_source_scopes`, `vacuum`, header-only guards, and re-hydration in `store_source_and_activate`. |
| `src/mnemo_memory/packages/storage/__init__.py` (modify) | Export `SourceSnapshotPrunePlan`. |
| `src/mnemo_memory/connectors/automatic_memory/source_observation.py` (modify) | Pure `source_snapshot_is_fresh`. |
| `src/mnemo_memory/connectors/automatic_memory/git_observation.py` (modify) | Shared `observe_and_store_git`. |
| `src/mnemo_memory/connectors/automatic_memory/source_refresh.py` (create) | `SourceRefreshLock`, `run_source_refresh`, `SourceRefreshOutcome`. |
| `src/mnemo_memory/apps/cli/source_refresh_entry.py` (create) | Light, silent, exit-0 entry. |
| `src/mnemo_memory/connectors/automatic_memory/hook.py` (modify) | Freshness-only refresh, `refresh_pending`, notice line, spawn after output. |
| `src/mnemo_memory/apps/cli/main.py` (modify) | `_start_source_refresh`, builder injection, `maintenance prune-source`. |
| `tests/conftest.py` (create) | Suite-wide spawn guard. |
| `tests/unit/test_source_snapshot_pruning.py` (create) | Retention and CLI tests. |
| `tests/unit/test_source_refresh_worker.py` (create) | Worker and entry tests. |
| `tests/unit/test_hook_background_source_refresh.py` (create) | Hook and starter tests. |
| `tests/unit/test_source_observation_skip.py`, `tests/unit/test_automatic_memory.py`, `tests/unit/test_git_observation.py` (modify) | Freshness tests and existing tests adapted to the background refresh. |
| `docs/user-guide.md` (modify) | Background refresh and the cleanup command. |

---

### Task 1: Snapshot retention in the SQLite repository

**Files:**
- Modify: `src/mnemo_memory/packages/storage/sqlite.py`. The import at line 12 changes, constants go after `BUSY_TIMEOUT_MS`, `store_source_and_activate` (about lines 4498-4640) changes, a helper is added after it, the new types go before `class SQLiteSourceStructureRepository`, and wrapper methods are added after `list_activation_history`. Module helpers go after `_maybe`.
- Modify: `src/mnemo_memory/packages/storage/__init__.py` (the `.sqlite` import, around line 174, and `__all__`)
- Create: `tests/unit/test_source_snapshot_pruning.py`

**Interfaces:**
- Consumes: the existing `SQLiteSourceStructureRepository`, `SQLiteCheckpointRepository._transaction()` / `._connect()` / `._require_project_scope()`, `SourceSnapshotNotFound`, and `SourceIndexStorageFailure`.
- Produces:
  - `SQLiteSourceStructureRepository.prune_source_snapshots(scope: MemoryScope, *, keep_activations: int = 17, max_snapshots: int | None) -> int`
  - `SQLiteSourceStructureRepository.plan_source_snapshot_prune(scope: MemoryScope, *, keep_activations: int = 17) -> SourceSnapshotPrunePlan`
  - `SQLiteSourceStructureRepository.list_source_scopes() -> tuple[MemoryScope, ...]`
  - `SQLiteSourceStructureRepository.vacuum() -> None`
  - `SourceSnapshotPrunePlan(full_snapshots: int, header_only_snapshots: int, symbols: int, edges: int)`, exported from `mnemo_memory.packages.storage`
  - private `SQLiteSourceStructureRepository._prune_candidates(scope, keep_activations) -> tuple[_SourcePruneCandidate, ...]`, where `_SourcePruneCandidate(snapshot_id: str, keep_header: bool)`. A test monkeypatches it.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_source_snapshot_pruning.py`:

```python
"""Source snapshot retention: keep what memory needs, delete the rest in short steps (spec §4)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mnemo_memory.packages.application.automatic_memory import (
    LocalMemoryProjectBindingStore,
    MemoryProjectBinding,
)
from mnemo_memory.packages.application.bootstrap import build_checkpoint_runtime
from mnemo_memory.packages.application.checkpoints import CreateCheckpoint
from mnemo_memory.packages.application.config import LocalConfig
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
from mnemo_memory.packages.storage.contracts import SourceSnapshotNotFound


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

    assert SQLiteCheckpointRepository(data / "mnemo.sqlite3").connection_settings()[
        "foreign_keys"
    ] == 1
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
        diff = service.diff(binding.scope, history[index + 1].snapshot_id, history[index].snapshot_id)
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_source_snapshot_pruning.py -q`
Expected: collection ERROR, `ImportError: cannot import name 'SourceSnapshotPrunePlan'`.

- [ ] **Step 3: Add the module constants, types and helpers**

In `src/mnemo_memory/packages/storage/sqlite.py`:

Change `from dataclasses import replace` to `from dataclasses import dataclass, replace`.

After `BUSY_TIMEOUT_MS = 5000` add:

```python
DEFAULT_KEPT_SOURCE_ACTIVATIONS = 17
# Whether a snapshot still has its projection rows (a pruned, checkpoint-named one keeps only its
# header). Every file yields at least one module symbol, so symbols or files decide it.
_SOURCE_SNAPSHOT_POPULATED_SQL = (
    "SELECT 1 FROM source_structure_symbols WHERE snapshot_id = ? "
    "UNION ALL SELECT 1 FROM source_structure_files WHERE snapshot_id = ? LIMIT 1"
)
# The active snapshot plus every snapshot among one scope's newest N activations (spec §4).
_KEPT_SOURCE_SNAPSHOTS_SQL = (
    "SELECT snapshot_id FROM source_structure_snapshots WHERE owner_id = ? "
    "AND workspace_id IS ? AND project_id = ? AND is_active = 1 "
    "UNION SELECT snapshot_id FROM (SELECT snapshot_id FROM source_snapshot_activations "
    "WHERE owner_id = ? AND workspace_id IS ? AND project_id = ? "
    "ORDER BY activation_id DESC LIMIT ?)"
)
```

Directly before `class SQLiteSourceStructureRepository:` add:

```python
@dataclass(frozen=True, slots=True)
class SourceSnapshotPrunePlan:
    """Counts only (no ids, paths or names): what one scope's prune would remove."""

    full_snapshots: int
    header_only_snapshots: int
    symbols: int
    edges: int


@dataclass(frozen=True, slots=True)
class _SourcePruneCandidate:
    snapshot_id: str
    keep_header: bool


def _source_scope_values(scope: MemoryScope) -> tuple[str, str | None, str]:
    return (str(scope.owner_id), _maybe(scope.workspace_id), str(scope.project_id))


def _validate_prune_bounds(keep_activations: int, max_snapshots: int | None) -> None:
    if isinstance(keep_activations, bool) or not isinstance(keep_activations, int):
        raise ValueError("kept source activations must be a positive integer")
    if keep_activations < 1:
        raise ValueError("kept source activations must be a positive integer")
    if max_snapshots is not None and (
        isinstance(max_snapshots, bool) or not isinstance(max_snapshots, int) or max_snapshots < 1
    ):
        raise ValueError("source prune limit must be a positive integer or None")
```

- [ ] **Step 4: Re-hydrate a header-only snapshot in the store, and share the projection insert**

In `SQLiteCheckpointRepository.store_source_and_activate`, replace the new-snapshot insert-and-count block. It runs from the first `connection.executemany(` through `raise SourceIndexStorageFailure("source snapshot projection count mismatch")`:

```python
                connection.executemany(
                    "INSERT INTO source_structure_files VALUES (?, ?, ?)",
                    [
                        (
                            str(artifact.snapshot.snapshot_id),
                            item.relative_path,
                            item.content_digest,
                        )
                        for item in artifact.files
                    ],
                )
                connection.executemany(
                    "INSERT INTO source_structure_symbols VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (
                            str(artifact.snapshot.snapshot_id),
                            str(symbol.symbol_id),
                            symbol.relative_path,
                            symbol.qualified_name,
                            symbol.kind.value,
                            symbol.line,
                        )
                        for symbol in artifact.symbols
                    ],
                )
                connection.executemany(
                    "INSERT INTO source_structure_edges VALUES (?, ?, ?, ?, ?)",
                    [
                        (
                            str(artifact.snapshot.snapshot_id),
                            str(edge.source_symbol_id),
                            edge.target,
                            edge.kind.value,
                            str(edge.target_symbol_id)
                            if edge.target_symbol_id is not None
                            else None,
                        )
                        for edge in artifact.edges
                    ],
                )
                counts = connection.execute(
                    "SELECT (SELECT COUNT(*) FROM source_structure_files WHERE snapshot_id = ?) "
                    "AS files, (SELECT COUNT(*) FROM source_structure_symbols "
                    "WHERE snapshot_id = ?) AS symbols, (SELECT COUNT(*) FROM "
                    "source_structure_edges WHERE snapshot_id = ?) AS edges",
                    (
                        str(artifact.snapshot.snapshot_id),
                        str(artifact.snapshot.snapshot_id),
                        str(artifact.snapshot.snapshot_id),
                    ),
                ).fetchone()
                if (
                    counts is None
                    or int(counts["files"]) != len(artifact.files)
                    or int(counts["symbols"]) != len(artifact.symbols)
                    or int(counts["edges"]) != len(artifact.edges)
                ):
                    raise SourceIndexStorageFailure("source snapshot projection count mismatch")
```

with:

```python
                self._insert_source_projection(
                    connection, artifact.snapshot.snapshot_id, artifact
                )
```

In the same method, replace:

```python
                if duplicate is not None:
                    snapshot_id = CodeSnapshotId.from_string(duplicate["snapshot_id"])
                    active = self._active_source_snapshot_row(connection, scope)
```

with:

```python
                if duplicate is not None:
                    snapshot_id = CodeSnapshotId.from_string(duplicate["snapshot_id"])
                    if self._source_snapshot_is_hollow(connection, duplicate):
                        # A prune kept only this header because a checkpoint names it. The tree is
                        # back at the same digest, so restore the projection before activating it;
                        # an empty map must never become active (2026-10-03 plan, review focus 1).
                        self._insert_source_projection(connection, snapshot_id, artifact)
                        connection.execute(
                            "UPDATE source_structure_snapshots SET file_count = ?, "
                            "symbol_count = ?, edge_count = ? WHERE snapshot_id = ?",
                            (
                                artifact.snapshot.file_count,
                                artifact.snapshot.symbol_count,
                                artifact.snapshot.edge_count,
                                str(snapshot_id),
                            ),
                        )
                        duplicate = connection.execute(
                            "SELECT * FROM source_structure_snapshots WHERE snapshot_id = ?",
                            (str(snapshot_id),),
                        ).fetchone()
                    active = self._active_source_snapshot_row(connection, scope)
```

Directly after `store_source_and_activate` (before `def get_active_source_snapshot`), add:

```python
    @staticmethod
    def _insert_source_projection(
        connection: sqlite3.Connection,
        snapshot_id: CodeSnapshotId,
        artifact: CodeStructureArtifact,
    ) -> None:
        """Insert one snapshot's files, symbols and edges, then prove every row landed."""
        key = str(snapshot_id)
        connection.executemany(
            "INSERT INTO source_structure_files VALUES (?, ?, ?)",
            [(key, item.relative_path, item.content_digest) for item in artifact.files],
        )
        connection.executemany(
            "INSERT INTO source_structure_symbols VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    key,
                    str(symbol.symbol_id),
                    symbol.relative_path,
                    symbol.qualified_name,
                    symbol.kind.value,
                    symbol.line,
                )
                for symbol in artifact.symbols
            ],
        )
        connection.executemany(
            "INSERT INTO source_structure_edges VALUES (?, ?, ?, ?, ?)",
            [
                (
                    key,
                    str(edge.source_symbol_id),
                    edge.target,
                    edge.kind.value,
                    str(edge.target_symbol_id) if edge.target_symbol_id is not None else None,
                )
                for edge in artifact.edges
            ],
        )
        counts = connection.execute(
            "SELECT (SELECT COUNT(*) FROM source_structure_files WHERE snapshot_id = ?) "
            "AS files, (SELECT COUNT(*) FROM source_structure_symbols "
            "WHERE snapshot_id = ?) AS symbols, (SELECT COUNT(*) FROM "
            "source_structure_edges WHERE snapshot_id = ?) AS edges",
            (key, key, key),
        ).fetchone()
        if (
            counts is None
            or int(counts["files"]) != len(artifact.files)
            or int(counts["symbols"]) != len(artifact.symbols)
            or int(counts["edges"]) != len(artifact.edges)
        ):
            raise SourceIndexStorageFailure("source snapshot projection count mismatch")

    @staticmethod
    def _source_snapshot_is_hollow(connection: sqlite3.Connection, row: sqlite3.Row) -> bool:
        """A header whose counts promise rows that a prune removed."""
        if int(row["symbol_count"]) == 0 and int(row["file_count"]) == 0:
            return False
        key = str(row["snapshot_id"])
        return connection.execute(_SOURCE_SNAPSHOT_POPULATED_SQL, (key, key)).fetchone() is None
```

- [ ] **Step 5: Guard every row read of one snapshot**

In `SQLiteSourceStructureRepository`, replace every line that is exactly `        self._backend.get_source_snapshot(scope, snapshot_id)` with `        self._populated_snapshot(scope, snapshot_id)`. Use replace-all. There are exactly 9 such lines, in `iter_symbols`, `iter_files`, `get_file`, `iter_edges`, `find_symbols`, `module_symbols_for_paths`, `symbols_by_ids`, `edges_from_symbols` and `edges_to_symbols`. `get_snapshot` keeps `return self._backend.get_source_snapshot(...)` unchanged, because a checkpoint observation still resolves a header.

After `get_snapshot`, add:

```python
    def _populated_snapshot(self, scope: MemoryScope, snapshot_id: CodeSnapshotId) -> CodeSnapshot:
        """The snapshot header, or ``SourceSnapshotNotFound`` when a prune emptied it (spec §4)."""
        snapshot = self._backend.get_source_snapshot(scope, snapshot_id)
        if snapshot.symbol_count == 0 and snapshot.file_count == 0:
            return snapshot
        key = str(snapshot_id)
        try:
            with self._backend._connect() as connection:
                populated = connection.execute(_SOURCE_SNAPSHOT_POPULATED_SQL, (key, key)).fetchone()
        except sqlite3.Error as error:
            raise SourceIndexStorageFailure("source index storage operation failed") from error
        if populated is None:
            raise SourceSnapshotNotFound("source snapshot was not found")
        return snapshot
```

- [ ] **Step 6: Add the prune, plan, scope-listing and vacuum methods**

After `list_activation_history` in `SQLiteSourceStructureRepository`, add:

```python
    def prune_source_snapshots(
        self,
        scope: MemoryScope,
        *,
        keep_activations: int = DEFAULT_KEPT_SOURCE_ACTIVATIONS,
        max_snapshots: int | None,
    ) -> int:
        """Delete snapshots this project no longer needs; return how many were pruned.

        Kept: the active snapshot and every snapshot among the newest ``keep_activations``
        activations. A snapshot a checkpoint observation names keeps its header and activation
        rows but loses its files, symbols and edges. Any other snapshot is deleted with its
        activation rows. Each snapshot is one short transaction, children before parents (every
        foreign key is ``ON DELETE RESTRICT``), re-checked under the write lock so a concurrent
        activation or observation is never broken. ``max_snapshots=None`` means no limit (the
        maintenance command only).
        """
        _validate_prune_bounds(keep_activations, max_snapshots)
        pruned = 0
        for candidate in self._prune_candidates(scope, keep_activations):
            if max_snapshots is not None and pruned >= max_snapshots:
                break
            if self._prune_one(scope, candidate.snapshot_id, keep_activations):
                pruned += 1
        return pruned

    def plan_source_snapshot_prune(
        self, scope: MemoryScope, *, keep_activations: int = DEFAULT_KEPT_SOURCE_ACTIVATIONS
    ) -> SourceSnapshotPrunePlan:
        """Count what ``prune_source_snapshots`` would remove; change nothing."""
        _validate_prune_bounds(keep_activations, None)
        candidates = self._prune_candidates(scope, keep_activations)
        symbols = 0
        edges = 0
        try:
            with self._backend._connect() as connection:
                for candidate in candidates:
                    symbols += int(
                        connection.execute(
                            "SELECT COUNT(*) FROM source_structure_symbols WHERE snapshot_id = ?",
                            (candidate.snapshot_id,),
                        ).fetchone()[0]
                    )
                    edges += int(
                        connection.execute(
                            "SELECT COUNT(*) FROM source_structure_edges WHERE snapshot_id = ?",
                            (candidate.snapshot_id,),
                        ).fetchone()[0]
                    )
        except sqlite3.Error as error:
            raise SourceIndexStorageFailure("source index storage operation failed") from error
        header_only = sum(1 for candidate in candidates if candidate.keep_header)
        return SourceSnapshotPrunePlan(len(candidates) - header_only, header_only, symbols, edges)

    def list_source_scopes(self) -> tuple[MemoryScope, ...]:
        """Every project scope that has source snapshots, for an all-projects cleanup."""
        try:
            with self._backend._connect() as connection:
                rows = connection.execute(
                    "SELECT DISTINCT owner_id, visibility, workspace_id, project_id "
                    "FROM source_structure_snapshots ORDER BY owner_id, workspace_id, project_id"
                ).fetchall()
        except sqlite3.Error as error:
            raise SourceIndexStorageFailure("source index storage operation failed") from error
        return tuple(
            MemoryScope(
                OwnerId.from_string(row["owner_id"]),
                ScopeLevel.PROJECT,
                Visibility(row["visibility"]),
                None
                if row["workspace_id"] is None
                else WorkspaceId.from_string(row["workspace_id"]),
                ProjectId.from_string(row["project_id"]),
            )
            for row in rows
        )

    def vacuum(self) -> None:
        """Rebuild the file so pages freed by a prune return to the disk (maintenance only)."""
        try:
            connection = self._backend._connect()
            try:
                connection.execute("VACUUM")
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                connection.close()
        except sqlite3.Error as error:
            raise SourceIndexStorageFailure("source index compaction failed") from error

    def _prune_candidates(
        self, scope: MemoryScope, keep_activations: int
    ) -> tuple[_SourcePruneCandidate, ...]:
        """Oldest first: unkept snapshots that still have rows to delete."""
        self._backend._require_project_scope(scope)
        values = _source_scope_values(scope)
        try:
            with self._backend._connect() as connection:
                rows = connection.execute(
                    "SELECT snapshot.snapshot_id, EXISTS (SELECT 1 FROM "
                    "checkpoint_source_observations AS observation "
                    "WHERE observation.source_snapshot_id = snapshot.snapshot_id) AS referenced, "
                    "(EXISTS (SELECT 1 FROM source_structure_symbols AS symbol "
                    "WHERE symbol.snapshot_id = snapshot.snapshot_id) OR EXISTS (SELECT 1 FROM "
                    "source_structure_files AS file "
                    "WHERE file.snapshot_id = snapshot.snapshot_id)) "
                    "AS populated, COALESCE((SELECT MAX(activation.activation_id) FROM "
                    "source_snapshot_activations AS activation "
                    "WHERE activation.snapshot_id = snapshot.snapshot_id), 0) AS last_activation "
                    "FROM source_structure_snapshots AS snapshot WHERE snapshot.owner_id = ? "
                    "AND snapshot.workspace_id IS ? AND snapshot.project_id = ? "
                    "AND snapshot.snapshot_id NOT IN (" + _KEPT_SOURCE_SNAPSHOTS_SQL + ") "
                    "ORDER BY last_activation ASC, snapshot.snapshot_id ASC",
                    (*values, *values, *values, keep_activations),
                ).fetchall()
        except sqlite3.Error as error:
            raise SourceIndexStorageFailure("source index storage operation failed") from error
        return tuple(
            _SourcePruneCandidate(str(row["snapshot_id"]), bool(row["referenced"]))
            for row in rows
            if row["populated"] or not row["referenced"]
        )

    def _prune_one(self, scope: MemoryScope, snapshot_id: str, keep_activations: int) -> bool:
        """Delete one snapshot in its own short transaction; re-check it under the write lock."""
        values = _source_scope_values(scope)
        try:
            with self._backend._transaction() as connection:
                kept = {
                    str(row["snapshot_id"])
                    for row in connection.execute(
                        _KEPT_SOURCE_SNAPSHOTS_SQL, (*values, *values, keep_activations)
                    )
                }
                present = connection.execute(
                    "SELECT 1 FROM source_structure_snapshots WHERE snapshot_id = ? "
                    "AND owner_id = ? AND workspace_id IS ? AND project_id = ? AND is_active = 0",
                    (snapshot_id, *values),
                ).fetchone()
                if present is None or snapshot_id in kept:
                    return False
                referenced = (
                    connection.execute(
                        "SELECT 1 FROM checkpoint_source_observations "
                        "WHERE source_snapshot_id = ? LIMIT 1",
                        (snapshot_id,),
                    ).fetchone()
                    is not None
                )
                populated = (
                    connection.execute(
                        _SOURCE_SNAPSHOT_POPULATED_SQL, (snapshot_id, snapshot_id)
                    ).fetchone()
                    is not None
                )
                if referenced and not populated:
                    return False  # already header-only
                # Children before parents: every foreign key here is ON DELETE RESTRICT, and the
                # edges also reference symbols through a composite key.
                connection.execute(
                    "DELETE FROM source_structure_edges WHERE snapshot_id = ?", (snapshot_id,)
                )
                connection.execute(
                    "DELETE FROM source_structure_symbols WHERE snapshot_id = ?", (snapshot_id,)
                )
                connection.execute(
                    "DELETE FROM source_structure_files WHERE snapshot_id = ?", (snapshot_id,)
                )
                if not referenced:
                    connection.execute(
                        "DELETE FROM source_snapshot_activations WHERE snapshot_id = ?",
                        (snapshot_id,),
                    )
                    connection.execute(
                        "DELETE FROM source_structure_snapshots WHERE snapshot_id = ?",
                        (snapshot_id,),
                    )
                return True
        except sqlite3.Error as error:
            raise SourceIndexStorageFailure("source index storage operation failed") from error
```

- [ ] **Step 7: Export the plan type**

In `src/mnemo_memory/packages/storage/__init__.py`, change the `.sqlite` import to:

```python
from .sqlite import (
    SourceSnapshotPrunePlan,
    SQLiteCheckpointRepository,
    SQLiteKnowledgeDocumentRepository,
    SQLiteMigrationError,
    SQLiteSchemaTooNewError,
    SQLiteSourceStructureRepository,
)
```

In `__all__`, add `"SourceSnapshotPrunePlan",` directly before `"SourceStructureRepository",`.

- [ ] **Step 8: Run the new tests, then lint and type-check the touched files**

Run: `uv run ruff check --fix src/mnemo_memory/packages/storage tests/unit/test_source_snapshot_pruning.py && uv run ruff format src/mnemo_memory/packages/storage tests/unit/test_source_snapshot_pruning.py && uv run pytest tests/unit/test_source_snapshot_pruning.py -q`
Expected: 11 passed.

Run: `uv run mypy src/mnemo_memory/packages/storage tests/unit/test_source_snapshot_pruning.py`
Expected: `Success: no issues found`.

- [ ] **Step 9: Run the existing storage and source suites for regressions**

Run: `uv run pytest tests/unit/test_source_structure_storage.py tests/unit/test_source_structure_multilang_storage.py tests/unit/test_source_observation_skip.py tests/unit/test_mcp_server.py -q`
Expected: all pass.

- [ ] **Step 10: Commit**

```bash
git add tests/unit/test_source_snapshot_pruning.py
git commit -m "feat(storage): prune old source snapshots in bounded, FK-safe steps" -m "$CO_AUTHOR" -- src/mnemo_memory/packages/storage/sqlite.py src/mnemo_memory/packages/storage/__init__.py tests/unit/test_source_snapshot_pruning.py
```

---

### Task 2: Freshness check, background worker and light entry

**Files:**
- Modify: `src/mnemo_memory/connectors/automatic_memory/source_observation.py` (the imports and `refresh_registered_project_source` at lines 14-85)
- Modify: `src/mnemo_memory/connectors/automatic_memory/git_observation.py` (append after `GitSourceObserver` / `_run_git`)
- Create: `src/mnemo_memory/connectors/automatic_memory/source_refresh.py`
- Create: `src/mnemo_memory/apps/cli/source_refresh_entry.py`
- Modify: `tests/unit/test_source_observation_skip.py`
- Create: `tests/unit/test_source_refresh_worker.py`

**Interfaces:**
- Consumes: `SQLiteSourceStructureRepository.prune_source_snapshots(scope, *, keep_activations, max_snapshots)` from Task 1.
- Produces:
  - `source_snapshot_is_fresh(binding: MemoryProjectBinding, active_snapshot_id: CodeSnapshotId | None, *, cache_dir: Path, fingerprint: Callable[[Path], str] = working_tree_fingerprint) -> bool` in `source_observation.py`
  - `observe_and_store_git(data_directory: Path, project_root: Path, scope: MemoryScope, source_digest: str, observer: GitSourceObserver | None = None) -> GitSourceObservation | None` in `git_observation.py`
  - In `source_refresh.py`:
    - `SOURCE_REFRESH_LOCK_FILE = ".source-refresh.lock"`
    - `SourceRefreshLock(data_directory: Path)`, with `.hold() -> ContextManager[bool]` and `.running() -> bool`
    - `SourceRefreshOutcome(ran: bool, parses: int = 0, pruned: int = 0)`
    - `run_source_refresh(data_directory: Path, project_root: Path, *, git_observer: GitSourceObserver | None = None, fingerprint: Callable[[Path], str] = working_tree_fingerprint) -> SourceRefreshOutcome`
    - the constants `MAXIMUM_PARSES_PER_RUN = 3`, `PRUNE_SNAPSHOTS_PER_RUN = 4` and `KEPT_SOURCE_ACTIVATIONS = 17`
  - `mnemo_memory.apps.cli.source_refresh_entry.main(argv: Sequence[str] | None = None) -> int`

- [ ] **Step 1: Write the failing freshness tests**

In `tests/unit/test_source_observation_skip.py`, change the source-observation import to:

```python
from mnemo_memory.connectors.automatic_memory.source_observation import (
    refresh_registered_project_source,
    source_snapshot_is_fresh,
)
```

add `from mnemo_memory.packages.domain import CodeSnapshotId` to the imports, and append:

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/unit/test_source_observation_skip.py -q`
Expected: collection ERROR, `ImportError: cannot import name 'source_snapshot_is_fresh'`.

- [ ] **Step 3: Implement the pure freshness check**

In `source_observation.py`, change `from mnemo_memory.packages.domain import CheckpointSourceObservation, CodeSnapshot` to:

```python
from mnemo_memory.packages.domain import (
    CheckpointSourceObservation,
    CodeSnapshot,
    CodeSnapshotId,
)
```

After `_write_cache`, add:

```python
def _cache_matches(cached: tuple[str, str] | None, fingerprint: str, snapshot_id: str) -> bool:
    return cached is not None and cached == (fingerprint, snapshot_id)


def source_snapshot_is_fresh(
    binding: MemoryProjectBinding,
    active_snapshot_id: CodeSnapshotId | None,
    *,
    cache_dir: Path,
    fingerprint: Callable[[Path], str] = working_tree_fingerprint,
) -> bool:
    """Whether the active snapshot still matches the working tree, by file metadata only.

    True only when the scan-cache entry exists, its fingerprint equals a fresh stat-only
    fingerprint of the tree, and it names the active snapshot. It never parses. A missing cache,
    no active snapshot, or an unreadable tree counts as stale (spec 2026-10-03 §2).
    """
    if active_snapshot_id is None:
        return False
    cached = _read_cache(_cache_path(cache_dir, binding))
    if cached is None:
        return False
    try:
        current = fingerprint(binding.project_root)
    except OSError:
        return False
    return _cache_matches(cached, current, str(active_snapshot_id))
```

In `refresh_registered_project_source`, replace:

```python
            if (
                active is not None
                and cached is not None
                and cached[0] == current_fp
                and cached[1] == str(active.snapshot_id)
            ):
                return active  # nothing changed — skip parse + store
```

with:

```python
            if (
                active is not None
                and current_fp is not None
                and _cache_matches(cached, current_fp, str(active.snapshot_id))
            ):
                return active  # nothing changed — skip parse + store
```

- [ ] **Step 4: Run the freshness tests**

Run: `uv run pytest tests/unit/test_source_observation_skip.py -q`
Expected: 5 passed.

- [ ] **Step 5: Write the failing worker and entry tests**

Create `tests/unit/test_source_refresh_worker.py`:

```python
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
        data, project, git_observer=GitSourceObserver(lambda arguments, root: answers.get(arguments))
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
    assert run_source_refresh(data, project, git_observer=_NO_GIT) == SourceRefreshOutcome(
        ran=True
    )
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
    project = tmp_path / "repo Ω \"quoted\""
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

    assert source_refresh_entry.main(["--data-dir", str(tmp_path), "--project-root", str(tmp_path)]) == 0
    assert source_refresh_entry.main(["--data-dir", "relative", "--project-root", str(tmp_path)]) == 0
    assert source_refresh_entry.main(["--data-dir", str(tmp_path), "--data-dir", str(tmp_path)]) == 0
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
```

- [ ] **Step 6: Run them to verify they fail**

Run: `uv run pytest tests/unit/test_source_refresh_worker.py -q`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'mnemo_memory.apps.cli.source_refresh_entry'`.

- [ ] **Step 7: Add the shared Git helper**

Append to `src/mnemo_memory/connectors/automatic_memory/git_observation.py`, after `_run_git` and before `class GitObservationStore` (it uses the store, and that is fine at call time):

```python
def observe_and_store_git(
    data_directory: Path,
    project_root: Path,
    scope: MemoryScope,
    source_digest: str,
    observer: GitSourceObserver | None = None,
) -> GitSourceObservation | None:
    """Observe Git state for one stored source digest and cache it.

    The hook and the background refresh worker share this, so both record the same evidence.
    """
    observation = (observer or GitSourceObserver()).observe(project_root, source_digest)
    if observation is not None:
        GitObservationStore(data_directory).put(scope, observation)
    return observation
```

- [ ] **Step 8: Create the worker**

Create `src/mnemo_memory/connectors/automatic_memory/source_refresh.py`:

```python
"""The background code-map refresh worker and its single-instance lock (spec 2026-10-03 §3).

Lifecycle hooks never parse source (§2). When the stat-only fingerprint says the saved map is
stale, the hook starts ``mnemo_memory.apps.cli.source_refresh_entry`` detached, and that entry
calls ``run_source_refresh`` here. One worker runs per data directory: it holds a non-blocking
``flock`` on ``<data_dir>/.source-refresh.lock``, and any other worker leaves at once. The OS
releases the lock when its holder exits, however it exits. Nothing here prints or stores source
text.
"""

from __future__ import annotations

import fcntl
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from mnemo_memory.connectors.automatic_memory.git_observation import (
    GitSourceObserver,
    observe_and_store_git,
)
from mnemo_memory.connectors.automatic_memory.scan_fingerprint import working_tree_fingerprint
from mnemo_memory.connectors.automatic_memory.source_observation import (
    refresh_registered_project_source,
    source_snapshot_is_fresh,
)
from mnemo_memory.packages.application.automatic_memory import (
    AutomaticMemoryBindingError,
    LocalMemoryProjectBindingStore,
    MemoryProjectBinding,
)
from mnemo_memory.packages.storage import SQLiteSourceStructureRepository

SOURCE_REFRESH_LOCK_FILE = ".source-refresh.lock"
MAXIMUM_PARSES_PER_RUN = 3
PRUNE_SNAPSHOTS_PER_RUN = 4
# ``source_changes`` reads at most 16 transitions, which is 17 activations (spec §4).
KEPT_SOURCE_ACTIVATIONS = 17


class SourceRefreshLock:
    """The single-worker lock for one data directory."""

    def __init__(self, data_directory: Path) -> None:
        self._directory = data_directory.expanduser().resolve()
        self.path = self._directory / SOURCE_REFRESH_LOCK_FILE

    @contextmanager
    def hold(self) -> Iterator[bool]:
        """Hold the lock for one run; yield ``False`` at once when another holder has it."""
        self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(self.path, _flags(create=True), 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                with suppress(OSError):
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def running(self) -> bool:
        """Whether a holder has the lock now: a status probe that never creates the file."""
        try:
            descriptor = os.open(self.path, _flags(create=False))
        except OSError:
            return False
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        except OSError:
            return False
        else:
            with suppress(OSError):
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            return False
        finally:
            os.close(descriptor)


@dataclass(frozen=True, slots=True)
class SourceRefreshOutcome:
    """What one run did. ``ran`` is ``False`` when another worker held the lock."""

    ran: bool
    parses: int = 0
    pruned: int = 0


def run_source_refresh(
    data_directory: Path,
    project_root: Path,
    *,
    git_observer: GitSourceObserver | None = None,
    fingerprint: Callable[[Path], str] = working_tree_fingerprint,
) -> SourceRefreshOutcome:
    """Refresh one enabled project's code map. Failures may escape; the entry swallows them.

    In order: take the lock or leave; resolve the binding exactly as the hook does; while the
    tree is stale, parse, store and activate, then write the scan cache (at most three parses,
    so edits made during a parse are picked up); record Git state for each new digest; prune
    one bounded step.
    """
    with SourceRefreshLock(data_directory).hold() as held:
        if not held:
            return SourceRefreshOutcome(ran=False)
        binding = _binding(data_directory, project_root)
        if binding is None:
            return SourceRefreshOutcome(ran=True)
        repository = SQLiteSourceStructureRepository(data_directory / "mnemo.sqlite3")
        repository.migrate()
        cache_dir = data_directory / "scan-cache"
        parses = 0
        while parses < MAXIMUM_PARSES_PER_RUN:
            active = repository.get_active_snapshot(binding.scope)
            if source_snapshot_is_fresh(
                binding,
                None if active is None else active.snapshot_id,
                cache_dir=cache_dir,
                fingerprint=fingerprint,
            ):
                break
            parses += 1
            snapshot = refresh_registered_project_source(
                binding, repository, cache_dir=cache_dir, fingerprint=fingerprint
            )
            if snapshot is None:
                break
            observe_and_store_git(
                data_directory,
                binding.project_root,
                binding.scope,
                snapshot.source_digest,
                git_observer,
            )
        pruned = repository.prune_source_snapshots(
            binding.scope,
            keep_activations=KEPT_SOURCE_ACTIVATIONS,
            max_snapshots=PRUNE_SNAPSHOTS_PER_RUN,
        )
        return SourceRefreshOutcome(ran=True, parses=parses, pruned=pruned)


def _binding(data_directory: Path, project_root: Path) -> MemoryProjectBinding | None:
    """Resolve the enabled project as the hook does; a disabled or vanished one is no work."""
    try:
        return LocalMemoryProjectBindingStore(data_directory).get(project_root)
    except (AutomaticMemoryBindingError, OSError):
        return None


def _flags(*, create: bool) -> int:
    flags = os.O_RDWR | (os.O_CREAT if create else 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return flags
```

- [ ] **Step 9: Create the light entry module**

Create `src/mnemo_memory/apps/cli/source_refresh_entry.py`:

```python
"""Light entry point for the background code-map refresh.

``python -P -m mnemo_memory.apps.cli.source_refresh_entry --data-dir D --project-root R``

The hook starts it detached after building its output (spec 2026-10-03 §2-§3). Like
``judge_entry`` it loads neither typer nor the CLI module, prints nothing, and returns 0
whatever happens, so a broken refresh never reaches a session. Arguments are read by hand so a
bad one stays silent too.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path


def _arguments(argv: Sequence[str]) -> tuple[Path, Path] | None:
    """``(data_dir, project_root)`` for exactly ``--data-dir D --project-root R``, either order."""

    if len(argv) != 4:
        return None
    values = dict(zip(argv[0::2], argv[1::2], strict=True))
    if set(values) != {"--data-dir", "--project-root"}:
        return None
    data_dir = Path(values["--data-dir"])
    project_root = Path(values["--project-root"])
    if not data_dir.is_absolute() or not project_root.is_absolute():
        return None
    return data_dir, project_root


def main(argv: Sequence[str] | None = None) -> int:
    try:
        parsed = _arguments(sys.argv[1:] if argv is None else argv)
        if parsed is not None:
            from mnemo_memory.connectors.automatic_memory import source_refresh

            source_refresh.run_source_refresh(*parsed)
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

(The entry calls through the module attribute `source_refresh.run_source_refresh` so that tests can monkeypatch it.)

- [ ] **Step 10: Run the worker tests, lint and type-check**

Run: `uv run ruff check --fix src/mnemo_memory/connectors/automatic_memory src/mnemo_memory/apps/cli/source_refresh_entry.py tests/unit/test_source_refresh_worker.py tests/unit/test_source_observation_skip.py && uv run ruff format src/mnemo_memory/connectors/automatic_memory src/mnemo_memory/apps/cli/source_refresh_entry.py tests/unit/test_source_refresh_worker.py tests/unit/test_source_observation_skip.py && uv run pytest tests/unit/test_source_refresh_worker.py tests/unit/test_source_observation_skip.py -q`
Expected: 16 passed.

Run: `uv run mypy src/mnemo_memory/connectors/automatic_memory src/mnemo_memory/apps/cli/source_refresh_entry.py tests/unit/test_source_refresh_worker.py tests/unit/test_source_observation_skip.py && uv run python scripts/check_architecture.py`
Expected: `Success: no issues found` and `Architecture dependency check passed`.

- [ ] **Step 11: Commit**

```bash
git add src/mnemo_memory/connectors/automatic_memory/source_refresh.py src/mnemo_memory/apps/cli/source_refresh_entry.py tests/unit/test_source_refresh_worker.py
git commit -m "feat(automatic-memory): background source refresh worker and light entry" -m "$CO_AUTHOR" -- src/mnemo_memory/connectors/automatic_memory/source_observation.py src/mnemo_memory/connectors/automatic_memory/git_observation.py src/mnemo_memory/connectors/automatic_memory/source_refresh.py src/mnemo_memory/apps/cli/source_refresh_entry.py tests/unit/test_source_observation_skip.py tests/unit/test_source_refresh_worker.py
```

---

### Task 3: The hook never parses; it starts the worker after its output

**Files:**
- Modify: `src/mnemo_memory/connectors/automatic_memory/hook.py`. The changes cover the module docstring, the imports at lines 24-55, the aliases after line 114, the dataclass fields at lines 121-134, `handle` at line 136 and its four refresh call sites (about lines 181, 234, 268 and 348), `_refresh_source_structure` / `_observe_git` at lines 496-550, `_SourceRefresh` at lines 666-673, `_clean_git_baseline` at line 1005, and the endings of `_checkpoint_instruction` and `_dirty_session_instruction`.
- Modify: `src/mnemo_memory/apps/cli/main.py`. Add `_start_source_refresh` after `_maybe_start_note_judge` (about line 1395) and the builder injection in `build_automatic_memory_hook` (about line 4535).
- Create: `tests/conftest.py`
- Create: `tests/unit/test_hook_background_source_refresh.py`
- Modify: `tests/unit/test_automatic_memory.py`, `tests/unit/test_git_observation.py`

**Interfaces:**
- Consumes: `source_snapshot_is_fresh`, `observe_and_store_git`, `SourceRefreshLock` and `run_source_refresh` (in tests), all from Task 2.
- Produces:
  - `AutomaticMemoryHook.source_refresh_starter: Callable[[Path, Path], None] | None = None`
  - `SOURCE_REFRESH_PENDING_NOTICE: str` in `hook.py`
  - `_SourceRefresh.refresh_pending: bool = False`
  - `mnemo_memory.apps.cli.main._start_source_refresh(data_directory: Path, project_root: Path) -> None`
  - the environment variable `MNEMO_DISABLE_BACKGROUND_SOURCE_REFRESH` (`"1"` turns spawning off)

- [ ] **Step 1: Add the suite-wide spawn guard**

Create `tests/conftest.py`:

```python
"""Suite-wide guards for the whole test tree."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_background_source_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may start a real background code-map refresh worker (spec 2026-10-03 §7).

    The CLI's starter reads this variable at call time, so it also reaches hooks that tests run
    in a subprocess. Tests of the starter itself remove it and replace ``subprocess.Popen``.
    """
    monkeypatch.setenv("MNEMO_DISABLE_BACKGROUND_SOURCE_REFRESH", "1")
```

- [ ] **Step 2: Write the failing hook and starter tests**

Create `tests/unit/test_hook_background_source_refresh.py`:

```python
"""Lifecycle hooks never parse source; a stale map is refreshed by a detached worker (spec §2)."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import warnings
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from mnemo_memory.apps.cli import main as cli
from mnemo_memory.connectors.automatic_memory.git_observation import GitSourceObserver
from mnemo_memory.connectors.automatic_memory.hook import (
    SOURCE_REFRESH_PENDING_NOTICE,
    AutomaticMemoryHook,
)
from mnemo_memory.connectors.automatic_memory.source_refresh import (
    SourceRefreshLock,
    run_source_refresh,
)
from mnemo_memory.packages.application.automatic_memory import (
    LocalMemoryProjectBindingStore,
    MemoryProjectBinding,
)
from mnemo_memory.packages.application.bootstrap import build_checkpoint_runtime
from mnemo_memory.packages.application.checkpoints import CreateCheckpoint
from mnemo_memory.packages.application.config import LocalConfig
from mnemo_memory.packages.domain import (
    CheckpointContent,
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    MemoryScope,
    SourceId,
    SourceTrustClass,
    VerificationStatus,
)
from mnemo_memory.packages.project_index import SourceStructureParser, SourceStructureParseRequest
from mnemo_memory.packages.storage import SQLiteSourceStructureRepository

_NO_GIT = GitSourceObserver(lambda arguments, root: None)
_CLEAN_COMMIT = "c" * 40
_CLEAN_ANSWERS: dict[tuple[str, ...], str] = {
    ("rev-parse", "--is-inside-work-tree"): "true",
    ("rev-parse", "--verify", "HEAD"): _CLEAN_COMMIT,
    ("status", "--porcelain=v1", "-z"): "",
}
_CLEAN_GIT = GitSourceObserver(lambda arguments, root: _CLEAN_ANSWERS.get(arguments))
_SAVE = "mcp__mnemo-memory__save_checkpoint"


class _Starts:
    """A fake spawner that records each start instead of launching a process."""

    def __init__(self) -> None:
        self.calls: list[tuple[Path, Path]] = []

    def __call__(self, data_directory: Path, project_root: Path) -> None:
        self.calls.append((data_directory, project_root))


def _project(tmp_path: Path) -> tuple[Path, Path, MemoryProjectBinding]:
    project = tmp_path / "repo"
    project.mkdir()
    (project / "service.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    data = tmp_path / "data"
    return project, data, LocalMemoryProjectBindingStore(data).enable(project)


def _event(name: str, project: Path, session: str = "s1", **fields: object) -> dict[str, object]:
    return {"hook_event_name": name, "session_id": session, "cwd": str(project), **fields}


def _prime(data: Path, project: Path) -> None:
    """Build the map the way the background worker does, so the next hook sees it fresh."""
    assert run_source_refresh(data, project, git_observer=_NO_GIT).ran


def _record_parses(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    parsed: list[str] = []
    real = SourceStructureParser.parse

    def recording(self: SourceStructureParser, request: SourceStructureParseRequest) -> Any:
        parsed.append("parse")
        return real(self, request)

    monkeypatch.setattr(SourceStructureParser, "parse", recording)
    return parsed


def _context(result: dict[str, object]) -> str:
    output = result["hookSpecificOutput"]
    assert isinstance(output, dict)
    return str(output["additionalContext"])


def _create_handoff(data: Path, binding: MemoryProjectBinding) -> None:
    content = CheckpointContent(
        task_objective="Preserve the focused task.",
        completed_work=("Recorded bounded progress.",),
        current_state="The handoff is durable.",
        remaining_work=("Continue the focused task.",),
        decisions=("Do not expand scope.",),
        failures=(),
        blockers=(),
        relevant_files=("service.py",),
        relevant_artifacts=(),
        verification_performed=("Focused evidence recorded.",),
        token_estimate=70,
    )
    evidence = EvidenceReference(
        EvidenceId.new(),
        SourceId.new(),
        EvidenceSourceType.CHECKPOINT,
        SourceTrustClass.USER_AUTHORED,
        "fixture://background-refresh/persisted",
        "sha256:" + "d" * 64,
        EvidenceLocation("fixture://background-refresh/persisted"),
        datetime(2026, 10, 3, tzinfo=UTC),
        VerificationStatus.VERIFIED,
    )
    with build_checkpoint_runtime(LocalConfig.defaults(data)) as runtime:
        runtime.checkpoint_service.create(
            CreateCheckpoint(binding.checkpoint_scope, content, (evidence,))
        )


def test_a_stale_tree_is_never_parsed_in_the_hook_and_starts_one_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, data, binding = _project(tmp_path)
    parsed = _record_parses(monkeypatch)
    starts = _Starts()
    hook = AutomaticMemoryHook(data, "codex", source_refresh_starter=starts)

    context = _context(hook.handle(_event("SessionStart", project)))

    assert parsed == []
    assert starts.calls == [(data, binding.project_root)]
    assert "current_source_digest" not in context  # an unproven map is never called current
    repository = SQLiteSourceStructureRepository(data / "mnemo.sqlite3")
    assert repository.get_active_snapshot(binding.scope) is None


def test_a_fresh_tree_behaves_as_before_and_starts_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, data, _ = _project(tmp_path)
    _prime(data, project)
    parsed = _record_parses(monkeypatch)
    starts = _Starts()
    hook = AutomaticMemoryHook(data, "codex", git_observer=_NO_GIT, source_refresh_starter=starts)

    started = _context(hook.handle(_event("SessionStart", project)))
    hook.handle(_event("PostToolUse", project, tool_name="Edit"))
    reminder = _context(hook.handle(_event("UserPromptSubmit", project, prompt="next request")))

    assert "current_source_digest" in started
    assert SOURCE_REFRESH_PENDING_NOTICE not in started
    assert reminder.startswith("MNEMO_DIRTY_V1")
    assert SOURCE_REFRESH_PENDING_NOTICE not in reminder
    assert len(reminder) <= 256
    assert parsed == []
    assert starts.calls == []


def test_a_pending_refresh_adds_the_fixed_notice_line(tmp_path: Path) -> None:
    project, data, _ = _project(tmp_path)
    hook = AutomaticMemoryHook(data, "claude-code", source_refresh_starter=_Starts())
    hook.handle(_event("SessionStart", project))
    hook.handle(_event("PostToolUse", project, tool_name="Edit"))

    reminder = _context(hook.handle(_event("UserPromptSubmit", project, prompt="next request")))
    stop = hook.handle(_event("Stop", project))
    compact = _context(hook.handle(_event("PreCompact", project)))

    assert reminder.startswith("MNEMO_DIRTY_V1")
    assert reminder.splitlines()[-1] == SOURCE_REFRESH_PENDING_NOTICE
    assert stop["decision"] == "block"
    assert str(stop["reason"]).splitlines()[-1] == SOURCE_REFRESH_PENDING_NOTICE
    assert SOURCE_REFRESH_PENDING_NOTICE in compact.splitlines()
    for text in (reminder, str(stop["reason"]), compact):
        assert str(project) not in text
        assert "service.py" not in text


def test_a_failed_start_is_ignored(tmp_path: Path) -> None:
    project, data, _ = _project(tmp_path)

    def broken(data_directory: Path, project_root: Path) -> None:
        raise OSError("synthetic spawn failure")

    failing = AutomaticMemoryHook(data, "codex", source_refresh_starter=broken)
    plain = AutomaticMemoryHook(data, "codex")

    assert failing.handle(_event("SessionStart", project)) == plain.handle(
        _event("SessionStart", project, session="s2")
    )


def test_no_worker_starts_while_one_holds_the_lock(tmp_path: Path) -> None:
    project, data, _ = _project(tmp_path)
    starts = _Starts()
    hook = AutomaticMemoryHook(data, "codex", source_refresh_starter=starts)

    with SourceRefreshLock(data).hold() as held:
        assert held
        hook.handle(_event("SessionStart", project))

    assert starts.calls == []
    hook.handle(_event("SessionStart", project, session="s2"))
    assert len(starts.calls) == 1


def test_a_checkpoint_save_writes_no_git_baseline_while_the_map_is_stale(tmp_path: Path) -> None:
    project, data, binding = _project(tmp_path)
    _prime(data, project)
    starts = _Starts()
    hook = AutomaticMemoryHook(data, "codex", git_observer=_CLEAN_GIT, source_refresh_starter=starts)
    hook.handle(_event("SessionStart", project))
    state_path = data / "automatic-memory-session-state.json"
    assert json.loads(state_path.read_text(encoding="utf-8"))["s1"]["git_source_digest"]

    (project / "service.py").write_text("def run():\n    return 2\n", encoding="utf-8")
    hook.handle(_event("PostToolUse", project, tool_name="Edit"))
    _create_handoff(data, binding)
    saved = hook.handle(
        _event("PostToolUse", project, tool_name=_SAVE, tool_input={"operation": "create"})
    )

    assert saved == {}
    assert "git_source_digest" not in json.loads(state_path.read_text(encoding="utf-8"))["s1"]
    assert starts.calls == [(data, binding.project_root)]


def test_a_stale_tree_never_holds_the_hook_for_a_slow_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wall-time guard (spec §7): a 5 s parser cannot slow any hook event past 1 s."""
    project, data, _ = _project(tmp_path)

    def slow(self: SourceStructureParser, request: SourceStructureParseRequest) -> Any:
        time.sleep(5)
        raise AssertionError("a lifecycle hook parsed source")

    monkeypatch.setattr(SourceStructureParser, "parse", slow)
    starts = _Starts()
    hook = AutomaticMemoryHook(data, "codex", source_refresh_starter=starts)

    for event in (
        _event("SessionStart", project),
        _event("PostToolUse", project, tool_name="Edit"),
        _event("UserPromptSubmit", project, prompt="next request"),
        _event("Stop", project),
    ):
        started = time.perf_counter()
        hook.handle(event)
        assert time.perf_counter() - started < 1.0
    assert len(starts.calls) == 3


def test_the_worker_starts_only_after_the_output_is_built(tmp_path: Path) -> None:
    project, data, _ = _project(tmp_path)
    order: list[str] = []

    def loader(scope: MemoryScope) -> str | None:
        order.append("output")
        return None

    def start(data_directory: Path, project_root: Path) -> None:
        order.append("start")

    hook = AutomaticMemoryHook(data, "codex", context_loader=loader, source_refresh_starter=start)
    hook.handle(_event("SessionStart", project))

    assert order == ["output", "start"]


def test_the_start_is_detached_and_passes_each_path_as_one_argument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review focus 4: spaces, quotes and non-ASCII stay one argv element; no shell."""
    monkeypatch.delenv("MNEMO_DISABLE_BACKGROUND_SOURCE_REFRESH", raising=False)
    data = tmp_path / "Application Support" / "it's mnemo"
    project = tmp_path / "repo Ω \"quoted\""
    seen: dict[str, Any] = {}

    class FakeProcess:
        def __init__(self, command: list[str], **kwargs: Any) -> None:
            seen.update(kwargs, command=command)

        def wait(self, timeout: float | None = None) -> int:
            raise AssertionError("the hook must never wait for the refresh worker")

    monkeypatch.setattr(subprocess, "Popen", FakeProcess)
    cli._start_source_refresh(data, project)

    assert seen["command"] == [
        sys.executable,
        "-P",
        "-m",
        "mnemo_memory.apps.cli.source_refresh_entry",
        "--data-dir",
        str(data),
        "--project-root",
        str(project),
    ]
    assert seen["start_new_session"] is True
    assert seen["close_fds"] is True
    assert seen["stdin"] == seen["stdout"] == seen["stderr"] == subprocess.DEVNULL
    assert "shell" not in seen and "env" not in seen


def test_the_start_is_off_while_the_guard_variable_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MNEMO_DISABLE_BACKGROUND_SOURCE_REFRESH", "1")

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("no real worker may start while the guard is set")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    cli._start_source_refresh(tmp_path, tmp_path)


def test_dropping_the_worker_handle_raises_no_resource_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MNEMO_DISABLE_BACKGROUND_SOURCE_REFRESH", raising=False)

    class WarningProcess:
        def __init__(self, command: list[str], **kwargs: Any) -> None:
            warnings.warn("subprocess 123 is still running", ResourceWarning, stacklevel=2)

    monkeypatch.setattr(subprocess, "Popen", WarningProcess)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cli._start_source_refresh(tmp_path, tmp_path)
    assert caught == []


def test_the_cli_hook_composition_injects_the_detached_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[tuple[Path, Path]] = []

    def record(data_directory: Path, project_root: Path) -> None:
        started.append((data_directory, project_root))

    monkeypatch.setattr(cli, "_start_source_refresh", record)
    hook = cli.build_automatic_memory_hook(LocalConfig.defaults(tmp_path / "data"), "codex")

    assert hook.source_refresh_starter is not None
    hook.source_refresh_starter(tmp_path / "data", tmp_path)
    assert started == [(tmp_path / "data", tmp_path)]
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest tests/unit/test_hook_background_source_refresh.py -q`
Expected: collection ERROR, `ImportError: cannot import name 'SOURCE_REFRESH_PENDING_NOTICE'`.

- [ ] **Step 4: Change the hook's imports, aliases, notice and field**

In `hook.py`, replace the module docstring's last sentence. Old:

```
conversation text). On a trusted enabled project boundary it may refresh Mnemo's bounded static
source-structure projection; it never stores source text.
"""
```

New:

```
conversation text). On a trusted enabled project boundary it checks, by file metadata only,
whether Mnemo's bounded static source-structure map is current. It never parses or stores source
here; a stale map is refreshed by a detached background worker it starts after its output is
built (spec 2026-10-03). It never stores source text.
"""
```

Replace:

```python
from mnemo_memory.connectors.automatic_memory.git_observation import (
    GitObservationStore,
    GitSourceObservation,
    GitSourceObserver,
)
from mnemo_memory.connectors.automatic_memory.source_observation import (
    refresh_registered_project_source,
)
```

with:

```python
from mnemo_memory.connectors.automatic_memory.git_observation import (
    GitObservationStore,
    GitSourceObservation,
    GitSourceObserver,
    observe_and_store_git,
)
from mnemo_memory.connectors.automatic_memory.source_observation import (
    source_snapshot_is_fresh,
)
from mnemo_memory.connectors.automatic_memory.source_refresh import SourceRefreshLock
```

In the `mnemo_memory.packages.domain` import, replace `    CodeFile,\n    CodeSnapshotId,` with `    CodeFile,\n    CodeSnapshot,\n    CodeSnapshotId,`.

After `_DeliveryTelemetryObserver = Callable[[UUID, int, int, bool], None]` add:

```python
_SourceRefreshStarter = Callable[[Path, Path], None]

# Spec 2026-10-03 §2: the one fixed line a pending background refresh adds to a notice.
SOURCE_REFRESH_PENDING_NOTICE = (
    "Code map refresh running in background; structural change details may lag one prompt."
)
```

After `    delivery_telemetry_observer: _DeliveryTelemetryObserver | None = None` add:

```python
    # Starts the detached code-map refresh worker with (data_directory, project_root). ``None``
    # starts nothing; the CLI composition injects the real detached start.
    source_refresh_starter: _SourceRefreshStarter | None = None
```

- [ ] **Step 5: Split `handle` so the start happens after the output is built**

Replace:

```python
    def handle(self, event: object) -> dict[str, object]:
        if not isinstance(event, dict):
```

with:

```python
    def handle(self, event: object) -> dict[str, object]:
        refresh_requests: list[MemoryProjectBinding] = []
        output = self._handle(event, refresh_requests)
        if refresh_requests:
            # Spec 2026-10-03 §2: start the worker only once the output exists; never wait.
            self._start_source_refresh(refresh_requests[0])
        return output

    def _handle(
        self, event: object, refresh_requests: list[MemoryProjectBinding]
    ) -> dict[str, object]:
        if not isinstance(event, dict):
```

Then add one line after each of the four refresh calls.

PostToolUse after a save — replace:

```python
                refreshed = self._refresh_source_structure(binding)
                git_source_digest, git_clean_commit_id = _clean_git_baseline(refreshed)
                state_store.save(
                    session_id,
                    dirty=False,
                    saved=True,
```

with:

```python
                refreshed = self._refresh_source_structure(binding)
                _note_pending_refresh(refreshed, binding, refresh_requests)
                git_source_digest, git_clean_commit_id = _clean_git_baseline(refreshed)
                state_store.save(
                    session_id,
                    dirty=False,
                    saved=True,
```

SessionStart — replace:

```python
            refreshed = self._refresh_source_structure(binding)
            git_source_digest, git_clean_commit_id = _clean_git_baseline(refreshed)
            state_store.save(
                session_id,
                dirty=False,
                saved=False,
```

with:

```python
            refreshed = self._refresh_source_structure(binding)
            _note_pending_refresh(refreshed, binding, refresh_requests)
            git_source_digest, git_clean_commit_id = _clean_git_baseline(refreshed)
            state_store.save(
                session_id,
                dirty=False,
                saved=False,
```

UserPromptSubmit — replace:

```python
                refreshed = self._refresh_source_structure(binding)
            prompt_attachment = self._attached_prompt_context(binding.checkpoint_scope, event)
```

with:

```python
                refreshed = self._refresh_source_structure(binding)
                _note_pending_refresh(refreshed, binding, refresh_requests)
            prompt_attachment = self._attached_prompt_context(binding.checkpoint_scope, event)
```

Stop/PreCompact — replace:

```python
            refreshed = self._refresh_source_structure(binding)
            instruction = _checkpoint_instruction(binding.checkpoint_scope.to_dict(), refreshed)
```

with:

```python
            refreshed = self._refresh_source_structure(binding)
            _note_pending_refresh(refreshed, binding, refresh_requests)
            instruction = _checkpoint_instruction(binding.checkpoint_scope.to_dict(), refreshed)
```

- [ ] **Step 6: Replace the refresh with the freshness-only read and add the start**

Replace the whole of `_refresh_source_structure` and `_observe_git`, from `    def _refresh_source_structure(` through the `return observation` that ends `_observe_git`, with:

```python
    def _refresh_source_structure(
        self, binding: MemoryProjectBinding, *, include_latest_transition: bool = False
    ) -> _SourceRefresh:
        """Read the stored map and check freshness by file metadata only; never parse here.

        Fresh: as before; Git is observed for the active digest, and the latest stored transition
        is diffed only when the caller asks. Stale or missing: ``refresh_pending`` is set, no
        digest is claimed current and no Git baseline is offered, and ``handle`` starts the
        background worker after its output is built (spec 2026-10-03 §2). Failure never blocks a
        coding client session.
        """
        try:
            repository = SQLiteSourceStructureRepository(self.data_directory / "mnemo.sqlite3")
            repository.migrate()
            active = repository.get_active_snapshot(binding.scope)
            fresh = active is not None and source_snapshot_is_fresh(
                binding, active.snapshot_id, cache_dir=self.data_directory / "scan-cache"
            )
            if active is None or not fresh:
                if not include_latest_transition:
                    return _SourceRefresh(None, refresh_pending=True)
                stored = repository.latest_transition(binding.scope)
                if stored is None:
                    return _SourceRefresh(None, refresh_pending=True)
                return self._transition_refresh(
                    repository,
                    binding,
                    stored,
                    digest=None,
                    git_observation=None,
                    refresh_pending=True,
                )
            git_observation = self._observe_git(binding, active.source_digest)
            if not include_latest_transition:
                return _SourceRefresh(active.source_digest, git_observation=git_observation)
            transition = repository.latest_transition(binding.scope)
            if transition is None:
                return _SourceRefresh(active.source_digest, git_observation=git_observation)
            return self._transition_refresh(
                repository,
                binding,
                transition,
                digest=active.source_digest,
                git_observation=git_observation,
                refresh_pending=False,
            )
        except (ProjectIndexRepositoryError, OSError, ValueError, RuntimeError):
            return _SourceRefresh(None)

    def _transition_refresh(
        self,
        repository: SQLiteSourceStructureRepository,
        binding: MemoryProjectBinding,
        transition: tuple[CodeSnapshot, CodeSnapshot],
        *,
        digest: str | None,
        git_observation: GitSourceObservation | None,
        refresh_pending: bool,
    ) -> _SourceRefresh:
        before, after = transition
        diff = SourceImpactService(repository).diff(
            binding.scope, before.snapshot_id, after.snapshot_id
        )
        changes = _SourceChangeSummary.from_diff(diff)
        return _SourceRefresh(
            digest,
            changes,
            _dependent_impact_cues(repository, binding.scope, diff, changes),
            _dbt_downstream_cues(self.data_directory, binding.scope, changes),
            git_observation,
            GitObservationStore(self.data_directory).get(binding.scope, before.source_digest),
            refresh_pending,
        )

    def _observe_git(
        self, binding: MemoryProjectBinding, source_digest: str
    ) -> GitSourceObservation | None:
        return observe_and_store_git(
            self.data_directory,
            binding.project_root,
            binding.scope,
            source_digest,
            self.git_observer,
        )

    def _start_source_refresh(self, binding: MemoryProjectBinding) -> None:
        """Start the detached worker unless one runs; ignore every failure (spec §2)."""
        if self.source_refresh_starter is None:
            return
        try:
            if SourceRefreshLock(self.data_directory).running():
                return  # a status probe only; the worker's own lock is authoritative
            self.source_refresh_starter(self.data_directory, binding.project_root)
        except Exception:
            return
```

- [ ] **Step 7: Carry `refresh_pending` and add the notice line**

In `_SourceRefresh`, after `    previous_git_observation: GitSourceObservation | None = None` add:

```python
    refresh_pending: bool = False
```

After `_clean_git_baseline`, add:

```python
def _note_pending_refresh(
    refreshed: _SourceRefresh,
    binding: MemoryProjectBinding,
    refresh_requests: list[MemoryProjectBinding],
) -> None:
    if refreshed.refresh_pending:
        refresh_requests.append(binding)
```

The following text occurs exactly twice, at the end of `_checkpoint_instruction` and at the end of `_dirty_session_instruction`:

```python
    if refreshed.dbt_impact_cues:
        instruction += _dbt_impact_instruction(refreshed.dbt_impact_cues)
    return instruction
```

Replace both occurrences (replace-all) with:

```python
    if refreshed.dbt_impact_cues:
        instruction += _dbt_impact_instruction(refreshed.dbt_impact_cues)
    if refreshed.refresh_pending:
        instruction += "\n" + SOURCE_REFRESH_PENDING_NOTICE
    return instruction
```

(`_resume_instruction` has an 8-space-indented variant followed by a Git branch, so it does not match and stays unchanged.)

- [ ] **Step 8: Add the detached start and inject it in the CLI composition**

In `src/mnemo_memory/apps/cli/main.py`, directly before `@dataclass(frozen=True, slots=True)\nclass _TypedApplication:`, add:

```python
_BACKGROUND_SOURCE_REFRESH_OFF = "MNEMO_DISABLE_BACKGROUND_SOURCE_REFRESH"


def _start_source_refresh(data_directory: Path, project_root: Path) -> None:
    """Start the background code-map refresh detached: a new session, no pipes, no waiting.

    The command line names only the data directory and the enabled project root, each as one
    argument and without a shell (spec 2026-10-03 §2). It runs the light
    ``source_refresh_entry`` module, which loads neither typer nor this CLI module; ``-P`` keeps
    the working directory off ``sys.path``. ``MNEMO_DISABLE_BACKGROUND_SOURCE_REFRESH=1`` turns
    the start off (the test suite sets it so no test starts a real worker).
    """

    if os.environ.get(_BACKGROUND_SOURCE_REFRESH_OFF) == "1":
        return
    # The handle is dropped on purpose; without this Python warns that the child still runs.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        subprocess.Popen(
            [
                sys.executable,
                "-P",
                "-m",
                "mnemo_memory.apps.cli.source_refresh_entry",
                "--data-dir",
                str(data_directory),
                "--project-root",
                str(project_root),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )


```

In `build_automatic_memory_hook`, replace:

```python
        retention_sweeper=expire_due_checkpoints,
```

with:

```python
        retention_sweeper=expire_due_checkpoints,
        # Looked up at call time so tests can replace ``_start_source_refresh``.
        source_refresh_starter=lambda data_directory, project_root: _start_source_refresh(
            data_directory, project_root
        ),
```

- [ ] **Step 9: Run the new tests**

Run: `uv run ruff check --fix src/mnemo_memory/connectors/automatic_memory/hook.py src/mnemo_memory/apps/cli/main.py tests/conftest.py tests/unit/test_hook_background_source_refresh.py && uv run ruff format src/mnemo_memory/connectors/automatic_memory/hook.py src/mnemo_memory/apps/cli/main.py tests/conftest.py tests/unit/test_hook_background_source_refresh.py && uv run pytest tests/unit/test_hook_background_source_refresh.py -q`
Expected: 12 passed.

- [ ] **Step 10: Run the existing hook suites and confirm the expected failures**

Run: `uv run pytest tests/unit/test_automatic_memory.py tests/unit/test_git_observation.py -q`
Expected failures are tests that relied on the hook parsing a missing or changed tree synchronously:
- in `test_automatic_memory.py`:
  - `test_hook_requests_bounded_checkpoint_only_after_work_and_tracks_save`
  - both parameters of `test_dirty_session_prompt_reminder_never_reads_or_persists_prompt_content`
  - `test_dirty_prompt_boundary_refreshes_and_cues_exact_static_impact`
  - `test_session_start_refreshes_supported_static_source_structure`
  - `test_session_start_indexes_typescript_without_reading_source_text`
  - `test_automatic_memory_persists_a_mixed_language_map_for_later_context`
  - `test_stop_after_a_mutation_refreshes_the_static_structure_before_checkpointing`
  - `test_checkpoint_save_refreshes_changed_structure_without_waiting_for_stop_or_restart`
  - `test_session_start_reports_a_bounded_prior_structural_change_without_source_text`
  - `test_session_start_reports_a_body_only_file_transition_without_source_text`
  - `test_session_start_attaches_bounded_static_dependents_for_an_exact_changed_file`
  - `test_session_start_attaches_authoritative_dbt_downstream_cue_for_changed_model`
  - `test_session_start_uses_new_path_of_digest_proven_renamed_dbt_model`
- in `test_git_observation.py`:
  - `test_automatic_hook_attaches_git_state_without_source_or_status_output`
  - `test_clean_git_observation_proves_read_only_shell_needs_no_checkpoint`

(`test_shell_with_changed_git_state_still_requires_checkpoint` and `test_shell_without_a_clean_git_baseline_still_requires_checkpoint` may pass for the wrong reason; Step 12 primes them too.)

The rule for any *other* failure: if it fails only because no map exists yet (a missing `current_source_digest`, a missing Git line, a missing snapshot, or the notice line in a fresh-path assertion), add `_prime_source_map(data, project)` directly after the test's `LocalMemoryProjectBindingStore(data).enable(project)` and change no assertion. Any failure of another kind is a bug in Steps 4-8: fix the code, not the test.

- [ ] **Step 11: Adapt `tests/unit/test_automatic_memory.py`**

Add these imports. `ruff --fix` orders them; `AutomaticMemoryHook` and `PromptContextAttachment` stay in the hook import.

```python
from mnemo_memory.connectors.automatic_memory.git_observation import GitSourceObserver
from mnemo_memory.connectors.automatic_memory.hook import (
    SOURCE_REFRESH_PENDING_NOTICE,
    AutomaticMemoryHook,
    PromptContextAttachment,
    _checkpoint_instruction,
    _dirty_session_instruction,
    _resume_instruction,
)
from mnemo_memory.connectors.automatic_memory.source_refresh import run_source_refresh
```

(the second block replaces the existing `from mnemo_memory.connectors.automatic_memory.hook import (AutomaticMemoryHook, PromptContextAttachment,)`).

After `_run_hook_process`, add:

```python
_NO_GIT = GitSourceObserver(lambda arguments, root: None)


def _prime_source_map(data: Path, project: Path) -> None:
    """Build the code map the way the background worker does, so the next hook sees it fresh."""
    assert run_source_refresh(data, project, git_observer=_NO_GIT).ran


def _inline_source_refresh(data_directory: Path, project_root: Path) -> None:
    """A fake spawner: run the worker in-process, right after the hook has built its output."""
    run_source_refresh(data_directory, project_root, git_observer=_NO_GIT)
```

In `test_hook_requests_bounded_checkpoint_only_after_work_and_tracks_save`, replace:

```python
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    swept: list[MemoryProjectBinding] = []
```

with:

```python
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    _prime_source_map(data, project)
    swept: list[MemoryProjectBinding] = []
```

In `test_dirty_session_prompt_reminder_never_reads_or_persists_prompt_content`, replace:

```python
def test_dirty_session_prompt_reminder_never_reads_or_persists_prompt_content(
    tmp_path: Path, client: str
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    LocalMemoryProjectBindingStore(data).enable(project)
```

with:

```python
def test_dirty_session_prompt_reminder_never_reads_or_persists_prompt_content(
    tmp_path: Path, client: str
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    LocalMemoryProjectBindingStore(data).enable(project)
    _prime_source_map(data, project)
```

In each of `test_session_start_refreshes_supported_static_source_structure`, `test_session_start_indexes_typescript_without_reading_source_text` and `test_automatic_memory_persists_a_mixed_language_map_for_later_context`, change `    AutomaticMemoryHook(data, "codex").handle(` to `    AutomaticMemoryHook(data, "codex", source_refresh_starter=_inline_source_refresh).handle(`. In `test_checkpoint_save_refreshes_changed_structure_without_waiting_for_stop_or_restart`, change `    hook = AutomaticMemoryHook(data, "codex")` to `    hook = AutomaticMemoryHook(data, "codex", source_refresh_starter=_inline_source_refresh)`. Their assertions stay. The inline worker runs right after SessionStart's output (and after the save's output), so the snapshots they read exist.

Replace the whole function `test_dirty_prompt_boundary_refreshes_and_cues_exact_static_impact` with:

```python
def test_dirty_prompt_boundary_defers_the_parse_and_keeps_the_exact_static_impact_on_record(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    core = project / "core.py"
    core.write_text("def calculate():\n    return 1\n", encoding="utf-8")
    (project / "service.py").write_text(
        "import core\n\ndef serve():\n    return core.calculate()\n", encoding="utf-8"
    )
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    hook = AutomaticMemoryHook(data, "codex", source_refresh_starter=_inline_source_refresh)
    hook.handle({"hook_event_name": "SessionStart", "session_id": "s1", "cwd": str(project)})
    core.write_text("def calculate():\n    return 2\n", encoding="utf-8")
    hook.handle(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "s1",
            "cwd": str(project),
            "tool_name": "Edit",
        }
    )

    result = hook.handle(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "s1",
            "cwd": str(project),
            "prompt": "private user question must not be retained",
        }
    )

    output = result["hookSpecificOutput"]
    assert isinstance(output, dict)
    instruction = str(output["additionalContext"])
    assert instruction.startswith("MNEMO_DIRTY_V1")
    assert instruction.splitlines()[-1] == SOURCE_REFRESH_PENDING_NOTICE
    assert "Modified files" not in instruction
    assert "private user question must not be retained" not in instruction
    state = (data / "automatic-memory-session-state.json").read_text()
    assert "private user question" not in state

    # The worker ran after that output; the stored transition keeps the bounded cue on request.
    lagged = hook._refresh_source_structure(binding, include_latest_transition=True)
    assert not lagged.refresh_pending
    cue = _dirty_session_instruction(lagged)
    assert "Modified files: core.py." in cue
    assert "static dependent candidates" in cue
    assert "source snapshot " in cue
    assert "service.py:service" in cue
    assert "return 1" not in cue
    assert "return 2" not in cue

    failed_save = hook.handle(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "s1",
            "cwd": str(project),
            "tool_name": "mcp__mnemo-memory__save_checkpoint",
        }
    )
    assert failed_save == {"systemMessage": "MNEMO_MEMORY_CHECKPOINT_NOT_PERSISTED"}
    still_dirty = hook.handle(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "s1",
            "cwd": str(project),
            "prompt": "another private user question",
        }
    )
    assert still_dirty == {}
    _create_test_handoff(data, binding)
```

Replace the whole function `test_stop_after_a_mutation_refreshes_the_static_structure_before_checkpointing` with:

```python
def test_stop_after_a_mutation_defers_the_parse_and_the_worker_stores_the_change(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    source_file = project / "service.py"
    source_file.write_text("def initial():\n    return 1\n")
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    hook = AutomaticMemoryHook(data, "codex", source_refresh_starter=_inline_source_refresh)
    hook.handle({"hook_event_name": "SessionStart", "session_id": "s1", "cwd": str(project)})
    initial = SQLiteSourceStructureRepository(data / "mnemo.sqlite3").get_active_snapshot(
        binding.scope
    )
    assert initial is not None

    source_file.write_text("def current():\n    return 2\n")
    hook.handle(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "s1",
            "cwd": str(project),
            "tool_name": "Edit",
        }
    )
    result = hook.handle({"hook_event_name": "Stop", "session_id": "s1", "cwd": str(project)})
    refreshed = SQLiteSourceStructureRepository(data / "mnemo.sqlite3").get_active_snapshot(
        binding.scope
    )

    assert result["decision"] == "block"
    reason = str(result["reason"])
    assert reason.splitlines()[-1] == SOURCE_REFRESH_PENDING_NOTICE
    assert "Mnemo observed a structural change" not in reason
    assert refreshed is not None
    assert refreshed.snapshot_id != initial.snapshot_id  # the worker ran after the output

    lagged = hook._refresh_source_structure(binding, include_latest_transition=True)
    recorded = _checkpoint_instruction(binding.checkpoint_scope.to_dict(), lagged)
    assert "Mnemo observed a structural change" in recorded
    assert "service.py:service.current" in recorded
    assert "service.py:service.initial" in recorded
    assert "return 2" not in recorded
    assert str(project) not in recorded
```

Replace the whole function `test_session_start_reports_a_bounded_prior_structural_change_without_source_text` with:

```python
def test_session_start_defers_a_changed_tree_and_keeps_the_bounded_transition_on_record(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    source_file = project / "service.py"
    source_file.write_text("def initial():\n    return 'private initial body'\n")
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    hook = AutomaticMemoryHook(data, "codex", source_refresh_starter=_inline_source_refresh)
    hook.handle({"hook_event_name": "SessionStart", "session_id": "first", "cwd": str(project)})

    source_file.write_text("def current():\n    return 'private changed body'\n")
    result = hook.handle(
        {"hook_event_name": "SessionStart", "session_id": "second", "cwd": str(project)}
    )

    context = result["hookSpecificOutput"]
    assert isinstance(context, dict)
    instruction = str(context["additionalContext"])
    assert "Mnemo observed a structural change" not in instruction
    assert "current_source_digest" not in instruction  # an unproven map is never called current
    assert "private changed body" not in instruction

    lagged = hook._refresh_source_structure(binding, include_latest_transition=True)
    recorded = _resume_instruction(binding.checkpoint_scope.to_dict(), lagged)
    assert "Mnemo observed a structural change" in recorded
    assert "service.py:service.current" in recorded
    assert "service.py:service.initial" in recorded
    assert "private initial body" not in recorded
    assert "private changed body" not in recorded
    assert str(project) not in recorded

    later = hook.handle(
        {"hook_event_name": "SessionStart", "session_id": "third", "cwd": str(project)}
    )
    later_output = later["hookSpecificOutput"]
    assert isinstance(later_output, dict)
    later_instruction = str(later_output["additionalContext"])
    assert "most recent saved transition" not in later_instruction
    assert "source_changes" in later_instruction
    assert "current_source_digest" in later_instruction
    assert "private initial body" not in later_instruction
    assert "private changed body" not in later_instruction
```

Replace the whole function `test_session_start_reports_a_body_only_file_transition_without_source_text` with:

```python
def test_a_body_only_file_transition_is_recorded_without_source_text(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    source_file = project / "pricing.py"
    source_file.write_text("def price():\n    return 'private first implementation'\n")
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    hook = AutomaticMemoryHook(data, "codex", source_refresh_starter=_inline_source_refresh)
    hook.handle({"hook_event_name": "SessionStart", "session_id": "first", "cwd": str(project)})

    source_file.write_text("def price():\n    return 'private corrected implementation'\n")
    hook.handle({"hook_event_name": "SessionStart", "session_id": "second", "cwd": str(project)})

    lagged = hook._refresh_source_structure(binding, include_latest_transition=True)
    instruction = _resume_instruction(binding.checkpoint_scope.to_dict(), lagged)
    assert "1 modified" in instruction
    assert "Modified files: pricing.py." in instruction
    assert "private first implementation" not in instruction
    assert "private corrected implementation" not in instruction
    assert str(project) not in instruction
```

Replace the whole function `test_session_start_attaches_bounded_static_dependents_for_an_exact_changed_file` with:

```python
def test_a_recorded_transition_carries_bounded_static_dependents_for_an_exact_changed_file(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    core = project / "core.py"
    core.write_text("def calculate():\n    return 1\n", encoding="utf-8")
    (project / "service.py").write_text(
        "import core\n\ndef serve():\n    return core.calculate()\n", encoding="utf-8"
    )
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    hook = AutomaticMemoryHook(data, "codex", source_refresh_starter=_inline_source_refresh)
    hook.handle({"hook_event_name": "SessionStart", "session_id": "first", "cwd": str(project)})

    core.write_text("def calculate():\n    return 2\n", encoding="utf-8")
    hook.handle({"hook_event_name": "SessionStart", "session_id": "second", "cwd": str(project)})

    lagged = hook._refresh_source_structure(binding, include_latest_transition=True)
    instruction = _resume_instruction(binding.checkpoint_scope.to_dict(), lagged)
    assert "static dependent candidates" in instruction
    assert "source snapshot " in instruction
    assert "core.py" in instruction
    assert "service.py:service" in instruction
    assert "return 1" not in instruction
    assert "return 2" not in instruction
```

Replace the whole function `test_session_start_attaches_authoritative_dbt_downstream_cue_for_changed_model` with:

```python
def test_a_recorded_transition_carries_the_authoritative_dbt_downstream_cue(
    tmp_path: Path,
) -> None:
    project = tmp_path / "dbt repo"
    model = project / "models" / "marts" / "fct_orders.sql"
    model.parent.mkdir(parents=True)
    model.write_text("select 1\n", encoding="utf-8")
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    checkpoint_repository = SQLiteCheckpointRepository(data / "mnemo.sqlite3")
    checkpoint_repository.migrate()
    ingested = DbtManifestApplicationService(checkpoint_repository, DbtManifestParser()).ingest(
        IngestManifest(
            binding.scope,
            DBT_FIXTURE.read_bytes(),
            "tests/fixtures/dbt/manifest-v12.json",
            datetime(2026, 8, 4, tzinfo=UTC),
        )
    )
    hook = AutomaticMemoryHook(data, "codex", source_refresh_starter=_inline_source_refresh)
    hook.handle({"hook_event_name": "SessionStart", "session_id": "first", "cwd": str(project)})

    model.write_text("select 2\n", encoding="utf-8")
    hook.handle({"hook_event_name": "SessionStart", "session_id": "second", "cwd": str(project)})

    lagged = hook._refresh_source_structure(binding, include_latest_transition=True)
    instruction = _resume_instruction(binding.checkpoint_scope.to_dict(), lagged)
    assert "authoritative dbt-manifest downstream facts" in instruction
    assert "models/marts/fct_orders.sql" in instruction
    assert "model.mnemo_analytics.mart_customer_value" in instruction
    assert str(ingested.snapshot.snapshot_id) in instruction
    assert "currentness unknown" in instruction
    assert "select 1" not in instruction
    assert "select 2" not in instruction
```

Replace the whole function `test_session_start_uses_new_path_of_digest_proven_renamed_dbt_model` with:

```python
def test_a_recorded_transition_uses_the_new_path_of_a_digest_proven_renamed_dbt_model(
    tmp_path: Path,
) -> None:
    project = tmp_path / "dbt repo"
    legacy = project / "models" / "marts" / "legacy_orders.sql"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("select 1\n", encoding="utf-8")
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    checkpoint_repository = SQLiteCheckpointRepository(data / "mnemo.sqlite3")
    checkpoint_repository.migrate()
    ingested = DbtManifestApplicationService(checkpoint_repository, DbtManifestParser()).ingest(
        IngestManifest(
            binding.scope,
            DBT_FIXTURE.read_bytes(),
            "tests/fixtures/dbt/manifest-v12.json",
            datetime(2026, 8, 4, tzinfo=UTC),
        )
    )
    hook = AutomaticMemoryHook(data, "codex", source_refresh_starter=_inline_source_refresh)
    hook.handle({"hook_event_name": "SessionStart", "session_id": "first", "cwd": str(project)})

    legacy.rename(project / "models" / "marts" / "fct_orders.sql")
    hook.handle({"hook_event_name": "SessionStart", "session_id": "second", "cwd": str(project)})

    lagged = hook._refresh_source_structure(binding, include_latest_transition=True)
    instruction = _resume_instruction(binding.checkpoint_scope.to_dict(), lagged)
    assert (
        "Renamed files: models/marts/legacy_orders.sql → models/marts/fct_orders.sql."
        in instruction
    )
    assert "authoritative dbt-manifest downstream facts" in instruction
    assert "models/marts/fct_orders.sql" in instruction
    assert str(ingested.snapshot.snapshot_id) in instruction
    assert "select 1" not in instruction
```

- [ ] **Step 12: Adapt `tests/unit/test_git_observation.py`**

Add the import `from mnemo_memory.connectors.automatic_memory.source_refresh import run_source_refresh`, and after `_runner` add:

```python
def _prime_source_map(data: Path, project: Path) -> None:
    """Build the map the way the background worker does, without consuming a test runner."""
    assert run_source_refresh(
        data, project, git_observer=GitSourceObserver(lambda arguments, root: None)
    ).ran
```

The line `    LocalMemoryProjectBindingStore(data).enable(project)` occurs exactly 4 times, once in each hook test. Replace all of them with:

```python
    LocalMemoryProjectBindingStore(data).enable(project)
    _prime_source_map(data, project)
```

- [ ] **Step 13: Run every hook-related suite**

Run: `uv run ruff check --fix tests/unit/test_automatic_memory.py tests/unit/test_git_observation.py && uv run ruff format tests/unit/test_automatic_memory.py tests/unit/test_git_observation.py && uv run pytest tests/unit/test_automatic_memory.py tests/unit/test_git_observation.py tests/unit/test_hook_background_source_refresh.py tests/unit/test_typed_hook_integration.py tests/unit/test_typed_note_judge_start.py tests/integration/test_mcp_durability.py tests/evals/test_lifecycle_token_break_even.py -q`
Expected: all pass.

Run: `uv run mypy src tests scripts && uv run python scripts/check_architecture.py`
Expected: `Success: no issues found` and `Architecture dependency check passed`.

- [ ] **Step 14: Commit**

```bash
git add tests/conftest.py tests/unit/test_hook_background_source_refresh.py
git commit -m "fix(hook): never parse source in a lifecycle hook; refresh in the background" -m "$CO_AUTHOR" -- src/mnemo_memory/connectors/automatic_memory/hook.py src/mnemo_memory/apps/cli/main.py tests/conftest.py tests/unit/test_hook_background_source_refresh.py tests/unit/test_automatic_memory.py tests/unit/test_git_observation.py
```

---

### Task 4: `maintenance prune-source` command

**Files:**
- Modify: `src/mnemo_memory/apps/cli/main.py`. Add imports near lines 40-44 and 228-235, the `maintenance_app` after `typed_decisions_app` (about line 344), its `add_typer` after the typed-decisions `add_typer` (about line 352), and the command directly before `def build_automatic_memory_hook`.
- Modify: `tests/unit/test_source_snapshot_pruning.py` (append)

**Interfaces:**
- Consumes:
  - `SQLiteSourceStructureRepository.prune_source_snapshots`, `plan_source_snapshot_prune`, `list_source_scopes`, `vacuum` and `SourceSnapshotPrunePlan` (Task 1)
  - `SourceRefreshLock(data_directory).hold()` (Task 2)
- Produces:
  - the CLI command `mnemo-memory maintenance prune-source [--project-root PATH | --all-projects] [--compact] [--dry-run] [--data-dir DIR]`
  - test seams `mnemo_memory.apps.cli.main._free_disk_bytes(directory: Path) -> int` and `_database_file_bytes(database_path: Path) -> int`
  - stable error codes: `MNEMO_PRUNE_SOURCE_ARGUMENTS_INVALID`, `MNEMO_DATABASE_NOT_FOUND`, `MNEMO_SOURCE_REFRESH_RUNNING`, `MNEMO_COMPACT_INSUFFICIENT_DISK_SPACE`, `MNEMO_COMPACT_UNAVAILABLE`, `MNEMO_PRUNE_SOURCE_UNAVAILABLE`

- [ ] **Step 1: Write the failing CLI tests**

Append to `tests/unit/test_source_snapshot_pruning.py` (add `import json` and the imports below to the file's import block; `ruff --fix` orders them):

```python
import json

from typer.testing import CliRunner

from mnemo_memory.apps.cli import main as cli
from mnemo_memory.connectors.automatic_memory.source_refresh import SourceRefreshLock

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
            ["maintenance", "prune-source", "--project-root", str(project), "--data-dir", str(data)],
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/unit/test_source_snapshot_pruning.py -q -k "dry_run or refuses or compact or all_projects or conflicting"`
Expected: FAIL, exit code 2 with `No such command 'maintenance'`.

- [ ] **Step 3: Add the imports and the command group**

In `main.py`, after:

```python
from mnemo_memory.connectors.automatic_memory.learned_routes import (
    LearnedRouteStoreError,
    LocalLearnedRouteStore,
)
```

add:

```python
from mnemo_memory.connectors.automatic_memory.source_refresh import SourceRefreshLock
```

Replace:

```python
    SQLiteKnowledgeDocumentRepository,
    SQLiteSourceStructureRepository,
)
from mnemo_memory.packages.telemetry import (
```

with:

```python
    SQLiteKnowledgeDocumentRepository,
    SQLiteSourceStructureRepository,
)
from mnemo_memory.packages.storage.contracts import ProjectIndexRepositoryError
from mnemo_memory.packages.telemetry import (
```

Replace:

```python
typed_decisions_app = typer.Typer(
    no_args_is_help=True,
    help="Inspect and set Jev typed-decision modes; live stays locked to synthetic data.",
)
```

with:

```python
typed_decisions_app = typer.Typer(
    no_args_is_help=True,
    help="Inspect and set Jev typed-decision modes; live stays locked to synthetic data.",
)
maintenance_app = typer.Typer(
    no_args_is_help=True,
    help="Tidy and shrink the local Mnemo database.",
)
```

Replace:

```python
app.add_typer(
    typed_decisions_app,
    name="typed-decisions",
    help="Inspect and set Jev typed-decision modes.",
)
```

with:

```python
app.add_typer(
    typed_decisions_app,
    name="typed-decisions",
    help="Inspect and set Jev typed-decision modes.",
)
app.add_typer(maintenance_app, name="maintenance", help="Tidy and shrink the local database.")
```

- [ ] **Step 4: Add the command**

Directly before `def build_automatic_memory_hook(config: LocalConfig, client: ClientName) -> AutomaticMemoryHook:`, add:

```python
def _free_disk_bytes(directory: Path) -> int:
    """Free bytes on the volume that holds ``directory`` (a seam the tests replace)."""
    return shutil.disk_usage(directory).free


def _database_file_bytes(database_path: Path) -> int:
    """The database's size on disk, counting its write-ahead log when one exists."""
    total = 0
    for path in (database_path, database_path.with_name(database_path.name + "-wal")):
        with suppress(FileNotFoundError):
            total += path.stat().st_size
    return total


@maintenance_app.command(
    "prune-source",
    help="Delete old code-map snapshots that memory no longer needs; optionally compact.",
)
def maintenance_prune_source(
    project_root: Path | None = typer.Option(None, "--project-root"),  # noqa: B008
    all_projects: bool = typer.Option(False, "--all-projects"),
    compact: bool = typer.Option(False, "--compact", help="Run VACUUM after pruning."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print counts only; change nothing."),
    data_dir: Path | None = typer.Option(None, "--data-dir"),  # noqa: B008
) -> None:
    """Prune with no per-run limit under the retention rules (spec 2026-10-03 §4-§5).

    Never run from a hook. It holds the refresh worker's lock for its whole run, so it refuses
    while a worker runs and no worker can start meanwhile. ``--compact`` first checks that free
    disk space is at least the database size, so a refusal changes nothing.
    """
    if all_projects and project_root is not None:
        raise typer.BadParameter("MNEMO_PRUNE_SOURCE_ARGUMENTS_INVALID")
    try:
        config = resolve_local_config(data_dir)
        database_path = config.database_path
        if not database_path.exists():
            raise typer.BadParameter("MNEMO_DATABASE_NOT_FOUND")
        repository = SQLiteSourceStructureRepository(
            database_path, base_directory=config.data_directory
        )
        with SourceRefreshLock(config.data_directory).hold() as held:
            if not held:
                raise typer.BadParameter("MNEMO_SOURCE_REFRESH_RUNNING")
            scopes = (
                repository.list_source_scopes()
                if all_projects
                else (
                    _enabled_memory_binding(
                        config.data_directory, project_root or Path(".")
                    ).scope,
                )
            )
            before = _database_file_bytes(database_path)
            if dry_run:
                plans = [repository.plan_source_snapshot_prune(scope) for scope in scopes]
                _show(
                    {
                        "dry_run": True,
                        "projects": len(scopes),
                        "full_snapshots": sum(plan.full_snapshots for plan in plans),
                        "header_only_snapshots": sum(
                            plan.header_only_snapshots for plan in plans
                        ),
                        "symbols": sum(plan.symbols for plan in plans),
                        "edges": sum(plan.edges for plan in plans),
                        "database_bytes": before,
                    }
                )
                return
            if compact and _free_disk_bytes(config.data_directory) < before:
                raise typer.BadParameter("MNEMO_COMPACT_INSUFFICIENT_DISK_SPACE")
            pruned = sum(
                repository.prune_source_snapshots(scope, max_snapshots=None) for scope in scopes
            )
            if compact:
                try:
                    repository.vacuum()
                except ProjectIndexRepositoryError as error:
                    raise typer.BadParameter("MNEMO_COMPACT_UNAVAILABLE") from error
            _show(
                {
                    "dry_run": False,
                    "projects": len(scopes),
                    "pruned_snapshots": pruned,
                    "compacted": compact,
                    "database_bytes_before": before,
                    "database_bytes_after": _database_file_bytes(database_path),
                }
            )
    except (AutomaticMemoryBindingError, ProjectIndexRepositoryError, OSError, ValueError) as error:
        raise typer.BadParameter("MNEMO_PRUNE_SOURCE_UNAVAILABLE") from error
```

(`typer.BadParameter` is a Click exception rather than a `ValueError`, so the refusals above pass through the final `except` unchanged.)

- [ ] **Step 5: Run the CLI tests and the whole pruning file**

Run: `uv run ruff check --fix src/mnemo_memory/apps/cli/main.py tests/unit/test_source_snapshot_pruning.py && uv run ruff format src/mnemo_memory/apps/cli/main.py tests/unit/test_source_snapshot_pruning.py && uv run pytest tests/unit/test_source_snapshot_pruning.py -q`
Expected: 17 passed.

Run: `uv run mypy src/mnemo_memory/apps/cli/main.py tests/unit/test_source_snapshot_pruning.py`
Expected: `Success: no issues found`.

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(cli): maintenance prune-source with dry run and safe compaction" -m "$CO_AUTHOR" -- src/mnemo_memory/apps/cli/main.py tests/unit/test_source_snapshot_pruning.py
```

---

### Task 5: User-guide documentation

**Files:**
- Modify: `docs/user-guide.md` (lines 260-273, in "Does Mnemo remember an entire codebase?", before `### Finding a structural starting point`)

**Interfaces:**
- Consumes: the command, error codes, notice text and environment variable from Tasks 3-4.
- Produces: user-facing documentation only.

- [ ] **Step 1: Replace the session-start and in-session refresh paragraphs**

Replace:

```markdown
At session start, Mnemo has just refreshed the snapshot it attaches, so that attached source map
is explicitly labeled **current** for the captured digest. Later manual requests still require
comparable source-state evidence before Mnemo calls a saved snapshot current; active alone is not
enough.

During an active session, Mnemo does not scan after every edit. It marks project work as changed,
then refreshes once at the next user-turn boundary and attaches the same small file/impact cue.
That gives the agent relevant structural facts while it is analyzing the next request without
capturing the user’s prompt or source body.

For an exact changed file in a supported parsed language, that fresh-session cue also includes a
small list of proven **static dependent candidates** and the immutable source snapshot that proves
them. This is a practical “what might be affected?” starting point, not a runtime promise: the
agent should still inspect the cited structure and run the appropriate verification.
```

with (the outer fence below has four backticks because the text contains a code block):

````markdown
Mnemo never rebuilds the code map inside a client hook, so a large repository cannot make a
session wait or time out. At each lifecycle boundary the hook only compares file sizes and
modification times with the last saved map, which is cheap. When nothing changed, the saved map is
used as before and its digest is offered as **current**. When something changed, the hook answers
at once with the last saved map (and does not call it current). It then starts a quiet background
refresh that re-parses, saves and activates the new map. Agent reminders then add one fixed line:
“Code map refresh running in background; structural change details may lag one prompt.” Later
manual requests still require comparable source-state evidence before Mnemo calls a saved
snapshot current; active alone is not enough.

Hook reminders no longer include a per-change summary. To see what changed and which code
statically depends on it, ask for `source_changes` (optionally with one `relative_path`). Each
recorded transition still lists exact changed files, declarations and a small set of proven
**static dependent candidates** with the immutable snapshot that proves them. This is a
practical “what might be affected?” starting point, not a runtime promise: the agent should
still inspect the cited structure and run the appropriate verification.

#### Keeping the saved code map small

Each background refresh also deletes a few old snapshots (at most four per run). It always keeps
the active map and every map from the 17 most recent refreshes, which is everything
`source_changes` can ask for. A map that a saved checkpoint points to keeps a one-line record
(its digest and counts) so that checkpoint still resolves, but its detailed rows are removed.
Asking for a removed snapshot by its id answers “source snapshot was not found”.

A database that grew before this cleanup existed can be shrunk once, by hand:

```bash
mnemo-memory maintenance prune-source --dry-run      # counts only; changes nothing
mnemo-memory maintenance prune-source                # this project, no per-run limit
mnemo-memory maintenance prune-source --all-projects --compact
```

`--compact` then rebuilds the database file (SQLite `VACUUM`) so the freed space returns to the
disk. It refuses when the free disk space is smaller than the database file, and nothing is
changed in that case. The command prints the file size before and after. It also refuses
(`MNEMO_SOURCE_REFRESH_RUNNING`) while a background refresh is running; run it again a moment
later. Close other Mnemo sessions before `--compact`, because a busy database cannot be rebuilt
(`MNEMO_COMPACT_UNAVAILABLE`).

To turn the background refresh off, set `MNEMO_DISABLE_BACKGROUND_SOURCE_REFRESH=1` in the
environment of your coding client. The map is then refreshed only by an explicit checkpoint save.
````

- [ ] **Step 2: Check the command help and the notice text agree with the guide**

Run: `uv run mnemo-memory maintenance prune-source --help`
Expected: the help lists `--project-root`, `--all-projects`, `--compact`, `--dry-run` and `--data-dir`.

Run: `grep -c "Code map refresh running in background; structural change details may lag one prompt." docs/user-guide.md src/mnemo_memory/connectors/automatic_memory/hook.py`
Expected: `docs/user-guide.md:1` and `src/mnemo_memory/connectors/automatic_memory/hook.py:1`.

- [ ] **Step 3: Commit**

```bash
git commit -m "docs: background code-map refresh and source snapshot cleanup" -m "$CO_AUTHOR" -- docs/user-guide.md
```

---

### Task 6: Full verification

**Files:** none (verification only; formatting fixes, if any, go in their own commit).

**Interfaces:**
- Consumes: everything above.
- Produces: a green `npm run check`.

- [ ] **Step 1: Run the full check**

Run: `npm run check`
Expected: every stage passes, in order: format:check, lint, typecheck, test, postgres:check, schema:check, dependencies:check, architecture:check and package:check.

- [ ] **Step 2: If `format:check` or `lint` fails, fix and re-run**

Run: `npm run format && uv run ruff check --fix . && npm run check`
Expected: green. Commit only the files you changed:

```bash
git commit -m "style: format background source refresh changes" -m "$CO_AUTHOR" -- $(git diff --name-only -- src tests scripts docs)
```

If `postgres:check` cannot run because no PostgreSQL is available on this machine, report that fact with its exact output. Do not skip or edit the check.

- [ ] **Step 3: Confirm no real worker was started by the suite and nothing outside the tree changed**

Run: `git status --short`
Expected: no changes outside this plan's files, and `reports/` and `research_notes/` are still untracked and unstaged.
