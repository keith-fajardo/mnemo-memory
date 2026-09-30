import asyncio
import inspect
from collections.abc import Mapping

from mnemo_memory.apps.cli.typed_decision_composition import (
    RUNTIME_DEADLINE_SECONDS,
    build_runtime_typed_decision_classifier,
)
from mnemo_memory.packages.application import PersonalSettings
from mnemo_memory.packages.domain import TypedDecisionUnavailableReason
from mnemo_memory.packages.model_gateway.decision_axes import FRONT_DOOR_AXES


class NeverTransport:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> bytes:
        self.calls += 1
        raise AssertionError("runtime text must never reach the network in phase 1")


def test_default_settings_compose_nothing() -> None:
    assert build_runtime_typed_decision_classifier(PersonalSettings(), environ={}) is None


def test_enabled_runtime_classifier_is_data_route_blocked_without_network() -> None:
    transport = NeverTransport()
    settings = PersonalSettings(experimental_typed_decisions_enabled=True)
    environments: tuple[dict[str, str], ...] = ({"TYPESAFE_API_KEY": "test-key-not-real"}, {})
    for environ in environments:
        guard = build_runtime_typed_decision_classifier(
            settings, environ=environ, jev_transport=transport
        )
        assert guard is not None
        outcome = asyncio.run(guard.ask(FRONT_DOOR_AXES, "What did we decide last week?"))
        assert outcome.unavailable_reason is TypedDecisionUnavailableReason.DATA_ROUTE_BLOCKED
    assert transport.calls == 0


def test_runtime_source_cannot_be_chosen_by_callers() -> None:
    parameters = inspect.signature(build_runtime_typed_decision_classifier).parameters
    assert "source" not in parameters and "data_route" not in parameters
    assert RUNTIME_DEADLINE_SECONDS == 0.6
