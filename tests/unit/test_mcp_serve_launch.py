"""``mcp serve`` starts the server with ``-P``, so the working directory never shadows Mnemo."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mnemo_memory.apps.cli.main import app

runner = CliRunner()


def _serve(monkeypatch: pytest.MonkeyPatch, *extra: str) -> tuple[str, list[str]]:
    seen: list[tuple[str, list[str]]] = []

    def fake_execv(path: str, argv: list[str]) -> None:
        seen.append((path, argv))

    monkeypatch.setattr(os, "execv", fake_execv)
    result = runner.invoke(app, ["mcp", "serve", "--stdio", *extra])
    assert result.exit_code == 0
    [(path, argv)] = seen
    return path, argv


def test_mcp_serve_starts_the_server_with_p(monkeypatch: pytest.MonkeyPatch) -> None:
    path, argv = _serve(monkeypatch)
    assert path == sys.executable
    assert argv == [sys.executable, "-P", "-m", "mnemo_memory.apps.mcp.server"]


def test_mcp_serve_passes_the_data_directory_and_profile_after_the_module(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, argv = _serve(monkeypatch, "--data-dir", str(tmp_path), "--profile", "compact")
    assert argv == [
        sys.executable,
        "-P",
        "-m",
        "mnemo_memory.apps.mcp.server",
        "--data-dir",
        str(tmp_path),
        "--profile",
        "compact",
    ]
