"""Bounded process-local request limiting after authenticated team scope resolution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from time import monotonic

from mnemo_memory.packages.domain import OwnerId, WorkspaceId


@dataclass(frozen=True, slots=True)
class TeamRequestRateLimit:
    requests: int = 120
    window_seconds: int = 60
    tracked_identities: int = 10_000

    def __post_init__(self) -> None:
        for value in (self.requests, self.window_seconds, self.tracked_identities):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError("team request rate limit is invalid")
        if self.requests > 100_000 or self.window_seconds > 86_400:
            raise ValueError("team request rate limit is invalid")
        if self.tracked_identities > 100_000:
            raise ValueError("team request rate-limit identity bound is invalid")


class TeamRequestRateLimiter:
    def __init__(
        self,
        limit: TeamRequestRateLimit,
        *,
        timer: Callable[[], float] = monotonic,
    ) -> None:
        self._limit = limit
        self._timer = timer
        self._windows: dict[tuple[OwnerId, WorkspaceId], tuple[float, int]] = {}
        self._lock = Lock()

    def require(self, principal_id: OwnerId, workspace_id: WorkspaceId) -> None:
        now = self._timer()
        key = principal_id, workspace_id
        with self._lock:
            started, count = self._windows.get(key, (now, 0))
            if now < started or now - started >= self._limit.window_seconds:
                started, count = now, 0
            if count >= self._limit.requests:
                raise ValueError("MNEMO_RATE_LIMITED: authenticated request limit exceeded")
            if key not in self._windows and len(self._windows) >= self._limit.tracked_identities:
                self._remove_expired(now)
                if len(self._windows) >= self._limit.tracked_identities:
                    raise ValueError("MNEMO_RATE_LIMITED: authenticated request limit exceeded")
            self._windows[key] = started, count + 1

    def _remove_expired(self, now: float) -> None:
        expired = tuple(
            key
            for key, (started, _) in self._windows.items()
            if now < started or now - started >= self._limit.window_seconds
        )
        for key in expired:
            del self._windows[key]
