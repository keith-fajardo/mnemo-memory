"""Git observation stays bounded, shell-free, and independent of a real repository."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from mnemo_memory.connectors.automatic_memory.git_observation import (
    GitObservationStore,
    GitSourceObserver,
)
from mnemo_memory.connectors.automatic_memory.hook import AutomaticMemoryHook
from mnemo_memory.packages.application.automatic_memory import LocalMemoryProjectBindingStore


def _runner(
    values: dict[tuple[str, ...], str | None],
) -> Callable[[tuple[str, ...], Path], str | None]:
    def run(arguments: tuple[str, ...], root: Path) -> str | None:
        assert root.is_absolute()
        return values.get(arguments)

    return run


def test_git_observer_collects_only_commit_ids_parent_and_dirty_state(tmp_path: Path) -> None:
    root = tmp_path / "project Δ"
    root.mkdir()
    commit = "a" * 40
    parent = "b" * 40
    observer = GitSourceObserver(
        _runner(
            {
                ("rev-parse", "--is-inside-work-tree"): "true\n",
                ("rev-parse", "--verify", "HEAD"): f"{commit}\n",
                ("rev-parse", "--verify", "HEAD^"): f"{parent}\n",
                ("status", "--porcelain=v1", "-z"): " M private.py\0",
            }
        )
    )

    observation = observer.observe(root, "sha256:" + "1" * 64)

    assert observation is not None
    assert observation.commit_id == commit
    assert observation.parent_commit_id == parent
    assert observation.dirty is True
    assert "private.py" not in json.dumps(observation.to_dict())


def test_git_observer_fails_closed_for_missing_or_invalid_git_state(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    source_digest = "sha256:" + "2" * 64
    assert GitSourceObserver(_runner({})).observe(root, source_digest) is None
    assert (
        GitSourceObserver(
            _runner(
                {
                    ("rev-parse", "--is-inside-work-tree"): "true",
                    ("rev-parse", "--verify", "HEAD"): "bad",
                }
            )
        ).observe(root, source_digest)
        is None
    )


def test_git_observation_store_is_scoped_bounded_and_path_free(tmp_path: Path) -> None:
    first_project = tmp_path / "first private path"
    second_project = tmp_path / "second"
    first_project.mkdir()
    second_project.mkdir()
    data = tmp_path / "data"
    first = LocalMemoryProjectBindingStore(data).enable(first_project)
    second = LocalMemoryProjectBindingStore(data).enable(second_project)
    commit = "c" * 40
    observation = GitSourceObserver(
        _runner(
            {
                ("rev-parse", "--is-inside-work-tree"): "true",
                ("rev-parse", "--verify", "HEAD"): commit,
                ("rev-parse", "--verify", "HEAD^"): None,
                ("status", "--porcelain=v1", "-z"): "",
            }
        )
    ).observe(first_project, "sha256:" + "3" * 64)
    assert observation is not None
    store = GitObservationStore(data)
    store.put(first.scope, observation)

    assert store.get(first.scope, observation.source_digest) == observation
    assert store.get(second.scope, observation.source_digest) is None
    persisted = (data / "automatic-memory-git-observations.json").read_text(encoding="utf-8")
    assert str(first_project) not in persisted
    assert "private path" not in persisted


def test_automatic_hook_attaches_git_state_without_source_or_status_output(tmp_path: Path) -> None:
    project = tmp_path / "project Ω"
    project.mkdir()
    (project / "service.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    data = tmp_path / "data"
    LocalMemoryProjectBindingStore(data).enable(project)
    commit = "d" * 40
    hook = AutomaticMemoryHook(
        data,
        "codex",
        git_observer=GitSourceObserver(
            _runner(
                {
                    ("rev-parse", "--is-inside-work-tree"): "true",
                    ("rev-parse", "--verify", "HEAD"): commit,
                    ("rev-parse", "--verify", "HEAD^"): None,
                    ("status", "--porcelain=v1", "-z"): " M service.py\0",
                }
            )
        ),
    )

    result = hook.handle(
        {"hook_event_name": "SessionStart", "session_id": "session", "cwd": str(project)}
    )

    context = str(result["hookSpecificOutput"])
    assert f"dirty at {commit}" in context
    assert "service.py" not in context
    assert "return 1" not in context
    assert str(project) not in context


def test_clean_git_observation_proves_read_only_shell_needs_no_checkpoint(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "service.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    data = tmp_path / "data"
    LocalMemoryProjectBindingStore(data).enable(project)
    commit = "e" * 40
    hook = AutomaticMemoryHook(
        data,
        "codex",
        git_observer=GitSourceObserver(
            _runner(
                {
                    ("rev-parse", "--is-inside-work-tree"): "true",
                    ("rev-parse", "--verify", "HEAD"): commit,
                    ("rev-parse", "--verify", "HEAD^"): None,
                    ("status", "--porcelain=v1", "-z"): "",
                }
            )
        ),
    )
    hook.handle({"hook_event_name": "SessionStart", "session_id": "s1", "cwd": str(project)})

    result = hook.handle(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "s1",
            "cwd": str(project),
            "tool_name": "Bash",
            "tool_input": {"command": "private read-only command"},
        }
    )

    assert result == {}
    assert hook.handle({"hook_event_name": "Stop", "session_id": "s1", "cwd": str(project)}) == {}
    state = (data / "automatic-memory-session-state.json").read_text(encoding="utf-8")
    assert '"dirty":false' in state
    assert "private read-only command" not in state
    assert not (data / "automatic-memory-handoff-state.json").exists()


def test_shell_with_changed_git_state_still_requires_checkpoint(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "service.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    data = tmp_path / "data"
    LocalMemoryProjectBindingStore(data).enable(project)
    commit = "f" * 40
    status_calls = 0

    def changing_runner(arguments: tuple[str, ...], root: Path) -> str | None:
        nonlocal status_calls
        assert root == project.resolve()
        if arguments == ("rev-parse", "--is-inside-work-tree"):
            return "true"
        if arguments == ("rev-parse", "--verify", "HEAD"):
            return commit
        if arguments == ("rev-parse", "--verify", "HEAD^"):
            return None
        if arguments == ("status", "--porcelain=v1", "-z"):
            status_calls += 1
            return "" if status_calls == 1 else " M private.py\0"
        return None

    hook = AutomaticMemoryHook(
        data,
        "codex",
        git_observer=GitSourceObserver(changing_runner),
    )
    hook.handle({"hook_event_name": "SessionStart", "session_id": "s1", "cwd": str(project)})

    assert (
        hook.handle(
            {
                "hook_event_name": "PostToolUse",
                "session_id": "s1",
                "cwd": str(project),
                "tool_name": "Bash",
            }
        )
        == {}
    )
    stop = hook.handle({"hook_event_name": "Stop", "session_id": "s1", "cwd": str(project)})

    assert stop["decision"] == "block"
    assert "save_checkpoint" in str(stop)


@pytest.mark.parametrize(
    ("status", "case"),
    ((None, "missing Git evidence"), (" M existing.py\0", "initially dirty Git state")),
)
def test_shell_without_a_clean_git_baseline_still_requires_checkpoint(
    tmp_path: Path, status: str | None, case: str
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "service.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    data = tmp_path / "data"
    LocalMemoryProjectBindingStore(data).enable(project)
    commit = "1" * 40
    hook = AutomaticMemoryHook(
        data,
        "codex",
        git_observer=GitSourceObserver(
            _runner(
                {
                    ("rev-parse", "--is-inside-work-tree"): "true",
                    ("rev-parse", "--verify", "HEAD"): commit,
                    ("rev-parse", "--verify", "HEAD^"): None,
                    ("status", "--porcelain=v1", "-z"): status,
                }
            )
        ),
    )
    hook.handle({"hook_event_name": "SessionStart", "session_id": case, "cwd": str(project)})

    hook.handle(
        {
            "hook_event_name": "PostToolUse",
            "session_id": case,
            "cwd": str(project),
            "tool_name": "Bash",
        }
    )
    stop = hook.handle({"hook_event_name": "Stop", "session_id": case, "cwd": str(project)})

    assert stop["decision"] == "block"
    assert "save_checkpoint" in str(stop)
