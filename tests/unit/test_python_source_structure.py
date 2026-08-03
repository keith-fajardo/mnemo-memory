from __future__ import annotations

from pathlib import Path

import pytest

from mnemo_memory.packages.domain import (
    CodeEdgeKind,
    CodeSymbolKind,
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.project_index import (
    PythonSourceLimits,
    PythonSourceParser,
    PythonSourceParseRequest,
)
from mnemo_memory.packages.project_index.python_ast import PythonSourceStructureError


def scope() -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string("11111111-1111-4111-8111-111111111111"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string("22222222-2222-4222-8222-222222222222"),
        ProjectId.from_string("33333333-3333-4333-8333-333333333333"),
    )


def test_parser_builds_deterministic_static_structure_without_executing_code(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project Δ"
    package = root / "app"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "service.py").write_text(
        "import os\nfrom app.models import Order\n\nclass Service:\n"
        "    async def run(self):\n        return 1\n"
    )
    (root / "ignored.py").symlink_to(package / "service.py")
    parser = PythonSourceParser()
    request = PythonSourceParseRequest(scope(), root.resolve())

    first = parser.parse(request)
    second = parser.parse(request)

    assert first == second
    assert first.snapshot.file_count == 2
    assert [(item.qualified_name, item.kind) for item in first.symbols] == [
        ("app", CodeSymbolKind.MODULE),
        ("app.service", CodeSymbolKind.MODULE),
        ("app.service.Service", CodeSymbolKind.CLASS),
        ("app.service.Service.run", CodeSymbolKind.ASYNC_FUNCTION),
    ]
    assert [(item.kind, item.target) for item in first.edges] == [
        (CodeEdgeKind.IMPORTS, "app.models.Order"),
        (CodeEdgeKind.IMPORTS, "os"),
    ]
    assert "return 1" not in repr(first)


def test_parser_records_only_explicit_syntactic_calls_without_resolving_dispatch(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "service.py").write_text(
        "def outer():\n"
        "    helpers.build()\n"
        "    callback()\n"
        "    (factory())()\n"
        "    def nested():\n"
        "        nested_target.run()\n"
    )

    artifact = PythonSourceParser().parse(PythonSourceParseRequest(scope(), root.resolve()))
    symbols = {item.symbol_id: item.qualified_name for item in artifact.symbols}
    calls = {
        (symbols[edge.source_symbol_id], edge.target)
        for edge in artifact.edges
        if edge.kind is CodeEdgeKind.CALLS
    }

    assert calls == {
        ("service.outer", "helpers.build"),
        ("service.outer", "callback"),
        ("service.outer", "factory"),
        ("service.outer.nested", "nested_target.run"),
    }


def test_parser_resolves_only_explicit_sibling_self_calls(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "service.py").write_text(
        "class Service:\n"
        "    def run(self):\n"
        "        return self.validate()\n\n"
        "    def validate(self):\n"
        "        return True\n\n"
        "def free():\n"
        "    return self.validate()\n"
    )

    artifact = PythonSourceParser().parse(PythonSourceParseRequest(scope(), root.resolve()))
    symbols = {item.symbol_id: item.qualified_name for item in artifact.symbols}
    calls = {
        (symbols[edge.source_symbol_id], edge.target): edge.target_symbol_id
        for edge in artifact.edges
        if edge.kind is CodeEdgeKind.CALLS
    }

    assert calls[("service.Service.run", "self.validate")] is not None
    assert calls[("service.free", "self.validate")] is None


@pytest.mark.parametrize(
    ("content", "limits", "code"),
    [
        ("def broken(:\n", PythonSourceLimits(), "MNEMO_SOURCE_PYTHON_INVALID"),
        ("x = 1\n", PythonSourceLimits(max_files=1), "MNEMO_SOURCE_FILE_LIMIT"),
    ],
)
def test_parser_rejects_invalid_or_over_limit_input(
    tmp_path: Path, content: str, limits: PythonSourceLimits, code: str
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "one.py").write_text(content)
    if code == "MNEMO_SOURCE_FILE_LIMIT":
        (root / "two.py").write_text("x = 2\n")

    with pytest.raises(PythonSourceStructureError, match=code):
        PythonSourceParser().parse(PythonSourceParseRequest(scope(), root.resolve(), limits))
