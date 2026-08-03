"""Deterministic coverage for the model-free fresh-session resumption fixture."""

from __future__ import annotations

from copy import deepcopy
from typing import cast

from mnemo_memory.packages.domain import CheckpointContent, ContextPacket
from scripts import run_resumption_benchmark as benchmark


def fixture() -> tuple[dict[str, object], str]:
    value, transcript = benchmark.load_fixture()
    return value, transcript


def test_fixture_is_evidenced_and_distinguishes_current_stale_and_inference() -> None:
    value, transcript = fixture()
    facts = value["facts"]
    assert isinstance(facts, list)
    kinds = {fact["kind"] for fact in facts if isinstance(fact, dict)}
    assert {
        "required",
        "current_decision",
        "forbidden_stale",
        "unverified_inference",
        "optional",
    } <= kinds
    for fact in facts:
        assert isinstance(fact, dict)
        assert " ".join(str(fact["text"]).lower().split()) in " ".join(transcript.lower().split())
        for evidence_id in fact["evidence"]:
            assert f"## {evidence_id}" in transcript


def test_checkpoint_and_packet_are_canonical_and_within_hard_budgets() -> None:
    value, _ = fixture()
    content, packet = benchmark.build_checkpoint_packet(value)
    assert content.token_estimate <= 600
    assert packet.declared_total_tokens <= packet.budget.total_limit
    assert packet.active_task_checkpoint is not None
    assert packet.provenance and packet.provenance[0].evidence_references
    assert ContextPacket.from_json(packet.to_json()) == packet
    encoded = content.to_dict()
    assert {"checkpoint_id", "checkpoint_revision_id", "supersedes_checkpoint_id"}.isdisjoint(
        encoded
    )
    assert CheckpointContent.from_dict(encoded) == content


def test_three_conditions_have_expected_information_and_token_outcomes() -> None:
    value, transcript = fixture()
    result = benchmark.evaluate(value, transcript)
    conditions = cast(dict[str, dict[str, object]], result["conditions"])
    no_memory = conditions["no_memory"]
    full = conditions["full_transcript"]
    mnemo = conditions["mnemo_context"]
    no_memory_quality = cast(dict[str, object], no_memory["quality"])
    full_quality = cast(dict[str, object], full["quality"])
    mnemo_quality = cast(dict[str, object], mnemo["quality"])
    assert no_memory_quality["required_fact_recall"] == 0.0
    assert no_memory_quality["reasoning_lesson_available"] is False
    assert full_quality["required_fact_recall"] == 1.0
    assert full_quality["reasoning_lesson_available"] is True
    assert full_quality["forbidden_stale_fact_ids_present"] == ["superseded-decision"]
    assert mnemo_quality["required_fact_recall"] == 1.0
    assert mnemo_quality["provenance_coverage"] == 1.0
    assert mnemo_quality["current_decision_available"] is True
    assert mnemo_quality["expected_next_action_available"] is True
    assert mnemo_quality["reasoning_lesson_available"] is True
    assert mnemo_quality["forbidden_stale_fact_ids_as_current"] == []
    tokens = cast(dict[str, float], result["token_accounting"])
    assert tokens["full_transcript_tokens"] > tokens["context_packet_tokens"]
    assert tokens["raw_transcript_to_packet_reduction_percent"] >= 50
    assert tokens["full_condition_to_mnemo_total_input_reduction_percent"] > 0
    assert result["passed"] is True


def test_result_and_digest_are_deterministic_and_gates_can_fail(capsys) -> None:  # type: ignore[no-untyped-def]
    value, transcript = fixture()
    assert benchmark.evaluate(value, transcript) == benchmark.evaluate(value, transcript)
    changed = deepcopy(value)
    changed["fixture_version"] = "changed"
    assert benchmark.fixture_digest(value, transcript) != benchmark.fixture_digest(
        changed, transcript
    )
    assert benchmark.main(["--minimum-savings-percent", "99", "--json"]) == 1
    assert '"context_savings_threshold":false' in capsys.readouterr().out
