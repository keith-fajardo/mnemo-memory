import asyncio
import json
import os
import shutil
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from typer.testing import CliRunner

from apps.cli import main as cli
from connectors.claude_code.mcp_config import SERVER_NAME, ClaudeMcpManager


def done(command: list[str], code: int = 0, output: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, code, output, "")


def test_claude_user_scope_registration_is_idempotent_and_conflict_safe(tmp_path: Path) -> None:
    launcher = tmp_path / "launcher with spaces" / "mnemo"
    launcher.parent.mkdir()
    launcher.touch()
    state = ""
    calls: list[list[str]] = []

    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        nonlocal state
        calls.append(command)
        if command[2] == "get":
            return done(command, 0 if state else 1, state)
        if command[2] == "add":
            state = f"{launcher} mcp serve --stdio user"
        return done(command)

    manager = ClaudeMcpManager("/fake/claude", launcher, run)
    assert manager.connect()["changed"] is True
    assert calls[1] == [
        "/fake/claude",
        "mcp",
        "add",
        "--scope",
        "user",
        SERVER_NAME,
        "--",
        str(launcher),
        "mcp",
        "serve",
        "--stdio",
    ]
    assert manager.connect()["changed"] is False
    with pytest.raises(ValueError, match="MNEMO_CLAUDE_UNRECOGNIZED_ENTRY"):
        ClaudeMcpManager(
            "/fake/claude", launcher, lambda command, **_: done(command, 0, "other")
        ).disconnect()


@pytest.mark.skipif(shutil.which("claude") is None, reason="Claude Code CLI is unavailable")
def test_real_claude_registration_and_registered_launcher_smoke(tmp_path: Path) -> None:
    home = tmp_path / "Claude Home With Spaces"
    launcher = tmp_path / "Mnemo Launcher With Spaces" / "mnemo"
    home.mkdir()
    launcher.parent.mkdir()
    launcher.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "os.execv(sys.executable, [sys.executable, '-m', 'apps.cli.main', *sys.argv[1:]])\n"
    )
    launcher.chmod(0o700)
    config = home / ".claude.json"
    config.write_text(json.dumps({"unrelated": True, "mcpServers": {"other": {}}}))
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("ANTHROPIC_")
    }
    environment["HOME"] = str(home)
    environment["MNEMO_DATA_DIR"] = str(tmp_path / "mnemo data")
    manager = ClaudeMcpManager(
        shutil.which("claude") or "claude", launcher, environment=environment
    )
    assert manager.connect()["changed"] is True
    detail = manager.inspect()
    assert detail is not None and manager.is_owned(detail)
    entry = json.loads(config.read_text())["mcpServers"][SERVER_NAME]
    assert entry["type"] == "stdio" and entry["command"] == str(launcher)
    assert entry["args"] == ["mcp", "serve", "--stdio"]

    async def smoke() -> None:
        parameters = StdioServerParameters(
            command=entry["command"], args=entry["args"], env=environment
        )
        async with stdio_client(parameters) as (read, write), ClientSession(read, write) as session:
            initialized = await session.initialize()
            assert initialized.serverInfo.name == "mnemo-local"
            assert [tool.name for tool in (await session.list_tools()).tools] == [
                "get_context",
                "save_checkpoint",
            ]
            saved = await session.call_tool(
                "save_checkpoint", _durable_payload(), read_timeout_seconds=timedelta(seconds=5)
            )
            assert saved.isError is False and isinstance(saved.structuredContent, dict)
            checkpoint_id = saved.structuredContent["checkpoint_id"]

        # Use the exact user-scope launcher stored by Claude Code after a restart.
        async with stdio_client(parameters) as (read, write), ClientSession(read, write) as session:
            initialized = await session.initialize()
            assert initialized.serverInfo.name == "mnemo-local"
            context = await session.call_tool(
                "get_context",
                _durable_scope(),
                read_timeout_seconds=timedelta(seconds=5),
            )
            assert context.isError is False and isinstance(context.structuredContent, dict)
            assert checkpoint_id in json.dumps(context.structuredContent)

    asyncio.run(asyncio.wait_for(smoke(), timeout=10))
    assert manager.connect()["changed"] is False
    assert manager.disconnect()["changed"] is True
    remaining = json.loads(config.read_text())
    assert SERVER_NAME not in remaining["mcpServers"]
    assert remaining["unrelated"] is True and "other" in remaining["mcpServers"]


