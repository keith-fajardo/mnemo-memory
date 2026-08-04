from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mnemo_memory.packages.application.checkpoints import (
    CheckpointApplicationService,
    CreateCheckpoint,
)
from mnemo_memory.packages.application.unified_context import (
    ContextCheckpointSourceImpact,
    ContextSourceChangeQuery,
    ContextSourceImpactQuery,
    ContextSourceOverviewQuery,
    GetUnifiedContext,
    UnifiedContextService,
)
from mnemo_memory.packages.domain import (
    CheckpointContent,
    CheckpointSourceObservation,
    CodeSnapshotId,
    ContextBudget,
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
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
from mnemo_memory.packages.project_index import (
    PythonSourceParser,
    PythonSourceParseRequest,
    SourceStructureParser,
    SourceStructureParseRequest,
)
from mnemo_memory.packages.storage import (
    ReferenceCheckpointRepository,
    ReferenceCheckpointSourceObservationRepository,
    ReferenceSourceStructureRepository,
)


def _checkpoint_content() -> CheckpointContent:
    return CheckpointContent(
        task_objective="Continue the bounded source-context task.",
        completed_work=("Saved an exact handoff.",),
        current_state="A source snapshot may be co-observed after this revision.",
        remaining_work=("Review the resulting structural facts.",),
        decisions=("Keep source observations explicitly non-causal.",),
        failures=(),
        blockers=(),
        relevant_files=("service.py",),
        relevant_artifacts=(),
        verification_performed=("focused test passed",),
        token_estimate=80,
    )


def _checkpoint_evidence() -> EvidenceReference:
    return EvidenceReference(
        EvidenceId.new(),
        SourceId.new(),
        EvidenceSourceType.CHECKPOINT,
        SourceTrustClass.USER_AUTHORED,
        "fixture://source-context/checkpoint",
        "sha256:" + "a" * 64,
        EvidenceLocation("fixture://source-context/checkpoint"),
        datetime(2026, 8, 4, tzinfo=UTC),
        VerificationStatus.VERIFIED,
    )


def test_source_query_returns_scoped_provenance_bearing_structural_facts(tmp_path: Path) -> None:
    project_scope = MemoryScope(
        OwnerId.from_string("11111111-1111-4111-8111-111111111111"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string("22222222-2222-4222-8222-222222222222"),
        ProjectId.from_string("33333333-3333-4333-8333-333333333333"),
    )
    task_scope = MemoryScope(
        project_scope.owner_id,
        ScopeLevel.TASK,
        project_scope.visibility,
        project_scope.workspace_id,
        project_scope.project_id,
        SessionId.new(),
        TaskId.new(),
    )
    root = tmp_path / "source"
    root.mkdir()
    (root / "service.py").write_text("def process_order():\n    return 1\n")
    source = ReferenceSourceStructureRepository()
    source.store_and_activate(
        PythonSourceParser().parse(PythonSourceParseRequest(project_scope, root))
    )
    context = UnifiedContextService(
        CheckpointApplicationService(
            ReferenceCheckpointRepository(), clock=lambda: datetime(2026, 8, 3, tzinfo=UTC)
        ),
        None,
        source,
    ).get_context(GetUnifiedContext(task_scope, source_query="process_order"))

    assert len(context.structural_items) == 1
    item = context.structural_items[0]
    assert "process_order" in item.content
    assert item.source_scope == task_scope
    assert item.evidence_references[0].content_hash.startswith("sha256:")
    assert item.validity.value == "unknown"
    assert '"currentness":"unknown"' in item.content
    assert str(root) not in item.content


def test_context_attaches_an_exact_checkpoint_source_observation_without_claiming_cause(
    tmp_path: Path,
) -> None:
    project_scope = MemoryScope(
        OwnerId.from_string("11111111-1111-4111-8111-111111111111"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string("22222222-2222-4222-8222-222222222222"),
        ProjectId.from_string("33333333-3333-4333-8333-333333333333"),
    )
    task_scope = MemoryScope(
        project_scope.owner_id,
        ScopeLevel.TASK,
        project_scope.visibility,
        project_scope.workspace_id,
        project_scope.project_id,
        SessionId.new(),
        TaskId.new(),
    )
    root = tmp_path / "source"
    root.mkdir()
    (root / "service.py").write_text("def reconcile():\n    return True\n", encoding="utf-8")
    source = ReferenceSourceStructureRepository()
    artifact = source.store_and_activate(
        SourceStructureParser().parse(SourceStructureParseRequest(project_scope, root))
    )
    checkpoints = ReferenceCheckpointRepository()
    service = CheckpointApplicationService(
        checkpoints, clock=lambda: datetime(2026, 8, 4, tzinfo=UTC)
    )
    view = service.create(
        CreateCheckpoint(task_scope, _checkpoint_content(), (_checkpoint_evidence(),))
    )
    observations = ReferenceCheckpointSourceObservationRepository(checkpoints, source)
    observations.append_checkpoint_source_observation(
        CheckpointSourceObservation(
            task_scope,
            view.aggregate.checkpoint_id,
            view.revision.revision_id,
            artifact.snapshot.snapshot_id,
            datetime(2026, 8, 4, tzinfo=UTC),
        )
    )

    packet = UnifiedContextService(service, None, source, observations).get_context(
        GetUnifiedContext(task_scope)
    )

    item = next(
        item for item in packet.structural_items if item.item_id.startswith("source-observation:")
    )
    assert str(view.revision.revision_id) in item.content
    assert str(artifact.snapshot.snapshot_id) in item.content
    assert "source_snapshot_observed_after_checkpoint_revision_persisted" in item.content
    assert "because" not in item.content
    assert item.validity.value == "unknown"
    assert len(item.evidence_references) == 2
    assert packet.provenance[-1].item_id == item.item_id
    assert str(root) not in item.content


def test_recent_source_changes_are_scoped_bounded_and_evidenced(tmp_path: Path) -> None:
    project_scope = MemoryScope(
        OwnerId.from_string("11111111-1111-4111-8111-111111111111"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string("22222222-2222-4222-8222-222222222222"),
        ProjectId.from_string("33333333-3333-4333-8333-333333333333"),
    )
    task_scope = MemoryScope(
        project_scope.owner_id,
        ScopeLevel.TASK,
        project_scope.visibility,
        project_scope.workspace_id,
        project_scope.project_id,
        SessionId.new(),
        TaskId.new(),
    )
    root = tmp_path / "source with unicode Ω"
    root.mkdir()
    path = root / "orders.py"
    path.write_text("def calculate_total():\n    return 1\n")
    source = ReferenceSourceStructureRepository()
    first = PythonSourceParser().parse(PythonSourceParseRequest(project_scope, root))
    source.store_and_activate(first)
    path.write_text(
        "def calculate_total():\n    return 2\n\ndef reconcile_orders():\n    return 3\n"
    )
    second = PythonSourceParser().parse(PythonSourceParseRequest(project_scope, root))
    source.store_and_activate(second)

    context = UnifiedContextService(
        CheckpointApplicationService(
            ReferenceCheckpointRepository(), clock=lambda: datetime(2026, 8, 3, tzinfo=UTC)
        ),
        None,
        source,
    ).get_context(
        GetUnifiedContext(
            task_scope,
            source_changes=ContextSourceChangeQuery(
                maximum_declarations=1,
                maximum_relationships=1,
                current_source_digest=second.snapshot.source_digest,
                require_current=True,
            ),
        )
    )

    assert len(context.structural_items) == 1
    item = context.structural_items[0]
    assert (
        item.item_id == f"source-change:{first.snapshot.snapshot_id}:{second.snapshot.snapshot_id}"
    )
    assert '"currentness":"current"' in item.content
    assert "reconcile_orders" in item.content
    assert '"modified_files":["orders.py"]' in item.content
    assert len(item.evidence_references) == 2
    assert all(
        reference.content_hash.startswith("sha256:") for reference in item.evidence_references
    )
    assert str(root) not in item.content
    assert context.provenance[-1].item_id == item.item_id


def test_recent_source_changes_cite_a_digest_proven_rename_without_source_text(
    tmp_path: Path,
) -> None:
    project_scope = MemoryScope(
        OwnerId.from_string("11111111-1111-4111-8111-111111111111"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string("22222222-2222-4222-8222-222222222222"),
        ProjectId.from_string("33333333-3333-4333-8333-333333333333"),
    )
    task_scope = MemoryScope(
        project_scope.owner_id,
        ScopeLevel.TASK,
        project_scope.visibility,
        project_scope.workspace_id,
        project_scope.project_id,
        SessionId.new(),
        TaskId.new(),
    )
    root = tmp_path / "source"
    root.mkdir()
    old_path = root / "legacy.py"
    old_path.write_text("def calculate():\n    return 1\n")
    source = ReferenceSourceStructureRepository()
    first = PythonSourceParser().parse(PythonSourceParseRequest(project_scope, root))
    source.store_and_activate(first)
    old_path.rename(root / "current.py")
    second = PythonSourceParser().parse(PythonSourceParseRequest(project_scope, root))
    source.store_and_activate(second)

    packet = UnifiedContextService(
        CheckpointApplicationService(
            ReferenceCheckpointRepository(), clock=lambda: datetime(2026, 8, 3, tzinfo=UTC)
        ),
        None,
        source,
    ).get_context(GetUnifiedContext(task_scope, source_changes=ContextSourceChangeQuery()))

    assert len(packet.structural_items) == 1
    assert json.loads(packet.structural_items[0].content)["renamed_files"] == [
        "legacy.py → current.py"
    ]
    assert "return 1" not in packet.structural_items[0].content


def test_recent_source_changes_do_not_disclose_scope_or_claim_unknown_is_current(
    tmp_path: Path,
) -> None:
    project_scope = MemoryScope(
        OwnerId.from_string("11111111-1111-4111-8111-111111111111"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string("22222222-2222-4222-8222-222222222222"),
        ProjectId.from_string("33333333-3333-4333-8333-333333333333"),
    )
    wrong_task_scope = MemoryScope(
        project_scope.owner_id,
        ScopeLevel.TASK,
        project_scope.visibility,
        project_scope.workspace_id,
        ProjectId.from_string("44444444-4444-4444-8444-444444444444"),
        SessionId.new(),
        TaskId.new(),
    )
    root = tmp_path / "source"
    root.mkdir()
    path = root / "private.py"
    path.write_text("def one():\n    return 1\n")
    source = ReferenceSourceStructureRepository()
    source.store_and_activate(
        PythonSourceParser().parse(PythonSourceParseRequest(project_scope, root))
    )
    path.write_text("def two():\n    return 2\n")
    source.store_and_activate(
        PythonSourceParser().parse(PythonSourceParseRequest(project_scope, root))
    )
    service = UnifiedContextService(
        CheckpointApplicationService(
            ReferenceCheckpointRepository(), clock=lambda: datetime(2026, 8, 3, tzinfo=UTC)
        ),
        None,
        source,
    )

    unknown = service.get_context(
        GetUnifiedContext(
            MemoryScope(
                project_scope.owner_id,
                ScopeLevel.TASK,
                project_scope.visibility,
                project_scope.workspace_id,
                project_scope.project_id,
                SessionId.new(),
                TaskId.new(),
            ),
            source_changes=ContextSourceChangeQuery(),
        )
    )
    hidden = service.get_context(
        GetUnifiedContext(wrong_task_scope, source_changes=ContextSourceChangeQuery())
    )

    assert unknown.structural_items[0].validity.value == "unknown"
    assert hidden.structural_items == ()
    assert hidden.omissions[-1].detail == "no prior source transition"


def test_recent_source_changes_require_current_and_respect_the_structural_budget(
    tmp_path: Path,
) -> None:
    project_scope = MemoryScope(
        OwnerId.from_string("11111111-1111-4111-8111-111111111111"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string("22222222-2222-4222-8222-222222222222"),
        ProjectId.from_string("33333333-3333-4333-8333-333333333333"),
    )
    task_scope = MemoryScope(
        project_scope.owner_id,
        ScopeLevel.TASK,
        project_scope.visibility,
        project_scope.workspace_id,
        project_scope.project_id,
        SessionId.new(),
        TaskId.new(),
    )
    root = tmp_path / "source"
    root.mkdir()
    path = root / "orders.py"
    path.write_text("def old():\n    return 1\n")
    source = ReferenceSourceStructureRepository()
    source.store_and_activate(
        PythonSourceParser().parse(PythonSourceParseRequest(project_scope, root))
    )
    path.write_text("def new():\n    return 2\n")
    source.store_and_activate(
        PythonSourceParser().parse(PythonSourceParseRequest(project_scope, root))
    )
    service = UnifiedContextService(
        CheckpointApplicationService(
            ReferenceCheckpointRepository(), clock=lambda: datetime(2026, 8, 3, tzinfo=UTC)
        ),
        None,
        source,
    )

    not_current = service.get_context(
        GetUnifiedContext(
            task_scope,
            source_changes=ContextSourceChangeQuery(require_current=True),
        )
    )
    budgeted = service.get_context(
        GetUnifiedContext(
            task_scope,
            budget=ContextBudget(structural=0),
            source_changes=ContextSourceChangeQuery(),
        )
    )

    assert not_current.structural_items == ()
    assert not_current.omissions[-1].reason.value == "stale"
    assert budgeted.structural_items == ()
    assert budgeted.omissions[-1].reason.value == "token_budget"


def test_source_changes_can_select_a_scoped_historical_snapshot_pair(tmp_path: Path) -> None:
    project_scope = MemoryScope(
        OwnerId.from_string("11111111-1111-4111-8111-111111111111"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string("22222222-2222-4222-8222-222222222222"),
        ProjectId.from_string("33333333-3333-4333-8333-333333333333"),
    )
    task_scope = MemoryScope(
        project_scope.owner_id,
        ScopeLevel.TASK,
        project_scope.visibility,
        project_scope.workspace_id,
        project_scope.project_id,
        SessionId.new(),
        TaskId.new(),
    )
    root = tmp_path / "source"
    root.mkdir()
    path = root / "service.py"
    source = ReferenceSourceStructureRepository()
    path.write_text("def first():\n    return 1\n")
    first = PythonSourceParser().parse(PythonSourceParseRequest(project_scope, root))
    source.store_and_activate(first)
    path.write_text("def second():\n    return 2\n")
    second = PythonSourceParser().parse(PythonSourceParseRequest(project_scope, root))
    source.store_and_activate(second)
    path.write_text("def third():\n    return 3\n")
    source.store_and_activate(
        PythonSourceParser().parse(PythonSourceParseRequest(project_scope, root))
    )
    service = UnifiedContextService(
        CheckpointApplicationService(
            ReferenceCheckpointRepository(), clock=lambda: datetime(2026, 8, 3, tzinfo=UTC)
        ),
        None,
        source,
    )

    packet = service.get_context(
        GetUnifiedContext(
            task_scope,
            source_changes=ContextSourceChangeQuery(
                before_snapshot_id=first.snapshot.snapshot_id,
                after_snapshot_id=second.snapshot.snapshot_id,
            ),
        )
    )

    assert len(packet.structural_items) == 1
    assert (
        '"before_snapshot_id":"' + str(first.snapshot.snapshot_id)
        in packet.structural_items[0].content
    )
    assert (
        '"after_snapshot_id":"' + str(second.snapshot.snapshot_id)
        in packet.structural_items[0].content
    )
    assert "second" in packet.structural_items[0].content
    assert "third" not in packet.structural_items[0].content


def test_source_change_history_can_be_bounded_to_one_relative_path(tmp_path: Path) -> None:
    project_scope = MemoryScope(
        OwnerId.from_string("11111111-1111-4111-8111-111111111111"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string("22222222-2222-4222-8222-222222222222"),
        ProjectId.from_string("33333333-3333-4333-8333-333333333333"),
    )
    task_scope = MemoryScope(
        project_scope.owner_id,
        ScopeLevel.TASK,
        project_scope.visibility,
        project_scope.workspace_id,
        project_scope.project_id,
        SessionId.new(),
        TaskId.new(),
    )
    root = tmp_path / "source history Ω"
    root.mkdir()
    tracked = root / "orders.py"
    unrelated = root / "private.py"
    tracked.write_text("def orders():\n    return 1\n")
    unrelated.write_text("def private():\n    return 1\n")
    source = ReferenceSourceStructureRepository()
    parser = PythonSourceParser()
    first = parser.parse(PythonSourceParseRequest(project_scope, root))
    source.store_and_activate(first)
    tracked.write_text("def orders():\n    return 2\n")
    second = parser.parse(PythonSourceParseRequest(project_scope, root))
    source.store_and_activate(second)
    unrelated.write_text("def private():\n    return 2\n")
    third = parser.parse(PythonSourceParseRequest(project_scope, root))
    source.store_and_activate(third)
    tracked.write_text("def orders():\n    return 3\n")
    fourth = parser.parse(PythonSourceParseRequest(project_scope, root))
    source.store_and_activate(fourth)
    service = UnifiedContextService(
        CheckpointApplicationService(
            ReferenceCheckpointRepository(), clock=lambda: datetime(2026, 8, 3, tzinfo=UTC)
        ),
        None,
        source,
    )

    packet = service.get_context(
        GetUnifiedContext(
            task_scope,
            source_changes=ContextSourceChangeQuery(
                relative_path="orders.py", maximum_transitions=3
            ),
        )
    )

    assert [item.item_id for item in packet.structural_items] == [
        f"source-change:{third.snapshot.snapshot_id}:{fourth.snapshot.snapshot_id}",
        f"source-change:{first.snapshot.snapshot_id}:{second.snapshot.snapshot_id}",
    ]
    assert all(
        '"requested_relative_path":"orders.py"' in item.content for item in packet.structural_items
    )
    assert all("private.py" not in item.content for item in packet.structural_items)
    assert all(len(item.evidence_references) == 2 for item in packet.structural_items)
    assert len(packet.provenance) == 2


def test_source_change_history_rejects_unsafe_path_and_reports_no_matching_path(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="canonical relative path"):
        ContextSourceChangeQuery(relative_path="../private.py")
    with pytest.raises(ValueError, match="cannot request history"):
        ContextSourceChangeQuery(
            maximum_transitions=2,
            before_snapshot_id=CodeSnapshotId.new(),
            after_snapshot_id=CodeSnapshotId.new(),
        )
    project_scope = MemoryScope(
        OwnerId.from_string("11111111-1111-4111-8111-111111111111"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string("22222222-2222-4222-8222-222222222222"),
        ProjectId.from_string("33333333-3333-4333-8333-333333333333"),
    )
    root = tmp_path / "source"
    root.mkdir()
    path = root / "orders.py"
    source = ReferenceSourceStructureRepository()
    path.write_text("def one():\n    return 1\n")
    source.store_and_activate(
        PythonSourceParser().parse(PythonSourceParseRequest(project_scope, root))
    )
    path.write_text("def two():\n    return 2\n")
    source.store_and_activate(
        PythonSourceParser().parse(PythonSourceParseRequest(project_scope, root))
    )
    packet = UnifiedContextService(
        CheckpointApplicationService(
            ReferenceCheckpointRepository(), clock=lambda: datetime(2026, 8, 3, tzinfo=UTC)
        ),
        None,
        source,
    ).get_context(
        GetUnifiedContext(
            MemoryScope(
                project_scope.owner_id,
                ScopeLevel.TASK,
                project_scope.visibility,
                project_scope.workspace_id,
                project_scope.project_id,
                SessionId.new(),
                TaskId.new(),
            ),
            source_changes=ContextSourceChangeQuery(relative_path="not-recorded.py"),
        )
    )
    assert packet.structural_items == ()
    assert packet.omissions[-1].detail == "no recorded source changes for requested relative path"


def test_source_query_does_not_disclose_another_projects_snapshot(tmp_path: Path) -> None:
    project_scope = MemoryScope(
        OwnerId.from_string("11111111-1111-4111-8111-111111111111"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string("22222222-2222-4222-8222-222222222222"),
        ProjectId.from_string("33333333-3333-4333-8333-333333333333"),
    )
    wrong_task_scope = MemoryScope(
        project_scope.owner_id,
        ScopeLevel.TASK,
        project_scope.visibility,
        project_scope.workspace_id,
        ProjectId.from_string("44444444-4444-4444-8444-444444444444"),
        SessionId.new(),
        TaskId.new(),
    )
    root = tmp_path / "private-source"
    root.mkdir()
    (root / "service.py").write_text("def private_operation():\n    return 1\n")
    source = ReferenceSourceStructureRepository()
    source.store_and_activate(
        PythonSourceParser().parse(PythonSourceParseRequest(project_scope, root))
    )
    packet = UnifiedContextService(
        CheckpointApplicationService(
            ReferenceCheckpointRepository(), clock=lambda: datetime(2026, 8, 3, tzinfo=UTC)
        ),
        None,
        source,
    ).get_context(GetUnifiedContext(wrong_task_scope, source_query="private_operation"))

    assert packet.structural_items == ()
    assert packet.provenance == ()
    assert packet.omissions[-1].detail == "no source snapshot"


def test_source_query_includes_matching_module_import_edges(tmp_path: Path) -> None:
    project_scope = MemoryScope(
        OwnerId.from_string("11111111-1111-4111-8111-111111111111"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string("22222222-2222-4222-8222-222222222222"),
        ProjectId.from_string("33333333-3333-4333-8333-333333333333"),
    )
    task_scope = MemoryScope(
        project_scope.owner_id,
        ScopeLevel.TASK,
        project_scope.visibility,
        project_scope.workspace_id,
        project_scope.project_id,
        SessionId.new(),
        TaskId.new(),
    )
    root = tmp_path / "source"
    root.mkdir()
    (root / "orders.py").write_text("import payments\n\ndef process_order():\n    return 1\n")
    source = ReferenceSourceStructureRepository()
    source.store_and_activate(
        PythonSourceParser().parse(PythonSourceParseRequest(project_scope, root))
    )
    packet = UnifiedContextService(
        CheckpointApplicationService(
            ReferenceCheckpointRepository(), clock=lambda: datetime(2026, 8, 3, tzinfo=UTC)
        ),
        None,
        source,
    ).get_context(GetUnifiedContext(task_scope, source_query="process_order"))

    assert len(packet.structural_items) == 2
    assert any('"relationship":"imports"' in item.content for item in packet.structural_items)
    assert len(packet.provenance) == 2


def test_source_query_includes_explicit_calls_from_a_matching_function(tmp_path: Path) -> None:
    project_scope = MemoryScope(
        OwnerId.from_string("11111111-1111-4111-8111-111111111111"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string("22222222-2222-4222-8222-222222222222"),
        ProjectId.from_string("33333333-3333-4333-8333-333333333333"),
    )
    task_scope = MemoryScope(
        project_scope.owner_id,
        ScopeLevel.TASK,
        project_scope.visibility,
        project_scope.workspace_id,
        project_scope.project_id,
        SessionId.new(),
        TaskId.new(),
    )
    root = tmp_path / "source"
    root.mkdir()
    (root / "orders.py").write_text("def process_order():\n    payments.capture()\n")
    source = ReferenceSourceStructureRepository()
    source.store_and_activate(
        PythonSourceParser().parse(PythonSourceParseRequest(project_scope, root))
    )
    packet = UnifiedContextService(
        CheckpointApplicationService(
            ReferenceCheckpointRepository(), clock=lambda: datetime(2026, 8, 3, tzinfo=UTC)
        ),
        None,
        source,
    ).get_context(GetUnifiedContext(task_scope, source_query="process_order"))

    assert any('"relationship":"calls"' in item.content for item in packet.structural_items)
    assert any('"target":"payments.capture"' in item.content for item in packet.structural_items)


def test_source_query_returns_typescript_static_import_and_call_facts(tmp_path: Path) -> None:
    project_scope = MemoryScope(
        OwnerId.from_string("11111111-1111-4111-8111-111111111111"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string("22222222-2222-4222-8222-222222222222"),
        ProjectId.from_string("33333333-3333-4333-8333-333333333333"),
    )
    task_scope = MemoryScope(
        project_scope.owner_id,
        ScopeLevel.TASK,
        project_scope.visibility,
        project_scope.workspace_id,
        project_scope.project_id,
        SessionId.new(),
        TaskId.new(),
    )
    root = tmp_path / "source"
    root.mkdir()
    (root / "orders.ts").write_text(
        "import { capture } from './payments';\n"
        "export function processOrder() { capture(); client.send(); }\n"
    )
    (root / "payments.ts").write_text("export function capture() {}\n")
    source = ReferenceSourceStructureRepository()
    source.store_and_activate(
        SourceStructureParser().parse(SourceStructureParseRequest(project_scope, root))
    )
    packet = UnifiedContextService(
        CheckpointApplicationService(
            ReferenceCheckpointRepository(), clock=lambda: datetime(2026, 8, 3, tzinfo=UTC)
        ),
        None,
        source,
    ).get_context(GetUnifiedContext(task_scope, source_query="processOrder"))

    assert any('"relationship":"imports"' in item.content for item in packet.structural_items)
    assert any('"target":"capture"' in item.content for item in packet.structural_items)
    assert any('"target":"client.send"' in item.content for item in packet.structural_items)
    assert any(
        '"resolved_target":{"path":"payments.ts","symbol":"payments"}' in item.content
        for item in packet.structural_items
    )
    assert len(packet.provenance) == len(packet.structural_items)


def test_source_impact_context_returns_bounded_dependents_with_provenance(tmp_path: Path) -> None:
    project_scope = MemoryScope(
        OwnerId.from_string("11111111-1111-4111-8111-111111111111"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string("22222222-2222-4222-8222-222222222222"),
        ProjectId.from_string("33333333-3333-4333-8333-333333333333"),
    )
    task_scope = MemoryScope(
        project_scope.owner_id,
        ScopeLevel.TASK,
        project_scope.visibility,
        project_scope.workspace_id,
        project_scope.project_id,
        SessionId.new(),
        TaskId.new(),
    )
    root = tmp_path / "source"
    root.mkdir()
    (root / "core.py").write_text("def calculate():\n    return 1\n")
    (root / "service.py").write_text("import core\n\ndef serve():\n    return core.calculate()\n")
    (root / "app.py").write_text("import service\n\ndef run():\n    return service.serve()\n")
    source = ReferenceSourceStructureRepository()
    source.store_and_activate(
        SourceStructureParser().parse(SourceStructureParseRequest(project_scope, root))
    )

    packet = UnifiedContextService(
        CheckpointApplicationService(
            ReferenceCheckpointRepository(), clock=lambda: datetime(2026, 8, 3, tzinfo=UTC)
        ),
        None,
        source,
    ).get_context(
        GetUnifiedContext(
            task_scope,
            source_impact=ContextSourceImpactQuery("core", maximum_depth=1),
        )
    )

    assert any('"symbol":"core"' in item.content for item in packet.structural_items)
    assert any('"symbol":"service"' in item.content for item in packet.structural_items)
    assert all('"currentness":"unknown"' in item.content for item in packet.structural_items)
    assert any('"impact_depth":1' in item.content for item in packet.structural_items)
    assert packet.omissions[-1].detail == "maximum depth reached"
    assert len(packet.provenance) == len(packet.structural_items)


def test_source_impact_context_resolves_a_relative_path_exactly(tmp_path: Path) -> None:
    project_scope = MemoryScope(
        OwnerId.from_string("11111111-1111-4111-8111-111111111111"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string("22222222-2222-4222-8222-222222222222"),
        ProjectId.from_string("33333333-3333-4333-8333-333333333333"),
    )
    task_scope = MemoryScope(
        project_scope.owner_id,
        ScopeLevel.TASK,
        project_scope.visibility,
        project_scope.workspace_id,
        project_scope.project_id,
        SessionId.new(),
        TaskId.new(),
    )
    root = tmp_path / "source"
    root.mkdir()
    (root / "core.py").write_text("def calculate():\n    return 1\n")
    (root / "other_core.py").write_text("def calculate():\n    return 2\n")
    (root / "service.py").write_text("import core\n\ndef serve():\n    return core.calculate()\n")
    source = ReferenceSourceStructureRepository()
    source.store_and_activate(
        SourceStructureParser().parse(SourceStructureParseRequest(project_scope, root))
    )

    packet = UnifiedContextService(
        CheckpointApplicationService(
            ReferenceCheckpointRepository(), clock=lambda: datetime(2026, 8, 3, tzinfo=UTC)
        ),
        None,
        source,
    ).get_context(
        GetUnifiedContext(
            task_scope,
            source_impact=ContextSourceImpactQuery(None, relative_path="core.py"),
        )
    )

    assert any('"path":"core.py"' in item.content for item in packet.structural_items)
    assert any('"path":"service.py"' in item.content for item in packet.structural_items)
    assert not any('"path":"other_core.py"' in item.content for item in packet.structural_items)


def test_checkpoint_relevant_file_selects_bounded_current_static_impact(tmp_path: Path) -> None:
    project_scope = MemoryScope(
        OwnerId.from_string("11111111-1111-4111-8111-111111111111"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string("22222222-2222-4222-8222-222222222222"),
        ProjectId.from_string("33333333-3333-4333-8333-333333333333"),
    )
    task_scope = MemoryScope(
        project_scope.owner_id,
        ScopeLevel.TASK,
        project_scope.visibility,
        project_scope.workspace_id,
        project_scope.project_id,
        SessionId.new(),
        TaskId.new(),
    )
    root = tmp_path / "source"
    root.mkdir()
    (root / "core.py").write_text("def calculate():\n    return 1\n", encoding="utf-8")
    (root / "service.py").write_text(
        "import core\n\ndef serve():\n    return core.calculate()\n", encoding="utf-8"
    )
    source = ReferenceSourceStructureRepository()
    stored = source.store_and_activate(
        SourceStructureParser().parse(SourceStructureParseRequest(project_scope, root))
    )
    checkpoints = CheckpointApplicationService(
        ReferenceCheckpointRepository(), clock=lambda: datetime(2026, 8, 3, tzinfo=UTC)
    )
    checkpoints.create(
        CreateCheckpoint(
            task_scope,
            CheckpointContent(
                "Assess the impact of a core calculation change.",
                (),
                "The calculation was changed and needs impact review.",
                (),
                (),
                (),
                (),
                ("core.py",),
                (),
                (),
                40,
            ),
            (_checkpoint_evidence(),),
        )
    )

    packet = UnifiedContextService(checkpoints, None, source).get_context(
        GetUnifiedContext(
            task_scope,
            checkpoint_source_impact=ContextCheckpointSourceImpact(
                current_source_digest=stored.snapshot.source_digest
            ),
        )
    )

    assert any('"path":"core.py"' in item.content for item in packet.structural_items)
    assert any('"path":"service.py"' in item.content for item in packet.structural_items)
    assert any(
        '"impact_direction":"dependents"' in item.content for item in packet.structural_items
    )
    assert all('"currentness":"current"' in item.content for item in packet.structural_items)
    assert all(item.evidence_references for item in packet.structural_items)
    assert "return core.calculate" not in "".join(item.content for item in packet.structural_items)


def test_source_impact_honors_the_symbol_limit_for_a_file_start(tmp_path: Path) -> None:
    project_scope = MemoryScope(
        OwnerId.from_string("11111111-1111-4111-8111-111111111111"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string("22222222-2222-4222-8222-222222222222"),
        ProjectId.from_string("33333333-3333-4333-8333-333333333333"),
    )
    task_scope = MemoryScope(
        project_scope.owner_id,
        ScopeLevel.TASK,
        project_scope.visibility,
        project_scope.workspace_id,
        project_scope.project_id,
        SessionId.new(),
        TaskId.new(),
    )
    root = tmp_path / "source"
    root.mkdir()
    (root / "many.py").write_text(
        "def first():\n    return 1\n\ndef second():\n    return 2\n", encoding="utf-8"
    )
    source = ReferenceSourceStructureRepository()
    source.store_and_activate(
        SourceStructureParser().parse(SourceStructureParseRequest(project_scope, root))
    )
    packet = UnifiedContextService(
        CheckpointApplicationService(
            ReferenceCheckpointRepository(), clock=lambda: datetime(2026, 8, 3, tzinfo=UTC)
        ),
        None,
        source,
    ).get_context(
        GetUnifiedContext(
            task_scope,
            source_impact=ContextSourceImpactQuery(
                None, relative_path="many.py", maximum_symbols=1
            ),
        )
    )

    symbols = [item for item in packet.structural_items if item.item_id.startswith("source:")]
    assert len(symbols) == 1
    assert not any(item.item_id.startswith("source-edge:") for item in packet.structural_items)
    assert packet.omissions[-1].detail == "maximum symbol count reached"


def test_source_impact_can_select_an_immutable_historical_snapshot(tmp_path: Path) -> None:
    project_scope = MemoryScope(
        OwnerId.from_string("11111111-1111-4111-8111-111111111111"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string("22222222-2222-4222-8222-222222222222"),
        ProjectId.from_string("33333333-3333-4333-8333-333333333333"),
    )
    task_scope = MemoryScope(
        project_scope.owner_id,
        ScopeLevel.TASK,
        project_scope.visibility,
        project_scope.workspace_id,
        project_scope.project_id,
        SessionId.new(),
        TaskId.new(),
    )
    root = tmp_path / "source"
    root.mkdir()
    (root / "core.py").write_text("def calculate():\n    return 1\n")
    source = ReferenceSourceStructureRepository()
    first = SourceStructureParser().parse(SourceStructureParseRequest(project_scope, root))
    source.store_and_activate(first)
    (root / "service.py").write_text("import core\n\ndef serve():\n    return core.calculate()\n")
    source.store_and_activate(
        SourceStructureParser().parse(SourceStructureParseRequest(project_scope, root))
    )

    packet = UnifiedContextService(
        CheckpointApplicationService(
            ReferenceCheckpointRepository(), clock=lambda: datetime(2026, 8, 3, tzinfo=UTC)
        ),
        None,
        source,
    ).get_context(
        GetUnifiedContext(
            task_scope,
            source_impact=ContextSourceImpactQuery(
                "core", maximum_depth=1, snapshot_id=first.snapshot.snapshot_id
            ),
        )
    )

    assert any(
        f'"snapshot_id":"{first.snapshot.snapshot_id}"' in item.content
        for item in packet.structural_items
    )
    assert not any('"symbol":"service"' in item.content for item in packet.structural_items)


def test_source_impact_currentness_requires_an_exact_source_digest(tmp_path: Path) -> None:
    project_scope = MemoryScope(
        OwnerId.from_string("11111111-1111-4111-8111-111111111111"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string("22222222-2222-4222-8222-222222222222"),
        ProjectId.from_string("33333333-3333-4333-8333-333333333333"),
    )
    task_scope = MemoryScope(
        project_scope.owner_id,
        ScopeLevel.TASK,
        project_scope.visibility,
        project_scope.workspace_id,
        project_scope.project_id,
        SessionId.new(),
        TaskId.new(),
    )
    root = tmp_path / "source"
    root.mkdir()
    (root / "core.py").write_text("def calculate():\n    return 1\n")
    source = ReferenceSourceStructureRepository()
    stored = source.store_and_activate(
        SourceStructureParser().parse(SourceStructureParseRequest(project_scope, root))
    )
    service = UnifiedContextService(
        CheckpointApplicationService(
            ReferenceCheckpointRepository(), clock=lambda: datetime(2026, 8, 3, tzinfo=UTC)
        ),
        None,
        source,
    )

    current = service.get_context(
        GetUnifiedContext(
            task_scope,
            source_impact=ContextSourceImpactQuery(
                "core", current_source_digest=stored.snapshot.source_digest
            ),
        )
    )
    stale = service.get_context(
        GetUnifiedContext(
            task_scope,
            source_impact=ContextSourceImpactQuery(
                "core",
                current_source_digest="sha256:" + "0" * 64,
                require_current=True,
            ),
        )
    )

    assert all('"currentness":"current"' in item.content for item in current.structural_items)
    assert stale.structural_items == ()
    assert stale.omissions[-1].detail == "source snapshot is not proven current"


def test_source_overview_is_scoped_deterministic_bounded_and_provenance_bearing(
    tmp_path: Path,
) -> None:
    project_scope = MemoryScope(
        OwnerId.from_string("11111111-1111-4111-8111-111111111111"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string("22222222-2222-4222-8222-222222222222"),
        ProjectId.from_string("33333333-3333-4333-8333-333333333333"),
    )
    task_scope = MemoryScope(
        project_scope.owner_id,
        ScopeLevel.TASK,
        project_scope.visibility,
        project_scope.workspace_id,
        project_scope.project_id,
        SessionId.new(),
        TaskId.new(),
    )
    root = tmp_path / "source directory with ünicode"
    root.mkdir()
    (root / "app.py").write_text(
        "from helpers import normalize\n\ndef run():\n    return normalize(1)\n",
        encoding="utf-8",
    )
    (root / "helpers.py").write_text("def normalize(value):\n    return value\n", encoding="utf-8")
    source = ReferenceSourceStructureRepository()
    stored = source.store_and_activate(
        SourceStructureParser().parse(SourceStructureParseRequest(project_scope, root))
    )
    service = UnifiedContextService(
        CheckpointApplicationService(
            ReferenceCheckpointRepository(), clock=lambda: datetime(2026, 8, 3, tzinfo=UTC)
        ),
        None,
        source,
    )
    query = ContextSourceOverviewQuery(
        maximum_modules=2,
        maximum_declarations=2,
        current_source_digest=stored.snapshot.source_digest,
    )

    first = service.get_context(GetUnifiedContext(task_scope, source_overview=query))
    second = service.get_context(GetUnifiedContext(task_scope, source_overview=query))

    first_payload = first.to_dict()
    second_payload = second.to_dict()
    first_payload.pop("request_id")
    second_payload.pop("request_id")
    assert first_payload == second_payload
    overview = next(
        item for item in first.structural_items if item.item_id.startswith("source-overview:")
    )
    assert '"kind":"source_snapshot_overview"' in overview.content
    assert '"file_count":2' in overview.content
    assert '"currentness":"current"' in overview.content
    assert str(root) not in overview.content
    assert overview.evidence_references[0].content_hash == stored.snapshot.source_digest
    file_item = next(
        item for item in first.structural_items if item.item_id.startswith("source-file:")
    )
    assert '"kind":"source_file"' in file_item.content
    assert '"path":"app.py"' in file_item.content
    assert file_item.evidence_references[0].content_hash.startswith("sha256:")
    assert any(item.item_id.startswith("source:") for item in first.structural_items)
    assert all(item.evidence_references for item in first.structural_items)
    assert all(str(root) not in item.content for item in first.structural_items)

    wrong_scope = MemoryScope(
        task_scope.owner_id,
        ScopeLevel.TASK,
        task_scope.visibility,
        task_scope.workspace_id,
        ProjectId.new(),
        task_scope.session_id,
        task_scope.task_id,
    )
    cross_scope = service.get_context(
        GetUnifiedContext(wrong_scope, source_overview=ContextSourceOverviewQuery())
    )
    assert cross_scope.structural_items == ()
    assert cross_scope.omissions[-1].detail == "no source snapshot"


def test_source_overview_currentness_and_budget_omissions_are_explicit(tmp_path: Path) -> None:
    project_scope = MemoryScope(
        OwnerId.from_string("11111111-1111-4111-8111-111111111111"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string("22222222-2222-4222-8222-222222222222"),
        ProjectId.from_string("33333333-3333-4333-8333-333333333333"),
    )
    task_scope = MemoryScope(
        project_scope.owner_id,
        ScopeLevel.TASK,
        project_scope.visibility,
        project_scope.workspace_id,
        project_scope.project_id,
        SessionId.new(),
        TaskId.new(),
    )
    root = tmp_path / "source"
    root.mkdir()
    (root / "module.py").write_text("def stable():\n    return 1\n", encoding="utf-8")
    source = ReferenceSourceStructureRepository()
    stored = source.store_and_activate(
        SourceStructureParser().parse(SourceStructureParseRequest(project_scope, root))
    )
    service = UnifiedContextService(
        CheckpointApplicationService(
            ReferenceCheckpointRepository(), clock=lambda: datetime(2026, 8, 3, tzinfo=UTC)
        ),
        None,
        source,
    )

    stale = service.get_context(
        GetUnifiedContext(
            task_scope,
            source_overview=ContextSourceOverviewQuery(
                current_source_digest="sha256:" + "0" * 64, require_current=True
            ),
        )
    )
    exhausted = service.get_context(
        GetUnifiedContext(
            task_scope,
            budget=ContextBudget(structural=0),
            source_overview=ContextSourceOverviewQuery(
                current_source_digest=stored.snapshot.source_digest
            ),
        )
    )

    assert stale.structural_items == ()
    assert stale.omissions[-1].detail == "source snapshot is not proven current"
    assert exhausted.structural_items == ()
    assert (
        exhausted.omissions[-1].detail
        == "source overview summary exceeds remaining structural budget"
    )


@pytest.mark.parametrize(
    "kwargs",
    (
        {"maximum_modules": 0},
        {"maximum_files": 0},
        {"maximum_declarations": 0},
        {"maximum_modules": 33},
        {"maximum_files": 33},
        {"maximum_declarations": 65},
        {"current_source_digest": "not-a-digest"},
    ),
)
def test_source_overview_query_rejects_unbounded_or_invalid_inputs(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        ContextSourceOverviewQuery(**kwargs)  # type: ignore[arg-type]


def test_source_overview_includes_file_only_inputs_without_claiming_structure(
    tmp_path: Path,
) -> None:
    project_scope = MemoryScope(
        OwnerId.from_string("11111111-1111-4111-8111-111111111111"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string("22222222-2222-4222-8222-222222222222"),
        ProjectId.from_string("33333333-3333-4333-8333-333333333333"),
    )
    task_scope = MemoryScope(
        project_scope.owner_id,
        ScopeLevel.TASK,
        project_scope.visibility,
        project_scope.workspace_id,
        project_scope.project_id,
        SessionId.new(),
        TaskId.new(),
    )
    root = tmp_path / "source"
    root.mkdir()
    (root / "models").mkdir()
    (root / "models" / "orders.sql").write_text("select 1", encoding="utf-8")
    source = ReferenceSourceStructureRepository()
    source.store_and_activate(
        SourceStructureParser().parse(SourceStructureParseRequest(project_scope, root))
    )
    packet = UnifiedContextService(
        CheckpointApplicationService(
            ReferenceCheckpointRepository(), clock=lambda: datetime(2026, 8, 3, tzinfo=UTC)
        ),
        None,
        source,
    ).get_context(
        GetUnifiedContext(
            task_scope,
            source_overview=ContextSourceOverviewQuery(
                maximum_files=1, maximum_modules=1, maximum_declarations=1
            ),
        )
    )

    file_item = next(
        item for item in packet.structural_items if item.item_id.startswith("source-file:")
    )
    assert '"path":"models/orders.sql"' in file_item.content
    assert not any(item.item_id.startswith("source:") for item in packet.structural_items)
    assert "select 1" not in file_item.content


def test_source_overview_discloses_bounded_sample_counts(tmp_path: Path) -> None:
    project_scope = MemoryScope(
        OwnerId.from_string("11111111-1111-4111-8111-111111111111"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string("22222222-2222-4222-8222-222222222222"),
        ProjectId.from_string("33333333-3333-4333-8333-333333333333"),
    )
    task_scope = MemoryScope(
        project_scope.owner_id,
        ScopeLevel.TASK,
        project_scope.visibility,
        project_scope.workspace_id,
        project_scope.project_id,
        SessionId.new(),
        TaskId.new(),
    )
    root = tmp_path / "source"
    root.mkdir()
    for name in ("a", "b", "c"):
        (root / f"{name}.py").write_text(f"def {name}():\n    return 1\n", encoding="utf-8")
    source = ReferenceSourceStructureRepository()
    source.store_and_activate(
        SourceStructureParser().parse(SourceStructureParseRequest(project_scope, root))
    )
    packet = UnifiedContextService(
        CheckpointApplicationService(
            ReferenceCheckpointRepository(), clock=lambda: datetime(2026, 8, 3, tzinfo=UTC)
        ),
        None,
        source,
    ).get_context(
        GetUnifiedContext(
            task_scope,
            source_overview=ContextSourceOverviewQuery(
                maximum_files=1, maximum_modules=1, maximum_declarations=1
            ),
        )
    )

    overview = next(
        item for item in packet.structural_items if item.item_id.startswith("source-overview:")
    )
    assert '"selected_file_count":1' in overview.content
    assert '"omitted_file_count":2' in overview.content
    assert '"selected_module_count":1' in overview.content
    assert '"omitted_module_count":2' in overview.content
    assert '"selected_declaration_count":1' in overview.content
    assert '"omitted_declaration_count":2' in overview.content
