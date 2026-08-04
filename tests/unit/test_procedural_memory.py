from __future__ import annotations

from datetime import UTC, datetime

import pytest

from mnemo_memory.packages.application.checkpoints import CheckpointApplicationService
from mnemo_memory.packages.application.mcp_durable import DurableMcpContextPort
from mnemo_memory.packages.application.unified_context import (
    GetUnifiedContext,
    UnifiedContextService,
)
from mnemo_memory.packages.domain import (
    ContextBudget,
    ContextItemType,
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
from mnemo_memory.packages.skills_registry import KnowledgeDocumentProcedureRegistry
from mnemo_memory.packages.storage import (
    ReferenceCheckpointRepository,
    ReferenceKnowledgeDocumentRepository,
)

NOW = datetime(2026, 8, 5, tzinfo=UTC)


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
        SessionId.new(),
        TaskId.new(),
    )


def _revision(scope: MemoryScope, path: str, body: str) -> KnowledgeDocumentRevision:
    document = KnowledgeDocumentParser().parse(KnowledgeDocumentParseRequest(scope, path), body)
    return KnowledgeDocumentRevision(KnowledgeDocumentRevisionId.new(), document, 1, None, NOW)


def _context(repository: ReferenceKnowledgeDocumentRepository) -> UnifiedContextService:
    checkpoints = CheckpointApplicationService(ReferenceCheckpointRepository(), clock=lambda: NOW)
    return UnifiedContextService(
        checkpoints,
        None,
        procedures=KnowledgeDocumentProcedureRegistry(repository),
    )


def test_registry_selects_only_explicit_matching_checked_in_procedures() -> None:
    repository = ReferenceKnowledgeDocumentRepository()
    reconciliation = _revision(
        _project_scope(),
        "docs/reconciliation-procedure.md",
        "---\nmnemo_kind: procedure\nmnemo_tags: reconciliation, dbt\n"
        "mnemo_mandatory: true\n---\n# Reconcile\nUse the business-date grain.",
    )
    ordinary_note = _revision(
        _project_scope(),
        "docs/ordinary-note.md",
        "---\nmnemo_tags: reconciliation\n---\n# Note\nNot a procedure.",
    )
    invalid = _revision(
        _project_scope(),
        "docs/invalid-procedure.md",
        "---\nmnemo_kind: procedure\nmnemo_tags: reconciliation, reconciliation\n---\n"
        "# Invalid\nThis must not be selected.",
    )
    repository.apply_sync(_project_scope(), (ordinary_note, invalid, reconciliation), ())

    selected = KnowledgeDocumentProcedureRegistry(repository).find_current_procedures(
        _project_scope(), ("DBT", "reconciliation"), 8
    )

    assert selected == (selected[0],)
    assert selected[0].revision == reconciliation
    assert selected[0].tags == ("dbt", "reconciliation")
    assert selected[0].mandatory is True
    assert (
        KnowledgeDocumentProcedureRegistry(repository).find_current_procedures(
            _project_scope(), ("python",), 8
        )
        == ()
    )


def test_registry_rejects_malformed_query_tags_and_cross_scope_selection() -> None:
    registry = KnowledgeDocumentProcedureRegistry(ReferenceKnowledgeDocumentRepository())
    with pytest.raises(ValueError, match="procedure tag"):
        registry.find_current_procedures(_project_scope(), ("has spaces",), 8)
    with pytest.raises(ValueError, match="project scope"):
        registry.find_current_procedures(_task_scope(), ("dbt",), 8)


