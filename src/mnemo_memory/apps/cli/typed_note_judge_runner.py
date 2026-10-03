"""Run the background note judge from a data directory: the one body behind both entry points.

``typed-decisions judge-notes`` (hidden) and ``judge_entry`` call ``run_queued_note_judge``. It
wires the real reader and the 5 s runtime guard into ``run_note_judge``; every gate (master
switch, data route, pinned model id, run lock, budget) is applied there and in the guard. The
Jev connector is reached only through the runtime composition, imported at call time.
"""

from __future__ import annotations

from pathlib import Path

from mnemo_memory.apps.cli.typed_decision_hook import FillerCandidate
from mnemo_memory.apps.cli.typed_note_judge import (
    JUDGE_DEADLINE_SECONDS,
    run_note_judge,
)
from mnemo_memory.apps.cli.typed_note_reader import note_candidates_by_id
from mnemo_memory.packages.application import PersonalSettingsStore, resolve_local_config
from mnemo_memory.packages.domain import MemoryScope
from mnemo_memory.packages.model_gateway.typed_decisions import GuardedTypedDecisionClassifier


def run_queued_note_judge(data_dir: Path | None) -> None:
    """Judge the queued notes once; any failure may escape (callers swallow it)."""

    from mnemo_memory.apps.cli.typed_decision_composition import (
        build_runtime_typed_decision_classifier,
    )

    data_directory = resolve_local_config(data_dir).data_directory
    settings = PersonalSettingsStore(data_directory).load()

    def read(scope: MemoryScope, item_ids: tuple[str, ...]) -> tuple[FillerCandidate, ...]:
        return note_candidates_by_id(data_directory, scope, item_ids)

    def guard() -> GuardedTypedDecisionClassifier | None:
        return build_runtime_typed_decision_classifier(
            settings, data_directory=data_directory, deadline_seconds=JUDGE_DEADLINE_SECONDS
        )

    run_note_judge(
        data_directory,
        model_version=settings.typed_decision_model_id,
        read_notes=read,
        build_guard=guard,
    )
