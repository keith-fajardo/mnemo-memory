import http.client
import json
import time
from collections.abc import Mapping
from email.message import Message
from typing import Any
from urllib import error as urllib_error

import pytest

from mnemo_memory.connectors.typesafe import (
    JEV_DEFAULT_MODEL,
    JEV_ENDPOINT,
    JevClassifier,
    JevTransport,
)
from mnemo_memory.packages.domain import TypedDecisionUnavailableReason
from mnemo_memory.packages.model_gateway.cascade_router import (
    YES_NO_LABELS,
    AxisKind,
    ClassifierAxis,
)
from mnemo_memory.packages.model_gateway.decision_axes import COMPLEXITY, TOOL_NEED
from mnemo_memory.packages.model_gateway.typed_decisions import TypedDecisionAdapterError

KEY = "test-key-not-real-0000"
# Local axes match the recorded smoke response, so connector tests do not depend on the catalogue.
NEEDS_LONG_TERM = ClassifierAxis(
    "needs_long_term",
    "Answering needs stored memory from earlier sessions (past decisions, notes, history) "
    "that is not in the current message",
    YES_NO_LABELS,
    0.0,
    kind=AxisKind.YES_NO,
)
NEEDS_STRUCTURE = ClassifierAxis(
    "needs_structure",
    "Answering needs knowledge of source code or database structure "
    "(files, symbols, migrations, lineage)",
    YES_NO_LABELS,
    0.0,
    kind=AxisKind.YES_NO,
)
FRONT_DOOR_AXES = (NEEDS_LONG_TERM, NEEDS_STRUCTURE, COMPLEXITY, TOOL_NEED)
# Recorded 2026-09-29 from jev-1.13.0 for a synthetic prompt (docs/superpowers/specs §4.2).
SMOKE_RESPONSE = {
    "model": "jev-1.13.0",
    "answers": {
        "needs_long_term": {"type": "noul", "noul": 0.93},
        "needs_structure": {"type": "noul", "noul": 0.61},
        "complexity": {
            "type": "choice",
            "choice": "heavy",
            "confidence": 0.6,
            "probabilities": {"heavy": 0.8, "light": 0.2},
        },
        "tool_need": {
            "type": "choice",
            "choice": "read_heavy",
            "confidence": 0.73,
            "probabilities": {"none": 0.18, "read_heavy": 0.82, "edit": 0.0},
        },
    },
    "usage": {"input_tokens": 485, "output_tokens": 108},
}


