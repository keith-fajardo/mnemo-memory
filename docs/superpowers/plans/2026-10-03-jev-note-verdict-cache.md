# Jev Note-Verdict Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make each prompt send exactly one Jev request (the front door) by reading per-note filler verdicts from a local, content-free cache that a detached background judge fills after the prompt, and warm that cache in the synthetic replay before any prompt runs.

**Architecture:** Two small file-locked stores in `packages/storage` hold the verdict cache (keyed by item ID, judged-text digest, model version and question version, all hashed into one key) and the judge queue (item IDs plus scope). The hook in `apps/cli/main.py` reads verdicts for its filler candidates, queues the ones without a usable verdict, and starts `typed-decisions judge-notes` detached after its output is built. A new `apps/cli/typed_note_judge.py` drains the queue under a single-instance lock, re-reads note text through the scoped `get_context item_ids` lookup, and asks Jev through a composition-built `RUNTIME` guard (5 s deadline, 4 in flight, 32 per run). The replay primes the seed's cache through the synthetic guard and gates on the priming answered-share.

**Tech Stack:** Python 3.12, standard library only (`asyncio`, `fcntl`, `hashlib`, `json`, `subprocess`), Typer CLI, pytest, mypy strict, ruff.

**Spec:** `docs/superpowers/specs/2026-10-03-jev-note-verdict-cache-design.md` (approved 2026-10-03). It builds on `docs/superpowers/specs/2026-10-02-jev-hook-wiring-design.md` (the "hook spec") and ADR 0049. Executors read both specs.

## Global Constraints

- **Toolchain.** Python 3.12, mypy strict over `src`, `tests` and `scripts` (`warn_unreachable = true`), ruff line length 100 (rule sets B, E, F, I, RUF, SIM, UP; RUF022 keeps `__all__` sorted), standard library only. No new dependencies.
- **Layer rules** (`scripts/check_architecture.py`):
  - `packages/application` may not import `model_gateway` or `telemetry`.
  - `packages/storage` may import only `packages/domain` and `packages/policy`.
  - `packages/telemetry` imports nothing internal.
  - apps may not import other apps.
- **Connector importers.** `tests/architecture/test_typed_decision_boundaries.py` requires that the connector `mnemo_memory.connectors.typesafe` is imported ONLY by `src/mnemo_memory/apps/cli/typed_decision_composition.py`. The judge therefore builds its guard through the composition module (`build_runtime_typed_decision_classifier`), and `typed_note_judge.py` never imports the connector.
- **No live data or secrets in tests.** No live network in any test. Never read or print `TYPESAFE_API_KEY`; tests use the fake literal `test-key-not-real-0000` (`FAKE_TYPESAFE_KEY`) through `environ=`, `monkeypatch.setenv` or an explicit child `env`.
- **Real data never leaves the machine.**
  - The data route stays `synthetic_only`.
  - The judge's runtime guard has source `RUNTIME`, so every judge request is `data_route_blocked` today.
  - The replay's priming pass uses the synthetic builder, only inside a verified seed.
- **Byte-identical off and shadow.** With the feature off, and in shadow, hook output stays byte-identical to `main`. The existing contract tests in `tests/contract/test_typed_hook_contract.py` must keep passing.
- **Content-free files.** The verdict cache holds only 64-hex keys and numbers; the queue holds only item IDs and scope identifiers. No note text, prompt text or skill name, ever.
- **Failure means keep.** Every cache read, queue write and judge start sits inside the existing typed-step guard and fails toward today's behaviour (the note is kept). The judge prints nothing and exits 0.
- **Focused checks** per task: `uv run pytest <files> -q`, `uv run ruff format <files>`, `uv run ruff check <files>`, `uv run mypy <files>`. Full check: `npm run check` (Task 10).
- **Commits.** Use `git commit -- <paths>` (run `git add <new files>` first, because a path git does not track yet cannot be committed by pathspec). Each message ends with the committer's own session Co-Authored-By line. The commands below show `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; if your session's attribution line differs, use yours.
- No live Jev run is part of this plan. A live replay needs the maintainer's explicit go-ahead each time.

## Review Focus

The five input classes or failure modes that the spec implies but that no spec-listed test exercises, most likely to bite a person first. Each has a pinning test in the owning task.

1. **The wall clock jumps** (sleep/wake, NTP correction, a manual change). Expected: a verdict stamped in the future is not trusted, a small skew (up to 5 minutes) is tolerated, and a naive clock reads as "no verdict" instead of crashing. Test: Task 1, `test_a_verdict_stamped_in_the_future_is_not_trusted`.
2. **A note is edited after it was judged** (or while its ID waits in the queue). Expected: the old verdict never drops the new text; the edited note is kept and queued, and the judge stores its verdict under the new text's key. Tests: Task 5, `test_an_edited_note_is_kept_until_its_new_text_is_judged`; Task 4, `test_three_failed_attempts_stop_retries_until_the_text_changes` (the edit branch).
3. **A judge process is killed mid-run** (laptop sleep, `kill -9`, OOM). Expected: the next judge is not blocked by a stale lock file, and `status` stops reporting it as running. Test: Task 2, `test_a_judge_that_died_never_blocks_the_next_one`.
4. **A queued note is deleted, retracted or moved out of scope before the judge reads it.** Expected: the judge skips it without a crash, records no attempt, and drains it from the queue; an unreadable scope never stops the other scopes. Tests: Task 4, `test_a_deleted_note_or_an_unserved_scope_is_skipped`, `test_an_exception_in_the_reader_skips_only_its_scope`.
5. **A data directory whose path holds spaces or quotes** (macOS `Application Support`, a user name with an apostrophe). Expected: the detached start passes the path as one argv element, never through a shell, and the command line names nothing but the data directory. Test: Task 6, `test_the_start_is_detached_names_only_the_data_directory_and_never_waits`.

## Decisions this plan makes where the spec is silent

Executors and reviewers treat these as settled for this plan; each is also flagged to the maintainer.

- **One hashed key per entry.** The cache key is `sha256(json([format, item_id, sha256(judged_text), model_version, question_version]))`. The file stores neither item IDs nor text digests, only the 64-hex key. This keeps 5,000 entries near 470 KB (under the 1 MB cap; a structured key with the ~105-character item ID would not fit) and makes the file content-free by construction. Every key part still has to match exactly.
- **The model version in the key is the pinned `settings.typed_decision_model_id`** (`jev-1.13.0`). The hook knows it before any answer; the judge stores a verdict only when the answer's `model_version` equals it, and counts any other answer as a failed attempt.
- **`FILLER_QUESTION_VERSION = 1` lives in `decision_axes`**, and storage takes `question_version` as a parameter, so storage imports nothing from `model_gateway`. A test pins the `NOTE_SUBSTANCE` wording digest to the version, so a wording change fails CI until the version is bumped.
- **Failed attempts never expire by age.** A verdict counts for 30 days; a failed-attempt record stays until its text changes or the 5,000-entry cap evicts it, which is the spec's "not queued again until its content changes". A success resets the attempt count to 0.
- **Policy blocks are not attempts.** `data_route_blocked`, `no_credential`, `budget_denied` and `disabled` stop the judge run and record nothing. `timeout`, `http_error`, `schema_invalid`, a secret or sensitivity block, and an answer from another model version each count as one failed attempt.
- **Corrupt means empty, and the next write rebuilds.** Unlike the budget counter (which is never overwritten), a corrupt cache or queue is treated as empty and the next write replaces it, as spec §5 requires ("rebuilt by later judging"). A symlinked or non-regular path is never written through.
- **The judge starts only when the data route would let it send runtime text** (`_judge_route_open`). This is one condition beyond spec §3. Under `synthetic_only` every judge request would be `data_route_blocked`, so starting a Python process per prompt would only burn CPU, and it also keeps test runs of the real hook from spawning real background processes. Queueing still happens exactly as the spec says, so `status` shows the backlog. The real-hook spawn path is therefore dormant until a real-traffic route exists; tests open the route by monkeypatching `_judge_route_open`.
- **The judge starts only when this prompt queued at least one note.** A backlog left by an earlier policy block is picked up by the next prompt that queues something; the hook never reads the queue just to decide.
- **Queue semantics.** Deduplicated by `(item_id, scope)`, original position kept; capped at 256 by evicting the oldest; `take(n)` removes entries under the lock. The judge takes 4 at a time, so a policy block loses at most 4 entries, which later prompts re-queue.
- **Reads go through the scoped lookup in chunks of 4 IDs** (`_NOTE_READ_BATCH`), then through the hook's own `filler_candidates` (pins, conflicts, sensitivity, secret scan, unreadable text). So the judged text is exactly the text the hook keys on; a test pins that equality.
- **Cached verdicts apply whatever the front door's outcome is.** A filler verdict belongs to the note, not to the prompt, so a front-door timeout no longer cancels filler drops.
- **`typed_notes_queued`** counts this prompt's candidates sent to the queue (0 when the write failed). **`typed_notes_cached`** counts candidates with a usable verdict. Both are 0–16 and their sum never exceeds `typed_notes_checked`.
- **Replay filler `answered` sub-gate** now means "checked notes that had a cached verdict" (the old per-note answered count is always 0 on the prompt path). Spec §7's new `priming_answered_share` sub-gate sits beside it in each set's filler gates; a set never primed fails it.
- **Replay priming scope.** It judges the notes project's Markdown sections and the main project's approved events (the pinned one is never sent). Skill documents are not notes and are not primed; a main-project candidate that is a skill section stays uncached and is kept.
- **A priming failure never stops the run.** It is recorded as `{"notes": 0, "answered": 0, "share": 0.0, "blocked": true}`, and its gate fails.

## Deviations from the brief's task breakdown

- **Telemetry is its own task (Task 3)** instead of part of the hook task, because `packages/telemetry` is a separate layer with its own key-set tier tests, and a reviewer can approve it on its own.
- **The judge core (Task 4) comes before the hook change (Task 5).** Once the prompt path stops asking per-note questions, a dozen existing integration and contract tests need a warm cache to keep meaning what they mean. They warm it with `prime_note_verdicts`, which uses the judge's reader and `judge_candidates`, so those must exist first.
- **The detached start moves from the hook task to the judge-command task (Task 6)**, because the start command and the command it starts are reviewed together.
- **The composition `deadline_seconds` parameter is folded into Task 4**, the first task that needs a 5 s guard.

## Tasks

1. Verdict cache store
2. Judge queue store and run lock
3. Telemetry: `typed_notes_cached` and `typed_notes_queued`
4. Judge core: question version, verdict keys, scoped note reader, `judge_candidates`, `run_note_judge`, 5 s guard
5. Hook: one request per prompt, filler verdicts from the cache, queueing
6. Background judge command and the detached start
7. `typed-decisions status` additions
8. Replay: priming pass and its gate
9. Contract, security and docs
10. Final step: `npm run check`

---

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `src/mnemo_memory/packages/storage/local_note_verdicts.py` (new) | `note_verdict_key`, `NoteVerdictState`, `LocalNoteVerdictCache`: file-locked, content-free, bounded verdict cache | 1 |
| `src/mnemo_memory/packages/storage/local_note_judge_queue.py` (new) | `QueuedNote`, `LocalNoteJudgeQueue`: file-locked ID queue and the single-judge run lock | 2 |
| `src/mnemo_memory/packages/storage/__init__.py` | export the new store names | 1, 2 |
| `src/mnemo_memory/packages/telemetry/automatic_routes.py`, `__init__.py` | `typed_notes_cached`, `typed_notes_queued`, their key-set tier | 3 |
| `src/mnemo_memory/packages/model_gateway/decision_axes.py` | `FILLER_QUESTION_VERSION`, `should_drop_filler` | 4 |
| `src/mnemo_memory/apps/cli/typed_decision_composition.py` | `deadline_seconds` on both guard builders | 4 |
| `src/mnemo_memory/apps/cli/typed_note_judge.py` (new) | `judge_candidates`, `run_note_judge`, `JudgeTally`, pacing constants | 4 |
| `src/mnemo_memory/apps/cli/typed_decision_hook.py` | `filler_probability`, `filler_verdict_key` (Task 4); front door only, `filler_verdicts`, `cached_filler_drops`, cache counts (Task 5) | 4, 5 |
| `src/mnemo_memory/apps/cli/main.py` | `_note_candidates_by_id` (Task 4); cache read and queueing in `_typed_prompt_render` (Task 5); `judge-notes`, detached start (Task 6); status (Task 7) | 4, 5, 6, 7 |
| `scripts/typed_decision_replay.py` | `knowledge_note_item_ids`, `approved_event_item_ids` (Task 4); priming, `notes_cached`, gates (Task 8) | 4, 8 |
| `scripts/typed_decision_test_support.py` | `prime_note_verdicts` | 5 |
| `scripts/run_typed_decision_replay.py` | run the priming pass before the cases; report it | 8 |
| `tests/unit/test_local_note_verdicts.py` (new) | cache tests | 1 |
| `tests/unit/test_local_note_judge_queue.py` (new) | queue and run-lock tests | 2 |
| `tests/unit/test_context_route_telemetry.py` | cache-count telemetry tests | 3 |
| `tests/unit/test_decision_axes.py`, `test_typed_decision_composition.py`, `test_typed_note_judge.py` (new), `test_typed_note_reader.py` (new), `test_typed_decision_hook.py` | judge-core tests | 4 |
| `tests/unit/test_typed_decision_hook.py`, `test_typed_hook_integration.py`, `tests/contract/test_typed_hook_contract.py` | hook tests | 5 |
| `tests/unit/test_typed_note_judge_start.py` (new) | judge command and detached start | 6 |
| `tests/unit/test_typed_decisions_cli.py` | status | 7 |
| `tests/evals/test_typed_decision_replay.py` | replay priming and gates | 8 |
| `tests/contract/test_typed_hook_contract.py`, `tests/security/test_typed_hook_network_boundary.py`, `tests/architecture/test_typed_decision_boundaries.py` | contracts, zero transport calls, content-free files, network-free modules | 9 |
| `docs/adr/0049-hosted-typed-classifier-and-cheap-model-tier.md`, `docs/user-guide.md` | paperwork | 9 |

---

### Task 1: Verdict cache store

**Files:**
- Create: `src/mnemo_memory/packages/storage/local_note_verdicts.py`
- Modify: `src/mnemo_memory/packages/storage/__init__.py:126` (import block, next to `from .local_model_budget import LocalDailyModelBudget`) and `__all__` (from line 186)
- Test: `tests/unit/test_local_note_verdicts.py` (new)

**Interfaces:**
- Consumes: nothing new (standard library only; storage imports no domain type here).
- Produces (all exported from `mnemo_memory.packages.storage` except the three file constants, which tests import from the module):
  - `note_verdict_key(item_id: str, judged_text: str, *, model_version: str, question_version: int) -> str` — 64 lowercase hex characters; raises `ValueError` for an empty or over-long item ID (> 256), empty text, empty or over-long model version (> 64), or a question version that is not an `int >= 1` (a `bool` counts as invalid).
  - `@dataclass(frozen=True, slots=True) class NoteVerdictState: p_filler: float | None; attempts: int` with property `needs_judging -> bool` (`p_filler is None and attempts < 3`).
  - `MAXIMUM_NOTE_VERDICT_ATTEMPTS: int = 3`.
  - `class LocalNoteVerdictCache(data_directory: Path, *, clock: Callable[[], datetime] | None = None)`:
    - `path: Path` (`<data>/typed-decision-note-verdicts.json`);
    - `states(keys: Sequence[str]) -> tuple[NoteVerdictState, ...]` — in key order; never raises; unknown, stale, corrupt or unsafe reads as `NoteVerdictState(None, 0)`; a failed-attempt record reads as `NoteVerdictState(None, attempts)`;
    - `record(outcomes: Sequence[tuple[str, float | None]]) -> None` — `float` stores a verdict (attempts reset to 0), `None` adds one failed attempt; one lock per call; raises `ValueError` for a bad key, a value outside `[0, 1]`, a non-finite value or a naive clock, and `OSError` for an unsafe path;
    - `entry_count() -> int | None` — `None` when unreadable.
  - Module constants: `NOTE_VERDICTS_FILE = "typed-decision-note-verdicts.json"`, `NOTE_VERDICT_TTL = timedelta(days=30)`, `MAXIMUM_NOTE_VERDICTS = 5_000`, `MAXIMUM_NOTE_VERDICT_FILE_BYTES = 1_048_576`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_local_note_verdicts.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_local_note_verdicts.py -q`
Expected: FAIL at collection with `ImportError: cannot import name 'MAXIMUM_NOTE_VERDICT_ATTEMPTS' from 'mnemo_memory.packages.storage'`.

- [ ] **Step 3: Write the store**

Create `src/mnemo_memory/packages/storage/local_note_verdicts.py`:

```python
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
        (key, entry)
        for key, entry in entries.items()
        if entry[0] is None or _fresh(entry[1], now)
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
```

In `src/mnemo_memory/packages/storage/__init__.py`, add after `from .local_model_budget import LocalDailyModelBudget`:

```python
from .local_note_verdicts import (
    MAXIMUM_NOTE_VERDICT_ATTEMPTS,
    LocalNoteVerdictCache,
    NoteVerdictState,
    note_verdict_key,
)
```

and add `"MAXIMUM_NOTE_VERDICT_ATTEMPTS"`, `"LocalNoteVerdictCache"`, `"NoteVerdictState"` and `"note_verdict_key"` to `__all__`. Then let ruff place them (constants first, then classes, then functions):

Run: `uv run ruff check --select RUF022,I --fix src/mnemo_memory/packages/storage/__init__.py`

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_local_note_verdicts.py -q`
Expected: PASS (all tests).

