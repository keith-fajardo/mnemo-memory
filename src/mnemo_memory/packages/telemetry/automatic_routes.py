"""Bounded local telemetry for automatic context-route costs and outcomes."""

from __future__ import annotations

import fcntl
import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID

_FORMAT_VERSION = 1
_MAXIMUM_FILE_BYTES = 1_048_576
_SAFE_REASON = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_ROUTES = frozenset(
    {
        "none",
        "direct_lookup",
        "local_diagnostics",
        "prior_memory",
        "knowledge",
        "structure",
        "skill_discovery",
    }
)
_SHADOW_NEEDS = frozenset({"yes", "no", "unknown"})
_SEMANTIC_ROUTES = frozenset({"none", "prior_memory", "knowledge", "structure"})


class AutomaticRouteTelemetryError(RuntimeError):
    """Safe telemetry failure without local content or path details."""


class AutomaticRouteOutcome(StrEnum):
    HIT = "hit"
    MISS = "miss"
    NO_ATTACHMENT = "no_attachment"
    CANDIDATE = "candidate"
    ERROR = "error"


class AutomaticRouteToolCategory(StrEnum):
    DIRECT_INSPECTION = "direct_inspection"
    MNEMO = "mnemo"
    MUTATION = "mutation"
    OTHER = "other"


class AutomaticRouteFeedback(StrEnum):
    HELPFUL = "helpful"
    NOISE = "noise"
    MISSING = "missing"


class AutomaticRouteDiagnosticsMode(StrEnum):
    OFF = "off"
    SUMMARY = "summary"
    TRACE = "trace"


@dataclass(frozen=True, slots=True)
class AutomaticRouteDiagnosticsSettings:
    mode: AutomaticRouteDiagnosticsMode = AutomaticRouteDiagnosticsMode.SUMMARY
    retention_days: int = 7

    def __post_init__(self) -> None:
        if not isinstance(self.mode, AutomaticRouteDiagnosticsMode):
            raise TypeError("automatic route diagnostic mode is invalid")
        if (
            not isinstance(self.retention_days, int)
            or isinstance(self.retention_days, bool)
            or not 1 <= self.retention_days <= 90
        ):
            raise ValueError("automatic route diagnostic retention is invalid")

    def to_dict(self) -> dict[str, object]:
        return {"mode": self.mode.value, "retention_days": self.retention_days}


@dataclass(frozen=True, slots=True)
class AutomaticRouteScope:
    owner_id: str
    workspace_id: str
    project_id: str
    session_id: str
    task_id: str
    visibility: str

    def __post_init__(self) -> None:
        for value in (
            self.owner_id,
            self.workspace_id,
            self.project_id,
            self.session_id,
            self.task_id,
        ):
            try:
                UUID(value)
            except (TypeError, ValueError) as error:
                raise ValueError("automatic route scope identifier is invalid") from error
        if self.visibility != "project":
            raise ValueError("automatic route telemetry requires project visibility")

    def to_dict(self) -> dict[str, str]:
        return {
            "owner_id": self.owner_id,
            "workspace_id": self.workspace_id,
            "project_id": self.project_id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "visibility": self.visibility,
        }

    @classmethod
    def from_dict(cls, value: object) -> AutomaticRouteScope:
        if not isinstance(value, dict) or set(value) != {
            "owner_id",
            "workspace_id",
            "project_id",
            "session_id",
            "task_id",
            "visibility",
        }:
            raise ValueError("automatic route scope is invalid")
        if any(not isinstance(item, str) for item in value.values()):
            raise ValueError("automatic route scope is invalid")
        return cls(
            owner_id=_string(value["owner_id"]),
            workspace_id=_string(value["workspace_id"]),
            project_id=_string(value["project_id"]),
            session_id=_string(value["session_id"]),
            task_id=_string(value["task_id"]),
            visibility=_string(value["visibility"]),
        )


