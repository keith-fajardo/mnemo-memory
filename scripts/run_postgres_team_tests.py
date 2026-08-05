"""Run the team RLS suite against an isolated real PostgreSQL server."""

from __future__ import annotations

import getpass
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[1]
TEST_PATH = "tests/integration/test_postgres_team_control_plane.py"
REQUIRED_SETTINGS = (
    "MNEMO_TEST_POSTGRES_HOST",
    "MNEMO_TEST_POSTGRES_PORT",
    "MNEMO_TEST_POSTGRES_ADMIN_USER",
)


class PostgreSQLTestHarnessError(RuntimeError):
    """The required real-database security suite could not be executed."""


def _run(
    command: Sequence[str],
    *,
    environment: Mapping[str, str] | None = None,
    timeout: float = 180.0,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        tuple(command),
        cwd=REPOSITORY_ROOT,
        env=None if environment is None else dict(environment),
        check=False,
        text=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise PostgreSQLTestHarnessError(
            f"command failed with exit code {completed.returncode}: {command[0]}"
        )
    return completed


def _postgres_binary(name: str) -> str:
    direct = shutil.which(name)
    if direct is not None:
        return direct
    pg_config = shutil.which("pg_config")
    if pg_config is not None:
        result = subprocess.run(
            (pg_config, "--bindir"),
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            candidate = Path(result.stdout.strip()) / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    raise PostgreSQLTestHarnessError(f"required PostgreSQL binary is unavailable: {name}")


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _run_pytest(environment: Mapping[str, str]) -> None:
    _run(
        (sys.executable, "-m", "pytest", "-q", TEST_PATH),
        environment=environment,
    )


def run_checks() -> None:
    environment = dict(os.environ)
    configured = [bool(environment.get(name)) for name in REQUIRED_SETTINGS]
    if any(configured):
        if not all(configured):
            raise PostgreSQLTestHarnessError(
                "all MNEMO_TEST_POSTGRES_* settings are required when one is supplied"
            )
        _run_pytest(environment)
        return

    initdb = _postgres_binary("initdb")
    pg_ctl = _postgres_binary("pg_ctl")
    port = _available_port()
    admin_user = getpass.getuser()
    with tempfile.TemporaryDirectory(prefix="mnemo-postgres-test-") as temporary:
        root = Path(temporary)
        data_directory = root / "data"
        socket_directory = root / "socket"
        log_path = root / "postgres.log"
        socket_directory.mkdir(mode=0o700)
        _run(
            (
                initdb,
                "--pgdata",
                str(data_directory),
                "--username",
                admin_user,
                "--auth",
                "trust",
                "--encoding",
                "UTF8",
                "--no-locale",
                "--no-sync",
            )
        )
        started = False
        try:
            _run(
                (
                    pg_ctl,
                    "--pgdata",
                    str(data_directory),
                    "--log",
                    str(log_path),
                    "--options",
                    f"-h 127.0.0.1 -p {port} -k {socket_directory}",
                    "--wait",
                    "start",
                )
            )
            started = True
            environment.update(
                {
                    "MNEMO_TEST_POSTGRES_HOST": "127.0.0.1",
                    "MNEMO_TEST_POSTGRES_PORT": str(port),
                    "MNEMO_TEST_POSTGRES_ADMIN_USER": admin_user,
                }
            )
            _run_pytest(environment)
        finally:
            if started:
                _run(
                    (pg_ctl, "--pgdata", str(data_directory), "--wait", "stop"),
                    timeout=60,
                )


def main() -> None:
    run_checks()
    print("PostgreSQL team migration and row-level-security checks passed.")


if __name__ == "__main__":
    main()
