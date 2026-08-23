"""Content-free counters for local-first takeover route decisions."""

from __future__ import annotations

import fcntl
import json
import os
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from tempfile import NamedTemporaryFile

_ROUTES = frozenset({"local", "frontier"})

# The episodic route recorder emits exactly these outcome statuses (mcp_durable
# _record_episodic_route). "no_events" returns without the recorder, so it never appears.
_STATUSES = frozenset({"extracted", "handoff", "local_failed", "extraction_disabled", "error"})
# Only these statuses represent a routing decision that spends or saves tokens.
_LOCAL_STATUSES = frozenset({"extracted"})
_FRONTIER_STATUSES = frozenset({"handoff", "local_failed"})

_FORMAT_VERSION = 1
_MAXIMUM_FILE_BYTES = 65_536


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
    """Persist cumulative, content-free per-status route counts under the data dir."""

    def __init__(self, data_directory: Path) -> None:
        self._directory = data_directory.expanduser().resolve()
        self.path = self._directory / "takeover-route-telemetry.json"

    def record(self, status: str) -> None:
        if status not in _STATUSES:
            raise ValueError("takeover route status is invalid")
        with self._lock():
            counts = self._read()
            counts[status] = counts.get(status, 0) + 1
            self._write(counts)

    def counts(self) -> dict[str, int]:
        counts = self._read()
        return {status: counts.get(status, 0) for status in sorted(_STATUSES)}

    def savings(self) -> dict[str, object]:
        counts = self._read()
        local = sum(counts.get(status, 0) for status in _LOCAL_STATUSES)
        frontier = sum(counts.get(status, 0) for status in _FRONTIER_STATUSES)
        total = local + frontier
        ratio = local / total if total else None
        return {"local": local, "frontier": frontier, "ratio": ratio}

    def _read(self) -> dict[str, int]:
        if not self.path.exists():
            return {}
        if self.path.is_symlink() or not self.path.is_file():
            raise TakeoverRouteTelemetryError("takeover route telemetry is unavailable")
        try:
            if self.path.stat().st_size > _MAXIMUM_FILE_BYTES:
                raise ValueError
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if (
                not isinstance(value, dict)
                or set(value) != {"version", "counts"}
                or value["version"] != _FORMAT_VERSION
                or not isinstance(value["counts"], dict)
            ):
                raise ValueError
            counts: dict[str, int] = {}
            for status, count in value["counts"].items():
                if status not in _STATUSES or isinstance(count, bool) or not isinstance(count, int):
                    raise ValueError
                counts[status] = count
            return counts
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise TakeoverRouteTelemetryError("takeover route telemetry is unavailable") from error

    def _write(self, counts: dict[str, int]) -> None:
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
                json.dump(
                    {"version": _FORMAT_VERSION, "counts": counts},
                    handle,
                    sort_keys=True,
                    separators=(",", ":"),
                )
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
