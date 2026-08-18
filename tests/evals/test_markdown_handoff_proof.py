"""Contracts for the preregistered disciplined-Markdown comparison."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parents[2]
FIXTURE_PATH = ROOT / "tests/fixtures/evals/markdown-handoff-proof-v1.json"
SOURCE_CORPUS_PATH = ROOT / "tests/fixtures/evals/viability-corpus-v1.json"
PREREGISTRATION_PATH = ROOT / "docs/evaluations/markdown-handoff-proof-preregistration.md"


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _strings(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(item for child in value for item in _strings(child))
    if isinstance(value, dict):
        return tuple(item for child in value.values() for item in _strings(child))
    return ()


def test_fixture_references_all_original_scenarios_without_copying_truth() -> None:
    fixture_text = FIXTURE_PATH.read_text(encoding="utf-8")
    fixture = _load_json(FIXTURE_PATH)
    source = _load_json(SOURCE_CORPUS_PATH)

    assert fixture["schema_version"] == "mnemo-markdown-handoff-proof/1.0"
    assert fixture["fixture_id"] == "markdown-handoff-proof-v1"
    assert fixture["source_corpus"] == "tests/fixtures/evals/viability-corpus-v1.json"
    assert fixture["conditions"] == ["NM", "DM", "MR"]
    assert fixture["provenance"] == {
        "classification": "original_mnemo_synthetic_comparison",
        "originality_attestation": (
            "Original Mnemo evaluation schedule referencing existing Mnemo-owned synthetic "
            "event keys; no production, personal, secret, or competitor content."
        ),
    }
    assert fixture["markdown_baseline"] == {
        "format": "one project-local Markdown file",
        "current_view": "selective compact section",
        "history_view": "append-only section",
        "maintenance": "deterministic perfect fixture updates",
        "concurrency": "plain last-writer-wins save without external locking or Git commits",
    }

    source_templates = {
        item["template_id"]: item for item in source["templates"] if isinstance(item, dict)
    }
    scenarios = fixture["scenario_families"]
    assert len(scenarios) == 6
    assert {item["template_id"] for item in scenarios} == set(source_templates)
    supersession_count = 0
    expected_fields = {
        "template_id",
        "initial_event_keys",
        "revised_event_keys",
        "current_event_key",
        "superseded_event_key",
        "evidence_event_key",
        "next_action_event_key",
    }
    for scenario in scenarios:
        assert set(scenario) == expected_fields
        source_events = {
            event["event_key"]: event
            for event in source_templates[scenario["template_id"]]["events"]
        }
        referenced = (
            *scenario["initial_event_keys"],
            *scenario["revised_event_keys"],
            scenario["current_event_key"],
            scenario["evidence_event_key"],
            scenario["next_action_event_key"],
        )
        assert all(key in source_events for key in referenced)
        assert set(scenario["initial_event_keys"]).isdisjoint(scenario["revised_event_keys"])
        assert scenario["current_event_key"] in scenario["revised_event_keys"]
        assert scenario["next_action_event_key"] in scenario["revised_event_keys"]
        superseded = scenario["superseded_event_key"]
        if superseded is not None:
            supersession_count += 1
            assert superseded in scenario["initial_event_keys"]
            assert superseded in source_events
            assert "supersedes" in source_events[scenario["current_event_key"]]["summary"]
    assert supersession_count == 4

    copied_truth: set[str] = set()
    for template in source_templates.values():
        copied_truth.update(
            value for value in _strings(template["ground_truth"]) if len(value) >= 24
        )
        copied_truth.update(
            event["summary"]
            for event in template["events"]
            if isinstance(event, dict) and len(event["summary"]) >= 24
        )
    assert all(value not in fixture_text for value in copied_truth)


def test_preregistration_freezes_fair_baseline_gates_and_live_boundary() -> None:
    document = PREREGISTRATION_PATH.read_text(encoding="utf-8")
    normalized = " ".join(document.split())

    statements = (
        "Status: preregistered before implementation and before any comparison result",
        "NM — no durable memory",
        "DM — disciplined Markdown",
        "MR — real local Mnemo checkpoint storage",
        "Markdown is granted deterministic perfect fixture maintenance",
        "plain Markdown file has no compare-and-swap",
        "additional concurrency mechanism",
        "at most 1.25 times",
        "DIFFERENTIATED",
        "TRADEOFF",
        "PARITY",
        "MARKDOWN_PREFERRED",
        "STOP_FEATURE_EXPANSION",
        "model-generated task correctness is NOT EVALUATED",
        "prompts, responses, tool bodies, or model reasoning",
        "No Ollama or other model call is authorized",
    )
    for statement in statements:
        assert " ".join(statement.split()) in normalized
