"""Light entry point for the background code-map refresh.

``python -P -m mnemo_memory.apps.cli.source_refresh_entry --data-dir D --project-root R``

The hook starts it detached after building its output (spec 2026-10-03 §2-§3). Like
``judge_entry`` it loads neither typer nor the CLI module, prints nothing, and returns 0
whatever happens, so a broken refresh never reaches a session. Arguments are read by hand so a
bad one stays silent too.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path


def _arguments(argv: Sequence[str]) -> tuple[Path, Path] | None:
    """``(data_dir, project_root)`` for exactly ``--data-dir D --project-root R``, either order."""

    if len(argv) != 4:
        return None
    values = dict(zip(argv[0::2], argv[1::2], strict=True))
    if set(values) != {"--data-dir", "--project-root"}:
        return None
    data_dir = Path(values["--data-dir"])
    project_root = Path(values["--project-root"])
    if not data_dir.is_absolute() or not project_root.is_absolute():
        return None
    return data_dir, project_root


def main(argv: Sequence[str] | None = None) -> int:
    try:
        parsed = _arguments(sys.argv[1:] if argv is None else argv)
        if parsed is not None:
            from mnemo_memory.connectors.automatic_memory import source_refresh

            source_refresh.run_source_refresh(*parsed)
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
