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


def test_exact_relative_path_starts_from_only_that_file_without_fuzzy_fallback(
    tmp_path: Path,
) -> None:
    item_scope = scope()
    root = tmp_path / "source"
    root.mkdir()
    (root / "core.py").write_text("def calculate():\n    return 1\n")
    (root / "other_core.py").write_text("def calculate():\n    return 2\n")
    (root / "service.py").write_text("import core\n\ndef serve():\n    return core.calculate()\n")
    repository = ReferenceSourceStructureRepository()
    repository.store_and_activate(
        SourceStructureParser().parse(SourceStructureParseRequest(item_scope, root))
    )

    result = SourceImpactService(repository).query(
        SourceImpactQuery(item_scope, None, relative_path="core.py")
    )

    assert [item.relative_path for item in result.start_symbols] == ["core.py", "core.py"]
    assert [item.symbol.qualified_name for item in result.symbols] == ["service", "service.serve"]
    assert {item.symbol.relative_path for item in result.symbols} == {"service.py"}
    with pytest.raises(ValueError, match="exactly one"):
        SourceImpactQuery(item_scope, "core", relative_path="core.py")
    with pytest.raises(ValueError, match="canonical"):
        SourceImpactQuery(item_scope, None, relative_path="../core.py")


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


def test_snapshot_diff_reports_one_unique_content_identical_file_move_as_a_rename(
    tmp_path: Path,
) -> None:
    item_scope = scope()
    root = tmp_path / "source"
    root.mkdir()
    original = root / "legacy.py"
    original.write_text("def calculate():\n    return 1\n")
    repository = ReferenceSourceStructureRepository()
    first = SourceStructureParser().parse(SourceStructureParseRequest(item_scope, root))
    repository.store_and_activate(first)
    original.rename(root / "current.py")
    second = SourceStructureParser().parse(SourceStructureParseRequest(item_scope, root))
    repository.store_and_activate(second)

    diff = SourceImpactService(repository).diff(
        item_scope, first.snapshot.snapshot_id, second.snapshot.snapshot_id
    )

    assert diff.added_files == ()
    assert diff.removed_files == ()
    assert [
        (item.before.relative_path, item.after.relative_path) for item in diff.renamed_files
    ] == [("legacy.py", "current.py")]


def test_snapshot_diff_never_guesses_a_rename_for_duplicated_content(tmp_path: Path) -> None:
    item_scope = scope()
    root = tmp_path / "source"
    root.mkdir()
    (root / "first.py").write_text("def same():\n    return 1\n")
    (root / "second.py").write_text("def same():\n    return 1\n")
    repository = ReferenceSourceStructureRepository()
    first = SourceStructureParser().parse(SourceStructureParseRequest(item_scope, root))
    repository.store_and_activate(first)
    (root / "first.py").rename(root / "renamed.py")
    (root / "second.py").unlink()
    second = SourceStructureParser().parse(SourceStructureParseRequest(item_scope, root))
    repository.store_and_activate(second)

    diff = SourceImpactService(repository).diff(
        item_scope, first.snapshot.snapshot_id, second.snapshot.snapshot_id
    )

    assert diff.renamed_files == ()
    assert [item.relative_path for item in diff.added_files] == ["renamed.py"]
    assert [item.relative_path for item in diff.removed_files] == ["first.py", "second.py"]


@pytest.mark.parametrize("adapter", ("reference", "sqlite"))
def test_snapshot_diff_reports_a_body_only_file_change_without_source_text(
    tmp_path: Path, adapter: str
) -> None:
    """A durable source history must not miss a changed function body."""
    item_scope = scope()
    root = tmp_path / "source"
    root.mkdir()
    path = root / "orders.py"
    path.write_text("def calculate_total():\n    return 1\n")
    first = SourceStructureParser().parse(SourceStructureParseRequest(item_scope, root))
    repository = (
        ReferenceSourceStructureRepository()
        if adapter == "reference"
        else SQLiteSourceStructureRepository(tmp_path / "data" / "mnemo.sqlite3")
    )
    if adapter == "sqlite":
        repository.migrate()  # type: ignore[union-attr]
    repository.store_and_activate(first)

    path.write_text("def calculate_total():\n    return 2\n")
    second = SourceStructureParser().parse(SourceStructureParseRequest(item_scope, root))
    repository.store_and_activate(second)
    diff = SourceImpactService(repository).diff(
        item_scope, first.snapshot.snapshot_id, second.snapshot.snapshot_id
    )

    assert diff.file_fingerprints_available is True
    assert [item.relative_path for item in diff.modified_files] == ["orders.py"]
    assert diff.added_files == ()
    assert diff.removed_files == ()
    assert diff.added_symbols == ()
    assert diff.removed_symbols == ()
    assert "return 1" not in str(diff)
    assert "return 2" not in str(diff)


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


