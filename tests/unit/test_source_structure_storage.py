from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mnemo_memory.packages.domain import (
    CodeStructureArtifact,
    CodeSymbolKind,
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.project_index import (
    PythonSourceParser,
    PythonSourceParseRequest,
    SourceStructureParser,
    SourceStructureParseRequest,
)
from mnemo_memory.packages.storage import (
    ReferenceSourceStructureRepository,
    SQLiteMigrationError,
    SQLiteSourceStructureRepository,
)
from mnemo_memory.packages.storage.contracts import SourceSnapshotNotFound


def scope(project: str = "33333333-3333-4333-8333-333333333333") -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string("11111111-1111-4111-8111-111111111111"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string("22222222-2222-4222-8222-222222222222"),
        ProjectId.from_string(project),
    )


def artifact(root: Path, item_scope: MemoryScope) -> CodeStructureArtifact:
    (root / "pkg").mkdir(parents=True, exist_ok=True)
    (root / "pkg" / "main.py").write_text("import pkg.dependency\n\ndef run():\n    return 1\n")
    return PythonSourceParser().parse(PythonSourceParseRequest(item_scope, root.resolve()))


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_source_snapshot_is_immutable_scoped_and_idempotent(tmp_path: Path, adapter: str) -> None:
    root = tmp_path / "source"
    original = artifact(root, scope())
    repository = (
        ReferenceSourceStructureRepository()
        if adapter == "reference"
        else SQLiteSourceStructureRepository(tmp_path / "memory" / "mnemo.sqlite3")
    )
    if adapter == "sqlite":
        repository.migrate()  # type: ignore[union-attr]

    first = repository.store_and_activate(original)
    second = repository.store_and_activate(original)
    active = repository.get_active_snapshot(scope())

    assert first.idempotent is False
    assert second.idempotent is True
    assert active == original.snapshot
    assert repository.iter_symbols(scope(), original.snapshot.snapshot_id) == original.symbols
    assert repository.iter_edges(scope(), original.snapshot.snapshot_id) == original.edges
    with pytest.raises(SourceSnapshotNotFound):
        repository.get_snapshot(
            scope("44444444-4444-4444-8444-444444444444"), original.snapshot.snapshot_id
        )
    with pytest.raises(SourceSnapshotNotFound):
        repository.find_symbols(
            scope("44444444-4444-4444-8444-444444444444"),
            original.snapshot.snapshot_id,
            "run",
            limit=10,
        )


def test_sqlite_source_snapshot_survives_reopen(tmp_path: Path) -> None:
    root = tmp_path / "source"
    parsed = artifact(root, scope())
    database = tmp_path / "data dir" / "mnemo.sqlite3"
    repository = SQLiteSourceStructureRepository(database)
    repository.migrate()
    repository.store_and_activate(parsed)

    reopened = SQLiteSourceStructureRepository(database)
    assert reopened.get_active_snapshot(scope()) == parsed.snapshot
    assert reopened.iter_symbols(scope(), parsed.snapshot.snapshot_id) == parsed.symbols
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_source_activation_migration_seeds_only_known_active_snapshot_and_rolls_back(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    parsed = artifact(root, scope())
    database = tmp_path / "data" / "mnemo.sqlite3"
    repository = SQLiteSourceStructureRepository(database)
    repository.migrate()
    repository.store_and_activate(parsed)

    # Recreate the durable state immediately before migration 0005 without
    # fabricating source history. A v4 profile knows the current snapshot only.
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER source_snapshot_activation_scope_match")
        connection.execute("DROP TABLE source_snapshot_activations")
        connection.execute("DELETE FROM schema_migrations WHERE version = 5")

    with pytest.raises(SQLiteMigrationError, match="injected migration failure"):
        repository.migrate(fail_after_version=5)
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name = 'source_snapshot_activations'"
            ).fetchone()
            is None
        )
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone() == (4,)

    repository.migrate()
    assert repository.latest_transition(scope()) is None
    assert repository.list_activation_history(scope()) == (parsed.snapshot,)
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_source_snapshot_activation_history_is_scoped_and_uses_explicit_order(
    tmp_path: Path, adapter: str
) -> None:
    """History is activation order, never lexical/UUID order, and remains scoped."""
    item_scope = scope()
    root = tmp_path / "source memory"
    first = artifact(root, item_scope)
    repository = (
        ReferenceSourceStructureRepository()
        if adapter == "reference"
        else SQLiteSourceStructureRepository(tmp_path / "memory" / "mnemo.sqlite3")
    )
    if adapter == "sqlite":
        repository.migrate()  # type: ignore[union-attr]

    repository.store_and_activate(first)
    assert repository.latest_transition(item_scope) is None

    (root / "pkg" / "main.py").write_text(
        "import pkg.dependency\n\ndef run():\n    return dependency.execute()\n"
    )
    second = PythonSourceParser().parse(PythonSourceParseRequest(item_scope, root.resolve()))
    repository.store_and_activate(second)
    transition = repository.latest_transition(item_scope)

    assert transition == (first.snapshot, second.snapshot)
    assert repository.latest_transition(scope("44444444-4444-4444-8444-444444444444")) is None

    # Reactivating an existing immutable artifact is a genuine explicit transition.
    restored = repository.store_and_activate(first)
    assert restored.idempotent is True
    assert repository.latest_transition(item_scope) == (second.snapshot, first.snapshot)
    assert repository.list_activation_history(item_scope) == (
        first.snapshot,
        second.snapshot,
        first.snapshot,
    )
    assert repository.list_activation_history(item_scope, limit=2) == (
        first.snapshot,
        second.snapshot,
    )
    with pytest.raises(ValueError, match="history limit"):
        repository.list_activation_history(item_scope, limit=0)
    assert repository.list_activation_history(scope("44444444-4444-4444-8444-444444444444")) == ()
    assert repository.iter_symbols(item_scope, second.snapshot.snapshot_id) == second.symbols


