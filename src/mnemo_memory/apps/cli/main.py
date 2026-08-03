"""Mnemo lifecycle CLI."""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

import typer

from mnemo_memory.connectors.automatic_memory.client_config import (
    AutomaticMemoryClientConfigError,
    ClientName,
    client_home,
    disable_client_hooks,
    enable_client_hooks,
)
from mnemo_memory.connectors.automatic_memory.hook import AutomaticMemoryHook
from mnemo_memory.connectors.claude_code.mcp_config import ClaudeMcpManager
from mnemo_memory.connectors.codex.mcp_config import CodexMcpManager
from mnemo_memory.connectors.command_wrapper.subprocess_adapter import (
    LocalExecutableResolver,
    SubprocessExecutor,
)
from mnemo_memory.connectors.dbt.command_hooks import DbtManifestHooks
from mnemo_memory.connectors.dbt.manifest import DbtManifestParser
from mnemo_memory.connectors.dbt.project_binding import (
    DbtProjectBinding,
    DbtProjectBindingError,
    LocalDbtProjectBindingStore,
    find_dbt_project_root,
)
from mnemo_memory.packages.application import (
    CheckpointApplicationError,
    DbtApplicationConflict,
    DbtApplicationInvalidManifest,
    DbtApplicationStorageFailure,
    DbtManifestApplicationService,
    GetActiveManifestStatus,
    GetCheckpointContext,
    IngestManifest,
    build_checkpoint_runtime,
    build_lifecycle_service,
    resolve_local_config,
)
from mnemo_memory.packages.application.automatic_memory import (
    AutomaticMemoryBindingError,
    LocalMemoryProjectBindingStore,
    find_memory_project_root,
)
from mnemo_memory.packages.application.command_wrapper import (
    CommandInvocation,
    CommandWrapper,
    HookRegistration,
    discover_command_hooks,
    merge_command_hooks,
)
from mnemo_memory.packages.application.services import LifecycleService
from mnemo_memory.packages.domain import (
    CodeEdge,
    CodeSnapshotId,
    CodeSymbol,
    ContextBudget,
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
from mnemo_memory.packages.storage import SQLiteSourceStructureRepository

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Local-first durable task checkpoints and dbt lineage context.",
)
mcp_app = typer.Typer(no_args_is_help=True, help="Run the local MCP server.")
app.add_typer(mcp_app, name="mcp", help="Run the local MCP server.")
connect_app = typer.Typer(no_args_is_help=True, help="Register Mnemo with an AI coding client.")
disconnect_app = typer.Typer(no_args_is_help=True, help="Remove a client registration.")
dbt_app = typer.Typer(
    no_args_is_help=True,
    help="Enable personal dbt lineage memory and safely wrap local dbt commands.",
)
memory_app = typer.Typer(
    no_args_is_help=True,
    help="Enable automatic bounded task handoffs for a connected coding client.",
)
app.add_typer(connect_app, name="connect", help="Register Mnemo with an AI coding client.")
app.add_typer(disconnect_app, name="disconnect", help="Remove a client registration.")
app.add_typer(dbt_app, name="dbt", help="Enable personal dbt lineage memory and wrap dbt.")
app.add_typer(memory_app, name="memory", help="Set up automatic task memory for this project.")

_AUTOMATIC_SESSION_CONTEXT_BUDGET = ContextBudget(
    active_task_checkpoint=600,
    episodic_memories=600,
    knowledge=0,
    structural=0,
    skills_and_procedures=0,
    provenance_and_conflicts=0,
    total_limit=1_200,
)


def _service(data_dir: Path | None) -> LifecycleService:
    return build_lifecycle_service(resolve_local_config(data_dir))


def _automatic_context_attachment(data_directory: Path, scope: MemoryScope) -> str | None:
    """Return a small canonical handoff for an explicitly enabled session-start hook.

    This runs only after the hook has found a local project binding. The packet is deliberately
    smaller than the normal 5,700-token request and contains only the active task handoff plus
    bounded approved facts; structural lookups still require a named source question.
    """
    try:
        with build_checkpoint_runtime(resolve_local_config(data_directory)) as runtime:
            packet = runtime.checkpoint_service.get_context(
                GetCheckpointContext(
                    scope,
                    budget=_AUTOMATIC_SESSION_CONTEXT_BUDGET,
                    include_approved_events=True,
                    maximum_approved_events=8,
                )
            )
    except (CheckpointApplicationError, OSError, ValueError, RuntimeError):
        return None
    if packet.active_task_checkpoint is None and not packet.episodic_memories:
        return None
    return json.dumps(packet.to_dict(), sort_keys=True, separators=(",", ":"))


def _show(value: object) -> None:
    typer.echo(json.dumps(value, sort_keys=True))


def _guide_client_commands(choice: str) -> tuple[str, ...]:
    commands = {
        "codex": ("mnemo-memory connect codex --auto-memory",),
        "claude-code": ("mnemo-memory connect claude-code --auto-memory",),
        "both": (
            "mnemo-memory connect codex --auto-memory",
            "mnemo-memory connect claude-code --auto-memory",
        ),
        "later": (),
    }
    try:
        return commands[choice]
    except KeyError as error:
        raise typer.BadParameter("choose codex, claude-code, both, or later") from error


