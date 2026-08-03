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
from mnemo_memory.packages.application.dbt import (
    DbtManifestApplicationService,
    GetActiveManifestStatus,
    IngestManifest,
)
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


def test_dbt_hook_keeps_the_prior_snapshot_for_missing_invalid_failed_or_interrupted_output(
    tmp_path: Path,
) -> None:
    _, manifest, hooks, service, context = _configured_hook(tmp_path)
    first_before = hooks.before_dbt(context)
    manifest.write_bytes(FIXTURE.read_bytes())
    active = hooks.after_dbt(context, first_before, _success())
    assert active.status is HookStatus.ACTIVATED
    prior = service.get_active_status(GetActiveManifestStatus(scope())).snapshot
    assert prior is not None

    missing_before = hooks.before_dbt(context)
    manifest.unlink()
    assert (
        hooks.after_dbt(context, missing_before, _success()).code
        == "MNEMO_DBT_MANIFEST_UNAVAILABLE"
    )
    assert service.get_active_status(GetActiveManifestStatus(scope())).snapshot == prior

    manifest.write_bytes(FIXTURE.read_bytes())
    invalid_before = hooks.before_dbt(context)
    manifest.write_bytes(b"{not valid JSON")
    assert (
        hooks.after_dbt(context, invalid_before, _success()).code
        == "MNEMO_DBT_MANIFEST_ACTIVATION_FAILED"
    )
    assert service.get_active_status(GetActiveManifestStatus(scope())).snapshot == prior

    changed = _changed_manifest()
    manifest.write_bytes(FIXTURE.read_bytes())
    failed_before = hooks.before_dbt(context)
    manifest.write_bytes(changed)
    assert hooks.after_dbt(
        context, failed_before, CommandResult(True, 9, False, NOW, NOW)
    ).code == ("MNEMO_DBT_COMMAND_NOT_SUCCESSFUL")
    assert service.get_active_status(GetActiveManifestStatus(scope())).snapshot == prior

    interrupted_before = hooks.before_dbt(context)
    assert (
        hooks.after_dbt(context, interrupted_before, CommandResult(True, 130, True, NOW, NOW)).code
        == "MNEMO_DBT_COMMAND_NOT_SUCCESSFUL"
    )
    assert service.get_active_status(GetActiveManifestStatus(scope())).snapshot == prior


def test_dbt_hook_rejects_a_competing_activation_without_replacing_the_winner(
    tmp_path: Path,
) -> None:
    _, manifest, hooks, service, context = _configured_hook(tmp_path)
    first_before = hooks.before_dbt(context)
    manifest.write_bytes(FIXTURE.read_bytes())
    assert hooks.after_dbt(context, first_before, _success()).status is HookStatus.ACTIVATED
    stale_before = hooks.before_dbt(context)
    assert stale_before.expected_active_snapshot_id is not None

    winner = _changed_manifest("winner")
    manifest.write_bytes(winner)
    winner_result = service.ingest(
        IngestManifest(
            scope(),
            winner,
            "manifest.json",
            NOW,
            expected_active_snapshot_id=stale_before.expected_active_snapshot_id,
        )
    )
    losing = _changed_manifest("loser")
    manifest.write_bytes(losing)
    conflict = hooks.after_dbt(context, stale_before, _success())

    assert conflict.status is HookStatus.FAILED
    assert conflict.code == "MNEMO_DBT_ACTIVE_SNAPSHOT_CONFLICT"
    assert (
        service.get_active_status(GetActiveManifestStatus(scope())).snapshot
        == winner_result.snapshot
    )


def test_dbt_hook_honors_equals_project_and_target_path_forms(tmp_path: Path) -> None:
    project, manifest, hooks, _, _ = _configured_hook(tmp_path)
    target = manifest.parent
    context = CommandContext(
        Path(__file__).resolve(),
        (f"--project-dir={project}", f"--target-path={target}", "run"),
        tmp_path.resolve(),
        "dbt",
        "hook-options",
        NOW,
    )

    before = hooks.before_dbt(context)
    assert before.project_root == project
    assert before.manifest_path == manifest


def _configured_hook(
    tmp_path: Path,
) -> tuple[Path, Path, DbtManifestHooks, DbtManifestApplicationService, CommandContext]:
    project = tmp_path / "dbt Δ project"
    target = project / "target"
    target.mkdir(parents=True)
    project.joinpath("dbt_project.yml").write_text("name: synthetic\n")
    manifest = target / "manifest.json"
    store = LocalDbtProjectBindingStore(tmp_path / "memory")
    store.set(DbtProjectBinding(project.resolve(), scope()))
    service = DbtManifestApplicationService(ReferenceProjectIndexRepository(), DbtManifestParser())
    hooks = DbtManifestHooks(store, lambda: service, lambda: NOW)
    context = CommandContext(
        Path(__file__).resolve(), ("run",), project.resolve(), "dbt", "hook-1", NOW
    )
    return project.resolve(), manifest, hooks, service, context


def _success() -> CommandResult:
    return CommandResult(True, 0, False, NOW, NOW)


def _changed_manifest(suffix: str = "changed") -> bytes:
    return FIXTURE.read_bytes().replace(
        b"fixture-invocation-001", f"fixture-invocation-{suffix}".encode()
    )


def test_binding_store_is_project_specific_and_atomic(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "dbt_project.yml").write_text("name: synthetic\n")
    store = LocalDbtProjectBindingStore(tmp_path / "memory")
    store.set(DbtProjectBinding(project.resolve(), scope()))

    assert store.get(project) is not None
    assert store.remove(project) is True
    assert store.get(project) is None
