from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from mnemo_memory.packages.application.checkpoints import CheckpointApplicationService
from mnemo_memory.packages.application.unified_context import (
    GetUnifiedContext,
    UnifiedContextService,
)
from mnemo_memory.packages.domain import (
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    SessionId,
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
from mnemo_memory.packages.storage import (
    ReferenceCheckpointRepository,
    ReferenceSourceStructureRepository,
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
