import asyncio
import json
import os
import shutil
import subprocess
import sys
import unicodedata
from datetime import timedelta
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from typer.testing import CliRunner

from mnemo_memory.apps.cli import main as cli
from mnemo_memory.connectors.codex.mcp_config import SERVER_NAME, CodexMcpManager


def completed(
    command: list[str], code: int = 0, stdout: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, code, stdout, "")


def test_connect_registers_exact_argument_array_and_reads_it_back(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    state: dict[str, object] = {}
    launcher = tmp_path / "Mnemo With Spaces" / "mnemo-memory"
    launcher.parent.mkdir()
    launcher.touch()

    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[2] == "get":
            return completed(command, 0 if state else 1, json.dumps(state))
        if command[2] == "add":
            state.update({"command": command[5], "args": command[6:]})
        return completed(command)

    manager = CodexMcpManager("/fake/codex", launcher, run)
    assert manager.connect() == {"status": "connected", "changed": True, "server": SERVER_NAME}
    assert calls[1] == [
        "/fake/codex",
        "mcp",
        "add",
        SERVER_NAME,
        "--",
        str(launcher),
        "mcp",
        "serve",
        "--stdio",
    ]
    assert manager.connect()["changed"] is False


def test_conflicting_or_unrecognized_entry_is_never_replaced_or_removed(tmp_path: Path) -> None:
    launcher = tmp_path / "mnemo-memory"
    launcher.touch()
    entry = {"command": "/other", "args": ["mcp", "serve", "--stdio"]}
    manager = CodexMcpManager(
        "/fake/codex", launcher, lambda command, **_: completed(command, 0, json.dumps(entry))
    )
    with pytest.raises(ValueError, match="MNEMO_CODEX_CONFLICT"):
        manager.connect()
    with pytest.raises(ValueError, match="MNEMO_CODEX_UNRECOGNIZED_ENTRY"):
        manager.disconnect()


def test_owned_launcher_readback_normalizes_unicode_and_resolved_paths(tmp_path: Path) -> None:
    launcher = tmp_path / "Mnemo Δ" / "mnemo-memory"
    launcher.parent.mkdir()
    launcher.touch()
    stored_path = unicodedata.normalize("NFD", str(launcher.resolve()))
    manager = CodexMcpManager("/fake/codex", launcher)
    assert manager.is_owned(
        {"transport": {"command": stored_path, "args": ["mcp", "serve", "--stdio"]}}
    )


@pytest.mark.skipif(shutil.which("codex") is None, reason="codex CLI is unavailable")
def test_real_codex_registration_is_isolated_and_reversible(tmp_path: Path) -> None:
    codex_home = tmp_path / "isolated codex"
    launcher = tmp_path / "Mnemo Launcher" / "mnemo-memory"
    launcher.parent.mkdir()
    launcher.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "os.execv(sys.executable, [sys.executable, '-m', 'mnemo_memory.cli', *sys.argv[1:]])\n"
    )
    launcher.chmod(0o700)
    environment = {
        **os.environ,
        "CODEX_HOME": str(codex_home),
        "MNEMO_DATA_DIR": str(tmp_path / "mnemo-memory data"),
    }
    manager = CodexMcpManager(shutil.which("codex") or "codex", launcher, environment=environment)
    codex_home.mkdir()
    (codex_home / "config.toml").write_text("[feedback]\nenabled = false\n")

    assert manager.connect()["changed"] is True
    stored = manager.inspect()
    assert stored is not None and manager.is_owned(stored)
    transport = stored["transport"]
    assert isinstance(transport, dict)

    async def smoke_test() -> None:
        command = transport["command"]
        arguments = transport["args"]
        assert isinstance(command, str)
        assert isinstance(arguments, list)
        parameters = StdioServerParameters(command=command, args=arguments, env=environment)
        async with stdio_client(parameters) as (read, write), ClientSession(read, write) as session:
            initialized = await session.initialize()
            assert initialized.serverInfo.name == "mnemo-local"
            assert [tool.name for tool in (await session.list_tools()).tools] == [
                "get_context",
                "list_skills",
                "get_skill",
                "explain_context",
                "save_checkpoint",
                "structural_lookup",
            ]
            saved = await session.call_tool(
                "save_checkpoint", _durable_payload(), read_timeout_seconds=timedelta(seconds=5)
            )
            assert saved.isError is False and isinstance(saved.structuredContent, dict)
            checkpoint_id = saved.structuredContent["checkpoint_id"]

        # Re-open the exact read-back launcher in an independent stdio process.
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

    asyncio.run(asyncio.wait_for(smoke_test(), timeout=10))
    listed = subprocess.run(
        [manager.codex_executable, "mcp", "list", "--json"],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert SERVER_NAME in {item["name"] for item in json.loads(listed.stdout)}
    assert (codex_home / "config.toml").read_text().startswith("[feedback]\nenabled = false")
    assert manager.disconnect()["changed"] is True
    assert manager.inspect() is None


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


class FakeManager:
    def __init__(self, existing: object | None = None) -> None:
        self.existing = existing
        self.connect_calls = 0
        self.disconnect_calls = 0

    def inspect(self) -> object | None:
        return self.existing

    def connect(self, dry_run: bool = False) -> dict[str, object]:
        self.connect_calls += 1
        return {"status": "dry-run" if dry_run else "connected", "changed": not dry_run}

    def disconnect(self, dry_run: bool = False) -> dict[str, object]:
        self.disconnect_calls += 1
        return {"status": "dry-run" if dry_run else "disconnected", "changed": not dry_run}


@pytest.mark.parametrize(
    ("command", "input_value", "expected_exit", "connect_calls", "disconnect_calls"),
    [
        (["connect", "codex"], "", 0, 1, 0),
        (["connect", "codex", "--confirm"], "y\n", 0, 1, 0),
        (["connect", "codex", "--confirm"], "n\n", 1, 0, 0),
        (["disconnect", "codex"], "y\n", 0, 0, 1),
        (["disconnect", "codex"], "n\n", 1, 0, 0),
        (["connect", "codex", "--yes"], "", 0, 1, 0),
        (["disconnect", "codex", "--yes"], "", 0, 0, 1),
        (["connect", "codex", "--dry-run"], "", 0, 1, 0),
        (["connect", "codex", "--check"], "", 0, 0, 0),
        (["connect", "codex", "--confirm"], "", 1, 0, 0),
    ],
)
def test_confirmation_flow_is_bounded_and_deterministic(
    monkeypatch: pytest.MonkeyPatch,
    command: list[str],
    input_value: str,
    expected_exit: int,
    connect_calls: int,
    disconnect_calls: int,
) -> None:
    manager = FakeManager()
    monkeypatch.setattr(cli, "_codex_manager", lambda: manager)
    monkeypatch.setattr(
        cli,
        "_enable_automatic_task_memory",
        lambda client, project_dir, data_dir: {"automatic_memory": True},
    )
    result = CliRunner().invoke(cli.app, command, input=input_value)
    assert result.exit_code == expected_exit
    assert manager.connect_calls == connect_calls
    assert manager.disconnect_calls == disconnect_calls


def test_codex_connection_enables_automatic_memory_by_default_with_opt_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = FakeManager()
    enabled: list[str] = []
    monkeypatch.setattr(cli, "_codex_manager", lambda: manager)

    def enable(client: str, project_dir: object, data_dir: object) -> dict[str, object]:
        enabled.append(client)
        return {"automatic_memory": True}

    monkeypatch.setattr(
        cli,
        "_enable_automatic_task_memory",
        enable,
    )
    runner = CliRunner()

    default = runner.invoke(cli.app, ["connect", "codex"])
    disabled = runner.invoke(cli.app, ["connect", "codex", "--auto-memory-disable"])

    assert default.exit_code == 0, default.output
    assert disabled.exit_code == 0, disabled.output
    assert enabled == ["codex"]


def test_codex_default_confirmation_discloses_automatic_project_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = FakeManager()
    monkeypatch.setattr(cli, "_codex_manager", lambda: manager)

    result = CliRunner().invoke(cli.app, ["connect", "codex", "--confirm"], input="n\n")

    assert result.exit_code == 1
    assert "enable automatic task memory for this project" in result.output
    assert manager.connect_calls == 0


def test_codex_discovery_validates_launcher_before_external_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("mnemo_memory.connectors.codex.mcp_config.shutil.which", lambda _: None)

    with pytest.raises(ValueError, match="MNEMO_LAUNCHER_NOT_ABSOLUTE"):
        CodexMcpManager.discover(Path("relative"))
    with pytest.raises(ValueError, match="MNEMO_CODEX_NOT_INSTALLED"):
        CodexMcpManager.discover(tmp_path / "mnemo-memory")
