"""Stat-only working-tree fingerprint: detect 'nothing changed' without reading bytes."""

from __future__ import annotations

from pathlib import Path

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