def _run_setup_guide(data_dir: Path | None, *, initialize: bool, non_interactive: bool) -> None:
    """Explain explicit checkpoint memory and offer only confirmed setup actions."""
    try:
        config = resolve_local_config(data_dir)
    except ValueError as error:
        raise typer.BadParameter("MNEMO_GUIDE_STORAGE_UNAVAILABLE") from error

    initialized = config.config_path.exists()
    typer.echo("Mnemo Memory setup guide")
    typer.echo(
        "Mnemo stores explicit task checkpoints, not an automatic chat or directory history."
    )
    typer.echo(
        "When you enable automatic memory for a repository, Mnemo also stores a private "
        "static map of supported-language modules, imports, declarations, and explicit calls."
    )
    typer.echo(
        "A later client retrieves a saved checkpoint only from this same local store and scope."
    )
    typer.echo(f"Local store: {config.data_directory}")
    typer.echo("Store status: initialized" if initialized else "Store status: not initialized")

    should_initialize = initialize
    if not initialized and not initialize and not non_interactive:
        should_initialize = typer.confirm("Initialize this local store now?", default=True)
    if should_initialize:
        _show(_service(data_dir).initialize())
    elif not initialized:
        typer.echo(
            "Next step: run mnemo-memory init (or rerun this guide and confirm initialization)."
        )

    typer.echo("\nTo make the two MCP tools available, register one or both clients:")
    if non_interactive:
        choice = "both"
    else:
        choice = typer.prompt(
            "Choose a client (codex, claude-code, both, later)", default="later"
        ).strip()
    commands = _guide_client_commands(choice)
    if commands:
        typer.echo(
            "Run the following command(s) when you are ready. Add --auto-memory to enable "
            "automatic task handoffs for the current project:"
        )
        for command in commands:
            typer.echo(f"  {command}")
    else:
        typer.echo("Client registration deferred. You can return with mnemo-memory guide.")
    typer.echo(
        "\nWith automatic task memory enabled, Mnemo prompts the agent to retrieve context "
        "at a fresh session and save a bounded handoff before work stops."
    )
    typer.echo(
        "Optional dbt lineage: from a dbt project, run mnemo-memory dbt enable once. "
        "No UUIDs are needed."
    )


@app.command("agent", help="Run a deterministic interactive Mnemo setup guide.")
def agent(
    data_dir: Path | None = typer.Option(None, "--data-dir"),  # noqa: B008
    initialize: bool = typer.Option(False, "--initialize", help="Initialize the selected store."),
    non_interactive: bool = typer.Option(
        False, "--non-interactive", help="Print the setup plan without prompts or changes."
    ),
) -> None:
    """Start the local no-model onboarding guide."""
    _run_setup_guide(data_dir, initialize=initialize, non_interactive=non_interactive)


@app.command("guide", help="Alias for the interactive Mnemo setup agent.")
def guide(
    data_dir: Path | None = typer.Option(None, "--data-dir"),  # noqa: B008
    initialize: bool = typer.Option(False, "--initialize", help="Initialize the selected store."),
    non_interactive: bool = typer.Option(
        False, "--non-interactive", help="Print the setup plan without prompts or changes."
    ),
) -> None:
    """Run the onboarding guide using the shorter descriptive command name."""
    _run_setup_guide(data_dir, initialize=initialize, non_interactive=non_interactive)


@app.command(help="Initialize Mnemo's local data directory and SQLite database.")
def init(data_dir: Path | None = typer.Option(None, "--data-dir")) -> None:  # noqa: B008
    _show(_service(data_dir).initialize())


@app.command(help="Start the local Mnemo lifecycle service.")
def start(data_dir: Path | None = typer.Option(None, "--data-dir")) -> None:  # noqa: B008
    _show(_service(data_dir).start())


@app.command(help="Show local Mnemo lifecycle and storage status.")
def status(data_dir: Path | None = typer.Option(None, "--data-dir")) -> None:  # noqa: B008
    _show(_service(data_dir).status())


@app.command(help="Stop the local Mnemo lifecycle service.")
def stop(data_dir: Path | None = typer.Option(None, "--data-dir")) -> None:  # noqa: B008
    _show(_service(data_dir).stop())


def _project_scope(owner_id: str, workspace_id: str, project_id: str) -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string(owner_id),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string(workspace_id),
        ProjectId.from_string(project_id),
    )


def _binding_store(data_dir: Path | None) -> LocalDbtProjectBindingStore:
    return LocalDbtProjectBindingStore(resolve_local_config(data_dir).data_directory)


def _advanced_scope(
    owner_id: str | None, workspace_id: str | None, project_id: str | None
) -> MemoryScope | None:
    values = (owner_id, workspace_id, project_id)
    if not any(values):
        return None
    if not all(values):
        raise typer.BadParameter("MNEMO_DBT_SCOPE_OVERRIDE_INCOMPLETE")
    assert owner_id is not None and workspace_id is not None and project_id is not None
    return _project_scope(owner_id, workspace_id, project_id)


def _initialize_dbt_profile(data_dir: Path | None) -> tuple[Path, LocalDbtProjectBindingStore]:
    _service(data_dir).initialize()
    config = resolve_local_config(data_dir)
    return config.data_directory, LocalDbtProjectBindingStore(config.data_directory)