def test_context_attaches_cited_procedure_only_when_tags_are_requested() -> None:
    repository = ReferenceKnowledgeDocumentRepository()
    revision = _revision(
        _project_scope(),
        "docs/reconciliation-procedure.md",
        "---\nmnemo_kind: procedure\nmnemo_tags: reconciliation, dbt\n"
        "mnemo_mandatory: true\n---\n# Reconcile\nUse the documented business-date grain.",
    )
    repository.apply_sync(_project_scope(), (revision,), ())
    context = _context(repository)

    assert context.get_context(GetUnifiedContext(_task_scope())).skills_and_procedures == ()
    packet = context.get_context(
        GetUnifiedContext(_task_scope(), procedure_tags=("reconciliation",))
    )

    assert len(packet.skills_and_procedures) == 1
    item = packet.skills_and_procedures[0]
    assert item.item_type is ContextItemType.MANDATORY_PROCEDURE
    assert item.content_representation.value == "untrusted_evidence"
    assert "business-date grain" in item.content
    assert str(revision.revision_id) in item.item_id
    assert item.evidence_references[0].immutable_source_ref.startswith("procedure:")
    assert packet.provenance[-1].item_id == item.item_id


def test_registry_selects_the_current_immutable_procedure_revision() -> None:
    repository = ReferenceKnowledgeDocumentRepository()
    first = _revision(
        _project_scope(),
        "docs/reconciliation-procedure.md",
        "---\nmnemo_kind: procedure\nmnemo_tags: reconciliation\n---\n"
        "# Reconcile\nOld documented step.",
    )
    repository.apply_sync(_project_scope(), (first,), ())
    changed_document = KnowledgeDocumentParser().parse(
        KnowledgeDocumentParseRequest(_project_scope(), "docs/reconciliation-procedure.md"),
        "---\nmnemo_kind: procedure\nmnemo_tags: reconciliation\n---\n"
        "# Reconcile\nCurrent documented step.",
    )
    second = KnowledgeDocumentRevision(
        KnowledgeDocumentRevisionId.new(), changed_document, 2, first.revision_id, NOW
    )
    repository.apply_sync(_project_scope(), (second,), ())

    selected = KnowledgeDocumentProcedureRegistry(repository).find_current_procedures(
        _project_scope(), ("reconciliation",), 8
    )

    assert selected[0].revision.revision_id == second.revision_id
    assert selected[0].revision.predecessor_revision_id == first.revision_id
    assert "Current documented step" in selected[0].revision.document.sections[0].content


def test_context_omits_whole_procedure_when_skills_budget_cannot_hold_it() -> None:
    repository = ReferenceKnowledgeDocumentRepository()
    revision = _revision(
        _project_scope(),
        "docs/large-procedure.md",
        "---\nmnemo_kind: procedure\nmnemo_tags: reconciliation\n---\n# Reconcile\n"
        + "documented step " * 80,
    )
    repository.apply_sync(_project_scope(), (revision,), ())

    packet = _context(repository).get_context(
        GetUnifiedContext(
            _task_scope(),
            budget=ContextBudget(skills_and_procedures=1, total_limit=5700),
            procedure_tags=("reconciliation",),
        )
    )

    assert packet.skills_and_procedures == ()
    assert packet.omissions[-1].reason.value == "token_budget"


def test_context_procedure_query_is_scope_first_and_mcp_validates_tags() -> None:
    repository = ReferenceKnowledgeDocumentRepository()
    revision = _revision(
        _project_scope(2),
        "docs/private-procedure.md",
        "---\nmnemo_kind: procedure\nmnemo_tags: reconciliation\n---\n# Private\nOther scope.",
    )
    repository.apply_sync(_project_scope(2), (revision,), ())
    checkpoints = CheckpointApplicationService(ReferenceCheckpointRepository(), clock=lambda: NOW)
    port = DurableMcpContextPort(checkpoints, _context(repository))
    scope = _task_scope()
    request: dict[str, object] = {
        "owner_id": str(scope.owner_id),
        "workspace_id": str(scope.workspace_id),
        "project_id": str(scope.project_id),
        "session_id": str(scope.session_id),
        "task_id": str(scope.task_id),
        "procedure_tags": ["reconciliation"],
    }

    assert port.get_context(request)["skills_and_procedures"] == []
    with pytest.raises(ValueError, match="MNEMO_INVALID_INPUT"):
        port.get_context({**request, "procedure_tags": "reconciliation"})
