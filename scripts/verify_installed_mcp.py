"""Verify the written personal install/connect workflow from a built distribution."""

from __future__ import annotations

import argparse
import json
import os
import select
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from scripts.verify_release_artifacts import verify_sdist, verify_wheel

DISTRIBUTION_VERSION = "0.1.0a21"
WHEEL_NAME = f"mnemo_unified_context-{DISTRIBUTION_VERSION}-py3-none-any.whl"
SDIST_NAME = f"mnemo_unified_context-{DISTRIBUTION_VERSION}.tar.gz"
TOOLS = ["get_context", "list_skills", "get_skill", "explain_context", "save_checkpoint"]


class InstalledWorkflowError(RuntimeError):
    """A bounded installed-workflow verification failure."""


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    timeout: float = 180.0,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        tuple(command),
        cwd=cwd,
        env=dict(environment),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout).strip()
        if len(details) > 2000:
            details = details[-2000:]
        suffix = f": {details}" if details else ""
        raise InstalledWorkflowError(
            "installed workflow command failed with code "
            f"{completed.returncode}: {command[0]}{suffix}"
        )
    return completed


def _fake_codex(executable: Path, state_path: Path) -> None:
    program = f"""#!{sys.executable}
import json
import os
import sys
from pathlib import Path

state = Path(os.environ["MNEMO_FAKE_CODEX_STATE"])
arguments = sys.argv[1:]
if arguments == ["mcp", "get", "mnemo-memory", "--json"]:
    if not state.is_file():
        raise SystemExit(1)
    print(state.read_text(encoding="utf-8"))
elif arguments[:4] == ["mcp", "add", "mnemo-memory", "--"] and len(arguments) >= 5:
    value = {{"transport": {{"command": arguments[4], "args": arguments[5:]}}}}
    state.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
elif arguments == ["mcp", "remove", "mnemo-memory"]:
    state.unlink(missing_ok=True)
else:
    raise SystemExit(2)
"""
    executable.write_text(program, encoding="utf-8")
    executable.chmod(0o700)
    if state_path.exists():
        raise InstalledWorkflowError("fake Codex registration state must start absent")


class _McpProcess:
    """Small JSON-RPC client for an independently launched installed MCP process."""

    def __init__(
        self,
        command: str,
        arguments: Sequence[str],
        *,
        project_directory: Path,
        environment: Mapping[str, str],
    ) -> None:
        self._next_id = 1
        self._process = subprocess.Popen(
            (command, *arguments),
            cwd=project_directory,
            env=dict(environment),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.call(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "mnemo-installed-audit", "version": "1"},
            },
        )
        self.notify("notifications/initialized")

    def call(self, method: str, params: Mapping[str, object]) -> dict[str, object]:
        request_id = self._next_id
        self._next_id += 1
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + 15
        if self._process.stdout is None:
            raise InstalledWorkflowError("installed MCP stdout is unavailable")
        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            ready, _, _ = select.select([self._process.stdout], [], [], remaining)
            if not ready:
                break
            line = self._process.stdout.readline()
            if not line:
                break
            response = json.loads(line)
            if isinstance(response, dict) and response.get("id") == request_id:
                return cast(dict[str, object], response)
        error = ""
        if self._process.stderr is not None and self._process.poll() is not None:
            error = self._process.stderr.read(1000)
        raise InstalledWorkflowError(f"installed MCP response timed out: {error!r}")

    def notify(self, method: str) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": {}})

    def tool(self, name: str, arguments: Mapping[str, object]) -> dict[str, object]:
        response = self.call("tools/call", {"name": name, "arguments": dict(arguments)})
        result = response.get("result")
        if not isinstance(result, dict):
            raise InstalledWorkflowError(f"installed MCP returned no result for {name}")
        return result

    def close(self) -> None:
        if self._process.poll() is None:
            if self._process.stdin is not None:
                self._process.stdin.close()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.terminate()
                self._process.wait(timeout=5)

    def _send(self, value: Mapping[str, object]) -> None:
        if self._process.stdin is None:
            raise InstalledWorkflowError("installed MCP stdin is unavailable")
        self._process.stdin.write(json.dumps(value, separators=(",", ":")) + "\n")
        self._process.stdin.flush()


def _structured(result: Mapping[str, object]) -> dict[str, object]:
    if result.get("isError") is True:
        raise InstalledWorkflowError("installed MCP tool returned a bounded error")
    value = result.get("structuredContent")
    if not isinstance(value, dict):
        raise InstalledWorkflowError("installed MCP tool returned no structured content")
    return value


def _source_snapshot(packet: Mapping[str, object]) -> str:
    items = packet.get("structural_items")
    if not isinstance(items, list):
        raise InstalledWorkflowError("installed context omitted structural items")
    snapshots: list[str] = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("content"), str):
            continue
        content = json.loads(cast(str, item["content"]))
        if isinstance(content, dict) and content.get("kind") == "source_architecture_overview":
            snapshot = content.get("snapshot_id")
            if isinstance(snapshot, str):
                snapshots.append(snapshot)
    if len(snapshots) != 1:
        raise InstalledWorkflowError("installed context must contain one source overview")
    return snapshots[0]


