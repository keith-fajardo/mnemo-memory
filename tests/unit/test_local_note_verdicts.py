"""The note-verdict cache: keyed, content-free, bounded, fresh for 30 days, never denying."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mnemo_memory.packages.storage import (
    MAXIMUM_NOTE_VERDICT_ATTEMPTS,
    LocalNoteVerdictCache,
    NoteVerdictState,
    note_verdict_key,
)
from mnemo_memory.packages.storage.local_note_verdicts import (
    MAXIMUM_NOTE_VERDICT_FILE_BYTES,
    MAXIMUM_NOTE_VERDICTS,
    NOTE_VERDICTS_FILE,
)

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
ITEM = (
    "knowledge:00000000-0000-0000-0000-000000000001:"
    "revision:00000000-0000-0000-0000-000000002711:section:0"
)
OTHER_ITEM = ITEM.removesuffix("section:0") + "section:1"
TEXT = "Invoice export keeps ledger order."
MODEL = "jev-1.13.0"
UNKNOWN = NoteVerdictState(None, 0)


def _key(item_id: str = ITEM, text: str = TEXT, model: str = MODEL, version: int = 1) -> str:
    return note_verdict_key(item_id, text, model_version=model, question_version=version)


def _cache(directory: Path, at: datetime = NOW) -> LocalNoteVerdictCache:
    return LocalNoteVerdictCache(directory, clock=lambda: at)


def test_a_verdict_is_usable_for_thirty_days(tmp_path: Path) -> None:
    _cache(tmp_path).record([(_key(), 0.95)])
    fresh = _cache(tmp_path, NOW + timedelta(days=29, hours=23)).states([_key()])
    assert fresh == (NoteVerdictState(0.95, 0),)
    expired = _cache(tmp_path, NOW + timedelta(days=30)).states([_key()])[0]
    assert expired == UNKNOWN and expired.needs_judging
    assert _cache(tmp_path).path.name == NOTE_VERDICTS_FILE == "typed-decision-note-verdicts.json"


@pytest.mark.parametrize(
    ("item_id", "text", "model", "version"),
    [
        (ITEM, TEXT + " Changed.", MODEL, 1),
        (ITEM, TEXT, "jev-1.14.0", 1),
        (ITEM, TEXT, MODEL, 2),
        (OTHER_ITEM, TEXT, MODEL, 1),
    ],
)
def test_a_verdict_counts_only_when_every_key_part_matches(
    tmp_path: Path, item_id: str, text: str, model: str, version: int
) -> None:
    _cache(tmp_path).record([(_key(), 0.95)])
    changed = _key(item_id, text, model, version)
    assert len(changed) == 64 and changed != _key()
    assert _cache(tmp_path).states([changed, _key()]) == (UNKNOWN, NoteVerdictState(0.95, 0))


@pytest.mark.parametrize(
    ("item_id", "text", "model", "version"),
    [
        ("", TEXT, MODEL, 1),
        ("x" * 257, TEXT, MODEL, 1),
        (ITEM, "", MODEL, 1),
        (ITEM, TEXT, "", 1),
        (ITEM, TEXT, MODEL, 0),
        (ITEM, TEXT, MODEL, True),
    ],
)
def test_invalid_key_parts_are_refused(item_id: str, text: str, model: str, version: int) -> None:
    with pytest.raises(ValueError):
        note_verdict_key(item_id, text, model_version=model, question_version=version)


def test_three_failed_attempts_stop_judging_until_the_text_changes(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    for attempt in range(1, MAXIMUM_NOTE_VERDICT_ATTEMPTS + 1):
        assert cache.states([_key()])[0].needs_judging
        cache.record([(_key(), None)])
        assert cache.states([_key()]) == (NoteVerdictState(None, attempt),)
    assert MAXIMUM_NOTE_VERDICT_ATTEMPTS == 3
    assert not cache.states([_key()])[0].needs_judging
    later = _cache(tmp_path, NOW + timedelta(days=90))
    assert not later.states([_key()])[0].needs_judging  # failures never age out
    assert later.states([_key(text=TEXT + " Edited.")])[0].needs_judging
    later.record([(_key(), 0.1)])
    assert later.states([_key()]) == (NoteVerdictState(0.1, 0),)


def test_the_cache_keeps_the_newest_five_thousand_entries_under_one_megabyte(
    tmp_path: Path,
) -> None:
    oldest = _key(text="the oldest note")
    _cache(tmp_path).record([(oldest, 0.2)])
    newer = [_key(text=f"note {index}") for index in range(MAXIMUM_NOTE_VERDICTS)]
    later = _cache(tmp_path, NOW + timedelta(minutes=1))
    later.record([(key, 0.9) for key in newer])
    assert later.entry_count() == MAXIMUM_NOTE_VERDICTS == 5_000
    assert later.states([oldest, newer[0]]) == (UNKNOWN, NoteVerdictState(0.9, 0))
    assert later.path.stat().st_size <= MAXIMUM_NOTE_VERDICT_FILE_BYTES == 1_048_576


def test_expired_verdicts_are_pruned_on_the_next_write(tmp_path: Path) -> None:
    _cache(tmp_path).record([(_key(), 0.95), (_key(text="failed"), None)])
    _cache(tmp_path, NOW + timedelta(days=31)).record([(_key(text="new"), 0.5)])
    stored = json.loads(_cache(tmp_path).path.read_text("utf-8"))
    assert set(stored["entries"]) == {_key(text="failed"), _key(text="new")}


@pytest.mark.parametrize(
    "stored",
    [
        '{"version": 1, "entries": ',
        json.dumps({"version": 2, "entries": {}}),
        json.dumps({"version": True, "entries": {}}),
        json.dumps({"version": 1, "entries": {}, "extra": 1}),
        json.dumps({"version": 1, "entries": {"not-a-key": [0.9, 1, 0]}}),
        json.dumps({"version": 1, "entries": {"a" * 64: [1.5, 1, 0]}}),
        json.dumps({"version": 1, "entries": {"a" * 64: [0.5, -1, 0]}}),
        json.dumps({"version": 1, "entries": {"a" * 64: [0.5, 1, True]}}),
        json.dumps({"version": 1, "entries": {"a" * 64: [0.5, 1]}}),
        json.dumps({"version": 1, "entries": {"a" * 64: [0.5, 10**400, 0]}}),
        json.dumps({"version": 1, "entries": {"a" * 64: [0.5, 4_102_444_801, 0]}}),
    ],
)
def test_a_corrupt_cache_reads_as_empty_and_the_next_write_rebuilds_it(
    tmp_path: Path, stored: str
) -> None:
    cache = _cache(tmp_path)
    cache.path.write_text(stored, encoding="utf-8")
    assert cache.states([_key()]) == (UNKNOWN,)
    assert cache.entry_count() is None
    cache.record([(_key(), 0.8)])
    assert cache.states([_key()]) == (NoteVerdictState(0.8, 0),)
    assert cache.entry_count() == 1


def test_a_failed_attempt_never_erases_a_fresh_verdict(tmp_path: Path) -> None:
    _cache(tmp_path).record([(_key(), 0.95)])
    later = _cache(tmp_path, NOW + timedelta(days=1))
    later.record([(_key(), None)])
    assert later.states([_key()]) == (NoteVerdictState(0.95, 0),)
    expired = _cache(tmp_path, NOW + timedelta(days=31))
    expired.record([(_key(), None)])  # a stale verdict is not protected
    assert expired.states([_key()]) == (NoteVerdictState(None, 1),)


def test_an_oversized_cache_reads_as_empty(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    cache.path.write_text(" " * (MAXIMUM_NOTE_VERDICT_FILE_BYTES + 1), encoding="utf-8")
    assert cache.states([_key()]) == (UNKNOWN,)
    assert cache.entry_count() is None


def test_a_symlinked_cache_reads_as_empty_and_is_never_written_through(tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    data = tmp_path / "data"
    data.mkdir()
    cache = _cache(data)
    cache.path.symlink_to(outside)
    assert cache.states([_key()]) == (UNKNOWN,)
    assert cache.entry_count() is None
    with pytest.raises(OSError):
        cache.record([(_key(), 0.9)])
    assert outside.read_text("utf-8") == "{}"


def test_concurrent_writers_lose_no_update(tmp_path: Path) -> None:
    errors: list[BaseException] = []

    def worker(index: int) -> None:
        try:
            cache = _cache(tmp_path)
            for number in range(25):
                cache.record([(_key(text=f"writer {index} note {number}"), 0.5)])
        except BaseException as error:  # pragma: no cover - surfaced below
            errors.append(error)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert _cache(tmp_path).entry_count() == 8 * 25


def test_the_cache_file_holds_no_text_item_id_or_model(tmp_path: Path) -> None:
    marker = "private-note-4b7e"
    cache = _cache(tmp_path)
    cache.record([(_key(text=f"{marker} {TEXT}"), 0.95), (_key(text="failed"), None)])
    encoded = cache.path.read_text("utf-8")
    for leaked in (marker, "Invoice", ITEM, "knowledge:", MODEL):
        assert leaked not in encoded
    stored = json.loads(encoded)
    assert set(stored) == {"version", "entries"}
    assert all(len(key) == 64 for key in stored["entries"])
    assert cache.path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("p_filler", [1.5, -0.1, float("nan"), float("inf"), True])
def test_record_refuses_values_outside_zero_to_one(tmp_path: Path, p_filler: float) -> None:
    with pytest.raises(ValueError):
        _cache(tmp_path).record([(_key(), p_filler)])
    with pytest.raises(ValueError):
        _cache(tmp_path).record([("not-a-key", 0.5)])
    assert not _cache(tmp_path).path.exists()


def test_recording_nothing_writes_nothing(tmp_path: Path) -> None:
    _cache(tmp_path).record([])
    assert not _cache(tmp_path).path.exists()


def test_a_verdict_stamped_in_the_future_is_not_trusted(tmp_path: Path) -> None:
    """Review focus 1: a clock that jumped must never keep a verdict live for ever."""

    _cache(tmp_path, NOW + timedelta(days=400)).record([(_key(), 0.95)])
    assert _cache(tmp_path).states([_key()]) == (UNKNOWN,)
    _cache(tmp_path, NOW + timedelta(minutes=4)).record([(_key(text="skewed"), 0.95)])
    assert _cache(tmp_path).states([_key(text="skewed")]) == (NoteVerdictState(0.95, 0),)
    naive = LocalNoteVerdictCache(tmp_path, clock=lambda: datetime(2026, 10, 3))
    assert naive.states([_key(text="skewed")]) == (UNKNOWN,)
    with pytest.raises(ValueError, match="timezone-aware"):
        naive.record([(_key(), 0.5)])
