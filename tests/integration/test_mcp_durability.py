"""Independent-process durability checks for the production stdio MCP launcher."""

from __future__ import annotations

import json
import os
import select
import signal
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from typing import cast

from mnemo_memory.connectors.dbt.git_state import DbtGitStateObserver
from mnemo_memory.connectors.dbt.manifest import DbtManifestParser
from mnemo_memory.connectors.dbt.project_binding import (
    DbtProjectBinding,
    LocalDbtProjectBindingStore,
)
from mnemo_memory.packages.application import (
    GetCheckpoint,
    IngestManifest,
    LocalConfig,
    build_checkpoint_runtime,
)
from mnemo_memory.packages.application.automatic_memory import LocalMemoryProjectBindingStore
from mnemo_memory.packages.domain import CheckpointId, ContextPacket, MemoryScope

ROOT = Path(__file__).parents[2]
DBT_FIXTURE = ROOT / "tests" / "fixtures" / "dbt" / "manifest-v12.json"
SCOPE = {
    "owner_id": "11111111-1111-4111-8111-111111111111",
    "workspace_id": "22222222-2222-4222-8222-222222222222",
    "project_id": "33333333-3333-4333-8333-333333333333",
    "session_id": "44444444-4444-4444-8444-444444444444",
    "task_id": "55555555-5555-4555-8555-555555555555",
}


class McpProcess:
    """Minimal JSON-RPC client so tests can intentionally terminate the server process."""

    def __init__(self, data_directory: Path, *, working_directory: Path = ROOT) -> None:
        self._next_id = 1
        self.process = subprocess.Popen(
            [sys.executable, "-m", "mnemo_memory.cli", "mcp", "serve", "--stdio"],
            cwd=working_directory,
            env={**os.environ, "MNEMO_DATA_DIR": str(data_directory)},
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
                "clientInfo": {"name": "mnemo-durability-test", "version": "1"},
            },
        )
        self.notify("notifications/initialized")

    def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
        request_id = self._next_id
        self._next_id += 1
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + 8
        assert self.process.stdout is not None
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.process.stdout], [], [], deadline - time.monotonic())
            if not ready:
                break
            line = self.process.stdout.readline()
            if not line:
                break
            response = json.loads(line)
            if response.get("id") == request_id:
                return cast(dict[str, object], response)
        stderr = "" if self.process.stderr is None else self.process.stderr.read(2000)
        raise AssertionError(f"MCP response timed out; stderr={stderr!r}")

    def notify(self, method: str) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": {}})

    def tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        response = self.call("tools/call", {"name": name, "arguments": arguments})
        assert "result" in response, response
        return cast(dict[str, object], response["result"])

    def close(self) -> None:
        if self.process.poll() is None:
            # EOF is the normal stdio shutdown path.  Keep SIGKILL reserved for
            # the acknowledged-write durability test below.
            assert self.process.stdin is not None
            self.process.stdin.close()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=5)

    def kill(self) -> None:
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGKILL)
            self.process.wait(timeout=5)

    def _send(self, value: dict[str, object]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(value, separators=(",", ":")) + "\n")
        self.process.stdin.flush()


def evidence(identifier: str) -> dict[str, object]:
    return {
        "evidence_id": identifier,
        "source_id": "77777777-7777-4777-8777-777777777777",
        "source_type": "checkpoint",
        "trust_class": "user_authored",
        "immutable_source_ref": "synthetic://durability",
        "content_hash": "sha256:" + "d" * 64,
        "location": {
            "uri": "fixture://durability",
            "start_line": None,
            "start_column": None,
            "end_line": None,
            "end_column": None,
        },
        "observed_at": "2026-08-02T15:00:00+00:00",
        "verification_status": "verified",
    }


def save_payload(operation: str = "create", **changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "operation": operation,
        **SCOPE,
        "task_objective": "Prove durable MCP checkpoint storage",
        "current_state": "active",
        "evidence_references": [evidence("66666666-6666-4666-8666-666666666666")],
        "token_estimate": 600,
        "completed_work": ["save explicitly"],
        "remaining_work": ["restart the server"],
        "decisions": ["use durable SQLite"],
        "failures": [],
        "blockers": [],
        "relevant_files": ["apps/mcp/server.py"],
        "relevant_artifacts": [],
        "verification_performed": ["integration test"],
    }
    payload.update(changes)
    return payload


def context_payload() -> dict[str, object]:
    return dict(SCOPE)


def domain_scope() -> dict[str, object]:
    return {**SCOPE, "level": "task", "visibility": "project", "agent_id": None}


def structured(result: dict[str, object]) -> dict[str, object]:
    assert result.get("isError") is not True, result
    return cast(dict[str, object], result["structuredContent"])


