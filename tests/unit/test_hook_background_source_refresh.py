"""Lifecycle hooks never parse source; a stale map is refreshed by a detached worker (spec §2)."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from mnemo_memory.apps.cli import main as cli
from mnemo_memory.connectors.automatic_memory.git_observation import GitSourceObserver
from mnemo_memory.connectors.automatic_memory.hook import (
    SOURCE_REFRESH_PENDING_NOTICE,
    AutomaticMemoryHook,
)
from mnemo_memory.connectors.automatic_memory.source_refresh import (
    SourceRefreshLock,
    run_source_refresh,
)
from mnemo_memory.packages.application.automatic_memory import (
    LocalMemoryProjectBindingStore,
    MemoryProjectBinding,
)
from mnemo_memory.packages.application.bootstrap import build_checkpoint_runtime
from mnemo_memory.packages.application.checkpoints import CreateCheckpoint
from mnemo_memory.packages.application.config import LocalConfig
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
from mnemo_memory.packages.storage import SQLiteSourceStructureRepository

_NO_GIT = GitSourceObserver(lambda arguments, root: None)
_CLEAN_COMMIT = "c" * 40
_CLEAN_ANSWERS: dict[tuple[str, ...], str] = {
    ("rev-parse", "--is-inside-work-tree"): "true",
    ("rev-parse", "--verify", "HEAD"): _CLEAN_COMMIT,
    ("status", "--porcelain=v1", "-z"): "",
}
_CLEAN_GIT = GitSourceObserver(lambda arguments, root: _CLEAN_ANSWERS.get(arguments))
_SAVE = "mcp__mnemo-memory__save_checkpoint"


class _Starts:
    """A fake spawner that records each start instead of launching a process."""

    def __init__(self) -> None:
        self.calls: list[tuple[Path, Path]] = []

    def __call__(self, data_directory: Path, project_root: Path) -> None:
        self.calls.append((data_directory, project_root))


def _project(tmp_path: Path) -> tuple[Path, Path, MemoryProjectBinding]:
    project = tmp_path / "repo"
    project.mkdir()
    (project / "service.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    data = tmp_path / "data"
    return project, data, LocalMemoryProjectBindingStore(data).enable(project)


def _event(name: str, project: Path, session: str = "s1", **fields: object) -> dict[str, object]:
    return {"hook_event_name": name, "session_id": session, "cwd": str(project), **fields}


def _prime(data: Path, project: Path) -> None:
    """Build the map the way the background worker does, so the next hook sees it fresh."""
    assert run_source_refresh(data, project, git_observer=_NO_GIT).ran


def _record_parses(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    parsed: list[str] = []
    real = SourceStructureParser.parse

    def recording(self: SourceStructureParser, request: SourceStructureParseRequest) -> Any:
        parsed.append("parse")
        return real(self, request)

    monkeypatch.setattr(SourceStructureParser, "parse", recording)
    return parsed


def _context(result: dict[str, object]) -> str:
    output = result["hookSpecificOutput"]
    assert isinstance(output, dict)
    return str(output["additionalContext"])


def _create_handoff(data: Path, binding: MemoryProjectBinding) -> None:
    content = CheckpointContent(
        task_objective="Preserve the focused task.",
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
        "fixture://background-refresh/persisted",
        "sha256:" + "d" * 64,
        EvidenceLocation("fixture://background-refresh/persisted"),
        datetime(2026, 10, 3, tzinfo=UTC),
        VerificationStatus.VERIFIED,
    )
    with build_checkpoint_runtime(LocalConfig.defaults(data)) as runtime:
        runtime.checkpoint_service.create(
            CreateCheckpoint(binding.checkpoint_scope, content, (evidence,))
        )


def test_a_stale_tree_is_never_parsed_in_the_hook_and_starts_one_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, data, binding = _project(tmp_path)
    parsed = _record_parses(monkeypatch)
    starts = _Starts()
    hook = AutomaticMemoryHook(data, "codex", source_refresh_starter=starts)

    context = _context(hook.handle(_event("SessionStart", project)))

    assert parsed == []
    assert starts.calls == [(data, binding.project_root)]
    assert "current_source_digest" not in context  # an unproven map is never called current
    repository = SQLiteSourceStructureRepository(data / "mnemo.sqlite3")
    assert repository.get_active_snapshot(binding.scope) is None


def test_a_fresh_tree_behaves_as_before_and_starts_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, data, _ = _project(tmp_path)
    _prime(data, project)
    parsed = _record_parses(monkeypatch)
    starts = _Starts()
    hook = AutomaticMemoryHook(data, "codex", git_observer=_NO_GIT, source_refresh_starter=starts)

    started = _context(hook.handle(_event("SessionStart", project)))
    hook.handle(_event("PostToolUse", project, tool_name="Edit"))
    reminder = _context(hook.handle(_event("UserPromptSubmit", project, prompt="next request")))

    assert "current_source_digest" in started
    assert SOURCE_REFRESH_PENDING_NOTICE not in started
    assert reminder.startswith("MNEMO_DIRTY_V1")
    assert SOURCE_REFRESH_PENDING_NOTICE not in reminder
    assert len(reminder) <= 256
    assert parsed == []
    assert starts.calls == []


def test_a_pending_refresh_adds_the_fixed_notice_line(tmp_path: Path) -> None:
    project, data, _ = _project(tmp_path)
    hook = AutomaticMemoryHook(data, "claude-code", source_refresh_starter=_Starts())
    hook.handle(_event("SessionStart", project))
    hook.handle(_event("PostToolUse", project, tool_name="Edit"))

    reminder = _context(hook.handle(_event("UserPromptSubmit", project, prompt="next request")))
    stop = hook.handle(_event("Stop", project))
    compact = _context(hook.handle(_event("PreCompact", project)))

    assert reminder.startswith("MNEMO_DIRTY_V1")
    assert reminder.splitlines()[-1] == SOURCE_REFRESH_PENDING_NOTICE
    assert stop["decision"] == "block"
    assert str(stop["reason"]).splitlines()[-1] == SOURCE_REFRESH_PENDING_NOTICE
    assert SOURCE_REFRESH_PENDING_NOTICE in compact.splitlines()
    for text in (reminder, str(stop["reason"]), compact):
        assert str(project) not in text
        assert "service.py" not in text


def test_a_failed_start_is_ignored(tmp_path: Path) -> None:
    project, data, _ = _project(tmp_path)
    calls: list[Path] = []

    def broken(data_directory: Path, project_root: Path) -> None:
        calls.append(project_root)
        raise OSError("synthetic spawn failure")

    failing = AutomaticMemoryHook(data, "codex", source_refresh_starter=broken)
    plain = AutomaticMemoryHook(data, "codex")

    assert failing.handle(_event("SessionStart", project)) == plain.handle(
        _event("SessionStart", project, session="s2")
    )
    assert len(calls) == 1


def test_no_worker_starts_while_one_holds_the_lock(tmp_path: Path) -> None:
    project, data, _ = _project(tmp_path)
    starts = _Starts()
    hook = AutomaticMemoryHook(data, "codex", source_refresh_starter=starts)

    with SourceRefreshLock(data).hold() as held:
        assert held
        hook.handle(_event("SessionStart", project))

    assert starts.calls == []
    hook.handle(_event("SessionStart", project, session="s2"))
    assert len(starts.calls) == 1


def test_a_checkpoint_save_writes_no_git_baseline_while_the_map_is_stale(tmp_path: Path) -> None:
    project, data, binding = _project(tmp_path)
    _prime(data, project)
    starts = _Starts()
    hook = AutomaticMemoryHook(
        data, "codex", git_observer=_CLEAN_GIT, source_refresh_starter=starts
    )
    hook.handle(_event("SessionStart", project))
    state_path = data / "automatic-memory-session-state.json"
    assert json.loads(state_path.read_text(encoding="utf-8"))["s1"]["git_source_digest"]

    (project / "service.py").write_text("def run():\n    return 2\n", encoding="utf-8")
    hook.handle(_event("PostToolUse", project, tool_name="Edit"))
    _create_handoff(data, binding)
    saved = hook.handle(
        _event("PostToolUse", project, tool_name=_SAVE, tool_input={"operation": "create"})
    )

    assert saved == {}
    assert "git_source_digest" not in json.loads(state_path.read_text(encoding="utf-8"))["s1"]
    assert starts.calls == [(data, binding.project_root)]


def test_a_stale_tree_never_holds_the_hook_for_a_slow_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wall-time guard (spec §7): a 5 s parser cannot slow any hook event past 1 s."""
    project, data, _ = _project(tmp_path)

    def slow(self: SourceStructureParser, request: SourceStructureParseRequest) -> Any:
        time.sleep(5)
        raise AssertionError("a lifecycle hook parsed source")

    monkeypatch.setattr(SourceStructureParser, "parse", slow)
    # Migrate first so the timed loop measures hook work, not first-time schema creation.
    SQLiteSourceStructureRepository(data / "mnemo.sqlite3").migrate()
    starts = _Starts()
    hook = AutomaticMemoryHook(data, "codex", source_refresh_starter=starts)

    for event in (
        _event("SessionStart", project),
        _event("PostToolUse", project, tool_name="Edit"),
        _event("UserPromptSubmit", project, prompt="next request"),
        _event("Stop", project),
    ):
        started = time.perf_counter()
        hook.handle(event)
        assert time.perf_counter() - started < 1.0
    assert len(starts.calls) == 3