Run: `uv run ruff format src/mnemo_memory/packages/storage tests/unit/test_local_note_verdicts.py && uv run ruff check src/mnemo_memory/packages/storage tests/unit/test_local_note_verdicts.py && uv run mypy src/mnemo_memory/packages/storage tests/unit/test_local_note_verdicts.py && npm run -s architecture:check`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo_memory/packages/storage/local_note_verdicts.py tests/unit/test_local_note_verdicts.py
git commit -m "feat(storage): content-free note verdict cache" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- src/mnemo_memory/packages/storage/local_note_verdicts.py src/mnemo_memory/packages/storage/__init__.py tests/unit/test_local_note_verdicts.py
```

---

### Task 2: Judge queue store and run lock

**Files:**
- Create: `src/mnemo_memory/packages/storage/local_note_judge_queue.py`
- Modify: `src/mnemo_memory/packages/storage/__init__.py` (import block and `__all__`)
- Test: `tests/unit/test_local_note_judge_queue.py` (new)

**Interfaces:**
- Consumes: `mnemo_memory.packages.domain.MemoryScope` (with its `to_dict()` / `from_dict()`); nothing from Task 1.
- Produces (exported from `mnemo_memory.packages.storage`):
  - `MAXIMUM_QUEUED_NOTES: int = 256`.
  - `@dataclass(frozen=True, slots=True) class QueuedNote: item_id: str; scope: MemoryScope` — `item_id` must match `[A-Za-z0-9][A-Za-z0-9:_-]{0,255}` (else `ValueError`).
  - `class LocalNoteJudgeQueue(data_directory: Path)`:
    - `path: Path` (`<data>/typed-decision-judge-queue.json`);
    - `append(scope: MemoryScope, item_ids: Sequence[str]) -> int` — queue length after; dedupes by `(item_id, scope)` keeping the first position; keeps the newest 256; raises `ValueError` for an invalid ID and `OSError` for an unsafe path; a corrupt file is replaced;
    - `take(limit: int) -> tuple[QueuedNote, ...]` — removes and returns up to `limit` oldest entries; `ValueError` unless `limit` is an `int >= 1`;
    - `length() -> int | None` — `None` when unreadable;
    - `run_lock() -> contextlib.AbstractContextManager[bool]` — holds the single-judge `flock` for a run; yields `False` (at once) when another judge holds it;
    - `judge_running() -> bool` — whether a judge holds the run lock now; never creates the lock file.
  - Module constants: `JUDGE_QUEUE_FILE = "typed-decision-judge-queue.json"`, `JUDGE_RUN_LOCK_FILE = ".typed-decision-judge.lock"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_local_note_judge_queue.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_local_note_judge_queue.py -q`
Expected: FAIL at collection with `ImportError: cannot import name 'MAXIMUM_QUEUED_NOTES' from 'mnemo_memory.packages.storage'`.

- [ ] **Step 3: Write the store**

Create `src/mnemo_memory/packages/storage/local_note_judge_queue.py`:

```python
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
            if added:
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
```

Note: `test_the_queue_file_holds_ids_and_scope_only` compares the parsed JSON, so `sort_keys=True` does not affect it.

In `src/mnemo_memory/packages/storage/__init__.py`, add after the Task 1 import:

```python
from .local_note_judge_queue import MAXIMUM_QUEUED_NOTES, LocalNoteJudgeQueue, QueuedNote
```

and add `"MAXIMUM_QUEUED_NOTES"`, `"LocalNoteJudgeQueue"` and `"QueuedNote"` to `__all__`.

Run: `uv run ruff check --select RUF022,I --fix src/mnemo_memory/packages/storage/__init__.py`

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_local_note_judge_queue.py tests/unit/test_local_note_verdicts.py -q`
Expected: PASS.

Run: `uv run ruff format src/mnemo_memory/packages/storage tests/unit/test_local_note_judge_queue.py && uv run ruff check src/mnemo_memory/packages/storage tests/unit/test_local_note_judge_queue.py && uv run mypy src/mnemo_memory/packages/storage tests/unit/test_local_note_judge_queue.py && npm run -s architecture:check`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo_memory/packages/storage/local_note_judge_queue.py tests/unit/test_local_note_judge_queue.py
git commit -m "feat(storage): note judge queue and single-judge run lock" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- src/mnemo_memory/packages/storage/local_note_judge_queue.py src/mnemo_memory/packages/storage/__init__.py tests/unit/test_local_note_judge_queue.py
```

---

### Task 3: Telemetry: `typed_notes_cached` and `typed_notes_queued`

**Files:**
- Modify: `src/mnemo_memory/packages/telemetry/automatic_routes.py:62-82` (field constants), `:193-290` (`AutomaticRouteTypedDecisions`), `:571-620` (`AutomaticRouteEvent.from_dict` key-set tiers)
- Modify: `src/mnemo_memory/packages/telemetry/__init__.py` (export the new constant)
- Test: `tests/unit/test_context_route_telemetry.py` (append tests; extend the closed-values parametrize)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `AUTOMATIC_ROUTE_TYPED_CACHE_FIELDS: tuple[str, ...] = ("typed_notes_cached", "typed_notes_queued")`, exported from `mnemo_memory.packages.telemetry`.
  - `AutomaticRouteTypedDecisions.notes_cached: int = 0` and `.notes_queued: int = 0` — new last fields, each `0..16`, `notes_cached + notes_queued <= notes_checked`.
  - `to_dict()` always writes both keys; `from_dict()` reads them with default 0; an event's typed keys must be exactly `typed_v1` or `typed_v1` plus both cache keys.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_context_route_telemetry.py`, add `AUTOMATIC_ROUTE_TYPED_CACHE_FIELDS` to the `from mnemo_memory.packages.telemetry import (...)` block, extend the parametrize list of `test_typed_v1_accepts_only_closed_values` with these three rows:

```python
        ("notes_cached", 17),
        ("notes_queued", -1),
        ("notes_queued", True),
```

and append:

```python
CACHED = replace(TYPED, notes_unanswered=0, notes_cached=2, notes_queued=1)


def test_typed_cache_counts_round_trip_and_older_records_still_load() -> None:
    event = replace(_lazy_shadow(4), typed=CACHED)
    encoded = event.to_dict()
    assert set(AUTOMATIC_ROUTE_TYPED_CACHE_FIELDS) == {"typed_notes_cached", "typed_notes_queued"}
    assert (encoded["typed_notes_cached"], encoded["typed_notes_queued"]) == (2, 1)
    assert AutomaticRouteEvent.from_dict(encoded) == event
    older = dict(encoded)
    for key in AUTOMATIC_ROUTE_TYPED_CACHE_FIELDS:
        del older[key]
    loaded = AutomaticRouteEvent.from_dict(older).typed
    assert loaded is not None and (loaded.notes_cached, loaded.notes_queued) == (0, 0)


def test_typed_cache_counts_arrive_together_and_only_with_typed_v1() -> None:
    encoded = replace(_event(1), typed=CACHED).to_dict()
    half = dict(encoded)
    del half["typed_notes_queued"]
    with pytest.raises(ValueError, match="automatic route event is invalid"):
        AutomaticRouteEvent.from_dict(half)
    alone = {
        key: value
        for key, value in encoded.items()
        if key not in AUTOMATIC_ROUTE_TYPED_V1_FIELDS
    }
    with pytest.raises(ValueError, match="automatic route event is invalid"):
        AutomaticRouteEvent.from_dict(alone)


def test_cached_and_queued_notes_never_exceed_the_checked_notes() -> None:
    with pytest.raises(ValueError, match="note counts"):
        replace(TYPED, notes_unanswered=0, notes_cached=2, notes_queued=2)
    assert replace(TYPED, notes_unanswered=0, notes_cached=3, notes_queued=0).notes_cached == 3
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_context_route_telemetry.py -q`
Expected: FAIL at collection with `ImportError: cannot import name 'AUTOMATIC_ROUTE_TYPED_CACHE_FIELDS'`.

- [ ] **Step 3: Add the fields and the key-set tier**

In `src/mnemo_memory/packages/telemetry/automatic_routes.py`, directly after the `AUTOMATIC_ROUTE_TYPED_V1_FIELDS` tuple add:

```python
# Added by the note-verdict cache (spec 2026-10-03 §6). Older ``typed_v1`` records lack them
# and load with zeros; a record carrying them carries both.
AUTOMATIC_ROUTE_TYPED_CACHE_FIELDS: tuple[str, ...] = (
    "typed_notes_cached",
    "typed_notes_queued",
)
```

In `AutomaticRouteTypedDecisions`, add after `skill: str`:

```python
    notes_cached: int = 0
    notes_queued: int = 0
```

Replace the note-count block in `__post_init__`:

```python
        counts = (self.notes_checked, self.notes_dropped, self.notes_unanswered)
        if (
            not all(_bounded_integer(value, _MAXIMUM_TYPED_NOTES) for value in counts)
            or self.notes_dropped + self.notes_unanswered > self.notes_checked
        ):
            raise ValueError("automatic route typed note counts are invalid")
```

with:

```python
        counts = (
            self.notes_checked,
            self.notes_dropped,
            self.notes_unanswered,
            self.notes_cached,
            self.notes_queued,
        )
        if (
            not all(_bounded_integer(value, _MAXIMUM_TYPED_NOTES) for value in counts)
            or self.notes_dropped + self.notes_unanswered > self.notes_checked
            or self.notes_cached + self.notes_queued > self.notes_checked
        ):
            raise ValueError("automatic route typed note counts are invalid")
```

In `to_dict`, add after `"typed_skill": self.skill,`:

```python
            "typed_notes_cached": self.notes_cached,
            "typed_notes_queued": self.notes_queued,
```

In `from_dict`, add after `skill=_string(value["typed_skill"]),`:

```python
            notes_cached=_integer(value.get("typed_notes_cached", 0)),
            notes_queued=_integer(value.get("typed_notes_queued", 0)),
```

In `AutomaticRouteEvent.from_dict`, replace:

```python
        typed_v1 = frozenset(AUTOMATIC_ROUTE_TYPED_V1_FIELDS)
        if not isinstance(value, dict):
            raise ValueError("automatic route event is invalid")
        keys = frozenset(value)
        has_typed = bool(keys & typed_v1)
        if (has_typed and not typed_v1 <= keys) or keys - typed_v1 not in {
```

with:

```python
        typed_v1 = frozenset(AUTOMATIC_ROUTE_TYPED_V1_FIELDS)
        typed_all = typed_v1 | frozenset(AUTOMATIC_ROUTE_TYPED_CACHE_FIELDS)
        if not isinstance(value, dict):
            raise ValueError("automatic route event is invalid")
        keys = frozenset(value)
        typed_keys = keys & typed_all
        has_typed = bool(typed_keys)
        if (has_typed and typed_keys not in {typed_v1, typed_all}) or keys - typed_all not in {
```

(The rest of that `if` — the four `frozenset(required ...)` tiers — stays as it is.)

In `src/mnemo_memory/packages/telemetry/__init__.py`, add `AUTOMATIC_ROUTE_TYPED_CACHE_FIELDS` to the `from .automatic_routes import (...)` block and `"AUTOMATIC_ROUTE_TYPED_CACHE_FIELDS"` to `__all__`, then:

Run: `uv run ruff check --select RUF022,I --fix src/mnemo_memory/packages/telemetry/__init__.py`

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_context_route_telemetry.py tests/unit/test_typed_decision_hook.py tests/unit/test_typed_hook_integration.py -q`
Expected: PASS. (The hook still builds `AutomaticRouteTypedDecisions(**asdict(values))` without the new fields, which default to 0.)

Run: `uv run ruff format src/mnemo_memory/packages/telemetry tests/unit/test_context_route_telemetry.py && uv run ruff check src/mnemo_memory/packages/telemetry tests/unit/test_context_route_telemetry.py && uv run mypy src/mnemo_memory/packages/telemetry tests/unit/test_context_route_telemetry.py`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(telemetry): typed_notes_cached and typed_notes_queued" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- src/mnemo_memory/packages/telemetry/automatic_routes.py src/mnemo_memory/packages/telemetry/__init__.py tests/unit/test_context_route_telemetry.py
```

---

### Task 4: Judge core: question version, verdict keys, scoped note reader, `judge_candidates`, `run_note_judge`, 5 s guard

**Files:**
- Modify: `src/mnemo_memory/packages/model_gateway/decision_axes.py:30-31` (constants), `:155-158` (`should_drop_note`)
- Modify: `src/mnemo_memory/apps/cli/typed_decision_hook.py:49-65` (imports), append two functions after `notes_to_drop` (`:344-356`)
- Modify: `src/mnemo_memory/apps/cli/typed_decision_composition.py:37-100` (both builders and `_guard`)
- Modify: `src/mnemo_memory/apps/cli/main.py:12` (`collections.abc` import), `:153-160` (`unified_context` import), `:242-252` (`TYPE_CHECKING` block), after `_pin_state` (`:1274-1278`) add `_note_candidates_by_id`
- Create: `src/mnemo_memory/apps/cli/typed_note_judge.py`
- Modify: `scripts/typed_decision_replay.py` (add two public helpers after `_seed_approved_events`, `:441-475`)
- Test: `tests/unit/test_decision_axes.py`, `tests/unit/test_typed_decision_hook.py`, `tests/unit/test_typed_decision_composition.py`, `tests/unit/test_typed_note_judge.py` (new), `tests/unit/test_typed_note_reader.py` (new)

**Interfaces:**
- Consumes: Task 1 `note_verdict_key`, `LocalNoteVerdictCache`, `NoteVerdictState`; Task 2 `LocalNoteJudgeQueue`, `QueuedNote`, `MAXIMUM_QUEUED_NOTES`.
- Produces:
  - `decision_axes.FILLER_QUESTION_VERSION: int = 1`; `decision_axes.should_drop_filler(p_filler: float | None) -> bool` (`p_filler >= 0.7`).
  - `typed_decision_hook.filler_probability(outcome: TypedDecisionOutcome) -> float | None` — the `note_substance` escalation score of an answered outcome, else `None`.
  - `typed_decision_hook.filler_verdict_key(candidate: FillerCandidate, model_version: str) -> str`.
  - `build_runtime_typed_decision_classifier(..., deadline_seconds: float = RUNTIME_DEADLINE_SECONDS)` and `build_synthetic_typed_decision_classifier(..., deadline_seconds: float = RUNTIME_DEADLINE_SECONDS)` (keyword-only, last).
  - `main._note_candidates_by_id(data_directory: Path, scope: MemoryScope, item_ids: Sequence[str]) -> tuple[FillerCandidate, ...]`; `main._NOTE_READ_BATCH = 4`.
  - `typed_note_judge`: `JUDGE_DEADLINE_SECONDS = 5.0`, `JUDGE_IN_FLIGHT = 4`, `JUDGE_NOTES_PER_RUN = 32`; `NoteReader = Callable[[MemoryScope, tuple[str, ...]], tuple[FillerCandidate, ...]]`; `GuardBuilder = Callable[[], GuardedTypedDecisionClassifier | None]`; `@dataclass(frozen=True, slots=True) class JudgeTally(asked: int = 0, answered: int = 0, failed: int = 0, blocked: bool = False)` with `plus(other) -> JudgeTally`; `judge_candidates(guard, candidates, *, cache: LocalNoteVerdictCache, model_version: str, in_flight: int = JUDGE_IN_FLIGHT) -> JudgeTally`; `run_note_judge(data_directory: Path, *, model_version: str, read_notes: NoteReader, build_guard: GuardBuilder, limit: int = JUDGE_NOTES_PER_RUN) -> JudgeTally | None` (`None` when another judge holds the lock).
  - `scripts.typed_decision_replay.knowledge_note_item_ids(data: Path, binding: MemoryProjectBinding) -> tuple[str, ...]` and `approved_event_item_ids(data: Path, binding: MemoryProjectBinding) -> tuple[str, ...]`.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_decision_axes.py`, add `import hashlib` and `import json` to the imports, add `FILLER_QUESTION_VERSION` and `should_drop_filler` to the `decision_axes` import list, and append:

```python
# sha256 of the NOTE_SUBSTANCE wording below; bump FILLER_QUESTION_VERSION with it.
NOTE_SUBSTANCE_WORDING_SHA256 = "90a368ed40b38da5197a673f1740058cced0f4b13f1f838356a0b9a3b475724b"


def test_the_filler_question_version_pins_its_wording() -> None:
    """Changing NOTE_SUBSTANCE must bump FILLER_QUESTION_VERSION, so cached verdicts lapse."""

    axis = NOTE_SUBSTANCE
    wording = json.dumps(
        [
            axis.name,
            axis.instructions,
            list(axis.allowed_labels),
            [list(pair) for pair in axis.criteria],
            list(axis.label_scores) if axis.label_scores else None,
        ],
        sort_keys=True,
    )
    digest = hashlib.sha256(wording.encode()).hexdigest()
    assert (FILLER_QUESTION_VERSION, digest) == (1, NOTE_SUBSTANCE_WORDING_SHA256)


def test_a_cached_filler_probability_drops_only_at_the_bar() -> None:
    assert should_drop_filler(None) is False
    assert should_drop_filler(0.69) is False
    assert should_drop_filler(0.7) is True
    assert should_drop_note(_result("note_substance", "filler", 0.7)) is True
```

In `tests/unit/test_typed_decision_hook.py`, add `filler_probability` and `filler_verdict_key` to the `typed_decision_hook` import list and append:

```python
def test_filler_probability_reads_only_an_answered_note_question() -> None:
    assert filler_probability(FILLER) == pytest.approx(0.95)
    assert filler_probability(KEEP) == pytest.approx(0.05)
    assert filler_probability(_blocked(TypedDecisionUnavailableReason.TIMEOUT)) is None
    assert filler_probability(FRONT) is None  # no note question in it


def test_the_verdict_key_follows_the_judged_text_and_the_model() -> None:
    candidate = FillerCandidate(USEFUL_NOTE, "Invoice export keeps ledger order.")
    key = filler_verdict_key(candidate, "jev-1.13.0")
    assert len(key) == 64
    assert key == filler_verdict_key(FillerCandidate(USEFUL_NOTE, candidate.text), "jev-1.13.0")
    assert key != filler_verdict_key(replace(candidate, text=candidate.text + "!"), "jev-1.13.0")
    assert key != filler_verdict_key(candidate, "jev-1.14.0")
