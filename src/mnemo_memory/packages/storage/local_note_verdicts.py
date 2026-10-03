"""File-locked, content-free cache of Jev note verdicts (spec 2026-10-03 §5).

A note's filler verdict depends only on the note, so it is judged once in the background and
reused by every later prompt. The cache is one JSON file in the data directory. Each entry is
keyed by one SHA-256 over the item ID, the SHA-256 of the exact judged text, the pinned model
version and the filler question version, so the file holds no note text, item ID, model name,
prompt or skill name. A verdict counts for 30 days; a failed-attempt record stays until its
text changes or the size limits evict it. At most 5,000 entries are kept, oldest first out,
and the file stays under 1 MB. A corrupt, unreadable or oversized file reads as empty, so every
note is kept, and the next write rebuilds it. A symlinked or non-regular path is never written.
"""

from __future__ import annotations

import fcntl
import json
import math
import os
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile

NOTE_VERDICTS_FILE = "typed-decision-note-verdicts.json"
NOTE_VERDICT_TTL = timedelta(days=30)
MAXIMUM_NOTE_VERDICTS = 5_000
MAXIMUM_NOTE_VERDICT_ATTEMPTS = 3
MAXIMUM_NOTE_VERDICT_FILE_BYTES = 1_048_576
_FORMAT_VERSION = 1
_KEY_FORMAT = "mnemo.note-verdict-key.v1"
_KEY = re.compile(r"[0-9a-f]{64}")
_CLOCK_SKEW = timedelta(minutes=5)
_MAXIMUM_STORED_ATTEMPTS = 1_000
_MAXIMUM_ITEM_ID_CHARACTERS = 256
_MAXIMUM_MODEL_VERSION_CHARACTERS = 64

# (p_filler, or None after a failed attempt; judged-at UTC epoch seconds; failed attempts)
_Entry = tuple[float | None, int, int]


class _CacheUnsafe(OSError):
    """The cache path is a symlink or not a regular file; it is never written through."""


def note_verdict_key(
    item_id: str, judged_text: str, *, model_version: str, question_version: int
) -> str:
    """The cache key for one exact judged text; any changed key part gives another key."""

    if not isinstance(item_id, str) or not 0 < len(item_id) <= _MAXIMUM_ITEM_ID_CHARACTERS:
        raise ValueError("note verdict item id is invalid")
    if not isinstance(judged_text, str) or not judged_text:
        raise ValueError("note verdict text is invalid")
    if (
        not isinstance(model_version, str)
        or not 0 < len(model_version) <= _MAXIMUM_MODEL_VERSION_CHARACTERS
    ):
        raise ValueError("note verdict model version is invalid")
    if (
        isinstance(question_version, bool)
        or not isinstance(question_version, int)
        or question_version < 1
    ):
        raise ValueError("note verdict question version is invalid")
    text_digest = sha256(judged_text.encode("utf-8")).hexdigest()
    material = json.dumps(
        [_KEY_FORMAT, item_id, text_digest, model_version, question_version],
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class NoteVerdictState:
    """One key's cached state: a usable (fresh) ``p_filler`` and its failed attempts."""

    p_filler: float | None
    attempts: int

    @property
    def needs_judging(self) -> bool:
        """No usable verdict, and fewer than three failed attempts on this exact text."""

        return self.p_filler is None and self.attempts < MAXIMUM_NOTE_VERDICT_ATTEMPTS


_UNKNOWN = NoteVerdictState(None, 0)


class LocalNoteVerdictCache:
    """Read and record note verdicts for one data directory."""

    def __init__(
        self, data_directory: Path, *, clock: Callable[[], datetime] | None = None
    ) -> None:
        self._directory = data_directory.expanduser().resolve()
        self.path = self._directory / NOTE_VERDICTS_FILE
        self._lock_path = self._directory / f".{NOTE_VERDICTS_FILE}.lock"
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))

    def states(self, keys: Sequence[str]) -> tuple[NoteVerdictState, ...]:
        """Each key's state, in order. Never raises: an unreadable cache reads as empty."""

        try:
            now = self._now()
            entries = self._read()
        except Exception:
            return tuple(_UNKNOWN for _ in keys)
        return tuple(_state(entries.get(key), now) for key in keys)

    def record(self, outcomes: Sequence[tuple[str, float | None]]) -> None:
        """Store verdicts (a ``p_filler``) and failed attempts (``None``) under one lock."""

        checked = [
            (_checked_key(key), _checked_probability(p_filler)) for key, p_filler in outcomes
        ]
        if not checked:
            return
        now = self._now()
        stamp = math.floor(now.timestamp())
        with self._lock():
            try:
                entries = self._read()
            except _CacheUnsafe:
                raise
            except Exception:
                entries = {}  # corrupt: the cache is rebuilt from this write on
            for key, p_filler in checked:
                previous = entries.pop(key, None)
                if p_filler is None:
                    attempts = 0 if previous is None else previous[2]
                    entries[key] = (None, stamp, min(attempts + 1, _MAXIMUM_STORED_ATTEMPTS))
                else:
                    entries[key] = (p_filler, stamp, 0)
            self._write(_bounded(entries, now))

    def entry_count(self) -> int | None:
        """How many entries the cache holds, or ``None`` when it is unreadable."""

        try:
            return len(self._read())
        except Exception:
            return None

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("note verdict clock must be timezone-aware")
        return now

    def _read(self) -> dict[str, _Entry]:
        if self.path.is_symlink():
            raise _CacheUnsafe("note verdict cache is unsafe")
        if not self.path.exists():
            return {}
        if not self.path.is_file():
            raise _CacheUnsafe("note verdict cache is unsafe")
        if self.path.stat().st_size > MAXIMUM_NOTE_VERDICT_FILE_BYTES:
            raise ValueError("note verdict cache is too large")
        return _entries(json.loads(self.path.read_text(encoding="utf-8")))

    def _write(self, entries: Mapping[str, _Entry]) -> None:
        payload = _encode(entries)
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
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self._lock_path, flags, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            with suppress(OSError):
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def _state(entry: _Entry | None, now: datetime) -> NoteVerdictState:
    if entry is None:
        return _UNKNOWN
    p_filler, stamp, attempts = entry
    if p_filler is None:
        return NoteVerdictState(None, attempts)
    return NoteVerdictState(p_filler, 0) if _fresh(stamp, now) else _UNKNOWN