def _checkpoint_payload() -> dict[str, object]:
    long_state = "Installed checkpoint compaction remains deterministic and bounded. " * 30
    return {
        "operation": "create",
        "task_objective": "Verify the installed personal workflow",
        "current_state": long_state,
        "completed_work": ["installed Mnemo and connected the sample project"],
        "remaining_work": ["resume through a second fresh MCP process"],
        "decisions": ["use the registered project scope without UUID arguments"],
        "verification_performed": ["source-independent installed workflow"],
        "evidence_files": ["src/sample.py"],
    }


def _exercise_registration(
    launcher: Path,
    *,
    project_directory: Path,
    data_directory: Path,
    fake_bin: Path,
    tool_bin: Path,
    state_path: Path,
    base_environment: Mapping[str, str],
) -> None:
    environment = {
        **base_environment,
        "PATH": os.pathsep.join((str(fake_bin), str(tool_bin), base_environment.get("PATH", ""))),
        "CODEX_HOME": str(project_directory.parent / "codex-home"),
        "MNEMO_DATA_DIR": str(data_directory),
        "MNEMO_FAKE_CODEX_STATE": str(state_path),
    }
    initialized = _run(
        (str(launcher), "init", "--data-dir", str(data_directory)),
        cwd=project_directory,
        environment=environment,
    )
    if "initialized" not in initialized.stdout:
        raise InstalledWorkflowError("installed initialization returned an unexpected result")
    connected = _run(
        (
            str(launcher),
            "connect",
            "codex",
            "--json",
            "--project-dir",
            str(project_directory),
            "--data-dir",
            str(data_directory),
        ),
        cwd=project_directory,
        environment=environment,
    )
    connection = json.loads(connected.stdout)
    source = connection.get("source_structure")
    if not isinstance(source, dict) or source.get("indexed") is not True:
        raise InstalledWorkflowError("installed connection did not index the sample project")
    expected_snapshot = source.get("snapshot_id")
    if not isinstance(expected_snapshot, str):
        raise InstalledWorkflowError("installed connection omitted its source snapshot")

    registration = json.loads(state_path.read_text(encoding="utf-8"))
    transport = registration.get("transport")
    if not isinstance(transport, dict):
        raise InstalledWorkflowError("installed Codex registration is invalid")
    command, arguments = transport.get("command"), transport.get("args")
    if (
        not isinstance(command, str)
        or not isinstance(arguments, list)
        or not all(isinstance(value, str) for value in arguments)
    ):
        raise InstalledWorkflowError("installed Codex transport is invalid")

    overview_query = {
        "source_overview": {
            "maximum_files": 8,
            "maximum_modules": 8,
            "maximum_declarations": 8,
        }
    }
    first = _McpProcess(
        command,
        cast(list[str], arguments),
        project_directory=project_directory,
        environment=environment,
    )
    try:
        listed = first.call("tools/list", {}).get("result")
        tools = listed.get("tools") if isinstance(listed, dict) else None
        if not isinstance(tools, list) or not all(isinstance(tool, dict) for tool in tools):
            raise InstalledWorkflowError("installed MCP tool inventory is invalid")
        if [cast(dict[str, object], tool).get("name") for tool in tools] != TOOLS:
            raise InstalledWorkflowError("installed MCP tool inventory is incorrect")
        empty = _structured(first.tool("get_context", overview_query))
        if empty.get("active_task_checkpoint") is not None:
            raise InstalledWorkflowError("new installed project must not invent a checkpoint")
        if _source_snapshot(empty) != expected_snapshot:
            raise InstalledWorkflowError("installed source snapshot provenance changed")
        created = _structured(first.tool("save_checkpoint", _checkpoint_payload()))
        revision = created.get("checkpoint_revision_id")
        if not isinstance(revision, str):
            raise InstalledWorkflowError("installed checkpoint revision is missing")
        if (
            not isinstance(created.get("token_estimate"), int)
            or cast(int, created["token_estimate"]) > 200
            or not isinstance(created.get("compaction"), dict)
        ):
            raise InstalledWorkflowError("installed checkpoint compaction is unavailable")
        failed = first.tool(
            "save_checkpoint",
            {"operation": "invalid", "evidence_files": ["src/sample.py"]},
        )
        if failed.get("isError") is not True:
            raise InstalledWorkflowError("installed checkpoint failure was not sanitized")
    finally:
        first.close()

    second = _McpProcess(
        command,
        cast(list[str], arguments),
        project_directory=project_directory,
        environment=environment,
    )
    try:
        resumed = _structured(second.tool("get_context", overview_query))
        checkpoint = resumed.get("active_task_checkpoint")
        if not isinstance(checkpoint, dict) or revision not in json.dumps(resumed, sort_keys=True):
            raise InstalledWorkflowError("fresh installed MCP process lost the checkpoint revision")
        if _source_snapshot(resumed) != expected_snapshot:
            raise InstalledWorkflowError("fresh installed MCP process lost the source snapshot")
    finally:
        second.close()

    checked = _run(
        (str(launcher), "connect", "codex", "--check"),
        cwd=project_directory,
        environment=environment,
    )
    if json.loads(checked.stdout).get("connected") is not True:
        raise InstalledWorkflowError("installed Codex registration read-back failed")


