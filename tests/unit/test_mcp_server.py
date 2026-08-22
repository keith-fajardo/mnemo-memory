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

from mnemo_memory.apps.mcp import server as mcp_server_module
from mnemo_memory.apps.mcp.server import SERVER_NAME, SERVER_VERSION, create_server
from mnemo_memory.connectors.automatic_memory.checkpoint_evidence import (
    CheckpointFileEvidenceResolver,
)
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
from mnemo_memory.packages.application.mcp_port import McpContextPort
from mnemo_memory.packages.application.unified_context import UnifiedContextService
from mnemo_memory.packages.domain import (
    ContextBudget,
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
from mnemo_memory.packages.project_index import (
    PythonSourceParser,
    PythonSourceParseRequest,
    SourceStructureParser,
    SourceStructureParseRequest,
)
from mnemo_memory.packages.storage import SQLiteSourceStructureRepository

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
    recap_schema = tools[0].inputSchema["properties"]["recap_days"]
    assert any(candidate.get("maximum") == 90 for candidate in recap_schema["anyOf"])
    assert tools[1].annotations is not None and tools[1].annotations.readOnlyHint is True
    assert tools[2].annotations is not None and tools[2].annotations.readOnlyHint is True
    assert tools[3].annotations is not None and tools[3].annotations.readOnlyHint is True
    assert tools[4].annotations is not None and tools[4].annotations.readOnlyHint is False
    assert all(tool.inputSchema["additionalProperties"] is False for tool in tools)
    assert "operation" in tools[4].inputSchema["properties"]
    assert "lessons" in tools[4].inputSchema["properties"]
    assert "pattern" not in tools[4].inputSchema["properties"]["operation"]
    assert (
        "without resending the complete checkpoint"
        in tools[4].inputSchema["properties"]["operation"]["description"]
    )
    assert "exactly one" in tools[4].inputSchema["properties"]["lessons"]["description"]
    evidence_schema = tools[4].inputSchema["properties"]["evidence_references"]
    assert "evidence_files" in tools[4].inputSchema["properties"]
    assert (
        "Preferred local shorthand"
        in tools[4].inputSchema["properties"]["evidence_files"]["description"]
    )
    evidence_array = next(
        candidate for candidate in evidence_schema["anyOf"] if candidate.get("type") == "array"
    )
    evidence_reference = evidence_array["items"]
    assert evidence_reference["title"] == "CheckpointEvidenceReferenceInput"
    assert set(evidence_reference["required"]) == {
        "evidence_id",
        "source_id",
        "source_type",
        "trust_class",
        "immutable_source_ref",
        "content_hash",
        "location",
        "observed_at",
        "verification_status",
    }
    location = tools[4].inputSchema["$defs"]["CheckpointEvidenceLocationInput"]
    assert location["required"] == ["uri"]
    assert location["additionalProperties"] is False
    assert evidence_reference["additionalProperties"] is False
    assert set(location["properties"]) == {
        "uri",
        "start_line",
        "start_column",
        "end_line",
        "end_column",
    }
    assert "source_query" in tools[0].inputSchema["properties"]
    assert "query" in tools[0].inputSchema["properties"]
    assert "knowledge_query" in tools[0].inputSchema["properties"]
    assert "source_impact" in tools[0].inputSchema["properties"]
    assert "source_changes" in tools[0].inputSchema["properties"]
    assert "source_overview" in tools[0].inputSchema["properties"]
    source_overview = next(
        candidate
        for candidate in tools[0].inputSchema["properties"]["source_overview"]["anyOf"]
        if candidate.get("type") == "object"
    )
    assert source_overview["additionalProperties"] is False
    assert set(source_overview["properties"]) == {
        "maximum_files",
        "maximum_modules",
        "maximum_declarations",
        "maximum_components",
        "maximum_relationships",
        "snapshot_id",
        "current_source_digest",
        "require_current",
    }
    assert source_overview["properties"]["maximum_relationships"]["maximum"] == 32
    assert "dbt_test_coverage" in tools[0].inputSchema["properties"]
    assert "dbt_selector" in tools[0].inputSchema["properties"]
    dbt_selector = next(
        candidate
        for candidate in tools[0].inputSchema["properties"]["dbt_selector"]["anyOf"]
        if candidate.get("type") == "object"
    )
    assert dbt_selector["additionalProperties"] is False
    assert set(dbt_selector["properties"]) == {
        "resource_type",
        "package_name",
        "tag",
        "maximum_nodes",
        "include_nodes",
        "snapshot_id",
        "current_content_digest",
        "require_current",
    }
    assert dbt_selector["properties"]["maximum_nodes"]["maximum"] == 8
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
    assert "event_summary" in tools[4].inputSchema["properties"]
    token_estimate_property = tools[4].inputSchema["properties"]["token_estimate"]
    token_estimate_schema = next(
        candidate
        for candidate in token_estimate_property["anyOf"]
        if candidate.get("type") == "integer"
    )
    assert "maximum" not in token_estimate_schema
    assert "Deprecated compatibility field" in token_estimate_property["description"]
    assert "token_estimate" not in tools[4].inputSchema.get("required", [])
    assert set(tools[3].inputSchema["properties"]) == {"context_packet"}
    assert set(tools[1].inputSchema["required"]) == {"client"}
    assert set(tools[2].inputSchema["required"]) == {"name", "client"}
    for name in IDS:
        assert name not in tools[0].inputSchema.get("required", [])
        assert name not in tools[1].inputSchema.get("required", [])
        assert name not in tools[2].inputSchema.get("required", [])
        assert name not in tools[4].inputSchema.get("required", [])


def test_server_lists_verifier_only_when_experimental_semantic_memory_is_enabled(
    tmp_path: Path,
) -> None:
    async def list_tools() -> list[Tool]:
        with build_checkpoint_runtime(LocalConfig.defaults(tmp_path / "runtime")) as runtime:
            return list(
                await create_server(
                    DurableMcpContextPort(runtime.checkpoint_service),
                    experimental_semantic_memory_enabled=True,
                ).list_tools()
            )

    tools = asyncio.run(list_tools())
    names = [tool.name for tool in tools]

    assert names == [
        "get_context",
        "list_skills",
        "get_skill",
        "explain_context",
        "verify_against_memory",
        "save_checkpoint",
    ]
    verifier = tools[4]
    assert verifier.annotations is not None and verifier.annotations.readOnlyHint is True
    assert verifier.annotations.destructiveHint is False
    assert verifier.annotations.openWorldHint is False
    assert set(verifier.inputSchema["properties"]) == {
        "candidate",
        "owner_id",
        "workspace_id",
        "project_id",
        "session_id",
        "task_id",
        "maximum_mismatches",
        "reconcile",
    }
    assert verifier.inputSchema["properties"]["maximum_mismatches"]["maximum"] == 32
    assert verifier.inputSchema["properties"]["reconcile"]["default"] is False


def test_server_lists_episodic_tools_only_when_episodic_extraction_is_enabled(
    tmp_path: Path,
) -> None:
    with build_checkpoint_runtime(LocalConfig.defaults(tmp_path / "runtime")) as runtime:
        server = create_server(
            DurableMcpContextPort(runtime.checkpoint_service),
            episodic_extraction_enabled=True,
        )
        tools = list(asyncio.run(server.list_tools()))
        names = [tool.name for tool in tools]

        assert names == [
            "get_context",
            "list_skills",
            "get_skill",
            "explain_context",
            "save_checkpoint",
            "extract_episodic",
            "submit_episodic_candidates",
        ]

        tool_manager_tools = server._tool_manager._tools
        for name in ("extract_episodic", "submit_episodic_candidates"):
            assert name in tool_manager_tools
            assert tool_manager_tools[name].parameters["additionalProperties"] is False

        extract_tool = next(tool for tool in tools if tool.name == "extract_episodic")
        assert extract_tool.annotations is not None
        assert extract_tool.annotations.readOnlyHint is False
        assert extract_tool.annotations.destructiveHint is False
        assert extract_tool.annotations.openWorldHint is False
        assert set(extract_tool.inputSchema["properties"]) == {"event_id"}
        assert extract_tool.inputSchema["additionalProperties"] is False

        submit_tool = next(tool for tool in tools if tool.name == "submit_episodic_candidates")
        assert submit_tool.annotations is not None
        assert submit_tool.annotations.readOnlyHint is False
        assert submit_tool.annotations.destructiveHint is False
        assert submit_tool.annotations.openWorldHint is False
        assert set(submit_tool.inputSchema["properties"]) == {"candidates"}
        assert submit_tool.inputSchema["additionalProperties"] is False


def test_server_omits_episodic_tools_by_default(tmp_path: Path) -> None:
    with build_checkpoint_runtime(LocalConfig.defaults(tmp_path / "runtime")) as runtime:
        server = create_server(DurableMcpContextPort(runtime.checkpoint_service))
        tools = list(asyncio.run(server.list_tools()))
        names = [tool.name for tool in tools]

        assert "extract_episodic" not in names
        assert "submit_episodic_candidates" not in names
        assert names == [
            "get_context",
            "list_skills",
            "get_skill",
            "explain_context",
            "save_checkpoint",
        ]
        assert "extract_episodic" not in server._tool_manager._tools
        assert "submit_episodic_candidates" not in server._tool_manager._tools


def test_deferred_local_port_keeps_runtime_and_source_refresh_out_of_tool_listing() -> None:
    events: list[object] = []

    class RecordingPort:
        def get_context(self, request: dict[str, object]) -> dict[str, object]:
            events.append(("get_context", request))
            return request

        def list_skills(self, request: dict[str, object]) -> dict[str, object]:
            events.append(("list_skills", request))
            return request

        def get_skill(self, request: dict[str, object]) -> dict[str, object]:
            events.append(("get_skill", request))
            return request

        def save_checkpoint(self, request: dict[str, object]) -> dict[str, object]:
            events.append(("save_checkpoint", request))
            return request

        def extract_episodic(self, request: dict[str, object]) -> dict[str, object]:
            events.append(("extract_episodic", request))
            return request

        def submit_episodic_candidates(self, request: dict[str, object]) -> dict[str, object]:
            events.append(("submit_episodic_candidates", request))
            return request

    class RecordingSession:
        port: McpContextPort = RecordingPort()

        def refresh_source(self) -> None:
            events.append("refresh_source")

        def close(self) -> None:
            events.append("close")

    def build_session() -> RecordingSession:
        events.append("build")
        return RecordingSession()

    deferred = mcp_server_module._DeferredMcpContextPort(build_session)
    tools = asyncio.run(create_server(deferred).list_tools())
    assert [tool.name for tool in tools] == [
        "get_context",
        "list_skills",
        "get_skill",
        "explain_context",
        "save_checkpoint",
    ]
    assert events == []

    assert deferred.get_context({"source_query": None}) == {"source_query": None}
    assert events == ["build", ("get_context", {"source_query": None})]
    deferred.get_context({"source_changes": {"relative_path": "src/service.py"}})
    deferred.get_context({"source_overview": {}})
    assert events.count("build") == 1
    assert events.count("refresh_source") == 1

    deferred.close()
    deferred.close()
    assert events.count("close") == 1

    unused = mcp_server_module._DeferredMcpContextPort(build_session)
    unused.close()
    assert events.count("build") == 1


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


def test_durable_port_applies_personal_budget_and_capture_consent_defaults(
    tmp_path: Path,
) -> None:
    with build_checkpoint_runtime(LocalConfig.defaults(tmp_path / "settings-runtime")) as runtime:
        budget = ContextBudget(knowledge=321, total_limit=4_321)
        port = DurableMcpContextPort(
            runtime.checkpoint_service,
            default_budget=budget,
            approved_event_capture_enabled=False,
        )

        packet = ContextPacket.from_dict(port.get_context(context_payload()))
        assert packet.budget.knowledge == 321
        assert packet.budget.total_limit == 4_321
        with pytest.raises(ValueError, match="MNEMO_INVALID_INPUT"):
            port.save_checkpoint(
                save_payload(
                    "record_event",
                    event_kind="decision",
                    event_summary="A setting-disabled event",
                    source_event_key="settings-disabled-event",
                )
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


def test_durable_port_estimates_checkpoint_tokens_when_caller_omits_them(
    tmp_path: Path,
) -> None:
    payload: dict[str, object] = {
        "operation": "create",
        **IDS,
        "task_objective": "Save a concise durable handoff",
        "current_state": "The requested walkthrough is complete.",
        "evidence_references": [EVIDENCE],
    }
    with build_checkpoint_runtime(LocalConfig.defaults(tmp_path / "estimated-tokens")) as runtime:
        port = DurableMcpContextPort(runtime.checkpoint_service)

        created = port.save_checkpoint(payload)

        assert created["persistence"] == "durable"
        packet = ContextPacket.from_dict(port.get_context(context_payload()))
        assert packet.active_task_checkpoint is not None
        canonical = json.loads(packet.active_task_checkpoint.content)
        estimated_tokens = canonical["token_estimate"]
        canonical["token_estimate"] = 0
        assert (
            estimated_tokens
            == (len(json.dumps(canonical, sort_keys=True, separators=(",", ":"))) + 2) // 3
        )
        assert 0 < estimated_tokens <= 600


def test_durable_port_ignores_caller_undercount_and_compacts_to_200_tokens(
    tmp_path: Path,
) -> None:
    long_value = "Keep this checkpoint fact concise after deterministic compaction. " * 30
    payload = save_payload(
        token_estimate=1,
        task_objective=long_value,
        current_state=long_value,
        completed_work=[long_value, long_value],
        remaining_work=[long_value, long_value],
        decisions=[long_value, long_value],
        failures=[long_value],
        blockers=[long_value],
        relevant_files=[long_value],
        relevant_artifacts=[long_value],
        verification_performed=[long_value, long_value],
    )
    with build_checkpoint_runtime(LocalConfig.defaults(tmp_path / "compaction")) as runtime:
        port = DurableMcpContextPort(runtime.checkpoint_service)

        created = port.save_checkpoint(payload)
        packet = ContextPacket.from_dict(port.get_context(context_payload()))

    assert isinstance(created["token_estimate"], int)
    assert created["token_estimate"] <= 200
    compaction = created["compaction"]
    assert isinstance(compaction, dict)
    assert compaction["target_tokens"] == 200
    assert compaction["original_token_estimate"] > 200
    assert packet.active_task_checkpoint is not None
    canonical = json.loads(packet.active_task_checkpoint.content)
    assert canonical["token_estimate"] <= 200
    assert "failures" not in canonical
    assert "relevant_artifacts" not in canonical
    assert all(value != [] and value is not None for value in canonical.values())


def test_durable_port_normalizes_valid_uppercase_uuid_input(tmp_path: Path) -> None:
    payload = save_payload(
        task_id="AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA",
        evidence_references=[
            {
                **EVIDENCE,
                "evidence_id": "BBBBBBBB-BBBB-4BBB-8BBB-BBBBBBBBBBBB",
                "source_id": "CCCCCCCC-CCCC-4CCC-8CCC-CCCCCCCCCCCC",
            }
        ],
    )
    with build_checkpoint_runtime(LocalConfig.defaults(tmp_path / "uppercase")) as runtime:
        created = DurableMcpContextPort(runtime.checkpoint_service).save_checkpoint(payload)

    assert created["persistence"] == "durable"


def test_durable_port_resolves_local_evidence_files_and_lesson_ids(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    source = project / "checkpoint.py"
    source.write_text("checkpoint = True\n", encoding="utf-8")
    payload = save_payload(
        evidence_references=None,
        evidence_files=["checkpoint.py"],
        lessons=[
            {
                "trigger": "The previous save was too large.",
                "mistaken_assumption": "The caller estimate enforced the budget.",
                "correction": "Mnemo must calculate the canonical estimate.",
                "prevention": "Compact before persistence.",
            }
        ],
    )
    with build_checkpoint_runtime(LocalConfig.defaults(tmp_path / "evidence-files")) as runtime:
        port = DurableMcpContextPort(
            runtime.checkpoint_service,
            checkpoint_evidence_resolver=CheckpointFileEvidenceResolver(project),
        )
        created = port.save_checkpoint(payload)
        packet = ContextPacket.from_dict(port.get_context(context_payload()))

    assert created["persistence"] == "durable"
    assert packet.active_task_checkpoint is not None
    reference = packet.active_task_checkpoint.evidence_references[0]
    assert reference.content_hash.startswith("sha256:")
    assert len(reference.content_hash) == 71
    assert reference.location.to_dict() == {"uri": "repo://checkpoint.py"}
    content = json.loads(packet.active_task_checkpoint.content)
    assert content["lessons"][0]["evidence_ids"] == [str(reference.evidence_id)]


def test_durable_port_emits_content_free_failure_and_success_observations(
    tmp_path: Path,
) -> None:
    observations: list[tuple[object, ...]] = []

    def observe(*values: object) -> None:
        observations.append(values)

    with build_checkpoint_runtime(LocalConfig.defaults(tmp_path / "observations")) as runtime:
        port = DurableMcpContextPort(
            runtime.checkpoint_service,
            checkpoint_save_observer=observe,
        )
        with pytest.raises(ValueError, match="MNEMO_EVIDENCE_REQUIRED"):
            port.save_checkpoint(save_payload(evidence_references=None))
        port.save_checkpoint(save_payload())

    assert observations[0][1:4] == ("create", "failure", "MNEMO_EVIDENCE_REQUIRED")
    assert observations[0][5:] == (None, None)
    assert observations[1][1:4] == ("create", "success", None)
    assert isinstance(observations[1][5], int)


def test_durable_port_accepts_uri_only_evidence_but_rejects_partial_spans(
    tmp_path: Path,
) -> None:
    uri_only = {**EVIDENCE, "location": {"uri": "fixture://uri-only"}}
    with build_checkpoint_runtime(LocalConfig.defaults(tmp_path / "uri-only")) as runtime:
        port = DurableMcpContextPort(runtime.checkpoint_service)

        created = port.save_checkpoint(save_payload(evidence_references=[uri_only]))

        assert created["persistence"] == "durable"
        partial_span = {
            **EVIDENCE,
            "evidence_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "location": {"uri": "fixture://partial", "start_line": 1},
        }
        with pytest.raises(ValueError, match="MNEMO_INVALID_INPUT"):
            port.save_checkpoint(
                save_payload(
                    task_id="99999999-9999-4999-8999-999999999999",
                    evidence_references=[partial_span],
                )
            )
        unknown_location = {
            **EVIDENCE,
            "evidence_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "location": {"uri": "fixture://unknown", "excerpt": "must not be accepted"},
        }
        with pytest.raises(ValueError, match="MNEMO_INVALID_INPUT"):
            port.save_checkpoint(
                save_payload(
                    task_id="88888888-8888-4888-8888-888888888888",
                    evidence_references=[unknown_location],
                )
            )


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


def test_durable_port_returns_bounded_previous_session_recap(tmp_path: Path) -> None:
    with build_checkpoint_runtime(LocalConfig.defaults(tmp_path / "recap")) as runtime:
        port = DurableMcpContextPort(
            runtime.checkpoint_service,
            context_service=UnifiedContextService(runtime.checkpoint_service, None),
        )
        created = port.save_checkpoint(save_payload())
        completed = port.save_checkpoint(
            save_payload(
                "complete",
                checkpoint_id=created["checkpoint_id"],
                expected_revision_id=created["checkpoint_revision_id"],
                current_state="complete",
                remaining_work=[],
            )
        )
        packet = ContextPacket.from_dict(
            port.get_context(context_payload(recap_days=0, total_tokens=1_300))
        )
        with pytest.raises(ValueError, match="MNEMO_INVALID_INPUT"):
            port.get_context(context_payload(recap_days=91))

    assert packet.active_task_checkpoint is None
    assert len(packet.episodic_memories) == 1
    value = json.loads(packet.episodic_memories[0].content)
    assert value["query_kind"] == "checkpoint_recap"
    assert value["revision_id"] == completed["checkpoint_revision_id"]
    assert value["recap_days"] is None
    assert packet.declared_total_tokens < 300
    assert packet.episodic_memories[0].evidence_references


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
    stored_lesson = json.loads(packet.active_task_checkpoint.content)["lessons"][0]
    assert stored_lesson["prevention"].startswith("Check the shared validation")
    assert stored_lesson["prevention"].endswith("a CLI exception catch.")


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
        payload = port.get_context(
            context_payload(
                source_overview={
                    "maximum_modules": 1,
                    "maximum_declarations": 1,
                    "current_source_digest": stored.snapshot.source_digest,
                    "require_current": True,
                }
            )
        )
        packet = ContextPacket.from_dict(payload)

    overview = next(
        item for item in packet.structural_items if item.item_id.startswith("source-overview:")
    )
    assert len(packet.structural_items) == 1
    assert '"kind":"source_architecture_overview"' in overview.content
    assert '"currentness":"current"' in overview.content
    assert str(project) not in overview.content
    assert overview.evidence_references
    assert len(json.dumps(payload)) < 12_000


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
        with pytest.raises(ValueError, match="MNEMO_INVALID_INPUT") as rejected:
            port.get_context(context_payload(source_overview={"select": "private-marker"}))
        assert "private-marker" not in str(rejected.value)


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
        snapshot = runtime.dbt_manifest_service.ingest(
            IngestManifest(
                project_scope,
                DBT_FIXTURE.read_bytes(),
                "tests/fixtures/dbt/manifest-v12.json",
                datetime(2026, 8, 5, tzinfo=UTC),
            )
        ).snapshot
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
        inventory = ContextPacket.from_dict(
            port.get_context(
                context_payload(
                    dbt_selector={"resource_type": "model"},
                    total_tokens=8_000,
                )
            )
        )
        with pytest.raises(ValueError, match="MNEMO_INVALID_INPUT"):
            port.get_context(
                context_payload(
                    dbt_selector={
                        "resource_type": "model",
                        "select": "resource_type:model",
                        "limit": 500,
                    }
                )
            )

    values = [json.loads(item.content) for item in packet.structural_items]
    assert [value["node_unique_id"] for value in values] == [
        "model.mnemo_analytics.dim_customers",
        "model.mnemo_analytics.fct_orders",
    ]
    assert all(value["query_kind"] == "selector" for value in values)
    assert any(omission.item_id == "dbt-selector" for omission in packet.omissions)
    assert len(inventory.structural_items) == 1
    inventory_value = json.loads(inventory.structural_items[0].content)
    assert inventory_value == {
        "currentness": "unknown",
        "filters": {"resource_type": "model"},
        "matched_node_count": 7,
        "node_records_included": False,
        "project_name": "mnemo_analytics",
        "query_kind": "selector_inventory",
        "snapshot_id": str(snapshot.snapshot_id),
    }
    assert inventory.declared_total_tokens < 150
    assert len(json.dumps(inventory.to_dict())) < 8_000


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


def test_real_stdio_handshake_and_tool_listing_do_not_open_local_storage(tmp_path: Path) -> None:
    async def exercise() -> None:
        unavailable_data_directory = tmp_path / "not-a-directory"
        unavailable_data_directory.write_text("occupied", encoding="utf-8")
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "mnemo_memory.apps.mcp.server",
                "--data-dir",
                str(unavailable_data_directory),
            ],
            cwd=ROOT,
        )
        async with stdio_client(parameters) as (read, write), ClientSession(read, write) as session:
            initialized = await session.initialize()
            assert initialized.serverInfo.name == SERVER_NAME
            assert [tool.name for tool in (await session.list_tools()).tools] == [
                "get_context",
                "list_skills",
                "get_skill",
                "explain_context",
                "save_checkpoint",
            ]
            unavailable = await session.call_tool("get_context", IDS)
            assert unavailable.isError is True
            assert "MNEMO_STORAGE_UNAVAILABLE" in str(unavailable.content)

    asyncio.run(exercise())


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
            created = await session.call_tool(
                "save_checkpoint",
                save_payload(
                    evidence_references=[
                        {**EVIDENCE, "location": {"uri": "fixture://stdio-uri-only"}}
                    ]
                ),
            )
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
            private_marker = "must-not-echo-evidence-location"
            invalid_evidence = await session.call_tool(
                "save_checkpoint",
                save_payload(
                    task_id="99999999-9999-4999-8999-999999999999",
                    evidence_references=[
                        {
                            **EVIDENCE,
                            "location": {
                                "uri": "fixture://invalid",
                                "unexpected": private_marker,
                            },
                        }
                    ],
                ),
            )
            assert invalid_evidence.isError is True
            assert private_marker not in str(invalid_evidence.content)
            still_valid = await session.call_tool("get_context", IDS)
            assert still_valid.isError is False

    asyncio.run(asyncio.wait_for(exercise(), timeout=15))


def test_real_stdio_dbt_inventory_is_one_small_strict_result(tmp_path: Path) -> None:
    data = tmp_path / "data"
    project = tmp_path / "dbt project"
    project.mkdir()
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    with build_checkpoint_runtime(
        LocalConfig.defaults(data), dbt_parser=DbtManifestParser()
    ) as runtime:
        assert runtime.dbt_manifest_service is not None
        snapshot = runtime.dbt_manifest_service.ingest(
            IngestManifest(
                binding.scope,
                DBT_FIXTURE.read_bytes(),
                "tests/fixtures/dbt/manifest-v12.json",
                datetime(2026, 8, 7, tzinfo=UTC),
            )
        ).snapshot

    async def exercise() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "mnemo_memory.apps.mcp.server", "--data-dir", str(data)],
            cwd=project,
        )
        async with stdio_client(parameters) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            invalid = await session.call_tool(
                "get_context",
                {
                    "dbt_selector": {
                        "resource_type": "model",
                        "select": "resource_type:model",
                        "limit": 500,
                    },
                    "total_tokens": 8_000,
                    "render_for": "claude-code",
                },
            )
            assert invalid.isError is True
            assert "resource_type:model" not in "".join(
                item.text for item in invalid.content if hasattr(item, "text")
            )

            result = await session.call_tool(
                "get_context",
                {
                    "dbt_selector": {"resource_type": "model"},
                    "total_tokens": 8_000,
                    "render_for": "claude-code",
                },
            )
            assert result.isError is False
            payload = result.structuredContent or {}
            packet = ContextPacket.from_dict(payload["context_packet"])
            assert packet.declared_total_tokens < 150
            assert len(packet.structural_items) == 1
            inventory = json.loads(packet.structural_items[0].content)
            assert inventory["matched_node_count"] == 7
            assert inventory["snapshot_id"] == str(snapshot.snapshot_id)
            assert inventory["node_records_included"] is False
            assert len(json.dumps(payload)) < 12_000

    asyncio.run(asyncio.wait_for(exercise(), timeout=15))