@dataclass(frozen=True, slots=True)
class AutomaticRouteEvent:
    event_id: UUID
    scope: AutomaticRouteScope
    observed_at: datetime
    client: str
    route: str
    reason: str
    outcome: AutomaticRouteOutcome
    fallback_route: str | None
    maximum_attachment_tokens: int
    canonical_tokens: int
    rendered_characters: int
    rendered_bytes: int
    rendered_estimated_tokens: int
    duration_ms: int
    skill_candidate_count: int
    duplicate_render: bool
    tool_calls: tuple[tuple[str, int], ...] = ()
    tool_result_estimated_tokens: int = 0
    measured_tool_result_calls: int = 0
    shadow_structural_need: str | None = None
    shadow_long_term_need: str | None = None
    shadow_reason: str | None = None
    shadow_structural_tokens: int = 0
    shadow_long_term_tokens: int = 0
    shadow_shared_maximum_tokens: int = 0
    semantic_invoked: bool = False
    semantic_route: str | None = None
    semantic_latency_ms: int = 0
    feedback: AutomaticRouteFeedback | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, UUID) or self.event_id.version != 4:
            raise ValueError("automatic route event identity is invalid")
        if not isinstance(self.scope, AutomaticRouteScope):
            raise TypeError("automatic route event scope is invalid")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("automatic route event timestamp must be timezone-aware")
        if self.client not in {"codex", "claude-code"}:
            raise ValueError("automatic route client is invalid")
        if self.route not in _ROUTES:
            raise ValueError("automatic route is invalid")
        if _SAFE_REASON.fullmatch(self.reason) is None:
            raise ValueError("automatic route reason is invalid")
        if not isinstance(self.outcome, AutomaticRouteOutcome):
            raise TypeError("automatic route outcome is invalid")
        if self.fallback_route is not None and self.fallback_route not in _ROUTES:
            raise ValueError("automatic fallback route is invalid")
        for value in (
            self.maximum_attachment_tokens,
            self.canonical_tokens,
            self.rendered_characters,
            self.rendered_bytes,
            self.rendered_estimated_tokens,
            self.duration_ms,
            self.skill_candidate_count,
            self.tool_result_estimated_tokens,
            self.measured_tool_result_calls,
            self.shadow_structural_tokens,
            self.shadow_long_term_tokens,
            self.shadow_shared_maximum_tokens,
            self.semantic_latency_ms,
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not 0 <= value <= 10_000_000
            ):
                raise ValueError("automatic route numeric metric is invalid")
        if self.skill_candidate_count > 3:
            raise ValueError("automatic route skill candidate count is invalid")
        if not isinstance(self.duplicate_render, bool):
            raise TypeError("automatic route duplicate flag is invalid")
        categories = dict(self.tool_calls)
        if len(categories) != len(self.tool_calls) or any(
            category not in {item.value for item in AutomaticRouteToolCategory}
            or not isinstance(count, int)
            or isinstance(count, bool)
            or not 0 <= count <= 10_000
            for category, count in self.tool_calls
        ):
            raise ValueError("automatic route tool counts are invalid")
        if self.measured_tool_result_calls > sum(categories.values()):
            raise ValueError("automatic route measured tool count is invalid")
        if (self.shadow_structural_need is None) != (self.shadow_long_term_need is None):
            raise ValueError("automatic route shadow needs are invalid")
        if self.shadow_structural_need is None:
            if (
                self.shadow_reason is not None
                or self.shadow_structural_tokens
                or self.shadow_long_term_tokens
                or self.shadow_shared_maximum_tokens
                or self.semantic_invoked
                or self.semantic_route is not None
                or self.semantic_latency_ms
            ):
                raise ValueError("automatic route shadow metadata is invalid")
        elif (
            self.shadow_structural_need not in _SHADOW_NEEDS
            or self.shadow_long_term_need not in _SHADOW_NEEDS
            or self.shadow_reason is None
            or _SAFE_REASON.fullmatch(self.shadow_reason) is None
            or self.shadow_shared_maximum_tokens != 1_300
            or self.shadow_structural_tokens + self.shadow_long_term_tokens
            > self.shadow_shared_maximum_tokens
        ):
            raise ValueError("automatic route shadow metadata is invalid")
        if not isinstance(self.semantic_invoked, bool):
            raise TypeError("automatic route semantic flag is invalid")
        if self.semantic_invoked != (self.semantic_route is not None):
            raise ValueError("automatic route semantic metadata is invalid")
        if self.semantic_route is not None and self.semantic_route not in _SEMANTIC_ROUTES:
            raise ValueError("automatic route semantic route is invalid")
        if self.feedback is not None and not isinstance(self.feedback, AutomaticRouteFeedback):
            raise TypeError("automatic route feedback is invalid")

    def with_tool_observation(
        self, category: AutomaticRouteToolCategory, result_characters: int | None
    ) -> AutomaticRouteEvent:
        counts = dict(self.tool_calls)
        counts[category.value] = counts.get(category.value, 0) + 1
        tokens = self.tool_result_estimated_tokens
        measured_calls = self.measured_tool_result_calls
        if result_characters is not None:
            if not isinstance(result_characters, int) or isinstance(result_characters, bool):
                raise TypeError("tool result character count must be an integer or null")
            if not 0 <= result_characters <= 10_000_000:
                raise ValueError("tool result character count is out of bounds")
            tokens += (result_characters + 3) // 4
            measured_calls += 1
        fallback_route = self.fallback_route
        if (
            self.outcome is AutomaticRouteOutcome.MISS
            and category is AutomaticRouteToolCategory.DIRECT_INSPECTION
        ):
            fallback_route = "direct_lookup"
        return replace(
            self,
            fallback_route=fallback_route,
            tool_calls=tuple(sorted(counts.items())),
            tool_result_estimated_tokens=tokens,
            measured_tool_result_calls=measured_calls,
        )

    def with_delivery(
        self,
        rendered_characters: int,
        rendered_bytes: int,
        *,
        duplicate_render: bool,
    ) -> AutomaticRouteEvent:
        """Replace preliminary render counts with the client-delivered hook output counts."""

        if (
            not isinstance(rendered_characters, int)
            or isinstance(rendered_characters, bool)
            or not 0 <= rendered_characters <= 10_000_000
            or not isinstance(rendered_bytes, int)
            or isinstance(rendered_bytes, bool)
            or not 0 <= rendered_bytes <= 10_000_000
        ):
            raise ValueError("automatic route delivery metric is invalid")
        if not isinstance(duplicate_render, bool):
            raise TypeError("automatic route duplicate flag is invalid")
        return replace(
            self,
            rendered_characters=rendered_characters,
            rendered_bytes=rendered_bytes,
            rendered_estimated_tokens=(rendered_characters + 3) // 4,
            duplicate_render=duplicate_render,
        )

    def with_feedback(self, feedback: AutomaticRouteFeedback) -> AutomaticRouteEvent:
        if not isinstance(feedback, AutomaticRouteFeedback):
            raise TypeError("automatic route feedback is invalid")
        return replace(self, feedback=feedback)

    def to_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "event_id": str(self.event_id),
            "scope": self.scope.to_dict(),
            "observed_at": self.observed_at.astimezone(UTC).isoformat(),
            "client": self.client,
            "route": self.route,
            "reason": self.reason,
            "outcome": self.outcome.value,
            "fallback_route": self.fallback_route,
            "maximum_attachment_tokens": self.maximum_attachment_tokens,
            "canonical_tokens": self.canonical_tokens,
            "rendered_characters": self.rendered_characters,
            "rendered_bytes": self.rendered_bytes,
            "rendered_estimated_tokens": self.rendered_estimated_tokens,
            "duration_ms": self.duration_ms,
            "skill_candidate_count": self.skill_candidate_count,
            "duplicate_render": self.duplicate_render,
            "tool_calls": dict(self.tool_calls),
            "tool_result_estimated_tokens": self.tool_result_estimated_tokens,
            "measured_tool_result_calls": self.measured_tool_result_calls,
        }
        if self.shadow_structural_need is not None or self.feedback is not None:
            value.update(
                {
                    "shadow_structural_need": self.shadow_structural_need,
                    "shadow_long_term_need": self.shadow_long_term_need,
                    "shadow_reason": self.shadow_reason,
                    "shadow_structural_tokens": self.shadow_structural_tokens,
                    "shadow_long_term_tokens": self.shadow_long_term_tokens,
                    "shadow_shared_maximum_tokens": self.shadow_shared_maximum_tokens,
                    "semantic_invoked": self.semantic_invoked,
                    "semantic_route": self.semantic_route,
                    "semantic_latency_ms": self.semantic_latency_ms,
                    "feedback": None if self.feedback is None else self.feedback.value,
                }
            )
        return value

    @classmethod
    def from_dict(cls, value: object) -> AutomaticRouteEvent:
        required = {
            "event_id",
            "scope",
            "observed_at",
            "client",
            "route",
            "reason",
            "outcome",
            "fallback_route",
            "maximum_attachment_tokens",
            "canonical_tokens",
            "rendered_characters",
            "rendered_bytes",
            "rendered_estimated_tokens",
            "duration_ms",
            "skill_candidate_count",
            "duplicate_render",
            "tool_calls",
            "tool_result_estimated_tokens",
            "measured_tool_result_calls",
        }
        shadow = {
            "shadow_structural_need",
            "shadow_long_term_need",
            "shadow_reason",
            "shadow_structural_tokens",
            "shadow_long_term_tokens",
            "shadow_shared_maximum_tokens",
            "semantic_invoked",
            "semantic_route",
            "semantic_latency_ms",
            "feedback",
        }
        if not isinstance(value, dict) or frozenset(value) not in {
            frozenset(required),
            frozenset(required | shadow),
        }:
            raise ValueError("automatic route event is invalid")
        tool_calls = value["tool_calls"]
        if not isinstance(tool_calls, dict) or any(
            not isinstance(key, str) or not isinstance(count, int)
            for key, count in tool_calls.items()
        ):
            raise ValueError("automatic route tool counts are invalid")
        fallback = value["fallback_route"]
        if fallback is not None and not isinstance(fallback, str):
            raise ValueError("automatic fallback route is invalid")
        shadow_structural_need = value.get("shadow_structural_need")
        shadow_long_term_need = value.get("shadow_long_term_need")
        shadow_reason = value.get("shadow_reason")
        semantic_route = value.get("semantic_route")
        feedback = value.get("feedback")
        for item in (
            shadow_structural_need,
            shadow_long_term_need,
            shadow_reason,
            semantic_route,
            feedback,
        ):
            if item is not None and not isinstance(item, str):
                raise ValueError("automatic route shadow string is invalid")
        return cls(
            UUID(_string(value["event_id"])),
            AutomaticRouteScope.from_dict(value["scope"]),
            datetime.fromisoformat(_string(value["observed_at"])),
            _string(value["client"]),
            _string(value["route"]),
            _string(value["reason"]),
            AutomaticRouteOutcome(_string(value["outcome"])),
            fallback,
            _integer(value["maximum_attachment_tokens"]),
            _integer(value["canonical_tokens"]),
            _integer(value["rendered_characters"]),
            _integer(value["rendered_bytes"]),
            _integer(value["rendered_estimated_tokens"]),
            _integer(value["duration_ms"]),
            _integer(value["skill_candidate_count"]),
            _boolean(value["duplicate_render"]),
            tuple(sorted((key, _integer(count)) for key, count in tool_calls.items())),
            _integer(value["tool_result_estimated_tokens"]),
            _integer(value["measured_tool_result_calls"]),
            shadow_structural_need,
            shadow_long_term_need,
            shadow_reason,
            _integer(value.get("shadow_structural_tokens", 0)),
            _integer(value.get("shadow_long_term_tokens", 0)),
            _integer(value.get("shadow_shared_maximum_tokens", 0)),
            _boolean(value.get("semantic_invoked", False)),
            semantic_route,
            _integer(value.get("semantic_latency_ms", 0)),
            None if feedback is None else AutomaticRouteFeedback(feedback),
        )


