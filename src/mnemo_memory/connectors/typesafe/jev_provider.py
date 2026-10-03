"""TypeSafe Jev typed-classification adapter (stdlib HTTP, no new dependency).

The adapter only builds and parses requests. Mnemo's guard owns data-route, secret, sensitivity,
budget and deadline policy. This module never logs or returns request text or the API key.
"""

from __future__ import annotations

import errno
import http.client
import json
import math
import queue
import re
import socket
import sys
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any, NoReturn
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
_READ_CHUNK_BYTES = 65_536
# A configured "jev-X.Y.Z" pin must come back exactly; any other id (for example "jev-latest")
# only needs a well-formed reported model name.
_PINNED_MODEL = re.compile(r"jev-\d+\.\d+\.\d+", re.ASCII)
_REPORTED_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")
# A new TCP connect that has not finished by now has probably lost its first SYN and is waiting
# out the OS's 1 s retransmit, so a second attempt starts.
HEDGE_SECONDS = 0.2

JevTransport = Callable[[str, bytes, Mapping[str, str], float], bytes]


def _hedged_connection(
    address: tuple[str, int],
    *,
    timeout: float,
    hedge_seconds: float = HEDGE_SECONDS,
    connect: Callable[..., socket.socket] = socket.create_connection,
) -> socket.socket:
    """Open one TCP connection, starting a second identical attempt if the first is slow.

    The second attempt starts once the first has not connected within ``hedge_seconds`` (or has
    already failed); the first attempt keeps running. The first socket to connect is returned
    and any socket that connects later is closed. The whole call is bounded by ``timeout``:
    past it, ``TimeoutError``. If both attempts fail, the last failure is raised. Attempts run on
    daemon threads, so an abandoned one never blocks process exit.
    """

    deadline = time.monotonic() + timeout
    outcomes: queue.SimpleQueue[socket.socket | Exception] = queue.SimpleQueue()
    settled = threading.Event()  # set once the caller has a winner or has given up
    settle_lock = threading.Lock()

    def attempt() -> None:
        try:
            sock = connect(address, timeout=timeout)
        except Exception as failure:
            outcomes.put(failure)
            return
        with settle_lock:
            if not settled.is_set():
                outcomes.put(sock)
                return
        sock.close()  # a late loser: nobody will read it

    def launch() -> None:
        threading.Thread(target=attempt, name="jev-connect", daemon=True).start()

    launch()
    attempts, failures = 1, 0
    hedge_at = time.monotonic() + hedge_seconds
    last_failure: Exception | None = None
    winner: socket.socket | None = None
    try:
        while True:
            now = time.monotonic()
            if now >= deadline:
                break
            if attempts == 1 and now >= hedge_at:
                launch()
                attempts = 2
            wake = deadline if attempts == 2 else min(hedge_at, deadline)
            try:
                outcome = outcomes.get(timeout=wake - now)
            except queue.Empty:
                continue
            if isinstance(outcome, Exception):
                failures += 1
                last_failure = outcome
                if attempts == 1:
                    launch()
                    attempts = 2
                elif failures == attempts:
                    break
                continue
            winner = outcome
            break
    finally:
        # Close every socket that connected but was not returned. An attempt that finishes
        # after this point sees ``settled`` and closes its own socket.
        with settle_lock:
            settled.set()
        while True:
            try:
                leftover = outcomes.get_nowait()
            except queue.Empty:
                break
            if not isinstance(leftover, Exception):
                leftover.close()
    if winner is not None:
        return winner
    if last_failure is not None and failures == attempts:
        raise last_failure
    raise TimeoutError


