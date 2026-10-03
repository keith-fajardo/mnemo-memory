"""Background note judge: fills the verdict cache after the prompt (spec 2026-10-03 §4).

The prompt hook queues note IDs, never text, and starts the light ``judge_entry`` module in a new
session. ``run_note_judge`` drains that queue under a single-instance lock: it re-reads each
note through a caller-supplied scoped reader, skips notes that already have a usable verdict or
three failed attempts on the same text, and asks Jev through a caller-built guard: at most four
requests sent per batch (timed-out threads from an earlier batch may still be open), and at
most 32 notes per run (it may take up to 256 queue entries). ``judge_candidates`` is the shared
core; the replay's priming pass uses it with the synthetic guard. This module never imports the Jev
connector, builds a guard, prints, or touches any file but the two stores.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from mnemo_memory.apps.cli.typed_decision_hook import (
    FillerCandidate,
    filler_probability,
    filler_verdict_key,
    is_pinned_model_id,
)
from mnemo_memory.packages.domain import MemoryScope, TypedDecisionUnavailableReason
from mnemo_memory.packages.model_gateway.decision_axes import NOTE_SUBSTANCE
from mnemo_memory.packages.model_gateway.typed_decisions import GuardedTypedDecisionClassifier
from mnemo_memory.packages.storage import (
    MAXIMUM_QUEUED_NOTES,
    LocalNoteJudgeQueue,
    LocalNoteVerdictCache,
    QueuedNote,
)

JUDGE_DEADLINE_SECONDS = 5.0
JUDGE_IN_FLIGHT = 4
JUDGE_NOTES_PER_RUN = 32
# Reasons that hold for every note alike: the run stops and records no attempt.
_POLICY_BLOCKS = frozenset(
    {
        TypedDecisionUnavailableReason.DISABLED,
        TypedDecisionUnavailableReason.DATA_ROUTE_BLOCKED,
        TypedDecisionUnavailableReason.NO_CREDENTIAL,
        TypedDecisionUnavailableReason.BUDGET_DENIED,
    }
)

NoteReader = Callable[[MemoryScope, tuple[str, ...]], tuple[FillerCandidate, ...]]
GuardBuilder = Callable[[], GuardedTypedDecisionClassifier | None]


@dataclass(frozen=True, slots=True)
class JudgeTally:
    """Content-free counts for one judging pass."""

    asked: int = 0
    answered: int = 0
    failed: int = 0
    blocked: bool = False

    def plus(self, other: JudgeTally) -> JudgeTally:
        return JudgeTally(
            self.asked + other.asked,
            self.answered + other.answered,
            self.failed + other.failed,
            self.blocked or other.blocked,
        )


def judge_candidates(
    guard: GuardedTypedDecisionClassifier,
    candidates: Sequence[FillerCandidate],
    *,
    cache: LocalNoteVerdictCache,
    model_version: str,
    in_flight: int = JUDGE_IN_FLIGHT,
) -> JudgeTally:
    """Judge ``candidates`` in batches of ``in_flight`` and record every outcome.

    Each batch goes out together under the 5 s deadline, in one ``asyncio.run`` for the whole
    pass. An answer from the pinned ``model_version`` is a verdict; any other unanswered note is
    one failed attempt; a policy block stops the pass and records nothing for that note.
    """

    pending = tuple(candidates)
    if not pending:
        return JudgeTally()
    coroutine = _judge(guard, pending, cache, model_version, in_flight)
    try:
        return asyncio.run(coroutine)
    finally:
        coroutine.close()  # no "never awaited" warning when a loop is already running


async def _judge(
    guard: GuardedTypedDecisionClassifier,
    candidates: tuple[FillerCandidate, ...],
    cache: LocalNoteVerdictCache,
    model_version: str,
    in_flight: int,
) -> JudgeTally:
    tally = JudgeTally()
    for start in range(0, len(candidates), in_flight):
        batch = candidates[start : start + in_flight]
        outcomes = await guard.ask_each(
            [((NOTE_SUBSTANCE,), candidate.text) for candidate in batch],
            total_deadline_seconds=JUDGE_DEADLINE_SECONDS,
        )
        blocked = False
        recorded: list[tuple[str, float | None]] = []
        for candidate, outcome in zip(batch, outcomes, strict=True):
            if outcome.unavailable_reason in _POLICY_BLOCKS:
                blocked = True
                continue
            pinned = outcome.model_version == model_version
            p_filler = filler_probability(outcome) if pinned else None
            recorded.append((filler_verdict_key(candidate, model_version), p_filler))
        cache.record(recorded)
        answered = sum(p_filler is not None for _, p_filler in recorded)
        tally = tally.plus(JudgeTally(len(batch), answered, len(recorded) - answered, blocked))
        if blocked:
            break
    return tally


def run_note_judge(
    data_directory: Path,
    *,
    model_version: str,
    read_notes: NoteReader,
    build_guard: GuardBuilder,
    limit: int = JUDGE_NOTES_PER_RUN,
) -> JudgeTally | None:
    """One background run; ``None`` when no run happened because the lock was not taken.

    The run lock alone decides whether this judge may run: ``None`` when another judge holds
    it, or when the lock cannot be taken at all (an unsafe path, ``mkdir`` or ``flock``
    failing). No guard (the master switch is off) means nothing is read or taken. The run ends
    when the queue is empty, ``limit`` notes were asked, a policy block stops it, it has taken
    a whole queue's worth of entries, or the queue or cache files fail; an ``OSError`` from
    them never escapes, so the judge exits silently (spec §4). An unpinned ``model_version``
    (not ``jev-X.Y.Z``) asks nothing and takes nothing: the hook never queues for one.
    """

    if not is_pinned_model_id(model_version):
        return JudgeTally(blocked=True)
    try:
        queue = LocalNoteJudgeQueue(data_directory)
        with queue.run_lock() as acquired:
            if not acquired:
                return None
            guard = build_guard()
            if guard is None:
                return JudgeTally(blocked=True)
            return _drain(queue, guard, data_directory, model_version, read_notes, limit)
    except OSError:
        return None


def _drain(
    queue: LocalNoteJudgeQueue,
    guard: GuardedTypedDecisionClassifier,
    data_directory: Path,
    model_version: str,
    read_notes: NoteReader,
    limit: int,
) -> JudgeTally:
    """Take, re-read and judge queued notes under the held run lock; file failures end it."""

    tally = JudgeTally()
    taken_total = 0
    try:
        cache = LocalNoteVerdictCache(data_directory)
        while tally.asked < limit and not tally.blocked and taken_total < MAXIMUM_QUEUED_NOTES:
            taken = queue.take(min(limit - tally.asked, JUDGE_IN_FLIGHT))
            if not taken:
                break
            taken_total += len(taken)
            candidates = _unjudged(_read(taken, read_notes), cache, model_version)
            tally = tally.plus(
                judge_candidates(guard, candidates, cache=cache, model_version=model_version)
            )
    except OSError:
        pass  # an unsafe or unwritable store; a later prompt re-queues these notes
    return tally


def _read(taken: Sequence[QueuedNote], read_notes: NoteReader) -> tuple[FillerCandidate, ...]:
    """Re-read each note in its own scope; an unreadable scope is skipped, never fatal."""

    by_scope: dict[MemoryScope, list[str]] = {}
    for note in taken:
        by_scope.setdefault(note.scope, []).append(note.item_id)
    candidates: list[FillerCandidate] = []
    for scope, item_ids in by_scope.items():
        try:
            candidates.extend(read_notes(scope, tuple(item_ids)))
        except Exception:
            continue  # a later prompt re-queues these notes
    return tuple(candidates)


def _unjudged(
    candidates: Sequence[FillerCandidate], cache: LocalNoteVerdictCache, model_version: str
) -> tuple[FillerCandidate, ...]:
    keys = tuple(filler_verdict_key(candidate, model_version) for candidate in candidates)
    states = cache.states(keys)
    return tuple(
        candidate
        for candidate, state in zip(candidates, states, strict=True)
        if state.needs_judging
    )