def verify(source_root: Path, uv_executable: str, python_executable: Path) -> None:
    root = source_root.expanduser().resolve()
    if not root.is_dir():
        raise InstalledWorkflowError("source root is unavailable")
    with tempfile.TemporaryDirectory(prefix="mnemo-installed-audit-") as temporary:
        work = Path(temporary)
        distribution = work / "dist"
        tool_root = work / "tools"
        tool_bin = work / "tool-bin"
        fake_bin = work / "fake-bin"
        fake_bin.mkdir()
        base_environment = dict(os.environ)
        _run(
            (uv_executable, "build", "--no-sources", "--out-dir", str(distribution)),
            cwd=root,
            environment=base_environment,
        )
        wheel, sdist = distribution / WHEEL_NAME, distribution / SDIST_NAME
        verify_wheel(wheel)
        verify_sdist(sdist)

        install_environment = {
            **base_environment,
            "UV_TOOL_DIR": str(tool_root),
            "UV_TOOL_BIN_DIR": str(tool_bin),
            "UV_PYTHON_DOWNLOADS": "never",
            "UV_NO_PROGRESS": "1",
        }
        install_flags = (
            () if os.environ.get("MNEMO_VERIFY_INSTALLED_ALLOW_NETWORK") == "1" else ("--offline",)
        )
        _run(
            (
                uv_executable,
                "tool",
                "install",
                *install_flags,
                "--reinstall",
                "--python",
                str(python_executable.resolve()),
                str(wheel),
            ),
            cwd=work,
            environment=install_environment,
        )
        launcher = tool_bin / "mnemo-memory"
        if not launcher.is_file():
            raise InstalledWorkflowError("uv tool install did not create mnemo-memory")
        short_launcher = tool_bin / "mnemo"
        if not short_launcher.is_file():
            raise InstalledWorkflowError("uv tool install did not create mnemo")
        version_result = _run(
            (str(launcher), "--version"),
            cwd=work,
            environment=install_environment,
        )
        expected_version = f"mnemo-memory {DISTRIBUTION_VERSION}"
        if version_result.stdout.strip() != expected_version:
            raise InstalledWorkflowError("installed mnemo-memory reported an unexpected version")
        short_version_result = _run(
            (str(short_launcher), "--version"),
            cwd=work,
            environment=install_environment,
        )
        if short_version_result.stdout.strip() != f"mnemo {DISTRIBUTION_VERSION}":
            raise InstalledWorkflowError("installed mnemo reported an unexpected version")
        team_launcher = tool_bin / "mnemo-memory-team"
        if not team_launcher.is_file():
            raise InstalledWorkflowError("uv tool install did not create mnemo-memory-team")
        team_admin_launcher = tool_bin / "mnemo-memory-team-admin"
        if not team_admin_launcher.is_file():
            raise InstalledWorkflowError("uv tool install did not create mnemo-memory-team-admin")

        project = work / "sample-project"
        project.joinpath("src").mkdir(parents=True)
        project.joinpath("src", "sample.py").write_text(
            "def continue_task() -> str:\n    return 'ready'\n", encoding="utf-8"
        )
        project.joinpath("README.md").write_text("# Sample project\n", encoding="utf-8")
        fake_codex = fake_bin / "codex"
        state = work / "codex-registration.json"
        data_directory = work / "mnemo-data"
        _fake_codex(fake_codex, state)
        _exercise_registration(
            launcher,
            project_directory=project,
            data_directory=data_directory,
            fake_bin=fake_bin,
            tool_bin=tool_bin,
            state_path=state,
            base_environment=install_environment,
        )
        table = _run(
            (
                str(short_launcher),
                "memory",
                "diagnostics",
                "show",
                "--format",
                "table",
                "--project-dir",
                str(project),
                "--data-dir",
                str(data_directory),
            ),
            cwd=project,
            environment=install_environment,
        ).stdout
        if not table.startswith("TIME ") or "EVENT_ID" not in table:
            raise InstalledWorkflowError("installed diagnostics table header is unavailable")
        if not table.rstrip().endswith("does not prove causation."):
            raise InstalledWorkflowError("installed diagnostics table notice is unavailable")
        saves = _run(
            (
                str(short_launcher),
                "memory",
                "diagnostics",
                "saves",
                "--format",
                "table",
                "--project-dir",
                str(project),
                "--data-dir",
                str(data_directory),
            ),
            cwd=project,
            environment=install_environment,
        ).stdout
        if not saves.startswith("TIME ") or "MNEMO_INVALID_INPUT" not in saves:
            raise InstalledWorkflowError("installed checkpoint diagnostics table is unavailable")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path.cwd())
    parser.add_argument("--uv-executable", default=shutil.which("uv") or "uv")
    parser.add_argument("--python-executable", type=Path, default=Path(sys.executable))
    arguments = parser.parse_args()
    verify(arguments.source_root, arguments.uv_executable, arguments.python_executable)
    print("Installed personal workflow verification passed.")


if __name__ == "__main__":
    main()
