from __future__ import annotations

from datetime import UTC, datetime

from mnemo_memory.packages.application.checkpoints import CheckpointApplicationService
from mnemo_memory.packages.application.mcp_durable import DurableMcpContextPort
from mnemo_memory.packages.application.unified_context import (
    GetUnifiedContext,
    UnifiedContextService,
)
from mnemo_memory.packages.domain import (
    ContextBudget,
    ContextPacket,
    KnowledgeDocumentRevision,
    KnowledgeDocumentRevisionId,
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    SessionId,
    TaskId,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.knowledge import KnowledgeDocumentParser, KnowledgeDocumentParseRequest
from mnemo_memory.packages.storage import (
    ReferenceCheckpointRepository,
    ReferenceKnowledgeDocumentRepository,
)

NOW = datetime(2026, 8, 4, tzinfo=UTC)


def project_scope(seed: int = 1) -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string(f"00000000-0000-4000-8000-{seed:012d}"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string(f"00000000-0000-4000-8001-{seed:012d}"),
        ProjectId.from_string(f"00000000-0000-4000-8002-{seed:012d}"),
    )


def task_scope(seed: int = 1) -> MemoryScope:
    project = project_scope(seed)
    return MemoryScope(
        project.owner_id,
        ScopeLevel.TASK,
        project.visibility,
        project.workspace_id,
        project.project_id,
        SessionId.new(),
        TaskId.new(),
    )


def service(repository: ReferenceKnowledgeDocumentRepository) -> UnifiedContextService:
    return UnifiedContextService(
        CheckpointApplicationService(ReferenceCheckpointRepository(), clock=lambda: NOW),
        None,
        knowledge=repository,
    )


def test_unified_context_adds_cited_bounded_untrusted_knowledge() -> None:
    repository = ReferenceKnowledgeDocumentRepository()
    document = KnowledgeDocumentParser().parse(
        KnowledgeDocumentParseRequest(project_scope(), "notes/reconciliation.md"),
        "# Reconciliation\nCompare Finance inputs at the documented business-date grain.",
    )
    revision = KnowledgeDocumentRevision(KnowledgeDocumentRevisionId.new(), document, 1, None, NOW)
    repository.apply_sync(project_scope(), (revision,), ())

    request_scope = task_scope()
    packet = service(repository).get_context(
        GetUnifiedContext(request_scope, knowledge_query="Finance business-date")
    )

    assert len(packet.knowledge_items) == 1
    item = packet.knowledge_items[0]
    assert "documented business-date grain" in item.content
    assert item.source_scope == request_scope
    assert item.validity.value == "unknown"
    assert item.ranking is not None
    assert item.ranking.retrieval_method == "scoped-literal-knowledge"
    assert item.ranking.rank == 1
    assert '"source_kind":"markdown"' in item.content
    assert item.evidence_references[0].immutable_source_ref.startswith("knowledge:")
    assert str(revision.revision_id) in item.item_id
    assert packet.provenance[-1].item_id == item.item_id


def test_unified_context_omits_whole_knowledge_sections_that_do_not_fit_budget() -> None:
    repository = ReferenceKnowledgeDocumentRepository()
    document = KnowledgeDocumentParser().parse(
        KnowledgeDocumentParseRequest(project_scope(), "notes/large.md"),
        "# Finance\n" + "business date " * 80,
    )
    repository.apply_sync(
        project_scope(),
        (KnowledgeDocumentRevision(KnowledgeDocumentRevisionId.new(), document, 1, None, NOW),),
        (),
    )

    packet = service(repository).get_context(
        GetUnifiedContext(
            task_scope(),
            budget=ContextBudget(knowledge=1, total_limit=5700),
            knowledge_query="business",
        )
    )

    assert packet.knowledge_items == ()
    assert packet.omissions[-1].reason.value == "token_budget"


def test_unified_context_does_not_disclose_other_project_knowledge() -> None:
    repository = ReferenceKnowledgeDocumentRepository()
    document = KnowledgeDocumentParser().parse(
        KnowledgeDocumentParseRequest(project_scope(2), "notes/private.md"),
        "# Finance\nOther project only.",
    )
    repository.apply_sync(
        project_scope(2),
        (KnowledgeDocumentRevision(KnowledgeDocumentRevisionId.new(), document, 1, None, NOW),),
        (),
    )

    packet = service(repository).get_context(
        GetUnifiedContext(task_scope(), knowledge_query="finance")
    )

    assert packet.knowledge_items == ()


def test_durable_mcp_port_routes_explicit_knowledge_query_to_unified_context() -> None:
    repository = ReferenceKnowledgeDocumentRepository()
    document = KnowledgeDocumentParser().parse(
        KnowledgeDocumentParseRequest(project_scope(), "notes/reconciliation.md"),
        "# Reconciliation\nCompare Finance inputs at the documented business-date grain.",
    )
    repository.apply_sync(
        project_scope(),
        (KnowledgeDocumentRevision(KnowledgeDocumentRevisionId.new(), document, 1, None, NOW),),
        (),
    )
    checkpoint_service = CheckpointApplicationService(
        ReferenceCheckpointRepository(), clock=lambda: NOW
    )
    port = DurableMcpContextPort(checkpoint_service, service(repository))
    scope = task_scope()

    raw_packet = port.get_context(
        {
            "owner_id": str(scope.owner_id),
            "workspace_id": str(scope.workspace_id),
            "project_id": str(scope.project_id),
            "session_id": str(scope.session_id),
            "task_id": str(scope.task_id),
            "knowledge_query": "Finance business-date",
        }
    )

    packet = ContextPacket.from_dict(raw_packet)
    assert len(packet.knowledge_items) == 1
    assert packet.knowledge_items[0].validity.value == "unknown"
