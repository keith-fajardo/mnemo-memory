import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from typer.testing import CliRunner

from mnemo_memory.apps.api.app import create_app
from mnemo_memory.apps.cli.main import app
from mnemo_memory.packages.application import LocalConfig, build_lifecycle_service
from mnemo_memory.packages.application.services import LifecycleService


def service(tmp_path: Path) -> LifecycleService:
    config = LocalConfig.defaults(tmp_path / "mnemo-memory")
    return build_lifecycle_service(config)


def test_init_is_idempotent_and_creates_restrictive_local_state(tmp_path: Path) -> None:
    value = service(tmp_path)
    first = value.initialize()
    second = value.initialize()

    assert first["initialized"] is True
    assert second["initialized"] is False
    assert first["schema_version"] == 3
    assert value.config.config_path.exists()
    assert value.config.database_path.exists()


def test_configuration_is_strict_loopback_only_and_path_safe(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="loopback"):
        LocalConfig(tmp_path, tmp_path / "db.sqlite", host="0.0.0.0")
    with pytest.raises(ValueError, match="escapes"):
        LocalConfig(tmp_path, Path("../db.sqlite"))
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"unknown": True}))
    with pytest.raises(ValueError, match="unknown"):
        LocalConfig.load(config_path)


def test_lifecycle_api_exposes_only_local_readiness_endpoints(tmp_path: Path) -> None:
    value = service(tmp_path)
    value.initialize()
    app = create_app(value)
    routes = {route.path: route.endpoint for route in app.routes if isinstance(route, APIRoute)}

    assert routes["/live"]() == {"status": "live"}
    assert routes["/ready"]()["status"] == "ready"
    assert routes["/version"]()["profile"] == "personal"
    assert app.docs_url is None
    assert app.redoc_url is None


def test_cli_init_and_status_use_isolated_data_directory(tmp_path: Path) -> None:
    data_dir = tmp_path / "cli-state"
    init = subprocess.run(
        [sys.executable, "-m", "mnemo_memory.cli", "init", "--data-dir", str(data_dir)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert init.returncode == 0, init.stderr
    assert json.loads(init.stdout)["initialized"] is True
    status = subprocess.run(
        [sys.executable, "-m", "mnemo_memory.cli", "status", "--data-dir", str(data_dir)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["initialized"] is True


def test_cli_help_explains_top_level_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Initialize Mnemo's local data directory and SQLite database." in result.output
    assert "Register Mnemo with an AI coding client." in result.output
    assert "Ingest and inspect offline dbt manifests." in result.output


def test_interactive_guide_explains_explicit_memory_and_requires_confirmation(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "guide store"
    result = CliRunner().invoke(app, ["guide", "--data-dir", str(data_dir)], input="n\nboth\n")

    assert result.exit_code == 0
    assert "explicit task checkpoints, not an automatic chat or directory history" in result.output
    assert "Store status: not initialized" in result.output
    assert "mnemo-memory connect codex" in result.output
    assert "mnemo-memory connect claude-code" in result.output
    assert not data_dir.exists()


def test_non_interactive_guide_never_initializes_or_registers_clients(tmp_path: Path) -> None:
    data_dir = tmp_path / "guide store"
    result = CliRunner().invoke(app, ["guide", "--data-dir", str(data_dir), "--non-interactive"])

    assert result.exit_code == 0
    assert "mnemo-memory connect codex" in result.output
    assert "mnemo-memory connect claude-code" in result.output
    assert not data_dir.exists()


def test_agent_is_an_interactive_guide_alias(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app, ["agent", "--data-dir", str(tmp_path / "agent store"), "--non-interactive"]
    )

    assert result.exit_code == 0
    assert "Mnemo Memory setup guide" in result.output


def test_guide_initializes_only_when_explicitly_requested(tmp_path: Path) -> None:
    data_dir = tmp_path / "guide store"
    result = CliRunner().invoke(
        app, ["guide", "--data-dir", str(data_dir), "--initialize", "--non-interactive"]
    )

    assert result.exit_code == 0
    initialization = next(line for line in result.output.splitlines() if line.startswith("{"))
    assert json.loads(initialization)["initialized"] is True
    assert (data_dir / "config.json").exists()