def test_python_relative_imports_resolve_exact_internal_members_and_parent_packages(
    tmp_path: Path,
) -> None:
    """Relative imports are common local links and must not be treated as external text."""
    item_scope = scope()
    root = tmp_path / "source"
    (root / "pkg" / "nested").mkdir(parents=True)
    (root / "pkg" / "helpers.py").write_text("def validate():\n    return True\n")
    (root / "pkg" / "service.py").write_text(
        "from .helpers import validate as check\n\ndef process():\n    return check()\n"
    )
    (root / "pkg" / "nested" / "worker.py").write_text(
        "from ..helpers import validate\n\ndef execute():\n    return validate()\n"
    )
    artifact = SourceStructureParser().parse(SourceStructureParseRequest(item_scope, root))
    repository = ReferenceSourceStructureRepository()
    repository.store_and_activate(artifact)
    service = SourceImpactService(repository)

    result = service.query(
        SourceImpactQuery(item_scope, "pkg.helpers.validate", SourceImpactDirection.DEPENDENTS)
    )
    symbols = {item.symbol_id: item.qualified_name for item in artifact.symbols}
    calls = {
        (symbols[edge.source_symbol_id], edge.target): edge.target_symbol_id
        for edge in artifact.edges
        if edge.kind.value == "calls"
    }
    imports = [edge for edge in artifact.edges if edge.kind.value == "imports"]
    symbol_ids = {item.qualified_name: item.symbol_id for item in artifact.symbols}

    assert [item.symbol.qualified_name for item in result.symbols] == [
        "pkg.nested.worker.execute",
        "pkg.service.process",
    ]
    assert calls[("pkg.service.process", "check")] == symbol_ids["pkg.helpers.validate"]
    assert calls[("pkg.nested.worker.execute", "validate")] == symbol_ids["pkg.helpers.validate"]
    assert any(
        edge.target == ".helpers.validate" and edge.target_symbol_id == symbol_ids["pkg.helpers"]
        for edge in imports
    )
    assert any(
        edge.target == "..helpers.validate" and edge.target_symbol_id == symbol_ids["pkg.helpers"]
        for edge in imports
    )


def test_python_relative_import_that_escapes_the_registered_root_stays_unresolved(
    tmp_path: Path,
) -> None:
    item_scope = scope()
    root = tmp_path / "source"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "service.py").write_text(
        "from ...outside import validate\n\ndef process():\n    return validate()\n"
    )

    artifact = SourceStructureParser().parse(SourceStructureParseRequest(item_scope, root))
    imports = [edge for edge in artifact.edges if edge.kind.value == "imports"]
    calls = [edge for edge in artifact.edges if edge.kind.value == "calls"]

    assert imports[0].target == "...outside.validate"
    assert imports[0].target_symbol_id is None
    assert calls[0].target == "validate"
    assert calls[0].target_symbol_id is None


