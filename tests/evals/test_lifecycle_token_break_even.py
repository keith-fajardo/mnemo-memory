"""Contracts for the preregistered lifecycle token break-even evaluation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parents[2]
FIXTURE_PATH = ROOT / "tests/fixtures/evals/lifecycle-token-break-even-v1.json"
SOURCE_CORPUS_PATH = ROOT / "tests/fixtures/evals/viability-corpus-v1.json"
PREREGISTRATION_PATH = ROOT / "docs/evaluations/lifecycle-token-break-even-preregistration.md"


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _strings(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(item for value_item in value for item in _strings(value_item))
    if isinstance(value, dict):
        return tuple(item for value_item in value.values() for item in _strings(value_item))
    return ()


def test_lifecycle_fixture_references_original_scenarios_without_copying_truth() -> None:
    fixture_text = FIXTURE_PATH.read_text(encoding="utf-8")
    fixture = _load_json(FIXTURE_PATH)
    source = _load_json(SOURCE_CORPUS_PATH)

    assert fixture["schema_version"] == "mnemo-lifecycle-token-break-even/1.0"
    assert fixture["fixture_id"] == "lifecycle-token-break-even-v1"
    assert fixture["source_corpus"] == "tests/fixtures/evals/viability-corpus-v1.json"
    assert fixture["provenance"] == {
        "classification": "original_mnemo_synthetic_schedule",
        "originality_attestation": (
            "Original Mnemo evaluation schedule referencing existing Mnemo-owned synthetic "
            "event keys; no production, personal, secret, or competitor content."
        ),
    }
    assert fixture["conditions"] == ["FH", "RS", "NM", "MR"]
    assert fixture["horizons"] == [1, 10, 30]
    assert fixture["maximum_prompt_events_per_client_session"] == 4

    source_templates = {
        item["template_id"]: item for item in source["templates"] if isinstance(item, dict)
    }
    scenarios = fixture["scenario_families"]
    assert len(scenarios) == 6
    assert {item["template_id"] for item in scenarios} == set(source_templates)
    for scenario in scenarios:
        source_template = source_templates[scenario["template_id"]]
        event_keys = {event["event_key"] for event in source_template["events"]}
        assert set(scenario) == {"template_id", "seed_event_key", "changed_event_key"}
        assert scenario["seed_event_key"] in event_keys
        assert scenario["changed_event_key"] in event_keys
        assert scenario["seed_event_key"] != scenario["changed_event_key"]

    copied_truth = set()
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


def test_lifecycle_schedule_exposes_boundaries_duplicates_changes_and_compaction() -> None:
    fixture = _load_json(FIXTURE_PATH)
    sessions = fixture["sessions"]

    assert len(sessions) == 30
    assert [item["session_number"] for item in sessions] == list(range(1, 31))
    assert len({item["client_session_id"] for item in sessions}) == 30
    assert sessions[0]["scored_reuse"] is False
    assert all(item["scored_reuse"] is True for item in sessions[1:])

    compaction_sessions: list[int] = []
    changed_sessions: list[int] = []
    for session in sessions:
        events = session["events"]
        assert events[0] == {"hook_event_name": "SessionStart"}
        prompt_events = [
            event for event in events if event["hook_event_name"] == "UserPromptSubmit"
        ]
        assert len(prompt_events) <= fixture["maximum_prompt_events_per_client_session"]
        purposes = [event["purpose"] for event in prompt_events]
        assert purposes[:3] == [
            "self_contained",
            "prior_memory_need",
            "repeat_prior_memory_need",
        ]
        repeated = prompt_events[2]
        assert repeated["same_delivery_as"] == "prior_memory_need"
        if "changed_memory_need" in purposes:
            changed_sessions.append(session["session_number"])
            assert prompt_events[-1]["query_ref"] == "changed_event_key"
        if events[-1]["hook_event_name"] == "PreCompact":
            compaction_sessions.append(session["session_number"])

    assert changed_sessions == [10, 30]
    assert compaction_sessions == [10, 20]


def test_preregistration_freezes_claims_gates_and_artifact_privacy() -> None:
    document = PREREGISTRATION_PATH.read_text(encoding="utf-8")
    normalized_document = " ".join(document.split())

    required_statements = (
        "Status: preregistered before implementation and before any lifecycle result",
        "FH — full usable growing history",
        "RS — deterministic rolling summary",
        "NM — current-session input only",
        "MR — lifecycle-routed Mnemo",
        "1 - MR_total_model_tokens / FH_total_model_tokens",
        "at least 30%",
        "FH = 1.0, MR = 1.0, and NM = 0.0",
        "INVALID",
        "scenario family is the independence unit",
        "descriptive sensitivity summaries",
        "PROVISIONAL",
        "NOT EVALUATED",
        "prompts, responses, tool bodies, or model reasoning",
        "36 primary calls plus one preflight smoke call",
    )
    for statement in required_statements:
        assert " ".join(statement.split()) in normalized_document
