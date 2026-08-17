"""Opt-in automatic task-memory hooks never capture raw client content."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Event, Thread
from typing import cast
from uuid import UUID

import pytest
from typer.testing import CliRunner

from mnemo_memory.apps.cli import main as cli
from mnemo_memory.connectors.automatic_memory.client_config import (
    ClientName,
    disable_client_hooks,
    enable_client_hooks,
)
from mnemo_memory.connectors.automatic_memory.hook import (
    AutomaticMemoryHook,
    PromptContextAttachment,
)
from mnemo_memory.connectors.automatic_memory.learned_routes import LocalLearnedRouteStore
from mnemo_memory.connectors.automatic_memory.source_observation import (
    CheckpointSourceObserver,
    refresh_registered_project_source,
)
from mnemo_memory.connectors.dbt.manifest import DbtManifestParser
from mnemo_memory.connectors.dbt.project_binding import (
    DbtProjectBinding,
    LocalDbtProjectBindingStore,
)
from mnemo_memory.connectors.local_embeddings import (
    LocalPotionRouterSettingsStore,
    PotionRouterSettings,
)
from mnemo_memory.packages.application.automatic_memory import (
    AutomaticMemoryBindingError,
    LocalMemoryProjectBindingStore,
    LocalObsidianVaultBindingStore,
    MemoryProjectBinding,
    exclusive_local_file_lock,
)
from mnemo_memory.packages.application.bootstrap import build_checkpoint_runtime
from mnemo_memory.packages.application.checkpoints import (
    CheckpointView,
    CompleteCheckpoint,
    CreateCheckpoint,
    ReviseCheckpoint,
)
from mnemo_memory.packages.application.config import LocalConfig
from mnemo_memory.packages.application.context_routing import (
    AUTOMATIC_CONTEXT_LAZY_PULL_HINT,
    CompactMemoryRoute,
)
from mnemo_memory.packages.application.dbt import DbtManifestApplicationService, IngestManifest
from mnemo_memory.packages.application.mcp_durable import DurableMcpContextPort
from mnemo_memory.packages.application.settings import PersonalSettings, PersonalSettingsStore
from mnemo_memory.packages.application.unified_context import UnifiedContextService
from mnemo_memory.packages.domain import (
    CheckpointContent,
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    KnowledgeDocumentRevision,
    KnowledgeDocumentRevisionId,
    MemoryScope,
    SourceId,
    SourceTrustClass,
    VerificationStatus,
)
from mnemo_memory.packages.knowledge import KnowledgeDocumentParser, KnowledgeDocumentParseRequest
from mnemo_memory.packages.project_index import SourceStructureParser, SourceStructureParseRequest
from mnemo_memory.packages.storage import (
    SQLiteCheckpointRepository,
    SQLiteKnowledgeDocumentRepository,
    SQLiteSourceStructureRepository,
)
from mnemo_memory.packages.telemetry import (
    AutomaticRouteDiagnosticsMode,
    AutomaticRouteDiagnosticsSettings,
    LocalAutomaticRouteDiagnosticsSettingsStore,
    LocalAutomaticRouteTelemetryStore,
)

ROOT = Path(__file__).parents[2]
DBT_FIXTURE = ROOT / "tests" / "fixtures" / "dbt" / "manifest-v12.json"


def _create_test_handoff(
    data: Path, binding: MemoryProjectBinding, *, objective: str = "Preserve the focused task."
) -> CheckpointView:
    content = CheckpointContent(
        task_objective=objective,
        completed_work=("Recorded bounded progress.",),
        current_state="The handoff is durable.",
        remaining_work=("Continue the focused task.",),
        decisions=("Do not expand scope.",),
        failures=(),
        blockers=(),
        relevant_files=("service.py",),
        relevant_artifacts=(),
        verification_performed=("Focused evidence recorded.",),
        token_estimate=70,
    )
    evidence = EvidenceReference(
        EvidenceId.new(),
        SourceId.new(),
        EvidenceSourceType.CHECKPOINT,
        SourceTrustClass.USER_AUTHORED,
        "fixture://automatic-memory/persisted",
        "sha256:" + "d" * 64,
        EvidenceLocation("fixture://automatic-memory/persisted"),
        datetime(2026, 8, 5, tzinfo=UTC),
        VerificationStatus.VERIFIED,
    )
    with build_checkpoint_runtime(LocalConfig.defaults(data)) as runtime:
        return runtime.checkpoint_service.create(
            CreateCheckpoint(binding.checkpoint_scope, content, (evidence,))
        )


def _run_hook_process(data: Path, event: dict[str, object]) -> dict[str, object]:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "mnemo_memory.apps.cli.main",
            "automatic-memory-hook",
            "--client",
            "codex",
            "--data-dir",
            str(data),
        ],
        input=json.dumps(event),
        text=True,
        capture_output=True,
        check=True,
        timeout=15,
    )
    value = json.loads(completed.stdout)
    assert isinstance(value, dict)
    return value


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
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    swept: list[MemoryProjectBinding] = []
    hook = AutomaticMemoryHook(
        data,
        "codex",
        knowledge_refresher=lambda current: cli._refresh_project_knowledge(data, current),
        retention_sweeper=swept.append,
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
    assert swept == [binding]
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
    assert "project-relative evidence_files" in str(stop)
    assert "record_event" in str(stop)
    assert "Do not include a transcript" in str(stop)
    assert "about 200 tokens" in str(stop)
    assert "Omit scope IDs, token_estimate, empty lists, and null values" in str(stop)

    failed_save = hook.handle(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "s1",
            "cwd": str(project),
            "tool_name": "mcp__mnemo-memory__save_checkpoint",
        }
    )
    assert failed_save == {"systemMessage": "MNEMO_MEMORY_CHECKPOINT_NOT_PERSISTED"}
    assert (
        hook.handle({"hook_event_name": "Stop", "session_id": "s1", "cwd": str(project)})[
            "decision"
        ]
        == "block"
    )

    _create_test_handoff(data, binding)
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


def test_session_start_retention_failure_never_blocks_the_client(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    LocalMemoryProjectBindingStore(data).enable(project)

    def unavailable(_: MemoryProjectBinding) -> None:
        raise RuntimeError("sensitive storage detail")

    result = AutomaticMemoryHook(
        data,
        "codex",
        retention_sweeper=unavailable,
    ).handle({"hook_event_name": "SessionStart", "session_id": "s1", "cwd": str(project)})

    assert "hookSpecificOutput" in result
    assert "sensitive storage detail" not in json.dumps(result)


def test_session_start_uses_valid_attachment_without_redundant_context_fetch(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    LocalMemoryProjectBindingStore(data).enable(project)
    attachment = "MNEMO_CONTEXT_V1 client=codex\nMNEMO_TRUST_BOUNDARY {}\nMNEMO_CONTEXT_END"

    result = AutomaticMemoryHook(
        data,
        "codex",
        context_loader=lambda _: attachment,
        knowledge_status_loader=lambda _: 1,
    ).handle({"hook_event_name": "SessionStart", "session_id": "s1", "cwd": str(project)})

    output = result["hookSpecificOutput"]
    assert isinstance(output, dict)
    context = str(output["additionalContext"])
    assert attachment in context
    assert "already retrieved for this session" in context
    assert "get_context" not in context
    assert "knowledge_query" in context


def test_installed_hook_expires_a_checkpoint_due_under_personal_settings(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    handoff = _create_test_handoff(data, binding)
    old_write = (datetime.now(UTC) - timedelta(days=181)).isoformat()
    with sqlite3.connect(data / "mnemo.sqlite3") as connection:
        connection.execute(
            "UPDATE checkpoint_aggregates SET created_at = ?, updated_at = ? "
            "WHERE checkpoint_id = ?",
            (
                old_write,
                old_write,
                str(handoff.aggregate.checkpoint_id),
            ),
        )

    result = _run_hook_process(
        data,
        {"hook_event_name": "SessionStart", "session_id": "fresh", "cwd": str(project)},
    )

    assert "hookSpecificOutput" in result
    repository = SQLiteCheckpointRepository(data / "mnemo.sqlite3", base_directory=data)
    assert (
        repository.get_aggregate(
            binding.checkpoint_scope, handoff.aggregate.checkpoint_id
        ).lifecycle_status.value
        == "expired"
    )


def test_invalid_retention_settings_do_not_block_the_installed_hook(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    LocalMemoryProjectBindingStore(data).enable(project)
    (data / "settings.json").write_text("{}", encoding="utf-8")

    result = _run_hook_process(
        data,
        {"hook_event_name": "SessionStart", "session_id": "fresh", "cwd": str(project)},
    )

    assert "hookSpecificOutput" in result
    assert "MNEMO_MEMORY_HOOK_UNAVAILABLE" not in json.dumps(result)


def test_repository_verification_failure_never_clears_pending_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    LocalMemoryProjectBindingStore(data).enable(project)
    monkeypatch.setattr(
        AutomaticMemoryHook,
        "_current_checkpoint_marker",
        lambda self, scope: "unavailable",
    )
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
    result = hook.handle(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "s1",
            "cwd": str(project),
            "tool_name": "mcp__mnemo-memory__save_checkpoint",
        }
    )

    assert result == {"systemMessage": "MNEMO_MEMORY_CHECKPOINT_VERIFICATION_UNAVAILABLE"}
    assert (
        hook.handle({"hook_event_name": "Stop", "session_id": "s1", "cwd": str(project)})[
            "decision"
        ]
        == "block"
    )


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


def test_unsaved_project_handoff_marker_survives_restart_and_full_checkpoint_clears_it(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo Δ with spaces"
    project.mkdir()
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    hook = AutomaticMemoryHook(data, "codex")

    hook.handle(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "first",
            "cwd": str(project),
            "tool_name": "Edit",
            "tool_input": {"private": "must never become lifecycle memory"},
        }
    )

    marker = (data / "automatic-memory-handoff-state.json").read_text(encoding="utf-8")
    assert str(project) not in marker
    assert "private" not in marker
    assert binding.scope.project_id is not None
    assert len(json.loads(marker)) == 1

    restarted = AutomaticMemoryHook(data, "codex").handle(
        {"hook_event_name": "SessionStart", "session_id": "second", "cwd": str(project)}
    )
    context = str(restarted["hookSpecificOutput"])
    assert "without a complete checkpoint" in context
    assert "no transcript or inferred explanation" in context

    # An incremental evidence record is useful but does not replace a complete handoff.
    hook.handle(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "second",
            "cwd": str(project),
            "tool_name": "mcp__mnemo-memory__save_checkpoint",
            "tool_input": {"operation": "record_event"},
        }
    )
    still_pending = AutomaticMemoryHook(data, "codex").handle(
        {"hook_event_name": "SessionStart", "session_id": "third", "cwd": str(project)}
    )
    assert "without a complete checkpoint" in str(still_pending["hookSpecificOutput"])

    _create_test_handoff(data, binding)
    hook.handle(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "third",
            "cwd": str(project),
            "tool_name": "mcp__mnemo-memory__save_checkpoint",
            "tool_input": {"operation": "revise"},
        }
    )
    cleared = AutomaticMemoryHook(data, "codex").handle(
        {"hook_event_name": "SessionStart", "session_id": "fourth", "cwd": str(project)}
    )
    assert "without a complete checkpoint" not in str(cleared["hookSpecificOutput"])
    assert json.loads((data / "automatic-memory-handoff-state.json").read_text()) == {}


def test_unsaved_handoff_marker_is_isolated_to_its_enabled_project(tmp_path: Path) -> None:
    first_project = tmp_path / "first"
    second_project = tmp_path / "second"
    first_project.mkdir()
    second_project.mkdir()
    data = tmp_path / "data"
    LocalMemoryProjectBindingStore(data).enable(first_project)
    LocalMemoryProjectBindingStore(data).enable(second_project)
    hook = AutomaticMemoryHook(data, "claude-code")

    hook.handle(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "first",
            "cwd": str(first_project),
            "tool_name": "Write",
        }
    )
    first = hook.handle(
        {
            "hook_event_name": "SessionStart",
            "session_id": "first-restart",
            "cwd": str(first_project),
        }
    )
    second = hook.handle(
        {
            "hook_event_name": "SessionStart",
            "session_id": "second-restart",
            "cwd": str(second_project),
        }
    )
    assert "without a complete checkpoint" in str(first["hookSpecificOutput"])
    assert "without a complete checkpoint" not in str(second["hookSpecificOutput"])


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
    assert '{"packet":"bounded evidence"}' in context
    assert "client-rendered view" in context
    assert str(project) not in context


def test_prompt_boundary_uses_prompt_transiently_and_attaches_bounded_context(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    received: list[tuple[MemoryScope, str]] = []

    def load(scope: MemoryScope, prompt: str) -> str:
        received.append((scope, prompt))
        return '{"packet":"relevant bounded memory"}'

    hook = AutomaticMemoryHook(data, "codex", prompt_context_loader=load)
    hook.handle({"hook_event_name": "SessionStart", "session_id": "s1", "cwd": str(project)})
    prompt = "How should the finance reconciliation variance be handled?"
    result = hook.handle(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "s1",
            "cwd": str(project),
            "prompt": prompt,
        }
    )

    assert received == [(binding.checkpoint_scope, prompt)]
    output = result["hookSpecificOutput"]
    assert isinstance(output, dict)
    assert output["hookEventName"] == "UserPromptSubmit"
    assert "relevant bounded memory" in str(output["additionalContext"])
    state = (data / "automatic-memory-session-state.json").read_text(encoding="utf-8")
    assert prompt not in state


def test_prompt_boundary_passes_only_a_bounded_head_tail_view_of_a_long_prompt(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    LocalMemoryProjectBindingStore(data).enable(project)
    received: list[str] = []

    def load(_scope: MemoryScope, prompt: str) -> None:
        received.append(prompt)

    hook = AutomaticMemoryHook(
        data,
        "codex",
        prompt_context_loader=load,
    )
    middle_marker = "private-middle-marker-f840"
    prompt = (
        "Use the prior handoff. "
        + ("pasted-prefix-noise " * 40)
        + middle_marker
        + (" pasted-suffix-noise" * 40)
        + "Trace every caller of this adapter."
    )

    result = hook.handle(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "s1",
            "cwd": str(project),
            "prompt": prompt,
        }
    )

    assert result == {}
    assert len(received) == 1
    assert len(received[0]) <= 512
    assert received[0].startswith("Use the prior handoff.")
    assert received[0].endswith("Trace every caller of this adapter.")
    assert middle_marker not in received[0]
    state = (data / "automatic-memory-session-state.json").read_text(encoding="utf-8")
    assert middle_marker not in state


def test_automatic_prompt_secret_view_never_reaches_the_embedding_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    secret = "api_key=abcdefghijklmnop1234"

    def reject_provider(_cache: Path) -> None:
        raise AssertionError("secret-bearing prompt must not construct an embedding provider")

    monkeypatch.setattr(cli, "FastEmbedLocalProvider", reject_provider)
    monkeypatch.setattr(cli, "PotionLocalMemoryRouter", reject_provider)
    LocalAutomaticRouteDiagnosticsSettingsStore(data).save(
        AutomaticRouteDiagnosticsSettings(AutomaticRouteDiagnosticsMode.TRACE, 7)
    )
    LocalPotionRouterSettingsStore(data).save(PotionRouterSettings(True))
    attached = cli._automatic_prompt_context_for_hook(
        data,
        binding.checkpoint_scope,
        f"Consult the project notes about reconciliation.\n{secret}",
        "codex",
    )

    assert attached.context is None
    telemetry = (data / "automatic-route-telemetry.json").read_text(encoding="utf-8")
    assert secret not in telemetry


def test_prompt_route_correlation_observes_only_tool_name_not_payload(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    LocalMemoryProjectBindingStore(data).enable(project)
    event_id = UUID("11111111-1111-4111-8111-111111111111")
    private_marker = "private-tool-payload-7f30"
    observed: list[tuple[UUID, str]] = []
    hook = AutomaticMemoryHook(
        data,
        "codex",
        prompt_context_loader=lambda _scope, prompt: (
            PromptContextAttachment(None, event_id) if "parser" in prompt else None
        ),
        tool_telemetry_observer=lambda route_event_id, tool_name: observed.append(
            (route_event_id, tool_name)
        ),
    )
    hook.handle({"hook_event_name": "SessionStart", "session_id": "s1", "cwd": str(project)})
    hook.handle(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "s1",
            "cwd": str(project),
            "prompt": "Where is the parser defined?",
        }
    )
    hook.handle(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "s1",
            "cwd": str(project),
            "tool_name": "Bash",
            "tool_input": {"command": private_marker},
            "tool_response": private_marker,
        }
    )
    hook.handle(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "s1",
            "cwd": str(project),
            "prompt": "Thanks",
        }
    )
    hook.handle(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "s1",
            "cwd": str(project),
            "tool_name": "Bash",
            "tool_input": {"command": private_marker},
            "tool_response": private_marker,
        }
    )

    assert observed == [(event_id, "Bash")]
    state = (data / "automatic-memory-session-state.json").read_text(encoding="utf-8")
    assert str(event_id) not in state
    assert private_marker not in state


def test_prompt_delivery_observer_receives_only_final_hook_output_counts(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    LocalMemoryProjectBindingStore(data).enable(project)
    event_id = UUID("11111111-1111-4111-8111-111111111111")
    delivered: list[tuple[UUID, int, int, bool]] = []
    hook = AutomaticMemoryHook(
        data,
        "codex",
        prompt_context_loader=lambda _scope, _prompt: PromptContextAttachment(
            "small context", event_id
        ),
        delivery_telemetry_observer=lambda *metrics: delivered.append(metrics),
    )

    result = hook.handle(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "s1",
            "cwd": str(project),
            "prompt": "Which context applies?",
        }
    )

    hook_output = cast(dict[str, object], result["hookSpecificOutput"])
    output = hook_output["additionalContext"]
    assert isinstance(output, str)
    assert delivered == [(event_id, len(output), len(output.encode("utf-8")), False)]


def test_exact_same_session_prompt_context_is_delivered_once_and_counted_as_duplicate(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    LocalMemoryProjectBindingStore(data).enable(project)
    first_event = UUID("11111111-1111-4111-8111-111111111111")
    second_event = UUID("22222222-2222-4222-8222-222222222222")
    event_ids = iter((first_event, second_event))
    base_key = "sha256:" + "a" * 64
    delivered: list[tuple[UUID, int, int, bool]] = []
    hook = AutomaticMemoryHook(
        data,
        "codex",
        prompt_context_loader=lambda _scope, _prompt: PromptContextAttachment(
            "private bounded context", next(event_ids), (base_key,)
        ),
        delivery_telemetry_observer=lambda *metrics: delivered.append(metrics),
    )
    hook.handle({"hook_event_name": "SessionStart", "session_id": "s1", "cwd": str(project)})

    first = hook.handle(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "s1",
            "cwd": str(project),
            "prompt": "Use the saved decision.",
        }
    )
    duplicate = hook.handle(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "s1",
            "cwd": str(project),
            "prompt": "Use the saved decision again.",
        }
    )

    output = cast(dict[str, object], first["hookSpecificOutput"])["additionalContext"]
    assert isinstance(output, str)
    assert duplicate == {}
    assert delivered == [
        (first_event, len(output), len(output.encode("utf-8")), False),
        (second_event, 0, 0, True),
    ]
    encoded_state = (data / "automatic-memory-session-state.json").read_text(encoding="utf-8")
    state = json.loads(encoded_state)
    delivery_keys = state["s1"]["delivered_context_keys"]
    assert len(delivery_keys) == 1
    assert delivery_keys[0].startswith("sha256:")
    assert base_key not in encoded_state
    assert "private bounded context" not in encoded_state
    assert "Use the saved decision" not in encoded_state


def test_changed_delivery_identity_is_not_suppressed_in_the_same_session(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    LocalMemoryProjectBindingStore(data).enable(project)
    first_key = "sha256:" + "a" * 64
    changed_key = "sha256:" + "b" * 64
    hook = AutomaticMemoryHook(
        data,
        "codex",
        prompt_context_loader=lambda _scope, prompt: PromptContextAttachment(
            "changed context" if "changed" in prompt else "initial context",
            delivery_keys=(changed_key if "changed" in prompt else first_key,),
        ),
    )
    hook.handle({"hook_event_name": "SessionStart", "session_id": "s1", "cwd": str(project)})

    first = hook.handle(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "s1",
            "cwd": str(project),
            "prompt": "Use the prior decision.",
        }
    )
    duplicate = hook.handle(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "s1",
            "cwd": str(project),
            "prompt": "Use the prior decision.",
        }
    )
    changed = hook.handle(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "s1",
            "cwd": str(project),
            "prompt": "Use the changed decision.",
        }
    )

    assert first
    assert duplicate == {}
    changed_output = cast(dict[str, object], changed["hookSpecificOutput"])
    assert "changed context" in str(changed_output["additionalContext"])


@pytest.mark.parametrize("client", ["codex", "claude-code"])
@pytest.mark.parametrize("dirty", [False, True])
def test_precompact_clears_only_delivery_keys_and_allows_safe_redelivery(
    tmp_path: Path, client: str, dirty: bool
) -> None:
    project = tmp_path / f"repo-{client}-{dirty}"
    project.mkdir()
    data = tmp_path / f"data-{client}-{dirty}"
    LocalMemoryProjectBindingStore(data).enable(project)
    key = "sha256:" + "a" * 64
    hook = AutomaticMemoryHook(
        data,
        cast(ClientName, client),
        prompt_context_loader=lambda _scope, _prompt: PromptContextAttachment(
            "bounded context", delivery_keys=(key,)
        ),
    )
    hook.handle({"hook_event_name": "SessionStart", "session_id": "s1", "cwd": str(project)})
    first = hook.handle(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "s1",
            "cwd": str(project),
            "prompt": "Use the previous handoff.",
        }
    )
    if dirty:
        hook.handle(
            {
                "hook_event_name": "PostToolUse",
                "session_id": "s1",
                "cwd": str(project),
                "tool_name": "Edit",
            }
        )

    hook.handle({"hook_event_name": "PreCompact", "session_id": "s1", "cwd": str(project)})
    state = json.loads((data / "automatic-memory-session-state.json").read_text(encoding="utf-8"))
    assert "delivered_context_keys" not in state["s1"]
    assert state["s1"]["dirty"] is dirty
    second = hook.handle(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "s1",
            "cwd": str(project),
            "prompt": "Use the previous handoff after compaction.",
        }
    )

    assert first
    assert second
    assert "bounded context" in str(second)


def test_delivery_identity_isolated_by_scope_client_and_session(tmp_path: Path) -> None:
    first_project = tmp_path / "first"
    second_project = tmp_path / "second"
    first_project.mkdir()
    second_project.mkdir()
    data = tmp_path / "data"
    LocalMemoryProjectBindingStore(data).enable(first_project)
    LocalMemoryProjectBindingStore(data).enable(second_project)
    key = "sha256:" + "a" * 64

    def hook(client: str) -> AutomaticMemoryHook:
        return AutomaticMemoryHook(
            data,
            cast(ClientName, client),
            prompt_context_loader=lambda _scope, _prompt: PromptContextAttachment(
                "bounded context", delivery_keys=(key,)
            ),
        )

    codex = hook("codex")
    first = codex.handle(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "shared",
            "cwd": str(first_project),
            "prompt": "Use saved memory.",
        }
    )
    other_scope = codex.handle(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "shared",
            "cwd": str(second_project),
            "prompt": "Use saved memory.",
        }
    )
    other_client = hook("claude-code").handle(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "shared",
            "cwd": str(first_project),
            "prompt": "Use saved memory.",
        }
    )
    other_session = codex.handle(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "other",
            "cwd": str(first_project),
            "prompt": "Use saved memory.",
        }
    )

    assert first and other_scope and other_client and other_session


def test_corrupt_delivery_key_state_fails_open_to_redelivery(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    LocalMemoryProjectBindingStore(data).enable(project)
    data.mkdir(exist_ok=True)
    state_path = data / "automatic-memory-session-state.json"
    state_path.write_text(
        json.dumps(
            {
                "s1": {
                    "dirty": False,
                    "saved": False,
                    "checkpoint_marker": None,
                    "delivered_context_keys": ["private-corrupt-key-body"],
                }
            }
        ),
        encoding="utf-8",
    )
    hook = AutomaticMemoryHook(
        data,
        "codex",
        prompt_context_loader=lambda _scope, _prompt: PromptContextAttachment(
            "bounded context", delivery_keys=("sha256:" + "a" * 64,)
        ),
    )

    result = hook.handle(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "s1",
            "cwd": str(project),
            "prompt": "Use saved memory.",
        }
    )

    assert result
    assert "private-corrupt-key-body" not in state_path.read_text(encoding="utf-8")


def test_rejected_prompt_attachment_records_zero_delivery(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    LocalMemoryProjectBindingStore(data).enable(project)
    event_id = UUID("11111111-1111-4111-8111-111111111111")
    delivered: list[tuple[UUID, int, int, bool]] = []
    hook = AutomaticMemoryHook(
        data,
        "codex",
        prompt_context_loader=lambda _scope, _prompt: PromptContextAttachment(
            "x" * 16_001, event_id
        ),
        delivery_telemetry_observer=lambda *metrics: delivered.append(metrics),
    )

    result = hook.handle(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "s1",
            "cwd": str(project),
            "prompt": "Which context applies?",
        }
    )

    assert result == {}
    assert delivered == [(event_id, 0, 0, False)]


def test_automatic_prompt_context_selects_scoped_markdown_with_material_token_savings(
    tmp_path: Path,
) -> None:
    first_project = tmp_path / "first"
    second_project = tmp_path / "second"
    first_project.mkdir()
    second_project.mkdir()
    data = tmp_path / "data"
    first = LocalMemoryProjectBindingStore(data).enable(first_project)
    second = LocalMemoryProjectBindingStore(data).enable(second_project)
    relevant = (
        "# Finance reconciliation\n"
        "Investigate reconciliation variance at the approved business-date grain. " * 12
    )
    (first_project / "reconciliation.md").write_text(relevant, encoding="utf-8")
    unrelated_documents: list[str] = []
    for index in range(8):
        content = f"# Unrelated {index}\n" + ("Deployment rotation maintenance notes. " * 80)
        unrelated_documents.append(content)
        (first_project / f"unrelated-{index}.md").write_text(content, encoding="utf-8")
    private_other_scope = "# Finance reconciliation\nprivate other-project variance decision"
    (second_project / "private.md").write_text(private_other_scope, encoding="utf-8")
    cli._refresh_project_knowledge(data, first)
    cli._refresh_project_knowledge(data, second)

    attached = cli._automatic_prompt_context_attachment(
        data, first.checkpoint_scope, "finance reconciliation variance"
    )
    long_attached = cli._automatic_prompt_context_attachment(
        data,
        first.checkpoint_scope,
        ("pasted-log-noise " * 100)
        + "\nConsult the project notes for the finance reconciliation variance.",
    )

    assert attached is not None
    assert long_attached is not None
    packet = json.loads(attached)
    long_packet = json.loads(long_attached)
    assert packet["declared_total_tokens"] <= 1_300
    assert long_packet["declared_total_tokens"] <= 1_300
    rendered = json.dumps(packet, sort_keys=True)
    assert "approved business-date grain" in json.dumps(long_packet, sort_keys=True)
    assert "approved business-date grain" in rendered
    assert "private other-project variance decision" not in rendered
    raw_markdown_tokens = (len(relevant) + sum(len(item) for item in unrelated_documents) + 3) // 4
    assert packet["declared_total_tokens"] * 2 < raw_markdown_tokens


def test_automatic_prompt_context_attaches_bounded_authoritative_dbt_models(
    tmp_path: Path,
) -> None:
    project = tmp_path / "dbt repo"
    project.mkdir()
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    repository = SQLiteCheckpointRepository(data / "mnemo.sqlite3")
    repository.migrate()
    ingested = DbtManifestApplicationService(repository, DbtManifestParser()).ingest(
        IngestManifest(
            binding.scope,
            DBT_FIXTURE.read_bytes(),
            "tests/fixtures/dbt/manifest-v12.json",
            datetime(2026, 8, 4, tzinfo=UTC),
        )
    )

    attached = cli._automatic_prompt_context_attachment(
        data, binding.checkpoint_scope, "can you see all the dbt models here?"
    )

    assert attached is not None
    packet = json.loads(attached)
    assert packet["declared_total_tokens"] <= 1_300
    structural = [json.loads(item["content"]) for item in packet["structural_items"]]
    inventories = [item for item in structural if item.get("query_kind") == "selector_inventory"]
    assert inventories == [
        {
            "currentness": "unknown",
            "filters": {"resource_type": "model"},
            "matched_node_count": 7,
            "node_records_included": False,
            "project_name": "mnemo_analytics",
            "query_kind": "selector_inventory",
            "snapshot_id": str(ingested.snapshot.snapshot_id),
        }
    ]
    assert all(item.get("query_kind") != "selector" for item in structural)


def test_automatic_prompt_context_attaches_one_compact_source_architecture_graph(
    tmp_path: Path,
) -> None:
    project = tmp_path / "python repo"
    (project / "src" / "api").mkdir(parents=True)
    (project / "src" / "domain").mkdir(parents=True)
    (project / "src" / "api" / "routes.ts").write_text(
        "import { Order } from '../domain/orders';\n"
        "export function createOrder() { return new Order(); }\n",
        encoding="utf-8",
    )
    (project / "src" / "domain" / "orders.ts").write_text(
        "export class Order {}\n",
        encoding="utf-8",
    )
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    source = SQLiteSourceStructureRepository(data / "mnemo.sqlite3", base_directory=data)
    source.migrate()
    stored = source.store_and_activate(
        SourceStructureParser().parse(SourceStructureParseRequest(binding.scope, project))
    )

    attached = cli._automatic_prompt_context_attachment(
        data,
        binding.checkpoint_scope,
        "Can you see the architecture of this repository and its main components?",
    )

    assert attached is not None
    packet = json.loads(attached)
    assert packet["declared_total_tokens"] <= 1_300
    assert packet["knowledge_items"] == []
    assert len(packet["structural_items"]) == 1
    overview = json.loads(packet["structural_items"][0]["content"])
    assert overview["kind"] == "source_architecture_overview"
    assert overview["snapshot_id"] == str(stored.snapshot.snapshot_id)
    assert overview["components"]
    assert overview["relationships"]
    assert len(attached) < 12_000


def test_automatic_prompt_context_attaches_only_bounded_checkpoint_recap(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    created = _create_test_handoff(data, binding, objective="Finish compact dbt inventory.")
    with build_checkpoint_runtime(LocalConfig.defaults(data)) as runtime:
        runtime.checkpoint_service.complete(
            CompleteCheckpoint(
                binding.checkpoint_scope,
                created.aggregate.checkpoint_id,
                created.revision.revision_id,
                replace(
                    created.revision.content,
                    current_state="The inventory fix is complete.",
                    remaining_work=(),
                ),
                created.revision.evidence_references,
            )
        )

    prompt = "mnemo recap what I worked on for the past 3days private-marker-2f09"
    attached = cli._automatic_prompt_context_attachment(
        data,
        binding.checkpoint_scope,
        prompt,
    )

    assert attached is not None
    packet = json.loads(attached)
    assert packet["declared_total_tokens"] <= 1_300
    assert packet["active_task_checkpoint"] is None
    assert packet["knowledge_items"] == packet["structural_items"] == []
    assert len(packet["episodic_memories"]) == 1
    recap = json.loads(packet["episodic_memories"][0]["content"])
    assert recap["query_kind"] == "checkpoint_recap"
    assert recap["recap_days"] == 3
    assert recap["task_objective"] == "Finish compact dbt inventory."
    assert packet["episodic_memories"][0]["evidence_references"]

    delivery = cli._automatic_prompt_context_for_hook(
        data, binding.checkpoint_scope, prompt, "codex"
    )

    assert delivery.context is not None
    assert (len(delivery.context) + 3) // 4 <= 1_300
    assert '"delivery_mode":"automatic_compact"' in delivery.context
    telemetry = (data / "automatic-route-telemetry.json").read_text(encoding="utf-8")
    assert "private-marker-2f09" not in telemetry
    summary = (
        LocalAutomaticRouteTelemetryStore(data)
        .summary(cli._automatic_route_scope(binding.checkpoint_scope))
        .to_dict()
    )
    routes = cast(dict[str, dict[str, int]], summary["routes"])
    assert routes["prior_memory"]["maximum_attachment_tokens"] == 1_300
    assert routes["prior_memory"]["estimated_total_tokens"] <= 1_300


def test_automatic_skill_discovery_is_lazy_bounded_and_content_free(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    private_body = "private brainstorming workflow body"
    prompt = "Help me design a complex feature with unclear requirements."
    (project / "brainstorm.md").write_text(
        "---\nmnemo_kind: skill\nmnemo_name: brainstorming\nmnemo_version: 1.0.0\n"
        "mnemo_tags: design, requirements\nmnemo_clients: codex\n"
        "mnemo_trust: checked_in\n"
        "mnemo_when: Use when designing a complex feature with unclear requirements\n---\n"
        f"# Brainstorming\n{private_body}\n",
        encoding="utf-8",
    )
    cli._refresh_project_knowledge(data, binding)

    attached = cli._automatic_prompt_context_for_hook(
        data, binding.checkpoint_scope, prompt, "codex"
    )

    assert attached.telemetry_event_id is not None
    assert attached.context is not None
    assert attached.context.startswith("MNEMO_SKILL_DISCOVERY_V1 ")
    assert '"name":"brainstorming"' in attached.context
    assert '"estimated_body_tokens":' in attached.context
    assert private_body not in attached.context
    assert (len(attached.context) + 3) // 4 <= 256
    telemetry = (data / "automatic-route-telemetry.json").read_text(encoding="utf-8")
    assert prompt not in telemetry
    assert private_body not in telemetry
    summary = (
        LocalAutomaticRouteTelemetryStore(data)
        .summary(cli._automatic_route_scope(binding.checkpoint_scope))
        .to_dict()
    )
    routes = cast(dict[str, dict[str, int]], summary["routes"])
    totals = cast(dict[str, int], summary["totals"])
    assert routes["skill_discovery"]["events"] == 1
    assert totals["rendered_estimated_tokens"] > 0


def test_local_mnemo_operation_emits_bounded_local_first_guidance_without_prompt(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    prompt = "What Mnemo are you using right? private-marker-5a91"

    attached = cli._automatic_prompt_context_for_hook(
        data, binding.checkpoint_scope, prompt, "codex"
    )

    assert attached.context is not None
    assert attached.context.startswith("MNEMO_LOCAL_DIAGNOSTICS_V1 ")
    assert "mnemo --version" in attached.context
    assert "mnemo status" in attached.context
    assert "mnemo recap" in attached.context
    assert "configured hook command" in attached.context
    assert "OpenAI documentation skills or web search" in attached.context
    assert "AGENTS.md" in attached.context
    assert "Never edit the repository automatically" in attached.context
    assert prompt not in attached.context
    assert (len(attached.context) + 3) // 4 <= 256
    assert attached.telemetry_event_id is not None
    telemetry = (data / "automatic-route-telemetry.json").read_text(encoding="utf-8")
    assert prompt not in telemetry
    summary = (
        LocalAutomaticRouteTelemetryStore(data)
        .summary(cli._automatic_route_scope(binding.checkpoint_scope))
        .to_dict()
    )
    routes = cast(dict[str, dict[str, int]], summary["routes"])
    assert routes["local_diagnostics"]["hits"] == 1


def test_exact_lookup_records_zero_attachment_and_unknown_direct_tool_cost(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)

    attached = cli._automatic_prompt_context_for_hook(
        data, binding.checkpoint_scope, "Where is AutomaticMemoryHook defined?", "codex"
    )
    assert attached.context is None
    assert attached.telemetry_event_id is not None
    cli._record_automatic_route_tool(data, attached.telemetry_event_id, "Bash")

    result = CliRunner().invoke(
        cli.app,
        [
            "memory",
            "routes",
            "--project-dir",
            str(project),
            "--data-dir",
            str(data),
        ],
    )
    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    assert summary["routes"]["direct_lookup"] == {
        "duplicate_renders": 0,
        "estimated_total_tokens": 0,
        "events": 1,
        "fallbacks": 0,
        "hits": 0,
        "maximum_attachment_tokens": 0,
        "misses": 0,
        "tool_calls": 1,
    }
    assert summary["totals"]["rendered_estimated_tokens"] == 0
    assert summary["totals"]["tool_calls"] == 1
    assert summary["totals"]["unmeasured_tool_calls"] == 1


def test_trace_mode_records_shadow_axes_without_loading_potion_and_off_stops_new_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    LocalLearnedRouteStore(data).learn(binding.scope, "blast radius", CompactMemoryRoute.STRUCTURE)
    settings = LocalAutomaticRouteDiagnosticsSettingsStore(data)
    settings.save(AutomaticRouteDiagnosticsSettings(AutomaticRouteDiagnosticsMode.TRACE, 7))
    monkeypatch.setattr(
        cli,
        "PotionLocalMemoryRouter",
        lambda *_args, **_kwargs: pytest.fail("automatic trace must not load Potion"),
    )

    prompt = "Use the previous session decision and check the blast radius."
    traced = cli._automatic_prompt_context_for_hook(data, binding.checkpoint_scope, prompt, "codex")
    scope = cli._automatic_route_scope(binding.checkpoint_scope)
    events = LocalAutomaticRouteTelemetryStore(data).events(scope)

    assert traced.telemetry_event_id is not None
    assert len(events) == 1
    assert events[0].route == "prior_memory"
    assert events[0].shadow_structural_need == "yes"
    assert events[0].shadow_long_term_need == "yes"
    assert events[0].shadow_reason == "learned_phrase"
    assert events[0].shadow_action == "push_both"
    assert events[0].shadow_estimated_tokens == 1_300
    assert events[0].shadow_duration_ms >= 0
    assert events[0].semantic_invoked is False
    assert events[0].semantic_latency_ms == 0
    assert events[0].shadow_structural_tokens + events[0].shadow_long_term_tokens == 1_300
    assert prompt not in LocalAutomaticRouteTelemetryStore(data).path.read_text(encoding="utf-8")

    settings.save(AutomaticRouteDiagnosticsSettings(AutomaticRouteDiagnosticsMode.OFF, 7))
    disabled = cli._automatic_prompt_context_for_hook(
        data, binding.checkpoint_scope, "Where is AutomaticMemoryHook defined?", "codex"
    )
    assert disabled.telemetry_event_id is None
    assert len(LocalAutomaticRouteTelemetryStore(data).events(scope)) == 1


def test_compact_router_skips_memory_without_retaining_its_prompt(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    marker = "private-router-marker-29a82e"
    prompt = f"Start a new implementation from this specification. {marker}"

    attached = cli._automatic_prompt_context_for_hook(
        data, binding.checkpoint_scope, prompt, "codex"
    )

    assert attached.context is None
    assert attached.telemetry_event_id is not None
    telemetry = (data / "automatic-route-telemetry.json").read_text(encoding="utf-8")
    assert marker not in telemetry
    assert '"reason":"router_no_memory"' in telemetry
    summary = (
        LocalAutomaticRouteTelemetryStore(data)
        .summary(cli._automatic_route_scope(binding.checkpoint_scope))
        .to_dict()
    )
    routes = cast(dict[str, dict[str, int]], summary["routes"])
    assert routes["none"]["events"] == 1
    assert routes["none"]["estimated_total_tokens"] == 0


def test_structural_miss_records_a_direct_lookup_fallback_without_prompt(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    prompt = "Show the architecture of this repository and its main components."

    attached = cli._automatic_prompt_context_for_hook(
        data, binding.checkpoint_scope, prompt, "codex"
    )

    assert attached.context is None
    before_tool = (
        LocalAutomaticRouteTelemetryStore(data)
        .summary(cli._automatic_route_scope(binding.checkpoint_scope))
        .to_dict()
    )
    before_routes = cast(dict[str, dict[str, int]], before_tool["routes"])
    assert before_routes["structure"]["fallbacks"] == 0
    assert attached.telemetry_event_id is not None

    cli._record_automatic_route_tool(data, attached.telemetry_event_id, "Bash")
    after_tool = (
        LocalAutomaticRouteTelemetryStore(data)
        .summary(cli._automatic_route_scope(binding.checkpoint_scope))
        .to_dict()
    )
    routes = cast(dict[str, dict[str, int]], after_tool["routes"])
    assert routes["structure"]["misses"] == 1
    assert routes["structure"]["fallbacks"] == 1
    assert prompt not in (data / "automatic-route-telemetry.json").read_text(encoding="utf-8")


def test_lazy_pull_correlates_get_context_as_a_closed_tool_category(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    LocalAutomaticRouteDiagnosticsSettingsStore(data).save(
        AutomaticRouteDiagnosticsSettings(AutomaticRouteDiagnosticsMode.TRACE, 7)
    )

    attached = cli._automatic_prompt_context_for_hook(
        data, binding.checkpoint_scope, "finance reconciliation variance", "codex"
    )
    assert attached.telemetry_event_id is not None
    cli._record_automatic_route_tool(data, attached.telemetry_event_id, "mnemo-memory.get_context")

    event = LocalAutomaticRouteTelemetryStore(data).events(
        cli._automatic_route_scope(binding.checkpoint_scope)
    )[0]
    assert event.shadow_action == "lazy_pull"
    assert dict(event.tool_calls) == {"context_recall": 1}
    assert "finance" not in LocalAutomaticRouteTelemetryStore(data).path.read_text(encoding="utf-8")


def test_experimental_live_gate_suppresses_no_and_lazy_pull_without_loading_slices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    PersonalSettingsStore(data).save(PersonalSettings(experimental_semantic_memory_enabled=True))

    def reject_slice(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("NO and UNKNOWN must not load a context slice")

    monkeypatch.setattr(cli, "_automatic_prompt_context_result", reject_slice)
    no_prompt = "This is the output; what is your conclusion? private-no-marker-21d4"
    unknown_prompt = "finance reconciliation variance private-unknown-marker-06c7"

    no_memory = cli._automatic_prompt_context_for_hook(
        data, binding.checkpoint_scope, no_prompt, "codex"
    )
    lazy_pull = cli._automatic_prompt_context_for_hook(
        data, binding.checkpoint_scope, unknown_prompt, "codex"
    )

    assert no_memory.context is None
    assert no_memory.delivery_keys == ()
    assert lazy_pull.context == AUTOMATIC_CONTEXT_LAZY_PULL_HINT
    assert len(lazy_pull.delivery_keys) == 1
    assert lazy_pull.delivery_keys[0].startswith("sha256:")
    events = LocalAutomaticRouteTelemetryStore(data).events(
        cli._automatic_route_scope(binding.checkpoint_scope)
    )
    assert [event.live_gate_applied for event in events] == [True, True]
    by_action = {event.shadow_action: event for event in events}
    assert by_action["none"].injected_context_tokens == 0
    lazy_event = by_action["lazy_pull"]
    assert lazy_event.injected_context_tokens == (len(AUTOMATIC_CONTEXT_LAZY_PULL_HINT) + 3) // 4
    token_account = cast(dict[str, object], cli._route_event_view(lazy_event)["token_account"])
    assert token_account == {
        "classification": "deterministically_measured",
        "injected_context_tokens": (len(AUTOMATIC_CONTEXT_LAZY_PULL_HINT) + 3) // 4,
        "mnemo_model_input_tokens": 0,
        "mnemo_model_output_tokens": 0,
        "break_even_reuse": None,
        "break_even_status": "requires_authorized_actual_agent_model_token_delta",
    }
    encoded = LocalAutomaticRouteTelemetryStore(data).path.read_text(encoding="utf-8")
    assert no_prompt not in encoded and unknown_prompt not in encoded


def test_experimental_live_gate_pushes_one_bounded_selected_slice(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    _create_test_handoff(data, binding, objective="Honor the remembered decision.")
    PersonalSettingsStore(data).save(PersonalSettings(experimental_semantic_memory_enabled=True))

    attached = cli._automatic_prompt_context_for_hook(
        data,
        binding.checkpoint_scope,
        "Use the decision from our previous session.",
        "codex",
    )

    assert attached.context is not None
    assert len(attached.delivery_keys) == 1
    assert attached.delivery_keys[0].startswith("sha256:")
    assert attached.context.startswith("MNEMO_CONTEXT_V1")
    assert (len(attached.context) + 3) // 4 <= 700
    event = LocalAutomaticRouteTelemetryStore(data).events(
        cli._automatic_route_scope(binding.checkpoint_scope)
    )[0]
    assert event.live_gate_applied is True
    assert event.shadow_action == "push_long_term"
    assert event.injected_context_tokens == (len(attached.context) + 3) // 4


def test_experimental_cli_hook_suppresses_exact_redelivery_and_resets_on_session_start(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    _create_test_handoff(data, binding, objective="Honor the remembered decision.")
    PersonalSettingsStore(data).save(PersonalSettings(experimental_semantic_memory_enabled=True))
    LocalAutomaticRouteDiagnosticsSettingsStore(data).save(
        AutomaticRouteDiagnosticsSettings(AutomaticRouteDiagnosticsMode.TRACE, 7)
    )
    prompt_event: dict[str, object] = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": "same-session",
        "cwd": str(project),
        "prompt": "Use the decision from our previous session.",
    }
    _run_hook_process(
        data,
        {
            "hook_event_name": "SessionStart",
            "session_id": "same-session",
            "cwd": str(project),
        },
    )

    first = _run_hook_process(data, prompt_event)
    duplicate = _run_hook_process(data, prompt_event)
    _run_hook_process(
        data,
        {
            "hook_event_name": "SessionStart",
            "session_id": "same-session",
            "source": "compact",
            "cwd": str(project),
        },
    )
    after_reset = _run_hook_process(data, prompt_event)

    assert "MNEMO_CONTEXT_V1" in str(first)
    assert duplicate == {}
    assert "MNEMO_CONTEXT_V1" in str(after_reset)
    events = LocalAutomaticRouteTelemetryStore(data).events(
        cli._automatic_route_scope(binding.checkpoint_scope)
    )
    assert len(events) == 3
    assert [event.duplicate_render for event in events] == [False, True, False]
    assert events[0].rendered_estimated_tokens > 0
    assert events[1].rendered_characters == 0
    assert events[1].rendered_bytes == 0
    assert events[1].rendered_estimated_tokens == 0


def test_stable_flag_off_prompt_context_never_activates_delivery_deduplication(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    _create_test_handoff(data, binding, objective="Honor the stable handoff.")

    first = cli._automatic_prompt_context_for_hook(
        data,
        binding.checkpoint_scope,
        "Use the decision from our previous session.",
        "codex",
    )
    second = cli._automatic_prompt_context_for_hook(
        data,
        binding.checkpoint_scope,
        "Use the decision from our previous session.",
        "codex",
    )

    assert first.context is not None
    assert second.context is not None
    assert first.delivery_keys == second.delivery_keys == ()


def test_bounded_delivery_key_eviction_fails_toward_later_redelivery(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    LocalMemoryProjectBindingStore(data).enable(project)

    def load(_scope: MemoryScope, prompt: str) -> PromptContextAttachment:
        index = int(prompt.rsplit(" ", 1)[-1])
        return PromptContextAttachment(
            f"bounded context {index}", delivery_keys=("sha256:" + f"{index:064x}",)
        )

    hook = AutomaticMemoryHook(data, "codex", prompt_context_loader=load)
    hook.handle({"hook_event_name": "SessionStart", "session_id": "s1", "cwd": str(project)})
    for index in range(40):
        assert hook.handle(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "s1",
                "cwd": str(project),
                "prompt": f"Use memory {index}",
            }
        )

    state = json.loads((data / "automatic-memory-session-state.json").read_text(encoding="utf-8"))
    assert len(state["s1"]["delivered_context_keys"]) == 32
    assert hook.handle(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "s1",
            "cwd": str(project),
            "prompt": "Use memory 0",
        }
    )


def test_delivery_state_write_failure_and_concurrent_stale_reads_overdeliver(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    LocalMemoryProjectBindingStore(data).enable(project)
    target = tmp_path / "unsafe-state-target"
    state_path = data / "automatic-memory-session-state.json"
    state_path.symlink_to(target)
    key = "sha256:" + "a" * 64
    failing_hook = AutomaticMemoryHook(
        data,
        "codex",
        prompt_context_loader=lambda _scope, _prompt: PromptContextAttachment(
            "bounded context", delivery_keys=(key,)
        ),
    )
    event = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": "write-failure",
        "cwd": str(project),
        "prompt": "Use saved memory.",
    }

    assert failing_hook.handle(event)
    assert failing_hook.handle(event)
    state_path.unlink()

    barrier = Barrier(2)

    def load(_scope: MemoryScope, _prompt: str) -> PromptContextAttachment:
        barrier.wait(timeout=2)
        return PromptContextAttachment("bounded context", delivery_keys=(key,))

    concurrent_hook = AutomaticMemoryHook(data, "codex", prompt_context_loader=load)
    outputs: list[dict[str, object]] = []

    def deliver() -> None:
        outputs.append(
            concurrent_hook.handle(
                {
                    **event,
                    "session_id": "concurrent",
                }
            )
        )

    first = Thread(target=deliver)
    second = Thread(target=deliver)
    first.start()
    second.start()
    first.join(timeout=3)
    second.join(timeout=3)

    assert len(outputs) == 2
    assert all(output for output in outputs)


def test_fresh_hook_process_accepts_only_a_persisted_handoff_and_attaches_it(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo with spaces Ω"
    project.mkdir()
    data = tmp_path / "data with spaces Ω"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    _run_hook_process(
        data, {"hook_event_name": "SessionStart", "session_id": "first", "cwd": str(project)}
    )
    _run_hook_process(
        data,
        {
            "hook_event_name": "PostToolUse",
            "session_id": "first",
            "cwd": str(project),
            "tool_name": "Edit",
        },
    )
    rejected = _run_hook_process(
        data,
        {
            "hook_event_name": "PostToolUse",
            "session_id": "first",
            "cwd": str(project),
            "tool_name": "mcp__mnemo-memory__save_checkpoint",
        },
    )
    assert rejected == {"systemMessage": "MNEMO_MEMORY_CHECKPOINT_NOT_PERSISTED"}

    _create_test_handoff(data, binding, objective="Remember the deadline-critical handoff.")
    accepted = _run_hook_process(
        data,
        {
            "hook_event_name": "PostToolUse",
            "session_id": "first",
            "cwd": str(project),
            "tool_name": "mcp__mnemo-memory__save_checkpoint",
            "tool_input": {"operation": "create"},
        },
    )
    assert accepted == {}
    resumed = _run_hook_process(
        data, {"hook_event_name": "SessionStart", "session_id": "second", "cwd": str(project)}
    )
    output = resumed["hookSpecificOutput"]
    assert isinstance(output, dict)
    context = str(output["additionalContext"])
    assert "Remember the deadline-critical handoff" in context
    assert "already retrieved for this session" in context
    assert "get_context" not in context
    assert "without a complete checkpoint" not in context


def test_codex_compaction_defers_handoff_to_schema_compatible_session_start(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    evidence = EvidenceReference(
        EvidenceId.new(),
        SourceId.new(),
        EvidenceSourceType.CHECKPOINT,
        SourceTrustClass.USER_AUTHORED,
        "fixture://automatic-memory/compact",
        "sha256:" + "c" * 64,
        EvidenceLocation("fixture://automatic-memory/compact"),
        datetime(2026, 8, 5, tzinfo=UTC),
        VerificationStatus.VERIFIED,
    )
    initial = CheckpointContent(
        task_objective="Preserve the task across compaction.",
        completed_work=("Started the focused change.",),
        current_state="Work is in progress.",
        remaining_work=("Finish the focused change.",),
        decisions=("Do not expand scope.",),
        failures=(),
        blockers=(),
        relevant_files=("service.py",),
        relevant_artifacts=(),
        verification_performed=(),
        token_estimate=70,
    )
    with build_checkpoint_runtime(LocalConfig.defaults(data)) as runtime:
        first = runtime.checkpoint_service.create(
            CreateCheckpoint(binding.checkpoint_scope, initial, (evidence,))
        )

    hook = AutomaticMemoryHook(
        data,
        "codex",
        context_loader=lambda scope: cli._automatic_context_attachment(data, scope, "codex"),
    )
    hook.handle({"hook_event_name": "SessionStart", "session_id": "before", "cwd": str(project)})
    hook.handle(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "before",
            "cwd": str(project),
            "tool_name": "Edit",
        }
    )

    compact = hook.handle(
        {"hook_event_name": "PreCompact", "session_id": "before", "cwd": str(project)}
    )
    assert compact == {}

    after_compaction = hook.handle(
        {
            "hook_event_name": "SessionStart",
            "session_id": "after-compaction",
            "source": "compact",
            "cwd": str(project),
        }
    )
    after_compaction_output = after_compaction["hookSpecificOutput"]
    assert isinstance(after_compaction_output, dict)
    assert after_compaction_output["hookEventName"] == "SessionStart"
    after_compaction_context = str(after_compaction_output["additionalContext"])
    assert "save a concise checkpoint" in after_compaction_context
    assert "without a complete checkpoint" in after_compaction_context
    assert "Preserve the task across compaction" in after_compaction_context

    revised = CheckpointContent(
        task_objective=initial.task_objective,
        completed_work=("Finished the focused change before compaction.",),
        current_state="The bounded handoff is durable.",
        remaining_work=("Run the final verification.",),
        decisions=initial.decisions,
        failures=(),
        blockers=(),
        relevant_files=initial.relevant_files,
        relevant_artifacts=(),
        verification_performed=("Focused tests passed.",),
        token_estimate=80,
    )
    with build_checkpoint_runtime(LocalConfig.defaults(data)) as runtime:
        runtime.checkpoint_service.revise(
            ReviseCheckpoint(
                binding.checkpoint_scope,
                first.aggregate.checkpoint_id,
                first.revision.revision_id,
                revised,
                (evidence,),
            )
        )
    hook.handle(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "after-compaction",
            "cwd": str(project),
            "tool_name": "mcp__mnemo-memory__save_checkpoint",
            "tool_input": {"operation": "revise"},
        }
    )

    resumed = AutomaticMemoryHook(
        data,
        "codex",
        context_loader=lambda scope: cli._automatic_context_attachment(data, scope, "codex"),
    ).handle({"hook_event_name": "SessionStart", "session_id": "after", "cwd": str(project)})
    resumed_output = resumed["hookSpecificOutput"]
    assert isinstance(resumed_output, dict)
    resumed_context = str(resumed_output["additionalContext"])
    assert "Finished the focused change before compaction" in resumed_context
    assert "Run the final verification" in resumed_context
    assert "without a complete checkpoint" not in resumed_context


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
    assert "MNEMO_CONTEXT_V1" not in context
    assert "private loader failure" not in context


def test_cli_hook_wires_the_bounded_context_attachment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    received: list[tuple[Path, object, str]] = []
    prompt_received: list[tuple[Path, object, str, str]] = []
    refreshed: list[tuple[Path, object]] = []
    counted: list[tuple[Path, object]] = []

    def load(directory: Path, scope: object, client: str) -> str:
        received.append((directory, scope, client))
        return '{"packet":"saved"}'

    def load_prompt(directory: Path, scope: object, prompt: str, client: str) -> object:
        prompt_received.append((directory, scope, prompt, client))
        return PromptContextAttachment(f"rendered-for-{client}:relevant")

    rendered: list[tuple[str | None, str]] = []

    def render(packet: str | None, client: str) -> str | None:
        rendered.append((packet, client))
        return None if packet is None else f"rendered-for-{client}:{packet}"

    monkeypatch.setattr(
        cli,
        "_automatic_context_attachment",
        load,
    )
    monkeypatch.setattr(
        cli,
        "_automatic_prompt_context_for_hook",
        load_prompt,
    )
    monkeypatch.setattr(cli, "_render_automatic_context_attachment", render)
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
    assert received == [(data.resolve(), binding.checkpoint_scope, "codex")]
    assert refreshed == [(data.resolve(), binding)]
    assert counted == [(data.resolve(), binding)]
    emitted = json.loads(result.output)
    additional_context = emitted["hookSpecificOutput"]["additionalContext"]
    assert 'rendered-for-codex:{"packet":"saved"}' in additional_context
    assert "2 current scoped project knowledge document(s)" in additional_context
    assert "knowledge_query" in additional_context

    prompt_result = CliRunner().invoke(
        cli.app,
        ["automatic-memory-hook", "--client", "codex", "--data-dir", str(data)],
        input=json.dumps(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "s1",
                "cwd": str(project),
                "prompt": "finance reconciliation",
            }
        ),
    )
    assert prompt_result.exit_code == 0, prompt_result.output
    assert prompt_received == [
        (data.resolve(), binding.checkpoint_scope, "finance reconciliation", "codex")
    ]
    assert "relevant" in prompt_result.output
    assert rendered == [
        ('{"packet":"saved"}', "codex"),
    ]


def test_automatic_context_attachment_reads_the_real_bounded_durable_handoff(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    (project / "docs").mkdir()
    (project / "docs" / "reconciliation.md").write_text(
        "# Reconciliation\nUse the documented business-date grain.", encoding="utf-8"
    )
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
        created = runtime.checkpoint_service.create(
            CreateCheckpoint(binding.checkpoint_scope, content, (evidence,))
        )
    cli._refresh_project_knowledge(data, binding)

    attached = cli._automatic_context_attachment(data, binding.checkpoint_scope)

    assert attached is not None
    packet = json.loads(attached)
    assert packet["declared_total_tokens"] <= 1_750
    assert packet["active_task_checkpoint"]["content"] == json.dumps(
        created.revision.content.to_dict(), sort_keys=True, separators=(",", ":")
    )
    lifecycle = [
        json.loads(item["content"])
        for item in packet["episodic_memories"]
        if item["item_id"].startswith("checkpoint-lifecycle:")
    ]
    assert len(lifecycle) == 1
    assert lifecycle[0]["event_kind"] == "checkpoint_created"
    assert lifecycle[0]["revision_number"] == 1
    assert lifecycle[0]["revision_id"]
    assert lifecycle[0]["occurred_at"]
    assert all(item["evidence_references"] for item in packet["episodic_memories"])
    assert len(packet["knowledge_items"]) == 1
    assert "documented business-date grain" in packet["knowledge_items"][0]["content"]
    codex_rendering = cli._render_automatic_context_attachment(attached, "codex")
    claude_rendering = cli._render_automatic_context_attachment(attached, "claude-code")
    assert codex_rendering is not None
    assert claude_rendering is not None
    assert codex_rendering.startswith("MNEMO_CONTEXT_V1 client=codex\n")
    assert claude_rendering.startswith("MNEMO_CONTEXT_V1 client=claude-code\n")
    assert '"delivery_mode":"automatic_compact"' in codex_rendering
    assert (len(codex_rendering) + 3) // 4 <= 1_750
    assert (len(claude_rendering) + 3) // 4 <= 1_750
    assert "Run the regression check" in codex_rendering
    assert cli._render_automatic_context_attachment('{"not":"a packet"}', "codex") is None


def test_automatic_context_attaches_procedures_selected_by_one_client_profile(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    profile_document = KnowledgeDocumentParser().parse(
        KnowledgeDocumentParseRequest(binding.scope, "docs/codex-profile.md"),
        "---\nmnemo_kind: agent_profile\nmnemo_client: codex\n"
        "mnemo_procedure_tags: reconciliation\n---\n# Codex reconciliation\n",
    )
    procedure_document = KnowledgeDocumentParser().parse(
        KnowledgeDocumentParseRequest(binding.scope, "docs/reconciliation.md"),
        "---\nmnemo_kind: procedure\nmnemo_tags: reconciliation\n"
        "mnemo_mandatory: true\n---\n# Reconcile\nUse the cited input grain.",
    )
    with build_checkpoint_runtime(LocalConfig.defaults(data)) as runtime:
        assert runtime.knowledge_document_repository is not None
        runtime.knowledge_document_repository.apply_sync(
            binding.scope,
            (
                KnowledgeDocumentRevision(
                    KnowledgeDocumentRevisionId.new(),
                    profile_document,
                    1,
                    None,
                    datetime(2026, 8, 5, tzinfo=UTC),
                ),
                KnowledgeDocumentRevision(
                    KnowledgeDocumentRevisionId.new(),
                    procedure_document,
                    1,
                    None,
                    datetime(2026, 8, 5, tzinfo=UTC),
                ),
            ),
            (),
        )

    attached = cli._automatic_context_attachment(data, binding.checkpoint_scope, "codex")

    assert attached is not None
    packet = json.loads(attached)
    assert len(packet["skills_and_procedures"]) == 1
    item = packet["skills_and_procedures"][0]
    assert item["item_type"] == "mandatory_procedure"
    assert "codex-profile.md" in item["content"]
    assert len(item["evidence_references"]) == 2


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
    assert packet["declared_total_tokens"] <= 1_750
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
    assert summary["kind"] == "source_architecture_overview"
    assert summary["file_count"] == 1
    assert summary["currentness"] == "current"
    assert summary["files"] == ["service.py"]
    assert len(packet["structural_items"]) == 1
    assert packet["declared_total_tokens"] <= 1_750
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
    assert packet["declared_total_tokens"] <= 1_450
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
    assert context.startswith("MNEMO_DIRTY_V1")
    assert len(context) <= 256
    assert "source_changes" in context
    assert "relative_path" in context
    assert "private user question" not in context
    state = (data / "automatic-memory-session-state.json").read_text()
    assert '"dirty_reminder_sent":true' in state

    repeated = hook.handle(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "s1",
            "cwd": str(project),
            "prompt": "a second private question",
        }
    )
    assert repeated == {}


def test_dirty_session_reminder_resets_after_a_verified_checkpoint(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
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
    first = hook.handle(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "s1",
            "cwd": str(project),
            "prompt": "first request",
        }
    )
    assert "MNEMO_DIRTY_V1" in str(first)
    assert (
        hook.handle(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "s1",
                "cwd": str(project),
                "prompt": "second request",
            }
        )
        == {}
    )

    _create_test_handoff(data, binding)
    assert (
        hook.handle(
            {
                "hook_event_name": "PostToolUse",
                "session_id": "s1",
                "cwd": str(project),
                "tool_name": "mcp__mnemo-memory__save_checkpoint",
                "tool_input": {"operation": "create"},
            }
        )
        == {}
    )
    hook.handle(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "s1",
            "cwd": str(project),
            "tool_name": "Edit",
        }
    )
    next_cycle = hook.handle(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "s1",
            "cwd": str(project),
            "prompt": "third request",
        }
    )

    assert "MNEMO_DIRTY_V1" in str(next_cycle)


def test_dirty_prompt_boundary_refreshes_and_cues_exact_static_impact(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    core = project / "core.py"
    core.write_text("def calculate():\n    return 1\n", encoding="utf-8")
    (project / "service.py").write_text(
        "import core\n\ndef serve():\n    return core.calculate()\n", encoding="utf-8"
    )
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    hook = AutomaticMemoryHook(data, "codex")
    hook.handle({"hook_event_name": "SessionStart", "session_id": "s1", "cwd": str(project)})
    core.write_text("def calculate():\n    return 2\n", encoding="utf-8")
    failed_save = hook.handle(
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

    failed_save = hook.handle(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "s1",
            "cwd": str(project),
            "tool_name": "mcp__mnemo-memory__save_checkpoint",
        }
    )
    assert failed_save == {"systemMessage": "MNEMO_MEMORY_CHECKPOINT_NOT_PERSISTED"}
    still_dirty = hook.handle(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "s1",
            "cwd": str(project),
            "prompt": "another private user question",
        }
    )
    assert still_dirty == {}
    _create_test_handoff(data, binding)


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
        "one": {"checkpoint_marker": None, "dirty": True, "saved": False},
        "two": {"checkpoint_marker": None, "dirty": True, "saved": False},
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


def test_registered_project_source_refresh_fails_open_on_an_unsafe_file(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    (project / "oversized.py").write_bytes(b"x" * 1_000_001)
    data = tmp_path / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    repository = SQLiteSourceStructureRepository(data / "mnemo.sqlite3", base_directory=data)
    repository.migrate()

    assert refresh_registered_project_source(binding, repository) is None
    assert repository.get_active_snapshot(binding.scope) is None


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

    _create_test_handoff(data, binding)
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


def test_client_configuration_upgrades_owned_hook_timeout_without_duplication(
    tmp_path: Path,
) -> None:
    launcher = tmp_path / "mnemo-memory"
    launcher.touch()
    home = tmp_path / "codex"
    data = tmp_path / "mnemo data"
    assert enable_client_hooks("codex", launcher, home, data) is True
    path = home / "hooks.json"
    value = json.loads(path.read_text())
    for groups in value["hooks"].values():
        for group in groups:
            for handler in group["hooks"]:
                handler["timeout"] = 8
    path.write_text(json.dumps(value))

    assert enable_client_hooks("codex", launcher, home, data) is True
    upgraded = json.loads(path.read_text())
    owned = [
        handler
        for groups in upgraded["hooks"].values()
        for group in groups
        for handler in group["hooks"]
        if "automatic-memory-hook --client codex" in handler["command"]
    ]
    assert len(owned) == 5
    assert all(handler["timeout"] == 300 for handler in owned)
    assert enable_client_hooks("codex", launcher, home, data) is False


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
    payload = json.loads(result.output)
    assert payload["dbt"]["detected"] is True
    assert payload["dbt"]["existing_manifest"] == "unavailable"
    assert payload["dbt"]["ingested"] is False
    automatic = LocalMemoryProjectBindingStore(data).get(project)
    assert automatic is not None
    assert automatic.scope == expected


def test_scan_registers_detected_dbt_project_and_ingests_existing_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "dbt project"
    target = project / "target"
    target.mkdir(parents=True)
    project.joinpath("dbt_project.yml").write_text("name: synthetic\n", encoding="utf-8")
    target.joinpath("manifest.json").write_bytes(DBT_FIXTURE.read_bytes())
    data = tmp_path / "data"
    runner = CliRunner()
    monkeypatch.chdir(project)

    first = runner.invoke(
        cli.app,
        ["scan", "--data-dir", str(data)],
    )

    assert first.exit_code == 0, first.output
    first_payload = json.loads(first.output)
    assert first_payload["scanned"] is True
    assert first_payload["source_structure"]["indexed"] is True
    assert first_payload["dbt"]["detected"] is True
    assert first_payload["dbt"]["registered"] is True
    assert first_payload["dbt"]["existing_manifest"] == "activated"
    assert first_payload["dbt"]["ingested"] is True
    automatic = LocalMemoryProjectBindingStore(data).get(project)
    dbt = LocalDbtProjectBindingStore(data).get(project)
    assert automatic is not None and dbt is not None
    assert automatic.scope == dbt.scope

    second = runner.invoke(
        cli.app,
        ["scan", "--data-dir", str(data)],
    )

    assert second.exit_code == 0, second.output
    second_payload = json.loads(second.output)
    assert second_payload["source_structure"]["idempotent"] is True
    assert second_payload["dbt"]["existing_manifest"] == "unchanged"


def test_dbt_binding_scope_lookup_fails_closed_when_local_paths_are_ambiguous(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    store = LocalDbtProjectBindingStore(data)
    scope = LocalMemoryProjectBindingStore(data).personal_profile().scope()
    first = tmp_path / "first dbt project"
    first.mkdir()
    first.joinpath("dbt_project.yml").write_text("name: first\n")
    store.set(DbtProjectBinding(first.resolve(), scope))

    resolved = store.get_for_scope(scope)

    assert resolved is not None and resolved.project_root == first.resolve()
    second = tmp_path / "second dbt project"
    second.mkdir()
    second.joinpath("dbt_project.yml").write_text("name: second\n")
    store.set(DbtProjectBinding(second.resolve(), scope))
    assert store.get_for_scope(scope) is None