def test_python_package_initializer_re_exports_resolve_exact_public_calls(tmp_path: Path) -> None:
    """A literal package public API can point to one stored internal declaration."""
    item_scope = scope()
    root = tmp_path / "source"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "core.py").write_text(
        "def validate():\n    return True\n\n"
        "class Service:\n    def run(self):\n        return True\n"
    )
    (root / "pkg" / "__init__.py").write_text(
        "from .core import Service\nfrom .core import validate as check\n"
    )
    (root / "consumer.py").write_text(
        "from pkg import Service, check\n\ndef process():\n    check(); return Service.run()\n"
    )

    artifact = SourceStructureParser().parse(SourceStructureParseRequest(item_scope, root))
    repository = ReferenceSourceStructureRepository()
    repository.store_and_activate(artifact)
    service = SourceImpactService(repository)

    assert [
        item.symbol.qualified_name
        for item in service.query(
            SourceImpactQuery(item_scope, "pkg.core.validate", SourceImpactDirection.DEPENDENTS)
        ).symbols
    ] == ["consumer.process"]
    # This is not a claim about instance dispatch: the ordinary Python spelling
    # remains unresolved unless the exact class member is statically called.
    calls = [item for item in artifact.edges if item.kind.value == "calls"]
    assert {item.target: item.target_symbol_id is not None for item in calls} == {
        "check": True,
        "Service.run": True,
    }


def test_python_package_initializer_wildcard_re_export_stays_unresolved(tmp_path: Path) -> None:
    item_scope = scope()
    root = tmp_path / "source"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "core.py").write_text("def validate():\n    return True\n")
    (root / "pkg" / "__init__.py").write_text("from .core import *\n")
    (root / "consumer.py").write_text(
        "from pkg import validate\n\ndef process():\n    return validate()\n"
    )

    artifact = SourceStructureParser().parse(SourceStructureParseRequest(item_scope, root))
    calls = [item for item in artifact.edges if item.kind.value == "calls"]

    assert len(calls) == 1
    assert calls[0].target == "validate"
    assert calls[0].target_symbol_id is None


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
    module_ids = {
        item.qualified_name: item.symbol_id
        for item in artifact.symbols
        if item.kind.value == "module"
    }
    assert any(
        edge.kind.value == "imports"
        and edge.target == "./helpers"
        and edge.target_symbol_id == module_ids["helpers"]
        for edge in artifact.edges
    )


def test_typescript_top_level_const_function_exports_are_indexed_and_resolve_calls(
    tmp_path: Path,
) -> None:
    item_scope = scope()
    root = tmp_path / "source"
    root.mkdir()
    (root / "helpers.ts").write_text(
        "export const validate = (value: number) => value > 0;\n"
        "export const format = function(value: number) { return String(value); };\n",
        encoding="utf-8",
    )
    (root / "service.ts").write_text(
        "import { validate as check, format } from './helpers';\n"
        "const privateHelper = () => check(1);\n"
        "export const process = () => { privateHelper(); return format(1); };\n",
        encoding="utf-8",
    )
    artifact = SourceStructureParser().parse(SourceStructureParseRequest(item_scope, root))
    repository = ReferenceSourceStructureRepository()
    repository.store_and_activate(artifact)
    service = SourceImpactService(repository)

    assert [
        item.symbol.qualified_name
        for item in service.query(
            SourceImpactQuery(item_scope, "helpers.validate", SourceImpactDirection.DEPENDENTS)
        ).symbols
    ] == ["service.privateHelper", "service.process"]
    assert [
        item.symbol.qualified_name
        for item in service.query(
            SourceImpactQuery(item_scope, "helpers.format", SourceImpactDirection.DEPENDENTS)
        ).symbols
    ] == ["service.process"]


def test_typescript_dynamic_or_reassignable_variable_functions_stay_unresolved(
    tmp_path: Path,
) -> None:
    item_scope = scope()
    root = tmp_path / "source"
    root.mkdir()
    (root / "service.ts").write_text(
        "let mutable = () => true;\n"
        "const conditional = flag ? () => true : () => false;\n"
        "function outer() { const nested = () => true; return nested(); }\n",
        encoding="utf-8",
    )

    artifact = SourceStructureParser().parse(SourceStructureParseRequest(item_scope, root))
    names = {item.qualified_name for item in artifact.symbols}

    assert "service.mutable" not in names
    assert "service.conditional" not in names
    assert "service.outer.nested" not in names


