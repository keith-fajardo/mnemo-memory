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
    root = tmp_path / "p"
    root.mkdir()
    (root / "m.py").write_text(
        "import os\n\n"
        "def caller():\n    return target()\n\n"
        "def target():\n    return 1\n\n"
        "def bystander():\n    return os.getpid()\n"
    )
    repo = _repo_with(root, tmp_path / "mem" / "mnemo.sqlite3")
    service = StructuralLookupService(repo)

    callers = service.lookup(project_scope(), kind="callers", target="target")
    caller_names = {h.qualified_name.rsplit(".", 1)[-1] for h in callers.hits}
    assert "caller" in caller_names
    assert "bystander" not in caller_names  # importer of os, not a caller of target


def test_imports_finds_importers(tmp_path: Path) -> None:
    root = tmp_path / "p"
    root.mkdir()
    (root / "m.py").write_text("import os\n\ndef f():\n    return os.getpid()\n")
    repo = _repo_with(root, tmp_path / "mem" / "mnemo.sqlite3")
    service = StructuralLookupService(repo)
    result = service.lookup(project_scope(), kind="imports", target="os")
    assert result.hits, "expected at least one importer of os"


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
