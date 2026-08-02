"""Mnemo lifecycle CLI."""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

import typer

from mnemo_memory.connectors.claude_code.mcp_config import ClaudeMcpManager
from mnemo_memory.connectors.codex.mcp_config import CodexMcpManager
from mnemo_memory.connectors.dbt.manifest import DbtManifestParser
from mnemo_memory.packages.application import (
    GetActiveManifestStatus,
    IngestManifest,
    build_checkpoint_runtime,
    build_lifecycle_service,
    resolve_local_config,
)
from mnemo_memory.packages.application.services import LifecycleService
from mnemo_memory.packages.domain import (
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    Visibility,
    WorkspaceId,
)

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Local-first durable task checkpoints and dbt lineage context.",
)
mcp_app = typer.Typer(no_args_is_help=True, help="Run the local MCP server.")
app.add_typer(mcp_app, name="mcp", help="Run the local MCP server.")
connect_app = typer.Typer(no_args_is_help=True, help="Register Mnemo with an AI coding client.")
disconnect_app = typer.Typer(no_args_is_help=True, help="Remove a client registration.")
dbt_app = typer.Typer(no_args_is_help=True, help="Ingest and inspect offline dbt manifests.")
app.add_typer(connect_app, name="connect", help="Register Mnemo with an AI coding client.")
app.add_typer(disconnect_app, name="disconnect", help="Remove a client registration.")
app.add_typer(dbt_app, name="dbt", help="Ingest and inspect offline dbt manifests.")


def _service(data_dir: Path | None) -> LifecycleService:
    return build_lifecycle_service(resolve_local_config(data_dir))


def _show(value: object) -> None:
    typer.echo(json.dumps(value, sort_keys=True))


def _guide_client_commands(choice: str) -> tuple[str, ...]:
    commands = {
        "codex": ("mnemo-memory connect codex",),
        "claude-code": ("mnemo-memory connect claude-code",),
        "both": ("mnemo-memory connect codex", "mnemo-memory connect claude-code"),
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
            "Run the following command(s) when you are ready; each asks before changing "
            "client configuration:"
        )
        for command in commands:
            typer.echo(f"  {command}")
    else:
        typer.echo("Client registration deferred. You can return with mnemo-memory guide.")
    typer.echo(
        "\nBefore ending work, ask the connected agent to save a Mnemo checkpoint. "
        "In a fresh session, ask it to retrieve Mnemo context before continuing."
    )
    typer.echo(
        "Optional dbt lineage: run mnemo-memory dbt ingest target/manifest.json with your "
        "scope IDs."
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


@dbt_app.command("ingest", help="Validate and activate a local manifest.json without running dbt.")
def dbt_ingest(
    manifest: Path,
    owner_id: str = typer.Option(...),
    workspace_id: str = typer.Option(...),
    project_id: str = typer.Option(...),
    data_dir: Path | None = typer.Option(None, "--data-dir"),  # noqa: B008
    dry_run: bool = typer.Option(False, "--dry-run"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Validate and atomically activate a local dbt manifest without executing dbt."""
    try:
        raw = manifest.read_bytes()
        scope = _project_scope(owner_id, workspace_id, project_id)
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


@dbt_app.command("status", help="Show the active local dbt manifest snapshot for a scope.")
def dbt_status(
    owner_id: str = typer.Option(...),
    workspace_id: str = typer.Option(...),
    project_id: str = typer.Option(...),
    data_dir: Path | None = typer.Option(None, "--data-dir"),  # noqa: B008
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    scope = _project_scope(owner_id, workspace_id, project_id)
    with build_checkpoint_runtime(
        resolve_local_config(data_dir), dbt_parser=DbtManifestParser()
    ) as runtime:
        assert runtime.dbt_manifest_service is not None
        status = runtime.dbt_manifest_service.get_active_status(GetActiveManifestStatus(scope))
    result: dict[str, object] = {
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


@connect_app.command("codex", help="Register the installed Mnemo MCP launcher with Codex.")
def connect_codex(
    check: bool = typer.Option(False, "--check"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    yes: bool = typer.Option(False, "--yes"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    manager = _codex_manager()
    if check:
        _show({"connected": manager.inspect() is not None})
        return
    if not yes and not dry_run and not typer.confirm("Register Mnemo with Codex?"):
        raise typer.Abort()
    result = manager.connect(dry_run=dry_run)
    _show(result) if json_output else typer.echo(result["status"])


@connect_app.command(
    "claude-code", help="Register the installed Mnemo MCP launcher with Claude Code."
)
def connect_claude_code(check: bool = False, dry_run: bool = False, yes: bool = False) -> None:
    manager = _claude_manager()
    if check:
        _show({"connected": manager.inspect() is not None})
        return
    if not yes and not dry_run and not typer.confirm("Register Mnemo with Claude Code?"):
        raise typer.Abort()
    typer.echo(manager.connect(dry_run=dry_run)["status"])


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