```

In `tests/unit/test_typed_decision_composition.py`, add `TypedDecisionSource` to the `mnemo_memory.packages.domain` import and append:

```python
def test_builders_take_a_per_request_deadline_and_keep_the_hook_default(tmp_path: Path) -> None:
    settings = PersonalSettings(experimental_typed_decisions_enabled=True)
    hook = build_runtime_typed_decision_classifier(settings, data_directory=tmp_path, environ={})
    judge = build_runtime_typed_decision_classifier(
        settings, data_directory=tmp_path, environ={}, deadline_seconds=5.0
    )
    primer = build_synthetic_typed_decision_classifier(
        settings, data_directory=tmp_path, environ={}, deadline_seconds=5.0
    )
    assert hook is not None and judge is not None
    assert (hook._deadline, judge._deadline, primer._deadline) == (0.8, 5.0, 5.0)
    assert judge._source is TypedDecisionSource.RUNTIME
    with pytest.raises(ValueError, match="deadline"):
        build_runtime_typed_decision_classifier(
            settings, data_directory=tmp_path, environ={}, deadline_seconds=31.0
        )
```

Create `tests/unit/test_typed_note_judge.py`:

```python
"""The background note judge: lock, pacing, retries and recording (spec 2026-10-03 §4)."""

from __future__ import annotations

import math
import threading
import time
from pathlib import Path
from uuid import UUID

from mnemo_memory.apps.cli.typed_decision_hook import FillerCandidate, filler_verdict_key
from mnemo_memory.apps.cli.typed_note_judge import (
    JUDGE_DEADLINE_SECONDS,
    JUDGE_IN_FLIGHT,
    JUDGE_NOTES_PER_RUN,
    JudgeTally,
    run_note_judge,
)
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

    def __call__(self, scope: MemoryScope, item_ids: tuple[str, ...]) -> tuple[FillerCandidate, ...]:
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
    tally = run_note_judge(tmp_path, model_version=MODEL, read_notes=notes, build_guard=lambda: None)
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
```

Create `tests/unit/test_typed_note_reader.py`:

```python
"""The judge's note reader: the scoped ``get_context item_ids`` lookup plus the hook's own
exemptions, so verdicts are keyed on exactly the text the hook looks up (spec §4)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from mnemo_memory.apps.cli import main as cli
from mnemo_memory.apps.cli.typed_decision_hook import (
    TypedHookModes,
    TypedPromptDecisions,
    TypedStepInput,
)
from mnemo_memory.packages.domain import ProjectId, TypedDecisionMode
from scripts.typed_decision_replay import approved_event_item_ids, knowledge_note_item_ids
from scripts.typed_decision_test_support import (
    FILLER_EVENT,
    KNOWLEDGE_PROMPT,
    PINNED_EVENT,
    USEFUL_NOTE,
    ScriptedJevTransport,
    run_hook,
    seed_hook_fixture,
    synthetic_overrides,
)


def test_the_reader_returns_exactly_the_text_the_hook_judges(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    steps: list[TypedStepInput] = []

    def observe(step: TypedStepInput, decisions: TypedPromptDecisions) -> None:
        steps.append(step)

    shadow = synthetic_overrides(
        fixture, ScriptedJevTransport(), TypedHookModes(relevance=TypedDecisionMode.SHADOW), observe
    )
    run_hook(fixture, KNOWLEDGE_PROMPT, shadow)
    [step] = steps
    hook = {candidate.item_id: candidate.text for candidate in step.filler_candidates}
    assert len(hook) == 3
    read = cli._note_candidates_by_id(fixture.data, fixture.binding.checkpoint_scope, tuple(hook))
    assert {candidate.item_id: candidate.text for candidate in read} == hook


def test_the_reader_keeps_the_hooks_exemptions(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    notes = knowledge_note_item_ids(fixture.data, fixture.binding)
    events = approved_event_item_ids(fixture.data, fixture.binding)
    assert (len(notes), len(events)) == (4, 2)  # two notes and two skill documents; two events
    texts = [
        candidate.text
        for candidate in cli._note_candidates_by_id(
            fixture.data, fixture.binding.checkpoint_scope, (*notes, *events)
        )
    ]
    assert len(texts) == 5
    assert any(USEFUL_NOTE in text for text in texts)
    assert any(FILLER_EVENT in text for text in texts)
    assert not any(PINNED_EVENT in text for text in texts)  # a pinned event is never judged


def test_the_reader_skips_ids_it_cannot_serve_in_this_scope(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    scope = fixture.binding.checkpoint_scope
    ids = (
        *knowledge_note_item_ids(fixture.data, fixture.binding),
        *approved_event_item_ids(fixture.data, fixture.binding),
    )
    foreign = replace(
        scope, project_id=ProjectId.from_string("00000000-0000-4000-8002-00000000beef")
    )
    assert cli._note_candidates_by_id(fixture.data, foreign, ids) == ()
    assert cli._note_candidates_by_id(fixture.data, scope, ("not-an-item", "")) == ()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_decision_axes.py tests/unit/test_typed_decision_hook.py tests/unit/test_typed_decision_composition.py tests/unit/test_typed_note_judge.py tests/unit/test_typed_note_reader.py -q`
Expected: FAIL — `ImportError` for `FILLER_QUESTION_VERSION`, `filler_probability`, `mnemo_memory.apps.cli.typed_note_judge` and `approved_event_item_ids`, and `TypeError: ... unexpected keyword argument 'deadline_seconds'`.

- [ ] **Step 3: Implement**

In `src/mnemo_memory/packages/model_gateway/decision_axes.py`, after `FILLER_DROP_AT = 0.7` add:

```python
# Bump when NOTE_SUBSTANCE's wording, labels or scores change: verdicts cached for the old
# question then stop counting (spec 2026-10-03 §5).
FILLER_QUESTION_VERSION = 1
```

change the comment on `FILLER_CHECK_BUDGET_SECONDS` to `# the prompt hook's cap for its one front-door request`, and replace `should_drop_note` with:

```python
def should_drop_filler(p_filler: float | None) -> bool:
    """Drop only confident filler: a p(filler) at or above the bar; no verdict means keep."""

    return p_filler is not None and p_filler >= FILLER_DROP_AT


def should_drop_note(result: ClassifierResult | None) -> bool:
    """Drop only confident filler; keep is the safe side."""

    return result is not None and should_drop_filler(result.escalation_score)
```

In `src/mnemo_memory/apps/cli/typed_decision_hook.py`, add `FILLER_QUESTION_VERSION` to the `decision_axes` import list, add the import:

```python
from mnemo_memory.packages.storage import note_verdict_key
```

and add after `notes_to_drop`:

```python
def filler_probability(outcome: TypedDecisionOutcome) -> float | None:
    """p(filler) from one answered note request; ``None`` when Jev gave no usable answer."""

    if outcome.unavailable_reason is not None:
        return None
    result = next(
        (value for value in outcome.results if value.axis_name == NOTE_SUBSTANCE.name), None
    )
    if result is None:
        return None
    score = result.escalation_score
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        return None
    return float(score)


def filler_verdict_key(candidate: FillerCandidate, model_version: str) -> str:
    """The verdict-cache key for one candidate's exact judged text (spec 2026-10-03 §5)."""

    return note_verdict_key(
        candidate.item_id,
        candidate.text,
        model_version=model_version,
        question_version=FILLER_QUESTION_VERSION,
    )
```

In `src/mnemo_memory/apps/cli/typed_decision_composition.py`, give both public builders a last keyword parameter and pass it through:

```python
def build_runtime_typed_decision_classifier(
    settings: PersonalSettings,
    *,
    data_directory: Path,
    environ: Mapping[str, str] | None = None,
    jev_transport: JevTransport | None = None,
    recorder: TypedDecisionRecorder | None = None,
    deadline_seconds: float = RUNTIME_DEADLINE_SECONDS,
) -> GuardedTypedDecisionClassifier | None:
    """Return ``None`` when disabled; otherwise a guard bound to the runtime source.

    ``deadline_seconds`` is the per-request deadline: the prompt hook keeps the 0.8 s default,
    and the background note judge passes 5 s (spec 2026-10-03 §4).
    """

    if not settings.experimental_typed_decisions_enabled:
        return None
    return _guard(
        settings,
        TypedDecisionSource.RUNTIME,
        data_directory,
        environ,
        jev_transport,
        recorder,
        deadline_seconds,
    )


def build_synthetic_typed_decision_classifier(
    settings: PersonalSettings,
    *,
    data_directory: Path,
    environ: Mapping[str, str] | None = None,
    jev_transport: JevTransport | None = None,
    recorder: TypedDecisionRecorder | None = None,
    deadline_seconds: float = RUNTIME_DEADLINE_SECONDS,
) -> GuardedTypedDecisionClassifier:
    """Return a synthetic-fixture guard for the replay (spec §8.2); the hook never calls it.

    Its source lets fixture text through the ``synthetic_only`` route, so callers must pass only
    text read from fixtures that declare synthetic provenance. The replay's priming pass uses
    the judge's 5 s ``deadline_seconds``.
    """

    return _guard(
        settings,
        TypedDecisionSource.SYNTHETIC_FIXTURE,
        data_directory,
        environ,
        jev_transport,
        recorder,
        deadline_seconds,
    )


def _guard(
    settings: PersonalSettings,
    source: TypedDecisionSource,
    data_directory: Path,
    environ: Mapping[str, str] | None,
    transport: JevTransport | None,
    recorder: TypedDecisionRecorder | None,
    deadline_seconds: float,
) -> GuardedTypedDecisionClassifier:
    variables = os.environ if environ is None else environ
    return GuardedTypedDecisionClassifier(
        _adapter_from_environment(settings, variables, transport),
        data_route=TypedDecisionDataRoute(settings.typed_decision_data_route),
        source=source,
        budget=LocalDailyModelBudget(
            data_directory,
            task_type=ModelTaskType.TYPED_DECISION,
            daily_input_tokens=settings.typed_decision_daily_input_tokens,
        ),
        workspace_id=_LOCAL_WORKSPACE,
        reservation=_RUNTIME_RESERVATION,
        deadline_seconds=deadline_seconds,
        recorder=recorder,
    )
```

In `src/mnemo_memory/apps/cli/main.py`:
- change `from collections.abc import Callable` to `from collections.abc import Callable, Sequence`;
- add `is_requestable_item_id` to the `mnemo_memory.packages.application.unified_context` import;
- in the `if TYPE_CHECKING:` block, add `FillerCandidate` to the `typed_decision_hook` import;
- add after `_pin_state`:

```python
_NOTE_READ_BATCH = 4


def _note_candidates_by_id(
    data_directory: Path, scope: MemoryScope, item_ids: Sequence[str]
) -> tuple[FillerCandidate, ...]:
    """Re-read notes by ID through the scoped ``get_context item_ids`` lookup (spec §4).

    The lookup rechecks scope, currentness and sensitivity. The hook's own
    ``filler_candidates`` then applies every exemption (pinned events, conflicts, non-normal
    sensitivity, the secret scan, unreadable text) and builds the 300-character judged text, so
    the judge keys verdicts on exactly the text the hook looks up. An ID the lookup cannot
    serve (gone, changed, foreign, malformed) is skipped. Nothing is sent anywhere.
    """

    from mnemo_memory.apps.cli import typed_decision_hook as typed

    requested = tuple(
        dict.fromkeys(item_id for item_id in item_ids if is_requestable_item_id(item_id))
    )
    if not requested:
        return ()
    candidates: list[FillerCandidate] = []
    with build_checkpoint_runtime(resolve_local_config(data_directory)) as runtime:
        service = _automatic_prompt_context_service(runtime, None)
        for start in range(0, len(requested), _NOTE_READ_BATCH):
            chunk = requested[start : start + _NOTE_READ_BATCH]
            packet = service.get_context(GetUnifiedContext(scope, item_ids=chunk))
            candidates.extend(
                typed.filler_candidates(
                    packet,
                    pinned_item_ids=_pinned_approved_item_ids(runtime, packet),
                    rendered_item_ids=frozenset(chunk),
                )
            )
    return tuple(candidates)
```

Create `src/mnemo_memory/apps/cli/typed_note_judge.py`:

```python
"""Background note judge: fills the verdict cache after the prompt (spec 2026-10-03 §4).

The prompt hook queues note IDs, never text, and starts ``typed-decisions judge-notes`` in a new
session. ``run_note_judge`` drains that queue under a single-instance lock: it re-reads each
note through a caller-supplied scoped reader, skips notes that already have a usable verdict or
three failed attempts on the same text, and asks Jev through a caller-built guard, four
requests in flight and at most 32 notes per run. ``judge_candidates`` is the shared core; the
replay's priming pass uses it with the synthetic guard. This module never imports the Jev
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
    """One background run; ``None`` when another judge already holds the run lock.

    No guard (the master switch is off) means nothing is read or taken. The run ends when the
    queue is empty, ``limit`` notes were asked, a policy block stops it, or it has taken a
    whole queue's worth of entries.
    """

    queue = LocalNoteJudgeQueue(data_directory)
    with queue.run_lock() as acquired:
        if not acquired:
            return None
        guard = build_guard()
        if guard is None:
            return JudgeTally(blocked=True)
        cache = LocalNoteVerdictCache(data_directory)
        tally = JudgeTally()
        taken_total = 0
        while tally.asked < limit and not tally.blocked and taken_total < MAXIMUM_QUEUED_NOTES:
            taken = queue.take(min(limit - tally.asked, JUDGE_IN_FLIGHT))
            if not taken:
                break
            taken_total += len(taken)
            candidates = _unjudged(_read(taken, read_notes), cache, model_version)
            tally = tally.plus(
                judge_candidates(guard, candidates, cache=cache, model_version=model_version)
            )
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
```

In `scripts/typed_decision_replay.py`, add after `_seed_approved_events`:

```python
def knowledge_note_item_ids(data: Path, binding: MemoryProjectBinding) -> tuple[str, ...]:
    """Every current knowledge section in the project, named as ``get_context`` names it."""

    with build_checkpoint_runtime(resolve_local_config(data)) as runtime:
        repository = runtime.knowledge_document_repository
        if repository is None:
            return ()
        return tuple(
            f"{_KNOWLEDGE_ITEM_PREFIX}{revision.document.document_id}:revision:"
            f"{revision.revision_id}:section:{index}"
            for revision in repository.list_current_revisions(binding.scope)
            for index in range(len(revision.document.sections))
        )


def approved_event_item_ids(data: Path, binding: MemoryProjectBinding) -> tuple[str, ...]:
    """Every approved event in the project's task scope, pinned ones included."""

    item_ids: list[str] = []
    with build_checkpoint_runtime(resolve_local_config(data)) as runtime:
        offset: int | None = 0
        while offset is not None:
            page = runtime.repository.list_approved_events(
                binding.checkpoint_scope, offset=offset, limit=50
            )
            item_ids.extend(f"{_EVENT_ITEM_PREFIX}{event.event_id}" for event in page.items)
            offset = page.next_offset
    return tuple(item_ids)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_decision_axes.py tests/unit/test_typed_decision_hook.py tests/unit/test_typed_decision_composition.py tests/unit/test_typed_note_judge.py tests/unit/test_typed_note_reader.py tests/architecture -q`
Expected: PASS.

Run: `uv run ruff format src scripts tests/unit && uv run ruff check src scripts tests/unit && uv run mypy src/mnemo_memory/apps/cli src/mnemo_memory/packages/model_gateway scripts/typed_decision_replay.py tests/unit/test_typed_note_judge.py tests/unit/test_typed_note_reader.py tests/unit/test_typed_decision_composition.py tests/unit/test_decision_axes.py && npm run -s architecture:check`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo_memory/apps/cli/typed_note_judge.py tests/unit/test_typed_note_judge.py tests/unit/test_typed_note_reader.py
git commit -m "feat(cli): note judge core, scoped note reader and 5 s guard" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- src/mnemo_memory/packages/model_gateway/decision_axes.py src/mnemo_memory/apps/cli/typed_decision_hook.py src/mnemo_memory/apps/cli/typed_decision_composition.py src/mnemo_memory/apps/cli/main.py src/mnemo_memory/apps/cli/typed_note_judge.py scripts/typed_decision_replay.py tests/unit/test_decision_axes.py tests/unit/test_typed_decision_hook.py tests/unit/test_typed_decision_composition.py tests/unit/test_typed_note_judge.py tests/unit/test_typed_note_reader.py
```

---

### Task 5: Hook: one request per prompt, filler verdicts from the cache, queueing

**Files:**
- Modify: `src/mnemo_memory/apps/cli/typed_decision_hook.py` (docstring `:1-7`; `decision_axes` import; `TypedStepInput` `:139-152`; `TypedAnswers` `:155-161`; `TypedTelemetryValues` `:183-203`; replace `notes_to_drop` `:344-356`; `combine_typed_decisions` `:442-527`; `ask_typed_questions` `:589-610`; `_unavailable_answers` `:678-686`)
- Modify: `src/mnemo_memory/apps/cli/main.py` (storage import `:217-222`; replace `_typed_prompt_render` `:1319-1418`; add two helpers after `_rendered_item_ids` `:1296-1309`)
- Modify: `scripts/typed_decision_test_support.py` (imports; add `prime_note_verdicts`)
- Test: `tests/unit/test_typed_decision_hook.py`, `tests/unit/test_typed_hook_integration.py`, `tests/contract/test_typed_hook_contract.py`