@dataclass(frozen=True, slots=True)
class AutomaticRouteSummary:
    status: str
    event_count: int
    totals: dict[str, int]
    routes: dict[str, dict[str, int]]

    def to_dict(self) -> dict[str, object]:
        return {
            "format": "mnemo.automatic-route-summary.v1",
            "status": self.status,
            "event_count": self.event_count,
            "totals": dict(self.totals),
            "routes": {key: dict(value) for key, value in self.routes.items()},
        }


class LocalAutomaticRouteDiagnosticsSettingsStore:
    """Private opt-in level and bounded retention for automatic route footprints."""

    def __init__(self, data_directory: Path) -> None:
        self._directory = data_directory.expanduser().resolve()
        self.path = self._directory / "automatic-route-diagnostics.json"
        self._lock_path = self._directory / ".automatic-route-diagnostics.lock"

    def load(self) -> AutomaticRouteDiagnosticsSettings:
        if not self.path.exists():
            return AutomaticRouteDiagnosticsSettings()
        if self.path.is_symlink() or not self.path.is_file():
            raise AutomaticRouteTelemetryError("automatic route diagnostic settings are unsafe")
        try:
            if self.path.stat().st_size > 4_096:
                raise ValueError
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if (
                not isinstance(value, dict)
                or set(value) != {"version", "settings"}
                or value["version"] != 1
                or not isinstance(value["settings"], dict)
                or set(value["settings"]) != {"mode", "retention_days"}
            ):
                raise ValueError
            settings = value["settings"]
            return AutomaticRouteDiagnosticsSettings(
                AutomaticRouteDiagnosticsMode(_string(settings["mode"])),
                _integer(settings["retention_days"]),
            )
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise AutomaticRouteTelemetryError(
                "automatic route diagnostic settings are unavailable"
            ) from error

    def save(
        self, settings: AutomaticRouteDiagnosticsSettings
    ) -> AutomaticRouteDiagnosticsSettings:
        if not isinstance(settings, AutomaticRouteDiagnosticsSettings):
            raise TypeError("automatic route diagnostic settings are invalid")
        payload = json.dumps(
            {"version": 1, "settings": settings.to_dict()},
            sort_keys=True,
            separators=(",", ":"),
        )
        temporary: Path | None = None
        descriptor: int | None = None
        try:
            self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self._directory, 0o700)
            flags = os.O_CREAT | os.O_RDWR
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(self._lock_path, flags, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            if self.path.is_symlink():
                raise OSError
            with NamedTemporaryFile(
                "w", encoding="utf-8", dir=self._directory, delete=False
            ) as handle:
                temporary = Path(handle.name)
                handle.write(payload)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        except OSError as error:
            raise AutomaticRouteTelemetryError(
                "automatic route diagnostic settings are unavailable"
            ) from error
        finally:
            if descriptor is not None:
                with suppress(OSError):
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return settings


class LocalAutomaticRouteTelemetryStore:
    """Keep a bounded private snapshot of content-free route measurements."""

    def __init__(
        self,
        data_directory: Path,
        *,
        maximum_events: int = 256,
        retention_days: int = 7,
    ) -> None:
        if not isinstance(maximum_events, int) or isinstance(maximum_events, bool):
            raise TypeError("maximum route events must be an integer")
        if not 1 <= maximum_events <= 4_096:
            raise ValueError("maximum route events are out of bounds")
        if (
            not isinstance(retention_days, int)
            or isinstance(retention_days, bool)
            or not 1 <= retention_days <= 90
        ):
            raise ValueError("automatic route retention is out of bounds")
        self._directory = data_directory.expanduser().resolve()
        self.path = self._directory / "automatic-route-telemetry.json"
        self._maximum_events = maximum_events
        self._retention_days = retention_days

    def record(self, event: AutomaticRouteEvent) -> None:
        if not isinstance(event, AutomaticRouteEvent):
            raise TypeError("automatic route event is invalid")
        with self._lock():
            events, _ = self._read()
            events = [
                candidate
                for candidate in self._current(events, event.observed_at)
                if candidate.event_id != event.event_id
            ]
            events.append(event)
            events.sort(key=lambda item: (item.observed_at, str(item.event_id)))
            self._write(events[-self._maximum_events :])

    def record_tool_observation(
        self,
        event_id: UUID,
        category: AutomaticRouteToolCategory,
        *,
        result_characters: int | None,
    ) -> None:
        if not isinstance(event_id, UUID) or not isinstance(category, AutomaticRouteToolCategory):
            raise TypeError("automatic route tool observation is invalid")
        with self._lock():
            events, status = self._read()
            if status == "corrupt":
                return
            updated = False
            values: list[AutomaticRouteEvent] = []
            for event in events:
                if event.event_id == event_id:
                    event = event.with_tool_observation(category, result_characters)
                    updated = True
                values.append(event)
            if updated:
                self._write(values)

    def record_delivery(
        self,
        event_id: UUID,
        *,
        rendered_characters: int,
        rendered_bytes: int,
        duplicate_render: bool,
    ) -> None:
        """Finalize one event using counts from the complete emitted hook context."""

        if not isinstance(event_id, UUID) or event_id.version != 4:
            raise TypeError("automatic route delivery identity is invalid")
        with self._lock():
            events, status = self._read()
            if status == "corrupt":
                return
            updated = False
            values: list[AutomaticRouteEvent] = []
            for event in events:
                if event.event_id == event_id:
                    event = event.with_delivery(
                        rendered_characters,
                        rendered_bytes,
                        duplicate_render=duplicate_render,
                    )
                    updated = True
                values.append(event)
            if updated:
                self._write(values)

    def summary(self, scope: AutomaticRouteScope) -> AutomaticRouteSummary:
        events, status = self._read()
        selected = [event for event in self._current(events) if event.scope == scope]
        totals = {
            "maximum_attachment_tokens": sum(event.maximum_attachment_tokens for event in selected),
            "canonical_tokens": sum(event.canonical_tokens for event in selected),
            "rendered_bytes": sum(event.rendered_bytes for event in selected),
            "rendered_estimated_tokens": sum(event.rendered_estimated_tokens for event in selected),
            "tool_result_estimated_tokens": sum(
                event.tool_result_estimated_tokens for event in selected
            ),
            "estimated_total_tokens": sum(
                event.rendered_estimated_tokens + event.tool_result_estimated_tokens
                for event in selected
            ),
            "duration_ms": sum(event.duration_ms for event in selected),
            "duplicate_renders": sum(int(event.duplicate_render) for event in selected),
            "tool_calls": sum(count for event in selected for _, count in event.tool_calls),
            "measured_tool_result_calls": sum(
                event.measured_tool_result_calls for event in selected
            ),
        }
        totals["unmeasured_tool_calls"] = (
            totals["tool_calls"] - totals["measured_tool_result_calls"]
        )
        routes: dict[str, dict[str, int]] = {}
        for event in selected:
            values = routes.setdefault(
                event.route,
                {
                    "events": 0,
                    "hits": 0,
                    "misses": 0,
                    "tool_calls": 0,
                    "fallbacks": 0,
                    "duplicate_renders": 0,
                    "maximum_attachment_tokens": 0,
                    "estimated_total_tokens": 0,
                },
            )
            values["events"] += 1
            values["hits"] += int(event.outcome is AutomaticRouteOutcome.HIT)
            values["misses"] += int(event.outcome is AutomaticRouteOutcome.MISS)
            values["tool_calls"] += sum(count for _, count in event.tool_calls)
            values["fallbacks"] += int(event.fallback_route is not None)
            values["duplicate_renders"] += int(event.duplicate_render)
            values["maximum_attachment_tokens"] += event.maximum_attachment_tokens
            values["estimated_total_tokens"] += (
                event.rendered_estimated_tokens + event.tool_result_estimated_tokens
            )
        return AutomaticRouteSummary(status, len(selected), totals, routes)

    def events(
        self, scope: AutomaticRouteScope, *, limit: int = 20
    ) -> tuple[AutomaticRouteEvent, ...]:
        if not isinstance(scope, AutomaticRouteScope):
            raise TypeError("automatic route scope is invalid")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("automatic route event limit is invalid")
        events, status = self._read()
        if status == "corrupt":
            raise AutomaticRouteTelemetryError("automatic route state is unavailable")
        selected = [event for event in self._current(events) if event.scope == scope]
        return tuple(reversed(selected[-limit:]))

    def record_feedback(
        self,
        scope: AutomaticRouteScope,
        event_id: UUID,
        feedback: AutomaticRouteFeedback,
    ) -> bool:
        if (
            not isinstance(scope, AutomaticRouteScope)
            or not isinstance(event_id, UUID)
            or not isinstance(feedback, AutomaticRouteFeedback)
        ):
            raise TypeError("automatic route feedback is invalid")
        with self._lock():
            events, status = self._read()
            if status == "corrupt":
                raise AutomaticRouteTelemetryError("automatic route state is unavailable")
            changed = False
            values: list[AutomaticRouteEvent] = []
            for event in self._current(events):
                if event.event_id == event_id and event.scope == scope:
                    event = event.with_feedback(feedback)
                    changed = True
                values.append(event)
            if changed:
                self._write(values)
            return changed

    def purge(self, scope: AutomaticRouteScope) -> int:
        if not isinstance(scope, AutomaticRouteScope):
            raise TypeError("automatic route scope is invalid")
        with self._lock():
            events, status = self._read()
            if status == "corrupt":
                raise AutomaticRouteTelemetryError("automatic route state is unavailable")
            removed = sum(1 for event in events if event.scope == scope)
            if removed:
                self._write([event for event in events if event.scope != scope])
            return removed

    def _current(
        self, events: list[AutomaticRouteEvent], as_of: datetime | None = None
    ) -> list[AutomaticRouteEvent]:
        now = datetime.now(UTC) if as_of is None else as_of.astimezone(UTC)
        cutoff = now - timedelta(days=self._retention_days)
        return [event for event in events if event.observed_at.astimezone(UTC) >= cutoff]

    def _read(self) -> tuple[list[AutomaticRouteEvent], str]:
        if not self.path.exists():
            return [], "available"
        if self.path.is_symlink():
            return [], "corrupt"
        try:
            if self.path.stat().st_size > _MAXIMUM_FILE_BYTES:
                return [], "corrupt"
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or set(value) != {"version", "events"}:
                return [], "corrupt"
            if value["version"] != _FORMAT_VERSION or not isinstance(value["events"], list):
                return [], "corrupt"
            events = [AutomaticRouteEvent.from_dict(item) for item in value["events"]]
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return [], "corrupt"
        return events[-self._maximum_events :], "available"

    def _write(self, events: list[AutomaticRouteEvent]) -> None:
        try:
            self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self._directory, 0o700)
            if self.path.is_symlink():
                raise AutomaticRouteTelemetryError("automatic route state is unsafe")
            with NamedTemporaryFile(
                "w", encoding="utf-8", dir=self._directory, delete=False
            ) as handle:
                temporary = Path(handle.name)
                os.chmod(temporary, 0o600)
                json.dump(
                    {"version": _FORMAT_VERSION, "events": [event.to_dict() for event in events]},
                    handle,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                handle.write("\n")
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        except OSError as error:
            raise AutomaticRouteTelemetryError("automatic route state is unavailable") from error
        finally:
            if "temporary" in locals():
                temporary.unlink(missing_ok=True)

    @contextmanager
    def _lock(self) -> Iterator[None]:
        try:
            self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self._directory, 0o700)
            flags = os.O_CREAT | os.O_RDWR
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(self._directory / ".automatic-route-telemetry.lock", flags, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        except OSError as error:
            raise AutomaticRouteTelemetryError("automatic route lock is unavailable") from error
        finally:
            if "descriptor" in locals():
                with suppress(OSError):
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("automatic route string is invalid")
    return value


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("automatic route integer is invalid")
    return value


def _boolean(value: object) -> bool:
    if not isinstance(value, bool):
        raise ValueError("automatic route boolean is invalid")
    return value
