"""Content-free counters for local-first takeover route decisions."""

from __future__ import annotations

import fcntl
import json
import os
from collections import Counter
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TypedDict

_ROUTES = frozenset({"local", "frontier"})

# The episodic route recorder emits exactly these outcome statuses (mcp_durable
# _record_episodic_route). "no_events" returns without the recorder, so it never appears.
_STATUSES = frozenset({"extracted", "handoff", "local_failed", "extraction_disabled", "error"})
# Only these statuses represent a routing decision that spends or saves tokens.
_LOCAL_STATUSES = frozenset({"extracted"})
_FRONTIER_STATUSES = frozenset({"handoff", "local_failed"})

_FORMAT_VERSION = 2
_MAXIMUM_FILE_BYTES = 1_048_576
_DEFAULT_RECENT_DAYS = 7


class _State(TypedDict):
    days: dict[str, dict[str, int]]
    untimed: dict[str, int]


def _valid_day(day: str) -> bool:
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _validated_counts(raw: object) -> dict[str, int]:
    if not isinstance(raw, dict):
        raise ValueError("takeover route counts are invalid")
    counts: dict[str, int] = {}
    for status, count in raw.items():
        if status not in _STATUSES or isinstance(count, bool) or not isinstance(count, int):
            raise ValueError("takeover route counts are invalid")
        if count < 0:
            raise ValueError("takeover route counts are invalid")
        counts[status] = count
    return counts


class TakeoverRouteTelemetry:
    """Record only route name, escalation flag, and duration — never content."""

    def __init__(self) -> None:
        self._counts: Counter[str] = Counter()

    def record(self, route: str, *, escalated: bool, duration_ms: int) -> None:
        if route not in _ROUTES:
            raise ValueError("takeover route is invalid")
        if not isinstance(escalated, bool):
            raise TypeError("escalated must be a boolean")
        if isinstance(duration_ms, bool) or not isinstance(duration_ms, int) or duration_ms < 0:
            raise ValueError("duration_ms must be a non-negative integer")
        self._counts[route] += 1

    def counts(self) -> dict[str, int]:
        return {route: self._counts.get(route, 0) for route in sorted(_ROUTES)}


class TakeoverRouteTelemetryError(RuntimeError):
    """Safe local telemetry failure without payload or path details."""


class LocalTakeoverRouteTelemetryStore:
    """Persist content-free per-day route counts under the data dir.

    The file keeps one bucket per UTC calendar day plus an ``untimed`` bucket that holds counts
    migrated from the older cumulative (dateless) format, so historical totals survive while new
    activity becomes day/week/month reportable.
    """

    def __init__(self, data_directory: Path) -> None:
        self._directory = data_directory.expanduser().resolve()
        self.path = self._directory / "takeover-route-telemetry.json"

    def record(self, status: str, *, now: datetime | None = None) -> None:
        if status not in _STATUSES:
            raise ValueError("takeover route status is invalid")
        day = (now or datetime.now(UTC)).astimezone(UTC).date().isoformat()
        with self._lock():
            state = self._read()
            bucket = state["days"].setdefault(day, {})
            bucket[status] = bucket.get(status, 0) + 1
            self._write(state)

    def counts(self) -> dict[str, int]:
        totals = self._all_time_counts(self._read())
        return {status: totals.get(status, 0) for status in sorted(_STATUSES)}

    def savings(self) -> dict[str, object]:
        summary = self._summary(self._all_time_counts(self._read()))
        total = summary["local"] + summary["frontier"]
        ratio = summary["local"] / total if total else None
        return {**summary, "ratio": ratio}

    def stats(
        self, *, now: datetime | None = None, recent_days: int = _DEFAULT_RECENT_DAYS
    ) -> dict[str, object]:
        reference = (now or datetime.now(UTC)).astimezone(UTC).date()
        state = self._read()
        days = state["days"]

        def window(span: int) -> dict[str, int]:
            acc: dict[str, int] = {}
            for offset in range(span):
                for status, count in days.get(
                    (reference - timedelta(days=offset)).isoformat(), {}
                ).items():
                    acc[status] = acc.get(status, 0) + count
            return self._summary(acc)

        recent: list[tuple[str, int, int]] = []
        for offset in range(recent_days):
            day = (reference - timedelta(days=offset)).isoformat()
            bucket = days.get(day)
            if bucket:
                summary = self._summary(bucket)
                recent.append((day, summary["local"], summary["frontier"]))
        return {
            "today": window(1),
            "last_7_days": window(7),
            "last_30_days": window(30),
            "all_time": self._summary(self._all_time_counts(state)),
            "recent": recent,
        }

    @staticmethod
    def _summary(counts: Mapping[str, int]) -> dict[str, int]:
        local = sum(counts.get(status, 0) for status in _LOCAL_STATUSES)
        frontier = sum(counts.get(status, 0) for status in _FRONTIER_STATUSES)
        return {"local": local, "frontier": frontier}

    @staticmethod
    def _all_time_counts(state: _State) -> dict[str, int]:
        totals: dict[str, int] = dict(state["untimed"])
        for bucket in state["days"].values():
            for status, count in bucket.items():
                totals[status] = totals.get(status, 0) + count
        return totals

    def _read(self) -> _State:
        empty: _State = {"days": {}, "untimed": {}}
        if not self.path.exists():
            return empty
        if self.path.is_symlink() or not self.path.is_file():
            raise TakeoverRouteTelemetryError("takeover route telemetry is unavailable")
        try:
            if self.path.stat().st_size > _MAXIMUM_FILE_BYTES:
                raise ValueError
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError
            version = value.get("version")
            if version == 1:
                # Legacy cumulative counts have no dates: preserve them as untimed all-time totals.
                return {"days": {}, "untimed": _validated_counts(value.get("counts"))}
            if version == 2:
                if not set(value) <= {"version", "days", "untimed"} or "days" not in value:
                    raise ValueError
                raw_days = value["days"]
                if not isinstance(raw_days, dict):
                    raise ValueError
                days: dict[str, dict[str, int]] = {}
                for day, bucket in raw_days.items():
                    if not isinstance(day, str) or not _valid_day(day):
                        raise ValueError
                    days[day] = _validated_counts(bucket)
                return {"days": days, "untimed": _validated_counts(value.get("untimed", {}))}
            raise ValueError
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise TakeoverRouteTelemetryError("takeover route telemetry is unavailable") from error

    def _write(self, state: _State) -> None:
        payload: dict[str, object] = {"version": _FORMAT_VERSION, "days": state["days"]}
        if state["untimed"]:
            payload["untimed"] = state["untimed"]
        temporary: Path | None = None
        try:
            self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self._directory, 0o700)
            if self.path.is_symlink():
                raise OSError
            with NamedTemporaryFile(
                "w", encoding="utf-8", dir=self._directory, delete=False
            ) as handle:
                temporary = Path(handle.name)
                json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        except OSError as error:
            raise TakeoverRouteTelemetryError("takeover route telemetry is unavailable") from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @contextmanager
    def _lock(self) -> Iterator[None]:
        descriptor: int | None = None
        try:
            self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self._directory, 0o700)
            flags = os.O_CREAT | os.O_RDWR
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(self._directory / ".takeover-route-telemetry.lock", flags, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        except OSError as error:
            raise TakeoverRouteTelemetryError(
                "takeover route telemetry lock is unavailable"
            ) from error
        finally:
            if descriptor is not None:
                with suppress(OSError):
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
