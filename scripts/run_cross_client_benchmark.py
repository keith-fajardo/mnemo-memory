"""Prove model-free checkpoint transport between isolated Codex and Claude launchers."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from mnemo_memory.connectors.codex.mcp_config import CodexMcpManager
from mnemo_memory.packages.domain import ContextPacket
from scripts.run_resumption_benchmark import (
    build_checkpoint_packet,
    evaluate,
    load_fixture,
    score_context_packet,
)

ROOT = Path(__file__).parents[1]
SERVER_NAME = "mnemo-memory"


def _without_anthropic_environment(base: dict[str, str]) -> dict[str, str]:
    return {key: value for key, value in base.items() if not key.startswith("ANTHROPIC_")}


def _launcher(root: Path) -> Path:
    launcher = root / "launcher with spaces Δ" / "mnemo-memory"
    launcher.parent.mkdir(parents=True)
    launcher.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "os.execv(sys.executable, [sys.executable, '-m', 'mnemo_memory.cli', *sys.argv[1:]])\n"
    )
    launcher.chmod(0o700)
    return launcher


def _register(
    root: Path, data_directory: Path
) -> tuple[dict[str, str], list[str], list[str], dict[str, str]]:
    """Register through the real client CLIs, then return their exact stored launchers."""
    launcher = _launcher(root)
    codex_home = root / "Codex Home Δ"
    claude_home = root / "Claude Home Δ"
    project = root / "Project With Spaces"
    codex_home.mkdir()
    claude_home.mkdir()
    project.mkdir()
    (codex_home / "config.toml").write_text("[feedback]\nenabled = false\n")
    (claude_home / ".claude.json").write_text(
        json.dumps({"unrelated": True, "mcpServers": {"other": {}}})
    )
    environment = _without_anthropic_environment(dict(os.environ))
    environment.update(
        {
            "CODEX_HOME": str(codex_home),
            "HOME": str(claude_home),
            "MNEMO_DATA_DIR": str(data_directory),
            "PATH": f"{launcher.parent}{os.pathsep}{environment.get('PATH', '')}",
        }
    )
    # Use Mnemo's actual shorthand workflow from the isolated project; its discovered
    # `mnemo-memory` MCP launcher is the executable above.
    for client in ("codex", "claude-code"):
        result = subprocess.run(
            [sys.executable, "-m", "mnemo_memory.cli", "connect", client],
            cwd=project,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if result.returncode != 0:
            raise RuntimeError(f"isolated {client} registration failed: {result.stderr[-200:]}")
    codex = CodexMcpManager(shutil.which("codex") or "codex", launcher, environment=environment)
    stored = codex.inspect()
    if stored is None or not codex.is_owned(stored):
        raise RuntimeError(
            f"isolated Codex registration read-back failed: {stored!r}; expected={launcher!s}"
        )
    transport = cast(dict[str, object], stored["transport"])
    codex_command = cast(str, transport["command"])
    codex_args = cast(list[str], transport["args"])
    claude_config = cast(dict[str, object], json.loads((claude_home / ".claude.json").read_text()))
    claude_entry = cast(
        dict[str, object], cast(dict[str, object], claude_config["mcpServers"])[SERVER_NAME]
    )
    claude_command = claude_entry.get("command")
    if (
        claude_entry.get("type") != "stdio"
        or not isinstance(claude_command, str)
        or Path(claude_command).resolve() != launcher.resolve()
    ):
        raise RuntimeError(f"isolated Claude registration read-back failed: {claude_entry!r}")
    claude_args = cast(list[str], claude_entry["args"])
    return (
        environment,
        [codex_command, *codex_args],
        [claude_command, *claude_args],
        {
            "codex": _version(shutil.which("codex") or "codex", environment),
            "claude_code": _version(shutil.which("claude") or "claude", environment),
        },
    )


def _version(executable: str, environment: dict[str, str]) -> str:
    result = subprocess.run(
        [executable, "--version"],
        capture_output=True,
        text=True,
        env=environment,
        timeout=10,
        check=False,
    )
    return result.stdout.strip() or result.stderr.strip()


def _payload(fixture: dict[str, Any], *, checkpoint_id: str | None = None) -> dict[str, object]:
    content, _ = build_checkpoint_packet(fixture)
    scope = cast(dict[str, object], fixture["scope"])
    payload: dict[str, object] = {
        "operation": "create",
        **{
            name: scope[name]
            for name in ("owner_id", "workspace_id", "project_id", "session_id", "task_id")
        },
        **content.to_dict(),
        "evidence_references": cast(list[object], fixture["evidence"]),
    }
    if checkpoint_id is not None:
        payload["checkpoint_id"] = checkpoint_id
    return payload


async def _call_launcher(
    command: list[str], environment: dict[str, str], action: str, payload: dict[str, object]
) -> dict[str, object]:
    parameters = StdioServerParameters(command=command[0], args=command[1:], env=environment)
    async with stdio_client(parameters) as (read, write), ClientSession(read, write) as session:
        initialized = await session.initialize()
        if initialized.serverInfo.name != "mnemo-local":
            raise RuntimeError("unexpected MCP server identity")
        tools = await session.list_tools()
        if [tool.name for tool in tools.tools] != [
            "get_context",
            "list_skills",
            "get_skill",
            "explain_context",
            "save_checkpoint",
            "structural_lookup",
            "dbt_structure",
        ]:
            raise RuntimeError("unexpected MCP tool inventory")
        result = await session.call_tool(action, payload, read_timeout_seconds=timedelta(seconds=8))
        if result.isError or not isinstance(result.structuredContent, dict):
            raise RuntimeError(f"MCP {action} failed without exposing internal details")
        return cast(dict[str, object], result.structuredContent)


def _tool(
    command: list[str], environment: dict[str, str], action: str, payload: dict[str, object]
) -> dict[str, object]:
    try:
        return asyncio.run(
            asyncio.wait_for(_call_launcher(command, environment, action, payload), timeout=12)
        )
    except Exception as error:
        raise RuntimeError("isolated MCP process/tool operation failed") from error


def _context(
    command: list[str], environment: dict[str, str], scope: dict[str, object]
) -> ContextPacket:
    request = {
        name: scope[name]
        for name in ("owner_id", "workspace_id", "project_id", "session_id", "task_id")
    }
    return ContextPacket.from_dict(_tool(command, environment, "get_context", request))


def _revision_payload(
    fixture: dict[str, Any], created: dict[str, object], *, source: str
) -> dict[str, object]:
    payload = _payload(fixture)
    payload.update(
        {
            "operation": "revise",
            "checkpoint_id": created["checkpoint_id"],
            "expected_revision_id": created["checkpoint_revision_id"],
            "current_state": (
                "Validation remains in LocalConfig; Claude reviewed the pending regression."
            ),
            "completed_work": [
                "Added the shared CLI translation boundary.",
                "Claude reviewed revision 1.",
            ],
            "evidence_references": [
                {
                    **cast(dict[str, object], cast(list[object], fixture["evidence"])[0]),
                    "immutable_source_ref": f"fixture://fresh-session/transcript#E16?source={source}",
                }
            ],
        }
    )
    return payload


def _launcher_digest(command: list[str]) -> str:
    # Normalize the temporary absolute launcher path so the report is stable and private.
    if command[1:] != ["mcp", "serve", "--stdio"]:
        raise ValueError("unexpected registered Mnemo launcher arguments")
    return hashlib.sha256(b"mnemo-memory\0mcp\0serve\0--stdio").hexdigest()


def run(root: Path | None = None) -> dict[str, object]:
    if shutil.which("codex") is None or shutil.which("claude") is None:
        raise RuntimeError("SKIPPED: codex or claude executable is unavailable")
    temporary = (
        tempfile.TemporaryDirectory(prefix="mnemo-memory cross client Δ ") if root is None else None
    )
    base = Path(temporary.name) if temporary is not None else root
    assert base is not None
    try:
        fixture, transcript = load_fixture()
        environment, codex, claude, versions = _register(base, base / "shared Mnemo data Δ")
        registration_digests = {
            "codex": hashlib.sha256(
                (base / "Codex Home Δ" / "config.toml").read_bytes()
            ).hexdigest(),
            "claude_code": hashlib.sha256(
                (base / "Claude Home Δ" / ".claude.json").read_bytes()
            ).hexdigest(),
        }
        scope = cast(dict[str, object], fixture["scope"])

        codex_created = _tool(
            codex,
            environment,
            "save_checkpoint",
            _payload(fixture, checkpoint_id="88888888-8888-4888-8888-888888888888"),
        )
        claude_packet = _context(claude, environment, scope)
        codex_to_claude = score_context_packet(fixture, transcript, claude_packet)
        claude_revision = _tool(
            claude,
            environment,
            "save_checkpoint",
            _revision_payload(fixture, codex_created, source="claude-code"),
        )
        codex_revised_packet = _context(codex, environment, scope)
        alternating = score_context_packet(fixture, transcript, codex_revised_packet)

        reverse_data = base / "reverse Mnemo data Δ"
        reverse_environment = {**environment, "MNEMO_DATA_DIR": str(reverse_data)}
        _tool(
            claude,
            reverse_environment,
            "save_checkpoint",
            _payload(fixture, checkpoint_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        )
        codex_packet = _context(codex, reverse_environment, scope)
        claude_to_codex = score_context_packet(fixture, transcript, codex_packet)

        wrong_scope = {**scope, "project_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc"}
        wrong_codex_packet = _context(codex, environment, wrong_scope)
        wrong_claude_packet = _context(claude, reverse_environment, wrong_scope)
        baseline = evaluate(fixture, transcript)
        stale_recovery = False
        try:
            _tool(
                codex,
                environment,
                "save_checkpoint",
                _revision_payload(fixture, codex_created, source="stale-codex"),
            )
        except RuntimeError:
            stale_recovery = _context(codex, environment, scope).active_task_checkpoint is not None
        corrupt_data = base / "corrupt profile.sqlite3"
        corrupt_data.write_text("not a SQLite database")
        corrupt_failure = False
        try:
            _context(codex, {**environment, "MNEMO_DATA_DIR": str(corrupt_data)}, scope)
        except (OSError, RuntimeError):
            corrupt_failure = True
        missing_launcher_failure = False
        try:
            _context(
                [str(base / "missing launcher"), "mcp", "serve", "--stdio"], environment, scope
            )
        except (OSError, RuntimeError):
            missing_launcher_failure = True
        report = {
            "fixture_version": fixture["fixture_version"],
            "client_versions": versions,
            "registration_scope": "user",
            "launcher_digests": {
                "codex": _launcher_digest(codex),
                "claude_code": _launcher_digest(claude),
            },
            "codex_to_claude": {
                "source_client": "codex",
                "destination_client": "claude-code",
                "checkpoint_id": "88888888-8888-4888-8888-888888888888",
                "revision": "runtime-revision-1",
                **codex_to_claude,
            },
            "claude_to_codex": {
                "source_client": "claude-code",
                "destination_client": "codex",
                "checkpoint_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                "revision": "runtime-revision-1",
                **claude_to_codex,
            },
            "alternating_revision": {
                "checkpoint_id": codex_created["checkpoint_id"],
                "revision_1": "runtime-revision-1",
                "revision_2": "runtime-revision-2",
                "revision_number": claude_revision["revision_number"],
                "stable_identity": codex_created["checkpoint_id"]
                == claude_revision["checkpoint_id"],
                "distinct_revision_identity": codex_created["checkpoint_revision_id"]
                != claude_revision["checkpoint_revision_id"],
                **alternating,
            },
            "no_memory": cast(dict[str, object], baseline["conditions"])["no_memory"],
            "cross_scope_non_disclosure": (
                wrong_codex_packet.active_task_checkpoint is None
                and wrong_claude_packet.active_task_checkpoint is None
            ),
            "failure_degradation": {
                "stale_tool_failure_recovers": stale_recovery,
                "corrupt_database_fails_without_fallback": corrupt_failure,
                "missing_launcher_fails_promptly": missing_launcher_failure,
            },
            "client_configurations_preserved": registration_digests
            == {
                "codex": hashlib.sha256(
                    (base / "Codex Home Δ" / "config.toml").read_bytes()
                ).hexdigest(),
                "claude_code": hashlib.sha256(
                    (base / "Claude Home Δ" / ".claude.json").read_bytes()
                ).hexdigest(),
            },
            "no_model_call": True,
        }
        passed = (
            all(
                cast(bool, cast(dict[str, object], report[name])["passed"])
                for name in ("codex_to_claude", "claude_to_codex", "alternating_revision")
            )
            and cast(bool, report["cross_scope_non_disclosure"])
            and cast(bool, report["client_configurations_preserved"])
            and all(cast(dict[str, bool], report["failure_degradation"]).values())
        )
        return {**report, "passed": passed}
    finally:
        if temporary is not None:
            temporary.cleanup()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run()
    except RuntimeError as error:
        print(json.dumps({"passed": False, "reason": str(error)}, sort_keys=True))
        return 0 if str(error).startswith("SKIPPED:") else 1
    if not args.json:
        print("cross-client transport: " + ("passed" if result["passed"] else "failed"))
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
