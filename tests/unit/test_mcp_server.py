from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import Tool

from mnemo_memory.apps.mcp.server import SERVER_NAME, SERVER_VERSION, create_server
from mnemo_memory.packages.application import LocalConfig, build_checkpoint_runtime
from mnemo_memory.packages.application.mcp_durable import DurableMcpContextPort
from mnemo_memory.packages.application.mcp_fixture import FixtureMcpContextPort
from mnemo_memory.packages.application.unified_context import UnifiedContextService
from mnemo_memory.packages.domain import (
    ContextPacket,
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.project_index import PythonSourceParser, PythonSourceParseRequest

ROOT = Path(__file__).parents[2]
IDS = {
    "owner_id": "11111111-1111-4111-8111-111111111111",
    "workspace_id": "22222222-2222-4222-8222-222222222222",
    "project_id": "33333333-3333-4333-8333-333333333333",
    "session_id": "44444444-4444-4444-8444-444444444444",
    "task_id": "55555555-5555-4555-8555-555555555555",
}
EVIDENCE = {
    "evidence_id": "66666666-6666-4666-8666-666666666666",
    "source_id": "77777777-7777-4777-8777-777777777777",
    "source_type": "checkpoint",
    "trust_class": "user_authored",
    "immutable_source_ref": "synthetic://mcp-test",
    "content_hash": "sha256:" + "a" * 64,
    "location": {
        "uri": "fixture://mcp-test",
        "start_line": None,
        "start_column": None,
        "end_line": None,
        "end_column": None,
    },
    "observed_at": "2026-08-02T14:00:00+00:00",
    "verification_status": "verified",
}


def save_payload(operation: str = "create", **changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "operation": operation,
        **IDS,
        "task_objective": "Resume durable MCP checkpoint",
        "current_state": "active",
        "evidence_references": [EVIDENCE],
        "token_estimate": 25,
        "completed_work": ["created through MCP"],
        "remaining_work": ["retrieve through get_context"],
        "decisions": ["use durable service"],
        "failures": [],
        "blockers": [],
        "relevant_files": ["apps/mcp/server.py"],
        "relevant_artifacts": [],
        "verification_performed": ["pytest"],
    }
    payload.update(changes)
    return payload


def context_payload(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = dict(IDS)
    payload.update(changes)
    return payload


def test_server_lists_exact_tools_with_safety_annotations(tmp_path: Path) -> None:
    async def list_tools() -> list[Tool]:
        with build_checkpoint_runtime(LocalConfig.defaults(tmp_path / "runtime")) as runtime:
            return list(
                await create_server(DurableMcpContextPort(runtime.checkpoint_service)).list_tools()
            )

    tools = asyncio.run(list_tools())
    assert [tool.name for tool in tools] == ["get_context", "save_checkpoint"]
    assert tools[0].annotations is not None and tools[0].annotations.readOnlyHint is True
    assert tools[1].annotations is not None and tools[1].annotations.readOnlyHint is False
    assert all(tool.inputSchema["additionalProperties"] is False for tool in tools)
    assert "operation" in tools[1].inputSchema["properties"]
    assert "lessons" in tools[1].inputSchema["properties"]
    assert "record_lesson" in tools[1].inputSchema["properties"]["operation"]["pattern"]
    assert (
        "without resending the complete checkpoint"
        in tools[1].inputSchema["properties"]["operation"]["description"]
    )
    assert "exactly one" in tools[1].inputSchema["properties"]["lessons"]["description"]
    assert "source_query" in tools[0].inputSchema["properties"]
    assert "source_impact" in tools[0].inputSchema["properties"]
    assert "source_changes" in tools[0].inputSchema["properties"]
    assert "include_lifecycle_events" in tools[0].inputSchema["properties"]
    assert "include_approved_events" in tools[0].inputSchema["properties"]
    assert "record_event" in tools[1].inputSchema["properties"]["operation"]["pattern"]
    assert "event_summary" in tools[1].inputSchema["properties"]


def test_fixture_port_is_explicit_test_only_behavior() -> None:
    port = FixtureMcpContextPort()
    packet = ContextPacket.from_dict(
        port.get_context({"owner_id": IDS["owner_id"], "query": "resume"})
    )
    assert packet.declared_total_tokens == 0
    assert (
        port.save_checkpoint(
            {"owner_id": IDS["owner_id"], "evidence_references": ["synthetic-evidence"]}
        )["durability"]
        == "fixture-only"
    )


def test_durable_port_lifecycle_and_safe_errors(tmp_path: Path) -> None:
    with build_checkpoint_runtime(LocalConfig.defaults(tmp_path / "durable")) as runtime:
        port = DurableMcpContextPort(runtime.checkpoint_service)
        created = port.save_checkpoint(save_payload())
        packet = ContextPacket.from_dict(port.get_context(context_payload()))
        assert packet.active_task_checkpoint is not None
        assert str(created["checkpoint_revision_id"]) in packet.provenance[0].source_reference
        revised = port.save_checkpoint(
            save_payload(
                "revise",
                checkpoint_id=created["checkpoint_id"],
                expected_revision_id=created["checkpoint_revision_id"],
                current_state="revised",
            )
        )
        assert revised["revision_number"] == 2
        completed = port.save_checkpoint(
            save_payload(
                "complete",
                checkpoint_id=created["checkpoint_id"],
                expected_revision_id=revised["checkpoint_revision_id"],
                current_state="complete",
                remaining_work=[],
            )
        )
        assert completed["lifecycle_status"] == "completed"
        assert (
            ContextPacket.from_dict(port.get_context(context_payload())).active_task_checkpoint
            is None
        )
        abandoned = port.save_checkpoint(
            save_payload(
                task_id="99999999-9999-4999-8999-999999999999",
                evidence_references=[
                    {**EVIDENCE, "evidence_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}
                ],
            )
        )
        abandoned_result = port.save_checkpoint(
            save_payload(
                "abandon",
                task_id="99999999-9999-4999-8999-999999999999",
                checkpoint_id=abandoned["checkpoint_id"],
                expected_revision_id=abandoned["checkpoint_revision_id"],
                reason="synthetic blocker",
                evidence_references=[
                    {**EVIDENCE, "evidence_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"}
                ],
            )
        )
        assert abandoned_result["lifecycle_status"] == "abandoned"
        with pytest.raises(ValueError, match="MNEMO_CHECKPOINT_NOT_FOUND"):
            port.get_context({**IDS, "checkpoint_id": "88888888-8888-4888-8888-888888888888"})


def test_optional_checkpoint_observation_failure_never_changes_a_successful_save(
    tmp_path: Path,
) -> None:
    def failing_observer(_: object) -> object:
        raise RuntimeError("private parser failure")

    with build_checkpoint_runtime(LocalConfig.defaults(tmp_path / "durable")) as runtime:
        result = DurableMcpContextPort(
            runtime.checkpoint_service, after_checkpoint_save=failing_observer
        ).save_checkpoint(save_payload())

    assert result["persistence"] == "durable"
    assert result["lifecycle_status"] == "active"


def test_durable_port_returns_opt_in_scoped_lifecycle_history(tmp_path: Path) -> None:
    with build_checkpoint_runtime(LocalConfig.defaults(tmp_path / "timeline")) as runtime:
        port = DurableMcpContextPort(runtime.checkpoint_service)
        created = port.save_checkpoint(save_payload())
        port.save_checkpoint(
            save_payload(
                "revise",
                checkpoint_id=created["checkpoint_id"],
                expected_revision_id=created["checkpoint_revision_id"],
                current_state="revised",
            )
        )
        packet = ContextPacket.from_dict(
            port.get_context(context_payload(include_lifecycle_events=True))
        )
    assert ["checkpoint_revised" in item.content for item in packet.episodic_memories] == [
        True,
        False,
    ]
    assert all(item.evidence_references for item in packet.episodic_memories)


def test_durable_port_records_and_returns_explicit_approved_episodic_fact(tmp_path: Path) -> None:
    with build_checkpoint_runtime(LocalConfig.defaults(tmp_path / "approved event")) as runtime:
        port = DurableMcpContextPort(runtime.checkpoint_service)
        port.save_checkpoint(save_payload())
        stored = port.save_checkpoint(
            {
                "operation": "record_event",
                **IDS,
                "event_kind": "failure",
                "event_summary": "The reconciliation used a stale source snapshot.",
                "source_event_key": "reconciliation:stale-source:1",
                "evidence_references": [EVIDENCE],
            }
        )
        assert stored["event_kind"] == "failure"
        assert stored["idempotent"] is False
        assert (
            port.save_checkpoint(
                {
                    "operation": "record_event",
                    **IDS,
                    "event_kind": "failure",
                    "event_summary": "The reconciliation used a stale source snapshot.",
                    "source_event_key": "reconciliation:stale-source:1",
                    "evidence_references": [EVIDENCE],
                }
            )["idempotent"]
            is True
        )
        packet = ContextPacket.from_dict(
            port.get_context(context_payload(include_approved_events=True))
        )
    assert len(packet.episodic_memories) == 1
    assert "stale source snapshot" in packet.episodic_memories[0].content
    assert packet.episodic_memories[0].evidence_references


def test_durable_port_preserves_an_evidence_backed_reasoning_lesson(tmp_path: Path) -> None:
    lesson = {
        "trigger": "A reconciliation test disagreed with the finance seed.",
        "mistaken_assumption": "Both inputs used the same date grain.",
        "correction": "Compare at the documented business-date grain.",
        "prevention": "Check grain and null handling before changing a join.",
        "evidence_ids": [EVIDENCE["evidence_id"]],
    }
    with build_checkpoint_runtime(LocalConfig.defaults(tmp_path / "lesson")) as runtime:
        port = DurableMcpContextPort(runtime.checkpoint_service)
        port.save_checkpoint(save_payload(lessons=[lesson]))
        packet = ContextPacket.from_dict(port.get_context(context_payload()))
        assert packet.active_task_checkpoint is not None
        content = packet.active_task_checkpoint.content
        assert '"lessons"' in content
        assert "Check grain and null handling" in content

        with pytest.raises(ValueError, match="MNEMO_INVALID_INPUT"):
            port.save_checkpoint(
                save_payload(
                    task_id="99999999-9999-4999-8999-999999999999",
                    evidence_references=[
                        {**EVIDENCE, "evidence_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}
                    ],
                    lessons=[lesson],
                )
            )


def test_durable_port_records_one_lesson_without_full_checkpoint_content(tmp_path: Path) -> None:
    lesson_evidence = {**EVIDENCE, "evidence_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}
    lesson = {
        "trigger": "A focused test contradicted the per-command validation catch.",
        "mistaken_assumption": "Each CLI command owns validation exception handling.",
        "correction": "Keep validation in LocalConfig and translate at the CLI boundary.",
        "prevention": "Check the shared validation owner before adding a CLI exception catch.",
        "evidence_ids": [lesson_evidence["evidence_id"]],
    }
    with build_checkpoint_runtime(LocalConfig.defaults(tmp_path / "record lesson")) as runtime:
        port = DurableMcpContextPort(runtime.checkpoint_service)
        created = port.save_checkpoint(save_payload())
        recorded = port.save_checkpoint(
            {
                "operation": "record_lesson",
                **IDS,
                "checkpoint_id": created["checkpoint_id"],
                "expected_revision_id": created["checkpoint_revision_id"],
                "evidence_references": [lesson_evidence],
                "lessons": [lesson],
            }
        )
        assert recorded["revision_number"] == 2
        packet = ContextPacket.from_dict(port.get_context(context_payload()))
    assert packet.active_task_checkpoint is not None
    assert "Check the shared validation owner" in packet.active_task_checkpoint.content


def test_durable_port_returns_persisted_scoped_source_structure(tmp_path: Path) -> None:
    project = tmp_path / "Project Δ"
    project.mkdir()
    (project / "orders.py").write_text("def build_orders():\n    return []\n")
    config = LocalConfig.defaults(tmp_path / "durable source")
    project_scope = MemoryScope(
        OwnerId.from_string(IDS["owner_id"]),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string(IDS["workspace_id"]),
        ProjectId.from_string(IDS["project_id"]),
    )
    with build_checkpoint_runtime(config) as runtime:
        repository = runtime.source_structure_repository
        assert repository is not None
        repository.store_and_activate(
            PythonSourceParser().parse(PythonSourceParseRequest(project_scope, project))
        )

    with build_checkpoint_runtime(config) as runtime:
        repository = runtime.source_structure_repository
        assert repository is not None
        snapshot = repository.get_active_snapshot(project_scope)
        assert snapshot is not None
        port = DurableMcpContextPort(
            runtime.checkpoint_service,
            UnifiedContextService(runtime.checkpoint_service, None, repository),
        )
        packet = ContextPacket.from_dict(
            port.get_context(context_payload(source_query="build_orders"))
        )

    assert len(packet.structural_items) == 1
    item = packet.structural_items[0]
    assert "build_orders" in item.content
    assert str(project) not in item.content
    assert len(packet.provenance) == 1
    assert packet.provenance[0].item_id == item.item_id


def test_durable_port_returns_scoped_source_impact_context(tmp_path: Path) -> None:
    project = tmp_path / "Project Δ"
    project.mkdir()
    (project / "core.py").write_text("def calculate():\n    return 1\n")
    (project / "service.py").write_text(
        "import core\n\ndef serve():\n    return core.calculate()\n"
    )
    config = LocalConfig.defaults(tmp_path / "durable source")
    project_scope = MemoryScope(
        OwnerId.from_string(IDS["owner_id"]),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string(IDS["workspace_id"]),
        ProjectId.from_string(IDS["project_id"]),
    )
    with build_checkpoint_runtime(config) as runtime:
        repository = runtime.source_structure_repository
        assert repository is not None
        repository.store_and_activate(
            PythonSourceParser().parse(PythonSourceParseRequest(project_scope, project))
        )
    with build_checkpoint_runtime(config) as runtime:
        repository = runtime.source_structure_repository
        assert repository is not None
        snapshot = repository.get_active_snapshot(project_scope)
        assert snapshot is not None
        port = DurableMcpContextPort(
            runtime.checkpoint_service,
            UnifiedContextService(runtime.checkpoint_service, None, repository),
        )
        packet = ContextPacket.from_dict(
            port.get_context(
                context_payload(
                    source_impact={
                        "symbol": "core",
                        "direction": "dependents",
                        "maximum_depth": 1,
                        "snapshot_id": str(snapshot.snapshot_id),
                        "current_source_digest": snapshot.source_digest,
                    }
                )
            )
        )

    assert any('"symbol":"core"' in item.content for item in packet.structural_items)
    assert any('"symbol":"service"' in item.content for item in packet.structural_items)
    assert all('"currentness":"current"' in item.content for item in packet.structural_items)
    assert all(item.evidence_references for item in packet.structural_items)


def test_durable_port_returns_latest_scoped_source_change_context(tmp_path: Path) -> None:
    project = tmp_path / "Project Δ"
    project.mkdir()
    path = project / "orders.py"
    path.write_text("def calculate_total():\n    return 1\n")
    config = LocalConfig.defaults(tmp_path / "durable source changes")
    project_scope = MemoryScope(
        OwnerId.from_string(IDS["owner_id"]),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string(IDS["workspace_id"]),
        ProjectId.from_string(IDS["project_id"]),
    )
    with build_checkpoint_runtime(config) as runtime:
        repository = runtime.source_structure_repository
        assert repository is not None
        repository.store_and_activate(
            PythonSourceParser().parse(PythonSourceParseRequest(project_scope, project))
        )
        path.write_text("def calculate_total():\n    return 2\n\ndef reconcile():\n    return 3\n")
        latest = PythonSourceParser().parse(PythonSourceParseRequest(project_scope, project))
        repository.store_and_activate(latest)

    with build_checkpoint_runtime(config) as runtime:
        repository = runtime.source_structure_repository
        assert repository is not None
        port = DurableMcpContextPort(
            runtime.checkpoint_service,
            UnifiedContextService(runtime.checkpoint_service, None, repository),
        )
        packet = ContextPacket.from_dict(
            port.get_context(
                context_payload(
                    source_changes={
                        "maximum_declarations": 4,
                        "maximum_relationships": 4,
                        "maximum_transitions": 1,
                        "relative_path": "orders.py",
                        "current_source_digest": latest.snapshot.source_digest,
                        "require_current": True,
                    }
                )
            )
        )

    assert len(packet.structural_items) == 1
    item = packet.structural_items[0]
    assert "reconcile" in item.content
    assert '"requested_relative_path":"orders.py"' in item.content
    assert '"currentness":"current"' in item.content
    assert len(item.evidence_references) == 2


def test_real_stdio_server_is_durable_and_protocol_clean(tmp_path: Path) -> None:
    async def exercise() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "mnemo_memory.apps.mcp.server",
                "--data-dir",
                str(tmp_path / "data with spaces"),
            ],
            cwd=ROOT,
        )
        async with stdio_client(parameters) as (read, write), ClientSession(read, write) as session:
            initialized = await session.initialize()
            assert initialized.serverInfo.name == SERVER_NAME
            assert initialized.serverInfo.version == SERVER_VERSION
            assert [tool.name for tool in (await session.list_tools()).tools] == [
                "get_context",
                "save_checkpoint",
            ]
            created = await session.call_tool("save_checkpoint", save_payload())
            assert created.isError is False
            result = created.structuredContent or {}
            context = await session.call_tool("get_context", IDS)
            assert context.isError is False
            packet = ContextPacket.from_dict(context.structuredContent or {})
            assert packet.active_task_checkpoint is not None
            assert str(result["checkpoint_revision_id"]) in packet.provenance[0].source_reference
            invalid = await session.call_tool("save_checkpoint", {"operation": "invalid"})
            assert invalid.isError is True
            still_valid = await session.call_tool("get_context", IDS)
            assert still_valid.isError is False

    asyncio.run(asyncio.wait_for(exercise(), timeout=15))


def test_invalid_data_directory_exits_with_a_sanitized_startup_error(tmp_path: Path) -> None:
    occupied = tmp_path / "occupied data directory"
    occupied.write_text("not a directory")
    result = subprocess.run(
        [sys.executable, "-m", "mnemo_memory.apps.mcp.server", "--data-dir", str(occupied)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=8,
    )
    assert result.returncode == 2
    assert "MNEMO_STORAGE_UNAVAILABLE" in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""
