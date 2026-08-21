"""Coverage for `extract_episodic` / `submit_episodic_candidates` on `DurableMcpContextPort`.

Uses fakes for the episodic provider and the approved-event service, a real (in-memory)
`ReferenceTaskActivityEventRepository`, and a real `LocalPendingTakeoverStore` backed by
`tmp_path`. No live model or MCP call is made.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from mnemo_memory.connectors.automatic_memory.pending_takeover import LocalPendingTakeoverStore
from mnemo_memory.packages.application.checkpoints import RecordApprovedEpisodicEvent
from mnemo_memory.packages.application.mcp_durable import DurableMcpContextPort
from mnemo_memory.packages.domain import (
    EpisodicExtractionRequest,
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

_VALID_OUTPUT: dict[str, object] = {
    "candidates": [
        {"kind": "decision", "claim": "Use the local provider first.", "confidence": 0.9,
         "sensitivity": "normal"}
    ]
}
_INVALID_OUTPUT: dict[str, object] = {"nope": []}


def _scope(seed: int = 1) -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string(f"00000000-0000-4000-8000-{seed:012d}"),
        ScopeLevel.TASK,
        Visibility.PROJECT,
        WorkspaceId.from_string(f"10000000-0000-4000-8000-{seed:012d}"),
        ProjectId.from_string(f"20000000-0000-4000-8000-{seed:012d}"),
        session_id=SessionId.from_string(f"30000000-0000-4000-8000-{seed:012d}"),
        task_id=TaskId.from_string(f"40000000-0000-4000-8000-{seed:012d}"),
    )


def _evidence(seed: int = 1) -> EvidenceReference:
    return EvidenceReference(
        EvidenceId.from_string(f"50000000-0000-4000-8000-{seed:012d}"),
        SourceId.from_string(f"60000000-0000-4000-8000-{seed:012d}"),
        EvidenceSourceType.AGENT_EVENT,
        SourceTrustClass.APPROVED_CHECKPOINT,
        "fixture://task-activity/verified",
        "sha256:" + "a" * 64,
        EvidenceLocation("fixture://task-activity/verified"),
        NOW,
        VerificationStatus.VERIFIED,
    )


def _event(
    scope: MemoryScope, *, source_event_key: str = "task-outcome:port-test"
) -> TaskActivityEvent:
    return TaskActivityEvent.create(
        scope=scope,
        kind=TaskActivityEventKind.TASK_OUTCOME,
        actor=TaskActivityActor.AGENT,
        summary="The bounded implementation passed its verification gate.",
        source_event_key=source_event_key,
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


class FakeEpisodicProvider:
    provider_id = "fake-episodic-provider"
    model_id = "fake-episodic-model"

    def __init__(self, output: object = None, error: Exception | None = None) -> None:
        self.output = output
        self.error = error
        self.calls: list[EpisodicExtractionRequest] = []

    def generate(self, request: EpisodicExtractionRequest) -> object:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return self.output


class FakeApprovedEventService:
    def __init__(self) -> None:
        self.calls: list[RecordApprovedEpisodicEvent] = []

    def record_approved_event(self, command: RecordApprovedEpisodicEvent) -> None:
        self.calls.append(command)
        return None


def _port(
    *,
    scope: MemoryScope,
    service: FakeApprovedEventService,
    events: ReferenceTaskActivityEventRepository,
    provider: FakeEpisodicProvider | None,
    pending_store: LocalPendingTakeoverStore | None = None,
    episodic_extraction_enabled: bool = True,
    local_first_takeover_enabled: bool = False,
    takeover_live_calls_authorized: bool = False,
    approved_event_capture_enabled: bool = True,
    episodic_route_recorder: Callable[[str], None] | None = None,
) -> DurableMcpContextPort:
    return DurableMcpContextPort(
        service,  # type: ignore[arg-type]
        default_scope=scope,
        approved_event_capture_enabled=approved_event_capture_enabled,
        episodic_provider=provider,
        episodic_output_parser=parse_episodic_output,
        pending_takeover_store=pending_store,
        task_activity_events=events,
        episodic_extraction_enabled=episodic_extraction_enabled,
        local_first_takeover_enabled=local_first_takeover_enabled,
        takeover_live_calls_authorized=takeover_live_calls_authorized,
        episodic_route_recorder=episodic_route_recorder,
    )


def test_extract_episodic_is_disabled_without_a_provider() -> None:
    scope = _scope()
    events = ReferenceTaskActivityEventRepository()
    events.append_task_activity_event(_event(scope))
    port = _port(
        scope=scope,
        service=FakeApprovedEventService(),
        events=events,
        provider=None,
    )

    assert port.extract_episodic({}) == {"status": "extraction_disabled"}


def test_extract_episodic_is_disabled_when_flag_is_off() -> None:
    scope = _scope()
    events = ReferenceTaskActivityEventRepository()
    events.append_task_activity_event(_event(scope))
    port = _port(
        scope=scope,
        service=FakeApprovedEventService(),
        events=events,
        provider=FakeEpisodicProvider(output=_VALID_OUTPUT),
        episodic_extraction_enabled=False,
    )

    assert port.extract_episodic({}) == {"status": "extraction_disabled"}


def test_extract_episodic_persists_a_valid_result_when_capture_is_on() -> None:
    scope = _scope()
    events = ReferenceTaskActivityEventRepository()
    source = _event(scope)
    events.append_task_activity_event(source)
    service = FakeApprovedEventService()
    port = _port(
        scope=scope,
        service=service,
        events=events,
        provider=FakeEpisodicProvider(output=_VALID_OUTPUT),
    )

    result = port.extract_episodic({})

    assert result == {"status": "extracted", "persisted": 1, "dropped": 0}
    assert len(service.calls) == 1
    assert service.calls[0].scope == scope
    assert service.calls[0].source_event_key == f"{source.source_event_key}:0"


def test_extract_episodic_extracted_without_persisting_when_capture_is_off() -> None:
    scope = _scope()
    events = ReferenceTaskActivityEventRepository()
    events.append_task_activity_event(_event(scope))
    service = FakeApprovedEventService()
    port = _port(
        scope=scope,
        service=service,
        events=events,
        provider=FakeEpisodicProvider(output=_VALID_OUTPUT),
        approved_event_capture_enabled=False,
    )

    result = port.extract_episodic({})

    assert result == {"status": "extracted", "persisted": 0, "dropped": 0}
    assert service.calls == []


def test_extract_episodic_hands_off_invalid_output_when_both_flags_are_on(
    tmp_path: Path,
) -> None:
    scope = _scope()
    events = ReferenceTaskActivityEventRepository()
    source = _event(scope)
    events.append_task_activity_event(source)
    store = LocalPendingTakeoverStore(tmp_path)
    port = _port(
        scope=scope,
        service=FakeApprovedEventService(),
        events=events,
        provider=FakeEpisodicProvider(output=_INVALID_OUTPUT),
        pending_store=store,
        local_first_takeover_enabled=True,
        takeover_live_calls_authorized=True,
    )

    result = port.extract_episodic({})

    assert result["status"] == "handoff"
    assert result["reason"] == "local_invalid"
    assert result["event"] == {
        "summary": source.summary,
        "source_event_key": source.source_event_key,
    }
    assert store.pending(scope) == source.source_event_key


def test_extract_episodic_fails_local_when_invalid_output_and_flags_are_off() -> None:
    scope = _scope()
    events = ReferenceTaskActivityEventRepository()
    events.append_task_activity_event(_event(scope))
    service = FakeApprovedEventService()
    port = _port(
        scope=scope,
        service=service,
        events=events,
        provider=FakeEpisodicProvider(output=_INVALID_OUTPUT),
    )

    result = port.extract_episodic({})

    assert result == {"status": "local_failed"}
    assert service.calls == []


def test_extract_episodic_reports_no_events_when_the_source_is_empty() -> None:
    scope = _scope()
    events = ReferenceTaskActivityEventRepository()
    service = FakeApprovedEventService()
    port = _port(
        scope=scope,
        service=service,
        events=events,
        provider=FakeEpisodicProvider(output=_VALID_OUTPUT),
    )

    result = port.extract_episodic({})

    assert result == {"status": "no_events"}
    assert service.calls == []


def test_extract_episodic_is_fail_open_when_the_provider_raises() -> None:
    scope = _scope()
    events = ReferenceTaskActivityEventRepository()
    events.append_task_activity_event(_event(scope))
    service = FakeApprovedEventService()
    port = _port(
        scope=scope,
        service=service,
        events=events,
        provider=FakeEpisodicProvider(error=RuntimeError("provider is unavailable")),
    )

    result = port.extract_episodic({})

    assert result == {"status": "error"}
    assert service.calls == []


def test_submit_episodic_candidates_rejects_without_a_pending_marker(tmp_path: Path) -> None:
    scope = _scope()
    events = ReferenceTaskActivityEventRepository()
    events.append_task_activity_event(_event(scope))
    store = LocalPendingTakeoverStore(tmp_path)
    port = _port(
        scope=scope,
        service=FakeApprovedEventService(),
        events=events,
        provider=None,
        pending_store=store,
    )

    result = port.submit_episodic_candidates({"candidates": _VALID_OUTPUT["candidates"]})

    assert result == {"status": "rejected", "reason": "no_pending_handoff"}


def test_submit_episodic_candidates_rejects_invalid_candidates_and_keeps_the_marker(
    tmp_path: Path,
) -> None:
    scope = _scope()
    events = ReferenceTaskActivityEventRepository()
    source = _event(scope)
    events.append_task_activity_event(source)
    store = LocalPendingTakeoverStore(tmp_path)
    service = FakeApprovedEventService()
    port = _port(
        scope=scope,
        service=service,
        events=events,
        provider=FakeEpisodicProvider(output=_INVALID_OUTPUT),
        pending_store=store,
        local_first_takeover_enabled=True,
        takeover_live_calls_authorized=True,
    )
    handoff = port.extract_episodic({})
    assert handoff["status"] == "handoff"

    result = port.submit_episodic_candidates({"candidates": "not-a-list"})

    assert result == {"status": "rejected", "reason": "invalid_candidates"}
    assert service.calls == []
    assert store.pending(scope) == source.source_event_key


def test_submit_episodic_candidates_persists_once_and_a_second_submit_is_rejected(
    tmp_path: Path,
) -> None:
    scope = _scope()
    events = ReferenceTaskActivityEventRepository()
    source = _event(scope)
    events.append_task_activity_event(source)
    store = LocalPendingTakeoverStore(tmp_path)
    service = FakeApprovedEventService()
    port = _port(
        scope=scope,
        service=service,
        events=events,
        provider=FakeEpisodicProvider(output=_INVALID_OUTPUT),
        pending_store=store,
        local_first_takeover_enabled=True,
        takeover_live_calls_authorized=True,
    )
    handoff = port.extract_episodic({})
    assert handoff["status"] == "handoff"

    persisted = port.submit_episodic_candidates({"candidates": _VALID_OUTPUT["candidates"]})

    assert persisted == {"status": "persisted", "persisted": 1, "dropped": 0}
    assert len(service.calls) == 1
    assert store.pending(scope) is None

    second = port.submit_episodic_candidates({"candidates": _VALID_OUTPUT["candidates"]})

    assert second == {"status": "rejected", "reason": "no_pending_handoff"}
    assert len(service.calls) == 1


def test_extract_episodic_records_the_extracted_route_outcome() -> None:
    scope = _scope()
    events = ReferenceTaskActivityEventRepository()
    events.append_task_activity_event(_event(scope))
    recorded: list[str] = []
    port = _port(
        scope=scope,
        service=FakeApprovedEventService(),
        events=events,
        provider=FakeEpisodicProvider(output=_VALID_OUTPUT),
        episodic_route_recorder=recorded.append,
    )

    result = port.extract_episodic({})

    assert result["status"] == "extracted"
    assert recorded == ["extracted"]


def test_extract_episodic_records_the_handoff_route_outcome(tmp_path: Path) -> None:
    scope = _scope()
    events = ReferenceTaskActivityEventRepository()
    events.append_task_activity_event(_event(scope))
    recorded: list[str] = []
    port = _port(
        scope=scope,
        service=FakeApprovedEventService(),
        events=events,
        provider=FakeEpisodicProvider(output=_INVALID_OUTPUT),
        pending_store=LocalPendingTakeoverStore(tmp_path),
        local_first_takeover_enabled=True,
        takeover_live_calls_authorized=True,
        episodic_route_recorder=recorded.append,
    )

    result = port.extract_episodic({})

    assert result["status"] == "handoff"
    assert recorded == ["handoff"]
