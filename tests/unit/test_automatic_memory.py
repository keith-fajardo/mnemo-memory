"""Opt-in automatic task-memory hooks never capture raw client content."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Thread

import pytest
from typer.testing import CliRunner

from mnemo_memory.apps.cli import main as cli
from mnemo_memory.connectors.automatic_memory.client_config import (
    disable_client_hooks,
    enable_client_hooks,
)
from mnemo_memory.connectors.automatic_memory.hook import AutomaticMemoryHook
from mnemo_memory.connectors.automatic_memory.source_observation import CheckpointSourceObserver
from mnemo_memory.connectors.dbt.manifest import DbtManifestParser
from mnemo_memory.connectors.dbt.project_binding import (
    DbtProjectBinding,
    LocalDbtProjectBindingStore,
)
from mnemo_memory.packages.application.automatic_memory import (
    AutomaticMemoryBindingError,
    LocalMemoryProjectBindingStore,
    LocalObsidianVaultBindingStore,
    exclusive_local_file_lock,
)
from mnemo_memory.packages.application.bootstrap import build_checkpoint_runtime
from mnemo_memory.packages.application.checkpoints import CreateCheckpoint
from mnemo_memory.packages.application.config import LocalConfig
from mnemo_memory.packages.application.dbt import DbtManifestApplicationService, IngestManifest
from mnemo_memory.packages.application.mcp_durable import DurableMcpContextPort
from mnemo_memory.packages.application.unified_context import UnifiedContextService
from mnemo_memory.packages.domain import (
    CheckpointContent,
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    MemoryScope,
    SourceId,
    SourceTrustClass,
    VerificationStatus,
)
from mnemo_memory.packages.project_index import SourceStructureParser, SourceStructureParseRequest
from mnemo_memory.packages.storage import (
    SQLiteCheckpointRepository,
    SQLiteKnowledgeDocumentRepository,
    SQLiteSourceStructureRepository,
)

ROOT = Path(__file__).parents[2]
DBT_FIXTURE = ROOT / "tests" / "fixtures" / "dbt" / "manifest-v12.json"


def test_personal_binding_is_stable_and_never_derived_from_path(tmp_path: Path) -> None:
    project = tmp_path / "Project Δ with spaces"
    project.mkdir()
    data = tmp_path / "Mnemo Data"
    store = LocalMemoryProjectBindingStore(data)

    first = store.enable(project)
    second = store.enable(project)
    other = store.enable(tmp_path)

    assert first == second
    assert first.scope.owner_id == other.scope.owner_id
    assert first.scope.workspace_id == other.scope.workspace_id
    assert first.scope.project_id != other.scope.project_id
    assert first.checkpoint_scope.level.value == "task"
    assert first.checkpoint_scope.project_id == first.scope.project_id
    assert str(project) not in (data / "automatic-memory-profile.json").read_text()


def test_local_binding_lock_serializes_concurrent_updates_without_storing_paths(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    first_entered = Event()
    release_first = Event()
    second_entered = Event()

    def first() -> None:
        with exclusive_local_file_lock(data, ".test.lock"):
            first_entered.set()
            assert release_first.wait(timeout=2)

    def second() -> None:
        assert first_entered.wait(timeout=2)
        with exclusive_local_file_lock(data, ".test.lock"):
            second_entered.set()

    first_thread = Thread(target=first)
    second_thread = Thread(target=second)
    first_thread.start()
    second_thread.start()
    assert first_entered.wait(timeout=2)
    assert not second_entered.wait(timeout=0.1)
    release_first.set()
    first_thread.join(timeout=2)
    second_thread.join(timeout=2)
    assert second_entered.is_set()


def test_local_binding_lock_rejects_a_symlink(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / ".test.lock").symlink_to(tmp_path / "target")

    with (
        pytest.raises(AutomaticMemoryBindingError, match="MNEMO_MEMORY_LOCK_UNAVAILABLE"),
        exclusive_local_file_lock(data, ".test.lock"),
    ):
        pass


def test_obsidian_binding_requires_an_enabled_project_and_generates_a_non_path_identity(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    vault = tmp_path / "private vault Δ"
    (vault / ".obsidian").mkdir(parents=True)
    data = tmp_path / "data"
    store = LocalObsidianVaultBindingStore(data)

    with pytest.raises(AutomaticMemoryBindingError, match="MNEMO_OBSIDIAN_PROJECT_UNENABLED"):
        store.enable(project, vault)

    project_binding = LocalMemoryProjectBindingStore(data).enable(project)
    first = store.enable(project, vault)
    second = store.enable(project, vault)

    assert first == second
    assert first.scope == project_binding.scope
    assert first.relative_path_prefix.startswith("obsidian/")
    assert str(vault) not in first.relative_path_prefix
    config = (data / "obsidian-vault-bindings.json").read_text()
    assert oct((data / "obsidian-vault-bindings.json").stat().st_mode & 0o777) == "0o600"
    assert "owner_id" in config


def test_obsidian_binding_rejects_missing_marker_and_symlinked_vault_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    data = tmp_path / "data"
    LocalMemoryProjectBindingStore(data).enable(project)
    store = LocalObsidianVaultBindingStore(data)
    invalid = tmp_path / "not a vault"
    invalid.mkdir()
    with pytest.raises(AutomaticMemoryBindingError, match="MNEMO_OBSIDIAN_ROOT_INVALID"):
        store.enable(project, invalid)

    target = tmp_path / "target vault"
    (target / ".obsidian").mkdir(parents=True)
    linked = tmp_path / "linked vault"
    linked.symlink_to(target, target_is_directory=True)
    with pytest.raises(AutomaticMemoryBindingError, match="MNEMO_OBSIDIAN_ROOT_UNSAFE"):
        store.enable(project, linked)


def test_hook_requests_bounded_checkpoint_only_after_work_and_tracks_save(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    LocalMemoryProjectBindingStore(data).enable(project)
    hook = AutomaticMemoryHook(
        data,
        "codex",
        knowledge_refresher=lambda current: cli._refresh_project_knowledge(data, current),
    )

    started = hook.handle(
        {"hook_event_name": "SessionStart", "session_id": "s1", "cwd": str(project)}
    )
    context = started["hookSpecificOutput"]
    assert isinstance(context, dict)
    assert "get_context" in str(context)
    assert "source_query" in str(context)
    assert "source_changes" in str(context)
    assert "relative_path" in str(context)
    assert "maximum_transitions" in str(context)
    assert "recorded lessons" in str(context)
    assert "include_approved_events" in str(context)
    assert "approved decision" in str(context)
    assert "current_source_digest" in str(context)
    assert "Do not claim that you know prior changes" in str(context)
    assert str(project) not in str(context)

    assert (
        hook.handle(
            {
                "hook_event_name": "PostToolUse",
                "session_id": "s1",
                "cwd": str(project),
                "tool_name": "apply_patch",
            }
        )
        == {}
    )
    stop = hook.handle({"hook_event_name": "Stop", "session_id": "s1", "cwd": str(project)})
    assert stop["decision"] == "block"
    assert "save_checkpoint" in str(stop)
    assert "still-applicable lessons" in str(stop)
    assert "record_event" in str(stop)
    assert "full transcript" in str(stop)

    assert (
        hook.handle(
            {
                "hook_event_name": "PostToolUse",
                "session_id": "s1",
                "cwd": str(project),
                "tool_name": "mcp__mnemo-memory__save_checkpoint",
            }
        )
        == {}
    )
    assert hook.handle({"hook_event_name": "Stop", "session_id": "s1", "cwd": str(project)}) == {}
    state = (data / "automatic-memory-session-state.json").read_text()
    assert str(project) not in state
    assert "transcript" not in state.lower()


def test_enabled_project_hook_incrementally_syncs_markdown_without_emitting_note_text(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo Δ"
    project.mkdir()
    notes = project / "docs"
    notes.mkdir()
    note = notes / "reconciliation.md"
    note.write_text("# Reconciliation\nCompare Finance inputs at the business-date grain.\n")
    data = tmp_path / "mnemo data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    hook = AutomaticMemoryHook(
        data,
        "codex",
        knowledge_refresher=lambda current: cli._refresh_project_knowledge(data, current),
    )
    repository = SQLiteKnowledgeDocumentRepository(data / "mnemo.sqlite3", base_directory=data)

    started = hook.handle(
        {"hook_event_name": "SessionStart", "session_id": "knowledge-1", "cwd": str(project)}
    )
    active = repository.list_active_documents(binding.scope)
    assert len(active) == 1
    assert active[0].relative_path == "docs/reconciliation.md"
    assert "Finance inputs" not in str(started)

    note.write_text("# Reconciliation\nCompare Finance inputs after the approved grain change.\n")
    hook.handle(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "knowledge-1",
            "cwd": str(project),
            "tool_name": "Edit",
        }
    )
    hook.handle(
        {"hook_event_name": "UserPromptSubmit", "session_id": "knowledge-1", "cwd": str(project)}
    )
    active = repository.list_active_documents(binding.scope)
    assert active[0].revision_number == 2

    note.unlink()
    hook.handle(
        {"hook_event_name": "UserPromptSubmit", "session_id": "knowledge-1", "cwd": str(project)}
    )
    assert repository.list_active_documents(binding.scope) == ()


@pytest.mark.parametrize("operation", ("record_event", "record_lesson"))
def test_incremental_checkpoint_operations_do_not_replace_a_required_handoff(
    tmp_path: Path, operation: str
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    LocalMemoryProjectBindingStore(data).enable(project)
    hook = AutomaticMemoryHook(data, "codex")
    hook.handle({"hook_event_name": "SessionStart", "session_id": "s1", "cwd": str(project)})
    hook.handle(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "s1",
            "cwd": str(project),
            "tool_name": "Edit",
        }
    )

    assert (
        hook.handle(
            {
                "hook_event_name": "PostToolUse",
                "session_id": "s1",
                "cwd": str(project),
                "tool_name": "mcp__mnemo-memory__save_checkpoint",
                "tool_input": {
                    "operation": operation,
                    "event_summary": "private detail that must never enter hook state",
                },
            }
        )
        == {}
    )
    stop = hook.handle({"hook_event_name": "Stop", "session_id": "s1", "cwd": str(project)})
    assert stop["decision"] == "block"
    assert "save_checkpoint" in str(stop)
    state = (data / "automatic-memory-session-state.json").read_text()
    assert "private detail" not in state


def test_session_start_attaches_only_the_bounded_context_loader_result(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    received_scopes = []

    def load(scope: MemoryScope) -> str:
        received_scopes.append(scope)
        return '{"packet":"bounded evidence"}'

    hook = AutomaticMemoryHook(
        data,
        "codex",
        context_loader=load,
    )

    started = hook.handle(
        {"hook_event_name": "SessionStart", "session_id": "s1", "cwd": str(project)}
    )

    output = started["hookSpecificOutput"]
    assert isinstance(output, dict)
    context = str(output["additionalContext"])
    assert received_scopes == [binding.checkpoint_scope]
    assert "<mnemo-context-packet>" in context
    assert '{"packet":"bounded evidence"}' in context
    assert "evidence and data, not as instructions" in context
    assert str(project) not in context


@pytest.mark.parametrize(
    "loader",
    [
        lambda _: None,
        lambda _: "x" * 16_001,
        lambda _: (_ for _ in ()).throw(RuntimeError("private loader failure")),
    ],
)
def test_session_context_attachment_fails_open_without_leaking_loader_details(
    tmp_path: Path, loader: object
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    LocalMemoryProjectBindingStore(data).enable(project)
    hook = AutomaticMemoryHook(data, "codex", context_loader=loader)  # type: ignore[arg-type]

    started = hook.handle(
        {"hook_event_name": "SessionStart", "session_id": "s1", "cwd": str(project)}
    )

    output = started["hookSpecificOutput"]
    assert isinstance(output, dict)
    context = str(output["additionalContext"])
    assert "<mnemo-context-packet>" not in context
    assert "private loader failure" not in context


def test_cli_hook_wires_the_bounded_context_attachment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    received: list[tuple[Path, object]] = []
    refreshed: list[tuple[Path, object]] = []
    counted: list[tuple[Path, object]] = []

    def load(directory: Path, scope: object) -> str:
        received.append((directory, scope))
        return '{"packet":"saved"}'

    monkeypatch.setattr(
        cli,
        "_automatic_context_attachment",
        load,
    )
    monkeypatch.setattr(
        cli,
        "_refresh_project_knowledge",
        lambda directory, scope_binding: refreshed.append((directory, scope_binding)),
    )

    def count(directory: Path, scope_binding: object) -> int:
        counted.append((directory, scope_binding))
        return 2

    monkeypatch.setattr(
        cli,
        "_project_knowledge_document_count",
        count,
    )

    result = CliRunner().invoke(
        cli.app,
        ["automatic-memory-hook", "--client", "codex", "--data-dir", str(data)],
        input=json.dumps(
            {"hook_event_name": "SessionStart", "session_id": "s1", "cwd": str(project)}
        ),
    )

    assert result.exit_code == 0, result.output
    assert received == [(data.resolve(), binding.checkpoint_scope)]
    assert refreshed == [(data.resolve(), binding)]
    assert counted == [(data.resolve(), binding)]
    emitted = json.loads(result.output)
    additional_context = emitted["hookSpecificOutput"]["additionalContext"]
    assert '<mnemo-context-packet>\n{"packet":"saved"}' in additional_context
    assert "2 current scoped project knowledge document(s)" in additional_context
    assert "knowledge_query" in additional_context


def test_automatic_context_attachment_reads_the_real_bounded_durable_handoff(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    content = CheckpointContent(
        task_objective="Resume the reconciliation investigation",
        completed_work=("Recorded the current comparison grain.",),
        current_state="A bounded handoff is ready for the next session.",
        remaining_work=("Run the regression check.",),
        decisions=("Use the documented business-date grain.",),
        failures=("Do not compare stale source snapshots.",),
        blockers=(),
        relevant_files=("models/reconciliation.sql",),
        relevant_artifacts=(),
        verification_performed=("focused test passed",),
        token_estimate=120,
    )
    evidence = EvidenceReference(
        EvidenceId.new(),
        SourceId.new(),
        EvidenceSourceType.CHECKPOINT,
        SourceTrustClass.USER_AUTHORED,
        "fixture://automatic-memory/handoff",
        "sha256:" + "a" * 64,
        EvidenceLocation("fixture://automatic-memory/handoff"),
        datetime(2026, 8, 3, tzinfo=UTC),
        VerificationStatus.VERIFIED,
    )
    with build_checkpoint_runtime(LocalConfig.defaults(data)) as runtime:
        runtime.checkpoint_service.create(
            CreateCheckpoint(binding.checkpoint_scope, content, (evidence,))
        )

    attached = cli._automatic_context_attachment(data, binding.checkpoint_scope)

    assert attached is not None
    packet = json.loads(attached)
    assert packet["declared_total_tokens"] <= 1_200
    assert packet["active_task_checkpoint"]["content"] == json.dumps(
        content.to_dict(), sort_keys=True, separators=(",", ":")
    )
    assert packet["episodic_memories"] == []


def test_checkpoint_save_observes_the_bound_source_snapshot_without_affecting_checkpoint_success(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo with Ω"
    project.mkdir()
    (project / "service.py").write_text("def reconcile():\n    return True\n", encoding="utf-8")
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    evidence = EvidenceReference(
        EvidenceId.new(),
        SourceId.new(),
        EvidenceSourceType.CHECKPOINT,
        SourceTrustClass.USER_AUTHORED,
        "fixture://automatic-memory/observation",
        "sha256:" + "b" * 64,
        EvidenceLocation("fixture://automatic-memory/observation"),
        datetime(2026, 8, 4, tzinfo=UTC),
        VerificationStatus.VERIFIED,
    )
    content = CheckpointContent(
        task_objective="Resume from a co-observed source snapshot.",
        completed_work=("Created a durable handoff.",),
        current_state="The exact source projection is attached as evidence.",
        remaining_work=(),
        decisions=("Do not infer a cause from a snapshot association.",),
        failures=(),
        blockers=(),
        relevant_files=("service.py",),
        relevant_artifacts=(),
        verification_performed=("focused test passed",),
        token_estimate=80,
    )
    with build_checkpoint_runtime(LocalConfig.defaults(data)) as runtime:
        assert runtime.source_structure_repository is not None
        view = runtime.checkpoint_service.create(
            CreateCheckpoint(binding.checkpoint_scope, content, (evidence,))
        )
        observer = CheckpointSourceObserver(
            LocalMemoryProjectBindingStore(data),
            runtime.source_structure_repository,
            runtime.repository,
            lambda: datetime(2026, 8, 4, tzinfo=UTC),
        )
        assert observer.observe(view)
        observation = runtime.repository.get_checkpoint_source_observation(
            binding.checkpoint_scope,
            view.aggregate.checkpoint_id,
            view.revision.revision_id,
        )
        assert observation.revision_id == view.revision.revision_id

    attached = cli._automatic_context_attachment(data, binding.checkpoint_scope)

    assert attached is not None
    packet = json.loads(attached)
    observations = [
        item
        for item in packet["structural_items"]
        if item["item_id"].startswith("source-observation:")
    ]
    assert len(observations) == 1
    assert (
        "source_snapshot_observed_after_checkpoint_revision_persisted" in observations[0]["content"]
    )
    assert "return True" not in attached


def test_automatic_context_attachment_includes_the_latest_bounded_source_transition(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    source = project / "service.py"
    source.write_text("def reconcile():\n    return 'before'\n", encoding="utf-8")

    repository = SQLiteSourceStructureRepository(data / "mnemo.sqlite3", base_directory=data)
    repository.migrate()
    parser = SourceStructureParser()
    repository.store_and_activate(parser.parse(SourceStructureParseRequest(binding.scope, project)))
    source.write_text("def reconcile():\n    return 'after'\n", encoding="utf-8")
    repository.store_and_activate(parser.parse(SourceStructureParseRequest(binding.scope, project)))

    attached = cli._automatic_context_attachment(data, binding.checkpoint_scope)

    assert attached is not None
    packet = json.loads(attached)
    assert packet["active_task_checkpoint"] is None
    assert packet["declared_total_tokens"] <= 1_200
    change = next(
        item for item in packet["structural_items"] if item["item_id"].startswith("source-change:")
    )
    summary = json.loads(change["content"])
    assert summary["modified_files"] == ["service.py"]
    assert summary["currentness"] == "current"
    assert "return 'before'" not in attached
    assert "return 'after'" not in attached


def test_automatic_context_attachment_includes_a_bounded_source_overview_without_transition(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    (project / "service.py").write_text("def reconcile():\n    return True\n", encoding="utf-8")
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    repository = SQLiteSourceStructureRepository(data / "mnemo.sqlite3", base_directory=data)
    repository.migrate()
    repository.store_and_activate(
        SourceStructureParser().parse(SourceStructureParseRequest(binding.scope, project))
    )

    attached = cli._automatic_context_attachment(data, binding.checkpoint_scope)

    assert attached is not None
    packet = json.loads(attached)
    overview = next(
        item
        for item in packet["structural_items"]
        if item["item_id"].startswith("source-overview:")
    )
    summary = json.loads(overview["content"])
    assert summary["kind"] == "source_snapshot_overview"
    assert summary["file_count"] == 1
    assert summary["currentness"] == "current"
    assert any(item["item_id"].startswith("source-file:") for item in packet["structural_items"])
    assert packet["declared_total_tokens"] <= 1_200
    assert "return True" not in attached
    assert str(project) not in attached


def test_automatic_context_attaches_checkpoint_relevant_static_impact(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    (project / "core.py").write_text("def calculate():\n    return 1\n", encoding="utf-8")
    (project / "service.py").write_text(
        "import core\n\ndef serve():\n    return core.calculate()\n", encoding="utf-8"
    )
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    repository = SQLiteSourceStructureRepository(data / "mnemo.sqlite3", base_directory=data)
    repository.migrate()
    repository.store_and_activate(
        SourceStructureParser().parse(SourceStructureParseRequest(binding.scope, project))
    )
    evidence = EvidenceReference(
        EvidenceId.new(),
        SourceId.new(),
        EvidenceSourceType.CHECKPOINT,
        SourceTrustClass.USER_AUTHORED,
        "fixture://automatic-memory/checkpoint",
        "sha256:" + "b" * 64,
        EvidenceLocation("fixture://automatic-memory/checkpoint"),
        datetime(2026, 8, 4, tzinfo=UTC),
        VerificationStatus.VERIFIED,
    )
    with build_checkpoint_runtime(LocalConfig.defaults(data)) as runtime:
        runtime.checkpoint_service.create(
            CreateCheckpoint(
                binding.checkpoint_scope,
                CheckpointContent(
                    "Assess a changed calculation.",
                    (),
                    "Review its static dependents.",
                    (),
                    (),
                    (),
                    (),
                    ("core.py",),
                    (),
                    (),
                    30,
                ),
                (evidence,),
            )
        )

    attached = cli._automatic_context_attachment(data, binding.checkpoint_scope)

    assert attached is not None
    packet = json.loads(attached)
    impact = [
        json.loads(item["content"])
        for item in packet["structural_items"]
        if '"impact_direction":"dependents"' in item["content"]
    ]
    assert any(item["path"] == "core.py" for item in impact)
    assert any(item["path"] == "service.py" for item in impact)
    assert all(item["currentness"] == "current" for item in impact)
    assert packet["declared_total_tokens"] <= 1_200
    assert "return core.calculate" not in attached


@pytest.mark.parametrize("client", ["codex", "claude-code"])
def test_dirty_session_prompt_reminder_never_reads_or_persists_prompt_content(
    tmp_path: Path, client: str
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    LocalMemoryProjectBindingStore(data).enable(project)
    hook = AutomaticMemoryHook(data, client)  # type: ignore[arg-type]
    hook.handle({"hook_event_name": "SessionStart", "session_id": "s1", "cwd": str(project)})
    hook.handle(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "s1",
            "cwd": str(project),
            "tool_name": "Edit",
        }
    )

    result = hook.handle(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "s1",
            "cwd": str(project),
            "prompt": "private user question and secret-looking value",
        }
    )

    output = result["hookSpecificOutput"]
    assert isinstance(output, dict)
    assert output["hookEventName"] == "UserPromptSubmit"
    context = str(output["additionalContext"])
    assert "Mnemo observed a project mutation" in context
    assert "source_changes" in context
    assert "relative_path" in context
    assert "private user question" not in context


def test_dirty_prompt_boundary_refreshes_and_cues_exact_static_impact(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    core = project / "core.py"
    core.write_text("def calculate():\n    return 1\n", encoding="utf-8")
    (project / "service.py").write_text(
        "import core\n\ndef serve():\n    return core.calculate()\n", encoding="utf-8"
    )
    data = tmp_path / "data"
    LocalMemoryProjectBindingStore(data).enable(project)
    hook = AutomaticMemoryHook(data, "codex")
    hook.handle({"hook_event_name": "SessionStart", "session_id": "s1", "cwd": str(project)})
    core.write_text("def calculate():\n    return 2\n", encoding="utf-8")
    hook.handle(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "s1",
            "cwd": str(project),
            "tool_name": "Edit",
        }
    )

    result = hook.handle(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "s1",
            "cwd": str(project),
            "prompt": "private user question must not be retained",
        }
    )

    output = result["hookSpecificOutput"]
    assert isinstance(output, dict)
    instruction = str(output["additionalContext"])
    assert "Modified files: core.py." in instruction
    assert "static dependent candidates" in instruction
    assert "source snapshot " in instruction
    assert "service.py:service" in instruction
    assert "return 1" not in instruction
    assert "return 2" not in instruction
    assert "private user question must not be retained" not in instruction
    state = (data / "automatic-memory-session-state.json").read_text()
    assert "private user question" not in state

    hook.handle(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "s1",
            "cwd": str(project),
            "tool_name": "mcp__mnemo-memory__save_checkpoint",
        }
    )
    assert (
        hook.handle(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "s1",
                "cwd": str(project),
                "prompt": "another private user question",
            }
        )
        == {}
    )


def test_concurrent_lifecycle_events_keep_each_session_marker(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    LocalMemoryProjectBindingStore(data).enable(project)
    hook = AutomaticMemoryHook(data, "codex")
    start = Event()

    def mark_dirty(session_id: str) -> None:
        assert start.wait(timeout=2)
        assert (
            hook.handle(
                {
                    "hook_event_name": "PostToolUse",
                    "session_id": session_id,
                    "cwd": str(project),
                    "tool_name": "Edit",
                }
            )
            == {}
        )

    first = Thread(target=mark_dirty, args=("one",))
    second = Thread(target=mark_dirty, args=("two",))
    first.start()
    second.start()
    start.set()
    first.join(timeout=2)
    second.join(timeout=2)

    state = json.loads((data / "automatic-memory-session-state.json").read_text())
    assert state == {
        "one": {"dirty": True, "saved": False},
        "two": {"dirty": True, "saved": False},
    }


def test_session_start_refreshes_supported_static_source_structure(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    (project / "service.py").write_text("def run():\n    return 1\n")
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)

    AutomaticMemoryHook(data, "codex").handle(
        {"hook_event_name": "SessionStart", "session_id": "s1", "cwd": str(project)}
    )

    snapshot = SQLiteSourceStructureRepository(data / "mnemo.sqlite3").get_active_snapshot(
        binding.scope
    )
    assert snapshot is not None
    assert snapshot.symbol_count == 2


def test_session_start_indexes_typescript_without_reading_source_text(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    (project / "service.ts").write_text(
        "import { helper } from './tools';\nexport function run() { helper() }\n"
    )
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)

    AutomaticMemoryHook(data, "codex").handle(
        {"hook_event_name": "SessionStart", "session_id": "s1", "cwd": str(project)}
    )

    repository = SQLiteSourceStructureRepository(data / "mnemo.sqlite3")
    snapshot = repository.get_active_snapshot(binding.scope)
    assert snapshot is not None
    assert snapshot.file_count == 1
    assert {
        item.qualified_name for item in repository.iter_symbols(binding.scope, snapshot.snapshot_id)
    } == {
        "service",
        "service.run",
    }


def test_automatic_memory_persists_a_mixed_language_map_for_later_context(tmp_path: Path) -> None:
    project = tmp_path / "project Δ"
    project.mkdir()
    (project / "orders.ts").write_text(
        "import { capture } from './payments';\n"
        "export function processOrder() { capture(); client.send(); }\n"
    )
    (project / "payments.ts").write_text("export function capture() {}\n")
    (project / "Worker.java").write_text(
        "import tools.Helper; class Worker { void run() { Helper.go(); } }\n"
    )
    data = tmp_path / "mnemo data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)

    AutomaticMemoryHook(data, "codex").handle(
        {"hook_event_name": "SessionStart", "session_id": "s1", "cwd": str(project)}
    )

    request: dict[str, object] = {
        **binding.checkpoint_scope.to_dict(),
        "source_query": "processOrder",
    }
    with build_checkpoint_runtime(LocalConfig.defaults(data)) as runtime:
        assert runtime.source_structure_repository is not None
        packet = DurableMcpContextPort(
            runtime.checkpoint_service,
            UnifiedContextService(
                runtime.checkpoint_service, None, runtime.source_structure_repository
            ),
        ).get_context(request)
    with build_checkpoint_runtime(LocalConfig.defaults(data)) as runtime:
        assert runtime.source_structure_repository is not None
        restarted = DurableMcpContextPort(
            runtime.checkpoint_service,
            UnifiedContextService(
                runtime.checkpoint_service, None, runtime.source_structure_repository
            ),
        ).get_context(request)

    first_items = packet["structural_items"]
    assert isinstance(first_items, list)
    restarted_items = restarted["structural_items"]
    assert isinstance(restarted_items, list)
    assert [item["content"] for item in first_items] == [
        item["content"] for item in restarted_items
    ]
    content = "\n".join(str(item["content"]) for item in first_items)
    assert '"symbol":"orders.processOrder"' in content
    assert '"resolved_target":{"path":"payments.ts","symbol":"payments"}' in content
    assert str(project) not in content


def test_stop_after_a_mutation_refreshes_the_static_structure_before_checkpointing(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    source_file = project / "service.py"
    source_file.write_text("def initial():\n    return 1\n")
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    hook = AutomaticMemoryHook(data, "codex")
    hook.handle({"hook_event_name": "SessionStart", "session_id": "s1", "cwd": str(project)})
    initial = SQLiteSourceStructureRepository(data / "mnemo.sqlite3").get_active_snapshot(
        binding.scope
    )
    assert initial is not None

    source_file.write_text("def current():\n    return 2\n")
    hook.handle(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "s1",
            "cwd": str(project),
            "tool_name": "Edit",
        }
    )
    result = hook.handle({"hook_event_name": "Stop", "session_id": "s1", "cwd": str(project)})
    refreshed = SQLiteSourceStructureRepository(data / "mnemo.sqlite3").get_active_snapshot(
        binding.scope
    )

    assert result["decision"] == "block"
    assert refreshed is not None
    assert refreshed.snapshot_id != initial.snapshot_id
    reason = str(result["reason"])
    assert "Mnemo observed a structural change" in reason
    assert "service.py:service.current" in reason
    assert "service.py:service.initial" in reason
    assert "return 2" not in reason
    assert str(project) not in reason


def test_checkpoint_save_refreshes_changed_structure_without_waiting_for_stop_or_restart(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    source_file = project / "service.py"
    source_file.write_text("def initial():\n    return 1\n")
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    hook = AutomaticMemoryHook(data, "codex")
    hook.handle({"hook_event_name": "SessionStart", "session_id": "s1", "cwd": str(project)})
    repository = SQLiteSourceStructureRepository(data / "mnemo.sqlite3")
    initial = repository.get_active_snapshot(binding.scope)
    assert initial is not None

    source_file.write_text("def current():\n    return 2\n")
    hook.handle(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "s1",
            "cwd": str(project),
            "tool_name": "Edit",
        }
    )
    unchanged = repository.get_active_snapshot(binding.scope)
    assert unchanged is not None
    assert unchanged.snapshot_id == initial.snapshot_id

    result = hook.handle(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "s1",
            "cwd": str(project),
            "tool_name": "mcp__mnemo-memory__save_checkpoint",
        }
    )
    refreshed = repository.get_active_snapshot(binding.scope)

    assert result == {}
    assert refreshed is not None
    assert refreshed.snapshot_id != initial.snapshot_id
    assert {
        item.qualified_name
        for item in repository.iter_symbols(binding.scope, refreshed.snapshot_id)
    } == {
        "service",
        "service.current",
    }
    state = (data / "automatic-memory-session-state.json").read_text()
    assert "return 2" not in state
    assert str(project) not in state


def test_session_start_reports_a_bounded_prior_structural_change_without_source_text(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    source_file = project / "service.py"
    source_file.write_text("def initial():\n    return 'private initial body'\n")
    data = tmp_path / "data"
    LocalMemoryProjectBindingStore(data).enable(project)
    hook = AutomaticMemoryHook(data, "codex")
    hook.handle({"hook_event_name": "SessionStart", "session_id": "first", "cwd": str(project)})

    source_file.write_text("def current():\n    return 'private changed body'\n")
    result = hook.handle(
        {"hook_event_name": "SessionStart", "session_id": "second", "cwd": str(project)}
    )

    context = result["hookSpecificOutput"]
    assert isinstance(context, dict)
    instruction = str(context["additionalContext"])
    assert "Mnemo observed a structural change" in instruction
    assert "service.py:service.current" in instruction
    assert "service.py:service.initial" in instruction
    assert "private initial body" not in instruction
    assert "private changed body" not in instruction
    assert str(project) not in instruction

    later = hook.handle(
        {"hook_event_name": "SessionStart", "session_id": "third", "cwd": str(project)}
    )
    later_output = later["hookSpecificOutput"]
    assert isinstance(later_output, dict)
    later_instruction = str(later_output["additionalContext"])
    assert "most recent saved transition" in later_instruction
    assert "service.py:service.current" in later_instruction
    assert "service.py:service.initial" in later_instruction
    assert "private initial body" not in later_instruction
    assert "private changed body" not in later_instruction


def test_session_start_reports_a_body_only_file_transition_without_source_text(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    source_file = project / "pricing.py"
    source_file.write_text("def price():\n    return 'private first implementation'\n")
    data = tmp_path / "data"
    LocalMemoryProjectBindingStore(data).enable(project)
    hook = AutomaticMemoryHook(data, "codex")
    hook.handle({"hook_event_name": "SessionStart", "session_id": "first", "cwd": str(project)})

    source_file.write_text("def price():\n    return 'private corrected implementation'\n")
    result = hook.handle(
        {"hook_event_name": "SessionStart", "session_id": "second", "cwd": str(project)}
    )

    output = result["hookSpecificOutput"]
    assert isinstance(output, dict)
    instruction = str(output["additionalContext"])
    assert "1 modified" in instruction
    assert "Modified files: pricing.py." in instruction
    assert "private first implementation" not in instruction
    assert "private corrected implementation" not in instruction
    assert str(project) not in instruction


def test_session_start_attaches_bounded_static_dependents_for_an_exact_changed_file(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    core = project / "core.py"
    core.write_text("def calculate():\n    return 1\n", encoding="utf-8")
    (project / "service.py").write_text(
        "import core\n\ndef serve():\n    return core.calculate()\n", encoding="utf-8"
    )
    data = tmp_path / "data"
    LocalMemoryProjectBindingStore(data).enable(project)
    hook = AutomaticMemoryHook(data, "codex")
    hook.handle({"hook_event_name": "SessionStart", "session_id": "first", "cwd": str(project)})

    core.write_text("def calculate():\n    return 2\n", encoding="utf-8")
    result = hook.handle(
        {"hook_event_name": "SessionStart", "session_id": "second", "cwd": str(project)}
    )

    output = result["hookSpecificOutput"]
    assert isinstance(output, dict)
    instruction = str(output["additionalContext"])
    assert "static dependent candidates" in instruction
    assert "source snapshot " in instruction
    assert "core.py" in instruction
    assert "service.py:service" in instruction
    assert "return 1" not in instruction
    assert "return 2" not in instruction


def test_session_start_attaches_authoritative_dbt_downstream_cue_for_changed_model(
    tmp_path: Path,
) -> None:
    project = tmp_path / "dbt repo"
    model = project / "models" / "marts" / "fct_orders.sql"
    model.parent.mkdir(parents=True)
    model.write_text("select 1\n", encoding="utf-8")
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    checkpoint_repository = SQLiteCheckpointRepository(data / "mnemo.sqlite3")
    checkpoint_repository.migrate()
    ingested = DbtManifestApplicationService(checkpoint_repository, DbtManifestParser()).ingest(
        IngestManifest(
            binding.scope,
            DBT_FIXTURE.read_bytes(),
            "tests/fixtures/dbt/manifest-v12.json",
            datetime(2026, 8, 4, tzinfo=UTC),
        )
    )
    hook = AutomaticMemoryHook(data, "codex")
    hook.handle({"hook_event_name": "SessionStart", "session_id": "first", "cwd": str(project)})

    model.write_text("select 2\n", encoding="utf-8")
    result = hook.handle(
        {"hook_event_name": "SessionStart", "session_id": "second", "cwd": str(project)}
    )

    output = result["hookSpecificOutput"]
    assert isinstance(output, dict)
    instruction = str(output["additionalContext"])
    assert "authoritative dbt-manifest downstream facts" in instruction
    assert "models/marts/fct_orders.sql" in instruction
    assert "model.mnemo_analytics.mart_customer_value" in instruction
    assert str(ingested.snapshot.snapshot_id) in instruction
    assert "currentness unknown" in instruction
    assert "select 1" not in instruction
    assert "select 2" not in instruction


def test_session_start_uses_new_path_of_digest_proven_renamed_dbt_model(
    tmp_path: Path,
) -> None:
    project = tmp_path / "dbt repo"
    legacy = project / "models" / "marts" / "legacy_orders.sql"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("select 1\n", encoding="utf-8")
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    checkpoint_repository = SQLiteCheckpointRepository(data / "mnemo.sqlite3")
    checkpoint_repository.migrate()
    ingested = DbtManifestApplicationService(checkpoint_repository, DbtManifestParser()).ingest(
        IngestManifest(
            binding.scope,
            DBT_FIXTURE.read_bytes(),
            "tests/fixtures/dbt/manifest-v12.json",
            datetime(2026, 8, 4, tzinfo=UTC),
        )
    )
    hook = AutomaticMemoryHook(data, "codex")
    hook.handle({"hook_event_name": "SessionStart", "session_id": "first", "cwd": str(project)})

    legacy.rename(project / "models" / "marts" / "fct_orders.sql")
    result = hook.handle(
        {"hook_event_name": "SessionStart", "session_id": "second", "cwd": str(project)}
    )

    output = result["hookSpecificOutput"]
    assert isinstance(output, dict)
    instruction = str(output["additionalContext"])
    assert (
        "Renamed files: models/marts/legacy_orders.sql → models/marts/fct_orders.sql."
        in instruction
    )
    assert "authoritative dbt-manifest downstream facts" in instruction
    assert "models/marts/fct_orders.sql" in instruction
    assert str(ingested.snapshot.snapshot_id) in instruction
    assert "select 1" not in instruction


def test_unenabled_project_is_fail_open_and_discloses_no_path(tmp_path: Path) -> None:
    project = tmp_path / "private repo"
    project.mkdir()
    result = AutomaticMemoryHook(tmp_path / "data", "claude-code").handle(
        {"hook_event_name": "Stop", "session_id": "s1", "cwd": str(project)}
    )
    assert result == {"systemMessage": "MNEMO_MEMORY_PROJECT_UNENABLED"}
    assert str(project) not in json.dumps(result)


def test_client_hook_configuration_is_reversible_and_preserves_other_entries(
    tmp_path: Path,
) -> None:
    launcher = tmp_path / "Mnemo Δ" / "mnemo-memory"
    launcher.parent.mkdir()
    launcher.touch()
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    hooks_path = codex_home / "hooks.json"
    hooks_path.write_text(
        json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "other"}]}]}})
    )

    data_directory = tmp_path / "mnemo data"
    assert enable_client_hooks("codex", launcher, codex_home, data_directory) is True
    assert enable_client_hooks("codex", launcher, codex_home, data_directory) is False
    enabled = json.loads(hooks_path.read_text())
    assert any(
        "automatic-memory-hook --client codex" in item["command"]
        for group in enabled["hooks"]["Stop"]
        for item in group["hooks"]
    )
    assert "--data-dir" in next(
        item["command"]
        for group in enabled["hooks"]["Stop"]
        for item in group["hooks"]
        if "automatic-memory-hook --client codex" in item["command"]
    )
    assert disable_client_hooks("codex", launcher, codex_home, data_directory) is True
    disabled = json.loads(hooks_path.read_text())
    assert disabled["hooks"]["Stop"] == [{"hooks": [{"type": "command", "command": "other"}]}]


def test_client_configuration_registers_prompt_boundary_without_prompt_matcher(
    tmp_path: Path,
) -> None:
    launcher = tmp_path / "mnemo-memory"
    launcher.touch()
    data = tmp_path / "mnemo data"
    for client, home in (("codex", tmp_path / "codex"), ("claude-code", tmp_path / "claude")):
        assert enable_client_hooks(client, launcher, home, data) is True  # type: ignore[arg-type]
        config_path = (
            home / "hooks.json" if client == "codex" else home / ".claude" / "settings.json"
        )
        value = json.loads(config_path.read_text())
        prompt_groups = value["hooks"]["UserPromptSubmit"]
        assert len(prompt_groups) == 1
        assert "matcher" not in prompt_groups[0]


def test_claude_hook_configuration_uses_only_its_settings_file(tmp_path: Path) -> None:
    launcher = tmp_path / "mnemo-memory"
    launcher.touch()
    home = tmp_path / "Claude Home"
    data_directory = tmp_path / "mnemo data"
    assert enable_client_hooks("claude-code", launcher, home, data_directory) is True
    config = home / ".claude" / "settings.json"
    assert config.is_file()
    assert not (home / ".claude.json").exists()
    assert disable_client_hooks("claude-code", launcher, home, data_directory) is True


def test_cli_memory_enable_creates_binding_without_exposing_scope_ids(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    data = tmp_path / "data"
    launcher = tmp_path / "bin" / "mnemo-memory"
    launcher.parent.mkdir()
    launcher.touch()
    monkeypatch.setattr("mnemo_memory.apps.cli.main.shutil.which", lambda _: str(launcher))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))

    result = CliRunner().invoke(
        cli.app,
        [
            "memory",
            "enable",
            "codex",
            "--project-dir",
            str(project),
            "--data-dir",
            str(data),
            "--yes",
        ],
    )
    assert result.exit_code == 0
    assert "automatic_memory" in result.output
    assert "owner_id" not in result.output
    assert (tmp_path / "codex-home" / "hooks.json").is_file()
    assert (data / "mnemo.sqlite3").is_file()


def test_cli_obsidian_vault_enable_syncs_and_disable_tombstones_vault_documents(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    vault = tmp_path / "vault Δ"
    (vault / ".obsidian").mkdir(parents=True)
    (vault / "decision.md").write_text("# Decision\nUse the cited current source.")
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    runner = CliRunner()

    enabled = runner.invoke(
        cli.app,
        [
            "memory",
            "vault",
            "enable",
            str(vault),
            "--project-dir",
            str(project),
            "--data-dir",
            str(data),
        ],
    )
    assert enabled.exit_code == 0, enabled.output
    assert str(vault) not in enabled.output
    repository = SQLiteKnowledgeDocumentRepository(data / "mnemo.sqlite3", base_directory=data)
    active = repository.list_active_documents(binding.scope)
    assert len(active) == 1
    assert active[0].relative_path.startswith("obsidian/")

    disabled = runner.invoke(
        cli.app,
        [
            "memory",
            "vault",
            "disable",
            "--project-dir",
            str(project),
            "--data-dir",
            str(data),
        ],
    )
    assert disabled.exit_code == 0, disabled.output
    assert repository.list_active_documents(binding.scope) == ()


def test_automatic_enable_reuses_an_existing_dbt_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "dbt project"
    project.mkdir()
    (project / "dbt_project.yml").write_text("name: synthetic\n")
    data = tmp_path / "data"
    dbt_store = LocalDbtProjectBindingStore(data)
    expected = LocalMemoryProjectBindingStore(data).personal_profile().scope()
    dbt_store.set(DbtProjectBinding(project.resolve(), expected))
    launcher = tmp_path / "bin" / "mnemo-memory"
    launcher.parent.mkdir()
    launcher.touch()
    monkeypatch.setattr("mnemo_memory.apps.cli.main.shutil.which", lambda _: str(launcher))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))

    result = CliRunner().invoke(
        cli.app,
        [
            "memory",
            "enable",
            "codex",
            "--project-dir",
            str(project),
            "--data-dir",
            str(data),
            "--yes",
        ],
    )

    assert result.exit_code == 0, result.output
    automatic = LocalMemoryProjectBindingStore(data).get(project)
    assert automatic is not None
    assert automatic.scope == expected