**Interfaces:**
- Consumes: Task 1 `LocalNoteVerdictCache.states`, `NoteVerdictState`; Task 2 `LocalNoteJudgeQueue.append`; Task 3 `AutomaticRouteTypedDecisions.notes_cached/notes_queued`; Task 4 `should_drop_filler`, `filler_verdict_key`, `_note_candidates_by_id`, `judge_candidates`, `JUDGE_DEADLINE_SECONDS`, `knowledge_note_item_ids`, `approved_event_item_ids`, the composition `deadline_seconds`.
- Produces:
  - `TypedStepInput.filler_verdicts: tuple[float | None, ...] = ()` — cached p(filler) per candidate, same order; `()` means none cached.
  - `TypedAnswers(front_door: TypedDecisionOutcome | None, tier: TierDecision | None = None)` — the `fillers` field is gone.
  - `TypedTelemetryValues.notes_cached: int = 0`, `.notes_queued: int = 0` (last fields).
  - `cached_filler_drops(candidates: Sequence[FillerCandidate], verdicts: Sequence[float | None]) -> tuple[str, ...]` (replaces `notes_to_drop`; `ValueError` on a length mismatch).
  - `ask_typed_questions` sends only the front-door request.
  - `main._note_verdict_states(data_directory: Path, settings: PersonalSettings, candidates: Sequence[FillerCandidate]) -> tuple[NoteVerdictState, ...]` — never raises.
  - `main._queue_unjudged_notes(data_directory: Path, scope: MemoryScope, candidates: Sequence[FillerCandidate], states: Sequence[NoteVerdictState]) -> int` — how many were queued; a failed write returns 0.
  - In `_typed_prompt_render`, the local variable `queued: int` (Task 6 passes it to the judge start).
  - `scripts.typed_decision_test_support.prime_note_verdicts(fixture: HookFixture, transport: ScriptedJevTransport) -> JudgeTally`.

- [ ] **Step 1: Add the test helper and update the tests to the new contract**

In `scripts/typed_decision_test_support.py`, add to the imports:

```python
from mnemo_memory.apps.cli.typed_note_judge import (
    JUDGE_DEADLINE_SECONDS,
    JudgeTally,
    judge_candidates,
)
from mnemo_memory.packages.storage import LocalNoteVerdictCache, SQLiteKnowledgeDocumentRepository
from scripts.typed_decision_replay import (
    approved_event_item_ids,
    evidence,
    knowledge_note_item_ids,
    skill_markdown,
)
```

(replacing the existing `from mnemo_memory.packages.storage import SQLiteKnowledgeDocumentRepository` and `from scripts.typed_decision_replay import evidence, skill_markdown` lines), and append:

```python
def prime_note_verdicts(fixture: HookFixture, transport: ScriptedJevTransport) -> JudgeTally:
    """Warm the fixture's verdict cache: judge every note and event through ``transport`` only.

    It uses the background judge's own reader (the scoped ``get_context item_ids`` lookup, so
    the pinned event is never sent) and ``judge_candidates`` with the synthetic-source guard.
    """

    item_ids = (
        *knowledge_note_item_ids(fixture.data, fixture.binding),
        *approved_event_item_ids(fixture.data, fixture.binding),
    )
    candidates = cli._note_candidates_by_id(
        fixture.data, fixture.binding.checkpoint_scope, item_ids
    )
    settings = PersonalSettings()
    guard = build_synthetic_typed_decision_classifier(
        settings,
        data_directory=fixture.data,
        environ={"TYPESAFE_API_KEY": FAKE_TYPESAFE_KEY},
        jev_transport=transport,
        deadline_seconds=JUDGE_DEADLINE_SECONDS,
    )
    return judge_candidates(
        guard,
        candidates,
        cache=LocalNoteVerdictCache(fixture.data),
        model_version=settings.typed_decision_model_id,
    )
```

In `tests/unit/test_typed_decision_hook.py`:
- in the `typed_decision_hook` import list, replace `notes_to_drop` with `cached_filler_drops`;
- after `BASE_STEP`, add `CACHED_STEP = replace(BASE_STEP, filler_verdicts=(0.05, 0.95))`;
- replace `test_notes_to_drop_only_drops_confident_filler` with:

```python
def test_cached_filler_drops_only_drop_confident_filler() -> None:
    candidates = tuple(FillerCandidate(_knowledge_id(index), "note") for index in range(4))
    assert cached_filler_drops(candidates, (0.95, 0.65, 0.05, None)) == (_knowledge_id(0),)
    assert cached_filler_drops(candidates, (0.7, 0.7, 0.7, 0.7)) == tuple(
        candidate.item_id for candidate in candidates
    )
    with pytest.raises(ValueError):
        cached_filler_drops(candidates, (0.95,))
```

- in `test_shadow_records_every_answer_and_changes_nothing`, call `combine_typed_decisions(CACHED_STEP, TypedAnswers(FRONT, LIGHT), step_ms=310, model_version="jev-1.13.0")` and add `"notes_cached": 2, "notes_queued": 0,` after `"skill": "differs",` in the expected dict;
- in `test_live_returns_only_the_decisions_to_apply`, use `replace(CACHED_STEP, modes=ALL_LIVE)` and `TypedAnswers(FRONT, LIGHT)`;
- in `test_hard_rule_leaves_memory_need_to_the_rules`, use `TypedAnswers(skill_only, None)`;
- in `test_below_bar_memory_answer_is_unsure_and_keeps_the_rules_needs`, use `TypedAnswers(unsure, None)`;
- in `test_no_front_door_request_is_not_asked`, use `TypedAnswers(None, None)`;
- replace `test_an_unavailable_front_door_falls_back_everywhere` with these two tests:

```python
def test_an_unavailable_front_door_falls_back_everywhere() -> None:
    blocked = _blocked(TypedDecisionUnavailableReason.DATA_ROUTE_BLOCKED)
    decisions = combine_typed_decisions(
        replace(BASE_STEP, modes=ALL_LIVE),
        TypedAnswers(blocked, TierDecision("heavy", "unavailable:data_route_blocked", None)),
        step_ms=2,
        model_version=None,
    )
    assert decisions.typed_needs is None and decisions.drop_item_ids == ()
    assert decisions.skill is None and decisions.show_hint is False
    telemetry = decisions.telemetry
    assert telemetry.front_door_outcome == "data_route_blocked"
    assert telemetry.memory_label == "unsure" and telemetry.memory_confidence_bucket is None
    assert (
        telemetry.notes_checked,
        telemetry.notes_dropped,
        telemetry.notes_unanswered,
        telemetry.notes_cached,
    ) == (2, 0, 0, 0)
    assert (telemetry.tier, telemetry.hint, telemetry.skill) == ("heavy", "none", "unsure")


def test_cached_drops_do_not_depend_on_the_front_door() -> None:
    """A verdict belongs to the note, so a front-door timeout keeps cached drops (§3)."""

    timed_out = _blocked(TypedDecisionUnavailableReason.TIMEOUT)
    decisions = combine_typed_decisions(
        replace(CACHED_STEP, modes=ALL_LIVE),
        TypedAnswers(timed_out, None),
        step_ms=2,
        model_version=None,
    )
    assert decisions.drop_item_ids == (FILLER_EVENT,)
    assert (decisions.telemetry.notes_cached, decisions.telemetry.notes_dropped) == (2, 1)
```

- in `test_telemetry_values_never_carry_prompt_note_or_skill_text`, add `filler_verdicts=(0.95,),` after the `filler_candidates=...` line of the `replace(...)` call and use `TypedAnswers(FRONT, LIGHT)`;
- replace `test_one_front_door_request_and_one_request_per_note_run_concurrently` with:

```python
def test_the_prompt_path_sends_only_the_front_door_request() -> None:
    adapter = ScriptedAdapter(SCRIPT)
    step = replace(STEP, filler_verdicts=(0.05, 0.95, None, 0.95))
    decisions = _decide(_factory(adapter), step)
    assert adapter.requests == [
        (("memory_need", "complexity", "tool_need", "skill_pick"), STEP.prompt)
    ]
    telemetry = decisions.telemetry
    assert telemetry.front_door_outcome == "answered"
    assert (
        telemetry.notes_checked,
        telemetry.notes_dropped,
        telemetry.notes_unanswered,
        telemetry.notes_cached,
    ) == (4, 2, 0, 3)
    assert decisions.drop_item_ids == ()  # shadow records the drops and applies none
    live = _decide(_factory(ScriptedAdapter(SCRIPT)), replace(step, modes=ALL_LIVE))
    assert live.drop_item_ids == ("knowledge:1", "knowledge:3")
    assert telemetry.memory_label == "project_docs"
    assert telemetry.tier == "light" and telemetry.hint == "would_show"
    assert telemetry.model_version == "jev-1.13.0"


def test_verdicts_that_do_not_match_the_candidates_are_a_step_error() -> None:
    step = replace(STEP, filler_verdicts=(0.95,))
    assert _decide(_factory(ScriptedAdapter(SCRIPT)), step).telemetry.front_door_outcome == (
        "typed_step_error"
    )
```

- in `test_requests_still_running_at_the_cap_count_as_timeouts`, change `assert telemetry.notes_unanswered == 4 and telemetry.notes_dropped == 0` to `assert telemetry.notes_unanswered == 0 and telemetry.notes_dropped == 0`;
- in `test_the_cap_counts_from_the_start_of_the_typed_step`, change `assert len(records) == 1 + len(STEP.filler_candidates)` to `assert len(records) == 1`;
- in `test_runtime_source_is_blocked_and_a_missing_guard_is_disabled`, change `assert blocked.telemetry.notes_unanswered == 4` to `assert blocked.telemetry.notes_unanswered == 0`.

In `tests/unit/test_typed_hook_integration.py`:
- imports: change `from collections.abc import Callable, Mapping` to `from collections.abc import Callable, Mapping, Sequence`; add `from datetime import UTC, datetime, timedelta`; add `LocalNoteJudgeQueue`, `LocalNoteVerdictCache` and `NoteVerdictState` to the `mnemo_memory.packages.storage` import; in the `scripts.typed_decision_test_support` import, remove `FILLER_EVENT` (no longer used) and add `prime_note_verdicts`; change `SHADOW, LIVE = TypedDecisionMode.SHADOW, TypedDecisionMode.LIVE` to `OFF, SHADOW, LIVE = TypedDecisionMode.OFF, TypedDecisionMode.SHADOW, TypedDecisionMode.LIVE`;
- in each of these tests, insert `prime_note_verdicts(fixture, ScriptedJevTransport())` on the line directly after the line that assigns `fixture`: `test_a_drop_is_cancelled_when_its_omission_line_does_not_fit`, `test_drops_never_apply_to_a_route_fetched_after_the_answer`, `test_filler_work_covers_only_rendered_notes_and_never_refills`, `test_pin_states_are_read_in_one_pass_and_one_by_one_only_after_a_failure`, `test_a_confirmed_live_route_keeps_its_prefetched_notes_and_their_drops`, `test_live_notes_dropped_counts_only_drops_that_happened`, `test_invalid_typed_telemetry_never_costs_the_context_or_the_event`, `test_unusable_typed_values_never_cost_the_applied_context`;
- replace `test_each_live_filler_drop_leaves_its_own_lower_rank_omission`, `test_an_unreadable_pin_state_keeps_the_event`, `test_without_the_gate_every_retrieved_packet_is_filler_checked` and `test_shadow_writes_the_typed_v1_group` with:

```python
def test_each_live_filler_drop_leaves_its_own_lower_rank_omission(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    transport = ScriptedJevTransport()
    prime_note_verdicts(fixture, transport)
    primed = transport.calls
    live = synthetic_overrides(fixture, transport, TypedHookModes(relevance=LIVE))

    off = run_hook(fixture, KNOWLEDGE_PROMPT)
    dropped = run_hook(fixture, KNOWLEDGE_PROMPT, live)

    assert transport.calls == primed  # relevance alone sends nothing on the prompt path
    assert _filler_omission_ids(off.context) == []
    ids = _filler_omission_ids(dropped.context)
    assert len(ids) == 2
    assert fixture.filler_event_id in ids
    assert any(item_id.startswith(fixture.filler_note_prefix) for item_id in ids)
    assert not any(item_id.startswith(fixture.useful_note_prefix) for item_id in ids)
    assert fixture.pinned_event_id not in ids
    assert all(PINNED_EVENT not in state for state in transport.states)  # pinned never sent


def test_an_unreadable_pin_state_keeps_the_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    transport = ScriptedJevTransport()
    prime_note_verdicts(fixture, transport)  # the event has a cached filler verdict
    primed = transport.calls

    def unreadable(*args: object, **kwargs: object) -> None:
        raise RuntimeError("synthetic storage failure")

    monkeypatch.setattr(SQLiteCheckpointRepository, "get_approved_event_records", unreadable)
    monkeypatch.setattr(SQLiteCheckpointRepository, "get_approved_event_record", unreadable)
    live = synthetic_overrides(fixture, transport, TypedHookModes(relevance=LIVE))
    ids = _filler_omission_ids(run_hook(fixture, KNOWLEDGE_PROMPT, live).context)
    assert len(ids) == 1 and ids[0].startswith(fixture.filler_note_prefix)
    assert transport.calls == primed  # nothing about any note is sent on the prompt path


def test_without_the_gate_every_retrieved_packet_is_filler_checked(tmp_path: Path) -> None:
    """With the semantic gate off there is no plan to gate on, so "push" means the rules
    retrieved a packet (spec §4.2): this router-uncertain prompt plans ``lazy_pull`` but today's
    hook still attaches its notes, so they are looked up in the cache."""

    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    off = run_hook(fixture, LAZY_PROMPT).context
    assert off is not None and fixture.filler_event_id in _item_ids(off)
    transport = ScriptedJevTransport()
    prime_note_verdicts(fixture, transport)
    primed = transport.calls
    live = synthetic_overrides(fixture, transport, TypedHookModes(relevance=LIVE))
    context = run_hook(fixture, LAZY_PROMPT, live).context
    assert transport.calls == primed
    assert _filler_omission_ids(context) == [fixture.filler_event_id]
    assert _item_ids(context) == _item_ids(off) - {fixture.filler_event_id}
    typed = _latest_event(fixture).typed
    assert typed is not None
    assert (typed.notes_checked, typed.notes_dropped, typed.notes_cached, typed.notes_queued) == (
        1,
        1,
        1,
        0,
    )
    # Shadow looks up the same notes and still changes nothing.
    shadow = synthetic_overrides(fixture, transport, TypedHookModes(relevance=SHADOW))
    assert run_hook(fixture, LAZY_PROMPT, shadow).context == off
    shadowed = _latest_event(fixture).typed
    assert shadowed is not None and shadowed.notes_dropped == 1
    assert transport.calls == primed


def test_shadow_writes_the_typed_v1_group(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    transport = ScriptedJevTransport(EVERYTHING)
    prime_note_verdicts(fixture, transport)
    shadow = synthetic_overrides(fixture, transport, TypedHookModes(SHADOW, SHADOW, SHADOW, SHADOW))
    run_hook(fixture, KNOWLEDGE_PROMPT, shadow)
    typed = _latest_event(fixture).typed
    assert typed is not None
    assert typed.front_door_outcome == "answered"
    assert typed.model_version == "jev-1.13.0"
    assert (typed.memory_label, typed.memory_confidence_bucket, typed.action) == (
        "nothing",
        ">=0.9",
        "none",
    )
    assert typed.agrees_with_rules is False
    assert (typed.notes_checked, typed.notes_dropped, typed.notes_unanswered) == (3, 2, 0)
    assert (typed.notes_cached, typed.notes_queued) == (3, 0)
    assert (typed.tier, typed.hint, typed.skill) == ("light", "would_show", "differs")
    assert 0 <= typed.step_ms <= 10_000
```

- append these new tests:

```python
def test_the_prompt_path_sends_exactly_one_request(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    transport = ScriptedJevTransport(EVERYTHING)
    prime_note_verdicts(fixture, transport)
    for modes in (
        TypedHookModes(SHADOW, SHADOW, SHADOW, SHADOW),
        TypedHookModes(LIVE, LIVE, LIVE, LIVE),
    ):
        before = transport.calls
        run_hook(fixture, KNOWLEDGE_PROMPT, synthetic_overrides(fixture, transport, modes))
        assert transport.calls - before == 1
        assert transport.questions[-1] == ("memory_need", "complexity", "tool_need", "skill_pick")


def test_a_missing_or_stale_verdict_keeps_the_note_and_queues_its_id(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    off = run_hook(fixture, KNOWLEDGE_PROMPT).context
    live = synthetic_overrides(fixture, ScriptedJevTransport(), TypedHookModes(relevance=LIVE))
    assert run_hook(fixture, KNOWLEDGE_PROMPT, live).context == off  # nothing cached: keep all
    typed = _latest_event(fixture).typed
    assert typed is not None
    assert (typed.notes_checked, typed.notes_cached, typed.notes_queued, typed.notes_dropped) == (
        3,
        0,
        3,
        0,
    )
    queue = LocalNoteJudgeQueue(fixture.data)
    assert queue.length() == 3
    encoded = queue.path.read_text("utf-8")
    for text in ("invoice", "ledger", FILLER_MARKER, "lunch"):
        assert text not in encoded

    prime_note_verdicts(fixture, ScriptedJevTransport())
    cache = LocalNoteVerdictCache(fixture.data)
    stored = json.loads(cache.path.read_text("utf-8"))
    month_ago = int((datetime.now(UTC) - timedelta(days=31)).timestamp())
    stored["entries"] = {
        key: [value[0], month_ago, value[2]] for key, value in stored["entries"].items()
    }
    cache.path.write_text(json.dumps(stored), encoding="utf-8")
    assert run_hook(fixture, KNOWLEDGE_PROMPT, live).context == off  # stale: keep all
    stale = _latest_event(fixture).typed
    assert stale is not None and (stale.notes_cached, stale.notes_queued) == (0, 3)
    assert queue.length() == 3  # deduplicated, not doubled


def test_an_edited_note_is_kept_until_its_new_text_is_judged(tmp_path: Path) -> None:
    """Review focus 2: an old verdict never drops a note whose text changed."""

    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    prime_note_verdicts(fixture, ScriptedJevTransport())
    (fixture.project / "notes" / "chatter.md").write_text(
        "# Invoice export chatter\nFILLER: the invoice export chatter moved to Friday's lunch.\n",
        "utf-8",
    )
    cli._refresh_project_knowledge(fixture.data, fixture.binding)
    live = synthetic_overrides(fixture, ScriptedJevTransport(), TypedHookModes(relevance=LIVE))
    context = run_hook(fixture, KNOWLEDGE_PROMPT, live).context
    assert _filler_omission_ids(context) == [fixture.filler_event_id]
    typed = _latest_event(fixture).typed
    assert typed is not None and (typed.notes_cached, typed.notes_queued) == (2, 1)


def test_a_note_that_failed_three_times_is_not_queued_again(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    steps: list[TypedStepInput] = []

    def observe(step: TypedStepInput, decisions: TypedPromptDecisions) -> None:
        steps.append(step)

    live = synthetic_overrides(
        fixture, ScriptedJevTransport(), TypedHookModes(relevance=LIVE), observe
    )
    run_hook(fixture, KNOWLEDGE_PROMPT, live)
    keys = [
        typed_decision_hook.filler_verdict_key(candidate, "jev-1.13.0")
        for candidate in steps[0].filler_candidates
    ]
    cache = LocalNoteVerdictCache(fixture.data)
    for _ in range(3):
        cache.record([(key, None) for key in keys])
    queue = LocalNoteJudgeQueue(fixture.data)
    queue.take(256)
    run_hook(fixture, KNOWLEDGE_PROMPT, live)
    typed = _latest_event(fixture).typed
    assert typed is not None
    assert (typed.notes_checked, typed.notes_cached, typed.notes_queued) == (3, 0, 0)
    assert queue.length() == 0


def test_an_unreadable_cache_keeps_every_note(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    off = run_hook(fixture, KNOWLEDGE_PROMPT).context
    prime_note_verdicts(fixture, ScriptedJevTransport())
    LocalNoteVerdictCache(fixture.data).path.write_text('{"version": 1, "entries": ', "utf-8")
    live = synthetic_overrides(fixture, ScriptedJevTransport(), TypedHookModes(relevance=LIVE))
    assert run_hook(fixture, KNOWLEDGE_PROMPT, live).context == off
    typed = _latest_event(fixture).typed
    assert typed is not None and typed.front_door_outcome != "typed_step_error"
    assert (typed.notes_cached, typed.notes_queued) == (0, 3)


def test_a_failed_queue_write_keeps_the_answers_and_queues_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)

    def broken(self: LocalNoteJudgeQueue, scope: MemoryScope, item_ids: Sequence[str]) -> int:
        raise OSError("synthetic queue failure")

    monkeypatch.setattr(LocalNoteJudgeQueue, "append", broken)
    shadow = synthetic_overrides(
        fixture, ScriptedJevTransport(EVERYTHING), TypedHookModes(SHADOW, SHADOW, SHADOW, SHADOW)
    )
    run_hook(fixture, KNOWLEDGE_PROMPT, shadow)
    typed = _latest_event(fixture).typed
    assert typed is not None and typed.front_door_outcome == "answered"
    assert (typed.notes_checked, typed.notes_queued) == (3, 0)


def test_relevance_off_reads_and_queues_no_verdicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    reads: list[int] = []

    def counted(self: LocalNoteVerdictCache, keys: Sequence[str]) -> tuple[NoteVerdictState, ...]:
        reads.append(len(keys))
        return ()

    monkeypatch.setattr(LocalNoteVerdictCache, "states", counted)
    quiet = synthetic_overrides(
        fixture, ScriptedJevTransport(EVERYTHING), TypedHookModes(SHADOW, OFF, SHADOW, SHADOW)
    )
    run_hook(fixture, KNOWLEDGE_PROMPT, quiet)
    assert reads == []
    assert not LocalNoteJudgeQueue(fixture.data).path.exists()
    typed = _latest_event(fixture).typed
    assert typed is not None and typed.front_door_outcome == "answered"
```

In `tests/contract/test_typed_hook_contract.py`, add `prime_note_verdicts` to the `scripts.typed_decision_test_support` import and, in `test_live_filler_omission_lines_fit_the_unchanged_v1_schema`, insert `prime_note_verdicts(fixture, ScriptedJevTransport())` directly after `fixture = seed_hook_fixture(tmp_path, semantic_gate=False)`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_typed_decision_hook.py tests/unit/test_typed_hook_integration.py tests/contract/test_typed_hook_contract.py -q`
Expected: FAIL — the unit file at collection with `ImportError: cannot import name 'cached_filler_drops'`; in the integration file, `test_the_prompt_path_sends_exactly_one_request` fails with `assert 4 == 1` (one front-door request plus three note requests), and the `notes_cached` / `notes_queued` assertions fail because both are still 0.

- [ ] **Step 3: Change the typed step**

In `src/mnemo_memory/apps/cli/typed_decision_hook.py`, replace the module docstring with:

```python
"""Per-prompt typed-decision step for the automatic-memory hook (spec 2026-10-02 §3-§4).

The functions here turn guard outcomes and cached note verdicts into plain values and never
read or write files. ``main.py`` prepares the local inputs (including the verdict-cache read),
runs the step, applies live answers on top of today's rules result, and queues the notes that
still need a verdict. Since the note-verdict cache (spec 2026-10-03 §3), the prompt path sends
exactly one Jev request, the front door. Prompt text, note text and skill names never enter
``TypedTelemetryValues``.
"""
```

In the `decision_axes` import list, replace `should_drop_note` with `should_drop_filler`.

In `TypedStepInput`, after `filler_candidates: tuple[FillerCandidate, ...] = ()` add:

```python
    # Cached p(filler) per candidate, in the same order; ``None`` (or an empty tuple) means no
    # usable verdict, so the note is kept (spec 2026-10-03 §3).
    filler_verdicts: tuple[float | None, ...] = ()
```

Replace `TypedAnswers` with:

```python
@dataclass(frozen=True, slots=True)
class TypedAnswers:
    """Guard outcomes for one prompt: the front door (or not asked) and the tier."""

    front_door: TypedDecisionOutcome | None
    tier: TierDecision | None = None
```

In `TypedTelemetryValues`, after `skill: str` add:

```python
    notes_cached: int = 0
    notes_queued: int = 0
```

Replace `notes_to_drop` with:

```python
def cached_filler_drops(
    candidates: Sequence[FillerCandidate], verdicts: Sequence[float | None]
) -> tuple[str, ...]:
    """Item IDs whose cached p(filler) is at least 0.7; a missing verdict keeps the note."""

    return tuple(
        candidate.item_id
        for candidate, p_filler in zip(candidates, verdicts, strict=True)
        if should_drop_filler(p_filler)
    )
```

In `combine_typed_decisions`, replace:

```python
    drops = (
        notes_to_drop(step.filler_candidates, answers.fillers) if modes.relevance is not OFF else ()
    )
    unanswered = sum(outcome.unavailable_reason is not None for outcome in answers.fillers)
```

with:

```python
    # Filler verdicts come from the local cache, never from this prompt's requests (spec
    # 2026-10-03 §3); a missing verdict keeps its note, whatever the front door answered.
    verdicts: tuple[float | None, ...] = step.filler_verdicts or tuple(
        None for _ in step.filler_candidates
    )
    drops = (
        cached_filler_drops(step.filler_candidates, verdicts)
        if modes.relevance is not OFF
        else ()
    )
```

and in the `TypedTelemetryValues(...)` call replace:

```python
        notes_checked=len(answers.fillers),
        notes_dropped=len(drops),
        notes_unanswered=unanswered,
```

with:

```python
        notes_checked=len(step.filler_candidates),
        notes_dropped=len(drops),
        notes_unanswered=0,
```

and add after `skill=comparison.value,`:

```python
        notes_cached=sum(verdict is not None for verdict in verdicts),
```

Replace `ask_typed_questions` with:

```python
async def ask_typed_questions(
    guard: GuardedTypedDecisionClassifier,
    step: TypedStepInput,
    *,
    total_deadline_seconds: float = FILLER_CHECK_BUDGET_SECONDS,
) -> TypedAnswers:
    """Send the one front-door request under the cap (spec 2026-10-03 §3).

    Filler verdicts are not asked here: they come from the local cache, which the background
    judge fills after the prompt.
    """

    axes = front_door_axes(step)
    front: TypedDecisionOutcome | None = None
    if axes:
        outcomes = await guard.ask_each(
            [(axes, step.prompt)], total_deadline_seconds=total_deadline_seconds
        )
        front = outcomes[0]
    tier: TierDecision | None = None
    if front is not None and step.modes.tier_hint is not OFF and not step.hard_rule:
        tier = await _tier(front, step.prompt)
    return TypedAnswers(front, tier)
```

Replace `_unavailable_answers` with:

```python
def _unavailable_answers(
    step: TypedStepInput, reason: TypedDecisionUnavailableReason
) -> TypedAnswers:
    unavailable = TypedDecisionOutcome((), reason, 0, None)
    front = unavailable if front_door_axes(step) else None
    tier: TierDecision | None = None
    if front is not None and step.modes.tier_hint is not OFF and not step.hard_rule:
        tier = TierDecision("heavy", f"unavailable:{reason.value}", None)
    return TypedAnswers(front, tier)
```

- [ ] **Step 4: Read verdicts and queue notes in the hook**

In `src/mnemo_memory/apps/cli/main.py`, add `LocalNoteJudgeQueue`, `LocalNoteVerdictCache` and `NoteVerdictState` to the `from mnemo_memory.packages.storage import (...)` block. Add after `_rendered_item_ids`:

```python
def _note_verdict_states(
    data_directory: Path, settings: PersonalSettings, candidates: Sequence[FillerCandidate]
) -> tuple[NoteVerdictState, ...]:
    """Each candidate's cached verdict state; any failure reads as no verdict (keep)."""

    if not candidates:
        return ()
    try:
        from mnemo_memory.apps.cli import typed_decision_hook as typed

        keys = tuple(
            typed.filler_verdict_key(candidate, settings.typed_decision_model_id)
            for candidate in candidates
        )
        return LocalNoteVerdictCache(data_directory).states(keys)
    except Exception:
        return tuple(NoteVerdictState(None, 0) for _ in candidates)


def _queue_unjudged_notes(
    data_directory: Path,
    scope: MemoryScope,
    candidates: Sequence[FillerCandidate],
    states: Sequence[NoteVerdictState],
) -> int:
    """Queue the IDs (never text) of candidates that still need a verdict; return how many.

    A note with a usable verdict, or with three failed attempts on this exact text, is not
    queued. A failed write queues nothing and changes nothing else (spec 2026-10-03 §3).
    """

    unjudged = tuple(
        candidate.item_id
        for candidate, state in zip(candidates, states, strict=True)
        if state.needs_judging
    )
    if not unjudged:
        return 0
    try:
        LocalNoteJudgeQueue(data_directory).append(scope, unjudged)
    except Exception:
        return 0
    return len(unjudged)
```

Replace the whole `_typed_prompt_render` function with:

```python
def _typed_prompt_render(
    data_directory: Path,
    scope: MemoryScope,
    prompt: str,
    client: ClientName,
    settings: PersonalSettings,
    modes: TypedHookModes,
    trace: _AutomaticShadowTrace | None,
    rules: _PromptRender,
    overrides: TypedHookOverrides | None,
    *,
    started: float,
) -> tuple[_PromptRender, _AutomaticShadowTrace | None, AutomaticRouteTypedDecisions | None]:
    """Run the typed step on top of today's result; any exception keeps today's result.

    The whole step is wrapped (spec 2026-10-02 §7): the module import, the local preparation,
    the verdict-cache read, the one Jev request, the combine, the live application and the
    judge-queue write. The hook's own outer catch would attach no context at all. ``started``
    is the step's clock, taken before the mode read: the Jev request gets what is left of the
    0.8 s cap, and ``step_ms`` counts from it. Filler verdicts come from the local cache (spec
    2026-10-03 §3); notes without one are kept and their IDs queued for the background judge.
    """

    try:
        from mnemo_memory.apps.cli import typed_decision_hook as typed

        bounded = bounded_automatic_context_prompt(prompt)
        learned = (
            trace.learned_phrases
            if trace is not None
            else _learned_route_phrases(data_directory, scope)
        )
        rules_plan = (
            trace.plan
            if trace is not None
            else plan_automatic_context_needs(bounded, learned_phrases=learned)
        )
        # Filler verdicts apply only to the notes a push action pre-fetched (spec §4.2).
        # Without the semantic gate (no trace) nothing gates on the plan, so a retrieved packet
        # is the push. Skill discovery and hard routes retrieve none.
        checked = (
            rules.result.packet
            if modes.relevance is not TypedDecisionMode.OFF
            and (trace is None or rules_plan.action in _PUSH_ACTIONS)
            else None
        )
        local = _typed_local_inputs(
            data_directory,
            scope,
            client,
            checked,
            list_skills=modes.skill is not TypedDecisionMode.OFF,
            listing=rules.result.skill_listing,
        )
        candidates = (
            ()
            if checked is None
            else typed.filler_candidates(
                checked,
                pinned_item_ids=local.pinned_item_ids,
                rendered_item_ids=_rendered_item_ids(rules.rendered),
            )
        )
        verdicts = _note_verdict_states(data_directory, settings, candidates)
        step = typed.TypedStepInput(
            prompt=bounded,
            modes=modes,
            hard_rule=rules_plan.hard_rule,
            rules_plan=rules_plan,
            rules_route=rules.result.decision.route,
            learned_phrases=learned,
            skill_names=tuple(skill.name for skill in local.skills),
            skills_over_limit=local.skills_over_limit,
            keyword_skill_names=tuple(
                candidate.skill.name for candidate in rules.result.discovered_skills
            ),
            filler_candidates=candidates,
            filler_verdicts=tuple(state.p_filler for state in verdicts),
        )
        guard_factory = (
            overrides.guard_factory
            if overrides is not None
            else _runtime_guard_factory(settings, data_directory)
        )
        decisions = typed.decide_typed_prompt(guard_factory, step, started=started)
        if overrides is not None and overrides.observer is not None:
            with suppress(Exception):
                overrides.observer(step, decisions)
        applied = _apply_typed_decisions(
            data_directory, scope, prompt, client, trace, rules, decisions, local, step
        )
        queued = _queue_unjudged_notes(data_directory, scope, candidates, verdicts)
    except Exception:
        return rules, trace, _typed_step_error_telemetry(modes, started)
    try:
        values = replace(decisions.telemetry, notes_queued=queued)
        if modes.relevance is TypedDecisionMode.LIVE:
            values = replace(values, notes_dropped=applied.notes_dropped)
        telemetry: AutomaticRouteTypedDecisions | None = typed.route_telemetry(
            replace(values, step_ms=_elapsed_milliseconds(started))
        )
        if not isinstance(telemetry, AutomaticRouteTypedDecisions):
            telemetry = None
    except Exception:
        telemetry = None  # losing the record is acceptable; losing the applied context is not
    return applied.render, applied.trace, telemetry
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_typed_decision_hook.py tests/unit/test_typed_hook_integration.py tests/unit/test_typed_note_reader.py tests/contract tests/security tests/unit/test_automatic_memory.py -q`
Expected: PASS, including the unchanged `test_shadow_output_is_byte_identical_to_off` and `test_off_never_enters_the_typed_step`.

Run: `uv run ruff format src scripts tests && uv run ruff check src scripts tests && uv run mypy src/mnemo_memory/apps/cli scripts/typed_decision_test_support.py tests/unit/test_typed_decision_hook.py tests/unit/test_typed_hook_integration.py tests/contract/test_typed_hook_contract.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(hook): one Jev request per prompt; filler verdicts from the cache" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- src/mnemo_memory/apps/cli/typed_decision_hook.py src/mnemo_memory/apps/cli/main.py scripts/typed_decision_test_support.py tests/unit/test_typed_decision_hook.py tests/unit/test_typed_hook_integration.py tests/contract/test_typed_hook_contract.py
```

---

### Task 6: Background judge command and the detached start

**Files:**
- Modify: `src/mnemo_memory/apps/cli/main.py` (`mnemo_memory.packages.domain` import `:165-194`; end of `_typed_prompt_render`; new helpers after `_queue_unjudged_notes`; new hidden command after `typed_decisions_disable` `:4084-4096`)
- Test: `tests/unit/test_typed_note_judge_start.py` (new)

**Interfaces:**
- Consumes: Task 2 `LocalNoteJudgeQueue`; Task 4 `run_note_judge`, `JUDGE_DEADLINE_SECONDS`, `_note_candidates_by_id`, composition `deadline_seconds`; Task 5 the `queued: int` local in `_typed_prompt_render`.
- Produces:
  - `main._judge_route_open(settings: PersonalSettings) -> bool` — whether the data route lets `RUNTIME` text leave the machine (always `False` under `synthetic_only`).
  - `main._start_note_judge(data_directory: Path) -> None` — `subprocess.Popen([sys.executable, "-m", "mnemo_memory.apps.cli.main", "typed-decisions", "judge-notes", "--data-dir", str(data_directory)], stdin/stdout/stderr=DEVNULL, start_new_session=True, close_fds=True)`; never waits.
  - `main._maybe_start_note_judge(data_directory: Path, settings: PersonalSettings, modes: TypedHookModes, overrides: TypedHookOverrides | None, queued: int) -> None` — never raises.
  - Hidden CLI command `mnemo-memory typed-decisions judge-notes --data-dir <d>`: prints nothing, always exits 0.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_typed_note_judge_start.py`:

