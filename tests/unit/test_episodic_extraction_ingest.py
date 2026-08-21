from datetime import UTC, datetime

from mnemo_memory.packages.application.episodic_extraction_ingest import (
    approved_kind_for_episodic,
    ingest_episodic_proposals,
)
from mnemo_memory.packages.domain import (
    EpisodicExtractionProposal,
    EpisodicMemoryKind,
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    Sensitivity,
    SessionId,
    SourceId,
    SourceTrustClass,
    TaskId,
    VerificationStatus,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.domain.approved_episodic_events import ApprovedEventKind

NOW = datetime(2026, 8, 22, 9, 30, tzinfo=UTC)


def _task_scope() -> MemoryScope:
    """Create a TASK-level MemoryScope for testing."""
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
    """Create a single EvidenceReference for testing."""
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


def test_kind_mapping_maps_known_and_drops_unmapped() -> None:
    assert approved_kind_for_episodic(EpisodicMemoryKind.DECISION) is ApprovedEventKind.DECISION
    assert approved_kind_for_episodic(EpisodicMemoryKind.FAILURE) is ApprovedEventKind.FAILURE
    assert approved_kind_for_episodic(EpisodicMemoryKind.OUTCOME) is ApprovedEventKind.TOOL_OUTCOME
    assert approved_kind_for_episodic(EpisodicMemoryKind.LESSON) is None
    assert approved_kind_for_episodic(EpisodicMemoryKind.PREFERENCE) is None


def test_ingest_persists_mapped_and_drops_unmapped() -> None:
    calls: list[object] = []

    class FakeService:
        def record_approved_event(self, command: object) -> object:
            calls.append(command)
            return object()

    proposals = (
        EpisodicExtractionProposal(
            EpisodicMemoryKind.DECISION, "chose X", 0.9, Sensitivity.NORMAL
        ),
        EpisodicExtractionProposal(
            EpisodicMemoryKind.LESSON, "note", 0.5, Sensitivity.NORMAL
        ),
    )
    result = ingest_episodic_proposals(
        service=FakeService(),
        scope=_task_scope(),
        source_event_key="evt-1",
        evidence_references=(_evidence(),),
        proposals=proposals,
    )
    assert result.persisted == 1 and result.dropped == 1 and len(calls) == 1


def test_ingest_drops_a_rejected_proposal_without_losing_earlier_persisted_ones() -> None:
    calls: list[object] = []

    class FakeService:
        def record_approved_event(self, command: object) -> object:
            calls.append(command)
            if len(calls) == 2:
                raise RuntimeError("secret policy rejected this candidate")
            return object()

    proposals = (
        EpisodicExtractionProposal(
            EpisodicMemoryKind.DECISION, "chose X", 0.9, Sensitivity.NORMAL
        ),
        EpisodicExtractionProposal(
            EpisodicMemoryKind.OUTCOME, "api_key=leaked", 0.8, Sensitivity.NORMAL
        ),
    )

    result = ingest_episodic_proposals(
        service=FakeService(),
        scope=_task_scope(),
        source_event_key="evt-2",
        evidence_references=(_evidence(),),
        proposals=proposals,
    )

    assert result.persisted == 1 and result.dropped == 1 and len(calls) == 2
