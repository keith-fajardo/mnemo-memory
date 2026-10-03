import asyncio
import inspect
import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from mnemo_memory.apps.cli.typed_decision_composition import (
    RUNTIME_DEADLINE_SECONDS,
    _adapter_from_environment,
    build_runtime_typed_decision_classifier,
    build_synthetic_typed_decision_classifier,
)
from mnemo_memory.connectors.typesafe import JevClassifier
from mnemo_memory.packages.application import PersonalSettings
from mnemo_memory.packages.domain import (
    ModelTaskType,
    TypedDecisionSource,
    TypedDecisionUnavailableReason,
)
from mnemo_memory.packages.model_gateway.decision_axes import (
    FILLER_CHECK_BUDGET_SECONDS,
    FRONT_DOOR_AXES,
    NOTE_SUBSTANCE,
)
from mnemo_memory.packages.model_gateway.typed_decisions import TypedDecisionRecord
from mnemo_memory.packages.storage import LocalDailyModelBudget

KEY = "test-key-not-real-0000"


class NeverTransport:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> bytes:
        self.calls += 1
        raise AssertionError("runtime text must never reach the network")


class FillerTransport:
    """Answer every question as confident filler; never opens a socket."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> bytes:
        self.calls += 1
        request = json.loads(body)
        answers = {
            name: {
                "type": "choice",
                "choice": "filler",
                "confidence": 0.9,
                "probabilities": {"task_information": 0.1, "filler": 0.9},
            }
            for name in request["questions"]
        }
        return json.dumps(
            {"model": "jev-1.13.0", "answers": answers, "usage": {"input_tokens": 12}}
        ).encode()


class ListRecorder:
    def __init__(self) -> None:
        self.records: list[TypedDecisionRecord] = []

    def record(self, record: TypedDecisionRecord) -> None:
        self.records.append(record)


def test_default_settings_compose_nothing(tmp_path: Path) -> None:
    assert (
        build_runtime_typed_decision_classifier(
            PersonalSettings(), data_directory=tmp_path, environ={}
        )
        is None
    )


def test_enabled_runtime_classifier_is_data_route_blocked_without_network(tmp_path: Path) -> None:
    transport = NeverTransport()
    settings = PersonalSettings(experimental_typed_decisions_enabled=True)
    environments: tuple[dict[str, str], ...] = ({"TYPESAFE_API_KEY": KEY}, {})
    for environ in environments:
        guard = build_runtime_typed_decision_classifier(
            settings, data_directory=tmp_path, environ=environ, jev_transport=transport
        )
        assert guard is not None
        outcome = asyncio.run(guard.ask(FRONT_DOOR_AXES, "What did we decide last week?"))
        assert outcome.unavailable_reason is TypedDecisionUnavailableReason.DATA_ROUTE_BLOCKED
    assert transport.calls == 0
    assert not (tmp_path / "typed_decision-budget.json").exists()


def test_runtime_source_cannot_be_chosen_by_callers() -> None:
    for builder in (
        build_runtime_typed_decision_classifier,
        build_synthetic_typed_decision_classifier,
    ):
        parameters = inspect.signature(builder).parameters
        assert "source" not in parameters and "data_route" not in parameters
    assert RUNTIME_DEADLINE_SECONDS == FILLER_CHECK_BUDGET_SECONDS == 0.8


def test_runtime_guard_records_each_blocked_request(tmp_path: Path) -> None:
    recorder = ListRecorder()
    guard = build_runtime_typed_decision_classifier(
        PersonalSettings(experimental_typed_decisions_enabled=True),
        data_directory=tmp_path,
        environ={"TYPESAFE_API_KEY": KEY},
        jev_transport=NeverTransport(),
        recorder=recorder,
    )
    assert guard is not None
    asyncio.run(
        guard.ask_each(
            [(FRONT_DOOR_AXES, "a prompt"), ((NOTE_SUBSTANCE,), "a stored note")],
            total_deadline_seconds=FILLER_CHECK_BUDGET_SECONDS,
        )
    )
    assert [record.outcome for record in recorder.records] == [
        "data_route_blocked",
        "data_route_blocked",
    ]
    assert {record.source for record in recorder.records} == {"runtime"}


def test_synthetic_builder_reaches_the_adapter_and_reserves_the_local_budget(
    tmp_path: Path,
) -> None:
    transport = FillerTransport()
    recorder = ListRecorder()
    guard = build_synthetic_typed_decision_classifier(
        PersonalSettings(),
        data_directory=tmp_path,
        environ={"TYPESAFE_API_KEY": KEY},
        jev_transport=transport,
        recorder=recorder,
    )
    outcome = asyncio.run(guard.ask((NOTE_SUBSTANCE,), "Background chatter about lunch."))
    assert outcome.available and outcome.results[0].label == "filler"
    assert transport.calls == 1
    assert recorder.records[0].source == "synthetic_fixture"
    budget = LocalDailyModelBudget(
        tmp_path, task_type=ModelTaskType.TYPED_DECISION, daily_input_tokens=10_000_000
    )
    assert budget.reserved_today() == 1_000


def test_daily_limit_from_settings_denies_once_spent(tmp_path: Path) -> None:
    guard = build_synthetic_typed_decision_classifier(
        PersonalSettings(typed_decision_daily_input_tokens=1_500),
        data_directory=tmp_path,
        environ={"TYPESAFE_API_KEY": KEY},
        jev_transport=FillerTransport(),
    )
    first = asyncio.run(guard.ask((NOTE_SUBSTANCE,), "note one"))
    second = asyncio.run(guard.ask((NOTE_SUBSTANCE,), "note two"))
    assert first.available
    assert second.unavailable_reason is TypedDecisionUnavailableReason.BUDGET_DENIED


@pytest.mark.parametrize("key", ["bad key", "bad\nkey", "é"])
def test_malformed_key_disables_the_adapter_quietly(key: str, tmp_path: Path) -> None:
    settings = PersonalSettings(experimental_typed_decisions_enabled=True)
    transport = NeverTransport()
    assert _adapter_from_environment(settings, {"TYPESAFE_API_KEY": key}, transport) is None
    guard = build_runtime_typed_decision_classifier(
        settings,
        data_directory=tmp_path,
        environ={"TYPESAFE_API_KEY": key},
        jev_transport=transport,
    )
    assert guard is not None
    outcome = asyncio.run(guard.ask(FRONT_DOOR_AXES, "What did we decide last week?"))
    assert outcome.unavailable_reason is TypedDecisionUnavailableReason.DATA_ROUTE_BLOCKED
    assert transport.calls == 0


def test_adapter_from_environment_needs_a_well_formed_key() -> None:
    settings = PersonalSettings(experimental_typed_decisions_enabled=True)
    transport = NeverTransport()
    assert _adapter_from_environment(settings, {}, transport) is None
    assert _adapter_from_environment(settings, {"TYPESAFE_API_KEY": "  "}, transport) is None
    adapter = _adapter_from_environment(settings, {"TYPESAFE_API_KEY": KEY}, transport)
    assert isinstance(adapter, JevClassifier)
    assert adapter.model_id == settings.typed_decision_model_id


def test_builders_take_a_per_request_deadline_and_keep_the_hook_default(tmp_path: Path) -> None:
    settings = PersonalSettings(experimental_typed_decisions_enabled=True)
    hook = build_runtime_typed_decision_classifier(settings, data_directory=tmp_path, environ={})
    judge = build_runtime_typed_decision_classifier(
        settings, data_directory=tmp_path, environ={}, deadline_seconds=5.0
    )
    primer = build_synthetic_typed_decision_classifier(
        settings, data_directory=tmp_path, environ={}, deadline_seconds=5.0
    )
    assert hook is not None and judge is not None
    assert (hook._deadline, judge._deadline, primer._deadline) == (0.8, 5.0, 5.0)
    assert judge._source is TypedDecisionSource.RUNTIME
    with pytest.raises(ValueError, match="deadline"):
        build_runtime_typed_decision_classifier(
            settings, data_directory=tmp_path, environ={}, deadline_seconds=31.0
        )
