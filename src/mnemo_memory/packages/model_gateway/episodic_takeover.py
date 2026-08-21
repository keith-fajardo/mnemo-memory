"""RawEpisodicExtractionProvider adapter that adds local-first frontier takeover."""

from __future__ import annotations

from collections.abc import Callable

from ..domain.model_budget import (
    ModelBudgetReservation,
    ModelBudgetReservationPort,
    ModelTaskType,
)
from .episodic_extraction import (
    EpisodicExtractionRequest,
    RawEpisodicExtractionProvider,
    parse_episodic_output,
)
from .local_first_takeover import run_local_first_takeover


class TakeoverEpisodicProvider:
    """Local-first provider; escalates to an optional frontier provider on invalid output."""

    def __init__(
        self,
        *,
        local: RawEpisodicExtractionProvider,
        frontier: RawEpisodicExtractionProvider | None,
        authorized: Callable[[], bool],
        budget: ModelBudgetReservationPort,
        reservation: ModelBudgetReservation,
        workspace_id: str,
        on_route: Callable[[str], None] = lambda route: None,
    ) -> None:
        self._local = local
        self._frontier = frontier
        self._authorized = authorized
        self._budget = budget
        self._reservation = reservation
        self._workspace_id = workspace_id
        self._on_route = on_route

    @property
    def provider_id(self) -> str:
        return self._local.provider_id

    @property
    def model_id(self) -> str:
        return self._local.model_id

    def generate(self, request: EpisodicExtractionRequest) -> object:
        frontier = None
        if self._frontier is not None:
            frontier = lambda: self._frontier.generate(request)  # noqa: E731
        return run_local_first_takeover(
            local=lambda: self._local.generate(request),
            frontier=frontier,
            validate=lambda raw: parse_episodic_output(raw, request.max_candidates),
            authorized=self._authorized,
            reserve_frontier=lambda: self._budget.reserve(
                self._workspace_id, ModelTaskType.FRONTIER_TAKEOVER, self._reservation
            ),
            on_route=self._on_route,
        )
