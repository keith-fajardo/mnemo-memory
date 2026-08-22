from types import SimpleNamespace
from typing import cast

import pytest

from mnemo_memory.packages.domain.episodic_candidates import EpisodicExtractionRequest
from mnemo_memory.packages.domain.identifiers import WorkspaceId
from mnemo_memory.packages.domain.model_budget import (
    ModelBudgetReservation,
    ModelTaskType,
)
from mnemo_memory.packages.model_gateway.episodic_takeover import TakeoverEpisodicProvider

VALID = {
    "candidates": [
        {
            "kind": "decision",
            "claim": "x",
            "confidence": 0.9,
            "sensitivity": "normal",
        }
    ]
}
INVALID = {"candidates": [{"bad": True}]}


class FakeProvider:
    def __init__(self, out: object, pid: str = "ollama", mid: str = "ministral-3:8b") -> None:
        self._out, self._pid, self._mid = out, pid, mid

    @property
    def provider_id(self) -> str:
        return self._pid

    @property
    def model_id(self) -> str:
        return self._mid

    def generate(self, request: EpisodicExtractionRequest) -> object:
        return self._out


def _req() -> EpisodicExtractionRequest:
    # The adapter only reads request.max_candidates and passes the object through,
    # so a lightweight stand-in avoids constructing heavy domain objects.
    return cast(EpisodicExtractionRequest, SimpleNamespace(max_candidates=4))


def test_valid_local_no_frontier() -> None:
    frontier = FakeProvider(VALID)
    p = TakeoverEpisodicProvider(
        local=FakeProvider(VALID),
        frontier=frontier,
        authorized=lambda: True,
        budget=_NullBudget(),
        reservation=_res(),
        workspace_id=WorkspaceId.new(),
    )
    assert p.generate(_req()) == VALID


def test_invalid_local_escalates_to_frontier() -> None:
    p = TakeoverEpisodicProvider(
        local=FakeProvider(INVALID),
        frontier=FakeProvider(VALID),
        authorized=lambda: True,
        budget=_NullBudget(),
        reservation=_res(),
        workspace_id=WorkspaceId.new(),
    )
    assert p.generate(_req()) == VALID


def test_default_off_no_frontier_provider_fails_closed() -> None:
    p = TakeoverEpisodicProvider(
        local=FakeProvider(INVALID),
        frontier=None,
        authorized=lambda: True,
        budget=_NullBudget(),
        reservation=_res(),
        workspace_id=WorkspaceId.new(),
    )
    with pytest.raises((TypeError, ValueError)):
        p.generate(_req())


# helpers
def _res() -> ModelBudgetReservation:
    return ModelBudgetReservation(input_tokens=2000, output_tokens=1000, cost_microusd=0)


class _NullBudget:
    def reserve(
        self,
        workspace_id: WorkspaceId,
        task_type: ModelTaskType,
        reservation: ModelBudgetReservation,
    ) -> None:
        return None
