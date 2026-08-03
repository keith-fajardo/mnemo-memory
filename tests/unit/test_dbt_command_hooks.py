from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from mnemo_memory.connectors.dbt.command_hooks import DbtManifestHooks
from mnemo_memory.connectors.dbt.manifest import DbtManifestParser
from mnemo_memory.connectors.dbt.project_binding import (
    DbtProjectBinding,
    LocalDbtProjectBindingStore,
)
from mnemo_memory.packages.application.command_wrapper import (
    CommandContext,
    CommandResult,
    HookStatus,
)
from mnemo_memory.packages.application.dbt import DbtManifestApplicationService
from mnemo_memory.packages.domain import (
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.storage import ReferenceProjectIndexRepository

NOW = datetime(2026, 8, 3, tzinfo=UTC)
FIXTURE = Path("tests/fixtures/dbt/manifest-v12.json")


def scope() -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string("11111111-1111-4111-8111-111111111111"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string("22222222-2222-4222-8222-222222222222"),
        ProjectId.from_string("33333333-3333-4333-8333-333333333333"),
    )


def test_dbt_hook_activates_changed_manifest_and_skips_failed_or_unchanged(tmp_path: Path) -> None:
    project = tmp_path / "dbt Δ project"
    target = project / "target"
    target.mkdir(parents=True)
    (project / "dbt_project.yml").write_text("name: synthetic\n")
    manifest = target / "manifest.json"
    store = LocalDbtProjectBindingStore(tmp_path / "memory")
    store.set(DbtProjectBinding(project.resolve(), scope()))
    service = DbtManifestApplicationService(ReferenceProjectIndexRepository(), DbtManifestParser())
    hooks = DbtManifestHooks(store, lambda: service, lambda: NOW)
    context = CommandContext(
        Path(__file__).resolve(), ("run",), project.resolve(), "dbt", "hook-1", NOW
    )

    before = hooks.before_dbt(context)
    manifest.write_bytes(FIXTURE.read_bytes())
    activated = hooks.after_dbt(context, before, CommandResult(True, 0, False, NOW, NOW))
    assert activated.status is HookStatus.ACTIVATED

    unchanged_before = hooks.before_dbt(context)
    unchanged = hooks.after_dbt(context, unchanged_before, CommandResult(True, 0, False, NOW, NOW))
    assert unchanged.status is HookStatus.UNCHANGED

    failed = hooks.after_dbt(context, unchanged_before, CommandResult(True, 1, False, NOW, NOW))
    assert failed.status is HookStatus.SKIPPED


def test_binding_store_is_project_specific_and_atomic(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "dbt_project.yml").write_text("name: synthetic\n")
    store = LocalDbtProjectBindingStore(tmp_path / "memory")
    store.set(DbtProjectBinding(project.resolve(), scope()))

    assert store.get(project) is not None
    assert store.remove(project) is True
    assert store.get(project) is None
