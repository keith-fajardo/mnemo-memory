"""Exact item-ID fetches recheck scope, currentness and sensitivity (spec 2026-10-02 §5)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from mnemo_memory.packages.application import (
    CorrectApprovedEpisodicEvent,
    RecordApprovedEpisodicEvent,
    RetractApprovedEpisodicEvent,
    unified_context,
)
from mnemo_memory.packages.application.checkpoints import CheckpointApplicationService
from mnemo_memory.packages.application.mcp_durable import DurableMcpContextPort
from mnemo_memory.packages.application.unified_context import (
    GetUnifiedContext,
    UnifiedContextService,
)
from mnemo_memory.packages.context_engine import UnifiedContextEngine
from mnemo_memory.packages.domain import (
    ApprovedEventKind,
    ContextBudget,
    ContextItem,
    EventId,
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    KnowledgeDocumentRevision,
    KnowledgeDocumentRevisionId,
    MemoryScope,
    OmissionNotice,
    OmissionReason,
    OwnerId,
    ProjectId,
    ProvenanceNotice,
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
from mnemo_memory.packages.knowledge import KnowledgeDocumentParser, KnowledgeDocumentParseRequest
from mnemo_memory.packages.storage import (
    ActiveEpisodicMemoryPage,
    ReferenceApprovedEpisodicEventRepository,
    ReferenceCheckpointRepository,
    ReferenceKnowledgeDocumentRepository,
)

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def _project_scope(seed: int = 1) -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string(f"00000000-0000-4000-8000-{seed:012d}"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string(f"00000000-0000-4000-8001-{seed:012d}"),
        ProjectId.from_string(f"00000000-0000-4000-8002-{seed:012d}"),
    )


def _task_scope(seed: int = 1) -> MemoryScope:
    project = _project_scope(seed)
    return MemoryScope(
        project.owner_id,
        ScopeLevel.TASK,
        project.visibility,
        project.workspace_id,
        project.project_id,
        SessionId.from_string(f"00000000-0000-4000-8003-{seed:012d}"),
        TaskId.from_string(f"00000000-0000-4000-8004-{seed:012d}"),
    )


def _evidence(seed: str) -> EvidenceReference:
    return EvidenceReference(
        EvidenceId.new(),
        SourceId.new(),
        EvidenceSourceType.TOOL_RESULT,
        SourceTrustClass.VERIFIED_TOOL_RESULT,
        f"fixture://item-lookup/{seed}",
        "sha256:" + "a" * 64,
        EvidenceLocation(f"fixture://item-lookup/{seed}"),
        NOW,
        VerificationStatus.VERIFIED,
    )


def _revision(
    path: str,
    body: str,
    predecessor: KnowledgeDocumentRevision | None = None,
    *,
    scope: MemoryScope | None = None,
) -> KnowledgeDocumentRevision:
    document = KnowledgeDocumentParser().parse(
        KnowledgeDocumentParseRequest(_project_scope() if scope is None else scope, path), body
    )
    return KnowledgeDocumentRevision(
        KnowledgeDocumentRevisionId.new(),
        document,
        1 if predecessor is None else predecessor.revision_number + 1,
        None if predecessor is None else predecessor.revision_id,
        NOW,
    )


def _knowledge_id(revision: KnowledgeDocumentRevision, section: int = 0) -> str:
    return (
        f"knowledge:{revision.document.document_id}:revision:{revision.revision_id}"
        f":section:{section}"
    )


def _services() -> tuple[
    CheckpointApplicationService, UnifiedContextService, ReferenceKnowledgeDocumentRepository
]:
    knowledge = ReferenceKnowledgeDocumentRepository()
    checkpoints = CheckpointApplicationService(
        ReferenceCheckpointRepository(),
        clock=lambda: NOW,
        approved_event_repository=ReferenceApprovedEpisodicEventRepository(),
    )
    return checkpoints, UnifiedContextService(checkpoints, None, knowledge=knowledge), knowledge


def _record(
    checkpoints: CheckpointApplicationService, summary: str, key: str, seed: int = 1
) -> str:
    event = checkpoints.record_approved_event(
        RecordApprovedEpisodicEvent(
            _task_scope(seed), ApprovedEventKind.DECISION, summary, key, (_evidence(key),)
        )
    ).event
    return f"approved-episodic:{event.event_id}"


def _reasons(omissions: tuple[OmissionNotice, ...]) -> dict[str, OmissionReason]:
    return {notice.item_id: notice.reason for notice in omissions}


def test_item_ids_return_exactly_the_requested_current_items() -> None:
    checkpoints, service, knowledge = _services()
    note = _revision("notes/export.md", "# Invoice export\nKeep ledger sequence numbers exactly.")
    knowledge.apply_sync(_project_scope(), (note,), ())
    event_id = _record(checkpoints, "Keep invoice export retries idempotent.", "lookup:a")
    _record(checkpoints, "An unrelated fact that was not requested.", "lookup:b")

    packet = service.get_context(
        GetUnifiedContext(_task_scope(), item_ids=(_knowledge_id(note), event_id))
    )

    assert {item.item_id for item in packet.items} == {_knowledge_id(note), event_id}
    assert packet.active_task_checkpoint is None and packet.omissions == ()
    assert "Keep ledger sequence numbers exactly." in packet.knowledge_items[0].content
    assert "retries idempotent" in packet.episodic_memories[0].content
    assert packet.declared_total_tokens == packet.computed_total_tokens


def test_changed_or_missing_knowledge_comes_back_as_an_omission() -> None:
    _, service, knowledge = _services()
    first = _revision("notes/export.md", "# Invoice export\nFirst wording.")
    knowledge.apply_sync(_project_scope(), (first,), ())
    second = _revision("notes/export.md", "# Invoice export\nSecond wording.", first)
    knowledge.apply_sync(_project_scope(), (second,), ())
    missing = f"knowledge:{uuid4()}:revision:{uuid4()}:section:0"

    packet = service.get_context(
        GetUnifiedContext(
            _task_scope(),
            item_ids=(_knowledge_id(first), missing, _knowledge_id(second, section=7)),
        )
    )

    assert packet.items == ()
    assert _reasons(packet.omissions) == {
        _knowledge_id(first): OmissionReason.SUPERSEDED,
        missing: OmissionReason.EXPIRED,
        _knowledge_id(second, section=7): OmissionReason.UNAUTHORIZED_SCOPE,
    }


def test_knowledge_from_another_project_comes_back_as_expired_never_as_the_item() -> None:
    _, service, knowledge = _services()
    other = _project_scope(2)
    foreign = _revision("notes/export.md", "# Invoice export\nAnother project's note.", scope=other)
    knowledge.apply_sync(other, (foreign,), ())

    packet = service.get_context(
        GetUnifiedContext(_task_scope(), item_ids=(_knowledge_id(foreign),))
    )

    assert packet.items == ()
    assert _reasons(packet.omissions) == {_knowledge_id(foreign): OmissionReason.EXPIRED}


def test_corrected_retracted_and_foreign_events_come_back_as_omissions() -> None:
    checkpoints, service, _ = _services()
    corrected = _record(checkpoints, "Retain the first grain.", "lookup:corrected")
    checkpoints.correct_approved_event(
        CorrectApprovedEpisodicEvent(
            _task_scope(),
            _event_id(corrected),
            "Retain the corrected grain.",
            "lookup:replacement",
            "The user corrected the grain.",
            "lookup:correct",
            (_evidence("correct"),),
        )
    )
    retracted = _record(checkpoints, "A withdrawn fact.", "lookup:retracted")
    checkpoints.retract_approved_event(
        RetractApprovedEpisodicEvent(
            _task_scope(),
            _event_id(retracted),
            "The user withdrew the fact.",
            "lookup:retract",
            (_evidence("retract"),),
        )
    )
    foreign = _record(checkpoints, "Another project's fact.", "lookup:foreign", seed=2)

    packet = service.get_context(
        GetUnifiedContext(_task_scope(), item_ids=(corrected, retracted, foreign))
    )

    assert packet.items == ()
    assert _reasons(packet.omissions) == {
        corrected: OmissionReason.SUPERSEDED,
        retracted: OmissionReason.EXPIRED,
        foreign: OmissionReason.UNAUTHORIZED_SCOPE,
    }


def _event_id(item_id: str) -> EventId:
    return EventId.from_string(item_id.removeprefix("approved-episodic:"))


def test_non_normal_sensitivity_is_withheld(monkeypatch: pytest.MonkeyPatch) -> None:
    _, service, knowledge = _services()
    note = _revision("notes/export.md", "# Invoice export\nKeep ledger sequence numbers exactly.")
    knowledge.apply_sync(_project_scope(), (note,), ())
    original = unified_context._knowledge_context_item

    def sensitive(*args: Any, **kwargs: Any) -> tuple[ContextItem, ProvenanceNotice]:
        item, notice = original(*args, **kwargs)
        return replace(item, sensitivity=Sensitivity.CONFIDENTIAL), notice

    monkeypatch.setattr(unified_context, "_knowledge_context_item", sensitive)
    packet = service.get_context(GetUnifiedContext(_task_scope(), item_ids=(_knowledge_id(note),)))
    assert packet.items == ()
    assert _reasons(packet.omissions) == {
        _knowledge_id(note): OmissionReason.PROHIBITED_SENSITIVITY
    }


def test_requested_items_respect_the_packet_budget() -> None:
    _, service, knowledge = _services()
    note = _revision("notes/export.md", "# Invoice export\nKeep ledger sequence numbers exactly.")
    knowledge.apply_sync(_project_scope(), (note,), ())
    packet = service.get_context(
        GetUnifiedContext(
            _task_scope(),
            budget=replace(ContextBudget(), knowledge=1),
            item_ids=(_knowledge_id(note),),
        )
    )
    assert packet.items == ()
    assert _reasons(packet.omissions) == {_knowledge_id(note): OmissionReason.TOKEN_BUDGET}


VALID = f"approved-episodic:{uuid4()}"


@pytest.mark.parametrize(
    "changes",
    [
        {"item_ids": (VALID, VALID)},
        {"item_ids": tuple(f"approved-episodic:{uuid4()}" for _ in range(17))},
        {"item_ids": (VALID.upper(),)},
        {"item_ids": (" " + VALID,)},
        {"item_ids": (f"checkpoint:{uuid4()}",)},
        {"item_ids": (VALID,), "query": "invoice export"},
        {"item_ids": (VALID,), "knowledge_query": "invoice export"},
    ],
)
def test_item_id_requests_are_validated(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="item_ids"):
        GetUnifiedContext(_task_scope(), **changes)


class _ForbiddenEpisodicMemories:
    def list_active_episodic_memories(
        self, scope: MemoryScope, *, offset: int = 0, limit: int = 50
    ) -> ActiveEpisodicMemoryPage:
        raise AssertionError("an item-ID fetch must return exactly the requested items")


def test_engine_returns_item_id_requests_without_additions() -> None:
    checkpoints, service, _ = _services()
    event_id = _record(checkpoints, "Keep invoice export retries idempotent.", "lookup:engine")
    engine = UnifiedContextEngine(service, _ForbiddenEpisodicMemories())
    packet = engine.get_context(GetUnifiedContext(_task_scope(), item_ids=(event_id,)))
    assert [item.item_id for item in packet.items] == [event_id]


def test_mcp_port_fetches_item_ids_and_refuses_mixed_requests() -> None:
    checkpoints, service, _ = _services()
    event_id = _record(checkpoints, "Keep invoice export retries idempotent.", "lookup:port")
    port = DurableMcpContextPort(checkpoints, context_service=service, default_scope=_task_scope())

    fetched = port.get_context({"item_ids": [event_id], "include_approved_events": True})
    episodic = fetched["episodic_memories"]
    assert isinstance(episodic, list) and [item["item_id"] for item in episodic] == [event_id]
    with pytest.raises(ValueError, match="MNEMO_INVALID_INPUT"):
        port.get_context({"item_ids": [event_id], "query": "invoice"})
    with pytest.raises(ValueError, match="MNEMO_INVALID_INPUT"):
        port.get_context({"item_ids": [event_id], "dbt_lineage": {"unique_id": "model.x.y"}})
    with pytest.raises(ValueError, match="MNEMO_INVALID_INPUT"):
        port.get_context({"item_ids": [event_id, "source-structure"]})


@pytest.mark.parametrize(
    "request_values",
    [
        {"item_ids": []},
        {"item_ids": [], "include_approved_events": True},
        {"item_ids": [], "query": "invoice export"},
    ],
)
def test_mcp_port_refuses_an_empty_item_id_list(request_values: dict[str, Any]) -> None:
    """An empty list is a request error, never the default path (which drops the include
    flags and allows mixing with other retrieval fields)."""

    checkpoints, service, _ = _services()
    _record(checkpoints, "Keep invoice export retries idempotent.", "lookup:empty")
    port = DurableMcpContextPort(checkpoints, context_service=service, default_scope=_task_scope())
    with pytest.raises(ValueError, match="MNEMO_INVALID_INPUT"):
        port.get_context(request_values)