class _HedgedHTTPSConnection(http.client.HTTPSConnection):
    """``HTTPSConnection`` whose direct TCP connect is hedged; a proxy tunnel is left alone."""

    def connect(self) -> None:
        # ``_tunnel_host`` and ``_context`` are CPython private attributes that typeshed does not
        # declare. A tunnel, a missing attribute or a non-numeric timeout takes the stock path,
        # so a configured proxy is never bypassed (a missing ``_tunnel_host`` counts as a tunnel).
        tunneled = bool(getattr(self, "_tunnel_host", True))
        context = getattr(self, "_context", None)
        timeout = self.timeout
        if tunneled or context is None or not isinstance(timeout, int | float):
            super().connect()
            return
        # CPython 3.12 ``HTTPConnection.connect`` then ``HTTPSConnection.connect``, with only
        # ``socket.create_connection`` replaced by the hedged connect.
        sys.audit("http.client.connect", self, self.host, self.port)
        self.sock = _hedged_connection((self.host, self.port or 443), timeout=timeout)
        try:
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError as failure:
            if failure.errno != errno.ENOPROTOOPT:
                raise
        self.sock = context.wrap_socket(self.sock, server_hostname=self.host)


class _HedgedHTTPSHandler(_request.HTTPSHandler):
    """The stock HTTPS handler, opening its connections with ``_HedgedHTTPSConnection``."""

    def https_open(self, req: _request.Request) -> http.client.HTTPResponse:
        # ``_context`` is the handler's TLS context (CPython private, absent from typeshed). A
        # ``None`` fallback makes the connection build the same default context.
        return self.do_open(_HedgedHTTPSConnection, req, context=getattr(self, "_context", None))