```python
"""The hook starts the background judge detached, after its output, only when it may; the
judge command itself is silent and asks Jev only through a 5 s runtime guard (spec §3-§4)."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from mnemo_memory.apps.cli import main as cli
from mnemo_memory.apps.cli import typed_decision_composition
from mnemo_memory.apps.cli.typed_decision_hook import TypedHookModes, TypedHookOverrides
from mnemo_memory.connectors.typesafe import jev_provider
from mnemo_memory.packages.application import PersonalSettings, PersonalSettingsStore
from mnemo_memory.packages.application.settings import with_typed_decision_mode
from mnemo_memory.packages.domain import TypedDecisionKind, TypedDecisionMode, TypedDecisionSource
from mnemo_memory.packages.model_gateway.typed_decisions import GuardedTypedDecisionClassifier
from mnemo_memory.packages.storage import LocalNoteJudgeQueue, LocalNoteVerdictCache
from scripts.typed_decision_replay import approved_event_item_ids, knowledge_note_item_ids
from scripts.typed_decision_test_support import (
    FAKE_TYPESAFE_KEY,
    KNOWLEDGE_PROMPT,
    HookFixture,
    run_hook,
    seed_hook_fixture,
)

OFF, SHADOW = TypedDecisionMode.OFF, TypedDecisionMode.SHADOW
runner = CliRunner()


def _relevance_shadow(fixture: HookFixture) -> None:
    """The real hook's own settings: master switch on, relevance in shadow."""

    base = PersonalSettings(
        experimental_semantic_memory_enabled=True, experimental_typed_decisions_enabled=True
    )
    PersonalSettingsStore(fixture.data).save(
        with_typed_decision_mode(base, TypedDecisionKind.RELEVANCE, SHADOW)
    )


def test_the_hook_starts_the_judge_once_after_its_output_is_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    off = run_hook(fixture, KNOWLEDGE_PROMPT)
    _relevance_shadow(fixture)
    order: list[str] = []
    apply = cli._apply_typed_decisions

    def applied(*args: Any, **kwargs: Any) -> Any:
        result = apply(*args, **kwargs)
        order.append("applied")
        return result

    monkeypatch.setattr(cli, "_apply_typed_decisions", applied)
    monkeypatch.setattr(cli, "_judge_route_open", lambda settings: True)
    monkeypatch.setattr(
        cli, "_start_note_judge", lambda data_directory: order.append(f"start:{data_directory}")
    )
    seen = run_hook(fixture, KNOWLEDGE_PROMPT)
    assert (seen.context, seen.delivery_keys) == (off.context, off.delivery_keys)
    assert order == ["applied", f"start:{fixture.data}"]
    assert LocalNoteJudgeQueue(fixture.data).length() == 3


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        ("all_hold", 1),
        ("route_closed", 0),
        ("relevance_off", 0),
        ("master_off", 0),
        ("nothing_queued", 0),
        ("replay_overrides", 0),
    ],
)
def test_the_judge_starts_only_when_every_condition_holds(
    monkeypatch: pytest.MonkeyPatch, condition: str, expected: int
) -> None:
    settings = PersonalSettings(experimental_typed_decisions_enabled=condition != "master_off")
    modes = TypedHookModes(relevance=OFF if condition == "relevance_off" else SHADOW)
    overrides = (
        TypedHookOverrides(lambda recorder: None, modes)
        if condition == "replay_overrides"
        else None
    )
    started: list[Path] = []
    monkeypatch.setattr(cli, "_judge_route_open", lambda settings: condition != "route_closed")
    monkeypatch.setattr(cli, "_start_note_judge", started.append)
    queued = 0 if condition == "nothing_queued" else 3
    cli._maybe_start_note_judge(Path("/data"), settings, modes, overrides, queued)
    assert len(started) == expected


def test_the_route_stays_closed_to_the_judge_under_synthetic_only() -> None:
    assert cli._judge_route_open(PersonalSettings()) is False


def test_the_real_hook_never_starts_the_judge_while_the_route_is_synthetic_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    _relevance_shadow(fixture)
    started: list[Path] = []
    monkeypatch.setattr(cli, "_start_note_judge", started.append)
    run_hook(fixture, KNOWLEDGE_PROMPT)
    assert started == []
    assert LocalNoteJudgeQueue(fixture.data).length() == 3  # queued, waiting for a route


def test_a_failed_start_changes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    off = run_hook(fixture, KNOWLEDGE_PROMPT)
    _relevance_shadow(fixture)

    def broken(data_directory: Path) -> None:
        raise OSError("synthetic spawn failure")

    monkeypatch.setattr(cli, "_judge_route_open", lambda settings: True)
    monkeypatch.setattr(cli, "_start_note_judge", broken)
    seen = run_hook(fixture, KNOWLEDGE_PROMPT)
    assert (seen.context, seen.delivery_keys) == (off.context, off.delivery_keys)
    assert seen.telemetry_event_id is not None


def test_the_start_is_detached_names_only_the_data_directory_and_never_waits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review focus 5: a path with spaces and quotes is one argv element, never a shell."""

    data = tmp_path / "Application Support" / "it's mnemo"
    seen: dict[str, Any] = {}

    class FakeProcess:
        def __init__(self, command: list[str], **kwargs: Any) -> None:
            seen.update(kwargs, command=command)

        def wait(self, timeout: float | None = None) -> int:
            raise AssertionError("the hook must never wait for the judge")

    monkeypatch.setenv("TYPESAFE_API_KEY", FAKE_TYPESAFE_KEY)
    monkeypatch.setattr(cli.subprocess, "Popen", FakeProcess)
    cli._start_note_judge(data)
    assert seen["command"] == [
        sys.executable,
        "-m",
        "mnemo_memory.apps.cli.main",
        "typed-decisions",
        "judge-notes",
        "--data-dir",
        str(data),
    ]
    assert seen["start_new_session"] is True
    assert seen["stdin"] == seen["stdout"] == seen["stderr"] == subprocess.DEVNULL
    assert "shell" not in seen and "env" not in seen
    assert FAKE_TYPESAFE_KEY not in json.dumps(seen["command"])


def test_the_judge_command_prints_nothing_and_exits_zero_when_it_cannot_run(
    tmp_path: Path,
) -> None:
    (tmp_path / "settings.json").write_text("{not json", encoding="utf-8")
    result = runner.invoke(
        cli.app, ["typed-decisions", "judge-notes", "--data-dir", str(tmp_path)]
    )
    assert (result.exit_code, result.output) == (0, "")


def test_the_judge_command_builds_a_five_second_runtime_guard_and_sends_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    PersonalSettingsStore(fixture.data).save(
        PersonalSettings(
            experimental_semantic_memory_enabled=True, experimental_typed_decisions_enabled=True
        )
    )
    item_ids = (
        *knowledge_note_item_ids(fixture.data, fixture.binding),
        *approved_event_item_ids(fixture.data, fixture.binding),
    )
    LocalNoteJudgeQueue(fixture.data).append(fixture.binding.checkpoint_scope, item_ids)
    calls: list[str] = []

    def counting(url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> bytes:
        calls.append(url)
        raise AssertionError("runtime note text reached the Jev transport")

    built: list[GuardedTypedDecisionClassifier] = []
    original = typed_decision_composition.build_runtime_typed_decision_classifier

    def recording(*args: Any, **kwargs: Any) -> GuardedTypedDecisionClassifier | None:
        guard = original(*args, **kwargs)
        if guard is not None:
            built.append(guard)
        return guard

    monkeypatch.setattr(jev_provider, "_urllib_transport", counting)
    monkeypatch.setattr(
        typed_decision_composition, "build_runtime_typed_decision_classifier", recording
    )
    result = runner.invoke(
        cli.app,
        ["typed-decisions", "judge-notes", "--data-dir", str(fixture.data)],
        env={"TYPESAFE_API_KEY": FAKE_TYPESAFE_KEY},
    )
    assert (result.exit_code, result.output) == (0, "")
    assert [guard._deadline for guard in built] == [5.0]
    assert all(guard._source is TypedDecisionSource.RUNTIME for guard in built)
    assert calls == []
    assert LocalNoteVerdictCache(fixture.data).entry_count() == 0
    assert not (fixture.data / "typed_decision-budget.json").exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_typed_note_judge_start.py -q`
Expected: FAIL — `AttributeError: <module 'mnemo_memory.apps.cli.main'> has no attribute '_judge_route_open'` (and `_maybe_start_note_judge`, `_start_note_judge`), and the command tests exit 2 with "No such command 'judge-notes'".

- [ ] **Step 3: Implement the start and the command**

In `src/mnemo_memory/apps/cli/main.py`, add `TypedDecisionDataRoute`, `TypedDecisionSource` and `typed_decision_source_permitted` to the `from mnemo_memory.packages.domain import (...)` block.

Add after `_queue_unjudged_notes`:

```python
def _judge_route_open(settings: PersonalSettings) -> bool:
    """Whether the data route lets runtime note text leave the machine (never on synthetic_only).

    Starting a judge that could only be ``data_route_blocked`` would cost a Python process per
    prompt and judge nothing, so the hook does not start one (plan decision; spec §3 queues
    regardless).
    """

    return typed_decision_source_permitted(
        TypedDecisionDataRoute(settings.typed_decision_data_route), TypedDecisionSource.RUNTIME
    )


def _start_note_judge(data_directory: Path) -> None:
    """Start ``typed-decisions judge-notes`` detached: a new session, no pipes, no waiting.

    The command line names only the data directory, as one argument and without a shell. Note
    text and the API key never appear on it; the child inherits the hook's environment.
    """

    subprocess.Popen(
        [
            sys.executable,
            "-m",
            "mnemo_memory.apps.cli.main",
            "typed-decisions",
            "judge-notes",
            "--data-dir",
            str(data_directory),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )


def _maybe_start_note_judge(
    data_directory: Path,
    settings: PersonalSettings,
    modes: TypedHookModes,
    overrides: TypedHookOverrides | None,
    queued: int,
) -> None:
    """Start the background judge after the hook's output is built (spec 2026-10-03 §3).

    Only the real hook starts it (never under replay overrides), and only when this prompt
    queued a note, relevance is on, the master switch is on and the route is open. Nothing
    waits on it, and a failure to start is ignored.
    """

    try:
        if (
            overrides is None
            and queued > 0
            and modes.relevance is not TypedDecisionMode.OFF
            and settings.experimental_typed_decisions_enabled
            and _judge_route_open(settings)
        ):
            _start_note_judge(data_directory)
    except Exception:
        return
```

At the end of `_typed_prompt_render`, replace the last line `return applied.render, applied.trace, telemetry` with:

```python
    _maybe_start_note_judge(data_directory, settings, modes, overrides, queued)
    return applied.render, applied.trace, telemetry
```

Add after `typed_decisions_disable`:

```python
@typed_decisions_app.command("judge-notes", hidden=True)
def typed_decisions_judge_notes(
    data_dir: Path | None = typer.Option(None, "--data-dir"),  # noqa: B008
) -> None:
    """Judge queued notes in the background; the prompt hook starts it (spec 2026-10-03 §4).

    It prints nothing and exits 0 whatever happens, so a broken judge never reaches a session.
    """

    with suppress(Exception):
        _judge_queued_notes(data_dir)


def _judge_queued_notes(data_dir: Path | None) -> None:
    from mnemo_memory.apps.cli.typed_decision_composition import (
        build_runtime_typed_decision_classifier,
    )
    from mnemo_memory.apps.cli.typed_note_judge import JUDGE_DEADLINE_SECONDS, run_note_judge

    data_directory = resolve_local_config(data_dir).data_directory
    settings = PersonalSettingsStore(data_directory).load()

    def read(scope: MemoryScope, item_ids: tuple[str, ...]) -> tuple[FillerCandidate, ...]:
        return _note_candidates_by_id(data_directory, scope, item_ids)

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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_typed_note_judge_start.py tests/unit/test_typed_hook_integration.py tests/security tests/contract -q`
Expected: PASS.

Run: `uv run ruff format src/mnemo_memory/apps/cli/main.py tests/unit/test_typed_note_judge_start.py && uv run ruff check src/mnemo_memory/apps/cli/main.py tests/unit/test_typed_note_judge_start.py && uv run mypy src/mnemo_memory/apps/cli tests/unit/test_typed_note_judge_start.py && npm run -s architecture:check`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_typed_note_judge_start.py
git commit -m "feat(cli): background typed-decisions judge-notes and detached start" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- src/mnemo_memory/apps/cli/main.py tests/unit/test_typed_note_judge_start.py
```

---

### Task 7: `typed-decisions status` additions

**Files:**
- Modify: `src/mnemo_memory/apps/cli/main.py:3970-4008` (`typed_decisions_status`)
- Test: `tests/unit/test_typed_decisions_cli.py`

**Interfaces:**
- Consumes: Task 1 `LocalNoteVerdictCache.entry_count`; Task 2 `LocalNoteJudgeQueue.length`, `.judge_running`.
- Produces: two new keys in the `status` JSON, placed before `"sends_real_prompts"`:
  - `"note_verdicts": {"cache": "available" | "unavailable", "entries": int | None}`
  - `"note_judge": {"queued": int | None, "running": bool}`

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_typed_decisions_cli.py`, add the imports:

```python
from uuid import UUID

from mnemo_memory.packages.domain import (
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.storage import (
    LocalNoteJudgeQueue,
    LocalNoteVerdictCache,
    note_verdict_key,
)
```

(merging `MemoryScope` and the rest into the existing `mnemo_memory.packages.domain` import). In `test_status_reports_switches_locks_budget_and_key_presence_only`, add these two entries to the expected dict, directly before `"sends_real_prompts": False,`:

```python
        "note_verdicts": {"cache": "available", "entries": 0},
        "note_judge": {"queued": 0, "running": False},
```

and add after the `with_key` assertions:

```python
    assert not (tmp_path / ".typed-decision-judge.lock").exists()  # status never creates it
```

Append:

```python
def test_status_shows_the_verdict_cache_the_queue_and_a_running_judge(tmp_path: Path) -> None:
    scope = MemoryScope(
        OwnerId.from_string("00000000-0000-4000-8000-000000000001"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string("00000000-0000-4000-8001-000000000001"),
        ProjectId.from_string("00000000-0000-4000-8002-000000000001"),
    )
    first, second = (f"approved-episodic:{UUID(int=index)}" for index in (1, 2))
    key = note_verdict_key(first, "a note", model_version="jev-1.13.0", question_version=1)
    LocalNoteVerdictCache(tmp_path).record([(key, 0.9)])
    queue = LocalNoteJudgeQueue(tmp_path)
    queue.append(scope, (first, second))
    with queue.run_lock() as held:
        assert held is True
        shown = json.loads(_invoke(tmp_path, "status").output)
    assert shown["note_verdicts"] == {"cache": "available", "entries": 1}
    assert shown["note_judge"] == {"queued": 2, "running": True}

    LocalNoteVerdictCache(tmp_path).path.write_text("{", "utf-8")
    queue.path.write_text("{", "utf-8")
    broken = json.loads(_invoke(tmp_path, "status").output)
    assert broken["note_verdicts"] == {"cache": "unavailable", "entries": None}
    assert broken["note_judge"] == {"queued": None, "running": False}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_typed_decisions_cli.py -q`
Expected: FAIL — the exact-dict comparison misses `note_verdicts` and `note_judge`, and `KeyError: 'note_verdicts'`.

- [ ] **Step 3: Implement**

In `typed_decisions_status`, after the `reserved = LocalDailyModelBudget(...).reserved_today()` statement add:

```python
    cache_entries = LocalNoteVerdictCache(config.data_directory).entry_count()
    note_queue = LocalNoteJudgeQueue(config.data_directory)
```

and in the `_show({...})` dict, directly before `"sends_real_prompts": False,`, add:

```python
            "note_verdicts": {
                "cache": "unavailable" if cache_entries is None else "available",
                "entries": cache_entries,
            },
            "note_judge": {
                "queued": note_queue.length(),
                "running": note_queue.judge_running(),
            },
```

Change the command's help text to `"Show switches, modes, locks, today's budget, the note-verdict cache and the judge."`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_typed_decisions_cli.py -q`
Expected: PASS.