def test_exact_launcher_survives_restart_and_terminal_selection(tmp_path: Path) -> None:
    data_directory = tmp_path / "Mnemo data Δ with spaces"
    process_a = McpProcess(data_directory)
    try:
        listed = cast(dict[str, object], process_a.call("tools/list", {})["result"])
        assert [tool["name"] for tool in cast(list[dict[str, object]], listed["tools"])] == [
            "get_context",
            "explain_context",
            "save_checkpoint",
        ]
        oversized = process_a.tool("save_checkpoint", save_payload(token_estimate=601))
        assert oversized["isError"] is True
        assert "MNEMO_TOKEN_BUDGET" not in json.dumps(oversized)
        created = structured(process_a.tool("save_checkpoint", save_payload()))
        revised = structured(
            process_a.tool(
                "save_checkpoint",
                save_payload(
                    "revise",
                    checkpoint_id=created["checkpoint_id"],
                    expected_revision_id=created["checkpoint_revision_id"],
                    current_state="revised",
                    evidence_references=[evidence("88888888-8888-4888-8888-888888888888")],
                ),
            )
        )
    finally:
        process_a.close()

    process_b = McpProcess(data_directory)
    try:
        packet = ContextPacket.from_dict(
            structured(process_b.tool("get_context", context_payload()))
        )
        assert packet.active_task_checkpoint is not None
        assert str(revised["checkpoint_revision_id"]) in packet.provenance[0].source_reference
        omitted = ContextPacket.from_dict(
            structured(process_b.tool("get_context", {**context_payload(), "total_tokens": 599}))
        )
        assert omitted.active_task_checkpoint is None
        assert omitted.omissions[0].reason.value == "token_budget"
        wrong_scope = process_b.tool(
            "get_context",
            {**context_payload(), "project_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"},
        )
        assert ContextPacket.from_dict(structured(wrong_scope)).active_task_checkpoint is None
        assert str(created["checkpoint_id"]) not in json.dumps(wrong_scope)
        completed = structured(
            process_b.tool(
                "save_checkpoint",
                save_payload(
                    "complete",
                    checkpoint_id=created["checkpoint_id"],
                    expected_revision_id=revised["checkpoint_revision_id"],
                    current_state="complete",
                    remaining_work=[],
                    evidence_references=[evidence("99999999-9999-4999-8999-999999999999")],
                ),
            )
        )
        assert completed["lifecycle_status"] == "completed"
    finally:
        process_b.close()

    process_c = McpProcess(data_directory)
    try:
        assert (
            ContextPacket.from_dict(
                structured(process_c.tool("get_context", context_payload()))
            ).active_task_checkpoint
            is None
        )
    finally:
        process_c.close()


def test_fresh_registered_process_labels_dbt_context_current_without_scope_ids(
    tmp_path: Path,
) -> None:
    project = tmp_path / "registered dbt project"
    project.mkdir()
    project.joinpath("dbt_project.yml").write_text("name: registered\n")
    project.joinpath("model.sql").write_text("select 1\n")
    subprocess.run(("git", "init", "--quiet"), cwd=project, check=True)
    subprocess.run(("git", "add", "."), cwd=project, check=True)
    subprocess.run(
        (
            "git",
            "-c",
            "user.name=Mnemo Test",
            "-c",
            "user.email=mnemo-test@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "fixture",
        ),
        cwd=project,
        check=True,
    )
    data_directory = tmp_path / "registered data"
    project_scope = MemoryScope.from_dict(
        {
            **SCOPE,
            "level": "project",
            "visibility": "project",
            "session_id": None,
            "task_id": None,
            "agent_id": None,
        }
    )
    LocalDbtProjectBindingStore(data_directory).set(
        DbtProjectBinding(project.resolve(), project_scope)
    )
    LocalMemoryProjectBindingStore(data_directory).enable(project, project_scope=project_scope)
    source_state = DbtGitStateObserver().observe(project)
    assert source_state is not None
    with build_checkpoint_runtime(
        LocalConfig.defaults(data_directory), dbt_parser=DbtManifestParser()
    ) as runtime:
        assert runtime.dbt_manifest_service is not None
        runtime.dbt_manifest_service.ingest(
            IngestManifest(
                project_scope,
                DBT_FIXTURE.read_bytes(),
                "manifest.json",
                datetime(2026, 8, 5, tzinfo=UTC),
                source_state=source_state,
            )
        )

    process = McpProcess(data_directory, working_directory=project)
    try:
        packet = ContextPacket.from_dict(
            structured(
                process.tool(
                    "get_context",
                    {
                        "dbt_lineage": {
                            "relative_path": "models/marts/fct_orders.sql",
                            "direction": "downstream",
                        }
                    },
                )
            )
        )
    finally:
        process.close()

    assert packet.structural_items
    assert all(
        json.loads(item.content)["currentness"] == "current" for item in packet.structural_items
    )


