"""Exact local workspace declarations become static dependency evidence."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from mnemo_memory.packages.application.checkpoints import CheckpointApplicationService
from mnemo_memory.packages.application.unified_context import (
    GetUnifiedContext,
    UnifiedContextService,
)
from mnemo_memory.packages.domain import (
    CodeEdgeKind,
    CodeSymbolKind,
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    SessionId,
    TaskId,
    Visibility,
)
from mnemo_memory.packages.project_index import (
    SourceImpactDirection,
    SourceImpactQuery,
    SourceImpactService,
    SourceStructureParser,
    SourceStructureParseRequest,
)
from mnemo_memory.packages.storage import (
    ReferenceCheckpointRepository,
    ReferenceSourceStructureRepository,
)


def _scope() -> MemoryScope:
    return MemoryScope(
        OwnerId.new(), ScopeLevel.PROJECT, Visibility.PROJECT, project_id=ProjectId.new()
    )


def _task_scope(project_scope: MemoryScope) -> MemoryScope:
    return MemoryScope(
        project_scope.owner_id,
        ScopeLevel.TASK,
        project_scope.visibility,
        project_scope.workspace_id,
        project_scope.project_id,
        SessionId.new(),
        TaskId.new(),
    )


def test_exact_workspace_dependency_becomes_a_resolved_static_package_edge(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    (root / "packages" / "app" / "src").mkdir(parents=True)
    (root / "packages" / "shared" / "src").mkdir(parents=True)
    (root / "package.json").write_text('{"workspaces":["packages/*"]}', encoding="utf-8")
    (root / "packages" / "app" / "package.json").write_text(
        '{"name":"@example/app","main":"./src/index.ts","dependencies":{"@example/shared":"workspace:^"}}',
        encoding="utf-8",
    )
    (root / "packages" / "shared" / "package.json").write_text(
        '{"name":"@example/shared","main":"./src/index.ts"}', encoding="utf-8"
    )
    (root / "packages" / "app" / "src" / "index.ts").write_text("export const app = 1\n")
    (root / "packages" / "shared" / "src" / "index.ts").write_text("export const shared = 1\n")

    scope = _scope()
    artifact = SourceStructureParser().parse(SourceStructureParseRequest(scope, root))
    modules = {
        item.qualified_name: item for item in artifact.symbols if item.kind is CodeSymbolKind.MODULE
    }
    edge = next(item for item in artifact.edges if item.kind is CodeEdgeKind.PACKAGE_DEPENDENCY)

    assert edge.target == "@example/shared"
    assert edge.source_symbol_id == modules["packages.app.src.index"].symbol_id
    assert edge.target_symbol_id == modules["packages.shared.src.index"].symbol_id

    repository = ReferenceSourceStructureRepository()
    repository.store_and_activate(artifact)
    impact = SourceImpactService(repository).query(
        SourceImpactQuery(scope, "packages.shared.src.index", SourceImpactDirection.DEPENDENTS)
    )
    assert [item.symbol.qualified_name for item in impact.symbols] == ["packages.app.src.index"]
    assert [item.kind for item in impact.edges] == [CodeEdgeKind.PACKAGE_DEPENDENCY]


def test_external_or_non_workspace_ranges_are_not_claimed_as_local_dependencies(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    (root / "packages" / "app" / "src").mkdir(parents=True)
    (root / "packages" / "shared" / "src").mkdir(parents=True)
    (root / "package.json").write_text('{"workspaces":["packages/*"]}', encoding="utf-8")
    (root / "packages" / "app" / "package.json").write_text(
        '{"name":"@example/app","main":"./src/index.ts","dependencies":{"@example/shared":"^1.0.0","external":"workspace:*"}}',
        encoding="utf-8",
    )
    (root / "packages" / "shared" / "package.json").write_text(
        '{"name":"@example/shared","main":"./src/index.ts"}', encoding="utf-8"
    )
    (root / "packages" / "app" / "src" / "index.ts").write_text("export const app = 1\n")
    (root / "packages" / "shared" / "src" / "index.ts").write_text("export const shared = 1\n")

    artifact = SourceStructureParser().parse(SourceStructureParseRequest(_scope(), root))
    assert all(edge.kind is not CodeEdgeKind.PACKAGE_DEPENDENCY for edge in artifact.edges)


def test_source_context_preserves_workspace_dependency_evidence(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    (root / "packages" / "app" / "src").mkdir(parents=True)
    (root / "packages" / "shared" / "src").mkdir(parents=True)
    (root / "package.json").write_text('{"workspaces":["packages/*"]}', encoding="utf-8")
    (root / "packages" / "app" / "package.json").write_text(
        '{"name":"@example/app","main":"./src/index.ts","dependencies":{"@example/shared":"workspace:*"}}',
        encoding="utf-8",
    )
    (root / "packages" / "shared" / "package.json").write_text(
        '{"name":"@example/shared","main":"./src/index.ts"}', encoding="utf-8"
    )
    (root / "packages" / "app" / "src" / "index.ts").write_text("export const app = 1\n")
    (root / "packages" / "shared" / "src" / "index.ts").write_text("export const shared = 1\n")

    project_scope = _scope()
    source = ReferenceSourceStructureRepository()
    source.store_and_activate(
        SourceStructureParser().parse(SourceStructureParseRequest(project_scope, root))
    )
    packet = UnifiedContextService(
        CheckpointApplicationService(
            ReferenceCheckpointRepository(), clock=lambda: datetime(2026, 8, 5, tzinfo=UTC)
        ),
        None,
        source,
    ).get_context(
        GetUnifiedContext(_task_scope(project_scope), source_query="packages.app.src.index")
    )

    assert any(
        '"relationship":"package_dependency"' in item.content for item in packet.structural_items
    )
    assert any("@example/shared" in item.content for item in packet.structural_items)
    assert len(packet.provenance) == len(packet.structural_items)


def test_exact_local_cargo_path_dependency_becomes_a_resolved_static_package_edge(
    tmp_path: Path,
) -> None:
    root = tmp_path / "rust-workspace"
    (root / "crates" / "app" / "src").mkdir(parents=True)
    (root / "crates" / "shared" / "src").mkdir(parents=True)
    (root / "crates" / "app" / "Cargo.toml").write_text(
        """[package]