Run: `uv run ruff format src/mnemo_memory/apps/cli/main.py tests/unit/test_typed_decisions_cli.py && uv run ruff check src/mnemo_memory/apps/cli/main.py tests/unit/test_typed_decisions_cli.py && uv run mypy src/mnemo_memory/apps/cli/main.py tests/unit/test_typed_decisions_cli.py`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(cli): status shows the note-verdict cache, queue and judge" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- src/mnemo_memory/apps/cli/main.py tests/unit/test_typed_decisions_cli.py
```

---

### Task 8: Replay: priming pass and its gate

**Files:**
- Modify: `scripts/typed_decision_replay.py` (module docstring `:1-19`; `ReplayResult` `:180-240`; new `PrimingResult`, `Primer`, `prime_replay_seed`, `_priming_request` after `approved_event_item_ids`; `run_replay_case` `:507-575`; `score_replay` `:735-789`; `_score_filler` `:878-934`)
- Modify: `scripts/run_typed_decision_replay.py` (docstring, imports, `main`, new `_primed`)
- Test: `tests/evals/test_typed_decision_replay.py`

**Interfaces:**
- Consumes: Task 4 `knowledge_note_item_ids`, `approved_event_item_ids`, `judge_candidates`, `JUDGE_DEADLINE_SECONDS`, `_note_candidates_by_id`, the composition `deadline_seconds`; Task 1 `LocalNoteVerdictCache`; Task 3 `typed.notes_cached`; Task 6 `_judge_route_open`, `_start_note_judge`.
- Produces:
  - `ReplayResult.notes_cached: int = 0` (last field).
  - `@dataclass(frozen=True, slots=True) class PrimingResult(set_name: str, notes: int, answered: int, blocked: bool)` with `to_dict() -> dict[str, Any]` (`{"notes", "answered", "share", "blocked"}`) and `failed(set_name) -> PrimingResult` (classmethod; `(set_name, 0, 0, True)`).
  - `Primer = Callable[[ReplaySeed], PrimingResult]`.
  - `prime_replay_seed(seed: ReplaySeed, *, environ: Mapping[str, str], jev_transport: JevTransport | None = None, live_calls_authorized: bool = False) -> PrimingResult` — raises `ReplayRefusedError` without a transport or authorization, or for an unverified seed.
  - `score_replay(cases, results, seeds, priming: Mapping[str, PrimingResult] | None = None)`; each filler section gains `"priming": {"asked", "answered", "share"[, "blocked"]}` and the gate `"priming_answered_share"`; the filler `"answered"` sub-gate now counts checked notes with a cached verdict.
  - `run_typed_decision_replay.main(..., primer: Primer | None = None, ...)`; the report gains `"priming": {set_name: PrimingResult.to_dict()}`.

- [ ] **Step 1: Write the failing tests**

In `tests/evals/test_typed_decision_replay.py`:
- add imports: `from mnemo_memory.connectors.typesafe import jev_provider`; add `PersonalSettings` and `PersonalSettingsStore` to the `mnemo_memory.packages.application` import; `from mnemo_memory.packages.storage import LocalNoteJudgeQueue, LocalNoteVerdictCache`; add `Primer`, `PrimingResult` and `prime_replay_seed` to the `scripts.typed_decision_replay` import;
- after `FAKE_ENVIRON = ...` add:

```python
PRIMED = {"dev": PrimingResult("dev", 2, 2, False)}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Defense in depth: no replay test may reach the real Jev transport."""

    def refuse(url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> bytes:
        raise AssertionError("a replay test reached the network transport")

    monkeypatch.setattr(jev_provider, "_urllib_transport", refuse)
```

- after `_in_process`, add:

```python
def _primer(transport: Callable[[str, bytes, Mapping[str, str], float], bytes]) -> Primer:
    def prime(seed: ReplaySeed) -> PrimingResult:
        return prime_replay_seed(seed, environ=FAKE_ENVIRON, jev_transport=transport)

    return prime
```

- in `test_in_process_replay_runs_both_arms_and_scores_them`, insert `priming = prime_replay_seed(seed, environ=FAKE_ENVIRON, jev_transport=oracle)` directly after `oracle = ReplayOracle()`; after `assert set(notes.applied_drop_item_ids) <= set(notes.dropped_item_ids)` insert `assert notes.notes_cached == len(notes.checked_item_ids)`; change `report = score_replay(subset, results, {"holdout": seed})` to `report = score_replay(subset, results, {"holdout": seed}, priming={"holdout": priming})`; and after `assert holdout["filler"]["relevant_dropped"] == 0` add `assert holdout["filler"]["gates"]["priming_answered_share"] is True`;
- in `_result`, add a keyword parameter `cached: int = 0` after `skill: str = "agreed",` and pass `notes_cached=cached if typed else 0,` as the last argument of the `ReplayResult(...)` call;
- replace `test_score_replay_applies_the_section_8_3_gates` with:

```python
def test_score_replay_applies_the_section_8_3_gates() -> None:
    skill = ReplayCase("dev", "skill", "s1", "p1", expected_skill="test-plan")
    none = ReplayCase("dev", "skill", "s2", "p2", expected_skill="none")
    notes = ReplayCase("dev", "notes", "n1", "p3")
    seeds = {"dev": _seed({"knowledge:good:": "relevant", "knowledge:noise:": "noise"})}
    cases = [skill, none, notes]
    passing = [
        _result(skill, "rules", tokens=200, skill_pick="none"),
        _result(skill, "typed", tokens=100, hook_ms=500, skill_pick="test-plan"),
        _result(none, "rules", tokens=200, skill_pick="release-notes"),
        _result(none, "typed", tokens=100, hook_ms=500, skill_pick="none"),
        _result(notes, "rules", tokens=200),
        _result(
            notes,
            "typed",
            tokens=100,
            hook_ms=500,
            checked=("knowledge:good:r:section:0", "knowledge:noise:r:section:0"),
            dropped=("knowledge:noise:r:section:0",),
            applied=("knowledge:noise:r:section:0",),
            cached=2,
        ),
    ]
    report = score_replay(cases, passing, seeds, priming=PRIMED)
    assert report["gates"] == {
        "filler_dev": True,
        "skill_dev": True,
        "steps": True,
        "latency": True,
        "tokens": True,
    }
    assert report["complete"] is True
    filler = report["sets"]["dev"]["filler"]
    assert (filler["drops_judged"], filler["drops_applied"], filler["drops_not_applied"]) == (
        1,
        1,
        0,
    )
    assert filler["priming"] == {"asked": 2, "answered": 2, "share": 1.0, "blocked": False}

    slow = [*passing[:-1], replace_result(passing[-1], hook_ms=1_200, cap_hit=True)]
    failing = score_replay(cases, slow, seeds, priming=PRIMED)
    assert failing["gates"]["latency"] is False
    assert failing["latency"]["added_hook_ms"]["max"] == 1_100

    leaky = [
        *passing[:-1],
        replace_result(passing[-1], dropped_item_ids=("knowledge:good:r:section:0",)),
    ]
    assert score_replay(cases, leaky, seeds, priming=PRIMED)["gates"]["filler_dev"] is False

    costly = [
        replace_result(result, attached_tokens=300) if result.arm == "typed" else result
        for result in passing
    ]
    assert score_replay(cases, costly, seeds, priming=PRIMED)["gates"]["tokens"] is False

    unchecked = [
        *passing[:-1],
        replace_result(passing[-1], checked_item_ids=(), dropped_item_ids=(), notes_cached=0),
    ]
    assert score_replay(cases, unchecked, seeds, priming=PRIMED)["gates"]["filler_dev"] is False

    uncached = [*passing[:-1], replace_result(passing[-1], notes_cached=1)]
    filler = score_replay(cases, uncached, seeds, priming=PRIMED)["sets"]["dev"]["filler"]
    assert filler["answered"] == {"asked": 2, "answered": 1, "share": 0.5}
    assert filler["gates"]["answered_share"] is False

    unprimed = score_replay(cases, passing, seeds)
    assert unprimed["sets"]["dev"]["filler"]["priming"] == {
        "asked": 0,
        "answered": 0,
        "share": 0.0,
    }
    assert unprimed["gates"]["filler_dev"] is False  # never primed is never evidence
    weak = score_replay(cases, passing, seeds, priming={"dev": PrimingResult("dev", 20, 18, False)})
    assert weak["sets"]["dev"]["filler"]["gates"]["priming_answered_share"] is False

    skipped = [
        replace_result(result, skill_comparison="skipped") if result.arm == "typed" else result
        for result in passing
    ]
    skill_section = score_replay(cases, skipped, seeds, priming=PRIMED)["sets"]["dev"]["skill"]
    assert skill_section["answered"] == {"asked": 0, "answered": 0, "share": 0.0}
    assert skill_section["gates"]["answered_share"] is False  # never asked is never evidence
```

- in `test_filler_gate_needs_every_seeded_note_checked`, add `cached=2,` to the typed `_result(...)`, call `score_replay([notes], results, {"dev": seed}, priming=PRIMED)`, and add `"priming_answered_share": True,` to the expected gates dict after `"answered_share": True,`;
- in `test_children_get_exactly_the_environment_they_are_given`, change `replay_cli.main(args, environ=environ)` to `replay_cli.main(args, environ=environ, primer=_primer(oracle))`;
- in `test_cli_writes_a_report_with_an_in_process_runner`, add `primer=_primer(ReplayOracle()),` to the `replay_cli.main(...)` call and append `assert set(report["priming"]) == {"dev", "holdout"}` and `assert report["priming"]["holdout"]["share"] == 1.0`;
- in `test_a_failing_child_is_recorded_and_the_report_is_still_written`, add `primer=_primer(ReplayOracle()),` to the `replay_cli.main(...)` call;
- append at the end of the file:

```python
def test_priming_judges_every_seeded_note_and_never_the_pinned_one(tmp_path: Path) -> None:
    seed = seed_replay_set(tmp_path, "holdout")
    oracle = ReplayOracle()
    priming = prime_replay_seed(seed, environ=FAKE_ENVIRON, jev_transport=oracle)
    # 24 Markdown notes plus 3 of the 4 events: the pinned one is never sent; skills are not notes.
    assert (priming.notes, priming.answered, priming.blocked) == (27, 27, False)
    assert oracle.calls == 27
    assert LocalNoteVerdictCache(seed.data_directory).entry_count() == 27
    assert priming.to_dict() == {"notes": 27, "answered": 27, "share": 1.0, "blocked": False}


@pytest.mark.parametrize(
    ("alter", "message"),
    [
        (_no_marker, "no seed marker"),
        (_extra_document, "document that was not seeded"),
        (_extra_event, "event that was not seeded"),
    ],
)
def test_priming_refuses_a_directory_that_is_not_exactly_the_seed(
    tmp_path: Path, alter: Callable[[ReplaySeed], None], message: str
) -> None:
    seed = seed_replay_set(tmp_path, "holdout")
    alter(seed)
    transport = ScriptedJevTransport()
    with pytest.raises(ReplayRefusedError, match=message):
        prime_replay_seed(seed, environ=FAKE_ENVIRON, jev_transport=transport)
    assert transport.calls == 0


def test_priming_without_a_test_transport_needs_authorization(tmp_path: Path) -> None:
    seed = seed_replay_set(tmp_path, "holdout")
    with pytest.raises(ReplayRefusedError, match="needs authorization"):
        prime_replay_seed(seed, environ=FAKE_ENVIRON)


def test_a_jev_that_never_answers_fails_the_priming_gate(tmp_path: Path) -> None:
    seed = seed_replay_set(tmp_path, "holdout")
    failing = _HttpErrors()
    priming = prime_replay_seed(seed, environ=FAKE_ENVIRON, jev_transport=failing)
    assert failing.calls > 0 and (priming.notes, priming.answered) == (27, 0)
    probe = find_case("holdout", "notes", "notes-holdout-b00")
    results = run_replay({"holdout": seed}, [probe], _in_process(ReplayOracle()))
    report = score_replay([probe], results, {"holdout": seed}, priming={"holdout": priming})
    filler = report["sets"]["holdout"]["filler"]
    assert filler["priming"]["share"] == 0.0
    assert filler["gates"]["priming_answered_share"] is False


def test_the_prompt_path_sends_one_request_once_the_cache_is_warm(tmp_path: Path) -> None:
    seed = seed_replay_set(tmp_path, "holdout")
    oracle = ReplayOracle()
    prime_replay_seed(seed, environ=FAKE_ENVIRON, jev_transport=oracle)
    probe = find_case("holdout", "notes", "notes-holdout-b00")
    before = oracle.calls
    result = _in_process(oracle)(replay_request(seed, probe, "typed"))
    assert oracle.calls - before == 1
    assert result.checked_item_ids
    assert result.notes_cached == len(result.checked_item_ids)
    assert result.notes_unanswered == 0
    assert set(result.dropped_item_ids) == {
        item_id
        for item_id in result.checked_item_ids
        for prefix, category in seed.categories.items()
        if item_id.startswith(prefix) and category == "noise"
    }


def test_the_replay_never_starts_the_background_judge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = seed_replay_set(tmp_path, "holdout")
    PersonalSettingsStore(seed.data_directory).save(
        PersonalSettings(
            experimental_semantic_memory_enabled=True, experimental_typed_decisions_enabled=True
        )
    )
    started: list[Path] = []
    monkeypatch.setattr(cli, "_judge_route_open", lambda settings: True)
    monkeypatch.setattr(cli, "_start_note_judge", started.append)
    probe = find_case("holdout", "notes", "notes-holdout-b00")
    result = _in_process(ReplayOracle())(replay_request(seed, probe, "typed"))  # cold cache
    assert result.checked_item_ids and result.notes_cached == 0
    assert LocalNoteJudgeQueue(seed.data_directory).length() == len(result.checked_item_ids)
    assert started == []


def test_a_priming_failure_is_recorded_and_the_run_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    small = (find_case("holdout", "notes", "notes-holdout-b00"),)
    monkeypatch.setattr(replay_cli, "replay_cases", lambda: small)

    def broken(seed: ReplaySeed) -> PrimingResult:
        raise RuntimeError("synthetic priming failure")

    code = replay_cli.main(
        ["--run-id", "unprimed", "--results-root", str(tmp_path), "--live-calls-authorized"],
        environ=FAKE_ENVIRON,
        runner=_in_process(ReplayOracle()),
        primer=broken,
    )
    report = json.loads((tmp_path / "unprimed" / "report.json").read_text("utf-8"))
    assert code == 1
    assert report["priming"]["holdout"] == {
        "notes": 0,
        "answered": 0,
        "share": 0.0,
        "blocked": True,
    }
    assert report["sets"]["holdout"]["filler"]["gates"]["priming_answered_share"] is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/evals/test_typed_decision_replay.py -q`
Expected: FAIL at collection with `ImportError: cannot import name 'Primer' from 'scripts.typed_decision_replay'`.

- [ ] **Step 3: Implement the priming pass and the gates**

In `scripts/typed_decision_replay.py`, add this paragraph to the module docstring, after its second paragraph:

```text
Before any prompt, ``prime_replay_seed`` warms the seed's note-verdict cache by judging every
seeded note through the synthetic-source guard (spec 2026-10-03 §7), so the prompt path sends
only the front-door request and filler is scored from cached verdicts.
```

In `ReplayResult`, add after `error_type: str | None = None`:

```python
    notes_cached: int = 0
```

and mention it in the class docstring: "``notes_cached`` counts checked notes that had a usable cached verdict."

In `run_replay_case`, add as the last argument of the returned `ReplayResult(...)`:

```python
        notes_cached=0 if typed is None or step is None else typed.notes_cached,
```

Add after `approved_event_item_ids`:

```python
@dataclass(frozen=True, slots=True)
class PrimingResult:
    """One set's priming pass: seeded notes read, and how many got a cached verdict."""

    set_name: str
    notes: int
    answered: int
    blocked: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "notes": self.notes,
            "answered": self.answered,
            "share": share(self.answered, self.notes),
            "blocked": self.blocked,
        }

    @classmethod
    def failed(cls, set_name: str) -> PrimingResult:
        """A priming pass that could not run: nothing judged, so its gate fails."""

        return cls(set_name, 0, 0, True)


Primer = Callable[[ReplaySeed], PrimingResult]


def prime_replay_seed(
    seed: ReplaySeed,
    *,
    environ: Mapping[str, str],
    jev_transport: JevTransport | None = None,
    live_calls_authorized: bool = False,
) -> PrimingResult:
    """Warm the seed's verdict cache by judging every seeded note once (spec 2026-10-03 §7).

    The notes are the notes project's Markdown sections and the main project's approved events;
    the pinned event is never sent and skills are not notes. Both projects are first verified to
    hold exactly the seeded content. Text is read with the background judge's scoped reader, and
    Jev is reached only through the synthetic-source guard with the judge's 5 s deadline, four
    requests in flight. Without a test ``jev_transport`` this calls Jev, so it needs
    ``live_calls_authorized=True``.
    """

    if jev_transport is None and not live_calls_authorized:
        raise ReplayRefusedError("a replay priming pass without a test transport needs authorization")
    from mnemo_memory.apps.cli.typed_decision_composition import (
        build_synthetic_typed_decision_classifier,
    )
    from mnemo_memory.apps.cli.typed_note_judge import JUDGE_DEADLINE_SECONDS, judge_candidates
    from mnemo_memory.packages.storage import LocalNoteVerdictCache

    data = seed.data_directory
    notes = _verified_binding(_priming_request(seed, seed.notes_project_directory, "notes"))
    events = _verified_binding(_priming_request(seed, seed.project_directory, "memory"))
    candidates = (
        *cli._note_candidates_by_id(
            data, notes.checkpoint_scope, knowledge_note_item_ids(data, notes)
        ),
        *cli._note_candidates_by_id(
            data, events.checkpoint_scope, approved_event_item_ids(data, events)
        ),
    )
    settings = PersonalSettingsStore(data).load()
    guard = build_synthetic_typed_decision_classifier(
        settings,
        data_directory=data,
        environ=environ,
        jev_transport=jev_transport,
        deadline_seconds=JUDGE_DEADLINE_SECONDS,
    )
    tally = judge_candidates(
        guard,
        candidates,
        cache=LocalNoteVerdictCache(data),
        model_version=settings.typed_decision_model_id,
    )
    return PrimingResult(seed.set_name, len(candidates), tally.answered, tally.blocked)


def _priming_request(seed: ReplaySeed, project: Path, group: str) -> ReplayRequest:
    """A request that only names a seeded project, so ``_verified_binding`` can check it."""

    return ReplayRequest(
        seed.set_name, group, "priming", "typed", str(seed.data_directory), str(project)
    )
```

Change the `score_replay` signature to:

```python
def score_replay(
    cases: Sequence[ReplayCase],
    results: Sequence[ReplayResult],
    seeds: Mapping[str, ReplaySeed],
    priming: Mapping[str, PrimingResult] | None = None,
) -> dict[str, Any]:
```

add to its docstring: "Each filler section also scores that set's priming pass (``priming``); a set never primed fails its ``priming_answered_share`` sub-gate.", and change the filler call to:

```python
            section["filler"] = _score_filler(
                [item for item in typed_results if not _step_error(item)],
                seeds[set_name],
                (priming or {}).get(set_name),
            )
