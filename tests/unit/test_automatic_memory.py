"""Opt-in automatic task-memory hooks never capture raw client content."""

from __future__ import annotations

import json
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
from mnemo_memory.connectors.dbt.project_binding import (
    DbtProjectBinding,
    LocalDbtProjectBindingStore,
)
from mnemo_memory.packages.application.automatic_memory import (
    AutomaticMemoryBindingError,
    LocalMemoryProjectBindingStore,
    exclusive_local_file_lock,
)
from mnemo_memory.packages.application.bootstrap import build_checkpoint_runtime
from mnemo_memory.packages.application.config import LocalConfig
from mnemo_memory.packages.application.mcp_durable import DurableMcpContextPort
from mnemo_memory.packages.application.unified_context import UnifiedContextService
from mnemo_memory.packages.storage import SQLiteSourceStructureRepository


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


def test_hook_requests_bounded_checkpoint_only_after_work_and_tracks_save(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    data = tmp_path / "data"
    LocalMemoryProjectBindingStore(data).enable(project)
    hook = AutomaticMemoryHook(data, "codex")

    started = hook.handle(
        {"hook_event_name": "SessionStart", "session_id": "s1", "cwd": str(project)}
    )
    context = started["hookSpecificOutput"]
    assert isinstance(context, dict)
    assert "get_context" in str(context)
    assert "source_query" in str(context)
    assert "current_source_digest" in str(context)
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
