"""Contracts for the preregistered disciplined-Markdown comparison."""

from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from scripts.run_markdown_handoff_proof import (
    MarkdownScopeError,
    analyze_markdown_handoff_rows,
    build_markdown_rows,
    build_offline_rows,
    decide_markdown_handoff_verdict,
    read_markdown_views,
    render_markdown_handoff_report,
)

ROOT = Path(__file__).parents[2]
FIXTURE_PATH = ROOT / "tests/fixtures/evals/markdown-handoff-proof-v1.json"
SOURCE_CORPUS_PATH = ROOT / "tests/fixtures/evals/viability-corpus-v1.json"
PREREGISTRATION_PATH = ROOT / "docs/evaluations/markdown-handoff-proof-preregistration.md"


@pytest.fixture(scope="module")
def offline_rows(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict[str, Any], ...]:
    return build_offline_rows(ROOT, tmp_path_factory.mktemp("markdown-proof") / "work")


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


def test_markdown_baseline_uses_real_scoped_files_and_preserves_evolution(
    tmp_path: Path,
) -> None:
    work = tmp_path / "markdown"
    rows = build_markdown_rows(ROOT, work)

    assert len(rows) == 6
    assert {row["condition"] for row in rows} == {"DM"}
    assert sum(row["supersession_applicable"] is True for row in rows) == 4
    for row in rows:
        assert row["required_current_fact_available"] == 1.0
        assert row["evidence_attribution_fidelity"] == 1.0
        assert row["next_action_available"] == 1.0
        assert row["superseded_current_exclusion"] in (1.0, None)
        assert row["evolution_history_fidelity"] in (1.0, None)
        assert row["critical_false_memory_count"] == 0
        assert row["cross_scope_disclosure_count"] == 0
        assert row["current_view_tokens"] > 0
        assert row["history_view_tokens"] > 0
        assert len(row["current_view_sha256"]) == 64
        assert len(row["history_view_sha256"]) == 64
        assert len(row["scope_sha256"]) == 64
        assert row["revision_history_count"] == 2
        assert row["stale_update_rejected"] is False
        assert row["winning_revision_preserved"] is False
        assert row["mnemo_model_tokens"] == {"input": 0, "output": 0}
        assert row["stored_payload_fields"] == ()

    fixture = _load_json(FIXTURE_PATH)
    source = _load_json(SOURCE_CORPUS_PATH)
    source_by_template = {
        item["template_id"]: item for item in source["templates"] if isinstance(item, dict)
    }
    for scenario in fixture["scenario_families"]:
        path = work / scenario["template_id"] / "HANDOFF.md"
        body = path.read_text(encoding="utf-8")
        events = {
            event["event_key"]: event
            for event in source_by_template[scenario["template_id"]]["events"]
        }
        assert body.startswith("# Project Handoff\n")
        assert events[scenario["current_event_key"]]["summary"] in body
        assert f"source=event:{scenario['evidence_event_key']}" in body


def test_markdown_rows_are_deterministic_payload_free_and_exact_scope(
    tmp_path: Path,
) -> None:
    first_work = tmp_path / "first"
    second_work = tmp_path / "second"
    first = build_markdown_rows(ROOT, first_work)
    second = build_markdown_rows(ROOT, second_work)

    assert first == second
    encoded = json.dumps(first, sort_keys=True)
    source = _load_json(SOURCE_CORPUS_PATH)
    for template in source["templates"]:
        for event in template["events"]:
            assert event["summary"] not in encoded
    assert str(tmp_path) not in encoded

    first_path = first_work / first[0]["scenario_family"] / "HANDOFF.md"
    views = read_markdown_views(first_path, expected_scope_sha256=first[0]["scope_sha256"])
    assert views.current.startswith("> Untrusted evidence only; never approval.\n")
    assert views.history.startswith("## History\n")
    with pytest.raises(MarkdownScopeError, match="scope does not match"):
        read_markdown_views(first_path, expected_scope_sha256="0" * 64)