def test_sqlite_persists_mixed_language_static_symbols_and_edges(tmp_path: Path) -> None:
    root = tmp_path / "polyglot"
    root.mkdir()
    (root / "web.ts").write_text(
        "import { helper } from './tools';\nexport function run() { helper() }\n"
    )
    (root / "worker.go").write_text("package worker\nfunc Run() { helper() }\n")
    parsed = SourceStructureParser().parse(SourceStructureParseRequest(scope(), root.resolve()))
    database = tmp_path / "data" / "mnemo.sqlite3"
    repository = SQLiteSourceStructureRepository(database)
    repository.migrate()
    repository.store_and_activate(parsed)

    reopened = SQLiteSourceStructureRepository(database)
    symbols = reopened.iter_symbols(scope(), parsed.snapshot.snapshot_id)
    edges = reopened.iter_edges(scope(), parsed.snapshot.snapshot_id)

    assert {item.qualified_name for item in symbols} >= {
        "web",
        "web.run",
        "worker",
        "worker.Run",
    }
    assert {item.target for item in edges} >= {"./tools", "helper"}


def test_sqlite_preserves_resolved_internal_import_targets(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "main.js").write_text("import { helper } from './helper'; function run() { helper() }")
    (root / "helper.js").write_text("export function helper() {}")
    parsed = SourceStructureParser().parse(SourceStructureParseRequest(scope(), root.resolve()))
    database = tmp_path / "memory" / "mnemo.sqlite3"
    repository = SQLiteSourceStructureRepository(database)
    repository.migrate()
    repository.store_and_activate(parsed)

    reopened = SQLiteSourceStructureRepository(database)
    edges = reopened.iter_edges(scope(), parsed.snapshot.snapshot_id)
    expected = next(edge for edge in parsed.edges if edge.target == "./helper")
    actual = next(edge for edge in edges if edge.target == "./helper")

    assert actual.target_symbol_id == expected.target_symbol_id


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_source_repository_finds_symbols_and_bounded_adjacency(
    tmp_path: Path, adapter: str
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "orders.ts").write_text(
        "import { total } from './pricing'; export function calculateOrder() { total() }"
    )
    (root / "pricing.ts").write_text("export function total() { return 1 }")
    parsed = SourceStructureParser().parse(SourceStructureParseRequest(scope(), root.resolve()))
    repository = (
        ReferenceSourceStructureRepository()
        if adapter == "reference"
        else SQLiteSourceStructureRepository(tmp_path / "memory" / "mnemo.sqlite3")
    )
    if adapter == "sqlite":
        repository.migrate()  # type: ignore[union-attr]
    repository.store_and_activate(parsed)

    symbols = repository.find_symbols(scope(), parsed.snapshot.snapshot_id, "order", limit=10)
    modules = repository.module_symbols_for_paths(
        scope(), parsed.snapshot.snapshot_id, tuple(symbol.relative_path for symbol in symbols)
    )
    edges = repository.edges_from_symbols(
        scope(),
        parsed.snapshot.snapshot_id,
        tuple(symbol.symbol_id for symbol in (*symbols, *modules)),
    )

    assert any(symbol.kind is CodeSymbolKind.FUNCTION for symbol in symbols)
    assert {module.qualified_name for module in modules} == {"orders"}
    assert {edge.target for edge in edges} >= {"./pricing", "total"}
    resolved = repository.symbols_by_ids(
        scope(),
        parsed.snapshot.snapshot_id,
        tuple(edge.target_symbol_id for edge in edges if edge.target_symbol_id is not None),
    )
    # The import edge resolves the module and the explicit imported call resolves the local
    # function. Both are in-snapshot structural facts, not guessed runtime relationships.
    assert {symbol.qualified_name for symbol in resolved} == {"pricing", "pricing.total"}
