# tests/evals/test_run_typed_decision_evaluation.py
"""End-to-end CLI checks with an oracle transport; no network, no real key."""

from __future__ import annotations

import json
import urllib.error
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from scripts.run_typed_decision_evaluation import main

FIXTURES = Path(__file__).parents[1] / "fixtures/evals"
KEY = "test-key-not-real-0000"
ROUTING = json.loads((FIXTURES / "automatic-context-routing-v1.json").read_text("utf-8"))
TYPED = json.loads((FIXTURES / "typed-decision-v1.json").read_text("utf-8"))
VIABILITY = json.loads((FIXTURES / "viability-corpus-v1.json").read_text("utf-8"))
HOLDOUT = json.loads((FIXTURES / "typed-decision-holdout-v1.json").read_text("utf-8"))
EXPECTED_ROUTE = {
    case["prompt"]: case["expected_route"]
    for case in (*ROUTING["cases"], *HOLDOUT["front_door_cases"])
}
MEMORY_LABEL = {
    "prior_memory": "past_sessions",
    "knowledge": "project_docs",
    "structure": "code_structure",
    "none": "nothing",
}
MEMORY_LABELS = ("past_sessions", "project_docs", "code_structure", "code_and_history", "nothing")
HOLDOUT_NOISE = {note["summary"] for note in HOLDOUT["notes"] if note["category"] == "noise"}
TIER = {
    case["prompt"]: (case["expected_tier"], case["expected_tool_need"])
    for case in TYPED["tier_cases"]
}
NEGATIVES = {item["summary"] for item in TYPED["worth_negatives"]}
KIND_BY_SUMMARY: dict[str, str | None] = {}
for _template in VIABILITY["templates"]:
    for _event in _template["events"]:
        _prefix, _, _body = _event["summary"].partition(": ")
        KIND_BY_SUMMARY[_body] = {
            "decision": "decision",
            "failure": "failure",
            "result": "outcome",
        }.get(_prefix)


def _noul(probability: float) -> dict[str, object]:
    return {"type": "noul", "noul": probability}


def _choice(labels: Sequence[str], chosen: str, confidence: float) -> dict[str, object]:
    others = [label for label in labels if label != chosen]
    probabilities = {label: 0.1 / len(others) for label in others}
    probabilities[chosen] = 0.9
    return {
        "type": "choice",
        "choice": chosen,
        "confidence": confidence,
        "probabilities": probabilities,
    }