def test_javascript_and_typescript_default_imports_resolve_only_explicit_named_defaults(
    tmp_path: Path,
) -> None:
    item_scope = scope()
    root = tmp_path / "source"
    root.mkdir()
    (root / "normalizer.ts").write_text(
        "export default function normalize(value: number) { return value }\n",
        encoding="utf-8",
    )
    (root / "formatter.js").write_text(
        "export default class Formatter { static format() { return true } }\n",
        encoding="utf-8",
    )
    (root / "service.ts").write_text(
        "import check from './normalizer';\n"
        "import Format from './formatter';\n"
        "export function process() { check(1); return Format.format(); }\n",
        encoding="utf-8",
    )
    artifact = SourceStructureParser().parse(SourceStructureParseRequest(item_scope, root))
    repository = ReferenceSourceStructureRepository()
    repository.store_and_activate(artifact)
    service = SourceImpactService(repository)

    default_function = service.query(
        SourceImpactQuery(item_scope, "normalizer.normalize", SourceImpactDirection.DEPENDENTS)
    )
    default_class = service.query(
        SourceImpactQuery(
            item_scope, "formatter.Formatter.format", SourceImpactDirection.DEPENDENTS
        )
    )

    assert [item.symbol.qualified_name for item in default_function.symbols] == ["service.process"]
    assert [item.symbol.qualified_name for item in default_class.symbols] == ["service.process"]


def test_default_import_does_not_resolve_an_implicit_or_ambiguous_export(tmp_path: Path) -> None:
    item_scope = scope()
    root = tmp_path / "source"
    root.mkdir()
    (root / "helpers.ts").write_text(
        "export function normalize() { return 1 }\n",
        encoding="utf-8",
    )
    (root / "service.ts").write_text(
        "import normalize from './helpers';\nexport function process() { return normalize(); }\n",
        encoding="utf-8",
    )
    artifact = SourceStructureParser().parse(SourceStructureParseRequest(item_scope, root))
    calls = [item for item in artifact.edges if item.kind.value == "calls"]

    assert len(calls) == 1
    assert calls[0].target == "normalize"
    assert calls[0].target_symbol_id is None


def test_default_class_import_never_treats_an_instance_method_as_static(tmp_path: Path) -> None:
    item_scope = scope()
    root = tmp_path / "source"
    root.mkdir()
    (root / "formatter.ts").write_text(
        "export default class Formatter { format() { return true } }\n", encoding="utf-8"
    )
    (root / "service.ts").write_text(
        "import Format from './formatter';\n"
        "export function process() { return Format.format(); }\n",
        encoding="utf-8",
    )

    artifact = SourceStructureParser().parse(SourceStructureParseRequest(item_scope, root))
    calls = [item for item in artifact.edges if item.kind.value == "calls"]

    assert len(calls) == 1
    assert calls[0].target == "Format.format"
    assert calls[0].target_symbol_id is None


def test_typescript_explicit_local_barrel_re_exports_resolve_exact_calls(tmp_path: Path) -> None:
    """A literal named re-export is safe without treating wildcard barrels as evidence."""
    item_scope = scope()
    root = tmp_path / "source"
    root.mkdir()
    (root / "helpers.ts").write_text(
        "export function validate() { return true }\n"
        "export default class Formatter { static format() { return true } }\n",
        encoding="utf-8",
    )
    (root / "barrel.ts").write_text(
        "export { validate as check, default as Format } from './helpers';\n",
        encoding="utf-8",
    )
    (root / "service.ts").write_text(
        "import { check, Format } from './barrel';\n"
        "export function process() { check(); return Format.format(); }\n",
        encoding="utf-8",
    )

    artifact = SourceStructureParser().parse(SourceStructureParseRequest(item_scope, root))
    repository = ReferenceSourceStructureRepository()
    repository.store_and_activate(artifact)
    service = SourceImpactService(repository)

    assert [
        item.symbol.qualified_name
        for item in service.query(
            SourceImpactQuery(item_scope, "helpers.validate", SourceImpactDirection.DEPENDENTS)
        ).symbols
    ] == ["service.process"]
    assert [
        item.symbol.qualified_name
        for item in service.query(
            SourceImpactQuery(
                item_scope, "helpers.Formatter.format", SourceImpactDirection.DEPENDENTS
            )
        ).symbols
    ] == ["service.process"]


