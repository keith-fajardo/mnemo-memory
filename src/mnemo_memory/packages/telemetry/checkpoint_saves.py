"""Private content-free diagnostics for durable checkpoint save attempts."""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID

from mnemo_memory.packages.telemetry.automatic_routes import AutomaticRouteScope

_FORMAT_VERSION = 1
_MAXIMUM_FILE_BYTES = 262_144
_OPERATIONS = frozenset(
    {"create", "revise", "complete", "abandon", "record_lesson", "record_event", "invalid"}
)
_ERROR_CODES = frozenset(
    {
        "MNEMO_CHECKPOINT_NOT_FOUND",
        "MNEMO_DUPLICATE_CHECKPOINT",
        "MNEMO_EPISODIC_EVENT_CONFLICT",
        "MNEMO_REVISION_CONFLICT",
        "MNEMO_INVALID_LIFECYCLE",
        "MNEMO_INVALID_SCOPE",
        "MNEMO_EVIDENCE_REQUIRED",
        "MNEMO_TOKEN_BUDGET",
        "MNEMO_QUOTA_EXCEEDED",
        "MNEMO_STORAGE_UNAVAILABLE",
        "MNEMO_INVALID_INPUT",
        "MNEMO_APPLICATION_ERROR",
    }
)


class CheckpointSaveOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"


class CheckpointSaveTelemetryError(RuntimeError):
    """Safe local telemetry failure without payload or path details."""


@dataclass(frozen=True, slots=True)
class CheckpointSaveDiagnosticEvent:
    event_id: UUID
    scope: AutomaticRouteScope
    observed_at: datetime
    operation: str
    outcome: CheckpointSaveOutcome
    duration_ms: int
    error_code: str | None = None
    token_estimate: int | None = None
    compacted: bool | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, UUID) or self.event_id.version != 4:
            raise ValueError("checkpoint diagnostic identity is invalid")
        if not isinstance(self.scope, AutomaticRouteScope):
            raise TypeError("checkpoint diagnostic scope is invalid")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("checkpoint diagnostic timestamp is invalid")
        if self.operation not in _OPERATIONS:
            raise ValueError("checkpoint diagnostic operation is invalid")
        if not isinstance(self.outcome, CheckpointSaveOutcome):
            raise TypeError("checkpoint diagnostic outcome is invalid")
        if (
            not isinstance(self.duration_ms, int)
            or isinstance(self.duration_ms, bool)
            or not 0 <= self.duration_ms <= 10_000_000
        ):
            raise ValueError("checkpoint diagnostic duration is invalid")
        if self.outcome is CheckpointSaveOutcome.SUCCESS:
            if self.error_code is not None:
                raise ValueError("successful checkpoint diagnostic cannot have an error")
        elif self.error_code not in _ERROR_CODES:
            raise ValueError("failed checkpoint diagnostic requires a safe error code")
        if self.token_estimate is not None and (
            not isinstance(self.token_estimate, int)
            or isinstance(self.token_estimate, bool)
            or not 0 <= self.token_estimate <= 600
        ):
            raise ValueError("checkpoint diagnostic token estimate is invalid")
        if self.compacted is not None and not isinstance(self.compacted, bool):
            raise TypeError("checkpoint diagnostic compacted flag is invalid")

    def to_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "event_id": str(self.event_id),
            "scope": self.scope.to_dict(),
            "observed_at": self.observed_at.astimezone(UTC).isoformat(),
            "operation": self.operation,
            "outcome": self.outcome.value,
            "duration_ms": self.duration_ms,
        }
        if self.error_code is not None:
            value["error_code"] = self.error_code
        if self.token_estimate is not None:
            value["token_estimate"] = self.token_estimate
        if self.compacted is not None:
            value["compacted"] = self.compacted
        return value

    @classmethod
    def from_dict(cls, value: object) -> CheckpointSaveDiagnosticEvent:
        if not isinstance(value, dict):
            raise ValueError("checkpoint diagnostic event is invalid")
        required = {"event_id", "scope", "observed_at", "operation", "outcome", "duration_ms"}
        allowed = required | {"error_code", "token_estimate", "compacted"}
        if not required.issubset(value) or not set(value).issubset(allowed):
            raise ValueError("checkpoint diagnostic event is invalid")
        observed_at = datetime.fromisoformat(_string(value["observed_at"]))
        return cls(
            UUID(_string(value["event_id"])),
            AutomaticRouteScope.from_dict(value["scope"]),
            observed_at,
            _string(value["operation"]),
            CheckpointSaveOutcome(_string(value["outcome"])),
            _integer(value["duration_ms"]),
            _optional_string(value.get("error_code")),
            _optional_integer(value.get("token_estimate")),
            _optional_boolean(value.get("compacted")),
        )


