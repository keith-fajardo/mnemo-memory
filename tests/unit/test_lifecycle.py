import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

from apps.api.app import create_app
from packages.application import LocalConfig, build_lifecycle_service
from packages.application.services import LifecycleService


def service(tmp_path: Path) -> LifecycleService:
    config = LocalConfig.defaults(tmp_path / "mnemo")
    return build_lifecycle_service(config)


def test_init_is_idempotent_and_creates_restrictive_local_state(tmp_path: Path) -> None:
    value = service(tmp_path)
    first = value.initialize()
    second = value.initialize()

    assert first["initialized"] is True
    assert second["initialized"] is False
    assert first["schema_version"] == 2
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
        [sys.executable, "-m", "apps.cli.main", "init", "--data-dir", str(data_dir)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert init.returncode == 0, init.stderr
    assert json.loads(init.stdout)["initialized"] is True
    status = subprocess.run(
        [sys.executable, "-m", "apps.cli.main", "status", "--data-dir", str(data_dir)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["initialized"] is True