def test_typescript_local_wildcard_barrel_resolves_one_non_default_member(tmp_path: Path) -> None:
    item_scope = scope()
    root = tmp_path / "source"
    root.mkdir()
    (root / "helpers.ts").write_text(
        "export function validate() { return true }\n"
        "export default function hidden() { return false }\n",
        encoding="utf-8",
    )
    (root / "barrel.ts").write_text("export * from './helpers';\n", encoding="utf-8")
    (root / "service.ts").write_text(
        "import { validate } from './barrel';\n"
        "import hidden from './barrel';\n"
        "export function process() { validate(); return hidden(); }\n",
        encoding="utf-8",
    )

    artifact = SourceStructureParser().parse(SourceStructureParseRequest(item_scope, root))
    calls = {
        item.target: item.target_symbol_id for item in artifact.edges if item.kind.value == "calls"
    }
    validate = next(item for item in artifact.symbols if item.qualified_name == "helpers.validate")

    assert calls["validate"] == validate.symbol_id
    assert calls["hidden"] is None


def test_typescript_literal_default_barrel_re_export_resolves_named_default(tmp_path: Path) -> None:
    item_scope = scope()
    root = tmp_path / "source"
    root.mkdir()
    (root / "helpers.ts").write_text(
        "export default function validate() { return true }\n", encoding="utf-8"
    )
    (root / "barrel.ts").write_text("export { default } from './helpers';\n", encoding="utf-8")
    (root / "service.ts").write_text(
        "import check from './barrel';\nexport function process() { return check(); }\n",
        encoding="utf-8",
    )

    artifact = SourceStructureParser().parse(SourceStructureParseRequest(item_scope, root))
    calls = [item for item in artifact.edges if item.kind.value == "calls"]
    validate = next(item for item in artifact.symbols if item.qualified_name == "helpers.validate")

    assert len(calls) == 1
    assert calls[0].target_symbol_id == validate.symbol_id


def test_typescript_wildcard_and_ambiguous_barrel_exports_stay_unresolved(tmp_path: Path) -> None:
    """Mnemo keeps barrel support literal: wildcard and duplicate aliases are not guessed."""
    item_scope = scope()
    root = tmp_path / "source"
    root.mkdir()
    (root / "first.ts").write_text("export function validate() { return true }\n", encoding="utf-8")
    (root / "second.ts").write_text(
        "export function validate() { return true }\n", encoding="utf-8"
    )
    (root / "barrel.ts").write_text(
        "export * from './first';\n"
        "export { validate as check } from './first';\n"
        "export { validate as check } from './second';\n",
        encoding="utf-8",
    )
    (root / "service.ts").write_text(
        "import { check } from './barrel';\nexport function process() { return check(); }\n",
        encoding="utf-8",
    )

    artifact = SourceStructureParser().parse(SourceStructureParseRequest(item_scope, root))
    calls = [item for item in artifact.edges if item.kind.value == "calls"]

    assert len(calls) == 1
    assert calls[0].target == "check"
    assert calls[0].target_symbol_id is None


