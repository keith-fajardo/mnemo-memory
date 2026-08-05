from __future__ import annotations

from pathlib import Path

from mnemo_memory.connectors.dbt.git_state import DbtGitStateObserver

COMMIT = b"a" * 40


def runner(status: bytes):  # type: ignore[no-untyped-def]
    def run(arguments: tuple[str, ...], _: Path) -> bytes | None:
        if arguments == ("rev-parse", "--is-inside-work-tree"):
            return b"true\n"
        if arguments == ("rev-parse", "--verify", "HEAD"):
            return COMMIT + b"\n"
        if arguments[0] == "status":
            return status
        return None

    return run


def test_dbt_git_state_hashes_changed_content_without_retaining_paths(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    root.joinpath("changed.py").write_text("first private body")
    root.joinpath("new.txt").write_text("new private body")
    status = b" M changed.py\0?? new.txt\0 D deleted.py\0"
    observer = DbtGitStateObserver(runner(status))

    first = observer.observe(root, target_name="dev")

    assert first is not None
    assert first.git_commit == COMMIT.decode()
    assert first.dirty is True
    assert first.target_name == "dev"
    assert first.working_tree_fingerprint is not None
    assert first.working_tree_fingerprint.startswith("sha256:")
    assert "changed.py" not in repr(first)
    assert "private body" not in repr(first)
    assert observer.observe(root, target_name="dev") == first

    root.joinpath("changed.py").write_text("second private body")
    second = observer.observe(root, target_name="dev")
    assert second is not None
    assert second.working_tree_fingerprint != first.working_tree_fingerprint


def test_dbt_git_state_is_clean_bounded_and_failure_isolated(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    clean = DbtGitStateObserver(runner(b"")).observe(root)
    assert clean is not None and clean.dirty is False
    secret_target = DbtGitStateObserver(runner(b"")).observe(
        root, target_name="sk-abcdefghijklmnopqrstuvwxyz"
    )
    assert secret_target is not None and secret_target.target_name is None

    assert DbtGitStateObserver(runner(b"?? ../outside\0")).observe(root) is None
    outside = tmp_path.parent / "outside-dbt-state"
    root.joinpath("escape").symlink_to(outside)
    assert DbtGitStateObserver(runner(b"?? escape\0")).observe(root) is None
    outside.mkdir(exist_ok=True)
    outside.joinpath("nested.py").write_text("outside private body")
    assert DbtGitStateObserver(runner(b"?? escape/nested.py\0")).observe(root) is None
    assert DbtGitStateObserver(lambda _arguments, _root: None).observe(root) is None
