"""The judge queue: note IDs and their scope, waiting for a background verdict (spec §4).

The hook appends the item IDs of notes with no usable verdict; the background judge takes them
oldest first. The file holds item IDs and scope identifiers only, never note text. It is
file-locked, written atomically, deduplicated by ``(item_id, scope)`` and capped at 256
entries (the oldest drop first). A corrupt file reads as empty and the next write replaces it;
a symlinked or non-regular path is never written through. A separate ``flock`` lock file keeps
one judge running at a time; the OS releases it when its holder exits, however it exits.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

from mnemo_memory.packages.domain import MemoryScope

JUDGE_QUEUE_FILE = "typed-decision-judge-queue.json"
JUDGE_RUN_LOCK_FILE = ".typed-decision-judge.lock"
MAXIMUM_QUEUED_NOTES = 256
_MAXIMUM_FILE_BYTES = 262_144
_FORMAT_VERSION = 1
_ITEM_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_-]{0,255}")


class _QueueUnsafe(OSError):
    """The queue path is a symlink or not a regular file; it is never written through."""


@dataclass(frozen=True, slots=True)
class QueuedNote:
    """One note waiting for a verdict: its item ID and the scope to read it in."""

    item_id: str
    scope: MemoryScope

    def __post_init__(self) -> None:
        if not isinstance(self.item_id, str) or _ITEM_ID.fullmatch(self.item_id) is None:
            raise ValueError("queued note item id is invalid")
        if not isinstance(self.scope, MemoryScope):
            raise TypeError("queued note scope is invalid")


class LocalNoteJudgeQueue:
    """The note-judge queue and run lock for one data directory."""

    def __init__(self, data_directory: Path) -> None:
        self._directory = data_directory.expanduser().resolve()
        self.path = self._directory / JUDGE_QUEUE_FILE
        self._lock_path = self._directory / f".{JUDGE_QUEUE_FILE}.lock"
        self._run_lock_path = self._directory / JUDGE_RUN_LOCK_FILE

    def append(self, scope: MemoryScope, item_ids: Sequence[str]) -> int:
        """Queue each note once, keep the newest 256, and return the queue length after."""

        added = tuple(dict.fromkeys(QueuedNote(item_id, scope) for item_id in item_ids))
        with self._lock():
            entries = self._read_or_empty()
            present = set(entries)
            merged = [*entries, *(note for note in added if note not in present)]
            merged = merged[-MAXIMUM_QUEUED_NOTES:]
            if merged != entries:
                self._write(merged)
            return len(merged)

    def take(self, limit: int) -> tuple[QueuedNote, ...]:
        """Remove and return up to ``limit`` notes, oldest first."""

        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("queue take limit must be a positive integer")
        with self._lock():
            entries = self._read_or_empty()
            taken = entries[:limit]
            if taken:
                self._write(entries[limit:])
            return tuple(taken)

    def length(self) -> int | None:
        """How many notes wait, or ``None`` when the queue is unreadable."""

        try:
            return len(self._read())
        except Exception:
            return None

    @contextmanager
    def run_lock(self) -> Iterator[bool]:
        """Hold the single-judge lock for one run; yield ``False`` when another judge has it."""

        self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(self._run_lock_path, _flags(create=True), 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                with suppress(OSError):
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def judge_running(self) -> bool:
        """Whether a judge holds the run lock now; never creates the lock file."""

        try:
            descriptor = os.open(self._run_lock_path, _flags(create=False))
        except OSError:
            return False
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        except OSError:
            return False
        else:
            with suppress(OSError):
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            return False
        finally:
            os.close(descriptor)

    def _read_or_empty(self) -> list[QueuedNote]:
        try:
            return self._read()
        except _QueueUnsafe:
            raise
        except Exception:
            return []  # corrupt: the next write starts the queue afresh

    def _read(self) -> list[QueuedNote]:
        if self.path.is_symlink():
            raise _QueueUnsafe("note judge queue is unsafe")
        if not self.path.exists():
            return []
        if not self.path.is_file():
            raise _QueueUnsafe("note judge queue is unsafe")
        if self.path.stat().st_size > _MAXIMUM_FILE_BYTES:
            raise ValueError("note judge queue is too large")
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or set(value) != {"version", "entries"}:
            raise ValueError("note judge queue is invalid")
        version, entries = value["version"], value["entries"]
        if (
            isinstance(version, bool)
            or version != _FORMAT_VERSION
            or not isinstance(entries, list)
            or len(entries) > MAXIMUM_QUEUED_NOTES
        ):
            raise ValueError("note judge queue is invalid")
        return [_queued(entry) for entry in entries]

    def _write(self, notes: Sequence[QueuedNote]) -> None:
        payload = json.dumps(
            {
                "version": _FORMAT_VERSION,
                "entries": [
                    {"item_id": note.item_id, "scope": note.scope.to_dict()} for note in notes
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        temporary: Path | None = None
        try:
            with NamedTemporaryFile(
                "w", encoding="utf-8", dir=self._directory, delete=False
            ) as handle:
                temporary = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise _QueueUnsafe("note judge queue is unsafe")
        descriptor = os.open(self._lock_path, _flags(create=True), 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            with suppress(OSError):
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def _queued(entry: object) -> QueuedNote:
    if not isinstance(entry, dict) or set(entry) != {"item_id", "scope"}:
        raise ValueError("note judge queue entry is invalid")
    scope = entry["scope"]
    if not isinstance(scope, dict):
        raise ValueError("note judge queue entry is invalid")
    return QueuedNote(entry["item_id"], MemoryScope.from_dict(scope))


def _flags(*, create: bool) -> int:
    flags = os.O_RDWR | (os.O_CREAT if create else 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return flags