```

Replace `_score_filler` with:

```python
def _score_filler(
    results: Sequence[ReplayResult], seed: ReplaySeed, priming: PrimingResult | None
) -> dict[str, Any]:
    """Jev's would-drop decisions by seeded category, per check, plus note coverage.

    Every seeded Markdown note must be checked at least once: a drop rate over the few notes
    a probe happened to render says nothing about the notes no probe ever showed. Approved
    events are reported, not gated; the pinned one is never sent. Verdicts come from the cache
    the priming pass warmed (spec 2026-10-03 §7): ``answered`` is the share of checked notes
    that had a cached verdict, and ``priming`` the share of seeded notes priming judged.
    """

    rows: list[tuple[str, bool]] = []
    uncategorized = 0
    checked_keys: set[str] = set()
    for result in results:
        dropped = set(result.dropped_item_ids)
        for item_id in result.checked_item_ids:
            key = _seed_key(seed, item_id)
            if key is None:
                uncategorized += 1
                continue
            checked_keys.add(key)
            rows.append((seed.categories[key], item_id in dropped))
    notes = {key for key in seed.categories if key.startswith(_KNOWLEDGE_ITEM_PREFIX)}
    events = {key for key in seed.categories if key.startswith(_EVENT_ITEM_PREFIX)}
    notes_checked = len(notes & checked_keys)
    relevant = [dropped for category, dropped in rows if category == "relevant"]
    noise = [dropped for category, dropped in rows if category == "noise"]
    noise_rate = share(sum(noise), len(noise))
    judged = sum(len(result.dropped_item_ids) for result in results)
    applied = sum(len(result.applied_drop_item_ids) for result in results)
    sent = sum(len(result.checked_item_ids) for result in results)
    answered = _answered_share(sent, sum(result.notes_cached for result in results))
    primed = (
        _answered_share(0, 0)
        if priming is None
        else _answered_share(priming.notes, priming.answered) | {"blocked": priming.blocked}
    )
    return {
        "checks": len(rows),
        "drops_judged": judged,
        "drops_applied": applied,
        "drops_not_applied": judged - applied,
        "uncategorized_checks": uncategorized,
        "notes_seeded": len(notes),
        "notes_checked": notes_checked,
        "events_seeded": len(events),
        "events_checked": len(events & checked_keys),
        "relevant_checks": len(relevant),
        "relevant_dropped": sum(relevant),
        "noise_checks": len(noise),
        "noise_drop_rate": noise_rate,
        "superseded_dropped": sum(
            dropped for category, dropped in rows if category == "superseded"
        ),
        "answered": answered,
        "priming": primed,
        "gates": {
            "relevant_dropped": bool(relevant) and sum(relevant) == 0,
            "filler_removed": bool(noise) and noise_rate >= 0.9,
            "all_notes_checked": bool(notes) and notes_checked == len(notes),
            "answered_share": _answered_gate(answered),
            "priming_answered_share": _answered_gate(primed),
        },
    }
```

In `scripts/run_typed_decision_replay.py`, add to the module docstring after its first paragraph: "Before the cases, each seed's note-verdict cache is warmed by a priming pass that judges every seeded note (spec 2026-10-03 §7); a priming failure is recorded and the run goes on." Add `PrimingResult`, `Primer`, `ReplaySeed` and `prime_replay_seed` to the `scripts.typed_decision_replay` import, and replace `main` from its signature through the `report["run"] = ...` line with:

```python
def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    runner: Runner | None = None,
    primer: Primer | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> int:
    """Run the replay, or with ``--child`` one case.

    A full run seeds both sets, warms each seed's verdict cache with ``primer`` (default: the
    live priming pass), then runs every case. Tests pass both ``runner`` and ``primer``.
    """

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id")
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    parser.add_argument("--live-calls-authorized", action="store_true")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    variables = os.environ if environ is None else environ
    if args.child:
        return _child(
            args.live_calls_authorized,
            variables,
            stdin or sys.stdin,
            stdout or sys.stdout,
        )
    if args.run_id is None or not _RUN_ID.fullmatch(args.run_id):
        return _refuse("--run-id must be 1-64 letters, digits, '.', '_' or '-'")
    if not args.live_calls_authorized:
        return _refuse("live Jev calls need --live-calls-authorized (maintainer go-ahead)")
    if not variables.get("TYPESAFE_API_KEY", "").strip():
        return _refuse("TYPESAFE_API_KEY is not set")
    output = args.results_root / args.run_id / "report.json"
    if output.exists():
        return _refuse(f"{output} already exists")

    def fresh(request: ReplayRequest) -> ReplayResult:
        return run_case_in_fresh_process(request, live_calls_authorized=True, environ=variables)

    def prime(seed: ReplaySeed) -> PrimingResult:
        return prime_replay_seed(seed, environ=variables, live_calls_authorized=True)

    run: Runner = runner or fresh
    warm: Primer = primer or prime
    cases = replay_cases()
    with TemporaryDirectory(prefix="mnemo-typed-replay-") as root:
        seeds = {name: seed_replay_set(Path(root), name) for name in SETS}
        priming = {name: _primed(warm, seed) for name, seed in seeds.items()}
        results = run_replay(seeds, cases, run)
        report = score_replay(cases, results, seeds, priming=priming)
    report["priming"] = {name: result.to_dict() for name, result in priming.items()}
    report["run"] = {"run_id": args.run_id, "prompts": len(cases), "requests": len(results)}
```

(the lines after `report["run"] = ...` — writing the report, printing the status and returning — stay as they are), and add before `_refuse`:

```python
def _primed(primer: Primer, seed: ReplaySeed) -> PrimingResult:
    """One set's priming pass; a failure is recorded as a blocked, empty pass, never fatal."""

    try:
        return primer(seed)
    except Exception:
        return PrimingResult.failed(seed.set_name)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/evals/test_typed_decision_replay.py tests/evals/test_run_typed_decision_evaluation.py -q`
Expected: PASS, including the unchanged `test_a_child_imports_the_typed_step_cold_inside_the_timed_hook_call` (priming imports the typed modules only inside `prime_replay_seed`).

Run: `uv run ruff format scripts tests/evals && uv run ruff check scripts tests/evals && uv run mypy scripts tests/evals/test_typed_decision_replay.py`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(replay): warm the note-verdict cache before the prompts and gate on it" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- scripts/typed_decision_replay.py scripts/run_typed_decision_replay.py tests/evals/test_typed_decision_replay.py
```

---

### Task 9: Contract, security and docs

**Files:**
- Modify: `tests/contract/test_typed_hook_contract.py` (append two tests)
- Modify: `tests/security/test_typed_hook_network_boundary.py` (append two tests)
- Modify: `tests/architecture/test_typed_decision_boundaries.py:5-11` (`POLICY_MODULES`)
- Modify: `docs/adr/0049-hosted-typed-classifier-and-cheap-model-tier.md` (Consequences, after the "Before promotion to live, four known gaps must be closed" list, before `## Security and privacy implications`)
- Modify: `docs/user-guide.md` (section "Optional: Jev typed decisions", after the paragraph ending "delete the file to reset it.")

**Interfaces:**
- Consumes: everything above; no new production code.
- Produces: pinning tests and paperwork only.

- [ ] **Step 1: Write the tests**

In `tests/contract/test_typed_hook_contract.py`, add `from collections.abc import Sequence`, `from mnemo_memory.packages.storage import LocalNoteJudgeQueue, LocalNoteVerdictCache, NoteVerdictState`, and (if not already added in Task 5) `prime_note_verdicts` to the test-support import. Append:

```python
def test_shadow_with_a_warm_cache_is_byte_identical_to_off(tmp_path: Path) -> None:
    """Shadow records the cached drops and changes no output (spec 2026-10-03 §3)."""

    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    transport = ScriptedJevTransport(ANSWER_EVERYTHING)
    prime_note_verdicts(fixture, transport)
    shadow = synthetic_overrides(fixture, transport, TypedHookModes(SHADOW, SHADOW, SHADOW, SHADOW))
    for prompt in (*PROMPTS[::4], KNOWLEDGE_PROMPT):
        off = run_hook(fixture, prompt)
        seen = run_hook(fixture, prompt, shadow)
        assert (seen.context, seen.delivery_keys) == (off.context, off.delivery_keys), prompt
    typed = (
        LocalAutomaticRouteTelemetryStore(fixture.data)
        .events(cli._automatic_route_scope(fixture.binding.checkpoint_scope), limit=1)[0]
        .typed
    )
    assert typed is not None and (typed.notes_cached, typed.notes_dropped) == (3, 2)


def test_off_reads_and_writes_no_verdict_cache_or_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    reads: list[int] = []

    def counted(self: LocalNoteVerdictCache, keys: Sequence[str]) -> tuple[NoteVerdictState, ...]:
        reads.append(len(keys))
        return ()

    monkeypatch.setattr(LocalNoteVerdictCache, "states", counted)
    for prompt in PROMPTS[::5]:
        run_hook(fixture, prompt)
    PersonalSettingsStore(fixture.data).save(
        PersonalSettings(
            experimental_semantic_memory_enabled=True, experimental_typed_decisions_enabled=True
        )
    )
    for prompt in PROMPTS[::5]:
        run_hook(fixture, prompt)
    assert reads == []
    assert not LocalNoteVerdictCache(fixture.data).path.exists()
    assert not LocalNoteJudgeQueue(fixture.data).path.exists()
```

In `tests/security/test_typed_hook_network_boundary.py`, add `import os`, `import subprocess`, `import sys`; `from mnemo_memory.packages.storage import LocalNoteJudgeQueue, LocalNoteVerdictCache`; `from scripts.typed_decision_replay import approved_event_item_ids, knowledge_note_item_ids`; and `FILLER_MARKER`, `KNOWLEDGE_PROMPT`, `ScriptedJevTransport`, `prime_note_verdicts`, `synthetic_overrides` to the test-support import. Append:

```python
def test_the_judge_process_stays_silent_and_sends_nothing_under_synthetic_only(
    tmp_path: Path,
) -> None:
    """The real ``python -m`` command the hook would start, in a child with a fake key only."""

    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    PersonalSettingsStore(fixture.data).save(
        PersonalSettings(
            experimental_semantic_memory_enabled=True, experimental_typed_decisions_enabled=True
        )
    )
    item_ids = (
        *knowledge_note_item_ids(fixture.data, fixture.binding),
        *approved_event_item_ids(fixture.data, fixture.binding),
    )
    LocalNoteJudgeQueue(fixture.data).append(fixture.binding.checkpoint_scope, item_ids)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "mnemo_memory.apps.cli.main",
            "typed-decisions",
            "judge-notes",
            "--data-dir",
            str(fixture.data),
        ],
        env={
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(tmp_path),
            "TYPESAFE_API_KEY": FAKE_TYPESAFE_KEY,
        },
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert (completed.returncode, completed.stdout) == (0, "")
    assert "Traceback" not in completed.stderr
    assert FAKE_TYPESAFE_KEY not in completed.stdout + completed.stderr
    # Blocked before the budget and the network: nothing reserved, nothing judged.
    assert not (fixture.data / "typed_decision-budget.json").exists()
    assert LocalNoteVerdictCache(fixture.data).entry_count() == 0


def test_the_verdict_cache_and_judge_queue_never_hold_note_or_prompt_text(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    shadow = synthetic_overrides(
        fixture, ScriptedJevTransport(), TypedHookModes(relevance=SHADOW)
    )
    run_hook(fixture, KNOWLEDGE_PROMPT + " private-marker-91aa", shadow)  # queues the notes
    prime_note_verdicts(fixture, ScriptedJevTransport())
    cache = LocalNoteVerdictCache(fixture.data).path.read_text("utf-8")
    queue = LocalNoteJudgeQueue(fixture.data).path.read_text("utf-8")
    for marker in (
        "private-marker-91aa",
        "invoice",
        "Invoice",
        "ledger",
        FILLER_MARKER,
        "lunch",
        "idempotent",
        "release-notes",
        "test-plan",
    ):
        assert marker not in cache and marker not in queue
    assert "knowledge:" not in cache and "approved-episodic:" not in cache
```

(`SHADOW` is already defined in that file as `TypedDecisionMode.SHADOW`.)

In `tests/architecture/test_typed_decision_boundaries.py`, add to `POLICY_MODULES`:

```python
    pathlib.Path("src/mnemo_memory/apps/cli/typed_note_judge.py"),
    pathlib.Path("src/mnemo_memory/packages/storage/local_note_verdicts.py"),
    pathlib.Path("src/mnemo_memory/packages/storage/local_note_judge_queue.py"),
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/contract tests/security tests/architecture -q`
Expected: PASS. (These pin behaviour the earlier tasks built; if one fails, fix the owning task's code, not the test.)

- [ ] **Step 3: Write the docs**

In `docs/adr/0049-hosted-typed-classifier-and-cheap-model-tier.md`, add after the four-gaps list and before `## Security and privacy implications`:

```markdown
- *Dated note (2026-10-03): note-verdict cache* (spec
  `docs/superpowers/specs/2026-10-03-jev-note-verdict-cache-design.md`). The first two live
  synthetic replays, `2026-10-03-replay-a` and `2026-10-03-replay-b`, passed only the `steps` and
  `tokens` gates. Jev's answers were good when they arrived, but too many arrived after the 0.8 s
  cap: the cap-hit share was 0.38 and then 0.44 (typed step p50 656 ms and 752 ms). Each prompt
  waited on the slowest of up to 17 fresh HTTPS requests (the front door plus one filler check
  per note), each with its own TCP, TLS and server tail; the hedged TCP connect (f3cd236)
  removed one tail and did not help. A filler verdict depends only on the note, so it is now
  cached. The prompt hook sends exactly one Jev request, the front door, and reads each
  candidate's verdict from `typed-decision-note-verdicts.json`: keyed by one hash of the item
  ID, the judged text, the pinned model version and `FILLER_QUESTION_VERSION`; content-free;
  fresh for 30 days; at most 5,000 entries and 1 MB. A missing, stale or unreadable verdict keeps
  the note. Notes without a verdict are queued by ID (`typed-decision-judge-queue.json`, at most
  256) and judged after the prompt by a detached `typed-decisions judge-notes` process: one at a
  time, a `RUNTIME` guard with a 5 s deadline, 4 requests in flight, 32 notes per run, and no
  retry after 3 failed attempts on the same text. The hook starts the judge only when the data
  route would let runtime text leave the machine, so under `synthetic_only` it is never started
  (and would be `data_route_blocked`); no real note leaves the machine. Telemetry adds
  `typed_notes_cached` and `typed_notes_queued`, and `status` shows the cache, the queue and
  whether a judge runs. The replay first warms each seed's cache through the synthetic guard
  and gates on a priming answered-share of at least 0.95. The prompt path's budget work (gap 4)
  now runs once per prompt. The 0.8 s cap is unchanged until the next live replay.
```

In `docs/user-guide.md`, add after the paragraph that ends "delete the file to reset it.":

```markdown
The filler check does not ask Jev during the prompt. Mnemo keeps a small local cache of note
verdicts (`typed-decision-note-verdicts.json` in the data directory): a note whose verdict is
less than 30 days old is dropped or kept from the cache, and a note without one is always kept.
Notes that still need a verdict are queued by ID only (`typed-decision-judge-queue.json`, up to
256 notes) for a background judge, `mnemo-memory typed-decisions judge-notes`. The hook starts it
after it has answered and never waits for it; the judge prints nothing, runs one at a time, and
gives up on a note after three failed attempts until the note changes. Neither file holds note or
prompt text, and deleting either is safe. While the data route is `synthetic_only`, the judge is
never started, so the queue only fills and no note leaves your machine. `status` also shows
`note_verdicts` (whether the cache is readable, and how many entries it holds) and `note_judge`
(how many notes wait, and whether a judge is running).
```

- [ ] **Step 4: Check formatting and types**

Run: `uv run ruff format tests && uv run ruff check tests && uv run mypy tests/contract tests/security tests/architecture`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git commit -m "test,docs: note-verdict cache contracts, security pins, ADR 0049 note, user guide" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- tests/contract/test_typed_hook_contract.py tests/security/test_typed_hook_network_boundary.py tests/architecture/test_typed_decision_boundaries.py docs/adr/0049-hosted-typed-classifier-and-cheap-model-tier.md docs/user-guide.md
```

---

### Task 10: Final step: `npm run check`

**Files:** none new; fix only what the check reports, in the task that owns the code.

**Interfaces:**
- Consumes: all tasks.
- Produces: a green branch.

- [ ] **Step 1: Run the full check**

Run: `npm run check`
Expected: format, lint, mypy, the whole pytest suite, the postgres, schema, dependency, architecture and package checks all pass.

- [ ] **Step 2: If anything fails, fix it and re-run**

Fix the failing code in place (never by weakening a test above), re-run `npm run check` until it passes, and commit the fix with `git commit -m "fix: <what> after npm run check" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- <paths>`.

- [ ] **Step 3: Confirm no secret or live call slipped in**

Run: `git diff main --stat` and `git grep -n "TYPESAFE_API_KEY" -- src scripts tests`
Expected: the key name appears only where it did on `main` plus the new tests that set it to `FAKE_TYPESAFE_KEY`; no value is ever read into output.

---

## Spec coverage

| Spec requirement | Task |
|---|---|
| §3 one request per prompt; cap unchanged | 5 (`ask_typed_questions`, `test_the_prompt_path_sends_exactly_one_request`) |
| §3 candidates unchanged; cached `p_filler >= 0.7` drops (live) / would-drop (shadow); missing, stale, unreadable keeps | 5 |
| §3 downstream unchanged (pinned re-render, `lower_rank`, fit rule, changed route unfiltered) | 5 (existing integration tests, now primed) |
| §3 queue IDs only, only when relevance is not off | 2, 5 |
| §3 start the judge after the output, fire-and-forget, failures ignored | 6 |
| §3 every cache read, queue write and start inside the step guard | 5, 6 |
| §4 hidden command, queue file, scope, no text on the command line | 2, 6 |
| §4 re-read through the scoped `get_context item_ids` lookup with the same exemptions | 4 (`_note_candidates_by_id`, reader tests) |
| §4 `RUNTIME` guard from the composition module; `synthetic_only` blocks | 4, 6, 9 |
| §4 5 s deadline, 4 in flight, 32 per run, single instance, exit when done | 4, 6 |
| §4 3-attempt backoff per content digest | 1, 4 |
| §4 silence: prints nothing, exits 0 | 6, 9 |
| §5 file, lock, atomic write, 1 MB; key parts; value; content-free; 30 days; 5,000; corrupt is empty | 1 |
| §6 `typed_notes_cached`, `typed_notes_queued`; old records load; `notes_checked` and `notes_unanswered` semantics | 3, 5 |
| §6 `status` cache count, queue length, judge running | 7 |
| §7 priming pass, synthetic guard, verified seed, priming gate | 8 |
| §7 prompt path one request; filler scored from cache; latency gates measure the front door | 8 |
| §7 the replay never starts the judge | 6, 8 |
| §8 hook, judge, cache, contract and replay tests | 1–9 |
| §10 ADR 0049 and user guide (the phase-2 memory note waits for the next live replay) | 9 |
