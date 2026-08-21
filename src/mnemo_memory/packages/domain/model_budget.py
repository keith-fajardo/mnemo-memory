"""Storage-neutral reservation contract for bounded optional model calls."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from .identifiers import WorkspaceId

_MAX_BIGINT = 9_223_372_036_854_775_807


class ModelTaskType(StrEnum):
    EPISODIC_CANDIDATE_EXTRACTION = "episodic_candidate_extraction"
    FRONTIER_TAKEOVER = "frontier_takeover"


class ModelBudgetDenied(RuntimeError):
    """A payload-free denial raised before an optional model provider call."""


@dataclass(frozen=True, slots=True)
class ModelBudgetReservation:
    input_tokens: int
    output_tokens: int
    cost_microusd: int

    def __post_init__(self) -> None:
        for value in (self.input_tokens, self.output_tokens):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 1 <= value <= _MAX_BIGINT
            ):
                raise ValueError("model token reservations must be positive bigint values")
        if (
            isinstance(self.cost_microusd, bool)
            or not isinstance(self.cost_microusd, int)
            or not 0 <= self.cost_microusd <= _MAX_BIGINT
        ):
            raise ValueError("model cost reservation must be a non-negative bigint value")


class ModelBudgetReservationPort(Protocol):
    def reserve(
        self,
        workspace_id: WorkspaceId,
        task_type: ModelTaskType,
        reservation: ModelBudgetReservation,
    ) -> None: ...
