"""The judge queue: IDs and scope only, deduplicated, capped, and one judge at a time."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest

from mnemo_memory.packages.domain import (
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.storage import (
    MAXIMUM_QUEUED_NOTES,
    LocalNoteJudgeQueue,
    QueuedNote,
)
from mnemo_memory.packages.storage.local_note_judge_queue import (
    JUDGE_QUEUE_FILE,
    JUDGE_RUN_LOCK_FILE,
)

SCOPE = MemoryScope(
    OwnerId.from_string("00000000-0000-4000-8000-000000000001"),
    ScopeLevel.PROJECT,
    Visibility.PROJECT,
    WorkspaceId.from_string("00000000-0000-4000-8001-000000000001"),
    ProjectId.from_string("00000000-0000-4000-8002-000000000001"),
)
OTHER = replace(SCOPE, project_id=ProjectId.from_string("00000000-0000-4000-8002-000000000002"))
_HOLD_AND_DIE = (
    "import fcntl, os, sys\n"
    "descriptor = os.open(sys.argv[1], os.O_CREAT | os.O_RDWR, 0o600)\n"
    "fcntl.flock(descriptor, fcntl.LOCK_EX)\n"
    "print('held', flush=True)\n"
    "sys.stdin.readline()\n"
    "os._exit(9)\n"
)


def _id(index: int) -> str:
    return f"approved-episodic:{UUID(int=index)}"


def test_append_queues_each_note_once_and_take_returns_the_oldest_first(tmp_path: Path) -> None:
    queue = LocalNoteJudgeQueue(tmp_path)
    assert queue.path.name == JUDGE_QUEUE_FILE == "typed-decision-judge-queue.json"
    assert queue.append(SCOPE, (_id(1), _id(2), _id(1))) == 2
    assert queue.append(SCOPE, (_id(2), _id(3))) == 3
    assert queue.append(OTHER, (_id(1),)) == 4  # the same ID in another scope is another note
    assert queue.take(2) == (QueuedNote(_id(1), SCOPE), QueuedNote(_id(2), SCOPE))
    assert queue.length() == 2
    assert queue.take(10) == (QueuedNote(_id(3), SCOPE), QueuedNote(_id(1), OTHER))
    assert queue.take(1) == ()
    assert queue.length() == 0


def test_the_queue_keeps_the_newest_256_notes(tmp_path: Path) -> None:
    queue = LocalNoteJudgeQueue(tmp_path)
    assert queue.append(SCOPE, tuple(_id(index) for index in range(300))) == 256
    assert MAXIMUM_QUEUED_NOTES == 256
    assert queue.take(1) == (QueuedNote(_id(44), SCOPE),)


def test_the_queue_file_holds_ids_and_scope_only(tmp_path: Path) -> None:
    queue = LocalNoteJudgeQueue(tmp_path)
    queue.append(SCOPE, (_id(1),))
    stored = json.loads(queue.path.read_text("utf-8"))
    assert stored == {"version": 1, "entries": [{"item_id": _id(1), "scope": SCOPE.to_dict()}]}
    assert queue.path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "stored",
    [
        '{"version": 1, "entries": ',
        json.dumps({"version": 2, "entries": []}),
        json.dumps({"version": 1, "entries": {}}),
        json.dumps({"version": 1, "entries": [{"item_id": "has space", "scope": {}}]}),
        json.dumps({"version": 1, "entries": [{"item_id": "x", "scope": {"owner_id": "no"}}]}),
    ],
)
def test_a_corrupt_queue_reads_as_empty_and_starts_afresh(tmp_path: Path, stored: str) -> None:
    queue = LocalNoteJudgeQueue(tmp_path)
    queue.path.write_text(stored, "utf-8")
    assert queue.length() is None
    assert queue.take(4) == ()
    assert queue.append(SCOPE, (_id(1),)) == 1
    assert queue.take(4) == (QueuedNote(_id(1), SCOPE),)


def test_a_symlinked_queue_is_never_written_through(tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text("{}", "utf-8")
    data = tmp_path / "data"
    data.mkdir()
    queue = LocalNoteJudgeQueue(data)
    queue.path.symlink_to(outside)
    assert queue.length() is None
    with pytest.raises(OSError):
        queue.append(SCOPE, (_id(1),))
    assert outside.read_text("utf-8") == "{}"


@pytest.mark.parametrize("item_id", ["", "has space", "x" * 300, "../escape", "note\ntext"])
def test_append_refuses_anything_that_is_not_an_item_id(tmp_path: Path, item_id: str) -> None:
    with pytest.raises(ValueError):
        LocalNoteJudgeQueue(tmp_path).append(SCOPE, (item_id,))
    assert not LocalNoteJudgeQueue(tmp_path).path.exists()


@pytest.mark.parametrize("limit", [0, -1, True])
def test_take_needs_a_positive_limit(tmp_path: Path, limit: int) -> None:
    with pytest.raises(ValueError):
        LocalNoteJudgeQueue(tmp_path).take(limit)


def test_concurrent_appends_and_takes_lose_nothing_and_never_repeat(tmp_path: Path) -> None:
    errors: list[BaseException] = []

    def appender(index: int) -> None:
        try:
            queue = LocalNoteJudgeQueue(tmp_path)
            for number in range(25):
                queue.append(SCOPE, (_id(index * 100 + number),))
        except BaseException as error:  # pragma: no cover - surfaced below
            errors.append(error)

    threads = [threading.Thread(target=appender, args=(index,)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == [] and LocalNoteJudgeQueue(tmp_path).length() == 100

    taken: list[QueuedNote] = []
    lock = threading.Lock()

    def taker() -> None:
        queue = LocalNoteJudgeQueue(tmp_path)
        while batch := queue.take(3):
            with lock:
                taken.extend(batch)

    takers = [threading.Thread(target=taker) for _ in range(6)]
    for thread in takers:
        thread.start()
    for thread in takers:
        thread.join()
    assert len(taken) == len(set(taken)) == 100


def test_the_run_lock_admits_one_judge_and_status_can_see_it(tmp_path: Path) -> None:
    queue = LocalNoteJudgeQueue(tmp_path)
    assert queue.judge_running() is False
    assert not (tmp_path / JUDGE_RUN_LOCK_FILE).exists()  # reading never creates it
    with queue.run_lock() as first:
        assert first is True
        assert queue.judge_running() is True
        with LocalNoteJudgeQueue(tmp_path).run_lock() as second:
            assert second is False
    assert queue.judge_running() is False


def test_a_judge_that_died_never_blocks_the_next_one(tmp_path: Path) -> None:
    """Review focus 3: the run lock is an flock, released by the OS when its holder exits."""

    queue = LocalNoteJudgeQueue(tmp_path)
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLD_AND_DIE, str(tmp_path.resolve() / JUDGE_RUN_LOCK_FILE)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdin is not None and holder.stdout is not None
    assert holder.stdout.readline().strip() == "held"
    assert queue.judge_running() is True
    with queue.run_lock() as acquired:
        assert acquired is False
    holder.stdin.write("\n")
    holder.stdin.flush()
    assert holder.wait(timeout=30) == 9
    assert queue.judge_running() is False
    with queue.run_lock() as acquired:
        assert acquired is True