class OracleTransport:
    """Answers from fixture labels so every gate should pass."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> bytes:
        self.calls += 1
        request = json.loads(body)
        state = request["state"]
        route = EXPECTED_ROUTE.get(state)
        tier, tool = TIER.get(state, ("heavy", "read_heavy"))
        answers: dict[str, dict[str, object]] = {}
        for name in request["questions"]:
            if name == "memory_need":
                answers[name] = _choice(MEMORY_LABELS, MEMORY_LABEL[route or "none"], 0.8)
            elif name == "complexity":
                answers[name] = _choice(("light", "heavy"), tier, 0.8)
            elif name == "tool_need":
                answers[name] = _choice(("none", "read_heavy", "edit"), tool, 0.8)
            elif name == "note_substance":
                noise = "Background conversation" in state or state in HOLDOUT_NOISE
                answers[name] = _choice(
                    ("task_information", "filler"), "filler" if noise else "task_information", 0.9
                )
            elif name == "worth_remembering":
                answers[name] = _noul(0.05 if state in NEGATIVES else 0.9)
            elif name == "episodic_kind":
                kind = KIND_BY_SUMMARY.get(state)
                labels = ("decision", "failure", "outcome", "lesson", "preference")
                answers[name] = _choice(labels, kind or "decision", 0.8 if kind else 0.3)
            else:
                raise AssertionError(f"unexpected question {name}")
        return json.dumps(
            {"model": "jev-1.13.0", "answers": answers, "usage": {"input_tokens": 100}}
        ).encode()


def _empty_ollama(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"response": json.dumps({"candidates": []})}


def _run(
    tmp_path: Path,
    *extra: str,
    transport: Callable[[str, bytes, Mapping[str, str], float], bytes],
    env: dict[str, str] | None = None,
    ollama: Callable[[str, dict[str, Any]], dict[str, Any]] = _empty_ollama,
) -> int:
    return main(
        ["--run-id", "test-run", "--results-root", str(tmp_path), *extra],
        environ={"TYPESAFE_API_KEY": KEY} if env is None else env,
        jev_transport=transport,
        ollama_transport=ollama,
    )


def test_cli_refuses_without_live_authorization_or_credential(tmp_path: Path) -> None:
    transport = OracleTransport()
    assert _run(tmp_path, transport=transport) == 2
    assert _run(tmp_path, "--live-calls-authorized", transport=transport, env={}) == 2
    assert transport.calls == 0
    assert not (tmp_path / "test-run").exists()


def test_cli_passes_every_gate_with_an_oracle_and_ollama_baseline(tmp_path: Path) -> None:
    transport = OracleTransport()
    code = _run(tmp_path, "--live-calls-authorized", "--ollama-model", "fake", transport=transport)
    report = json.loads((tmp_path / "test-run" / "report.json").read_text("utf-8"))
    assert code == 0 and report["phase_1_complete"] is True
    assert report["run"]["model_versions"] == ["jev-1.13.0"]
    assert report["run"]["pinned_model_only"] is True
    assert report["excluded_fixtures"] == {
        "telehealth-long-horizon-phase2-qwen25coder7b.json": "no synthetic provenance declared"
    }
    assert report["latency"]["samples"] == 60
    assert report["front_door_holdout"]["cases"] == 40
    assert report["relevance_holdout"]["candidates"] == 24
    assert report["run"]["total_requests"] == 302


def test_cli_is_incomplete_without_the_ollama_baseline(tmp_path: Path) -> None:
    code = _run(tmp_path, "--live-calls-authorized", transport=OracleTransport())
    report = json.loads((tmp_path / "test-run" / "report.json").read_text("utf-8"))
    assert code == 1
    assert report["extraction"]["gates"] == {"baseline": "not_evaluated"}


def test_cli_report_is_content_free_and_key_free(tmp_path: Path) -> None:
    _run(tmp_path, "--live-calls-authorized", "--ollama-model", "fake", transport=OracleTransport())
    text = (tmp_path / "test-run" / "report.json").read_text("utf-8")
    assert KEY not in text
    assert ROUTING["cases"][0]["prompt"] not in text
    assert TYPED["tier_cases"][0]["prompt"] not in text
    assert HOLDOUT["front_door_cases"][0]["prompt"] not in text
    assert all(note["summary"] not in text for note in HOLDOUT["notes"])


def test_cli_refuses_to_overwrite_an_existing_report(tmp_path: Path) -> None:
    (tmp_path / "test-run").mkdir()
    (tmp_path / "test-run" / "report.json").write_text("{}", encoding="utf-8")
    transport = OracleTransport()
    assert _run(tmp_path, "--live-calls-authorized", transport=transport) == 2
    assert transport.calls == 0


def _report(tmp_path: Path) -> dict[str, Any]:
    report: dict[str, Any] = json.loads((tmp_path / "test-run" / "report.json").read_text("utf-8"))
    return report


class FrontDoorOnlyTransport(OracleTransport):
    """Answers front-door questions and fails every other request."""

    def __call__(self, url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> bytes:
        if "memory_need" not in json.loads(body)["questions"]:
            self.calls += 1
            raise urllib.error.URLError("down")
        return super().__call__(url, body, headers, timeout)


def test_cli_is_incomplete_when_only_the_front_door_answers(tmp_path: Path) -> None:
    code = _run(
        tmp_path,
        "--live-calls-authorized",
        "--ollama-model",
        "fake",
        transport=FrontDoorOnlyTransport(),
    )
    report = _report(tmp_path)
    assert code == 1 and report["phase_1_complete"] is False
    assert report["relevance"]["gates"]["answered"] is False
    assert report["relevance_holdout"]["gates"]["answered"] is False
    assert report["front_door_holdout"]["gates"]["accuracy"] is True
    assert report["tier"]["gates"]["answered"] is False
    assert report["extraction"]["gates"]["jev_answered"] is False


def test_cli_is_incomplete_when_the_call_cap_starves_the_run(tmp_path: Path) -> None:
    code = _run(
        tmp_path,
        "--live-calls-authorized",
        "--ollama-model",
        "fake",
        "--max-calls",
        "129",
        transport=OracleTransport(),
    )
    report = _report(tmp_path)
    assert code == 1 and report["phase_1_complete"] is False
    assert report["run"]["total_requests"] > report["run"]["answered_requests"]


def test_cli_is_incomplete_when_the_ollama_baseline_is_down(tmp_path: Path) -> None:
    def down(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise ConnectionRefusedError("down")

    code = _run(
        tmp_path,
        "--live-calls-authorized",
        "--ollama-model",
        "fake",
        transport=OracleTransport(),
        ollama=down,
    )
    report = _report(tmp_path)
    assert code == 1 and report["phase_1_complete"] is False
    assert report["extraction"]["gates"]["baseline_answered"] is False
    assert report["extraction"]["gates"]["jev_answered"] is True
