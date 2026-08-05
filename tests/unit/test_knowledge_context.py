from __future__ import annotations

from datetime import UTC, datetime

from mnemo_memory.packages.application.checkpoints import (
    CheckpointApplicationService,
    CreateCheckpoint,
)
from mnemo_memory.packages.application.mcp_durable import DurableMcpContextPort
from mnemo_memory.packages.application.unified_context import (
    GetUnifiedContext,
    UnifiedContextService,
)
from mnemo_memory.packages.context_engine import UnifiedContextEngine
from mnemo_memory.packages.domain import (
    CheckpointContent,
    ContextBudget,
    ContextPacket,
    CurrentKnowledgeDocumentSection,
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    KnowledgeDocumentRevision,
    KnowledgeDocumentRevisionId,
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    SessionId,
    SourceId,
    SourceTrustClass,
    TaskId,
    VerificationStatus,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.knowledge import (
    KnowledgeDocumentParser,
    KnowledgeDocumentParseRequest,
    LocalSemanticKnowledgeIndexer,
    LocalSemanticKnowledgeRetriever,
    SemanticKnowledgeIndexRequest,
)
from mnemo_memory.packages.storage import (
    ActiveEpisodicMemoryPage,
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


class _LocalProvider:
    model_id = "test-local:context-v1"

    def embed_passages(self, passages: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(self._vector(value) for value in passages)

    def embed_query(self, query: str) -> tuple[float, ...]:
        return self._vector(query)

    @staticmethod
    def _vector(value: str) -> tuple[float, ...]:
        if "invoice" in value.casefold() or "charge" in value.casefold():
            return (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        return (0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


class _FixedSemanticRetriever:
    def __init__(self, section: CurrentKnowledgeDocumentSection) -> None:
        self.section = section

    def search_sections(
        self, scope: MemoryScope, query: str
    ) -> tuple[tuple[CurrentKnowledgeDocumentSection, float], ...]:
        assert scope == project_scope()
        assert query == "authentication return route"
        return ((self.section, 0.99),)


class _NoActiveEpisodicMemory:
    def list_active_episodic_memories(
        self, scope: MemoryScope, *, offset: int = 0, limit: int = 50
    ) -> ActiveEpisodicMemoryPage:
        assert scope.level is ScopeLevel.TASK
        assert offset == 0 and limit == 50
        return ActiveEpisodicMemoryPage((), None)


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


def test_transient_general_query_generates_scoped_lexical_knowledge_candidates() -> None:
    repository = ReferenceKnowledgeDocumentRepository()
    document = KnowledgeDocumentParser().parse(
        KnowledgeDocumentParseRequest(project_scope(), "notes/oauth.md"),
        "# OAuth callback\nValidate the callback state before exchanging the code.",
    )
    revision = KnowledgeDocumentRevision(KnowledgeDocumentRevisionId.new(), document, 1, None, NOW)
    repository.apply_sync(project_scope(), (revision,), ())
    engine = UnifiedContextEngine(service(repository), _NoActiveEpisodicMemory())

    packet = engine.get_context(GetUnifiedContext(task_scope(), query="oauth callback"))

    assert len(packet.knowledge_items) == 1
    assert "callback state" in packet.knowledge_items[0].content
    assert packet.knowledge_items[0].source_scope.level is ScopeLevel.TASK
    assert "query" not in packet.to_dict()


def test_unified_context_can_attach_explicit_local_semantic_knowledge() -> None:
    repository = ReferenceKnowledgeDocumentRepository()
    document = KnowledgeDocumentParser().parse(
        KnowledgeDocumentParseRequest(project_scope(), "notes/billing.md"),
        "# Invoices\nReconcile invoice totals before the close.",
    )
    revision = KnowledgeDocumentRevision(KnowledgeDocumentRevisionId.new(), document, 1, None, NOW)
    repository.apply_sync(project_scope(), (revision,), ())
    provider = _LocalProvider()
    LocalSemanticKnowledgeIndexer(repository, provider).index(
        SemanticKnowledgeIndexRequest(project_scope())
    )
    context = UnifiedContextService(
        CheckpointApplicationService(ReferenceCheckpointRepository(), clock=lambda: NOW),
        None,
        knowledge=repository,
        semantic_knowledge=LocalSemanticKnowledgeRetriever(repository, provider),
    )

    packet = context.get_context(
        GetUnifiedContext(task_scope(), semantic_knowledge_query="charge variance")
    )

    assert len(packet.knowledge_items) == 1
    assert "invoice totals" in packet.knowledge_items[0].content
    assert packet.knowledge_items[0].evidence_references


def test_lexical_and_local_vector_results_use_reciprocal_rank_fusion() -> None:
    repository = ReferenceKnowledgeDocumentRepository()
    first = KnowledgeDocumentParser().parse(
        KnowledgeDocumentParseRequest(project_scope(), "notes/a-oauth.md"),
        "# OAuth\nOAuth OAuth callback handling.",
    )
    second = KnowledgeDocumentParser().parse(
        KnowledgeDocumentParseRequest(project_scope(), "notes/b-callback.md"),
        "# Callback\nOAuth callback.",
    )
    revisions = (
        KnowledgeDocumentRevision(KnowledgeDocumentRevisionId.new(), first, 1, None, NOW),
        KnowledgeDocumentRevision(KnowledgeDocumentRevisionId.new(), second, 1, None, NOW),
    )
    repository.apply_sync(project_scope(), revisions, ())
    second_section = next(
        item
        for item in repository.iter_current_sections(project_scope(), 128)
        if item.revision.document.document_id == second.document_id
    )
    context = UnifiedContextService(
        CheckpointApplicationService(ReferenceCheckpointRepository(), clock=lambda: NOW),
        None,
        knowledge=repository,
        semantic_knowledge=_FixedSemanticRetriever(second_section),
    )

    packet = context.get_context(
        GetUnifiedContext(
            task_scope(),
            knowledge_query="oauth callback",
            semantic_knowledge_query="authentication return route",
        )
    )

    assert [item.item_id.split(":")[1] for item in packet.knowledge_items] == [
        str(second.document_id),
        str(first.document_id),
    ]
    first_rank = packet.knowledge_items[0].ranking
    second_rank = packet.knowledge_items[1].ranking
    assert first_rank is not None and second_rank is not None
    assert first_rank.retrieval_method == ("reciprocal-rank-fusion/k60:scoped-literal+local-vector")
    assert second_rank.retrieval_method == "reciprocal-rank-fusion/k60:scoped-literal"
    assert first_rank.score is not None and second_rank.score is not None
    assert first_rank.score > second_rank.score


def test_explicit_knowledge_conflict_is_preserved_without_inference() -> None:
    repository = ReferenceKnowledgeDocumentRepository()
    declared = KnowledgeDocumentParser().parse(
        KnowledgeDocumentParseRequest(project_scope(), "docs/current-policy.md"),
        "---\nmnemo_conflicts_with: docs/legacy-policy.md\n---\n"
        "# Current policy\nUse the current policy during reconciliation.",
    )
    declared_revision = KnowledgeDocumentRevision(
        KnowledgeDocumentRevisionId.new(), declared, 1, None, NOW
    )
    other = KnowledgeDocumentParser().parse(
        KnowledgeDocumentParseRequest(project_scope(), "docs/legacy-policy.md"),
        "# Legacy policy\nThis retained note has a different documented procedure.",
    )
    other_revision = KnowledgeDocumentRevision(
        KnowledgeDocumentRevisionId.new(), other, 1, None, NOW
    )
    repository.apply_sync(project_scope(), (declared_revision, other_revision), ())

    packet = service(repository).get_context(
        GetUnifiedContext(task_scope(), knowledge_query="current policy")
    )

    assert {item.item_id for item in packet.knowledge_items} == {
        f"knowledge:{declared.document_id}:revision:{declared_revision.revision_id}:section:0",
        f"knowledge:{other.document_id}:revision:{other_revision.revision_id}:section:0",
    }
    assert len(packet.conflicts) == 1
    assert packet.conflicts[0].state.value == "unresolved"
    assert set(packet.conflicts[0].item_ids) == {item.item_id for item in packet.knowledge_items}


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


def test_unified_context_can_select_cited_knowledge_from_checkpoint_file_identity() -> None:
    repository = ReferenceKnowledgeDocumentRepository()
    document = KnowledgeDocumentParser().parse(
        KnowledgeDocumentParseRequest(project_scope(), "docs/reconciliation.md"),
        "# Reconciliation\nThe reconciliation model uses the documented business-date grain.",
    )
    repository.apply_sync(
        project_scope(),
        (KnowledgeDocumentRevision(KnowledgeDocumentRevisionId.new(), document, 1, None, NOW),),
        (),
    )
    checkpoints = CheckpointApplicationService(ReferenceCheckpointRepository(), clock=lambda: NOW)
    scope = task_scope()
    checkpoints.create(
        CreateCheckpoint(
            scope,
            CheckpointContent(
                task_objective="Resume reconciliation",
                completed_work=(),
                current_state="A file-specific note may help.",
                remaining_work=("Review the model.",),
                decisions=(),
                failures=(),
                blockers=(),
                relevant_files=("models/reconciliation.sql",),
                relevant_artifacts=(),
                verification_performed=(),
                token_estimate=20,
            ),
            (
                EvidenceReference(
                    EvidenceId.new(),
                    SourceId.new(),
                    EvidenceSourceType.USER_DOCUMENT,
                    SourceTrustClass.USER_AUTHORED,
                    "fixture://checkpoint/reconciliation",
                    "sha256:" + "a" * 64,
                    EvidenceLocation("fixture://checkpoint/reconciliation"),
                    NOW,
                    VerificationStatus.VERIFIED,
                ),
            ),
        )
    )
    context = UnifiedContextService(checkpoints, None, knowledge=repository).get_context(
        GetUnifiedContext(scope, include_checkpoint_file_knowledge=True)
    )

    assert len(context.knowledge_items) == 1
    assert "reconciliation model" in context.knowledge_items[0].content
    assert context.knowledge_items[0].ranking is not None
    assert context.knowledge_items[0].ranking.retrieval_method == "scoped-literal-knowledge"


def test_checkpoint_file_knowledge_selection_ignores_unsafe_or_unsearchable_identities() -> None:
    repository = ReferenceKnowledgeDocumentRepository()
    checkpoints = CheckpointApplicationService(ReferenceCheckpointRepository(), clock=lambda: NOW)
    scope = task_scope()
    checkpoints.create(
        CreateCheckpoint(
            scope,
            CheckpointContent(
                task_objective="Resume safely",
                completed_work=(),
                current_state="No automatic note query should be derived.",
                remaining_work=(),
                decisions=(),
                failures=(),
                blockers=(),
                relevant_files=("/private/secret.md", "../../--.py"),
                relevant_artifacts=(),
                verification_performed=(),
                token_estimate=10,
            ),
            (
                EvidenceReference(
                    EvidenceId.new(),
                    SourceId.new(),
                    EvidenceSourceType.USER_DOCUMENT,
                    SourceTrustClass.USER_AUTHORED,
                    "fixture://checkpoint/safe",
                    "sha256:" + "b" * 64,
                    EvidenceLocation("fixture://checkpoint/safe"),
                    NOW,
                    VerificationStatus.VERIFIED,
                ),
            ),
        )
    )

    context = UnifiedContextService(checkpoints, None, knowledge=repository).get_context(
        GetUnifiedContext(scope, include_checkpoint_file_knowledge=True)
    )

    assert context.knowledge_items == ()
