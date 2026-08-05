from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import Tool

from mnemo_memory.apps.mcp.server import SERVER_NAME, SERVER_VERSION, create_server
from mnemo_memory.connectors.dbt.artifacts import DbtSourceFreshnessParser
from mnemo_memory.connectors.dbt.code_excerpt import DbtLocalCodeExcerptReader
from mnemo_memory.connectors.dbt.manifest import DbtManifestParser
from mnemo_memory.connectors.dbt.project_binding import (
    DbtProjectBinding,
    LocalDbtProjectBindingStore,
)
from mnemo_memory.packages.application import (
    IngestManifest,
    IngestSourceFreshness,
    LocalConfig,
    build_checkpoint_runtime,
)
from mnemo_memory.packages.application.automatic_memory import LocalMemoryProjectBindingStore
from mnemo_memory.packages.application.mcp_durable import DurableMcpContextPort
from mnemo_memory.packages.application.mcp_fixture import FixtureMcpContextPort
from mnemo_memory.packages.application.unified_context import UnifiedContextService
from mnemo_memory.packages.domain import (
    ContextPacket,
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    SessionId,
    SourceStateFingerprint,
    TaskId,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.project_index import PythonSourceParser, PythonSourceParseRequest

ROOT = Path(__file__).parents[2]
DBT_FIXTURE = ROOT / "tests" / "fixtures" / "dbt" / "manifest-v12.json"
DBT_SOURCES_FIXTURE = ROOT / "tests" / "fixtures" / "dbt" / "sources-v3.json"
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
    assert [tool.name for tool in tools] == [
        "get_context",
        "list_skills",
        "get_skill",
        "explain_context",
        "save_checkpoint",
    ]
    assert tools[0].annotations is not None and tools[0].annotations.readOnlyHint is True
    assert tools[1].annotations is not None and tools[1].annotations.readOnlyHint is True
    assert tools[2].annotations is not None and tools[2].annotations.readOnlyHint is True
    assert tools[3].annotations is not None and tools[3].annotations.readOnlyHint is True
    assert tools[4].annotations is not None and tools[4].annotations.readOnlyHint is False
    assert all(tool.inputSchema["additionalProperties"] is False for tool in tools)
    assert "operation" in tools[4].inputSchema["properties"]
    assert "lessons" in tools[4].inputSchema["properties"]
    assert "record_lesson" in tools[4].inputSchema["properties"]["operation"]["pattern"]
    assert (
        "without resending the complete checkpoint"
        in tools[4].inputSchema["properties"]["operation"]["description"]
    )
    assert "exactly one" in tools[4].inputSchema["properties"]["lessons"]["description"]
    assert "source_query" in tools[0].inputSchema["properties"]
    assert "query" in tools[0].inputSchema["properties"]
    assert "knowledge_query" in tools[0].inputSchema["properties"]
    assert "source_impact" in tools[0].inputSchema["properties"]
    assert "source_changes" in tools[0].inputSchema["properties"]
    assert "source_overview" in tools[0].inputSchema["properties"]
    assert "dbt_test_coverage" in tools[0].inputSchema["properties"]
    assert "dbt_selector" in tools[0].inputSchema["properties"]
    assert "dbt_freshness" in tools[0].inputSchema["properties"]
    assert "dbt_changes" in tools[0].inputSchema["properties"]
    assert "include_lifecycle_events" in tools[0].inputSchema["properties"]
    assert "include_approved_events" in tools[0].inputSchema["properties"]
    assert "skill_tags" in tools[0].inputSchema["properties"]
    assert "skill_client" in tools[0].inputSchema["properties"]
    assert "skill_agent_name" in tools[0].inputSchema["properties"]
    assert tools[0].inputSchema["properties"]["render_for"]["anyOf"][0]["pattern"] == (
        "^(codex|claude-code)$"
    )
    assert "record_event" in tools[4].inputSchema["properties"]["operation"]["pattern"]
    assert "event_summary" in tools[4].inputSchema["properties"]
    assert set(tools[3].inputSchema["properties"]) == {"context_packet"}
    assert set(tools[1].inputSchema["required"]) == {"client"}
    assert set(tools[2].inputSchema["required"]) == {"name", "client"}
    for name in IDS:
        assert name not in tools[0].inputSchema.get("required", [])
        assert name not in tools[1].inputSchema.get("required", [])
        assert name not in tools[2].inputSchema.get("required", [])
        assert name not in tools[4].inputSchema.get("required", [])


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


def test_durable_port_resolves_omitted_scope_only_from_registered_default(tmp_path: Path) -> None:
    scope = MemoryScope(
        OwnerId.from_string(IDS["owner_id"]),
        ScopeLevel.TASK,
        Visibility.PROJECT,
        WorkspaceId.from_string(IDS["workspace_id"]),
        ProjectId.from_string(IDS["project_id"]),
        SessionId.from_string(IDS["session_id"]),
        TaskId.from_string(IDS["task_id"]),
    )
    payload = save_payload()
    for name in IDS:
        payload.pop(name)
    with build_checkpoint_runtime(LocalConfig.defaults(tmp_path / "default-scope")) as runtime:
        port = DurableMcpContextPort(runtime.checkpoint_service, default_scope=scope)
        created = port.save_checkpoint(payload)
        packet = ContextPacket.from_dict(port.get_context({}))
        assert packet.active_task_checkpoint is not None
        assert packet.owner_scope == scope
        assert str(created["checkpoint_revision_id"]) in packet.provenance[0].source_reference
        with pytest.raises(ValueError, match="MNEMO_INVALID_INPUT"):
            port.get_context({"owner_id": IDS["owner_id"]})


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
        with pytest.raises(ValueError, match="MNEMO_INVALID_INPUT"):
            port.save_checkpoint(
                {
                    "operation": "record_event",
                    **IDS,
                    "event_kind": "failure",
                    "event_summary": "api_key=ABCDEFGHIJKLMNOPQRSTUVWX",
                    "source_event_key": "reconciliation:secret:1",
                    "evidence_references": [EVIDENCE],
                }
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


def test_durable_port_returns_bounded_scoped_source_overview(tmp_path: Path) -> None:
    project = tmp_path / "Project Δ"
    project.mkdir()
    (project / "core.py").write_text("def calculate():\n    return 1\n", encoding="utf-8")
    config = LocalConfig.defaults(tmp_path / "durable source overview")
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
        stored = repository.store_and_activate(
            PythonSourceParser().parse(PythonSourceParseRequest(project_scope, project))
        )
        port = DurableMcpContextPort(
            runtime.checkpoint_service,
            UnifiedContextService(runtime.checkpoint_service, None, repository),
        )
        packet = ContextPacket.from_dict(
            port.get_context(
                context_payload(
                    source_overview={
                        "maximum_modules": 1,
                        "maximum_declarations": 1,
                        "current_source_digest": stored.snapshot.source_digest,
                        "require_current": True,
                    }
                )
            )
        )

    overview = next(
        item for item in packet.structural_items if item.item_id.startswith("source-overview:")
    )
    assert '"kind":"source_snapshot_overview"' in overview.content
    assert '"currentness":"current"' in overview.content
    assert str(project) not in overview.content
    assert overview.evidence_references


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


def test_durable_port_rejects_a_non_string_source_change_path(tmp_path: Path) -> None:
    with build_checkpoint_runtime(
        LocalConfig.defaults(tmp_path / "invalid source history")
    ) as runtime:
        port = DurableMcpContextPort(runtime.checkpoint_service)
        with pytest.raises(ValueError, match="MNEMO_INVALID_INPUT"):
            port.get_context(context_payload(source_changes={"relative_path": 7}))


def test_durable_port_rejects_a_non_object_source_overview(tmp_path: Path) -> None:
    with build_checkpoint_runtime(
        LocalConfig.defaults(tmp_path / "invalid source overview")
    ) as runtime:
        port = DurableMcpContextPort(runtime.checkpoint_service)
        with pytest.raises(ValueError, match="MNEMO_INVALID_INPUT"):
            port.get_context(context_payload(source_overview="not-an-object"))


def test_durable_port_resolves_an_exact_dbt_manifest_file_for_lineage(tmp_path: Path) -> None:
    config = LocalConfig.defaults(tmp_path / "manifest file lineage")
    project_scope = MemoryScope(
        OwnerId.from_string(IDS["owner_id"]),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string(IDS["workspace_id"]),
        ProjectId.from_string(IDS["project_id"]),
    )
    with build_checkpoint_runtime(config, dbt_parser=DbtManifestParser()) as runtime:
        assert runtime.dbt_manifest_service is not None
        runtime.dbt_manifest_service.ingest(
            IngestManifest(
                project_scope,
                DBT_FIXTURE.read_bytes(),
                "tests/fixtures/dbt/manifest-v12.json",
                datetime(2026, 8, 4, tzinfo=UTC),
            )
        )
        port = DurableMcpContextPort(
            runtime.checkpoint_service,
            UnifiedContextService(runtime.checkpoint_service, runtime.dbt_manifest_service),
        )
        packet = ContextPacket.from_dict(
            port.get_context(
                context_payload(
                    dbt_lineage={
                        "relative_path": "models/marts/fct_orders.sql",
                        "direction": "downstream",
                    }
                )
            )
        )
    assert packet.structural_items
    assert all(
        '"start_node":"model.mnemo_analytics.fct_orders"' in item.content
        for item in packet.structural_items
    )


def test_durable_port_returns_an_opt_in_bounded_current_dbt_excerpt(tmp_path: Path) -> None:
    project = tmp_path / "dbt excerpt project"
    model = project / "models" / "marts" / "fct_orders.sql"
    model.parent.mkdir(parents=True)
    project.joinpath("dbt_project.yml").write_text("name: excerpt\n")
    model.write_text("select\n  order_id,\n  amount\nfrom raw.orders\n")
    config = LocalConfig.defaults(tmp_path / "dbt excerpt data")
    project_scope = MemoryScope(
        OwnerId.from_string(IDS["owner_id"]),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string(IDS["workspace_id"]),
        ProjectId.from_string(IDS["project_id"]),
    )
    bindings = LocalDbtProjectBindingStore(config.data_directory)
    bindings.set(DbtProjectBinding(project.resolve(), project_scope))
    with build_checkpoint_runtime(config, dbt_parser=DbtManifestParser()) as runtime:
        assert runtime.dbt_manifest_service is not None
        runtime.dbt_manifest_service.ingest(
            IngestManifest(
                project_scope,
                DBT_FIXTURE.read_bytes(),
                "manifest.json",
                datetime(2026, 8, 5, tzinfo=UTC),
            )
        )
        port = DurableMcpContextPort(
            runtime.checkpoint_service,
            UnifiedContextService(
                runtime.checkpoint_service,
                runtime.dbt_manifest_service,
                dbt_code_excerpts=DbtLocalCodeExcerptReader(
                    bindings, lambda: datetime(2026, 8, 5, tzinfo=UTC)
                ),
            ),
        )
        packet = ContextPacket.from_dict(
            port.get_context(
                context_payload(
                    dbt_lineage={
                        "relative_path": "models/marts/fct_orders.sql",
                        "direction": "downstream",
                        "include_code_excerpt": True,
                        "excerpt_start_line": 2,
                        "excerpt_maximum_lines": 2,
                    }
                )
            )
        )
        with pytest.raises(ValueError, match="MNEMO_INVALID_INPUT"):
            port.get_context(
                context_payload(
                    dbt_lineage={
                        "relative_path": "models/marts/fct_orders.sql",
                        "direction": "downstream",
                        "excerpt_start_line": 2,
                    }
                )
            )

    excerpts = [
        json.loads(item.content)
        for item in packet.structural_items
        if json.loads(item.content).get("query_kind") == "code_excerpt"
    ]
    assert excerpts == [
        {
            "end_line": 3,
            "excerpt": "  order_id,\n  amount",
            "manifest_currentness": "unknown",
            "node_unique_id": "model.mnemo_analytics.fct_orders",
            "query_kind": "code_excerpt",
            "relative_file": "models/marts/fct_orders.sql",
            "start_line": 2,
        }
    ]


def test_durable_port_resolves_current_dbt_state_after_scope_validation(tmp_path: Path) -> None:
    config = LocalConfig.defaults(tmp_path / "current manifest")
    project_scope = MemoryScope(
        OwnerId.from_string(IDS["owner_id"]),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string(IDS["workspace_id"]),
        ProjectId.from_string(IDS["project_id"]),
    )
    source_state = SourceStateFingerprint(
        git_commit="a" * 40,
        working_tree_fingerprint="sha256:" + "b" * 64,
        dirty=False,
    )
    resolved_scopes: list[MemoryScope] = []

    def resolve(scope: MemoryScope) -> SourceStateFingerprint:
        resolved_scopes.append(scope)
        return source_state

    with build_checkpoint_runtime(config, dbt_parser=DbtManifestParser()) as runtime:
        assert runtime.dbt_manifest_service is not None
        runtime.dbt_manifest_service.ingest(
            IngestManifest(
                project_scope,
                DBT_FIXTURE.read_bytes(),
                "tests/fixtures/dbt/manifest-v12.json",
                datetime(2026, 8, 5, tzinfo=UTC),
                source_state=source_state,
            )
        )
        context_service = UnifiedContextService(
            runtime.checkpoint_service, runtime.dbt_manifest_service
        )
        port = DurableMcpContextPort(
            runtime.checkpoint_service,
            context_service,
            current_dbt_source_state=resolve,
        )
        packet = ContextPacket.from_dict(
            port.get_context(
                context_payload(
                    dbt_lineage={
                        "relative_path": "models/marts/fct_orders.sql",
                        "direction": "downstream",
                    }
                )
            )
        )
        failed_observation = DurableMcpContextPort(
            runtime.checkpoint_service,
            context_service,
            current_dbt_source_state=lambda _scope: (_ for _ in ()).throw(OSError()),
        )
        unknown = ContextPacket.from_dict(
            failed_observation.get_context(
                context_payload(
                    dbt_lineage={
                        "relative_path": "models/marts/fct_orders.sql",
                        "direction": "downstream",
                    }
                )
            )
        )

    assert len(resolved_scopes) == 1
    assert resolved_scopes[0].project_id == project_scope.project_id
    assert packet.structural_items
    assert all(
        json.loads(item.content)["currentness"] == "current" for item in packet.structural_items
    )
    assert unknown.structural_items
    assert all(
        json.loads(item.content)["currentness"] == "unknown" for item in unknown.structural_items
    )


def test_durable_port_returns_a_bounded_dbt_path_through_the_existing_tool(
    tmp_path: Path,
) -> None:
    config = LocalConfig.defaults(tmp_path / "manifest path")
    project_scope = MemoryScope(
        OwnerId.from_string(IDS["owner_id"]),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string(IDS["workspace_id"]),
        ProjectId.from_string(IDS["project_id"]),
    )
    with build_checkpoint_runtime(config, dbt_parser=DbtManifestParser()) as runtime:
        assert runtime.dbt_manifest_service is not None
        runtime.dbt_manifest_service.ingest(
            IngestManifest(
                project_scope,
                DBT_FIXTURE.read_bytes(),
                "tests/fixtures/dbt/manifest-v12.json",
                datetime(2026, 8, 4, tzinfo=UTC),
            )
        )
        port = DurableMcpContextPort(
            runtime.checkpoint_service,
            UnifiedContextService(runtime.checkpoint_service, runtime.dbt_manifest_service),
        )
        packet = ContextPacket.from_dict(
            port.get_context(
                context_payload(
                    dbt_lineage={
                        "relative_path": "models/marts/fct_orders.sql",
                        "direction": "downstream",
                        "path_to_unique_id": "metric.mnemo_analytics.customer_value",
                        "maximum_depth": 4,
                    }
                )
            )
        )

    assert [json.loads(item.content)["node_unique_id"] for item in packet.structural_items] == [
        "model.mnemo_analytics.mart_customer_value",
        "semantic_model.mnemo_analytics.customer_value",
        "metric.mnemo_analytics.customer_value",
    ]
    assert all(json.loads(item.content)["query_kind"] == "path" for item in packet.structural_items)


def test_durable_port_rejects_a_non_string_dbt_path_destination(tmp_path: Path) -> None:
    with build_checkpoint_runtime(
        LocalConfig.defaults(tmp_path / "invalid dbt path"), dbt_parser=DbtManifestParser()
    ) as runtime:
        assert runtime.dbt_manifest_service is not None
        port = DurableMcpContextPort(
            runtime.checkpoint_service,
            UnifiedContextService(runtime.checkpoint_service, runtime.dbt_manifest_service),
        )
        with pytest.raises(ValueError, match="MNEMO_INVALID_INPUT"):
            port.get_context(
                context_payload(
                    dbt_lineage={
                        "unique_id": "model.mnemo_analytics.fct_orders",
                        "direction": "downstream",
                        "path_to_unique_id": 7,
                    }
                )
            )


def test_durable_port_returns_direct_dbt_test_coverage_through_get_context(
    tmp_path: Path,
) -> None:
    config = LocalConfig.defaults(tmp_path / "test coverage")
    project_scope = MemoryScope(
        OwnerId.from_string(IDS["owner_id"]),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string(IDS["workspace_id"]),
        ProjectId.from_string(IDS["project_id"]),
    )
    with build_checkpoint_runtime(config, dbt_parser=DbtManifestParser()) as runtime:
        assert runtime.dbt_manifest_service is not None
        runtime.dbt_manifest_service.ingest(
            IngestManifest(
                project_scope,
                DBT_FIXTURE.read_bytes(),
                "tests/fixtures/dbt/manifest-v12.json",
                datetime(2026, 8, 5, tzinfo=UTC),
            )
        )
        port = DurableMcpContextPort(
            runtime.checkpoint_service,
            UnifiedContextService(runtime.checkpoint_service, runtime.dbt_manifest_service),
        )
        packet = ContextPacket.from_dict(
            port.get_context(
                context_payload(
                    dbt_test_coverage={
                        "relative_path": "models/marts/fct_orders.sql",
                        "maximum_tests": 8,
                    }
                )
            )
        )

    assert len(packet.structural_items) == 1
    value = json.loads(packet.structural_items[0].content)
    assert value["subject_node"] == "model.mnemo_analytics.fct_orders"
    assert value["test_unique_id"] == "test.mnemo_analytics.unique_fct_orders"


def test_durable_port_returns_latest_dbt_changes_with_automatic_current_state(
    tmp_path: Path,
) -> None:
    config = LocalConfig.defaults(tmp_path / "dbt changes")
    project_scope = MemoryScope(
        OwnerId.from_string(IDS["owner_id"]),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string(IDS["workspace_id"]),
        ProjectId.from_string(IDS["project_id"]),
    )
    state = SourceStateFingerprint(
        git_commit="a" * 40,
        working_tree_fingerprint="sha256:" + "c" * 64,
        dirty=False,
    )
    with build_checkpoint_runtime(config, dbt_parser=DbtManifestParser()) as runtime:
        assert runtime.dbt_manifest_service is not None
        first = runtime.dbt_manifest_service.ingest(
            IngestManifest(
                project_scope,
                DBT_FIXTURE.read_bytes(),
                "manifest.json",
                datetime(2026, 8, 5, tzinfo=UTC),
                source_state=state,
            )
        ).snapshot
        runtime.dbt_manifest_service.ingest(
            IngestManifest(
                project_scope,
                DBT_FIXTURE.read_text().replace("fact-orders", "fact-orders-v2"),
                "manifest.json",
                datetime(2026, 8, 5, 0, 0, 1, tzinfo=UTC),
                expected_active_snapshot_id=first.snapshot_id,
                source_state=state,
            )
        )
        port = DurableMcpContextPort(
            runtime.checkpoint_service,
            UnifiedContextService(runtime.checkpoint_service, runtime.dbt_manifest_service),
            current_dbt_source_state=lambda _scope: state,
        )
        packet = ContextPacket.from_dict(
            port.get_context(context_payload(dbt_changes={"maximum_affected_nodes": 8}))
        )

    assert len(packet.structural_items) == 1
    value = json.loads(packet.structural_items[0].content)
    assert value["query_kind"] == "changes"
    assert value["currentness"] == "current"
    assert value["changes"][0]["unique_id"] == "model.mnemo_analytics.fct_orders"


def test_durable_port_returns_exact_bounded_dbt_selector_matches(tmp_path: Path) -> None:
    config = LocalConfig.defaults(tmp_path / "dbt selector")
    project_scope = MemoryScope(
        OwnerId.from_string(IDS["owner_id"]),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string(IDS["workspace_id"]),
        ProjectId.from_string(IDS["project_id"]),
    )
    with build_checkpoint_runtime(config, dbt_parser=DbtManifestParser()) as runtime:
        assert runtime.dbt_manifest_service is not None
        runtime.dbt_manifest_service.ingest(
            IngestManifest(
                project_scope,
                DBT_FIXTURE.read_bytes(),
                "tests/fixtures/dbt/manifest-v12.json",
                datetime(2026, 8, 5, tzinfo=UTC),
            )
        )
        port = DurableMcpContextPort(
            runtime.checkpoint_service,
            UnifiedContextService(runtime.checkpoint_service, runtime.dbt_manifest_service),
        )
        packet = ContextPacket.from_dict(
            port.get_context(
                context_payload(
                    dbt_selector={
                        "resource_type": "model",
                        "package_name": "mnemo_analytics",
                        "tag": "mart",
                        "maximum_nodes": 2,
                    }
                )
            )
        )

    values = [json.loads(item.content) for item in packet.structural_items]
    assert [value["node_unique_id"] for value in values] == [
        "model.mnemo_analytics.dim_customers",
        "model.mnemo_analytics.fct_orders",
    ]
    assert all(value["query_kind"] == "selector" for value in values)
    assert any(omission.item_id == "dbt-selector" for omission in packet.omissions)


def test_durable_port_returns_observed_dbt_source_freshness(tmp_path: Path) -> None:
    config = LocalConfig.defaults(tmp_path / "dbt freshness")
    project_scope = MemoryScope(
        OwnerId.from_string(IDS["owner_id"]),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string(IDS["workspace_id"]),
        ProjectId.from_string(IDS["project_id"]),
    )
    with build_checkpoint_runtime(
        config,
        dbt_parser=DbtManifestParser(),
        dbt_source_freshness_parser=DbtSourceFreshnessParser(),
    ) as runtime:
        assert runtime.dbt_manifest_service is not None
        snapshot = runtime.dbt_manifest_service.ingest(
            IngestManifest(
                project_scope,
                DBT_FIXTURE.read_bytes(),
                "tests/fixtures/dbt/manifest-v12.json",
                datetime(2026, 8, 5, tzinfo=UTC),
            )
        ).snapshot
        runtime.dbt_manifest_service.ingest_source_freshness(
            IngestSourceFreshness(
                project_scope,
                snapshot.snapshot_id,
                DBT_SOURCES_FIXTURE.read_bytes(),
                "sources.json",
                datetime(2026, 8, 5, 2, 1, tzinfo=UTC),
            )
        )
        port = DurableMcpContextPort(
            runtime.checkpoint_service,
            UnifiedContextService(runtime.checkpoint_service, runtime.dbt_manifest_service),
        )
        packet = ContextPacket.from_dict(
            port.get_context(
                context_payload(dbt_freshness={"unique_id": "source.mnemo_analytics.raw_orders"})
            )
        )

    assert len(packet.structural_items) == 1
    value = json.loads(packet.structural_items[0].content)
    assert value["status"] == "warn"
    assert value["age_seconds"] == 5400.0
    assert "private" not in packet.structural_items[0].content


def test_durable_port_requires_exactly_one_source_impact_target(tmp_path: Path) -> None:
    with build_checkpoint_runtime(LocalConfig.defaults(tmp_path / "runtime")) as runtime:
        port = DurableMcpContextPort(runtime.checkpoint_service)
        request = context_payload(source_impact={"symbol": "core", "relative_path": "core.py"})

        with pytest.raises(ValueError, match="MNEMO_INVALID_INPUT"):
            port.get_context(request)


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
                "list_skills",
                "get_skill",
                "explain_context",
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
            rendered = await session.call_tool("get_context", {**IDS, "render_for": "codex"})
            assert rendered.isError is False
            rendered_result = rendered.structuredContent or {}
            assert rendered_result["rendered_for"] == "codex"
            assert str(rendered_result["rendered_context"]).startswith(
                "MNEMO_CONTEXT_V1 client=codex\n"
            )
            rendered_packet = ContextPacket.from_dict(rendered_result["context_packet"])
            assert rendered_packet.active_task_checkpoint == packet.active_task_checkpoint
            assert str(rendered_packet.request_id) in str(rendered_result["rendered_context"])
            explanation = await session.call_tool(
                "explain_context", {"context_packet": context.structuredContent or {}}
            )
            assert explanation.isError is False
            explained = explanation.structuredContent or {}
            assert explained["included"][0]["item_id"] == packet.active_task_checkpoint.item_id
            assert packet.active_task_checkpoint.content not in json.dumps(explained)
            malformed = await session.call_tool(
                "explain_context", {"context_packet": {"unexpected": True}}
            )
            assert malformed.isError is True
            assert "MNEMO_INVALID_CONTEXT_PACKET" in str(malformed.content)
            oversized_marker = "must-not-escape" + "x" * 131_072
            oversized = await session.call_tool(
                "explain_context", {"context_packet": {"padding": oversized_marker}}
            )
            assert oversized.isError is True
            assert "MNEMO_INVALID_CONTEXT_PACKET" in str(oversized.content)
            assert "must-not-escape" not in str(oversized.content)
            unsupported_rendering = await session.call_tool(
                "get_context", {**IDS, "render_for": "unsupported"}
            )
            assert unsupported_rendering.isError is True
            invalid = await session.call_tool("save_checkpoint", {"operation": "invalid"})
            assert invalid.isError is True
            still_valid = await session.call_tool("get_context", IDS)
            assert still_valid.isError is False

    asyncio.run(asyncio.wait_for(exercise(), timeout=15))


def test_real_stdio_server_resolves_enabled_project_scope_without_uuid_arguments(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        project = tmp_path / "registered project Ω"
        project.mkdir()
        data = tmp_path / "registered data Ω"
        binding = LocalMemoryProjectBindingStore(data).enable(project)
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "mnemo_memory.apps.mcp.server", "--data-dir", str(data)],
            cwd=project,
        )
        async with stdio_client(parameters) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            payload = save_payload()
            for name in IDS:
                payload.pop(name)
            created = await session.call_tool("save_checkpoint", payload)
            assert created.isError is False
            context = await session.call_tool("get_context", {})
            assert context.isError is False
            packet = ContextPacket.from_dict(context.structuredContent or {})
            assert packet.owner_scope == binding.checkpoint_scope
            assert packet.active_task_checkpoint is not None

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
