"""Provider-neutral local-first execution with one bounded frontier takeover."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

Raw = TypeVar("Raw")

_ESCALATION_TRIGGERS = (TypeError, ValueError)


class TakeoverError(RuntimeError):
    """Payload-free takeover failure with a stable diagnostic code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def run_local_first_takeover(
    *,
    local: Callable[[], Raw],
    frontier: Callable[[], Raw] | None,
    validate: Callable[[Raw], None],
    authorized: Callable[[], bool],
    reserve_frontier: Callable[[], None],
    on_route: Callable[[str], None] = lambda route: None,
) -> Raw:
    """Run local, validate, and escalate to frontier at most once on a validity failure."""

    try:
        candidate = local()
        validate(candidate)
    except _ESCALATION_TRIGGERS as local_failure:
        if frontier is None or not authorized():
            raise
        try:
            reserve_frontier()
        except Exception:
            # Denied/unavailable frontier budget: fail closed to the local failure.
            raise local_failure
        escalated = frontier()
        validate(escalated)  # frontier invalid -> propagate -> fail closed
        on_route("frontier")
        return escalated
    on_route("local")
    return candidate
