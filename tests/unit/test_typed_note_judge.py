"""The background note judge: lock, pacing, retries and recording (spec 2026-10-03 §4)."""

from __future__ import annotations

import math
import os
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from uuid import UUID

from mnemo_memory.apps.cli.typed_decision_composition import (
    build_runtime_typed_decision_classifier,
)
from mnemo_memory.apps.cli.typed_decision_hook import FillerCandidate, filler_verdict_key
from mnemo_memory.apps.cli.typed_note_judge import (
    JUDGE_DEADLINE_SECONDS,
    JUDGE_IN_FLIGHT,
    JUDGE_NOTES_PER_RUN,
    JudgeTally,
    run_note_judge,
)
from mnemo_memory.packages.application import PersonalSettings
from mnemo_memory.packages.domain import (
    MemoryScope,
    ModelBudgetReservation,
    ModelTaskType,
    OwnerId,
    ProjectId,
    ScopeLevel,
    TypedDecisionDataRoute,
    TypedDecisionSource,
    TypedDecisionUnavailableReason,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.model_gateway.cascade_router import ClassifierAxis, ClassifierResult
from mnemo_memory.packages.model_gateway.typed_decisions import (
    AdapterAnswer,
    GuardedTypedDecisionClassifier,
    TypedDecisionAdapterError,
)
from mnemo_memory.packages.storage import (
    LocalNoteJudgeQueue,
    LocalNoteVerdictCache,
    NoteVerdictState,
)

MODEL = "jev-1.13.0"
FAKE_KEY = "test-key-not-real-0000"
SCOPE = MemoryScope(
    OwnerId.from_string("00000000-0000-4000-8000-000000000001"),
    ScopeLevel.PROJECT,
    Visibility.PROJECT,
    WorkspaceId.from_string("00000000-0000-4000-8001-000000000001"),
    ProjectId.from_string("00000000-0000-4000-8002-000000000001"),
)
OTHER_SCOPE = MemoryScope(
    OwnerId.from_string("00000000-0000-4000-8000-000000000001"),
    ScopeLevel.PROJECT,
    Visibility.PROJECT,
    WorkspaceId.from_string("00000000-0000-4000-8001-000000000001"),
    ProjectId.from_string("00000000-0000-4000-8002-000000000002"),
)


def _id(index: int) -> str:
    return f"approved-episodic:{UUID(int=index)}"


class NoteAdapter:
    """Answer the filler question: text with FILLER is filler. Tracks concurrency."""

    provider_id = "fake"
    model_id = MODEL

    def __init__(self, *, delay: float = 0.0, version: str = MODEL, fail: bool = False) -> None:
        self.delay = delay
        self.version = version
        self.fail = fail
        self.texts: list[str] = []
        self.active = 0
        self.peak = 0
        self._lock = threading.Lock()

    def answer(
        self, axes: tuple[ClassifierAxis, ...], text: str, *, timeout_seconds: float
    ) -> AdapterAnswer:
        with self._lock:
            self.texts.append(text)
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            if self.delay:
                time.sleep(self.delay)
            if self.fail:
                raise TypedDecisionAdapterError(TypedDecisionUnavailableReason.HTTP_ERROR)
            p_filler = 0.95 if "FILLER" in text else 0.05
            label = "filler" if p_filler >= 0.5 else "task_information"
            confidence = max(p_filler, 1.0 - p_filler)
            result = ClassifierResult(
                axes[0].name, label, math.log(confidence), p_filler, confidence=confidence
            )
            return AdapterAnswer((result,), self.version, 10)
        finally:
            with self._lock:
                self.active -= 1


class AllowBudget:
    def reserve(
        self,
        workspace_id: WorkspaceId,
        task_type: ModelTaskType,
        reservation: ModelBudgetReservation,
    ) -> None:
        return None


def _guard(
    adapter: NoteAdapter, source: TypedDecisionSource = TypedDecisionSource.SYNTHETIC_FIXTURE
) -> GuardedTypedDecisionClassifier:
    return GuardedTypedDecisionClassifier(
        adapter,
        data_route=TypedDecisionDataRoute.SYNTHETIC_ONLY,
        source=source,
        budget=AllowBudget(),
        workspace_id=WorkspaceId(UUID(int=0)),
        reservation=ModelBudgetReservation(input_tokens=1_000, output_tokens=1, cost_microusd=0),
        deadline_seconds=JUDGE_DEADLINE_SECONDS,
    )


class Notes:
    """A fake scoped store: item ID -> text, served in ``scope`` only; records every read."""

    def __init__(self, texts: dict[str, str], scope: MemoryScope = SCOPE) -> None:
        self.texts = dict(texts)
        self.scope = scope
        self.reads: list[tuple[MemoryScope, tuple[str, ...]]] = []

    def __call__(
        self, scope: MemoryScope, item_ids: tuple[str, ...]
    ) -> tuple[FillerCandidate, ...]:
        self.reads.append((scope, item_ids))
        if scope != self.scope:
            return ()
        return tuple(
            FillerCandidate(item_id, self.texts[item_id])
            for item_id in item_ids
            if item_id in self.texts
        )


def _run(
    directory: Path,
    notes: Notes,
    adapter: NoteAdapter,
    source: TypedDecisionSource = TypedDecisionSource.SYNTHETIC_FIXTURE,
) -> JudgeTally | None:
    return run_note_judge(
        directory,
        model_version=MODEL,
        read_notes=notes,
        build_guard=lambda: _guard(adapter, source),
    )


def _state(directory: Path, item_id: str, text: str) -> NoteVerdictState:
    key = filler_verdict_key(FillerCandidate(item_id, text), MODEL)
    return LocalNoteVerdictCache(directory).states([key])[0]


def test_the_judge_records_each_queued_notes_verdict(tmp_path: Path) -> None:
    notes = Notes({_id(1): "Keep ledger order.", _id(2): "FILLER chatter about lunch."})
    LocalNoteJudgeQueue(tmp_path).append(SCOPE, (_id(1), _id(2)))
    assert _run(tmp_path, notes, NoteAdapter()) == JudgeTally(2, 2, 0, False)
    assert _state(tmp_path, _id(1), "Keep ledger order.") == NoteVerdictState(0.05, 0)
    assert _state(tmp_path, _id(2), "FILLER chatter about lunch.") == NoteVerdictState(0.95, 0)
    assert notes.reads == [(SCOPE, (_id(1), _id(2)))]
    assert LocalNoteJudgeQueue(tmp_path).length() == 0


def test_at_most_four_requests_are_in_flight(tmp_path: Path) -> None:
    texts = {_id(index): f"note {index}" for index in range(10)}
    LocalNoteJudgeQueue(tmp_path).append(SCOPE, tuple(texts))
    adapter = NoteAdapter(delay=0.2)
    tally = _run(tmp_path, Notes(texts), adapter)
    assert tally is not None and tally.answered == 10
    assert adapter.peak == JUDGE_IN_FLIGHT == 4


def test_a_run_judges_at_most_thirty_two_notes(tmp_path: Path) -> None:
    texts = {_id(index): f"note {index}" for index in range(40)}
    queue = LocalNoteJudgeQueue(tmp_path)
    queue.append(SCOPE, tuple(texts))
    adapter = NoteAdapter()
    tally = _run(tmp_path, Notes(texts), adapter)
    assert tally is not None and tally.asked == JUDGE_NOTES_PER_RUN == 32
    assert len(adapter.texts) == 32 and queue.length() == 8


def test_a_second_judge_exits_at_once(tmp_path: Path) -> None:
    queue = LocalNoteJudgeQueue(tmp_path)
    queue.append(SCOPE, (_id(1),))
    adapter = NoteAdapter()
    notes = Notes({_id(1): "note"})
    with queue.run_lock() as held:
        assert held is True
        assert _run(tmp_path, notes, adapter) is None
    assert adapter.texts == [] and notes.reads == [] and queue.length() == 1


def test_three_failed_attempts_stop_retries_until_the_text_changes(tmp_path: Path) -> None:
    queue = LocalNoteJudgeQueue(tmp_path)
    notes = Notes({_id(1): "an unlucky note"})
    failing = NoteAdapter(fail=True)
    for attempt in (1, 2, 3, 4):
        queue.append(SCOPE, (_id(1),))
        _run(tmp_path, notes, failing)
        assert _state(tmp_path, _id(1), "an unlucky note").attempts == min(attempt, 3)
    assert len(failing.texts) == 3  # the fourth run skipped it
    notes.texts[_id(1)] = "an unlucky note, edited"
    queue.append(SCOPE, (_id(1),))
    answering = NoteAdapter()
    _run(tmp_path, notes, answering)
    assert answering.texts == ["an unlucky note, edited"]
    assert _state(tmp_path, _id(1), "an unlucky note, edited") == NoteVerdictState(0.05, 0)


def test_a_policy_block_stops_the_run_and_counts_no_attempt(tmp_path: Path) -> None:
    LocalNoteJudgeQueue(tmp_path).append(SCOPE, (_id(1), _id(2)))
    adapter = NoteAdapter()
    notes = Notes({_id(1): "one", _id(2): "two"})
    tally = _run(tmp_path, notes, adapter, TypedDecisionSource.RUNTIME)
    assert tally is not None and tally.blocked and tally.answered == tally.failed == 0
    assert adapter.texts == []
    assert _state(tmp_path, _id(1), "one") == NoteVerdictState(None, 0)
    assert LocalNoteVerdictCache(tmp_path).entry_count() == 0


def test_a_missing_guard_reads_and_takes_nothing(tmp_path: Path) -> None:
    queue = LocalNoteJudgeQueue(tmp_path)
    queue.append(SCOPE, (_id(1),))
    notes = Notes({_id(1): "note"})
    tally = run_note_judge(
        tmp_path, model_version=MODEL, read_notes=notes, build_guard=lambda: None
    )
    assert tally == JudgeTally(blocked=True)
    assert notes.reads == [] and queue.length() == 1


def test_an_answer_from_another_model_is_a_failed_attempt(tmp_path: Path) -> None:
    LocalNoteJudgeQueue(tmp_path).append(SCOPE, (_id(1),))
    tally = _run(tmp_path, Notes({_id(1): "note"}), NoteAdapter(version="jev-9.9.9"))
    assert tally == JudgeTally(1, 0, 1, False)
    assert _state(tmp_path, _id(1), "note") == NoteVerdictState(None, 1)


def test_a_note_already_judged_is_not_asked_again(tmp_path: Path) -> None:
    key = filler_verdict_key(FillerCandidate(_id(1), "note"), MODEL)
    LocalNoteVerdictCache(tmp_path).record([(key, 0.2)])
    LocalNoteJudgeQueue(tmp_path).append(SCOPE, (_id(1),))
    adapter = NoteAdapter()
    assert _run(tmp_path, Notes({_id(1): "note"}), adapter) == JudgeTally()
    assert adapter.texts == []


def test_a_deleted_note_or_an_unserved_scope_is_skipped(tmp_path: Path) -> None:
    """Review focus 4: a note gone before the judge runs is drained, not retried or fatal."""

    queue = LocalNoteJudgeQueue(tmp_path)
    queue.append(SCOPE, (_id(1), _id(2)))
    queue.append(OTHER_SCOPE, (_id(3),))
    adapter = NoteAdapter()
    tally = _run(tmp_path, Notes({_id(2): "still here"}), adapter)  # _id(1) was deleted
    assert tally == JudgeTally(1, 1, 0, False)
    assert adapter.texts == ["still here"] and queue.length() == 0
    assert LocalNoteVerdictCache(tmp_path).entry_count() == 1


def test_an_exception_in_the_reader_skips_only_its_scope(tmp_path: Path) -> None:
    queue = LocalNoteJudgeQueue(tmp_path)
    queue.append(OTHER_SCOPE, (_id(3),))
    queue.append(SCOPE, (_id(2),))

    def reader(scope: MemoryScope, item_ids: tuple[str, ...]) -> tuple[FillerCandidate, ...]:
        if scope == OTHER_SCOPE:
            raise RuntimeError("synthetic storage failure")
        return (FillerCandidate(_id(2), "still here"),)

    tally = run_note_judge(
        tmp_path, model_version=MODEL, read_notes=reader, build_guard=lambda: _guard(NoteAdapter())
    )
    assert tally == JudgeTally(1, 1, 0, False)


class NeverTransport:
    """A Jev transport that must never be called; counts any call that slips through."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> bytes:
        self.calls += 1
        raise AssertionError("a real note must never reach the network")


def test_the_runtime_guard_sends_nothing_under_synthetic_only(tmp_path: Path) -> None:
    """Controller ruling: the judge's composition-built runtime guard makes zero transport calls."""

    transport = NeverTransport()
    settings = PersonalSettings(experimental_typed_decisions_enabled=True)
    assert settings.typed_decision_data_route == TypedDecisionDataRoute.SYNTHETIC_ONLY.value
    LocalNoteJudgeQueue(tmp_path).append(SCOPE, (_id(1), _id(2)))

    def build() -> GuardedTypedDecisionClassifier | None:
        return build_runtime_typed_decision_classifier(
            settings,
            data_directory=tmp_path,
            environ={"TYPESAFE_API_KEY": FAKE_KEY},
            jev_transport=transport,
            deadline_seconds=JUDGE_DEADLINE_SECONDS,
        )

    tally = run_note_judge(
        tmp_path,
        model_version=MODEL,
        read_notes=Notes({_id(1): "one", _id(2): "FILLER two"}),
        build_guard=build,
    )
    assert tally is not None and tally.blocked and tally.answered == tally.failed == 0
    assert transport.calls == 0
    assert LocalNoteVerdictCache(tmp_path).entry_count() == 0


def test_a_run_lock_that_cannot_be_taken_exits_quietly(tmp_path: Path) -> None:
    """Controller ruling: an OSError from the run lock never escapes the judge."""

    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")
    adapter = NoteAdapter()
    assert _run(blocker / "data", Notes({}), adapter) is None  # mkdir fails
    (tmp_path / ".typed-decision-judge.lock").symlink_to(tmp_path / "elsewhere")
    assert _run(tmp_path, Notes({}), adapter) is None  # O_NOFOLLOW refuses the symlink
    assert adapter.texts == []


def test_an_unsafe_queue_ends_the_run_quietly(tmp_path: Path) -> None:
    """Controller ruling: an OSError from the queue never escapes the judge."""

    os.symlink(tmp_path / "elsewhere.json", tmp_path / "typed-decision-judge-queue.json")
    adapter = NoteAdapter()
    notes = Notes({_id(1): "note"})
    assert _run(tmp_path, notes, adapter) == JudgeTally()
    assert adapter.texts == [] and notes.reads == []
