"""Fresh-process entry for one replay case (spec 2026-10-02 §8.2).

It loads what a real hook process loads first, the CLI module, and then only the replay code,
which imports nothing of the typed step. The cold typed-module import therefore happens inside
the timed hook call, in the hook's mode read, exactly as in a real hook process, and its time
is part of the measured hook time; nothing is added on top. Usage (one JSON ``ReplayRequest``
on stdin)::

    python -m scripts.typed_decision_replay_child [--live-calls-authorized]
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

import mnemo_memory.apps.cli.main  # noqa: F401  (a real hook process loads the CLI first)
from scripts import run_typed_decision_replay


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    return run_typed_decision_replay.main(["--child", *arguments])


if __name__ == "__main__":
    raise SystemExit(main())