def _fresh(stamp: int, now: datetime) -> bool:
    """Judged under 30 days ago; a stamp more than 5 minutes in the future is not trusted."""

    age = now.timestamp() - stamp
    return -_CLOCK_SKEW.total_seconds() <= age < NOTE_VERDICT_TTL.total_seconds()


def _bounded(entries: Mapping[str, _Entry], now: datetime) -> dict[str, _Entry]:
    """Drop stale verdicts, then keep the newest 5,000 entries within 1 MB, oldest first out.

    Failed-attempt records never expire by age, so a note that failed three times stays
    skipped until its text changes; only the size limits evict them.
    """

    live = [
        (key, entry) for key, entry in entries.items() if entry[0] is None or _fresh(entry[1], now)
    ]
    newest = sorted(live, key=lambda item: item[1][1], reverse=True)[:MAXIMUM_NOTE_VERDICTS]
    while newest and len(_encode(dict(newest)).encode("utf-8")) > MAXIMUM_NOTE_VERDICT_FILE_BYTES:
        newest = newest[: len(newest) * 9 // 10]
    return dict(newest)


def _encode(entries: Mapping[str, _Entry]) -> str:
    return json.dumps(
        {
            "version": _FORMAT_VERSION,
            "entries": {key: list(entry) for key, entry in entries.items()},
        },
        separators=(",", ":"),
    )


def _entries(value: object) -> dict[str, _Entry]:
    if not isinstance(value, dict) or set(value) != {"version", "entries"}:
        raise ValueError("note verdict cache is invalid")
    version, raw = value["version"], value["entries"]
    if (
        isinstance(version, bool)
        or version != _FORMAT_VERSION
        or not isinstance(raw, dict)
        or len(raw) > MAXIMUM_NOTE_VERDICTS
    ):
        raise ValueError("note verdict cache is invalid")
    entries: dict[str, _Entry] = {}
    for key, item in raw.items():
        if _KEY.fullmatch(key) is None or not isinstance(item, list) or len(item) != 3:
            raise ValueError("note verdict cache entry is invalid")
        p_filler, stamp, attempts = item
        if not _natural(stamp) or not _natural(attempts) or attempts > _MAXIMUM_STORED_ATTEMPTS:
            raise ValueError("note verdict cache entry is invalid")
        entries[key] = (_checked_probability(p_filler), stamp, attempts)
    return entries


def _checked_key(value: object) -> str:
    if not isinstance(value, str) or _KEY.fullmatch(value) is None:
        raise ValueError("note verdict key is invalid")
    return value


def _checked_probability(value: object) -> float | None:
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0.0 <= value <= 1.0
    ):
        raise ValueError("note verdict p_filler must be within [0, 1]")
    return round(float(value), 6)


def _natural(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0
