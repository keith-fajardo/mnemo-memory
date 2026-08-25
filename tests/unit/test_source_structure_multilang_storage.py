from __future__ import annotations

from pathlib import Path

from mnemo_memory.packages.domain import (
    CodeSymbolKind,
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.project_index import (
    SourceStructureParser,
    SourceStructureParseRequest,
)
from mnemo_memory.packages.storage.sqlite import SQLiteSourceStructureRepository


def project_scope() -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string("11111111-1111-4111-8111-111111111111"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string("22222222-2222-4222-8222-222222222222"),
        ProjectId.from_string("33333333-3333-4333-8333-333333333333"),
    )


def test_go_package_symbol_persists(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "go.mod").write_text("module example.com/demo\n\ngo 1.21\n")
    (root / "main.go").write_text('package main\n\nfunc main() {\n\tprintln("hi")\n}\n')
    artifact = SourceStructureParser().parse(
        SourceStructureParseRequest(project_scope(), root.resolve())
    )
    assert any(s.kind is CodeSymbolKind.PACKAGE for s in artifact.symbols), (
        "fixture must exercise the package kind"
    )

    repo = SQLiteSourceStructureRepository(tmp_path / "memory" / "mnemo.sqlite3")
    repo.migrate()
    result = repo.store_and_activate(artifact)  # currently raises/silently fails

    active = repo.get_active_snapshot(project_scope())
    assert active is not None
    assert active.snapshot_id == result.snapshot.snapshot_id
    kinds = {s.kind for s in repo.iter_symbols(project_scope(), active.snapshot_id)}
    assert CodeSymbolKind.PACKAGE in kinds


def test_python_snapshot_survives_multilang_migration(tmp_path: Path) -> None:
    root = tmp_path / "py"
    root.mkdir()
    (root / "m.py").write_text("def a():\n    return b()\n\ndef b():\n    return 1\n")
    artifact = SourceStructureParser().parse(
        SourceStructureParseRequest(project_scope(), root.resolve())
    )
    repo = SQLiteSourceStructureRepository(tmp_path / "memory" / "mnemo.sqlite3")
    repo.migrate()
    repo.store_and_activate(artifact)
    active = repo.get_active_snapshot(project_scope())
    assert active is not None
    names = {s.qualified_name for s in repo.iter_symbols(project_scope(), active.snapshot_id)}
    assert {"a", "b"} <= {n.split(".")[-1] for n in names}
