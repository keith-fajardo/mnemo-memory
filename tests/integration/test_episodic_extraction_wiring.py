"""Integration coverage for the settings-gated Ollama episodic-extraction wiring.

Exercises the exact collaborators the production construction site
(`apps/mcp/server.py::_build_local_mcp_context_session`) builds and passes into
`DurableMcpContextPort` / `create_server`: `OllamaEpisodicProvider` (with a fake HTTP
transport — no live model or network call), `LocalPendingTakeoverStore`, and
`parse_episodic_output`, all gated by `PersonalSettings.episodic_extraction_enabled`.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mnemo_memory.apps.mcp.server import _DeferredMcpContextPort, create_server
from mnemo_memory.connectors.automatic_memory.pending_takeover import LocalPendingTakeoverStore
from mnemo_memory.connectors.ollama.episodic_provider import OllamaEpisodicProvider
from mnemo_memory.packages.application import (
    LocalConfig,
    PersonalSettings,
    build_checkpoint_runtime,
)
from mnemo_memory.packages.application.mcp_durable import DurableMcpContextPort
from mnemo_memory.packages.application.mcp_port import McpContextPort
from mnemo_memory.packages.domain import (
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    MemoryScope,
    OwnerId,
    ProjectId,
    RetentionPolicyId,
    RetentionSchedule,
    ScopeLevel,
    Sensitivity,
    SessionId,
    SourceId,
    SourceTrustClass,
    TaskActivityActor,
    TaskActivityEvent,
    TaskActivityEventKind,
    TaskId,
    VerificationStatus,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.model_gateway.episodic_extraction import parse_episodic_output
from mnemo_memory.packages.storage import ReferenceTaskActivityEventRepository

NOW = datetime(2026, 8, 22, 9, 30, tzinfo=UTC)
_OLLAMA_ENDPOINT = "http://127.0.0.1:11434"


def _scope() -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string("00000000-0000-4000-8000-000000000001"),
        ScopeLevel.TASK,
        Visibility.PROJECT,
        WorkspaceId.from_string("10000000-0000-4000-8000-000000000001"),
        ProjectId.from_string("20000000-0000-4000-8000-000000000001"),
        session_id=SessionId.from_string("30000000-0000-4000-8000-000000000001"),
        task_id=TaskId.from_string("40000000-0000-4000-8000-000000000001"),
    )


def _evidence() -> EvidenceReference:
    return EvidenceReference(
        EvidenceId.from_string("50000000-0000-4000-8000-000000000001"),
        SourceId.from_string("60000000-0000-4000-8000-000000000001"),
        EvidenceSourceType.AGENT_EVENT,
        SourceTrustClass.APPROVED_CHECKPOINT,
        "fixture://task-activity/verified",
        "sha256:" + "a" * 64,
        EvidenceLocation("fixture://task-activity/verified"),
        NOW,
        VerificationStatus.VERIFIED,
    )


def _event(scope: MemoryScope) -> TaskActivityEvent:
    return TaskActivityEvent.create(
        scope=scope,
        kind=TaskActivityEventKind.TASK_OUTCOME,
        actor=TaskActivityActor.AGENT,
        summary="The bounded implementation passed its verification gate.",
        source_event_key="task-outcome:wiring-test",
        sensitivity=Sensitivity.NORMAL,
        retention=RetentionSchedule(
            RetentionPolicyId.from_string("70000000-0000-4000-8000-000000000001"),
            True,
            NOW,
            NOW,
            NOW,
            None,
            None,
        ),
        occurred_at=NOW,
        evidence_references=(_evidence(),),
    )


def _fake_ollama_transport(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Stand-in for the loopback Ollama ``/api/generate`` call. No live model or network call."""
    return {
        "response": json.dumps(
            {
                "candidates": [
                    {
                        "kind": "decision",
                        "claim": "Use the local Ollama provider first.",
                        "confidence": 0.9,
                        "sensitivity": "normal",
                    }
                ]
            }
        )
    }


class _FakeApprovedEventService:
    """Minimal stand-in for `CheckpointApplicationService.record_approved_event`."""

    def record_approved_event(self, command: object) -> None:
        return None


def _build_port(settings: PersonalSettings, *, data_directory: Path) -> DurableMcpContextPort:
    """Mirror the exact wiring at `apps/mcp/server.py::_build_local_mcp_context_session`."""
    scope = _scope()
    events = ReferenceTaskActivityEventRepository()
    events.append_task_activity_event(_event(scope))

    episodic_enabled = settings.episodic_extraction_enabled
    episodic_provider = (
        OllamaEpisodicProvider(
            _OLLAMA_ENDPOINT, settings.model_id or "", transport=_fake_ollama_transport
        )
        if episodic_enabled
        else None
    )
    pending_takeover_store = LocalPendingTakeoverStore(data_directory) if episodic_enabled else None
    return DurableMcpContextPort(
        _FakeApprovedEventService(),  # type: ignore[arg-type]
        default_scope=scope,
        episodic_provider=episodic_provider,
        episodic_output_parser=parse_episodic_output,
        pending_takeover_store=pending_takeover_store,
        task_activity_events=events,
        episodic_extraction_enabled=episodic_enabled,
        local_first_takeover_enabled=settings.experimental_local_first_takeover_enabled,
        takeover_live_calls_authorized=settings.local_first_takeover_live_calls_authorized,
        episodic_route_recorder=None,
    )


