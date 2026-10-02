"""Fresh-process entry for one replay case (spec 2026-10-02 §8.2).

A real hook process loads the CLI module and then, only on the typed path, imports the typed
hook module inside the hook call. This entry loads the CLI module first, times that cold import
before anything else can load it, and hands the time to ``run_typed_decision_replay --child``,
which adds it to a typed case's hook time. Usage (one JSON ``ReplayRequest`` on stdin)::

    python -m scripts.typed_decision_replay_child [--live-calls-authorized]
"""

from __future__ import annotations

import importlib
import sys
import time
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    importlib.import_module("mnemo_memory.apps.cli.main")
    started = time.perf_counter()
    importlib.import_module("mnemo_memory.apps.cli.typed_decision_hook")
    typed_import_ms = round((time.perf_counter() - started) * 1_000)
    from scripts import run_typed_decision_replay

    arguments = list(sys.argv[1:] if argv is None else argv)
    return run_typed_decision_replay.main(["--child", *arguments], typed_import_ms=typed_import_ms)


if __name__ == "__main__":
    raise SystemExit(main())
