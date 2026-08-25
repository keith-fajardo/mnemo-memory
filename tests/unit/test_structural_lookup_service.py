from __future__ import annotations

from pathlib import Path

from mnemo_memory.packages.application.structural_lookup import StructuralLookupService
from mnemo_memory.packages.domain import (
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


def _repo_with(root: Path, db: Path) -> SQLiteSourceStructureRepository:
    artifact = SourceStructureParser().parse(
        SourceStructureParseRequest(project_scope(), root.resolve())
    )
    repo = SQLiteSourceStructureRepository(db)
    repo.migrate()
    repo.store_and_activate(artifact)
    return repo


def test_define_locates_symbol(tmp_path: Path) -> None:
    root = tmp_path / "p"
    root.mkdir()
    (root / "m.py").write_text("def target():\n    return 1\n")
    repo = _repo_with(root, tmp_path / "mem" / "mnemo.sqlite3")
    service = StructuralLookupService(repo)

    result = service.lookup(project_scope(), kind="define", target="target")

    assert result.kind == "define"
    assert result.snapshot_id is not None
    assert any(
        h.qualified_name.endswith("target") and h.relative_path == "m.py" for h in result.hits
    )


def test_lookup_on_empty_index_returns_empty(tmp_path: Path) -> None:
    repo = SQLiteSourceStructureRepository(tmp_path / "mem" / "mnemo.sqlite3")
    repo.migrate()
    service = StructuralLookupService(repo)
    result = service.lookup(project_scope(), kind="define", target="anything")
    assert result.hits == ()
    assert result.snapshot_id is None


def test_callers_are_isolated_from_importers(tmp_path: Path) -> None:
    """An IMPORTS edge and a CALLS edge that share the SAME literal target string
    must not leak into each other's results. `import shared` produces an IMPORTS
    edge from the module symbol with target "shared"; `caller()`'s `shared()` call
    produces a CALLS edge with the identical target string "shared". Only the
    `edge.kind is not wanted` filter separates them — deleting that filter would
    make `callers` also return the module (the importer) and `imports` also return
    `caller` (the caller), so this fails if the kind filter is removed.
    """
    root = tmp_path / "p"
    root.mkdir()
    (root / "m.py").write_text("import shared\n\ndef caller():\n    return shared()\n")
    repo = _repo_with(root, tmp_path / "mem" / "mnemo.sqlite3")
    service = StructuralLookupService(repo)

    callers = service.lookup(project_scope(), kind="callers", target="shared")
    caller_names = {h.qualified_name.rsplit(".", 1)[-1] for h in callers.hits}
    assert caller_names == {"caller"}, "callers must exclude the IMPORTS edge to 'shared'"

    importers = service.lookup(project_scope(), kind="imports", target="shared")
    importer_names = {h.qualified_name.rsplit(".", 1)[-1] for h in importers.hits}
    assert importer_names == {"m"}, "imports must exclude the CALLS edge to 'shared'"


def test_imports_finds_importers(tmp_path: Path) -> None:
    root = tmp_path / "p"
    root.mkdir()
    (root / "m.py").write_text(
        "import os\n\ndef f():\n    return os.getpid()\n\ndef caller():\n    return os()\n"
    )
    repo = _repo_with(root, tmp_path / "mem" / "mnemo.sqlite3")
    service = StructuralLookupService(repo)
    result = service.lookup(project_scope(), kind="imports", target="os")
    names = {h.qualified_name.rsplit(".", 1)[-1] for h in result.hits}
    assert "m" in names, "expected the importing module symbol"
    # caller() has a CALLS edge whose target string is also "os" — it must not
    # leak into an `imports` result; only the kind filter excludes it.
    assert "caller" not in names, "a CALLS edge to 'os' must not count as an import"


def test_contains_lists_file_symbols(tmp_path: Path) -> None:
    root = tmp_path / "p"
    root.mkdir()
    (root / "m.py").write_text("def a():\n    return 1\n\ndef b():\n    return 2\n")
    repo = _repo_with(root, tmp_path / "mem" / "mnemo.sqlite3")
    service = StructuralLookupService(repo)
    result = service.lookup(project_scope(), kind="contains", target="m.py")
    names = {h.qualified_name.rsplit(".", 1)[-1] for h in result.hits}
    assert {"a", "b"} <= names
    assert all(h.relative_path == "m.py" for h in result.hits)