class _StaticMcpContextSession:
    """Minimal `_McpContextSession` that hands back an already-built port.

    Stands in for `_LocalMcpContextSession` without needing a full `CheckpointRuntime`,
    so the test can prove `_DeferredMcpContextPort` reaches the exact wired
    `DurableMcpContextPort` production construction builds.
    """

    def __init__(self, port: DurableMcpContextPort) -> None:
        self.port: McpContextPort = port

    def refresh_source(self) -> None:
        return None

    def close(self) -> None:
        return None


def test_deferred_port_delegates_extract_episodic_to_the_wired_provider(
    tmp_path: Path,
) -> None:
    """Regression test: `_DeferredMcpContextPort` is the production port `main()` wires

    MCP tools against. It must not shadow the real `DurableMcpContextPort.extract_episodic`
    with a dead `{"status": "extraction_disabled"}` stub, or the whole live-episodic-extraction
    feature stays unreachable even when fully enabled.
    """
    settings = PersonalSettings(
        optional_model_enabled=True, model_provider="ollama", model_id="ministral-3:8b"
    )
    assert settings.episodic_extraction_enabled is True
    real_port = _build_port(settings, data_directory=tmp_path)
    deferred = _DeferredMcpContextPort(lambda: _StaticMcpContextSession(real_port))

    result = deferred.extract_episodic({})

    # "extracted" (not the old dead stub's unconditional "extraction_disabled") proves the
    # call reached the real wired `DurableMcpContextPort`, not a shadowing stub.
    assert result["status"] == "extracted"


def test_deferred_port_delegates_submit_episodic_candidates_to_the_wired_provider(
    tmp_path: Path,
) -> None:
    settings = PersonalSettings(
        optional_model_enabled=True, model_provider="ollama", model_id="ministral-3:8b"
    )
    real_port = _build_port(settings, data_directory=tmp_path)
    deferred = _DeferredMcpContextPort(lambda: _StaticMcpContextSession(real_port))

    result = deferred.submit_episodic_candidates({"candidates": []})

    # No pending handoff was recorded, but reaching this rejection (rather than the old
    # dead stub's unconditional "extraction_disabled") proves the call was delegated.
    assert result["status"] == "rejected"


def test_extract_episodic_runs_through_the_wired_ollama_provider_when_enabled(
    tmp_path: Path,
) -> None:
    settings = PersonalSettings(
        optional_model_enabled=True, model_provider="ollama", model_id="ministral-3:8b"
    )
    assert settings.episodic_extraction_enabled is True
    port = _build_port(settings, data_directory=tmp_path)

    result = port.extract_episodic({})

    assert result["status"] == "extracted"


def test_extract_episodic_is_disabled_when_settings_leave_it_off(tmp_path: Path) -> None:
    settings = PersonalSettings()
    assert settings.optional_model_enabled is False
    assert settings.episodic_extraction_enabled is False

    port = _build_port(settings, data_directory=tmp_path)

    assert port.extract_episodic({}) == {"status": "extraction_disabled"}


def test_create_server_omits_episodic_tools_when_settings_gate_is_off(tmp_path: Path) -> None:
    with build_checkpoint_runtime(LocalConfig.defaults(tmp_path / "runtime")) as runtime:
        settings = PersonalSettings()
        server = create_server(
            DurableMcpContextPort(runtime.checkpoint_service),
            episodic_extraction_enabled=settings.episodic_extraction_enabled,
        )
        names = [tool.name for tool in asyncio.run(server.list_tools())]

        assert "extract_episodic" not in names
        assert "submit_episodic_candidates" not in names
        assert "extract_episodic" not in server._tool_manager._tools


def test_create_server_lists_episodic_tools_when_settings_gate_is_on(tmp_path: Path) -> None:
    with build_checkpoint_runtime(LocalConfig.defaults(tmp_path / "runtime")) as runtime:
        settings = PersonalSettings(
            optional_model_enabled=True, model_provider="ollama", model_id="ministral-3:8b"
        )
        server = create_server(
            DurableMcpContextPort(runtime.checkpoint_service),
            episodic_extraction_enabled=settings.episodic_extraction_enabled,
        )
        names = [tool.name for tool in asyncio.run(server.list_tools())]

        assert "extract_episodic" in names
        assert "submit_episodic_candidates" in names
