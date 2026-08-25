"""Stat-only working-tree fingerprint: detect 'nothing changed' without reading bytes."""

from __future__ import annotations

import hashlib
from pathlib import Path

# Reuse the parser's own noise-directory skip set (rather than duplicating it) so the
# fingerprint never drifts from what the expensive parse actually walks.
from mnemo_memory.packages.project_index.source_structure import _SKIP_DIRECTORIES


def working_tree_fingerprint(root: Path) -> str:
    """Return a ``sha256:``-prefixed digest over sorted ``(relpath, size, mtime_ns)`` tuples.

    Stat-only: never reads file contents. Skips symlinks and the same noise directories the
    source-structure parser skips, so an unchanged tree yields a stable fingerprint cheaply.

    Accepted boundary: a content edit that preserves both size and mtime (within the
    filesystem's mtime granularity) leaves the fingerprint unchanged, so a re-parse can be
    briefly skipped and the structural index left momentarily stale. This is a fail-safe
    trade-off — structural lookup is a hint the agent can always fall back to a live search
    from — and the fingerprint deliberately covers every file, erring toward unnecessary
    re-parses rather than missed ones.
    """
    root = root.resolve()
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            continue
        rel = path.relative_to(root)
        if any(part in _SKIP_DIRECTORIES for part in rel.parts):
            continue
        if not path.is_file():
            continue
        stat = path.stat()
        digest.update(f"{rel.as_posix()}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode())
    return f"sha256:{digest.hexdigest()}"
