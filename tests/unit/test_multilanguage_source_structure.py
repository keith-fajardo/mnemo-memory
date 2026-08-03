"""Regression coverage for offline multi-language static source memory."""

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
    SourceStructureError,
    SourceStructureParser,
    SourceStructureParseRequest,
)


def scope() -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string("11111111-1111-4111-8111-111111111111"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string("22222222-2222-4222-8222-222222222222"),
        ProjectId.from_string("33333333-3333-4333-8333-333333333333"),
    )


def test_multi_language_parser_builds_one_deterministic_static_snapshot(tmp_path: Path) -> None:
    root = tmp_path / "polyglot Δ"
    root.mkdir()
    (root / "web.tsx").write_text(
        "import { helper } from './tools';\n"
        "export class Service { run(): void { helper(); client.call() } }\n"
    )
    (root / "worker.go").write_text(
        'package worker\nimport "example.com/tools"\n'
        "type Service struct{}\nfunc (s Service) Run() { helper(); client.Call() }\n"
    )
    (root / "engine.rs").write_text(
        "use crate::tools::helper;\n"
        "struct Service;\n"
        "impl Service { fn run(&self) { helper(); client.call(); } }\n"
    )

    parser = SourceStructureParser()
    first = parser.parse(SourceStructureParseRequest(scope(), root.resolve()))
    second = parser.parse(SourceStructureParseRequest(scope(), root.resolve()))
    names = {(symbol.relative_path, symbol.qualified_name, symbol.kind) for symbol in first.symbols}
    owners = {symbol.symbol_id: symbol.qualified_name for symbol in first.symbols}
    edges = {(owners[edge.source_symbol_id], edge.kind, edge.target) for edge in first.edges}

    assert parser.supported_languages == (
        "c",
        "cpp",
        "csharp",
        "go",
        "java",
        "javascript",
        "php",
        "python",
        "rust",
        "tsx",
        "typescript",
    )
    assert first == second
    assert names >= {
        ("web.tsx", "web.Service", CodeSymbolKind.CLASS),
        ("web.tsx", "web.Service.run", CodeSymbolKind.FUNCTION),
        ("worker.go", "worker.Service", CodeSymbolKind.STRUCT),
        ("worker.go", "worker.Run", CodeSymbolKind.FUNCTION),
        ("engine.rs", "engine.Service", CodeSymbolKind.STRUCT),
        ("engine.rs", "engine.run", CodeSymbolKind.FUNCTION),
    }
    assert edges >= {
        ("web", CodeEdgeKind.IMPORTS, "./tools"),
        ("web.Service.run", CodeEdgeKind.CALLS, "helper"),
        ("web.Service.run", CodeEdgeKind.CALLS, "client.call"),
        ("worker", CodeEdgeKind.IMPORTS, "example.com/tools"),
        ("worker.Run", CodeEdgeKind.CALLS, "helper"),
        ("worker.Run", CodeEdgeKind.CALLS, "client.Call"),
        ("engine", CodeEdgeKind.IMPORTS, "crate.tools.helper"),
        ("engine.run", CodeEdgeKind.CALLS, "helper"),
        ("engine.run", CodeEdgeKind.CALLS, "client.call"),
    }
    assert "helper();" not in repr(first)


@pytest.mark.parametrize(
    ("filename", "source", "error"),
    [
        ("broken.js", "function {", "MNEMO_SOURCE_JAVASCRIPT_INVALID"),
        ("broken.go", "func {", "MNEMO_SOURCE_GO_INVALID"),
        ("broken.rs", "fn {", "MNEMO_SOURCE_RUST_INVALID"),
    ],
)
def test_multi_language_parser_rejects_invalid_supported_syntax(
    tmp_path: Path, filename: str, source: str, error: str
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / filename).write_text(source)

    with pytest.raises(SourceStructureError, match=error):
        SourceStructureParser().parse(SourceStructureParseRequest(scope(), root.resolve()))


def test_parser_can_be_restricted_to_a_supported_language(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "supported.js").write_text("function run() { helper() }")
    (root / "ignored.rs").write_text("fn ignored() {}")

    artifact = SourceStructureParser(languages=frozenset({"javascript"})).parse(
        SourceStructureParseRequest(scope(), root.resolve())
    )

    assert artifact.snapshot.file_count == 1
    assert {item.relative_path for item in artifact.symbols} == {"supported.js"}