def test_real_stdio_server_resolves_enabled_project_scope_without_uuid_arguments(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        project = tmp_path / "registered project Ω"
        project.mkdir()
        data = tmp_path / "registered data Ω"
        binding = LocalMemoryProjectBindingStore(data).enable(project)
        source_repository = SQLiteSourceStructureRepository(
            data / "mnemo.sqlite3", base_directory=data
        )
        source_repository.migrate()
        source_repository.store_and_activate(
            SourceStructureParser().parse(SourceStructureParseRequest(binding.scope, project))
        )
        current_source = project / "src" / "current_service.py"
        current_source.parent.mkdir()
        current_source.write_text(
            "class CurrentService:\n    pass\n",
            encoding="utf-8",
        )
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
            structure = await session.call_tool("get_context", {"source_query": "CurrentService"})
            assert structure.isError is False
            structural_packet = ContextPacket.from_dict(structure.structuredContent or {})
            assert any(
                '"path":"src/current_service.py"' in item.content
                and '"symbol":"src.current_service.CurrentService"' in item.content
                for item in structural_packet.structural_items
            )
            overview = await session.call_tool(
                "get_context",
                {
                    "source_overview": {},
                    "active_task_checkpoint_tokens": 0,
                    "total_tokens": 8_000,
                },
            )
            assert overview.isError is False
            overview_payload = overview.structuredContent or {}
            overview_packet = ContextPacket.from_dict(overview_payload)
            graph_items = tuple(
                item
                for item in overview_packet.structural_items
                if item.item_id.startswith("source-overview:")
            )
            assert len(graph_items) == 1
            assert graph_items[0].token_estimate > 0
            assert graph_items[0].token_estimate <= 800
            assert len(json.dumps(overview_payload)) < 12_000
            text_payload = "".join(item.text for item in overview.content if hasattr(item, "text"))
            assert len(text_payload) < 12_000

    asyncio.run(asyncio.wait_for(exercise(), timeout=15))


def test_unused_invalid_data_directory_does_not_block_or_dirty_stdio_startup(
    tmp_path: Path,
) -> None:
    occupied = tmp_path / "occupied data directory"
    occupied.write_text("not a directory")
    result = subprocess.run(
        [sys.executable, "-m", "mnemo_memory.apps.mcp.server", "--data-dir", str(occupied)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=8,
    )
    assert result.returncode == 0
    assert "MNEMO_STORAGE_UNAVAILABLE" not in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""