def test_abrupt_acknowledged_write_and_two_process_conflict_are_durable(tmp_path: Path) -> None:
    data_directory = tmp_path / "abrupt durable store"
    first = McpProcess(data_directory)
    try:
        created = structured(first.tool("save_checkpoint", save_payload()))
        first.kill()
    finally:
        first.close()
    restarted = McpProcess(data_directory)
    try:
        packet = ContextPacket.from_dict(
            structured(restarted.tool("get_context", context_payload()))
        )
        assert packet.active_task_checkpoint is not None
        assert str(created["checkpoint_revision_id"]) in packet.provenance[0].source_reference
    finally:
        restarted.close()

    left, right = McpProcess(data_directory), McpProcess(data_directory)
    barrier = Barrier(2)

    def revise(process: McpProcess, evidence_id: str) -> dict[str, object]:
        barrier.wait(timeout=5)
        return process.tool(
            "save_checkpoint",
            save_payload(
                "revise",
                checkpoint_id=created["checkpoint_id"],
                expected_revision_id=created["checkpoint_revision_id"],
                evidence_references=[evidence(evidence_id)],
            ),
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(
                pool.map(
                    revise,
                    (left, right),
                    (
                        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                        "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                    ),
                )
            )
        successes = [result for result in outcomes if result.get("isError") is not True]
        conflicts = [result for result in outcomes if result.get("isError") is True]
        assert len(successes) == 1 and len(conflicts) == 1
        assert "MNEMO_REVISION_CONFLICT" in json.dumps(conflicts[0])
        assert (
            structured(left.tool("get_context", context_payload()))["declared_total_tokens"] == 600
        )
    finally:
        left.close()
        right.close()

    with build_checkpoint_runtime(LocalConfig.defaults(data_directory)) as runtime:
        view = runtime.checkpoint_service.get(
            GetCheckpoint(
                MemoryScope.from_dict(domain_scope()),
                CheckpointId.from_string(cast(str, created["checkpoint_id"])),
            )
        )
        assert view.revision.revision_number == 2
        with sqlite3.connect(runtime.repository.path) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            connection.execute("PRAGMA foreign_keys = ON")
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_abandonment_survives_restart_with_history_and_reason(tmp_path: Path) -> None:
    data_directory = tmp_path / "abandonment store"
    abandoned_scope = {**SCOPE, "task_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc"}
    first = McpProcess(data_directory)
    try:
        created = structured(first.tool("save_checkpoint", {**save_payload(), **abandoned_scope}))
        revised = structured(
            first.tool(
                "save_checkpoint",
                {
                    **save_payload(
                        "revise",
                        checkpoint_id=created["checkpoint_id"],
                        expected_revision_id=created["checkpoint_revision_id"],
                        evidence_references=[evidence("dddddddd-dddd-4ddd-8ddd-dddddddddddd")],
                    ),
                    **abandoned_scope,
                },
            )
        )
    finally:
        first.close()
    second = McpProcess(data_directory)
    try:
        terminal = structured(
            second.tool(
                "save_checkpoint",
                {
                    **save_payload(
                        "abandon",
                        checkpoint_id=created["checkpoint_id"],
                        expected_revision_id=revised["checkpoint_revision_id"],
                        reason="awaiting a decision",
                        evidence_references=[evidence("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")],
                    ),
                    **abandoned_scope,
                },
            )
        )
        assert terminal["lifecycle_status"] == "abandoned"
    finally:
        second.close()
    third = McpProcess(data_directory)
    try:
        assert (
            ContextPacket.from_dict(
                structured(third.tool("get_context", dict(abandoned_scope)))
            ).active_task_checkpoint
            is None
        )
    finally:
        third.close()
    with build_checkpoint_runtime(LocalConfig.defaults(data_directory)) as runtime:
        history = runtime.repository.get_revision(
            MemoryScope.from_dict({**domain_scope(), "task_id": abandoned_scope["task_id"]}),
            CheckpointId.from_string(cast(str, created["checkpoint_id"])),
            revision_number=2,
        )
        terminal_revision = runtime.checkpoint_service.get(
            GetCheckpoint(
                MemoryScope.from_dict({**domain_scope(), "task_id": abandoned_scope["task_id"]}),
                CheckpointId.from_string(cast(str, created["checkpoint_id"])),
            )
        ).revision
        assert history.revision_number == 2
        assert "awaiting a decision" in terminal_revision.content.failures
        assert (
            terminal_revision.evidence_references[0].evidence_id
            != history.evidence_references[0].evidence_id
        )
