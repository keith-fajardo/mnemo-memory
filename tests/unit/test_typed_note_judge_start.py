"""The hook starts the background judge detached, after its output, only when it may; the
judge command itself is silent and asks Jev only through a 5 s runtime guard (spec §3-§4)."""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import warnings
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from mnemo_memory.apps.cli import judge_entry, typed_decision_composition
from mnemo_memory.apps.cli import main as cli
from mnemo_memory.apps.cli.typed_decision_hook import TypedHookModes, TypedHookOverrides
from mnemo_memory.connectors.typesafe import jev_provider
from mnemo_memory.packages.application import PersonalSettings, PersonalSettingsStore
from mnemo_memory.packages.application.settings import with_typed_decision_mode
from mnemo_memory.packages.domain import TypedDecisionKind, TypedDecisionMode, TypedDecisionSource
from mnemo_memory.packages.model_gateway.typed_decisions import GuardedTypedDecisionClassifier
from mnemo_memory.packages.storage import LocalNoteJudgeQueue, LocalNoteVerdictCache
from scripts.typed_decision_replay import approved_event_item_ids, knowledge_note_item_ids
from scripts.typed_decision_test_support import (
    FAKE_TYPESAFE_KEY,
    KNOWLEDGE_PROMPT,
    HookFixture,
    run_hook,
    seed_hook_fixture,
)

OFF, SHADOW = TypedDecisionMode.OFF, TypedDecisionMode.SHADOW
runner = CliRunner()


def _relevance_shadow(fixture: HookFixture, model_id: str = "jev-1.13.0") -> None:
    """The real hook's own settings: master switch on, relevance in shadow."""

    base = PersonalSettings(
        experimental_semantic_memory_enabled=True,
        experimental_typed_decisions_enabled=True,
        typed_decision_model_id=model_id,
    )
    PersonalSettingsStore(fixture.data).save(
        with_typed_decision_mode(base, TypedDecisionKind.RELEVANCE, SHADOW)
    )


def test_the_hook_starts_the_judge_once_after_its_output_is_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    off = run_hook(fixture, KNOWLEDGE_PROMPT)
    _relevance_shadow(fixture)
    order: list[str] = []
    apply = cli._apply_typed_decisions

    def applied(*args: Any, **kwargs: Any) -> Any:
        result = apply(*args, **kwargs)
        order.append("applied")
        return result

    monkeypatch.setattr(cli, "_apply_typed_decisions", applied)
    monkeypatch.setattr(cli, "_judge_route_open", lambda settings: True)
    monkeypatch.setattr(
        cli, "_start_note_judge", lambda data_directory: order.append(f"start:{data_directory}")
    )
    seen = run_hook(fixture, KNOWLEDGE_PROMPT)
    assert (seen.context, seen.delivery_keys) == (off.context, off.delivery_keys)
    assert order == ["applied", f"start:{fixture.data}"]
    assert LocalNoteJudgeQueue(fixture.data).length() == 3


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        ("all_hold", 1),
        ("route_closed", 0),
        ("relevance_off", 0),
        ("master_off", 0),
        ("nothing_queued", 0),
        ("replay_overrides", 0),
    ],
)
def test_the_judge_starts_only_when_every_condition_holds(
    monkeypatch: pytest.MonkeyPatch, condition: str, expected: int
) -> None:
    settings = PersonalSettings(experimental_typed_decisions_enabled=condition != "master_off")
    modes = TypedHookModes(relevance=OFF if condition == "relevance_off" else SHADOW)
    overrides = (
        TypedHookOverrides(lambda recorder: None, modes)
        if condition == "replay_overrides"
        else None
    )
    started: list[Path] = []
    monkeypatch.setattr(cli, "_judge_route_open", lambda settings: condition != "route_closed")
    monkeypatch.setattr(cli, "_start_note_judge", started.append)
    queued = 0 if condition == "nothing_queued" else 3
    cli._maybe_start_note_judge(Path("/data"), settings, modes, overrides, queued)
    assert len(started) == expected


def test_the_route_stays_closed_to_the_judge_under_synthetic_only() -> None:
    assert cli._judge_route_open(PersonalSettings()) is False


def test_the_real_hook_never_starts_the_judge_while_the_route_is_synthetic_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    _relevance_shadow(fixture)
    started: list[Path] = []
    monkeypatch.setattr(cli, "_start_note_judge", started.append)
    run_hook(fixture, KNOWLEDGE_PROMPT)
    assert started == []
    assert LocalNoteJudgeQueue(fixture.data).length() == 3  # queued, waiting for a route


@pytest.mark.parametrize(
    ("model_id", "queued"),
    [("jev-1.13.0", 3), ("jev-latest", 0), ("jev-1.13", 0), ("jev-1.13.0-beta", 0)],
)
def test_the_hook_queues_and_spawns_only_for_a_pinned_model_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model_id: str, queued: int
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    _relevance_shadow(fixture, model_id)
    started: list[Path] = []
    monkeypatch.setattr(cli, "_judge_route_open", lambda settings: True)
    monkeypatch.setattr(cli, "_start_note_judge", started.append)
    run_hook(fixture, KNOWLEDGE_PROMPT)
    assert LocalNoteJudgeQueue(fixture.data).length() == queued
    assert len(started) == (1 if queued else 0)


