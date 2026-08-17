"""Contracts for the preregistered lifecycle token break-even evaluation."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from mnemo_memory.apps.cli.main import build_automatic_memory_hook
from mnemo_memory.packages.application import (
    PersonalSettings,
    PersonalSettingsStore,
    resolve_local_config,
)
from mnemo_memory.packages.application.automatic_memory import LocalMemoryProjectBindingStore
from mnemo_memory.packages.application.context_routing import AUTOMATIC_CONTEXT_LAZY_PULL_HINT
from mnemo_memory.packages.application.semantic_rendering import ConservativeTokenCounter
from scripts.run_lifecycle_token_break_even import (
    LifecycleTokenBreakEvenError,
    build_offline_rows,
    run_offline_evaluation,
)

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


@pytest.fixture(scope="module")
def offline_rows(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict[str, Any], ...]:
    return build_offline_rows(ROOT, tmp_path_factory.mktemp("lifecycle-rows") / "work")


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


def test_offline_rows_pair_conditions_and_require_prior_only_memory(
    offline_rows: tuple[dict[str, Any], ...],
) -> None:
    rows = offline_rows

    assert len(rows) == 6 * 3 * 4
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((row["scenario_family"], row["horizon"]), []).append(row)
        assert row["mnemo_model_tokens"] == {"input": 0, "output": 0}
        assert row["measurement_source"] == "tokenizer_estimate"
        assert row["memory_necessity_valid"] is True
        assert row["stored_payload_fields"] == ()

    assert len(grouped) == 18
    for (_, horizon), group in grouped.items():
        assert {row["condition"] for row in group} == {"FH", "RS", "NM", "MR"}
        assert len({row["current_input_sha256"] for row in group}) == 1
        assert len({row["starting_state_sha256"] for row in group}) == 1
        assert len({row["lifecycle_schedule_sha256"] for row in group}) == 1
        by_condition = {row["condition"]: row for row in group}
        if horizon == 1:
            assert all(row["required_prior_fact_available"] is None for row in group)
            continue
        assert by_condition["FH"]["required_prior_fact_available"] is True
        assert by_condition["MR"]["required_prior_fact_available"] is True
        assert by_condition["NM"]["required_prior_fact_available"] is False
        assert (
            by_condition["FH"]["required_prior_event_keys"]
            == by_condition["MR"]["required_prior_event_keys"]
        )
        assert by_condition["NM"]["required_prior_event_keys"] == ()


def test_offline_mr_uses_real_hook_boundaries_and_suppresses_duplicates(
    offline_rows: tuple[dict[str, Any], ...],
) -> None:
    rows = offline_rows
    mr_rows = {row["horizon"]: row for row in rows if row["condition"] == "MR"}

    assert mr_rows
    for horizon, row in mr_rows.items():
        lifecycle = row["lifecycle"]
        assert lifecycle["session_start_count"] == horizon
        assert lifecycle["self_contained_attachment_tokens"] == 0
        assert lifecycle["duplicate_suppression_count"] == {1: 1, 10: 10, 30: 31}[horizon]
        assert lifecycle["detail_delivery_count"] >= horizon
        assert lifecycle["precompact_count"] == {1: 0, 10: 1, 30: 2}[horizon]
        assert lifecycle["precompact_reset_count"] == lifecycle["precompact_count"]
        assert lifecycle["mnemo_model_call_count"] == 0


def test_production_hook_composition_delivers_one_bounded_uncertainty_hint(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    data = tmp_path / "data"
    project.mkdir()
    LocalMemoryProjectBindingStore(data).enable(project)
    PersonalSettingsStore(data).save(PersonalSettings(experimental_semantic_memory_enabled=True))
    hook = build_automatic_memory_hook(resolve_local_config(data), "codex")
    session_start = {"hook_event_name": "SessionStart", "session_id": "s1", "cwd": str(project)}
    uncertain = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": "s1",
        "cwd": str(project),
        "prompt": "finance reconciliation variance",
    }

    hook.handle(session_start)
    first = hook.handle(uncertain)
    repeated = hook.handle(uncertain)

    assert AUTOMATIC_CONTEXT_LAZY_PULL_HINT in json.dumps(first, sort_keys=True)
    assert repeated == {}
    assert ConservativeTokenCounter().count(AUTOMATIC_CONTEXT_LAZY_PULL_HINT) <= 40
    hook.handle(session_start)
    assert AUTOMATIC_CONTEXT_LAZY_PULL_HINT in json.dumps(hook.handle(uncertain), sort_keys=True)


def test_offline_artifacts_are_private_immutable_resumable_and_deterministic(
    tmp_path: Path,
) -> None:
    prompt_marker = "PRIVATE-PROMPT-MARKER-41af2f"
    response_marker = "PRIVATE-RESPONSE-MARKER-bfd951"
    reasoning_marker = "PRIVATE-REASONING-MARKER-839ce1"
    first = run_offline_evaluation(
        ROOT,
        tmp_path / "runs-a",
        run_id="offline-a",
        prompt_marker=prompt_marker,
        response_marker=response_marker,
        reasoning_marker=reasoning_marker,
    )
    second = run_offline_evaluation(
        ROOT,
        tmp_path / "runs-b",
        run_id="offline-b",
        prompt_marker=prompt_marker,
        response_marker=response_marker,
        reasoning_marker=reasoning_marker,
    )

    source = _load_json(SOURCE_CORPUS_PATH)
    forbidden = (
        prompt_marker,
        response_marker,
        reasoning_marker,
        "MNEMO_CONTEXT_V1",
        AUTOMATIC_CONTEXT_LAZY_PULL_HINT,
        *(str(template["task_prompt"]) for template in source["templates"]),
        *(
            str(event["summary"])
            for template in source["templates"]
            for event in template["events"]
        ),
    )
    for run_directory in (first, second):
        artifact_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(run_directory.iterdir())
            if path.is_file()
        )
        assert not any(marker in artifact_text for marker in forbidden)
        raw_path = run_directory / "raw-sessions.jsonl"
        assert len(raw_path.read_text(encoding="utf-8").splitlines()) == 72
        assert (run_directory / "failures.jsonl").read_text(encoding="utf-8") == ""
        manifest = _load_json(run_directory / "reproducibility-manifest.json")
        for relative_path, digest in manifest["artifact_sha256"].items():
            assert sha256((run_directory / relative_path).read_bytes()).hexdigest() == digest

    assert (first / "aggregate.json").read_bytes() == (second / "aggregate.json").read_bytes()
    before = (first / "raw-sessions.jsonl").read_bytes()
    assert (
        run_offline_evaluation(
            ROOT,
            tmp_path / "runs-a",
            run_id="offline-a",
            resume=True,
            prompt_marker=prompt_marker,
            response_marker=response_marker,
            reasoning_marker=reasoning_marker,
        )
        == first
    )
    assert (first / "raw-sessions.jsonl").read_bytes() == before
    with pytest.raises(LifecycleTokenBreakEvenError, match="already exists"):
        run_offline_evaluation(
            ROOT,
            tmp_path / "runs-a",
            run_id="offline-a",
            prompt_marker=prompt_marker,
            response_marker=response_marker,
            reasoning_marker=reasoning_marker,
        )
