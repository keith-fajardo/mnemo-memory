from __future__ import annotations

from pathlib import Path

import pytest

from mnemo_memory.packages.domain import (
    CodeStructureArtifact,
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.project_index import (
    SourceImpactDirection,
    SourceImpactQuery,
    SourceImpactService,
    SourceStructureParser,
    SourceStructureParseRequest,
)
from mnemo_memory.packages.storage import (
    ReferenceSourceStructureRepository,
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


def graph(root: Path, item_scope: MemoryScope) -> CodeStructureArtifact:
    root.mkdir()
    (root / "core.py").write_text("def calculate():\n    return 1\n")
    (root / "service.py").write_text("import core\n\ndef serve():\n    return core.calculate()\n")
    (root / "app.py").write_text("import service\n\ndef run():\n    return service.serve()\n")
    return SourceStructureParser().parse(SourceStructureParseRequest(item_scope, root))


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_deterministic_transitive_dependents_are_scoped_and_evidenced(
    tmp_path: Path, adapter: str
) -> None:
    item_scope = scope()
    artifact = graph(tmp_path / "source", item_scope)
    repository = (
        ReferenceSourceStructureRepository()
        if adapter == "reference"
        else SQLiteSourceStructureRepository(tmp_path / "data" / "mnemo.sqlite3")
    )
    if adapter == "sqlite":
        repository.migrate()  # type: ignore[union-attr]
    repository.store_and_activate(artifact)

    result = SourceImpactService(repository).query(
        SourceImpactQuery(item_scope, "core", SourceImpactDirection.DEPENDENTS)
    )

    assert [item.symbol.qualified_name for item in result.symbols] == ["service", "app"]
    assert [item.depth for item in result.symbols] == [1, 2]
    assert [edge.target for edge in result.edges] == ["core", "service"]
    assert result.truncated is False


def test_dependencies_have_shortest_depth_and_explicit_limits(tmp_path: Path) -> None:
    item_scope = scope()
    artifact = graph(tmp_path / "source", item_scope)
    repository = ReferenceSourceStructureRepository()
    repository.store_and_activate(artifact)
    service = SourceImpactService(repository)

    direct = service.query(
        SourceImpactQuery(
            item_scope,
            "app",
            SourceImpactDirection.DEPENDENCIES,
            transitive=False,
        )
    )
    limited = service.query(
        SourceImpactQuery(
            item_scope,
            "core",
            SourceImpactDirection.DEPENDENTS,
            maximum_depth=1,
        )
    )

    assert [(item.symbol.qualified_name, item.depth) for item in direct.symbols] == [("service", 1)]
    assert [(item.symbol.qualified_name, item.depth) for item in limited.symbols] == [
        ("service", 1)
    ]
    assert limited.truncated is True
    assert limited.truncation_reason == "maximum depth reached"


def test_snapshot_diff_preserves_immutable_history(tmp_path: Path) -> None:
    item_scope = scope()
    root = tmp_path / "source"
    first = graph(root, item_scope)
    repository = ReferenceSourceStructureRepository()
    repository.store_and_activate(first)
    (root / "worker.py").write_text("import core\n\ndef execute():\n    return core.calculate()\n")
    second = SourceStructureParser().parse(SourceStructureParseRequest(item_scope, root))
    repository.store_and_activate(second)

    diff = SourceImpactService(repository).diff(
        item_scope, first.snapshot.snapshot_id, second.snapshot.snapshot_id
    )

    assert diff.before == first.snapshot
    assert diff.after == second.snapshot
    assert [item.qualified_name for item in diff.added_symbols] == ["worker", "worker.execute"]
    assert diff.removed_symbols == ()
    assert repository.iter_symbols(item_scope, first.snapshot.snapshot_id) == first.symbols


def test_cross_scope_source_impact_does_not_disclose_snapshot(tmp_path: Path) -> None:
    artifact = graph(tmp_path / "source", scope())
    repository = ReferenceSourceStructureRepository()
    repository.store_and_activate(artifact)

    with pytest.raises(SourceSnapshotNotFound, match="source snapshot was not found"):
        SourceImpactService(repository).query(
            SourceImpactQuery(scope("44444444-4444-4444-8444-444444444444"), "core")
        )


def test_unambiguous_static_calls_participate_in_impact_but_dynamic_calls_do_not(
    tmp_path: Path,
) -> None:
    item_scope = scope()
    root = tmp_path / "source"
    root.mkdir()
    (root / "helpers.py").write_text("def validate():\n    return True\n")
    (root / "service.py").write_text(
        "from helpers import validate\n\n"
        "def process():\n    return validate()\n\n"
        "def dynamic(fn):\n    return fn()\n"
    )
    artifact = SourceStructureParser().parse(SourceStructureParseRequest(item_scope, root))
    repository = ReferenceSourceStructureRepository()
    repository.store_and_activate(artifact)

    result = SourceImpactService(repository).query(
        SourceImpactQuery(item_scope, "helpers.validate", SourceImpactDirection.DEPENDENTS)
    )
    calls = [edge for edge in artifact.edges if edge.kind.value == "calls"]

    assert [item.symbol.qualified_name for item in result.symbols] == ["service.process"]
    assert any(edge.target == "validate" and edge.target_symbol_id is not None for edge in calls)
    assert any(edge.target == "fn" and edge.target_symbol_id is None for edge in calls)


def test_python_import_alias_is_resolved_only_when_the_internal_target_is_unambiguous(
    tmp_path: Path,
) -> None:
    item_scope = scope()
    root = tmp_path / "source"
    root.mkdir()
    (root / "helpers.py").write_text("def validate():\n    return True\n")
    (root / "service.py").write_text(
        "import helpers as local_helpers\n\ndef process():\n    return local_helpers.validate()\n"
    )
    artifact = SourceStructureParser().parse(SourceStructureParseRequest(item_scope, root))
    repository = ReferenceSourceStructureRepository()
    repository.store_and_activate(artifact)

    result = SourceImpactService(repository).query(
        SourceImpactQuery(item_scope, "helpers.validate", SourceImpactDirection.DEPENDENTS)
    )

    assert [item.symbol.qualified_name for item in result.symbols] == ["service.process"]


def test_typescript_named_and_namespace_imports_resolve_safe_internal_calls(tmp_path: Path) -> None:
    item_scope = scope()
    root = tmp_path / "source"
    root.mkdir()
    (root / "helpers.ts").write_text(
        "export function validate() { return true }\nexport function other() { return true }\n"
    )
    (root / "service.ts").write_text(
        "import { validate as check } from './helpers';\n"
        "import * as helpers from './helpers';\n"
        "export function process() { check(); helpers.other(); }\n"
    )
    artifact = SourceStructureParser().parse(SourceStructureParseRequest(item_scope, root))
    repository = ReferenceSourceStructureRepository()
    repository.store_and_activate(artifact)
    service = SourceImpactService(repository)

    validation = service.query(
        SourceImpactQuery(item_scope, "helpers.validate", SourceImpactDirection.DEPENDENTS)
    )
    other = service.query(
        SourceImpactQuery(item_scope, "helpers.other", SourceImpactDirection.DEPENDENTS)
    )

    assert [item.symbol.qualified_name for item in validation.symbols] == ["service.process"]
    assert [item.symbol.qualified_name for item in other.symbols] == ["service.process"]


def test_go_import_aliases_resolve_only_unique_local_package_calls(tmp_path: Path) -> None:
    item_scope = scope()
    root = tmp_path / "source"
    (root / "internal" / "orders").mkdir(parents=True)
    (root / "internal" / "orders" / "orders.go").write_text("package orders\nfunc Process() {}\n")
    (root / "service").mkdir()
    (root / "service" / "service.go").write_text(
        "package service\n"
        'import local_orders "example.com/demo/internal/orders"\n'
        "func Run() { local_orders.Process() }\n"
    )
    artifact = SourceStructureParser().parse(SourceStructureParseRequest(item_scope, root))
    repository = ReferenceSourceStructureRepository()
    repository.store_and_activate(artifact)

    result = SourceImpactService(repository).query(
        SourceImpactQuery(
            item_scope,
            "internal.orders.orders.Process",
            SourceImpactDirection.DEPENDENTS,
        )
    )

    assert [item.symbol.qualified_name for item in result.symbols] == ["service.service.Run"]
    calls = [edge for edge in artifact.edges if edge.kind.value == "calls"]
    assert any(
        edge.target == "local_orders.Process" and edge.target_symbol_id is not None
        for edge in calls
    )


def test_go_package_calls_remain_unresolved_when_the_local_member_is_ambiguous(
    tmp_path: Path,
) -> None:
    item_scope = scope()
    root = tmp_path / "source"
    (root / "internal" / "orders").mkdir(parents=True)
    (root / "internal" / "orders" / "first.go").write_text("package orders\nfunc Process() {}\n")
    (root / "internal" / "orders" / "second.go").write_text("package orders\nfunc Process() {}\n")
    (root / "service").mkdir()
    (root / "service" / "service.go").write_text(
        "package service\n"
        'import "example.com/demo/internal/orders"\n'
        "func Run() { orders.Process() }\n"
    )

    artifact = SourceStructureParser().parse(SourceStructureParseRequest(item_scope, root))
    calls = [edge for edge in artifact.edges if edge.kind.value == "calls"]

    assert any(edge.target == "orders.Process" and edge.target_symbol_id is None for edge in calls)
