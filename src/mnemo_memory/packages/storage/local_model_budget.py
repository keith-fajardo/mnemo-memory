"""File-locked daily reservations for one optional model task in the personal profile.

The counter is one small JSON file under an exclusive ``flock``. It resets at each UTC day. Any
doubt — a corrupt, unreadable or unsafe file, a wrong task type, a naive clock, a lock failure —
denies the reservation, so the caller falls back to its rules. A corrupt file is never
overwritten; ``typed-decisions status`` reports it so the owner can remove it.
"""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from mnemo_memory.packages.domain import (
    ModelBudgetDenied,
    ModelBudgetReservation,
    ModelTaskType,
    WorkspaceId,
)

_FORMAT_VERSION = 1
_MAXIMUM_FILE_BYTES = 4_096
_MAXIMUM_DAILY_INPUT_TOKENS = 1_000_000_000
_KEYS = frozenset({"version", "task_type", "day", "input_tokens"})


class _CounterUnavailable(RuntimeError):
    """The counter cannot be trusted; every reservation is denied."""


class LocalDailyModelBudget:
    """Reserve worst-case input tokens for one task type against a per-UTC-day limit."""

    def __init__(
        self,
        data_directory: Path,
        *,
        task_type: ModelTaskType,
        daily_input_tokens: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(task_type, ModelTaskType):
            raise TypeError("model budget task type is invalid")
        if (
            isinstance(daily_input_tokens, bool)
            or not isinstance(daily_input_tokens, int)
            or not 1 <= daily_input_tokens <= _MAXIMUM_DAILY_INPUT_TOKENS
        ):
            raise ValueError("daily input token limit is invalid")
        self._directory = data_directory.expanduser().resolve()
        self.path = self._directory / f"{task_type.value}-budget.json"
        self._lock_path = self._directory / f".{task_type.value}-budget.lock"
        self._task_type = task_type
        self._limit = daily_input_tokens
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))

    @property
    def daily_input_tokens(self) -> int:
        return self._limit

    def reserve(
        self,
        workspace_id: WorkspaceId,
        task_type: ModelTaskType,
        reservation: ModelBudgetReservation,
    ) -> None:
        if (
            not isinstance(workspace_id, WorkspaceId)
            or task_type is not self._task_type
            or not isinstance(reservation, ModelBudgetReservation)
        ):
            raise ModelBudgetDenied("MNEMO_LOCAL_MODEL_BUDGET_DENIED")
        try:
            day = self._day()
            with self._lock():
                total = self._read(day) + reservation.input_tokens
                if total > self._limit:
                    raise ModelBudgetDenied("MNEMO_LOCAL_MODEL_BUDGET_EXHAUSTED")
                self._write(day, total)
        except ModelBudgetDenied:
            raise
        except Exception as error:
            raise ModelBudgetDenied("MNEMO_LOCAL_MODEL_BUDGET_UNAVAILABLE") from error

    def reserved_today(self) -> int | None:
        """Return today's reserved input tokens, or ``None`` when the counter is unreadable."""

        try:
            return self._read(self._day())
        except Exception:
            return None

    def _day(self) -> str:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise _CounterUnavailable("budget clock must be timezone-aware")
        return now.astimezone(UTC).date().isoformat()

    def _read(self, day: str) -> int:
        if self.path.is_symlink():
            raise _CounterUnavailable("budget counter is unsafe")
        if not self.path.exists():
            return 0
        if not self.path.is_file() or self.path.stat().st_size > _MAXIMUM_FILE_BYTES:
            raise _CounterUnavailable("budget counter is unsafe")
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or set(value) != _KEYS:
            raise _CounterUnavailable("budget counter is invalid")
        version, tokens = value["version"], value["input_tokens"]
        if (
            isinstance(version, bool)
            or version != _FORMAT_VERSION
            or value["task_type"] != self._task_type.value
            or not isinstance(value["day"], str)
            or isinstance(tokens, bool)
            or not isinstance(tokens, int)
            or tokens < 0
        ):
            raise _CounterUnavailable("budget counter is invalid")
        used: int = tokens
        return used if value["day"] == day else 0

    def _write(self, day: str, input_tokens: int) -> None:
        payload = json.dumps(
            {
                "version": _FORMAT_VERSION,
                "task_type": self._task_type.value,
                "day": day,
                "input_tokens": input_tokens,
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