def test_commonjs_literal_require_bindings_resolve_safe_internal_calls(tmp_path: Path) -> None:
    """Direct CommonJS imports have the same bounded static certainty as ES imports."""
    item_scope = scope()
    root = tmp_path / "source"
    root.mkdir()
    (root / "helpers.cjs").write_text(
        "function validate() { return true }\nfunction other() { return true }\n"
        "module.exports = { validate, other }\n"
    )
    (root / "service.cjs").write_text(
        "const helpers = require('./helpers');\n"
        "const { validate: check, other } = require('./helpers');\n"
        "function process() { helpers.validate(); check(); other(); }\n"
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
    module_ids = {
        item.qualified_name: item.symbol_id
        for item in artifact.symbols
        if item.kind.value == "module"
    }
    assert any(
        edge.kind.value == "imports"
        and edge.target == "./helpers"
        and edge.target_symbol_id == module_ids["helpers"]
        for edge in artifact.edges
    )


def test_dynamic_or_nested_commonjs_require_never_creates_a_guessed_binding(tmp_path: Path) -> None:
    """Only a literal top-level require is eligible for Mnemo's static graph."""
    item_scope = scope()
    root = tmp_path / "source"
    root.mkdir()
    (root / "helpers.js").write_text("export function validate() { return true }\n")
    (root / "service.js").write_text(
        "const path = './helpers';\n"
        "const dynamic = require(path);\n"
        "function process() { const nested = require('./helpers'); "
        "dynamic.validate(); nested.validate(); }\n"
    )

    artifact = SourceStructureParser().parse(SourceStructureParseRequest(item_scope, root))
    names = {item.symbol_id: item.qualified_name for item in artifact.symbols}
    calls = {
        (names[edge.source_symbol_id], edge.target): edge.target_symbol_id
        for edge in artifact.edges
        if edge.kind.value == "calls"
    }
    imports = [edge for edge in artifact.edges if edge.kind.value == "imports"]

    assert imports == []
    assert calls[("service.process", "dynamic.validate")] is None
    assert calls[("service.process", "nested.validate")] is None


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


def test_rust_explicit_use_alias_resolves_a_unique_local_member_call(tmp_path: Path) -> None:
    item_scope = scope()
    root = tmp_path / "source"
    (root / "tools").mkdir(parents=True)
    (root / "tools" / "helpers.rs").write_text("pub fn validate() {}\n")
    (root / "service.rs").write_text(
        "use crate::tools::helpers::validate as local_validate;\n"
        "fn process() { local_validate(); }\n"
    )
    artifact = SourceStructureParser().parse(SourceStructureParseRequest(item_scope, root))
    repository = ReferenceSourceStructureRepository()
    repository.store_and_activate(artifact)

    result = SourceImpactService(repository).query(
        SourceImpactQuery(item_scope, "tools.helpers.validate", SourceImpactDirection.DEPENDENTS)
    )
    names = {item.symbol_id: item.qualified_name for item in artifact.symbols}
    calls = {
        (names[edge.source_symbol_id], edge.target): edge.target_symbol_id
        for edge in artifact.edges
        if edge.kind.value == "calls"
    }

    assert [item.symbol.qualified_name for item in result.symbols] == ["service.process"]
    assert calls[("service.process", "local_validate")] is not None


def test_rust_flat_grouped_imports_resolve_only_explicit_unique_members(tmp_path: Path) -> None:
    item_scope = scope()
    root = tmp_path / "source"
    (root / "tools").mkdir(parents=True)
    (root / "tools" / "helpers.rs").write_text(
        "pub fn validate() {} pub fn normalize() {}\n", encoding="utf-8"
    )
    (root / "service.rs").write_text(
        "use crate::tools::helpers::{validate as check, normalize}; "
        "fn run() { check(); normalize(); }\n",
        encoding="utf-8",
    )
    artifact = SourceStructureParser().parse(SourceStructureParseRequest(item_scope, root))
    repository = ReferenceSourceStructureRepository()
    repository.store_and_activate(artifact)

    result = SourceImpactService(repository).query(
        SourceImpactQuery(item_scope, "tools.helpers.validate", SourceImpactDirection.DEPENDENTS)
    )
    names = {item.symbol_id: item.qualified_name for item in artifact.symbols}
    calls = {
        (names[edge.source_symbol_id], edge.target): edge.target_symbol_id
        for edge in artifact.edges
        if edge.kind.value == "calls"
    }

    assert [item.symbol.qualified_name for item in result.symbols] == ["service.run"]
    assert calls[("service.run", "check")] is not None
    assert calls[("service.run", "normalize")] is not None


def test_csharp_explicit_using_alias_resolves_a_unique_local_static_call(tmp_path: Path) -> None:
    item_scope = scope()
    root = tmp_path / "source"
    (root / "Tools").mkdir(parents=True)
    (root / "Tools" / "Helper.cs").write_text(
        "namespace Tools { class Helper { static void Go() {} } }\n"
    )
    (root / "Service.cs").write_text(
        "using H = Tools.Helper; class Service { void Run() { H.Go(); } }\n"
    )
    artifact = SourceStructureParser().parse(SourceStructureParseRequest(item_scope, root))
    repository = ReferenceSourceStructureRepository()
    repository.store_and_activate(artifact)

    result = SourceImpactService(repository).query(
        SourceImpactQuery(item_scope, "Tools.Helper.Helper.Go", SourceImpactDirection.DEPENDENTS)
    )
    names = {item.symbol_id: item.qualified_name for item in artifact.symbols}
    calls = {
        (names[edge.source_symbol_id], edge.target): edge.target_symbol_id
        for edge in artifact.edges
        if edge.kind.value == "calls"
    }

    assert [item.symbol.qualified_name for item in result.symbols] == ["Service.Service.Run"]
    assert calls[("Service.Service.Run", "H.Go")] is not None


def test_csharp_using_static_resolves_a_unique_local_static_member_call(tmp_path: Path) -> None:
    item_scope = scope()
    root = tmp_path / "source"
    (root / "Tools").mkdir(parents=True)
    (root / "Tools" / "Helper.cs").write_text(
        "class Helper { static void Go() {} }\n", encoding="utf-8"
    )
    (root / "Service.cs").write_text(
        "using static Tools.Helper; class Service { void Run() { Go(); } }\n",
        encoding="utf-8",
    )
    artifact = SourceStructureParser().parse(SourceStructureParseRequest(item_scope, root))
    repository = ReferenceSourceStructureRepository()
    repository.store_and_activate(artifact)

    result = SourceImpactService(repository).query(
        SourceImpactQuery(item_scope, "Tools.Helper.Helper.Go", SourceImpactDirection.DEPENDENTS)
    )
    names = {item.symbol_id: item.qualified_name for item in artifact.symbols}
    calls = {
        (names[edge.source_symbol_id], edge.target): edge.target_symbol_id
        for edge in artifact.edges
        if edge.kind.value == "calls"
    }

    assert [item.symbol.qualified_name for item in result.symbols] == ["Service.Service.Run"]
    assert calls[("Service.Service.Run", "Go")] is not None


def test_java_explicit_static_import_resolves_a_unique_local_method_call(tmp_path: Path) -> None:
    item_scope = scope()
    root = tmp_path / "source"
    (root / "tools").mkdir(parents=True)
    (root / "tools" / "Helper.java").write_text(
        "package tools; class Helper { static void go() {} }\n"
    )
    (root / "Service.java").write_text(
        "import static tools.Helper.go; class Service { void run() { go(); } }\n"
    )
    artifact = SourceStructureParser().parse(SourceStructureParseRequest(item_scope, root))
    repository = ReferenceSourceStructureRepository()
    repository.store_and_activate(artifact)

    result = SourceImpactService(repository).query(
        SourceImpactQuery(item_scope, "tools.Helper.Helper.go", SourceImpactDirection.DEPENDENTS)
    )
    names = {item.symbol_id: item.qualified_name for item in artifact.symbols}
    calls = {
        (names[edge.source_symbol_id], edge.target): edge.target_symbol_id
        for edge in artifact.edges
        if edge.kind.value == "calls"
    }

    assert [item.symbol.qualified_name for item in result.symbols] == ["Service.Service.run"]
    assert calls[("Service.Service.run", "go")] is not None


def test_php_explicit_use_alias_resolves_a_unique_local_static_call(tmp_path: Path) -> None:
    item_scope = scope()
    root = tmp_path / "source"
    (root / "Tools").mkdir(parents=True)
    (root / "Tools" / "Helper.php").write_text("<?php class Helper { static function go() {} }\n")
    (root / "service.php").write_text(
        "<?php use Tools\\Helper as H; class Service { function run() { H::go(); } }\n"
    )
    artifact = SourceStructureParser().parse(SourceStructureParseRequest(item_scope, root))
    repository = ReferenceSourceStructureRepository()
    repository.store_and_activate(artifact)

    result = SourceImpactService(repository).query(
        SourceImpactQuery(item_scope, "Tools.Helper.Helper.go", SourceImpactDirection.DEPENDENTS)
    )
    names = {item.symbol_id: item.qualified_name for item in artifact.symbols}
    calls = {
        (names[edge.source_symbol_id], edge.target): edge.target_symbol_id
        for edge in artifact.edges
        if edge.kind.value == "calls"
    }

    assert [item.symbol.qualified_name for item in result.symbols] == ["service.Service.run"]
    assert calls[("service.Service.run", "H.go")] is not None


def test_php_function_alias_resolves_but_const_alias_never_becomes_a_call_binding(
    tmp_path: Path,
) -> None:
    item_scope = scope()
    root = tmp_path / "source"
    root.mkdir()
    (root / "Tools.php").write_text(
        "<?php function validate() {} function VALUE() {}\n", encoding="utf-8"
    )
    (root / "service.php").write_text(
        "<?php use function Tools\\validate as check; use const Tools\\VALUE as V; "
        "function run() { check(); V(); }\n",
        encoding="utf-8",
    )
    artifact = SourceStructureParser().parse(SourceStructureParseRequest(item_scope, root))
    names = {item.symbol_id: item.qualified_name for item in artifact.symbols}
    calls = {
        (names[edge.source_symbol_id], edge.target): edge.target_symbol_id
        for edge in artifact.edges
        if edge.kind.value == "calls"
    }

    assert calls[("service.run", "check")] is not None
    assert calls[("service.run", "V")] is None


def test_php_flat_grouped_imports_keep_members_exact_and_callable_only_when_safe(
    tmp_path: Path,
) -> None:
    item_scope = scope()
    root = tmp_path / "source"
    (root / "Tools").mkdir(parents=True)
    (root / "Tools" / "Helper.php").write_text(
        "<?php class Helper { static function go() {} }\n", encoding="utf-8"
    )
    (root / "Tools.php").write_text(
        "<?php function validate() {} function VALUE() {}\n", encoding="utf-8"
    )
    (root / "service.php").write_text(
        "<?php use Tools\\{Helper as H}; use function Tools\\{validate as check}; "
        "use const Tools\\{VALUE as V}; function run() { H::go(); check(); V(); }\n",
        encoding="utf-8",
    )
    artifact = SourceStructureParser().parse(SourceStructureParseRequest(item_scope, root))
    names = {item.symbol_id: item.qualified_name for item in artifact.symbols}
    calls = {
        (names[edge.source_symbol_id], edge.target): edge.target_symbol_id
        for edge in artifact.edges
        if edge.kind.value == "calls"
    }
    imports = {
        edge.target
        for edge in artifact.edges
        if edge.kind.value == "imports" and edge.source_symbol_id is not None
    }

    assert calls[("service.run", "H.go")] is not None
    assert calls[("service.run", "check")] is not None
    assert calls[("service.run", "V")] is None
    assert {"Tools\\Helper", "Tools\\validate", "Tools\\VALUE"} <= imports


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


def test_duplicate_polyglot_symbol_names_never_create_a_guessed_internal_call_edge(
    tmp_path: Path,
) -> None:
    item_scope = scope()
    root = tmp_path / "source"
    root.mkdir()
    (root / "helpers.py").write_text("def validate():\n    return True\n")
    (root / "helpers.ts").write_text("export function validate() { return true }\n")
    (root / "service.py").write_text(
        "from helpers import validate\n\ndef process():\n    return validate()\n"
    )

    artifact = SourceStructureParser().parse(SourceStructureParseRequest(item_scope, root))
    names = {item.symbol_id: item.qualified_name for item in artifact.symbols}
    calls = {
        (names[item.source_symbol_id], item.target): item.target_symbol_id
        for item in artifact.edges
        if item.kind.value == "calls"
    }
    imports = [item for item in artifact.edges if item.kind.value == "imports"]

    assert calls[("service.process", "validate")] is None
    assert imports[0].target_symbol_id is None


def test_overloaded_declarations_do_not_receive_or_resolve_a_guessed_call_edge(
    tmp_path: Path,
) -> None:
    item_scope = scope()
    root = tmp_path / "source"
    root.mkdir()
    (root / "Service.java").write_text(
        "class Service { void handle() {} void handle(int value) {} void run() { handle(); } }\n"
    )

    artifact = SourceStructureParser().parse(SourceStructureParseRequest(item_scope, root))
    names = {item.symbol_id: item.qualified_name for item in artifact.symbols}
    calls = {
        (names[item.source_symbol_id], item.target): item.target_symbol_id
        for item in artifact.edges
        if item.kind.value == "calls"
    }

    assert calls[("Service.Service.run", "handle")] is None
