"""Preregistered telehealth corpus, hidden grader, and paired analysis contracts."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from pytest import MonkeyPatch, approx

from mnemo_memory.packages.domain import (
    EventId,
    SemanticAtomKind,
    SemanticMemoryAtom,
)
from scripts import run_long_horizon_evaluation as evaluation
from scripts.run_long_horizon_evaluation import (
    _DELIBERATIVE,
    _MNEMO,
    DEFAULT_CORPUS,
    _expected,
    _generate_candidate,
    _load_corpus,
    _memory_content,
    _valid_changes,
    _variant,
    _verifier_gain,
    hidden_checks,
)


def test_all_thirty_variants_have_distinct_hidden_identities() -> None:
    corpus = _load_corpus(DEFAULT_CORPUS)
    variants = [_variant(corpus, index) for index in range(30)]
    assert len({item["variant_id"] for item in variants}) == 30
    assert len({item["timezone"] for item in variants}) == 30
    assert len({item["idempotency_key"] for item in variants}) == 30


def test_hidden_grader_requires_every_safety_and_supersession_behavior() -> None:
    corpus = _load_corpus(DEFAULT_CORPUS)
    variant = _variant(corpus, 0)
    expected = _expected(variant)
    assert all(hidden_checks(expected, expected).values())
    poisoned = {**expected, "authorize_before_lookup": False, "timezone_mode": "offset"}
    checks = hidden_checks(poisoned, expected)
    assert checks["authorization_precedes_lookup"] is False
    assert checks["iana_mode"] is False


def test_model_changes_are_closed_to_the_preregistered_schema() -> None:
    corpus = _load_corpus(DEFAULT_CORPUS)
    accepted, invalid = _valid_changes(
        {
            "changes": {
                "atomic_reservation": True,
                "timezone_mode": "invented",
                "hidden_answer": "leak",
            }
        },
        corpus,
    )
    assert accepted == {"atomic_reservation": True}
    assert invalid == 2


def test_failed_approach_is_encoded_as_failure_memory() -> None:
    corpus = _load_corpus(DEFAULT_CORPUS)
    content = _memory_content(
        condition="SD",
        variant=_variant(corpus, 0),
        session=2,
        config={},
        public_history=[],
        response={"changes": {}, "uncertainty": "retry after stale cache"},
    )

    assert content.failures == ("failure: uncertainty=retry after stale cache",)


def test_sf_fixed_routes_current_config_through_volatile_state() -> None:
    corpus = _load_corpus(DEFAULT_CORPUS)
    variant = _variant(corpus, 0)
    config: dict[str, object] = {"timezone": "America/New_York"}

    factual = _memory_content(
        condition="SF",
        variant=variant,
        session=2,
        config=config,
        public_history=[],
        response=None,
    )
    fixed = _memory_content(
        condition="SF-fixed",
        variant=variant,
        session=2,
        config=config,
        public_history=[],
        response=None,
    )

    assert any(item.startswith("fact: Current config ") for item in factual.completed_work)
    assert fixed.current_state.startswith("state: Current config ")
    assert not any(item.startswith("fact: Current config ") for item in fixed.completed_work)


def test_sfp_is_an_optional_index_and_pull_harness_condition() -> None:
    corpus = _load_corpus(DEFAULT_CORPUS)
    content = _memory_content(
        condition="SFp",
        variant=_variant(corpus, 0),
        session=2,
        config={"timezone": "America/New_York"},
        public_history=[],
        response=None,
    )

    assert "SFp" in _MNEMO
    assert content.current_state.startswith("state: Current config ")


def test_sv_is_sd_plus_at_most_two_verifier_repair_retries(monkeypatch: MonkeyPatch) -> None:
    corpus = _load_corpus(DEFAULT_CORPUS)
    variant = _variant(corpus, 0)
    scope = evaluation._scope(str(variant["variant_id"]), "SV")
    atom = SemanticMemoryAtom.create(
        scope=scope,
        kind=SemanticAtomKind.CONSTRAINT,
        subject="user",
        predicate="requires",
        object_value="timezone_mode=iana",
        source_event_ids=(EventId.from_string("60000000-0000-4000-8000-000000000001"),),
        created_at=datetime(2026, 8, 16, tzinfo=UTC),
    )
    calls: list[str] = []

    def fake_post(_base_url: str, _path: str, payload: dict[str, object]) -> dict[str, object]:
        calls.append(str(payload["prompt"]))
        return {
            "response": json.dumps(
                {
                    "changes": {"timezone_mode": "offset"},
                    "analysis_summary": "checked",
                    "hypothesis": "offset",
                    "evidence_used": [],
                    "uncertainty": "none",
                    "next_action": "apply",
                    "confidence": 0.8,
                }
            ),
            "prompt_eval_count": 10,
            "eval_count": 5,
        }

    monkeypatch.setattr(evaluation, "_post", fake_post)
    generated = _generate_candidate(
        model_url="http://127.0.0.1:11434",
        payload={"model": "fixture", "prompt": "BASE"},
        base_prompt="BASE",
        corpus=corpus,
        verification_atoms=(atom,),
    )

    assert "SV" in _MNEMO
    assert "SV" in _DELIBERATIVE
    assert generated.model_call_count == 3
    assert len(calls) == 3
    assert generated.actual_usage["prompt_eval_count"] == 30
    assert generated.actual_usage["eval_count"] == 15
    assert len(generated.verification_reports) == 3
    assert "Consistency check only; not approval" in calls[1]
    assert "Consistency check only; not approval" in calls[2]


def test_sv_minus_sd_has_a_separate_ten_point_accuracy_gate() -> None:
    corpus = _load_corpus(DEFAULT_CORPUS)
    corpus["conditions"] = [*corpus["conditions"], "SV"]
    rows = [
        {
            "variant_id": "telehealth-01",
            "condition": "SD",
            "hidden_test_accuracy": 0.6,
        },
        {
            "variant_id": "telehealth-01",
            "condition": "SV",
            "hidden_test_accuracy": 0.8,
        },
    ]

    result = _verifier_gain(rows, corpus, seed=1, iterations=20)

    assert result is not None
    assert result["mean_difference"] == approx(0.2)
    assert result["required_margin"] == 0.10
    assert result["passes_margin"] is True
