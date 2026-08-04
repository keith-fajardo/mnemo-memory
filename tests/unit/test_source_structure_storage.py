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
    SourceImpactService,
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
    assert repository.iter_files(scope(), original.snapshot.snapshot_id) == original.files
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
    assert reopened.iter_files(scope(), parsed.snapshot.snapshot_id) == parsed.files
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
    # fabricating source history. Remove later migration state as well: a test
    # database cannot claim v6 while its v5 schema has been removed.
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER checkpoint_source_observation_snapshot_scope_match")
        connection.execute("DROP TRIGGER checkpoint_source_observation_checkpoint_scope_match")
        connection.execute("DROP TABLE checkpoint_source_observations")
        connection.execute("DROP TABLE checkpoint_lifecycle_events")
        connection.execute("DROP TABLE approved_episodic_event_evidence")
        connection.execute("DROP TABLE approved_episodic_events")
        connection.execute("DROP TABLE source_structure_files")
        connection.execute("DROP TRIGGER source_snapshot_activation_scope_match")
        connection.execute("DROP TABLE source_snapshot_activations")
        connection.execute("DELETE FROM schema_migrations WHERE version >= 5")

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


def test_file_fingerprint_migration_is_atomic_and_legacy_snapshots_make_no_false_file_claim(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    parsed = artifact(root, scope())
    database = tmp_path / "data" / "mnemo.sqlite3"
    repository = SQLiteSourceStructureRepository(database)
    repository.migrate()
    repository.store_and_activate(parsed)

    # Reproduce an existing v7 database. Its old snapshot has no path/digest rows,
    # so a later comparison must report file-level history as unavailable, not claim
    # every file was newly added.
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER checkpoint_source_observation_snapshot_scope_match")
        connection.execute("DROP TRIGGER checkpoint_source_observation_checkpoint_scope_match")
        connection.execute("DROP TABLE checkpoint_source_observations")
        connection.execute("DROP TABLE source_structure_files")
        connection.execute("DELETE FROM schema_migrations WHERE version >= 8")
    with pytest.raises(SQLiteMigrationError, match="injected migration failure"):
        repository.migrate(fail_after_version=8)
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name = 'source_structure_files'"
            ).fetchone()
            is None
        )
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone() == (7,)

    repository.migrate()
    assert repository.iter_files(scope(), parsed.snapshot.snapshot_id) == ()
    (root / "pkg" / "main.py").write_text("import pkg.dependency\n\ndef run():\n    return 2\n")
    changed = PythonSourceParser().parse(PythonSourceParseRequest(scope(), root.resolve()))
    repository.store_and_activate(changed)
    diff = SourceImpactService(repository).diff(
        scope(), parsed.snapshot.snapshot_id, changed.snapshot.snapshot_id
    )
    assert diff.file_fingerprints_available is False
    assert diff.added_files == ()
    assert diff.removed_files == ()
    assert diff.modified_files == ()
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_sqlite_file_fingerprint_projection_excludes_source_bodies(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    secret_source_body = "private implementation detail must not persist"
    (root / "service.py").write_text(f"def run():\n    return '{secret_source_body}'\n")
    parsed = SourceStructureParser().parse(SourceStructureParseRequest(scope(), root.resolve()))
    database = tmp_path / "data" / "mnemo.sqlite3"
    repository = SQLiteSourceStructureRepository(database)
    repository.migrate()
    repository.store_and_activate(parsed)

    with sqlite3.connect(database) as connection:
        columns = tuple(
            row[1]
            for row in connection.execute("PRAGMA table_info(source_structure_files)").fetchall()
        )
        row = connection.execute(
            "SELECT snapshot_id, relative_path, content_digest FROM source_structure_files"
        ).fetchone()

    assert columns == ("snapshot_id", "relative_path", "content_digest")
    assert row is not None
    assert row[1] == "service.py"
    assert str(row[2]).startswith("sha256:")
    assert secret_source_body not in str(row)


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_file_only_sql_and_unparsed_source_files_are_durable_change_evidence(
    tmp_path: Path, adapter: str
) -> None:
    root = tmp_path / "dbt and swift source"
    models = root / "models"
    models.mkdir(parents=True)
    seeds = root / "seeds"
    seeds.mkdir()
    sql = models / "orders.sql"
    schema = models / "schema.yml"
    finance_seed = seeds / "finance_orders.csv"
    swift = root / "Application.swift"
    stylesheet = root / "web" / "application.css"
    interface = root / "api" / "schema.graphql"
    configuration = root / "tooling.toml"
    package_manifest = root / "package.json"
    lockfile = root / "uv.lock"
    containerfile = root / "Dockerfile"
    integration = root / "config" / "integration.xml"
    stylesheet.parent.mkdir()
    interface.parent.mkdir()
    integration.parent.mkdir()
    secret_body = "private model expression must not persist"
    sql.write_text(f"select '{secret_body}' as value\n", encoding="utf-8")
    schema.write_text("version: 2\nmodels: []\n", encoding="utf-8")
    finance_seed.write_text("order_id,amount\n1,100\n", encoding="utf-8")
    swift.write_text("struct Application {}\n", encoding="utf-8")
    stylesheet.write_text(".button { color: blue; }\n", encoding="utf-8")
    interface.write_text("type Query { orders: [Order!]! }\n", encoding="utf-8")
    configuration.write_text("[tool.mnemo]\nenabled = true\n", encoding="utf-8")
    package_manifest.write_text('{"name":"safe-example"}\n', encoding="utf-8")
    lockfile.write_text("version = 1\n", encoding="utf-8")
    containerfile.write_text("FROM python:3.12-slim\n", encoding="utf-8")
    integration.write_text('<configuration enabled="true" />\n', encoding="utf-8")
    (root / ".env").write_text("PASSWORD=not-indexed\n", encoding="utf-8")
    parser = SourceStructureParser()
    first = parser.parse(SourceStructureParseRequest(scope(), root.resolve()))
    repository = (
        ReferenceSourceStructureRepository()
        if adapter == "reference"
        else SQLiteSourceStructureRepository(tmp_path / "memory" / "mnemo.sqlite3")
    )
    if adapter == "sqlite":
        repository.migrate()  # type: ignore[union-attr]
    repository.store_and_activate(first)

    assert {
        ".csv",
        ".css",
        ".graphql",
        ".json",
        ".lock",
        ".sql",
        ".toml",
        ".tsv",
        ".xml",
        ".yaml",
        ".yml",
    }.issubset(parser.file_only_suffixes)
    assert {"containerfile", "dockerfile", "makefile"}.issubset(parser.file_only_filenames)
    assert first.snapshot.symbol_count == 0
    assert first.snapshot.edge_count == 0
    assert [item.relative_path for item in first.files] == [
        "Application.swift",
        "Dockerfile",
        "api/schema.graphql",
        "config/integration.xml",
        "models/orders.sql",
        "models/schema.yml",
        "package.json",
        "seeds/finance_orders.csv",
        "tooling.toml",
        "uv.lock",
        "web/application.css",
    ]
    assert secret_body not in repr(first)

    sql.write_text("select 'changed' as value\n", encoding="utf-8")
    schema.write_text("version: 2\nmodels:\n  - name: orders\n", encoding="utf-8")
    finance_seed.write_text("order_id,amount\n1,110\n", encoding="utf-8")
    stylesheet.write_text(".button { color: green; }\n", encoding="utf-8")
    interface.write_text("type Query { orders: [Order!]!, orderCount: Int! }\n", encoding="utf-8")
    configuration.write_text("[tool.mnemo]\nenabled = false\n", encoding="utf-8")
    package_manifest.write_text('{"name":"safe-example","private":true}\n', encoding="utf-8")
    lockfile.write_text("version = 1\nrevision = 2\n", encoding="utf-8")
    containerfile.write_text("FROM python:3.12-alpine\n", encoding="utf-8")
    integration.write_text('<configuration enabled="false" />\n', encoding="utf-8")
    second = parser.parse(SourceStructureParseRequest(scope(), root.resolve()))
    repository.store_and_activate(second)
    diff = SourceImpactService(repository).diff(
        scope(), first.snapshot.snapshot_id, second.snapshot.snapshot_id
    )

    assert [item.relative_path for item in diff.modified_files] == [
        "Dockerfile",
        "api/schema.graphql",
        "config/integration.xml",
        "models/orders.sql",
        "models/schema.yml",
        "package.json",
        "seeds/finance_orders.csv",
        "tooling.toml",
        "uv.lock",
        "web/application.css",
    ]
    assert [item.relative_path for item in diff.added_files] == []
    assert [item.relative_path for item in diff.removed_files] == []
    if adapter == "sqlite":
        with sqlite3.connect(tmp_path / "memory" / "mnemo.sqlite3") as connection:
            stored = str(connection.execute("SELECT * FROM source_structure_files").fetchall())
        assert secret_body not in stored


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_exact_scoped_file_projection_is_available_without_parsing_source(
    tmp_path: Path, adapter: str
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    package_manifest = root / "package.json"
    package_manifest.write_text('{"private":"not-retained"}\n', encoding="utf-8")
    artifact = SourceStructureParser().parse(SourceStructureParseRequest(scope(), root.resolve()))
    repository = (
        ReferenceSourceStructureRepository()
        if adapter == "reference"
        else SQLiteSourceStructureRepository(tmp_path / "memory" / "mnemo.sqlite3")
    )
    if adapter == "sqlite":
        repository.migrate()  # type: ignore[union-attr]
    stored = repository.store_and_activate(artifact)

    file = repository.get_file(scope(), stored.snapshot.snapshot_id, "package.json")

    assert file is not None
    assert file.relative_path == "package.json"
    assert file.content_digest.startswith("sha256:")
    assert repository.get_file(scope(), stored.snapshot.snapshot_id, "missing.json") is None
    assert "not-retained" not in repr(file)
    with pytest.raises(SourceSnapshotNotFound):
        repository.get_file(
            scope("44444444-4444-4444-8444-444444444444"),
            stored.snapshot.snapshot_id,
            "package.json",
        )


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
