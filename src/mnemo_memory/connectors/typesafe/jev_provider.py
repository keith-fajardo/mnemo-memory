"""TypeSafe Jev typed-classification adapter (stdlib HTTP, no new dependency).

The adapter only builds and parses requests. Mnemo's guard owns data-route, secret, sensitivity,
budget and deadline policy. This module never logs or returns request text or the API key.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from typing import Any
from urllib import error as _error
from urllib import request as _request

from mnemo_memory.packages.domain import TypedDecisionUnavailableReason
from mnemo_memory.packages.model_gateway.cascade_router import (
    AxisKind,
    ClassifierAxis,
    ClassifierResult,
    logprob_from_probability,
)
from mnemo_memory.packages.model_gateway.typed_decisions import (
    AdapterAnswer,
    TypedDecisionAdapterError,
)

JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
JEV_DEFAULT_MODEL = "jev-1.13.0"
_MAXIMUM_RESPONSE_BYTES = 262_144

JevTransport = Callable[[str, bytes, Mapping[str, str], float], bytes]


def _urllib_transport(url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> bytes:
    request = _request.Request(url, data=body, headers=dict(headers), method="POST")
    with _request.urlopen(request, timeout=timeout) as response:
        payload: bytes = response.read(_MAXIMUM_RESPONSE_BYTES + 1)
    if len(payload) > _MAXIMUM_RESPONSE_BYTES:
        raise TypedDecisionAdapterError(TypedDecisionUnavailableReason.SCHEMA_INVALID)
    return payload


class JevClassifier:
    """Ask several closed questions about one bounded text in a single Jev request."""

    def __init__(
        self,
        api_key: str,
        *,
        model_id: str = JEV_DEFAULT_MODEL,
        endpoint: str = JEV_ENDPOINT,
        transport: JevTransport | None = None,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("MNEMO_TYPED_DECISION_CREDENTIAL_MISSING")
        self._api_key = api_key.strip()
        self._model_id = model_id
        self._endpoint = endpoint
        self._transport = transport or _urllib_transport

    def __repr__(self) -> str:
        return f"JevClassifier(model_id={self._model_id!r})"

    @property
    def provider_id(self) -> str:
        return "typesafe"

    @property
    def model_id(self) -> str:
        return self._model_id

    def answer(
        self, axes: tuple[ClassifierAxis, ...], text: str, *, timeout_seconds: float
    ) -> AdapterAnswer:
        body = json.dumps(
            {
                "model": self._model_id,
                "state": text,
                "questions": {axis.name: _question(axis) for axis in axes},
            },
            separators=(",", ":"),
        ).encode("utf-8")
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        try:
            raw = self._transport(self._endpoint, body, headers, timeout_seconds)
        except (TypedDecisionAdapterError, TimeoutError):
            raise
        except _error.HTTPError:
            raise TypedDecisionAdapterError(TypedDecisionUnavailableReason.HTTP_ERROR) from None
        except (_error.URLError, OSError) as failure:
            if isinstance(getattr(failure, "reason", None), TimeoutError):
                raise TimeoutError from None
            raise TypedDecisionAdapterError(TypedDecisionUnavailableReason.HTTP_ERROR) from None
        return _parse(axes, raw)


def _question(axis: ClassifierAxis) -> dict[str, object]:
    if axis.kind is AxisKind.YES_NO:
        return {"type": "noul", "instructions": axis.instructions}
    criteria = (
        dict(axis.criteria) if axis.criteria else {label: label for label in axis.allowed_labels}
    )
    return {"type": "choice", "instructions": axis.instructions, "criteria": criteria}


def _parse(axes: tuple[ClassifierAxis, ...], raw: bytes) -> AdapterAnswer:
    try:
        value: Any = json.loads(raw.decode("utf-8"))
        model = value["model"]
        answers = value["answers"]
        if (
            not isinstance(model, str)
            or not model.strip()
            or not isinstance(answers, dict)
            or set(answers) != {axis.name for axis in axes}
        ):
            raise ValueError("answers do not match the questions")
        results = tuple(_result(axis, answers[axis.name]) for axis in axes)
        usage = value.get("usage")
        tokens = usage.get("input_tokens", 0) if isinstance(usage, dict) else 0
        input_tokens = tokens if isinstance(tokens, int) and not isinstance(tokens, bool) else 0
    except (UnicodeDecodeError, ValueError, KeyError, TypeError, AttributeError):
        raise TypedDecisionAdapterError(TypedDecisionUnavailableReason.SCHEMA_INVALID) from None
    return AdapterAnswer(results, model, max(0, input_tokens))


def _result(axis: ClassifierAxis, answer: object) -> ClassifierResult:
    if not isinstance(answer, dict):
        raise ValueError("answer is not an object")
    if axis.kind is AxisKind.YES_NO:
        if answer.get("type") != "noul":
            raise ValueError("expected a noul answer")
        yes = _probability(answer.get("noul"))
        label = "yes" if yes >= 0.5 else "no"
        chosen = yes if label == "yes" else 1.0 - yes
        return ClassifierResult(
            axis.name,
            label,
            logprob_from_probability(chosen),
            axis.escalation_score_for({"yes": yes, "no": 1.0 - yes}),
        )
    if answer.get("type") != "choice":
        raise ValueError("expected a choice answer")
    choice = answer.get("choice")
    probabilities = answer.get("probabilities")
    if choice not in axis.allowed_labels or not isinstance(probabilities, dict):
        raise ValueError("choice is outside the allowed labels")
    if set(probabilities) - set(axis.allowed_labels):
        raise ValueError("probabilities name unknown labels")
    by_label = {name: _probability(probabilities.get(name, 0.0)) for name in axis.allowed_labels}
    return ClassifierResult(
        axis.name,
        str(choice),
        logprob_from_probability(by_label[str(choice)]),
        axis.escalation_score_for(by_label),
        confidence=_probability(answer.get("confidence")),
    )


def _probability(value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0.0 <= value <= 1.0
    ):
        raise ValueError("probability is invalid")
    return float(value)