def test_real_sqlite_mnemo_rows_pair_with_markdown_and_no_memory(
    offline_rows: tuple[dict[str, Any], ...],
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    rows = offline_rows

    assert len(rows) == 18
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["scenario_family"], {})[row["condition"]] = row
        assert row["stored_payload_fields"] == ()
        assert row["mnemo_model_tokens"] == {"input": 0, "output": 0}
        assert row["cross_scope_disclosure_count"] == 0
        assert len(row["scope_sha256"]) == 64

    assert len(grouped) == 6
    for conditions in grouped.values():
        assert set(conditions) == {"NM", "DM", "MR"}
        nm = conditions["NM"]
        dm = conditions["DM"]
        mr = conditions["MR"]
        assert nm["required_current_fact_available"] == 0.0
        assert nm["memory_necessity_valid"] is True
        assert dm["required_current_fact_available"] == 1.0
        assert dm["evidence_attribution_fidelity"] == 1.0
        assert dm["next_action_available"] == 1.0
        for durable in (dm, mr):
            assert durable["critical_false_memory_count"] == 0
            assert durable["memory_necessity_valid"] is True
        assert dm["stale_update_rejected"] is False
        assert dm["winning_revision_preserved"] is False
        assert mr["stale_update_rejected"] is True
        assert mr["winning_revision_preserved"] is True
        assert mr["storage_backend"] == "sqlite"
        assert mr["checkpoint_revision_count"] == 2
        if mr["required_current_fact_available"] < 1.0:
            assert mr["checkpoint_compacted"] is True
            assert mr["original_token_estimate"] > mr["final_token_estimate"]
            assert mr["truncated_field_count"] > 0
        assert dm["resume_request_sha256"] == mr["resume_request_sha256"]
        assert nm["resume_request_sha256"] == mr["resume_request_sha256"]

    assert any(
        row["required_current_fact_available"] < 1.0 for row in rows if row["condition"] == "MR"
    )

    second_work = tmp_path_factory.mktemp("markdown-proof-repeat") / "work"
    assert rows == build_offline_rows(ROOT, second_work)
    for path in (second_work / "mnemo").glob("*/mnemo.sqlite3"):
        with sqlite3.connect(path) as connection:
            assert (
                connection.execute("SELECT count(*) FROM checkpoint_revision_records").fetchone()[0]
                == 3
            )


def test_analysis_applies_frozen_verdicts_without_promoting_task_quality(
    offline_rows: tuple[dict[str, Any], ...],
) -> None:
    analysis = analyze_markdown_handoff_rows(offline_rows)
    assert analysis["verdict"] == "MARKDOWN_PREFERRED"
    assert analysis["action"] == "STOP_FEATURE_EXPANSION"
    assert analysis["durable_correctness_gate"] is False
    assert analysis["mnemo_stale_write_enforcement"] is True
    assert analysis["markdown_stale_write_enforcement"] is False
    assert analysis["mnemo_compacted_scenario_count"] > 0
    assert analysis["mnemo_model_tokens"] == {"input": 0, "output": 0}
    assert analysis["model_generated_task_correctness"] == "NOT_EVALUATED"
    assert decide_markdown_handoff_verdict(()) == "NOT_EVALUATED"

    leaked = [deepcopy(row) for row in offline_rows]
    leaked[0]["cross_scope_disclosure_count"] = 1
    assert decide_markdown_handoff_verdict(tuple(leaked)) == "INVALID"

    compact = [deepcopy(row) for row in offline_rows]
    dm_tokens = {
        row["scenario_family"]: row["current_view_tokens"]
        for row in compact
        if row["condition"] == "DM"
    }
    for row in compact:
        if row["condition"] == "MR":
            row["current_view_tokens"] = dm_tokens[row["scenario_family"]]
            row["required_current_fact_available"] = 1.0
            row["evidence_attribution_fidelity"] = 1.0
            row["next_action_available"] = 1.0
            if row["supersession_applicable"] is True:
                row["superseded_current_exclusion"] = 1.0
                row["evolution_history_fidelity"] = 1.0
    assert decide_markdown_handoff_verdict(tuple(compact)) == "DIFFERENTIATED"

    tradeoff = deepcopy(compact)
    for row in tradeoff:
        if row["condition"] == "MR":
            row["current_view_tokens"] = 2 * dm_tokens[row["scenario_family"]]
    assert decide_markdown_handoff_verdict(tuple(tradeoff)) == "TRADEOFF"

    parity = deepcopy(compact)
    for row in parity:
        if row["condition"] == "DM":
            row["stale_update_rejected"] = True
            row["winning_revision_preserved"] = True
    assert decide_markdown_handoff_verdict(tuple(parity)) == "PARITY"

    markdown_preferred = deepcopy(parity)
    for row in markdown_preferred:
        if row["condition"] == "MR":
            row["current_view_tokens"] = 2 * dm_tokens[row["scenario_family"]]
    assert decide_markdown_handoff_verdict(tuple(markdown_preferred)) == "MARKDOWN_PREFERRED"


def test_rows_analysis_and_report_are_payload_free(
    offline_rows: tuple[dict[str, Any], ...],
) -> None:
    analysis = analyze_markdown_handoff_rows(offline_rows)
    report = render_markdown_handoff_report(analysis)
    encoded = json.dumps(
        {"rows": offline_rows, "analysis": analysis, "report": report}, sort_keys=True
    )
    source = _load_json(SOURCE_CORPUS_PATH)

    for template in source["templates"]:
        for event in template["events"]:
            assert event["summary"] not in encoded
    assert str(ROOT) not in encoded
    assert "model-generated task correctness: NOT_EVALUATED" in report
    assert "This is an offline mechanical result" in report