def test_the_worker_starts_only_after_the_output_is_built(tmp_path: Path) -> None:
    project, data, _ = _project(tmp_path)
    order: list[str] = []

    def loader(scope: MemoryScope) -> str | None:
        order.append("output")
        return None

    def start(data_directory: Path, project_root: Path) -> None:
        order.append("start")

    hook = AutomaticMemoryHook(data, "codex", context_loader=loader, source_refresh_starter=start)
    hook.handle(_event("SessionStart", project))

    assert order == ["output", "start"]


def test_the_start_is_detached_and_passes_each_path_as_one_argument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review focus 4: spaces, quotes and non-ASCII stay one argv element; no shell."""
    monkeypatch.delenv("MNEMO_DISABLE_BACKGROUND_SOURCE_REFRESH", raising=False)
    data = tmp_path / "Application Support" / "it's mnemo"
    project = tmp_path / 'repo Ω "quoted"'
    seen: dict[str, Any] = {}

    class FakeProcess:
        def __init__(self, command: list[str], **kwargs: Any) -> None:
            seen.update(kwargs, command=command)

        def wait(self, timeout: float | None = None) -> int:
            raise AssertionError("the hook must never wait for the refresh worker")

    monkeypatch.setattr(subprocess, "Popen", FakeProcess)
    cli._start_source_refresh(data, project)

    assert seen["command"] == [
        sys.executable,
        "-P",
        "-m",
        "mnemo_memory.apps.cli.source_refresh_entry",
        "--data-dir",
        str(data),
        "--project-root",
        str(project),
    ]
    assert seen["start_new_session"] is True
    assert seen["close_fds"] is True
    assert seen["stdin"] == seen["stdout"] == seen["stderr"] == subprocess.DEVNULL
    assert "shell" not in seen and "env" not in seen


def test_the_start_is_off_while_the_guard_variable_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MNEMO_DISABLE_BACKGROUND_SOURCE_REFRESH", "1")
    calls: list[object] = []

    def forbidden(*args: Any, **kwargs: Any) -> None:
        calls.append(args)

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    cli._start_source_refresh(tmp_path, tmp_path)

    assert calls == []


def test_the_cli_hook_composition_injects_the_detached_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[tuple[Path, Path]] = []

    def record(data_directory: Path, project_root: Path) -> None:
        started.append((data_directory, project_root))

    monkeypatch.setattr(cli, "_start_source_refresh", record)
    hook = cli.build_automatic_memory_hook(LocalConfig.defaults(tmp_path / "data"), "codex")

    assert hook.source_refresh_starter is not None
    hook.source_refresh_starter(tmp_path / "data", tmp_path)
    assert started == [(tmp_path / "data", tmp_path)]


def _currentness(attached: str | None) -> list[str]:
    assert attached is not None
    values: list[str] = []
    for item in json.loads(attached)["structural_items"]:
        try:
            content = json.loads(item["content"])
        except ValueError:
            continue
        if isinstance(content, dict) and "currentness" in content:
            values.append(str(content["currentness"]))
    return values


def test_a_stale_tree_is_never_called_current_in_the_session_start_attachment(
    tmp_path: Path,
) -> None:
    """Controller ruling I2: the attached packet labels the map current only when it is fresh."""
    project, data, binding = _project(tmp_path)
    _prime(data, project)
    assert "current" in _currentness(
        cli._automatic_context_attachment(data, binding.checkpoint_scope)
    )

    (project / "service.py").write_text("def run():\n    return 22\n", encoding="utf-8")
    stale = _currentness(cli._automatic_context_attachment(data, binding.checkpoint_scope))

    assert stale
    assert "current" not in stale


def test_a_held_write_lock_never_stalls_or_breaks_a_hook_on_a_current_schema(
    tmp_path: Path,
) -> None:
    """Final review: a worker or CLI holding the SQLite write lock must not stall the hook."""
    project, data, binding = _project(tmp_path)
    _prime(data, project)
    (project / "service.py").write_text("def changed():\n    return 2\n", encoding="utf-8")
    starts = _Starts()
    hook = AutomaticMemoryHook(data, "codex", source_refresh_starter=starts)
    holder = sqlite3.connect(data / "mnemo.sqlite3", isolation_level=None)
    try:
        holder.execute("BEGIN IMMEDIATE")
        started = time.perf_counter()
        result = hook.handle(_event("SessionStart", project))
        elapsed = time.perf_counter() - started
    finally:
        holder.rollback()
        holder.close()

    assert elapsed < 1.0  # far below the 5 s busy timeout a queued migration would wait
    assert json.loads(json.dumps(result)) == result
    assert "current_source_digest" not in _context(result)
    assert starts.calls == [(data, binding.project_root)]


def test_a_storage_error_while_migrating_fails_the_hook_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, data, _ = _project(tmp_path)

    def locked(self: SQLiteSourceStructureRepository, **_: object) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(SQLiteSourceStructureRepository, "migrate", locked)
    starts = _Starts()
    hook = AutomaticMemoryHook(data, "codex", source_refresh_starter=starts)

    result = hook.handle(_event("SessionStart", project))

    assert "database is locked" not in json.dumps(result)
    assert "current_source_digest" not in _context(result)
    assert starts.calls == []


def test_the_schema_check_is_read_only_and_never_waits_for_the_write_lock(
    tmp_path: Path,
) -> None:
    database = tmp_path / "data" / "mnemo.sqlite3"
    repository = SQLiteSourceStructureRepository(database)

    assert repository.schema_is_current() is False
    assert not database.exists()  # a check never creates the database

    repository.migrate()
    holder = sqlite3.connect(database, isolation_level=None)
    try:
        holder.execute("BEGIN IMMEDIATE")
        started = time.perf_counter()
        assert repository.schema_is_current() is True
        assert time.perf_counter() - started < 1.0
    finally:
        holder.rollback()
        holder.close()


@pytest.mark.parametrize(
    "failure", [sqlite3.OperationalError("database is locked"), RuntimeError("unexpected")]
)
def test_the_cli_hook_prints_the_unavailable_output_on_any_unexpected_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    class _Broken:
        def handle(self, event: object) -> dict[str, object]:
            raise failure

    monkeypatch.setattr(cli, "build_automatic_memory_hook", lambda *_: _Broken())

    result = CliRunner().invoke(
        cli.app,
        ["automatic-memory-hook", "--client", "codex", "--data-dir", str(tmp_path / "data")],
        input=json.dumps(_event("SessionStart", tmp_path)),
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"systemMessage": "MNEMO_MEMORY_HOOK_UNAVAILABLE"}
