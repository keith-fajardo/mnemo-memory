"""Mnemo lifecycle CLI."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import typer

from connectors.claude_code.mcp_config import ClaudeMcpManager
from connectors.codex.mcp_config import CodexMcpManager
from packages.application import build_lifecycle_service, resolve_local_config
from packages.application.services import LifecycleService

app = typer.Typer(no_args_is_help=True, add_completion=False)
mcp_app = typer.Typer(no_args_is_help=True)
app.add_typer(mcp_app, name="mcp")
connect_app = typer.Typer(no_args_is_help=True)
disconnect_app = typer.Typer(no_args_is_help=True)
app.add_typer(connect_app, name="connect")
app.add_typer(disconnect_app, name="disconnect")


def _service(data_dir: Path | None) -> LifecycleService:
    return build_lifecycle_service(resolve_local_config(data_dir))


def _show(value: object) -> None:
    typer.echo(json.dumps(value, sort_keys=True))


@app.command()
def init(data_dir: Path | None = typer.Option(None, "--data-dir")) -> None:  # noqa: B008
    _show(_service(data_dir).initialize())


@app.command()
def start(data_dir: Path | None = typer.Option(None, "--data-dir")) -> None:  # noqa: B008
    _show(_service(data_dir).start())


@app.command()
def status(data_dir: Path | None = typer.Option(None, "--data-dir")) -> None:  # noqa: B008
    _show(_service(data_dir).status())


@app.command()
def stop(data_dir: Path | None = typer.Option(None, "--data-dir")) -> None:  # noqa: B008
    _show(_service(data_dir).stop())


@mcp_app.command("serve")
def mcp_serve(stdio: bool = typer.Option(False, "--stdio")) -> None:
    if not stdio:
        raise typer.BadParameter("Issue 7 supports only --stdio")
    os.execv(sys.executable, [sys.executable, "-m", "apps.mcp.server"])


def _codex_manager() -> CodexMcpManager:
    launcher = shutil.which("mnemo")
    if launcher is None:
        raise typer.BadParameter("MNEMO_LAUNCHER_NOT_RESOLVABLE: install Mnemo before connecting")
    return CodexMcpManager.discover(Path(launcher).resolve())


def _claude_manager() -> ClaudeMcpManager:
    launcher = shutil.which("mnemo")
    if launcher is None:
        raise typer.BadParameter("MNEMO_LAUNCHER_NOT_RESOLVABLE: install Mnemo before connecting")
    return ClaudeMcpManager.discover(Path(launcher).resolve())


@connect_app.command("codex")
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


@connect_app.command("claude-code")
def connect_claude_code(check: bool = False, dry_run: bool = False, yes: bool = False) -> None:
    manager = _claude_manager()
    if check:
        _show({"connected": manager.inspect() is not None})
        return
    if not yes and not dry_run and not typer.confirm("Register Mnemo with Claude Code?"):
        raise typer.Abort()
    typer.echo(manager.connect(dry_run=dry_run)["status"])


@disconnect_app.command("codex")
def disconnect_codex(
    dry_run: bool = typer.Option(False, "--dry-run"),
    yes: bool = typer.Option(False, "--yes"),
) -> None:
    manager = _codex_manager()
    if not yes and not dry_run and not typer.confirm("Disconnect Mnemo from Codex?"):
        raise typer.Abort()
    typer.echo(manager.disconnect(dry_run=dry_run)["status"])


@disconnect_app.command("claude-code")
def disconnect_claude_code(dry_run: bool = False, yes: bool = False) -> None:
    manager = _claude_manager()
    if not yes and not dry_run and not typer.confirm("Disconnect Mnemo from Claude Code?"):
        raise typer.Abort()
    typer.echo(manager.disconnect(dry_run=dry_run)["status"])


if __name__ == "__main__":
    app()