def test_parser_resolves_only_unambiguous_internal_imports(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "orders.ts").write_text(
        "import { total } from './pricing'; export function run() { total() }"
    )
    (root / "pricing.ts").write_text("export function total() { return 1 }")
    (root / "external.go").write_text(
        'package external\nimport "example.com/external"\nfunc Run() {}'
    )

    artifact = SourceStructureParser().parse(SourceStructureParseRequest(scope(), root.resolve()))
    modules = {
        symbol.qualified_name: symbol.symbol_id
        for symbol in artifact.symbols
        if symbol.kind is CodeSymbolKind.MODULE
    }
    imports = {edge.target: edge for edge in artifact.edges if edge.kind is CodeEdgeKind.IMPORTS}

    assert imports["./pricing"].target_symbol_id == modules["pricing"]
    assert imports["example.com/external"].target_symbol_id is None


def test_parser_extracts_static_structure_from_common_enterprise_languages(tmp_path: Path) -> None:
    root = tmp_path / "polyglot"
    root.mkdir()
    (root / "native.c").write_text(
        "#include <tools.h>\nstruct Service {}; void run() { helper(); client_call(); }\n"
    )
    (root / "native.cpp").write_text(
        "#include <tools>\nclass Service { void run() { helper(); client.call(); } };\n"
    )
    (root / "Service.cs").write_text(
        "using Tools; class Service { void Run() { Helper(); client.Call(); } }\n"
    )
    (root / "Service.java").write_text(
        "import tools.Helper; class Service { void run() { Helper.go(); client.call(); } }\n"
    )
    (root / "service.php").write_text(
        "<?php namespace App; use Tools\\Helper; class Service { "
        "function run() { helper(); $client->call(); } }\n"
    )

    artifact = SourceStructureParser().parse(SourceStructureParseRequest(scope(), root.resolve()))
    symbols = {(item.relative_path, item.qualified_name, item.kind) for item in artifact.symbols}
    owners = {item.symbol_id: item.qualified_name for item in artifact.symbols}
    edges = {(owners[edge.source_symbol_id], edge.kind, edge.target) for edge in artifact.edges}

    assert symbols >= {
        ("native.c", "native.run", CodeSymbolKind.FUNCTION),
        ("native.cpp", "native.Service.run", CodeSymbolKind.FUNCTION),
        ("Service.cs", "Service.Service.Run", CodeSymbolKind.FUNCTION),
        ("Service.java", "Service.Service.run", CodeSymbolKind.FUNCTION),
        ("service.php", "service.Service.run", CodeSymbolKind.FUNCTION),
    }
    assert edges >= {
        ("native", CodeEdgeKind.IMPORTS, "tools.h"),
        ("native.run", CodeEdgeKind.CALLS, "client_call"),
        ("native.Service.run", CodeEdgeKind.CALLS, "client.call"),
        ("Service", CodeEdgeKind.IMPORTS, "Tools"),
        ("Service.Service.Run", CodeEdgeKind.CALLS, "client.Call"),
        ("Service", CodeEdgeKind.IMPORTS, "tools.Helper"),
        ("Service.Service.run", CodeEdgeKind.CALLS, "Helper.go"),
        ("service", CodeEdgeKind.IMPORTS, "Tools\\Helper"),
        ("service.Service.run", CodeEdgeKind.CALLS, "$client.call"),
    }


def test_java_and_rust_imported_calls_resolve_only_to_local_unambiguous_symbols(
    tmp_path: Path,
) -> None:
    root = tmp_path / "polyglot"
    (root / "tools").mkdir(parents=True)
    (root / "tools" / "Helper.java").write_text(
        "package tools; public class Helper { static void go() {} }\n"
    )
    (root / "Service.java").write_text(
        "import tools.Helper; class Service { void run() { Helper.go(); } }\n"
    )
    (root / "tools.rs").write_text("pub fn helper() {}\n")
    (root / "engine.rs").write_text(
        "use crate::tools::helper; fn run() { helper(); callback(); }\n"
    )

    artifact = SourceStructureParser().parse(SourceStructureParseRequest(scope(), root.resolve()))
    symbols = {item.symbol_id: item.qualified_name for item in artifact.symbols}
    calls = {
        (symbols[item.source_symbol_id], item.target): item.target_symbol_id
        for item in artifact.edges
        if item.kind is CodeEdgeKind.CALLS
    }

    assert calls[("Service.Service.run", "Helper.go")] is not None
    assert calls[("engine.run", "helper")] is not None
    assert calls[("engine.run", "callback")] is None