name = "app"

[dependencies]
shared = { path = "../shared" }
""",
        encoding="utf-8",
    )
    (root / "crates" / "shared" / "Cargo.toml").write_text(
        """[package]
name = "shared"
""",
        encoding="utf-8",
    )
    (root / "crates" / "app" / "src" / "lib.rs").write_text("pub fn app() {}\n")
    (root / "crates" / "shared" / "src" / "lib.rs").write_text("pub fn shared() {}\n")

    scope = _scope()
    artifact = SourceStructureParser().parse(SourceStructureParseRequest(scope, root))
    modules = {
        item.qualified_name: item for item in artifact.symbols if item.kind is CodeSymbolKind.MODULE
    }
    edge = next(item for item in artifact.edges if item.kind is CodeEdgeKind.PACKAGE_DEPENDENCY)

    assert edge.target == "shared"
    assert edge.source_symbol_id == modules["crates.app.src.lib"].symbol_id
    assert edge.target_symbol_id == modules["crates.shared.src.lib"].symbol_id

    repository = ReferenceSourceStructureRepository()
    repository.store_and_activate(artifact)
    impact = SourceImpactService(repository).query(
        SourceImpactQuery(scope, "crates.shared.src.lib", SourceImpactDirection.DEPENDENTS)
    )
    assert [item.symbol.qualified_name for item in impact.symbols] == ["crates.app.src.lib"]


def test_cargo_dependency_requires_exact_local_path_and_matching_package_name(
    tmp_path: Path,
) -> None:
    root = tmp_path / "rust-workspace"
    (root / "crates" / "app" / "src").mkdir(parents=True)
    (root / "crates" / "shared" / "src").mkdir(parents=True)
    (root / "crates" / "app" / "Cargo.toml").write_text(
        """[package]
name = "app"

[dependencies]
shared = "1.0"
wrong_name = { path = "../shared" }
escaping = { path = "../../../outside" }
""",
        encoding="utf-8",
    )
    (root / "crates" / "shared" / "Cargo.toml").write_text(
        """[package]
name = "shared"
""",
        encoding="utf-8",
    )
    (root / "crates" / "app" / "src" / "lib.rs").write_text("pub fn app() {}\n")
    (root / "crates" / "shared" / "src" / "lib.rs").write_text("pub fn shared() {}\n")

    artifact = SourceStructureParser().parse(SourceStructureParseRequest(_scope(), root))
    assert all(edge.kind is not CodeEdgeKind.PACKAGE_DEPENDENCY for edge in artifact.edges)