def _ingest_existing_manifest(data_directory: Path, binding: DbtProjectBinding) -> tuple[str, bool]:
    manifest = binding.project_root / "target" / "manifest.json"
    if not manifest.is_file():
        return "unavailable", False
    try:
        with build_checkpoint_runtime(
            resolve_local_config(data_directory), dbt_parser=DbtManifestParser()
        ) as runtime:
            assert runtime.dbt_manifest_service is not None
            stored = runtime.dbt_manifest_service.ingest(
                IngestManifest(
                    binding.scope, manifest.read_bytes(), "manifest.json", datetime.now(UTC)
                )
            )
    except (
        DbtApplicationConflict,
        DbtApplicationInvalidManifest,
        DbtApplicationStorageFailure,
        OSError,
    ):
        return "invalid_or_unavailable", False
    return ("unchanged" if stored.idempotent else "activated"), True


@dbt_app.command("enable", help="Enable Mnemo for this dbt project; no UUIDs are needed normally.")
def dbt_enable(
    project_dir: Path = typer.Option(Path("."), "--project-dir"),  # noqa: B008
    data_dir: Path | None = typer.Option(None, "--data-dir"),  # noqa: B008
    ingest_existing: bool = typer.Option(True, "--ingest-existing/--no-ingest-existing"),
    owner_id: str | None = typer.Option(None, "--owner-id", help="Advanced scope override."),
    workspace_id: str | None = typer.Option(
        None, "--workspace-id", help="Advanced scope override."
    ),
    project_id: str | None = typer.Option(None, "--project-id", help="Advanced scope override."),
) -> None:
    """Create/reuse private personal identities and bind the nearest dbt project once."""
    try:
        data_directory, store = _initialize_dbt_profile(data_dir)
        root = find_dbt_project_root(project_dir)
        binding = store.get(root)
        scope_override = _advanced_scope(owner_id, workspace_id, project_id)
        automatic_binding = LocalMemoryProjectBindingStore(data_directory).get(root)
        automatic_scope = (
            automatic_binding.scope
            if automatic_binding is not None and automatic_binding.project_root == root
            else None
        )
        if binding is None:
            binding = DbtProjectBinding(
                root, scope_override or automatic_scope or store.personal_profile().project_scope()
            )
            store.set(binding)
        elif scope_override is not None and scope_override != binding.scope:
            raise typer.BadParameter("MNEMO_DBT_PROJECT_ALREADY_ENABLED")
        elif automatic_scope is not None and automatic_scope != binding.scope:
            raise typer.BadParameter("MNEMO_DBT_PROJECT_SCOPE_CONFLICT")

        manifest_status, ingested = (
            _ingest_existing_manifest(data_directory, binding)
            if ingest_existing
            else ("not_requested", False)
        )
        _show(
            {
                "enabled": True,
                "project_root": str(binding.project_root),
                "existing_manifest": manifest_status,
                "ingested": ingested,
            }
        )
    except (DbtProjectBindingError, ValueError) as error:
        raise typer.BadParameter("MNEMO_DBT_ENABLE_FAILED") from error


@dbt_app.command(
    "configure",
    help="Bind one local dbt project directory to an explicit Mnemo scope.",
    hidden=True,
)
def dbt_configure(
    project_dir: Path = typer.Option(..., "--project-dir"),  # noqa: B008
    owner_id: str = typer.Option(...),
    workspace_id: str = typer.Option(...),
    project_id: str = typer.Option(...),
    data_dir: Path | None = typer.Option(None, "--data-dir"),  # noqa: B008
) -> None:
    try:
        root = find_dbt_project_root(project_dir)
        _binding_store(data_dir).set(
            DbtProjectBinding(root, _project_scope(owner_id, workspace_id, project_id))
        )
        _show(
            {
                "configured": True,
                "project_root": str(root),
                "scope": _project_scope(owner_id, workspace_id, project_id).to_dict(),
            }
        )
    except (DbtProjectBindingError, ValueError) as error:
        raise typer.BadParameter("MNEMO_DBT_CONFIGURATION_INVALID") from error


@dbt_app.command(
    "configuration", help="Show the local Mnemo scope binding for a dbt project.", hidden=True
)
def dbt_configuration(
    project_dir: Path = typer.Option(..., "--project-dir"),  # noqa: B008
    data_dir: Path | None = typer.Option(None, "--data-dir"),  # noqa: B008
    check: bool = typer.Option(False, "--check"),
) -> None:
    try:
        binding = _binding_store(data_dir).get(project_dir)
    except DbtProjectBindingError as error:
        raise typer.BadParameter("MNEMO_DBT_CONFIGURATION_INVALID") from error
    if binding is None:
        _show({"configured": False})
        if check:
            raise typer.Exit(1)
        return
    _show(
        {
            "configured": True,
            "project_root": str(binding.project_root),
            "scope": binding.scope.to_dict(),
        }
    )


@dbt_app.command(
    "unconfigure", help="Remove only the local Mnemo binding for a dbt project.", hidden=True
)
def dbt_unconfigure(
    project_dir: Path = typer.Option(..., "--project-dir"),  # noqa: B008
    data_dir: Path | None = typer.Option(None, "--data-dir"),  # noqa: B008
) -> None:
    try:
        _show({"removed": _binding_store(data_dir).remove(project_dir)})
    except DbtProjectBindingError as error:
        raise typer.BadParameter("MNEMO_DBT_CONFIGURATION_INVALID") from error


