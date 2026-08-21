"""Local Ollama-backed episodic-extraction provider (loopback HTTP, no new deps)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from urllib import request as _request

_PROMPT = (
    "Extract at most {n} episodic memory candidates from the event summary below. "
    'Reply with ONLY JSON of the form {{"candidates":[{{"kind":"decision|failure|outcome|'
    'lesson|preference","claim":"...","confidence":0.0,"sensitivity":"normal"}}]}}. '
    "Emit an empty candidates list if nothing is worth remembering.\n\nEVENT:\n{summary}"
)


def _urllib_transport(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode()
    req = _request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with _request.urlopen(req, timeout=payload.pop("_timeout", 30.0)) as response:
        return json.loads(response.read().decode())  # type: ignore[no-any-return]


class OllamaEpisodicProvider:
    """RawEpisodicExtractionProvider that calls a loopback Ollama /api/generate."""

    def __init__(
        self,
        endpoint: str,
        model_id: str,
        *,
        timeout_seconds: float = 30.0,
        transport: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._model_id = model_id
        self._timeout = timeout_seconds
        self._transport = transport or _urllib_transport

    @property
    def provider_id(self) -> str:
        return "ollama"

    @property
    def model_id(self) -> str:
        return self._model_id

    def generate(self, request: object) -> object:
        summary = getattr(request, "summary", "")
        max_candidates = getattr(request, "max_candidates", 4)
        payload = {
            "model": self._model_id,
            "prompt": _PROMPT.format(n=max_candidates, summary=summary),
            "stream": False,
            "format": "json",
            "_timeout": self._timeout,
        }
        result = self._transport(f"{self._endpoint}/api/generate", payload)
        return json.loads(str(result.get("response", "")))
