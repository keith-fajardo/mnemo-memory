"""Stat-only working-tree fingerprint: detect 'nothing changed' without reading bytes."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import pytest

from mnemo_memory.connectors.automatic_memory.scan_fingerprint import working_tree_fingerprint


def test_fingerprint_stable_and_change_sensitive(tmp_path: Path) -> None:
    root = tmp_path / "p"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "a.py").write_text("x = 1\n")
    first = working_tree_fingerprint(root)
    assert first.startswith("sha256:")
    assert working_tree_fingerprint(root) == first  # stable
    (root / "pkg" / "b.py").write_text("y = 2\n")
    assert working_tree_fingerprint(root) != first  # new file changes it


def test_fingerprint_skips_noise_dirs(tmp_path: Path) -> None:
    root = tmp_path / "p"
    root.mkdir()
    (root / "a.py").write_text("x = 1\n")
    base = working_tree_fingerprint(root)
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "junk.pyc").write_bytes(b"\x00\x01")
    assert working_tree_fingerprint(root) == base  # ignored dir


def _expected(root: Path, files: list[str]) -> str:
    """The documented format: sorted by path components, ``relpath\\0size\\0mtime_ns\\n``."""
    digest = hashlib.sha256()
    for relative in sorted(files, key=lambda item: tuple(item.split("/"))):
        stat = (root / relative).stat()
        digest.update(f"{relative}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode())
    return f"sha256:{digest.hexdigest()}"


def test_fingerprint_ignores_changes_under_every_skipped_directory(tmp_path: Path) -> None:
    root = tmp_path / "p"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "a.py").write_text("x = 1\n")
    base = working_tree_fingerprint(root)
    for relative in (".venv/x.py", "node_modules/a.js", ".git/HEAD", "pkg/__pycache__/a.pyc"):
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text("noise\n")
    assert working_tree_fingerprint(root) == base
    (root / ".venv" / "x.py").write_text("changed noise, longer than before\n")
    assert working_tree_fingerprint(root) == base


def test_fingerprint_changes_when_a_parsed_file_changes(tmp_path: Path) -> None:
    root = tmp_path / "p"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "a.py").write_text("x = 1\n")
    (root / "Makefile").write_text("all:\n")
    base = working_tree_fingerprint(root)
    (root / "pkg" / "a.py").write_text("x = 12\n")
    edited = working_tree_fingerprint(root)
    assert edited != base
    (root / "Makefile").write_text("all: build\n")
    assert working_tree_fingerprint(root) != edited


def test_fingerprint_keeps_its_digest_format(tmp_path: Path) -> None:
    root = tmp_path / "p"
    files = ["a-b/x.py", "a/x.py", "a/z/y.py", "b.py", "pkg/c.sql"]
    for relative in files:
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(f"# {relative}\n")
    (root / "build").write_text("a file named like a skipped directory\n")
    (root / "link.py").symlink_to(root / "b.py")

    assert working_tree_fingerprint(root) == _expected(root, files)


def test_the_walk_never_descends_into_a_skipped_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "p"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "a.py").write_text("x = 1\n")
    for skipped in (".venv/lib/site", "node_modules/dep", ".git/objects"):
        (root / skipped).mkdir(parents=True)
        (root / skipped / "f.py").write_text("noise\n")
    listed: list[str] = []
    real = os.scandir

    def spy(path: Any = ".") -> Any:
        listed.append(os.fspath(path))
        return real(path)

    monkeypatch.setattr(os, "scandir", spy)
    working_tree_fingerprint(root)

    assert listed
    skipped_names = {".venv", "node_modules", ".git"}
    assert not [item for item in listed if skipped_names & set(Path(item).parts)]