class LocalCheckpointSaveTelemetryStore:
    """Keep bounded, private, scope-filtered checkpoint save diagnostics."""

    def __init__(
        self, data_directory: Path, *, maximum_events: int = 256, retention_days: int = 7
    ) -> None:
        if not 1 <= maximum_events <= 4_096 or not 1 <= retention_days <= 90:
            raise ValueError("checkpoint diagnostic storage bounds are invalid")
        self._directory = data_directory.expanduser().resolve()
        self.path = self._directory / "checkpoint-save-telemetry.json"
        self._maximum_events = maximum_events
        self._retention_days = retention_days

    def record(self, event: CheckpointSaveDiagnosticEvent) -> None:
        if not isinstance(event, CheckpointSaveDiagnosticEvent):
            raise TypeError("checkpoint diagnostic event is invalid")
        with self._lock():
            events = self._read()
            events = [
                item
                for item in self._current(events, event.observed_at)
                if item.event_id != event.event_id
            ]
            events.append(event)
            events.sort(key=lambda item: (item.observed_at, str(item.event_id)))
            self._write(events[-self._maximum_events :])

    def events(
        self, scope: AutomaticRouteScope, *, limit: int = 20
    ) -> tuple[CheckpointSaveDiagnosticEvent, ...]:
        if not isinstance(scope, AutomaticRouteScope) or not 1 <= limit <= 100:
            raise ValueError("checkpoint diagnostic query is invalid")
        selected = [event for event in self._current(self._read()) if event.scope == scope]
        return tuple(reversed(selected[-limit:]))

    def purge(self, scope: AutomaticRouteScope) -> int:
        if not isinstance(scope, AutomaticRouteScope):
            raise TypeError("checkpoint diagnostic scope is invalid")
        with self._lock():
            events = self._read()
            removed = sum(event.scope == scope for event in events)
            if removed:
                self._write([event for event in events if event.scope != scope])
            return removed

    def _current(
        self,
        events: list[CheckpointSaveDiagnosticEvent],
        as_of: datetime | None = None,
    ) -> list[CheckpointSaveDiagnosticEvent]:
        now = datetime.now(UTC) if as_of is None else as_of.astimezone(UTC)
        cutoff = now - timedelta(days=self._retention_days)
        return [event for event in events if event.observed_at.astimezone(UTC) >= cutoff]

    def _read(self) -> list[CheckpointSaveDiagnosticEvent]:
        if not self.path.exists():
            return []
        if self.path.is_symlink() or not self.path.is_file():
            raise CheckpointSaveTelemetryError("checkpoint diagnostic state is unavailable")
        try:
            if self.path.stat().st_size > _MAXIMUM_FILE_BYTES:
                raise ValueError
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if (
                not isinstance(value, dict)
                or set(value) != {"version", "events"}
                or value["version"] != _FORMAT_VERSION
                or not isinstance(value["events"], list)
            ):
                raise ValueError
            return [CheckpointSaveDiagnosticEvent.from_dict(item) for item in value["events"]][
                -self._maximum_events :
            ]
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise CheckpointSaveTelemetryError(
                "checkpoint diagnostic state is unavailable"
            ) from error

    def _write(self, events: list[CheckpointSaveDiagnosticEvent]) -> None:
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
                    {"version": _FORMAT_VERSION, "events": [event.to_dict() for event in events]},
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
            raise CheckpointSaveTelemetryError(
                "checkpoint diagnostic state is unavailable"
            ) from error
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
            descriptor = os.open(self._directory / ".checkpoint-save-telemetry.lock", flags, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        except OSError as error:
            raise CheckpointSaveTelemetryError(
                "checkpoint diagnostic lock is unavailable"
            ) from error
        finally:
            if descriptor is not None:
                with suppress(OSError):
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("checkpoint diagnostic string is invalid")
    return value


def _optional_string(value: object) -> str | None:
    return None if value is None else _string(value)


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("checkpoint diagnostic integer is invalid")
    return value


def _optional_integer(value: object) -> int | None:
    return None if value is None else _integer(value)


def _optional_boolean(value: object) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError("checkpoint diagnostic boolean is invalid")
    return value
