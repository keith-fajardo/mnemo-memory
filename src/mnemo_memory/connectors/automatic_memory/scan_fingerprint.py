"""Stat-only working-tree fingerprint: detect 'nothing changed' without reading bytes."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from stat import S_ISREG

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
    entries: list[tuple[tuple[str, ...], int, int]] = []
    # Prune skipped directories in place so the walk never enters them (a ``.venv`` or ``.git``
    # can hold most of a tree's entries). Like ``Path.rglob`` here, it never follows symlinks.
    for directory, subdirectories, files in os.walk(root):
        subdirectories[:] = [name for name in subdirectories if name not in _SKIP_DIRECTORIES]
        base = Path(directory).relative_to(root).parts
        for name in files:
            if name in _SKIP_DIRECTORIES:
                continue  # the parser skips any path with such a component, a file name too
            stat = os.lstat(os.path.join(directory, name))
            if S_ISREG(stat.st_mode):  # a symlink or a special file is never fingerprinted
                entries.append(((*base, name), stat.st_size, stat.st_mtime_ns))
    digest = hashlib.sha256()
    # Component-wise order, exactly as sorting the ``Path`` objects did, keeps the digest stable.
    for parts, size, mtime_ns in sorted(entries):
        digest.update(f"{'/'.join(parts)}\0{size}\0{mtime_ns}\n".encode())
    return f"sha256:{digest.hexdigest()}"