def test_the_hook_never_waits_on_a_busy_queue_lock_and_reports_nothing_queued(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    off = run_hook(fixture, KNOWLEDGE_PROMPT)
    _relevance_shadow(fixture)
    started: list[Path] = []
    monkeypatch.setattr(cli, "_judge_route_open", lambda settings: True)
    monkeypatch.setattr(cli, "_start_note_judge", started.append)
    lock = fixture.data / ".typed-decision-judge-queue.json.lock"
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        seen = run_hook(fixture, KNOWLEDGE_PROMPT)
    finally:
        os.close(descriptor)
    assert (seen.context, seen.delivery_keys) == (off.context, off.delivery_keys)
    assert started == []  # nothing was queued, so no judge starts
    assert not LocalNoteJudgeQueue(fixture.data).path.exists()
    run_hook(fixture, KNOWLEDGE_PROMPT)  # a later prompt queues the notes
    assert LocalNoteJudgeQueue(fixture.data).length() == 3


def test_queueing_reports_zero_when_the_queue_lock_is_busy(tmp_path: Path) -> None:
    from mnemo_memory.apps.cli.typed_decision_hook import FillerCandidate
    from mnemo_memory.packages.storage import NoteVerdictState

    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    candidates = (FillerCandidate("note-1", "text"),)
    states = (NoteVerdictState(None, 0),)
    scope = fixture.binding.checkpoint_scope
    descriptor = os.open(
        fixture.data / ".typed-decision-judge-queue.json.lock", os.O_CREAT | os.O_RDWR
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        assert cli._queue_unjudged_notes(fixture.data, scope, candidates, states, "jev-1.13.0") == 0
    finally:
        os.close(descriptor)
    assert cli._queue_unjudged_notes(fixture.data, scope, candidates, states, "jev-1.13.0") == 1


def test_the_judge_command_asks_nothing_for_an_unpinned_model_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    PersonalSettingsStore(fixture.data).save(
        PersonalSettings(
            experimental_semantic_memory_enabled=True,
            experimental_typed_decisions_enabled=True,
            typed_decision_model_id="jev-latest",
        )
    )
    item_ids = knowledge_note_item_ids(fixture.data, fixture.binding)
    LocalNoteJudgeQueue(fixture.data).append(fixture.binding.checkpoint_scope, item_ids)
    built: list[object] = []
    monkeypatch.setattr(
        typed_decision_composition,
        "build_runtime_typed_decision_classifier",
        lambda *args, **kwargs: built.append(args),
    )
    result = runner.invoke(
        cli.app,
        ["typed-decisions", "judge-notes", "--data-dir", str(fixture.data)],
        env={"TYPESAFE_API_KEY": FAKE_TYPESAFE_KEY},
    )
    assert (result.exit_code, result.stdout, result.stderr) == (0, "", "")
    assert built == []
    assert LocalNoteJudgeQueue(fixture.data).length() == len(item_ids)  # nothing taken


def test_a_failed_start_changes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    off = run_hook(fixture, KNOWLEDGE_PROMPT)
    _relevance_shadow(fixture)

    def broken(data_directory: Path) -> None:
        raise OSError("synthetic spawn failure")

    monkeypatch.setattr(cli, "_judge_route_open", lambda settings: True)
    monkeypatch.setattr(cli, "_start_note_judge", broken)
    seen = run_hook(fixture, KNOWLEDGE_PROMPT)
    assert (seen.context, seen.delivery_keys) == (off.context, off.delivery_keys)
    assert seen.telemetry_event_id is not None


def test_the_start_is_detached_names_only_the_data_directory_and_never_waits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review focus 5: a path with spaces and quotes is one argv element, never a shell."""

    data = tmp_path / "Application Support" / "it's mnemo"
    seen: dict[str, Any] = {}

    class FakeProcess:
        def __init__(self, command: list[str], **kwargs: Any) -> None:
            seen.update(kwargs, command=command)

        def wait(self, timeout: float | None = None) -> int:
            raise AssertionError("the hook must never wait for the judge")

    monkeypatch.setenv("TYPESAFE_API_KEY", FAKE_TYPESAFE_KEY)
    monkeypatch.setattr(subprocess, "Popen", FakeProcess)
    cli._start_note_judge(data)
    assert seen["command"] == [
        sys.executable,
        "-P",
        "-m",
        "mnemo_memory.apps.cli.judge_entry",
        "--data-dir",
        str(data),
    ]
    assert seen["start_new_session"] is True
    assert seen["close_fds"] is True
    assert seen["stdin"] == seen["stdout"] == seen["stderr"] == subprocess.DEVNULL
    assert "shell" not in seen and "env" not in seen
    assert FAKE_TYPESAFE_KEY not in json.dumps(seen["command"])


def test_dropping_the_child_handle_raises_no_resource_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hook drops the handle on purpose, so Python's "still running" warning is silenced."""

    class WarningProcess:
        def __init__(self, command: list[str], **kwargs: Any) -> None:
            warnings.warn("subprocess 123 is still running", ResourceWarning, stacklevel=2)

    monkeypatch.setattr(subprocess, "Popen", WarningProcess)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cli._start_note_judge(tmp_path)
    assert caught == []


def test_the_judge_command_prints_nothing_and_exits_zero_when_it_cannot_run(
    tmp_path: Path,
) -> None:
    (tmp_path / "settings.json").write_text("{not json", encoding="utf-8")
    result = runner.invoke(cli.app, ["typed-decisions", "judge-notes", "--data-dir", str(tmp_path)])
    assert (result.exit_code, result.stdout, result.stderr) == (0, "", "")


def test_the_judge_command_stays_silent_when_building_the_guard_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not only ``OSError``: any failure inside ``build_guard()`` still exits 0, silently."""

    built: list[float] = []

    def broken(*args: Any, deadline_seconds: float, **kwargs: Any) -> None:
        built.append(deadline_seconds)
        raise RuntimeError("synthetic guard failure")

    monkeypatch.setattr(
        typed_decision_composition, "build_runtime_typed_decision_classifier", broken
    )
    result = runner.invoke(cli.app, ["typed-decisions", "judge-notes", "--data-dir", str(tmp_path)])
    assert (result.exit_code, result.stdout, result.stderr) == (0, "", "")
    assert built == [5.0]


def test_the_judge_command_builds_a_five_second_runtime_guard_and_sends_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    PersonalSettingsStore(fixture.data).save(
        PersonalSettings(
            experimental_semantic_memory_enabled=True, experimental_typed_decisions_enabled=True
        )
    )
    item_ids = (
        *knowledge_note_item_ids(fixture.data, fixture.binding),
        *approved_event_item_ids(fixture.data, fixture.binding),
    )
    LocalNoteJudgeQueue(fixture.data).append(fixture.binding.checkpoint_scope, item_ids)
    calls: list[str] = []

    def counting(url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> bytes:
        calls.append(url)
        raise AssertionError("runtime note text reached the Jev transport")

    built: list[GuardedTypedDecisionClassifier] = []
    original = typed_decision_composition.build_runtime_typed_decision_classifier

    def recording(*args: Any, **kwargs: Any) -> GuardedTypedDecisionClassifier | None:
        guard = original(*args, **kwargs)
        if guard is not None:
            built.append(guard)
        return guard

    monkeypatch.setattr(jev_provider, "_urllib_transport", counting)
    monkeypatch.setattr(
        typed_decision_composition, "build_runtime_typed_decision_classifier", recording
    )
    result = runner.invoke(
        cli.app,
        ["typed-decisions", "judge-notes", "--data-dir", str(fixture.data)],
        env={"TYPESAFE_API_KEY": FAKE_TYPESAFE_KEY},
    )
    assert (result.exit_code, result.stdout, result.stderr) == (0, "", "")
    assert [guard._deadline for guard in built] == [5.0]
    assert all(guard._source is TypedDecisionSource.RUNTIME for guard in built)
    assert calls == []
    assert LocalNoteVerdictCache(fixture.data).entry_count() == 0
    assert not (fixture.data / "typed_decision-budget.json").exists()


def test_the_judge_entry_point_loads_neither_typer_nor_the_cli_main_module() -> None:
    """The spawned judge pays for what it needs only (about 0.4 s of CPU saved per start)."""

    code = (
        "import mnemo_memory.apps.cli.judge_entry, sys; "
        "print('typer' in sys.modules, 'mnemo_memory.apps.cli.main' in sys.modules)"
    )
    done = subprocess.run(
        [sys.executable, "-P", "-c", code], capture_output=True, text=True, check=True
    )
    assert done.stdout.strip() == "False False"


def test_the_judge_entry_prints_nothing_and_returns_zero_when_it_cannot_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "settings.json").write_text("{not json", encoding="utf-8")
    assert judge_entry.main(["--data-dir", str(tmp_path)]) == 0
    assert judge_entry.main(["--unknown", "x"]) == 0
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "")


def test_the_judge_entry_stays_silent_when_building_the_guard_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    built: list[float] = []

    def broken(*args: Any, deadline_seconds: float, **kwargs: Any) -> None:
        built.append(deadline_seconds)
        raise RuntimeError("synthetic guard failure")

    monkeypatch.setattr(
        typed_decision_composition, "build_runtime_typed_decision_classifier", broken
    )
    assert judge_entry.main(["--data-dir", str(tmp_path)]) == 0
    captured = capsys.readouterr()
    assert (captured.out, captured.err, built) == ("", "", [5.0])


def test_the_judge_entry_runs_the_same_judge_as_the_hidden_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran: list[Path | None] = []
    monkeypatch.setattr(
        "mnemo_memory.apps.cli.typed_note_judge_runner.run_queued_note_judge", ran.append
    )
    assert judge_entry.main(["--data-dir", str(tmp_path)]) == 0
    assert judge_entry.main([f"--data-dir={tmp_path}"]) == 0
    result = runner.invoke(cli.app, ["typed-decisions", "judge-notes", "--data-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert ran == [tmp_path, tmp_path, tmp_path]