@dbt_app.command("disable", help="Disable Mnemo only for this dbt project; saved snapshots remain.")
def dbt_disable(
    project_dir: Path = typer.Option(Path("."), "--project-dir"),  # noqa: B008
    data_dir: Path | None = typer.Option(None, "--data-dir"),  # noqa: B008
) -> None:
    try:
        removed = _binding_store(data_dir).remove(project_dir)
        _show({"enabled": False, "removed": removed})
    except DbtProjectBindingError as error:
        raise typer.BadParameter("MNEMO_DBT_DISABLE_FAILED") from error


@dbt_app.command(
    "ingest", help="Validate and activate a local manifest.json without running dbt.", hidden=True
)
def dbt_ingest(
    manifest: Path,
    owner_id: str | None = typer.Option(None, "--owner-id", help="Advanced scope override."),
    workspace_id: str | None = typer.Option(
        None, "--workspace-id", help="Advanced scope override."
    ),
    project_id: str | None = typer.Option(None, "--project-id", help="Advanced scope override."),
    data_dir: Path | None = typer.Option(None, "--data-dir"),  # noqa: B008
    dry_run: bool = typer.Option(False, "--dry-run"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Validate and atomically activate a local dbt manifest without executing dbt."""
    try:
        raw = manifest.read_bytes()
        scope = _advanced_scope(owner_id, workspace_id, project_id)
        if scope is None:
            binding = _binding_store(data_dir).get(manifest.parent)
            if binding is None:
                raise typer.BadParameter("MNEMO_DBT_PROJECT_NOT_ENABLED")
            scope = binding.scope
        with build_checkpoint_runtime(
            resolve_local_config(data_dir), dbt_parser=DbtManifestParser()
        ) as runtime:
            assert runtime.dbt_manifest_service is not None
            command = IngestManifest(
                scope,
                raw,
                "manifest.json",
                datetime.now(UTC),
            )
            if dry_run:
                artifact = DbtManifestParser().parse_for_ingestion(
                    raw,
                    scope=scope,
                    source_identity="manifest.json",
                    ingested_at=command.ingested_at,
                    source_state=None,
                )
                result = {
                    "dry_run": True,
                    "nodes": len(artifact.nodes),
                    "edges": len(artifact.edges),
                    "content_digest": artifact.metadata.content_digest,
                }
            else:
                stored = runtime.dbt_manifest_service.ingest(command)
                result = {
                    "snapshot_id": str(stored.snapshot.snapshot_id),
                    "nodes": stored.snapshot.node_count,
                    "edges": stored.snapshot.edge_count,
                    "idempotent": stored.idempotent,
                }
        _show(result) if json_output else typer.echo(json.dumps(result, sort_keys=True))
    except Exception as error:
        raise typer.BadParameter("MNEMO_DBT_INGEST_FAILED") from error


@dbt_app.command("status", help="Show the active Mnemo manifest snapshot for this dbt project.")
def dbt_status(
    project_dir: Path = typer.Option(Path("."), "--project-dir"),  # noqa: B008
    owner_id: str | None = typer.Option(None, "--owner-id", help="Advanced scope override."),
    workspace_id: str | None = typer.Option(
        None, "--workspace-id", help="Advanced scope override."
    ),
    project_id: str | None = typer.Option(None, "--project-id", help="Advanced scope override."),
    data_dir: Path | None = typer.Option(None, "--data-dir"),  # noqa: B008
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    scope = _advanced_scope(owner_id, workspace_id, project_id)
    if scope is None:
        try:
            binding = _binding_store(data_dir).get(project_dir)
        except DbtProjectBindingError as error:
            raise typer.BadParameter("MNEMO_DBT_STATUS_FAILED") from error
        if binding is None:
            unenabled = {
                "enabled": False,
                "active": False,
                "instruction": "mnemo-memory dbt enable",
            }
            _show(unenabled) if json_output else typer.echo(json.dumps(unenabled, sort_keys=True))
            return
        scope = binding.scope
    with build_checkpoint_runtime(
        resolve_local_config(data_dir), dbt_parser=DbtManifestParser()
    ) as runtime:
        assert runtime.dbt_manifest_service is not None
        status = runtime.dbt_manifest_service.get_active_status(GetActiveManifestStatus(scope))
    result: dict[str, object] = {
        "enabled": True,
        "active": status.snapshot is not None,
        "currentness": status.currentness.value,
        "reason": status.reason,
    }
    if status.snapshot is not None:
        result.update(
            {
                "snapshot_id": str(status.snapshot.snapshot_id),
                "nodes": status.snapshot.node_count,
                "edges": status.snapshot.edge_count,
            }
        )
    _show(result) if json_output else typer.echo(json.dumps(result, sort_keys=True))


def _dbt_executable(explicit: Path | None) -> str | Path:
    if explicit is not None:
        if not explicit.is_absolute():
            raise typer.BadParameter("MNEMO_DBT_EXECUTABLE_NOT_ABSOLUTE")
        return explicit
    configured = os.environ.get("MNEMO_DBT_EXECUTABLE")
    if configured is not None:
        candidate = Path(configured)
        if not candidate.is_absolute():
            raise typer.BadParameter("MNEMO_DBT_EXECUTABLE_NOT_ABSOLUTE")
        return candidate
    return "dbt"


@dbt_app.command(
    "exec",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    help="Run exact dbt arguments with safe Mnemo pre/post manifest hooks.",
)
def dbt_exec(
    context: typer.Context,
    data_dir: Path | None = typer.Option(None, "--data-dir"),  # noqa: B008
    strict_memory: bool = typer.Option(False, "--strict-memory"),
    json_summary: bool = typer.Option(False, "--json-summary"),
    dbt_executable: Path | None = typer.Option(None, "--dbt-executable"),  # noqa: B008
) -> None:
    arguments = tuple(context.args)
    if not arguments:
        raise typer.BadParameter("MNEMO_DBT_ARGUMENTS_REQUIRED")
    config = resolve_local_config(data_dir)

    def dbt_service() -> DbtManifestApplicationService:
        with build_checkpoint_runtime(config, dbt_parser=DbtManifestParser()) as runtime:
            assert runtime.dbt_manifest_service is not None
            return runtime.dbt_manifest_service

    hooks = DbtManifestHooks(
        LocalDbtProjectBindingStore(config.data_directory),
        dbt_service,
        lambda: datetime.now(UTC),
    )
    launcher = shutil.which("mnemo-memory")
    wrapper_path = Path(launcher).resolve() if launcher is not None else None
    built_in_hooks = (HookRegistration("dbt-manifest", "dbt", hooks.before_dbt, hooks.after_dbt),)
    discovered_hooks = merge_command_hooks(built_in_hooks, discover_command_hooks("dbt"))
    wrapped = CommandWrapper(
        LocalExecutableResolver(),
        SubprocessExecutor(),
        lambda: datetime.now(UTC),
        lambda: str(uuid4()),
        discovered_hooks.registrations,
    ).run(
        CommandInvocation(_dbt_executable(dbt_executable), arguments, Path.cwd().resolve(), "dbt"),
        strict_memory=strict_memory,
        wrapper_executable=wrapper_path,
    )
    summary = {
        "exit_code": wrapped.result.exit_code,
        "started": wrapped.result.started,
        "interrupted": wrapped.result.interrupted,
        "outcomes": [
            {
                "hook": value.registration,
                "status": value.outcome.status.value,
                "code": value.outcome.code,
            }
            for value in wrapped.outcomes
        ],
        "warnings": [warning.code for warning in (*discovered_hooks.warnings, *wrapped.warnings)],
    }
    if json_summary:
        _show(summary)
    elif wrapped.outcomes or wrapped.warnings:
        setup_required = any(
            value.outcome.code == "MNEMO_DBT_PROJECT_UNCONFIGURED" for value in wrapped.outcomes
        )
        if setup_required:
            typer.echo(
                "Mnemo skipped dbt memory for this project. Run: mnemo-memory dbt enable",
                err=True,
            )
        else:
            typer.echo(json.dumps(summary, sort_keys=True), err=True)
    raise typer.Exit(wrapped.result.exit_code)


@dbt_app.command("shell-hook", help="Print opt-in shell code that routes dbt through Mnemo.")
def dbt_shell_hook(shell: str = typer.Argument(...)) -> None:
    if shell in {"zsh", "bash"}:
        typer.echo('dbt() { command mnemo-memory dbt exec -- "$@"; }')
        return
    if shell == "fish":
        typer.echo("function dbt\n    command mnemo-memory dbt exec -- $argv\nend")
        return
    raise typer.BadParameter("supported shells: zsh, bash, fish")


@mcp_app.command("serve", help="Serve exactly get_context and save_checkpoint over stdio.")
def mcp_serve(
    stdio: bool = typer.Option(False, "--stdio"),
    data_dir: Path | None = typer.Option(None, "--data-dir"),  # noqa: B008
) -> None:
    if not stdio:
        raise typer.BadParameter("Issue 7 supports only --stdio")
    arguments = [sys.executable, "-m", "mnemo_memory.apps.mcp.server"]
    if data_dir is not None:
        arguments.extend(["--data-dir", str(data_dir)])
    os.execv(sys.executable, arguments)


def _codex_manager() -> CodexMcpManager:
    launcher = shutil.which("mnemo-memory")
    if launcher is None:
        raise typer.BadParameter("MNEMO_LAUNCHER_NOT_RESOLVABLE: install Mnemo before connecting")
    return CodexMcpManager.discover(Path(launcher).resolve())


def _claude_manager() -> ClaudeMcpManager:
    launcher = shutil.which("mnemo-memory")
    if launcher is None:
        raise typer.BadParameter("MNEMO_LAUNCHER_NOT_RESOLVABLE: install Mnemo before connecting")
    return ClaudeMcpManager.discover(Path(launcher).resolve())


def _installed_launcher() -> Path:
    launcher = shutil.which("mnemo-memory")
    if launcher is None:
        raise typer.BadParameter("MNEMO_LAUNCHER_NOT_RESOLVABLE: install Mnemo before connecting")
    return Path(launcher).resolve()


def _enable_automatic_task_memory(
    client: str, project_dir: Path, data_dir: Path | None
) -> dict[str, object]:
    """Create local scope binding and only Mnemo's explicit client hook entries."""
    if client not in {"codex", "claude-code"}:
        raise typer.BadParameter("MNEMO_MEMORY_CLIENT_INVALID")
    typed_client = cast(ClientName, client)
    try:
        config = resolve_local_config(data_dir)
        _service(data_dir).initialize()
        dbt_scope: MemoryScope | None = None
        try:
            dbt_binding = LocalDbtProjectBindingStore(config.data_directory).get(project_dir)
            if dbt_binding is not None and dbt_binding.project_root == find_memory_project_root(
                project_dir
            ):
                dbt_scope = dbt_binding.scope
        except DbtProjectBindingError:
            # Non-dbt repositories have no dbt binding to align with.
            pass
        binding = LocalMemoryProjectBindingStore(config.data_directory).enable(
            project_dir, project_scope=dbt_scope
        )
        source_repository = SQLiteSourceStructureRepository(
            config.database_path, base_directory=config.data_directory
        )
        source_repository.migrate()
        source_result = source_repository.store_and_activate(
            SourceStructureParser().parse(
                SourceStructureParseRequest(binding.scope, binding.project_root)
            )
        )
        changed = enable_client_hooks(
            typed_client, _installed_launcher(), client_home(typed_client), config.data_directory
        )
        return {
            "automatic_memory": True,
            "project_root": str(binding.project_root),
            "hook_configuration_changed": changed,
            "source_structure": {
                "indexed": True,
                "snapshot_id": str(source_result.snapshot.snapshot_id),
                "files": source_result.snapshot.file_count,
                "symbols": source_result.snapshot.symbol_count,
                "relationships": source_result.snapshot.edge_count,
                "idempotent": source_result.idempotent,
            },
        }
    except (AutomaticMemoryBindingError, AutomaticMemoryClientConfigError, ValueError) as error:
        raise typer.BadParameter("MNEMO_MEMORY_ENABLE_FAILED") from error


def _disable_automatic_task_memory(client: str, data_dir: Path | None) -> bool:
    if client not in {"codex", "claude-code"}:
        raise typer.BadParameter("MNEMO_MEMORY_CLIENT_INVALID")
    typed_client = cast(ClientName, client)
    try:
        config = resolve_local_config(data_dir)
        return disable_client_hooks(
            typed_client, _installed_launcher(), client_home(typed_client), config.data_directory
        )
    except AutomaticMemoryClientConfigError as error:
        raise typer.BadParameter("MNEMO_MEMORY_DISABLE_FAILED") from error


@memory_app.command("enable", help="Enable automatic task handoffs for this project and client.")
def memory_enable(
    client: str = typer.Argument(..., help="codex or claude-code"),
    project_dir: Path = typer.Option(Path("."), "--project-dir"),  # noqa: B008
    data_dir: Path | None = typer.Option(None, "--data-dir"),  # noqa: B008
    yes: bool = typer.Option(False, "--yes", help="Confirm client hook configuration changes."),
) -> None:
    """Opt in once; the agent is then reminded automatically at stop/compaction."""
    if not yes and not typer.confirm(
        "Enable Mnemo automatic task-memory hooks for this client and project?"
    ):
        raise typer.Abort()
    _show(_enable_automatic_task_memory(client, project_dir, data_dir))


@memory_app.command("disable", help="Remove only Mnemo's automatic task-memory hooks.")
def memory_disable(
    client: str = typer.Argument(..., help="codex or claude-code"),
    data_dir: Path | None = typer.Option(None, "--data-dir"),  # noqa: B008
    yes: bool = typer.Option(False, "--yes", help="Confirm removal of Mnemo hook entries."),
) -> None:
    if not yes and not typer.confirm("Remove Mnemo automatic task-memory hooks for this client?"):
        raise typer.Abort()
    _show(
        {
            "automatic_memory": False,
            "removed": _disable_automatic_task_memory(client, data_dir),
        }
    )


@memory_app.command("history", help="List recent saved structural refreshes for this project.")
def memory_history(
    limit: int = typer.Option(20, "--limit", min=1, max=100),
    project_dir: Path = typer.Option(Path("."), "--project-dir"),  # noqa: B008
    data_dir: Path | None = typer.Option(None, "--data-dir"),  # noqa: B008
) -> None:
    """List activation order without exposing source bodies or absolute project paths."""
    try:
        config = resolve_local_config(data_dir)
        binding = LocalMemoryProjectBindingStore(config.data_directory).get(project_dir)
        if binding is None:
            raise typer.BadParameter("MNEMO_MEMORY_PROJECT_NOT_ENABLED")
        repository = SQLiteSourceStructureRepository(
            config.database_path, base_directory=config.data_directory
        )
        active = repository.get_active_snapshot(binding.scope)
        snapshots = repository.list_activation_history(binding.scope, limit=limit)
    except (AutomaticMemoryBindingError, ValueError) as error:
        raise typer.BadParameter("MNEMO_SOURCE_HISTORY_UNAVAILABLE") from error
    _show(
        {
            "active_snapshot_id": None if active is None else str(active.snapshot_id),
            "snapshots": [
                {
                    "snapshot_id": str(snapshot.snapshot_id),
                    "source_digest": snapshot.source_digest,
                    "file_count": snapshot.file_count,
                    "symbol_count": snapshot.symbol_count,
                    "relationship_count": snapshot.edge_count,
                    "active": active is not None and snapshot.snapshot_id == active.snapshot_id,
                }
                for snapshot in snapshots
            ],
        }
    )


@memory_app.command(
    "impact", help="Show proven static dependencies or dependents for this project."
)
def memory_impact(
    symbol: str = typer.Argument(..., help="Saved symbol name or relative source path."),
    direction: SourceImpactDirection = SourceImpactDirection.DEPENDENTS,
    direct: bool = typer.Option(False, "--direct", help="Return only one relationship hop."),
    maximum_depth: int | None = typer.Option(None, "--maximum-depth", min=0),
    project_dir: Path = typer.Option(Path("."), "--project-dir"),  # noqa: B008
    data_dir: Path | None = typer.Option(None, "--data-dir"),  # noqa: B008
) -> None:
    """Query the enabled project's bounded, evidence-backed static impact map."""
    try:
        config = resolve_local_config(data_dir)
        binding = LocalMemoryProjectBindingStore(config.data_directory).get(project_dir)
        if binding is None:
            raise typer.BadParameter("MNEMO_MEMORY_PROJECT_NOT_ENABLED")
        repository = SQLiteSourceStructureRepository(
            config.database_path, base_directory=config.data_directory
        )
        result = SourceImpactService(repository).query(
            SourceImpactQuery(
                binding.scope,
                symbol,
                direction,
                not direct,
                maximum_depth,
            )
        )
    except (AutomaticMemoryBindingError, ValueError) as error:
        raise typer.BadParameter("MNEMO_SOURCE_IMPACT_UNAVAILABLE") from error
    _show(
        {
            "snapshot_id": str(result.snapshot.snapshot_id),
            "currentness": "unknown",
            "direction": result.direction.value,
            "start_symbols": [item.qualified_name for item in result.start_symbols],
            "symbols": [
                {
                    "path": item.symbol.relative_path,
                    "symbol": item.symbol.qualified_name,
                    "kind": item.symbol.kind.value,
                    "line": item.symbol.line,
                    "depth": item.depth,
                }
                for item in result.symbols
            ],
            "relationships": [
                {
                    "kind": item.kind.value,
                    "target": item.target,
                    "resolved": item.target_symbol_id is not None,
                }
                for item in result.edges
            ],
            "truncated": result.truncated,
            "truncation_reason": result.truncation_reason,
        }
    )


@memory_app.command(
    "changes", help="Show the latest recorded structural change, or compare two snapshots."
)
def memory_changes(
    before_snapshot_id: str | None = typer.Option(
        None, "--from", help="Earlier source snapshot UUID (advanced)."
    ),
    after_snapshot_id: str | None = typer.Option(
        None, "--to", help="Later source snapshot UUID (advanced)."
    ),
    latest: bool = typer.Option(
        False, "--latest", help="Use the two most recent recorded snapshot activations."
    ),
    project_dir: Path = typer.Option(Path("."), "--project-dir"),  # noqa: B008
    data_dir: Path | None = typer.Option(None, "--data-dir"),  # noqa: B008
) -> None:
    """Show only structural additions/removals; saved snapshots remain immutable."""
    try:
        config = resolve_local_config(data_dir)
        binding = LocalMemoryProjectBindingStore(config.data_directory).get(project_dir)
        if binding is None:
            raise typer.BadParameter("MNEMO_MEMORY_PROJECT_NOT_ENABLED")
        repository = SQLiteSourceStructureRepository(
            config.database_path, base_directory=config.data_directory
        )
        if latest and (before_snapshot_id is not None or after_snapshot_id is not None):
            raise typer.BadParameter("MNEMO_SOURCE_DIFF_ARGUMENTS_INVALID")
        use_latest = latest or (before_snapshot_id is None and after_snapshot_id is None)
        if use_latest:
            transition = repository.latest_transition(binding.scope)
            if transition is None:
                raise typer.BadParameter("MNEMO_SOURCE_DIFF_NO_PRIOR_TRANSITION")
            before_id, after_id = transition[0].snapshot_id, transition[1].snapshot_id
        elif before_snapshot_id is None or after_snapshot_id is None:
            raise typer.BadParameter("MNEMO_SOURCE_DIFF_ARGUMENTS_INVALID")
        else:
            before_id = CodeSnapshotId.from_string(before_snapshot_id)
            after_id = CodeSnapshotId.from_string(after_snapshot_id)
        diff = SourceImpactService(repository).diff(
            binding.scope,
            before_id,
            after_id,
        )
    except (AutomaticMemoryBindingError, ValueError) as error:
        raise typer.BadParameter("MNEMO_SOURCE_DIFF_UNAVAILABLE") from error

    def symbol(value: object) -> dict[str, object]:
        item = cast(CodeSymbol, value)
        return {
            "path": item.relative_path,
            "symbol": item.qualified_name,
            "kind": item.kind.value,
            "line": item.line,
        }

    def edge(value: object) -> dict[str, object]:
        item = cast(CodeEdge, value)
        return {
            "relationship": item.kind.value,
            "target": item.target,
            "resolved": item.target_symbol_id is not None,
        }

    _show(
        {
            "before_snapshot_id": str(diff.before.snapshot_id),
            "after_snapshot_id": str(diff.after.snapshot_id),
            "added_symbols": [symbol(item) for item in diff.added_symbols],
            "removed_symbols": [symbol(item) for item in diff.removed_symbols],
            "added_relationships": [edge(item) for item in diff.added_edges],
            "removed_relationships": [edge(item) for item in diff.removed_edges],
        }
    )


@memory_app.command(
    "refresh", help="Rebuild the enabled project's static source-structure snapshot."
)
def memory_refresh(
    project_dir: Path = typer.Option(Path("."), "--project-dir"),  # noqa: B008
    data_dir: Path | None = typer.Option(None, "--data-dir"),  # noqa: B008
) -> None:
    """Refresh from local source syntax only; no source text is retained."""
    try:
        config = resolve_local_config(data_dir)
        binding = LocalMemoryProjectBindingStore(config.data_directory).get(project_dir)
        if binding is None:
            raise typer.BadParameter("MNEMO_MEMORY_PROJECT_NOT_ENABLED")
        repository = SQLiteSourceStructureRepository(
            config.database_path, base_directory=config.data_directory
        )
        repository.migrate()
        previous = repository.get_active_snapshot(binding.scope)
        stored = repository.store_and_activate(
            SourceStructureParser().parse(
                SourceStructureParseRequest(binding.scope, binding.project_root)
            )
        )
    except (AutomaticMemoryBindingError, ValueError) as error:
        raise typer.BadParameter("MNEMO_SOURCE_REFRESH_UNAVAILABLE") from error
    _show(
        {
            "snapshot_id": str(stored.snapshot.snapshot_id),
            "previous_snapshot_id": None if previous is None else str(previous.snapshot_id),
            "idempotent": stored.idempotent,
            "files": stored.snapshot.file_count,
            "symbols": stored.snapshot.symbol_count,
            "relationships": stored.snapshot.edge_count,
            "currentness": "unknown_after_refresh",
        }
    )


@app.command("automatic-memory-hook", hidden=True)
def automatic_memory_hook(
    client: str = typer.Option(..., "--client"),
    data_dir: Path | None = typer.Option(None, "--data-dir"),  # noqa: B008
) -> None:
    """Client-facing hook entry point; JSON in, sanitized JSON out."""
    if client not in {"codex", "claude-code"}:
        raise typer.Exit(0)
    try:
        raw = json.load(sys.stdin)
        config = resolve_local_config(data_dir)
        hook = AutomaticMemoryHook(
            config.data_directory,
            cast(ClientName, client),
            context_loader=lambda scope: _automatic_context_attachment(
                config.data_directory, scope
            ),
        )
        result = hook.handle(raw)
    except (OSError, ValueError, json.JSONDecodeError):
        result = {"systemMessage": "MNEMO_MEMORY_HOOK_UNAVAILABLE"}
    typer.echo(json.dumps(result, sort_keys=True, separators=(",", ":")))


@connect_app.command("codex", help="Register the installed Mnemo MCP launcher with Codex.")
def connect_codex(
    check: bool = typer.Option(False, "--check"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    yes: bool = typer.Option(False, "--yes"),
    json_output: bool = typer.Option(False, "--json"),
    auto_memory: bool = typer.Option(
        False,
        "--auto-memory",
        help="Also enable automatic task handoffs for the current project.",
    ),
    project_dir: Path = typer.Option(Path("."), "--project-dir"),  # noqa: B008
    data_dir: Path | None = typer.Option(None, "--data-dir"),  # noqa: B008
) -> None:
    manager = _codex_manager()
    if check:
        _show({"connected": manager.inspect() is not None})
        return
    prompt = "Register Mnemo with Codex"
    if auto_memory:
        prompt += " and enable automatic task memory for this project"
    if not yes and not dry_run and not typer.confirm(f"{prompt}?"):
        raise typer.Abort()
    result = manager.connect(dry_run=dry_run)
    if auto_memory and not dry_run:
        result.update(_enable_automatic_task_memory("codex", project_dir, data_dir))
    _show(result) if json_output else typer.echo(result["status"])


@connect_app.command(
    "claude-code", help="Register the installed Mnemo MCP launcher with Claude Code."
)
def connect_claude_code(
    check: bool = False,
    dry_run: bool = False,
    yes: bool = False,
    auto_memory: bool = typer.Option(
        False,
        "--auto-memory",
        help="Also enable automatic task handoffs for the current project.",
    ),
    project_dir: Path = typer.Option(Path("."), "--project-dir"),  # noqa: B008
    data_dir: Path | None = typer.Option(None, "--data-dir"),  # noqa: B008
) -> None:
    manager = _claude_manager()
    if check:
        _show({"connected": manager.inspect() is not None})
        return
    prompt = "Register Mnemo with Claude Code"
    if auto_memory:
        prompt += " and enable automatic task memory for this project"
    if not yes and not dry_run and not typer.confirm(f"{prompt}?"):
        raise typer.Abort()
    result = manager.connect(dry_run=dry_run)
    if auto_memory and not dry_run:
        result.update(_enable_automatic_task_memory("claude-code", project_dir, data_dir))
    typer.echo(result["status"])


@disconnect_app.command("codex", help="Remove the Mnemo MCP registration from Codex.")
def disconnect_codex(
    dry_run: bool = typer.Option(False, "--dry-run"),
    yes: bool = typer.Option(False, "--yes"),
) -> None:
    manager = _codex_manager()
    if not yes and not dry_run and not typer.confirm("Disconnect Mnemo from Codex?"):
        raise typer.Abort()
    typer.echo(manager.disconnect(dry_run=dry_run)["status"])


@disconnect_app.command("claude-code", help="Remove the Mnemo MCP registration from Claude Code.")
def disconnect_claude_code(dry_run: bool = False, yes: bool = False) -> None:
    manager = _claude_manager()
    if not yes and not dry_run and not typer.confirm("Disconnect Mnemo from Claude Code?"):
        raise typer.Abort()
    typer.echo(manager.disconnect(dry_run=dry_run)["status"])


if __name__ == "__main__":
    app()