class RecordingTransport:
    def __init__(self, response: object = SMOKE_RESPONSE, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[tuple[str, dict[str, object], dict[str, str], float]] = []

    def __call__(self, url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> bytes:
        self.calls.append((url, json.loads(body), dict(headers), timeout))
        if self.error is not None:
            raise self.error
        if isinstance(self.response, bytes):
            return self.response
        return json.dumps(self.response).encode()


def test_request_uses_pinned_model_bearer_key_and_typed_questions() -> None:
    transport = RecordingTransport()
    JevClassifier(KEY, transport=transport).answer(FRONT_DOOR_AXES, "prompt", timeout_seconds=0.6)
    url, body, headers, timeout = transport.calls[0]
    assert (url, timeout) == (JEV_ENDPOINT, 0.6)
    assert headers == {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
    assert body["model"] == JEV_DEFAULT_MODEL == "jev-1.13.0"
    assert body["state"] == "prompt"
    questions = body["questions"]
    assert isinstance(questions, dict)
    assert questions["needs_long_term"]["type"] == "noul"
    assert questions["complexity"] == {
        "type": "choice",
        "instructions": "How hard is this task for an AI coding assistant",
        "criteria": {
            "light": "Simple lookup, rename, formatting or summarizing",
            "heavy": "Needs reasoning across several facts, design judgement, or risky changes",
        },
    }


def test_recorded_smoke_response_parses_into_axis_results() -> None:
    answer = JevClassifier(KEY, transport=RecordingTransport()).answer(
        FRONT_DOOR_AXES, "prompt", timeout_seconds=0.6
    )
    by_axis = {result.axis_name: result for result in answer.results}
    assert (answer.model_version, answer.input_tokens) == ("jev-1.13.0", 485)
    assert (by_axis["needs_long_term"].label, by_axis["needs_long_term"].escalation_score) == (
        "yes",
        0.93,
    )
    assert by_axis["needs_structure"].escalation_score == pytest.approx(0.61)
    assert by_axis["complexity"].label == "heavy"
    assert by_axis["complexity"].escalation_score == pytest.approx(0.8)
    assert by_axis["complexity"].confidence == 0.6
    assert by_axis["tool_need"].escalation_score == pytest.approx(0.205)
    assert by_axis["tool_need"].confidence == 0.73


def test_extreme_probabilities_stay_finite() -> None:
    response = json.loads(json.dumps(SMOKE_RESPONSE))
    response["answers"]["needs_long_term"]["noul"] = 0.0
    response["answers"]["needs_structure"]["noul"] = 1.0
    answer = JevClassifier(KEY, transport=RecordingTransport(response)).answer(
        FRONT_DOOR_AXES, "prompt", timeout_seconds=0.6
    )
    assert answer.results[0].label == "no" and answer.results[0].label_logprob == 0.0
    assert answer.results[1].label == "yes" and answer.results[1].label_logprob == 0.0


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r["answers"].pop("tool_need"),
        lambda r: r["answers"]["tool_need"]["probabilities"].update({"delete": 0.1}),
        lambda r: r["answers"]["complexity"].update({"choice": "medium"}),
        lambda r: r["answers"]["needs_long_term"].update({"noul": 1.5}),
        lambda r: r["answers"]["needs_long_term"].update({"type": "choice"}),
        lambda r: r.update({"model": ""}),
        lambda r: r["answers"]["complexity"].update({"probabilities": {}}),
        lambda r: r["answers"]["complexity"]["probabilities"].pop("light"),
        lambda r: r["answers"]["tool_need"].update(
            {"choice": "edit", "probabilities": {"none": 1.0, "read_heavy": 0.0, "edit": 0.0}}
        ),
    ],
)
def test_malformed_answers_are_schema_invalid(mutate: object) -> None:
    response = json.loads(json.dumps(SMOKE_RESPONSE))
    assert callable(mutate)
    mutate(response)
    with pytest.raises(TypedDecisionAdapterError) as caught:
        JevClassifier(KEY, transport=RecordingTransport(response)).answer(
            FRONT_DOOR_AXES, "prompt", timeout_seconds=0.6
        )
    assert caught.value.reason is TypedDecisionUnavailableReason.SCHEMA_INVALID


def test_non_json_body_is_schema_invalid() -> None:
    with pytest.raises(TypedDecisionAdapterError) as caught:
        JevClassifier(KEY, transport=RecordingTransport(b"<html>")).answer(
            FRONT_DOOR_AXES, "prompt", timeout_seconds=0.6
        )
    assert caught.value.reason is TypedDecisionUnavailableReason.SCHEMA_INVALID


def test_http_and_network_failures_map_to_closed_reasons() -> None:
    overloaded = urllib_error.HTTPError(JEV_ENDPOINT, 529, "overloaded", Message(), None)
    with pytest.raises(TypedDecisionAdapterError) as caught:
        JevClassifier(KEY, transport=RecordingTransport(error=overloaded)).answer(
            FRONT_DOOR_AXES, "prompt", timeout_seconds=0.6
        )
    assert caught.value.reason is TypedDecisionUnavailableReason.HTTP_ERROR
    slow = urllib_error.URLError(TimeoutError("timed out"))
    with pytest.raises(TimeoutError):
        JevClassifier(KEY, transport=RecordingTransport(error=slow)).answer(
            FRONT_DOOR_AXES, "prompt", timeout_seconds=0.6
        )


def test_api_key_never_appears_in_repr_or_errors() -> None:
    classifier = JevClassifier(KEY, transport=RecordingTransport(b"not json"))
    assert KEY not in repr(classifier)
    with pytest.raises(TypedDecisionAdapterError) as caught:
        classifier.answer(FRONT_DOOR_AXES, "prompt", timeout_seconds=0.6)
    assert KEY not in str(caught.value) and caught.value.__cause__ is None
    with pytest.raises(ValueError) as missing:
        JevClassifier("   ")
    assert str(missing.value) == "MNEMO_TYPED_DECISION_CREDENTIAL_MISSING"


def test_redirects_are_never_followed() -> None:
    from urllib import request as urllib_request

    from mnemo_memory.connectors.typesafe import jev_provider

    req = urllib_request.Request(JEV_ENDPOINT, data=b"{}", method="POST")
    handler: Any = jev_provider._NoRedirect()
    redirect = handler.redirect_request(req, None, 302, "Found", Message(), "http://evil.example/")
    assert redirect is None
    opener: Any = jev_provider._OPENER
    handlers = opener.handlers
    assert not any(type(h) is urllib_request.HTTPRedirectHandler for h in handlers)


class DripResponse:
    """A fake HTTP response that hands out its body slowly, like a trickling server."""

    def __init__(self, body: bytes, *, chunk: int = 1, pause: float = 0.0) -> None:
        self._body = body
        self._chunk = chunk
        self._pause = pause

    def __enter__(self) -> "DripResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read1(self, size: int = -1) -> bytes:
        time.sleep(self._pause)
        piece, self._body = self._body[: self._chunk], self._body[self._chunk :]
        return piece

    def read(self, size: int = -1) -> bytes:
        data = b""
        while size < 0 or len(data) < size:
            piece = self.read1()
            if not piece:
                break
            data += piece
        return data


def _serve(monkeypatch: pytest.MonkeyPatch, response: DripResponse) -> None:
    from mnemo_memory.connectors.typesafe import jev_provider

    monkeypatch.setattr(jev_provider._OPENER, "open", lambda request, timeout: response)


def test_transport_enforces_a_total_deadline_on_a_trickling_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mnemo_memory.connectors.typesafe import jev_provider

    _serve(monkeypatch, DripResponse(b"x" * 50, pause=0.03))
    started = time.monotonic()
    with pytest.raises(TimeoutError) as caught:
        jev_provider._urllib_transport(JEV_ENDPOINT, b"{}", {}, 0.1)
    assert time.monotonic() - started < 0.5
    assert caught.value.args == ()


def test_transport_keeps_the_response_size_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    from mnemo_memory.connectors.typesafe import jev_provider

    cap = 262_144
    _serve(monkeypatch, DripResponse(b"x" * cap, chunk=65_536))
    assert len(jev_provider._urllib_transport(JEV_ENDPOINT, b"{}", {}, 5.0)) == cap
    _serve(monkeypatch, DripResponse(b"x" * (cap + 1), chunk=65_536))
    with pytest.raises(TypedDecisionAdapterError) as caught:
        jev_provider._urllib_transport(JEV_ENDPOINT, b"{}", {}, 5.0)
    assert caught.value.reason is TypedDecisionUnavailableReason.SCHEMA_INVALID


def _answer_with_model(reported: str, *, model_id: str = JEV_DEFAULT_MODEL) -> str:
    response = json.loads(json.dumps(SMOKE_RESPONSE))
    response["model"] = reported
    classifier = JevClassifier(KEY, model_id=model_id, transport=RecordingTransport(response))
    return classifier.answer(FRONT_DOOR_AXES, "prompt", timeout_seconds=0.6).model_version


def test_pinned_model_accepts_the_recorded_smoke_response() -> None:
    assert _answer_with_model("jev-1.13.0") == JEV_DEFAULT_MODEL == "jev-1.13.0"


def test_pinned_model_rejects_a_different_reported_version() -> None:
    with pytest.raises(TypedDecisionAdapterError) as caught:
        _answer_with_model("jev-1.14.0")
    assert caught.value.reason is TypedDecisionUnavailableReason.SCHEMA_INVALID


def test_unpinned_model_id_accepts_any_well_formed_reported_version() -> None:
    assert _answer_with_model("jev-1.13.0", model_id="jev-latest") == "jev-1.13.0"
    assert _answer_with_model("jev:2/beta_1-x", model_id="jev-latest") == "jev:2/beta_1-x"


@pytest.mark.parametrize("model_id", [JEV_DEFAULT_MODEL, "jev-latest"])
@pytest.mark.parametrize(
    "reported",
    ["jev 1.13.0", "jev-1.13.0\n", "jev-1.13.0\x00", "\tjev", "-jev", "j\u00e9v", "j" * 129],
)
def test_malformed_reported_model_is_schema_invalid(model_id: str, reported: str) -> None:
    with pytest.raises(TypedDecisionAdapterError) as caught:
        _answer_with_model(reported, model_id=model_id)
    assert caught.value.reason is TypedDecisionUnavailableReason.SCHEMA_INVALID


@pytest.mark.parametrize("key", ["bad\nkey", "bad\u201ckey", "bad key", "\u00e9"])
def test_malformed_keys_are_rejected_without_echo(key: str) -> None:
    with pytest.raises(ValueError) as caught:
        JevClassifier(key)
    assert key not in str(caught.value)
    assert str(caught.value).startswith("MNEMO_TYPED_DECISION_CREDENTIAL_")


def _raising(error: Exception) -> JevTransport:
    """A transport that, like the real one, holds the headers (and so the key) in its frame."""

    def transport(url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> bytes:
        raise error

    return transport


def _frame_locals(error: BaseException) -> str:
    """Every local in every frame of the error's traceback, as a crash reporter would show it."""

    shown: list[str] = []
    trace = error.__traceback__
    while trace is not None:
        shown += [f"{name}={value!r}" for name, value in trace.tb_frame.f_locals.items()]
        trace = trace.tb_next
    return "\n".join(shown)


Reason = TypedDecisionUnavailableReason
_TRANSPORT_FAILURES: list[tuple[JevTransport, type[Exception], Reason | None]] = [
    (
        _raising(urllib_error.HTTPError(JEV_ENDPOINT, 529, "overloaded", Message(), None)),
        TypedDecisionAdapterError,
        Reason.HTTP_ERROR,
    ),
    (_raising(urllib_error.URLError(TimeoutError("timed out"))), TimeoutError, None),
    (_raising(urllib_error.URLError("refused")), TypedDecisionAdapterError, Reason.HTTP_ERROR),
    (_raising(OSError("connection reset")), TypedDecisionAdapterError, Reason.HTTP_ERROR),
    (_raising(TimeoutError()), TimeoutError, None),
    (
        _raising(http.client.IncompleteRead(b"partial")),
        TypedDecisionAdapterError,
        Reason.HTTP_ERROR,
    ),
    (_raising(RuntimeError("odd")), TypedDecisionAdapterError, Reason.HTTP_ERROR),
    (
        _raising(TypedDecisionAdapterError(Reason.SCHEMA_INVALID)),
        TypedDecisionAdapterError,
        Reason.SCHEMA_INVALID,
    ),
    (RecordingTransport(b"not json"), TypedDecisionAdapterError, Reason.SCHEMA_INVALID),
]


@pytest.mark.parametrize(
    ("transport", "raised", "reason"),
    _TRANSPORT_FAILURES,
    ids=[
        "http_status",
        "url_timeout",
        "url_refused",
        "os_error",
        "timeout",
        "incomplete_read",
        "runtime_error",
        "adapter_error",
        "not_json",
    ],
)
def test_adapter_errors_carry_no_chain_and_no_key_in_any_frame(
    transport: JevTransport, raised: type[Exception], reason: Reason | None
) -> None:
    classifier = JevClassifier(KEY, transport=transport)
    with pytest.raises(raised) as caught:
        classifier.answer(FRONT_DOOR_AXES, "prompt", timeout_seconds=0.6)
    error = caught.value
    assert type(error) is raised
    assert getattr(error, "reason", None) is reason
    assert error.__cause__ is None
    assert error.__context__ is None
    assert KEY not in str(error) and KEY not in repr(error)
    assert KEY not in _frame_locals(error)


def test_stored_key_is_masked_in_the_classifier_state() -> None:
    classifier = JevClassifier(KEY, transport=RecordingTransport())
    assert KEY not in repr(vars(classifier))
    assert repr(classifier._api_key) == str(classifier._api_key) == "***"
    assert classifier._api_key.reveal() == KEY
