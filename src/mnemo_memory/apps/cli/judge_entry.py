"""Light entry point for the background note judge: ``python -P -m ...judge_entry --data-dir D``.

It does what ``typed-decisions judge-notes`` does without importing typer or the CLI module.
It prints nothing and returns 0 whatever happens, so a broken judge never reaches a session.
Arguments are read by hand so a bad one stays silent too.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path


def _data_directory(argv: Sequence[str]) -> tuple[bool, Path | None]:
    """``(ok, data_dir)`` for no arguments, ``--data-dir D`` or ``--data-dir=D``; else not ok."""

    if not argv:
        return True, None
    if len(argv) == 2 and argv[0] == "--data-dir":
        return True, Path(argv[1])
    if len(argv) == 1 and argv[0].startswith("--data-dir="):
        return True, Path(argv[0].removeprefix("--data-dir="))
    return False, None


def main(argv: Sequence[str] | None = None) -> int:
    try:
        ok, data_dir = _data_directory(sys.argv[1:] if argv is None else argv)
        if ok:
            from mnemo_memory.apps.cli.typed_note_judge_runner import run_queued_note_judge

            run_queued_note_judge(data_dir)
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