def _durable_scope() -> dict[str, object]:
    return {
        "owner_id": "11111111-1111-4111-8111-111111111111",
        "workspace_id": "22222222-2222-4222-8222-222222222222",
        "project_id": "33333333-3333-4333-8333-333333333333",
        "session_id": "44444444-4444-4444-8444-444444444444",
        "task_id": "55555555-5555-4555-8555-555555555555",
    }


def _durable_payload() -> dict[str, object]:
    return {
        **_durable_scope(),
        "operation": "create",
        "task_objective": "Connector smoke fixture",
        "current_state": "active",
        "evidence_references": [
            {
                "evidence_id": "66666666-6666-4666-8666-666666666666",
                "source_id": "77777777-7777-4777-8777-777777777777",
                "source_type": "checkpoint",
                "trust_class": "user_authored",
                "immutable_source_ref": "synthetic://connector",
                "content_hash": "sha256:" + "a" * 64,
                "location": {
                    "uri": "fixture://connector",
                    "start_line": None,
                    "start_column": None,
                    "end_line": None,
                    "end_column": None,
                },
                "observed_at": "2026-08-02T14:00:00+00:00",
                "verification_status": "verified",
            }
        ],
        "token_estimate": 10,
    }


class FakeClaudeManager:
    def __init__(self, existing: object | None = None) -> None:
        self.existing = existing
        self.adds = 0
        self.removes = 0

    def inspect(self) -> object | None:
        return self.existing

    def connect(self, dry_run: bool = False) -> dict[str, object]:
        self.adds += 1
        return {"status": "dry-run" if dry_run else "connected"}

    def disconnect(self, dry_run: bool = False) -> dict[str, object]:
        self.removes += 1
        return {"status": "dry-run" if dry_run else "disconnected"}


@pytest.mark.parametrize(
    ("command", "input_value", "exit_code", "adds", "removes", "output"),
    [
        (["connect", "claude-code"], "y\n", 0, 1, 0, "connected"),
        (["connect", "claude-code"], "n\n", 1, 0, 0, "Aborted"),
        (["disconnect", "claude-code"], "y\n", 0, 0, 1, "disconnected"),
        (["disconnect", "claude-code"], "n\n", 1, 0, 0, "Aborted"),
        (["connect", "claude-code", "--yes"], "", 0, 1, 0, "connected"),
        (["disconnect", "claude-code", "--yes"], "", 0, 0, 1, "disconnected"),
        (["connect", "claude-code", "--dry-run"], "", 0, 1, 0, "dry-run"),
        (["connect", "claude-code", "--check"], "", 0, 0, 0, "connected"),
        (["connect", "claude-code"], "", 1, 0, 0, "Aborted"),
        (["disconnect", "claude-code"], "", 1, 0, 0, "Aborted"),
    ],
)
def test_claude_cli_confirmation_matrix_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
    command: list[str],
    input_value: str,
    exit_code: int,
    adds: int,
    removes: int,
    output: str,
) -> None:
    manager = FakeClaudeManager(existing={"owned": True})
    monkeypatch.setattr(cli, "_claude_manager", lambda: manager)
    result = CliRunner().invoke(cli.app, command, input=input_value)
    assert result.exit_code == exit_code
    assert output in result.output
    assert manager.adds == adds and manager.removes == removes


def test_claude_manager_failure_cases_are_safe(tmp_path: Path) -> None:
    launcher = tmp_path / "mnemo path with spaces"
    launcher.touch()
    with pytest.raises(ValueError, match="MNEMO_LAUNCHER_NOT_ABSOLUTE"):
        ClaudeMcpManager.discover(Path("relative"))
    manager = ClaudeMcpManager("/fake/claude", launcher, lambda command, **_: done(command, 1))
    assert manager.connect(dry_run=True)["status"] == "dry-run"
    with pytest.raises(ValueError, match="MNEMO_CLAUDE_REGISTRATION_FAILED"):
        manager.connect()
