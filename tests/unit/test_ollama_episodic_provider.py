import json
from collections.abc import Callable
from typing import Any

from mnemo_memory.connectors.ollama.episodic_provider import OllamaEpisodicProvider
from mnemo_memory.packages.model_gateway.episodic_extraction import parse_episodic_output


def _fake_transport(
    captured: dict[str, Any],
) -> Callable[[str, dict[str, Any]], dict[str, Any]]:
    def transport(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        captured["url"] = url
        captured["payload"] = payload
        candidates = {
            "candidates": [
                {"kind": "decision", "claim": "x", "confidence": 0.9, "sensitivity": "normal"}
            ]
        }
        return {"response": json.dumps(candidates)}

    return transport


def test_generate_returns_parseable_candidates() -> None:
    captured: dict[str, Any] = {}
    p = OllamaEpisodicProvider(
        "http://127.0.0.1:11434", "ministral-3:8b", transport=_fake_transport(captured)
    )

    class Req:
        summary = "did a thing"
        max_candidates = 4

    raw = p.generate(Req())
    assert parse_episodic_output(raw, 4)[0].claim == "x"
    assert captured["url"].endswith("/api/generate")
    assert captured["payload"]["model"] == "ministral-3:8b"
    assert p.provider_id == "ollama" and p.model_id == "ministral-3:8b"
