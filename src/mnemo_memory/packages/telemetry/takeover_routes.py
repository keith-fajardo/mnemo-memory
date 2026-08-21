"""Content-free counters for local-first takeover route decisions."""

from __future__ import annotations

from collections import Counter

_ROUTES = frozenset({"local", "frontier"})


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