class _NoRedirect(_request.HTTPRedirectHandler):
    """Never follow a redirect: it would forward the Authorization header."""

    def redirect_request(
        self,
        req: _request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


_OPENER: _request.OpenerDirector | None = None
_OPENER_LOCK = threading.Lock()


def _opener() -> _request.OpenerDirector:
    """The shared no-redirect, hedged-connect opener, built on first use rather than at import.

    Building it creates the default TLS context (about 17 ms). The first transport call builds
    it on the guard's worker thread, so the cost falls inside the request cap instead of before
    the cap starts. The lock keeps concurrent first calls to one build.
    """

    global _OPENER
    with _OPENER_LOCK:
        if _OPENER is None:
            _OPENER = _request.build_opener(_NoRedirect(), _HedgedHTTPSHandler())
        return _OPENER


def _urllib_transport(url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> bytes:
    """POST once; ``timeout`` is a total deadline for the body read only.

    DNS and the TCP connect are hedged and bounded together by the same ``timeout`` (see
    ``_hedged_connection``); the TLS handshake and each header read are limited only per
    operation. Reading a trickling body stops at the deadline plus at most one socket read.
    """

    deadline = time.monotonic() + timeout
    request = _request.Request(url, data=body, headers=dict(headers), method="POST")
    chunks: list[bytes] = []
    size = 0
    with _opener().open(request, timeout=timeout) as response:
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError
            chunk: bytes = response.read1(_READ_CHUNK_BYTES)
            if not chunk:
                break
            size += len(chunk)
            if size > _MAXIMUM_RESPONSE_BYTES:
                raise TypedDecisionAdapterError(TypedDecisionUnavailableReason.SCHEMA_INVALID)
            chunks.append(chunk)
    return b"".join(chunks)


class _Secret:
    """Hold the API key so ``repr``, ``str``, ``vars`` and pickle never show it."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "***"

    def __str__(self) -> str:
        return "***"

    def __reduce__(self) -> NoReturn:
        raise TypeError("the TypeSafe API key cannot be pickled")


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
        api_key = api_key.strip()
        if not all(33 <= ord(ch) <= 126 for ch in api_key):
            raise ValueError("MNEMO_TYPED_DECISION_CREDENTIAL_INVALID")
        self._api_key = _Secret(api_key)
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
        # Errors are raised after the ``except`` blocks, never inside them, so they carry no
        # ``__context__`` or ``__cause__``: the transport's traceback (whose frames hold the
        # Authorization header) never travels with them. The header is built inline so no
        # local in this frame holds the key either.
        raw: bytes = b""
        timed_out = False
        failure_reason: TypedDecisionUnavailableReason | None = None
        try:
            raw = self._transport(
                self._endpoint,
                body,
                {
                    "Authorization": f"Bearer {self._api_key.reveal()}",
                    "Content-Type": "application/json",
                },
                timeout_seconds,
            )
        except TypedDecisionAdapterError as failure:
            failure_reason = _closed_reason(failure.reason)
        except TimeoutError:
            timed_out = True
        except _error.HTTPError:
            failure_reason = TypedDecisionUnavailableReason.HTTP_ERROR
        except OSError as failure:  # URLError and socket errors; a wrapped timeout stays one
            if isinstance(getattr(failure, "reason", None), TimeoutError):
                timed_out = True
            else:
                failure_reason = TypedDecisionUnavailableReason.HTTP_ERROR
        except Exception:  # e.g. http.client.IncompleteRead, which is not an OSError
            failure_reason = TypedDecisionUnavailableReason.HTTP_ERROR
        if timed_out:
            raise TimeoutError
        if failure_reason is not None:
            raise TypedDecisionAdapterError(failure_reason)
        return _parse(axes, raw, self._model_id)


def _question(axis: ClassifierAxis) -> dict[str, object]:
    if axis.kind is AxisKind.YES_NO:
        return {"type": "noul", "instructions": axis.instructions}
    criteria = (
        dict(axis.criteria) if axis.criteria else {label: label for label in axis.allowed_labels}
    )
    return {"type": "choice", "instructions": axis.instructions, "criteria": criteria}


def _closed_reason(reason: object) -> TypedDecisionUnavailableReason:
    if isinstance(reason, TypedDecisionUnavailableReason):
        return reason
    return TypedDecisionUnavailableReason.HTTP_ERROR


def _parse(axes: tuple[ClassifierAxis, ...], raw: bytes, configured_model: str) -> AdapterAnswer:
    """Parse a response, or raise ``schema_invalid`` with no chained parser error."""

    try:
        answer: AdapterAnswer | None = _parse_answer(axes, raw, configured_model)
    except (
        UnicodeDecodeError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        RecursionError,  # deeply nested JSON
        OverflowError,  # an integer probability too large for a float
    ):
        answer = None
    if answer is None:
        raise TypedDecisionAdapterError(TypedDecisionUnavailableReason.SCHEMA_INVALID)
    return answer


def _parse_answer(
    axes: tuple[ClassifierAxis, ...], raw: bytes, configured_model: str
) -> AdapterAnswer:
    value: Any = json.loads(raw.decode("utf-8"))
    model = value["model"]
    answers = value["answers"]
    if (
        not _reported_model_valid(configured_model, model)
        or not isinstance(answers, dict)
        or set(answers) != {axis.name for axis in axes}
    ):
        raise ValueError("answers do not match the questions")
    results = tuple(_result(axis, answers[axis.name]) for axis in axes)
    usage = value.get("usage")
    tokens = usage.get("input_tokens", 0) if isinstance(usage, dict) else 0
    input_tokens = tokens if isinstance(tokens, int) and not isinstance(tokens, bool) else 0
    return AdapterAnswer(results, model, max(0, input_tokens))


def _reported_model_valid(configured: str, reported: object) -> bool:
    if not isinstance(reported, str):
        return False
    if _PINNED_MODEL.fullmatch(configured):
        return reported == configured
    return _REPORTED_MODEL.fullmatch(reported) is not None


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
    if set(probabilities) != set(axis.allowed_labels):
        raise ValueError("probabilities do not match the allowed labels")
    by_label = {name: _probability(probabilities[name]) for name in axis.allowed_labels}
    if by_label[str(choice)] < max(by_label.values()) - 1e-6:
        raise ValueError("choice is not the most probable label")
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
