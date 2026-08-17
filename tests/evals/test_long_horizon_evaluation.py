"""Preregistered telehealth corpus, hidden grader, and paired analysis contracts."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pytest import MonkeyPatch, approx

from mnemo_memory.packages.domain import (
    EventId,
    SemanticAtomKind,
    SemanticMemoryAtom,
    TaskActivityActor,
)
from scripts import run_long_horizon_evaluation as evaluation
from scripts.run_long_horizon_evaluation import (
    _DELIBERATIVE,
    _MNEMO,
    DEFAULT_CORPUS,
    _exact_value_integrity,
    _expected,
    _generate_candidate,
    _load_corpus,
    _memory_content,
    _repository_corpus_path,
    _trusted_constraint_events,
    _valid_changes,
    _variant,
    _verifier_gain,
    deterministic_ceiling_diagnostic,
    hidden_checks,
)

PHASE2_CORPORA = {
    "qwen2.5-coder:7b": Path(
        "tests/fixtures/evals/telehealth-long-horizon-phase2-qwen25coder7b.json"
    ),
    "qwen3:14b-instruct": Path(
        "tests/fixtures/evals/telehealth-long-horizon-phase2-qwen3-14b.json"
    ),
    "qwen3:14b-thinking": Path(
        "tests/fixtures/evals/telehealth-long-horizon-phase2-qwen3-14b-thinking.json"
    ),
}


def test_all_thirty_variants_have_distinct_hidden_identities() -> None:
    corpus = _load_corpus(DEFAULT_CORPUS)
    variants = [_variant(corpus, index) for index in range(30)]
    assert len({item["variant_id"] for item in variants}) == 30
    assert len({item["timezone"] for item in variants}) == 30
    assert len({item["idempotency_key"] for item in variants}) == 30


def test_phase2_capability_ladder_corpora_keep_gates_and_record_generation_modes() -> None:
    anchor = _load_corpus(PHASE2_CORPORA["qwen2.5-coder:7b"])
    instruct = _load_corpus(PHASE2_CORPORA["qwen3:14b-instruct"])
    thinking = _load_corpus(PHASE2_CORPORA["qwen3:14b-thinking"])

    expected_conditions = ["S0", "SI", "SR", "SF", "SD", "SX", "SF-fixed", "SFp", "SV"]
    assert anchor["conditions"] == instruct["conditions"] == thinking["conditions"]
    assert anchor["conditions"] == expected_conditions
    assert all(corpus["variant_count"] == 30 for corpus in (anchor, instruct, thinking))
    assert all(
        corpus["preregistered_thresholds"]["verifier_hidden_test_accuracy_margin"] == 0.1
        for corpus in (anchor, instruct, thinking)
    )
    assert anchor["model"]["identifier"] == "qwen2.5-coder:7b"
    assert instruct["model"] == {
        "identifier": "qwen3:14b",
        "temperature": 0,
        "num_ctx": 4096,
        "num_predict": 320,
        "think": False,
        "generation_strategy": "single_json",
    }
    assert thinking["model"] == {
        **instruct["model"],
        "think": True,
        "generation_strategy": "two_phase_json",
    }


def test_repository_relative_corpus_path_is_normalized_before_provenance(
    tmp_path: Path,
) -> None:
    relative = PHASE2_CORPORA["qwen2.5-coder:7b"]

    assert _repository_corpus_path(relative) == relative.resolve()
    try:
        _repository_corpus_path(tmp_path / "outside.json")
    except evaluation.LongHorizonError as error:
        assert str(error) == "evaluation corpus must be inside the repository"
    else:
        raise AssertionError("an external corpus path must be rejected")


def test_hidden_grader_requires_every_safety_and_supersession_behavior() -> None:
    corpus = _load_corpus(DEFAULT_CORPUS)
    variant = _variant(corpus, 0)
    expected = _expected(variant)
    assert all(hidden_checks(expected, expected).values())
    poisoned = {**expected, "authorize_before_lookup": False, "timezone_mode": "offset"}
    checks = hidden_checks(poisoned, expected)
    assert checks["authorization_precedes_lookup"] is False
    assert checks["iana_mode"] is False


def test_exact_value_integrity_is_distinct_from_memory_recall() -> None:
    corpus = _load_corpus(DEFAULT_CORPUS)
    expected = _expected(_variant(corpus, 0))

    complete = _exact_value_integrity(expected, expected)
    overridden = _exact_value_integrity({**expected, "timezone": "UTC+00:00"}, expected)

    assert complete == {"matches": 8, "opportunities": 8, "rate": 1.0}
    assert overridden == {"matches": 7, "opportunities": 8, "rate": 0.875}


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


def test_sv_verifies_accumulated_candidate_before_accepting_current_changes(
    monkeypatch: MonkeyPatch,
) -> None:
    corpus = _load_corpus(DEFAULT_CORPUS)
    variant = _variant(corpus, 0)
    scope = evaluation._scope(str(variant["variant_id"]), "SV")
    atom = SemanticMemoryAtom.create(
        scope=scope,
        kind=SemanticAtomKind.CONSTRAINT,
        subject="user",
        predicate="requires",
        object_value="idempotent_replay=true",
        source_event_ids=(EventId.from_string("60000000-0000-4000-8000-000000000002"),),
        created_at=datetime(2026, 8, 16, tzinfo=UTC),
    )
    calls = 0

    def fake_post(_base_url: str, _path: str, _payload: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        changes: dict[str, object] = {} if calls == 1 else {"idempotent_replay": True}
        return {
            "response": json.dumps(
                {
                    "changes": changes,
                    "analysis_summary": "checked",
                    "hypothesis": "repair accumulated candidate",
                    "evidence_used": [],
                    "uncertainty": "none",
                    "next_action": "apply",
                    "confidence": 1.0,
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
        verification_candidate_base={"idempotent_replay": False},
    )

    assert calls == generated.model_call_count == 2
    assert generated.changes["idempotent_replay"] is True
    assert [report["status"] for report in generated.verification_reports] == [
        "mismatch",
        "consistent",
    ]
    first_violations = cast(
        list[dict[str, object]], generated.verification_reports[0]["violations"]
    )
    assert first_violations[0]["field"] == "idempotent_replay"


def test_thinking_mode_reasons_transiently_then_emits_json_without_format_tax(
    monkeypatch: MonkeyPatch,
) -> None:
    corpus = _load_corpus(DEFAULT_CORPUS)
    corpus["model"]["generation_strategy"] = "two_phase_json"
    corpus["model"]["think"] = True
    calls: list[dict[str, object]] = []
    private_reasoning = "private transient chain marker 7b92"
    final = {
        "changes": {"timezone_mode": "iana"},
        "analysis_summary": "checked",
        "hypothesis": "apply evidence",
        "evidence_used": ["DR-1"],
        "uncertainty": "none",
        "next_action": "save candidate",
        "confidence": 1.0,
    }

    def fake_post(_base_url: str, _path: str, payload: dict[str, object]) -> dict[str, object]:
        calls.append(payload)
        return {
            "response": private_reasoning if len(calls) == 1 else json.dumps(final),
            "prompt_eval_count": 10,
            "eval_count": 5,
        }

    monkeypatch.setattr(evaluation, "_post", fake_post)
    generated = _generate_candidate(
        model_url="http://127.0.0.1:11434",
        payload={"model": "fixture", "prompt": "BASE", "format": "json", "think": True},
        base_prompt="TASK\n\nReturn one JSON object with exactly: changes",
        corpus=corpus,
    )

    assert len(calls) == generated.model_call_count == 2
    assert "format" not in calls[0] and calls[0]["think"] is True
    assert calls[1]["format"] == "json" and calls[1]["think"] is False
    assert private_reasoning in str(calls[1]["prompt"])
    assert generated.response == final
    assert generated.actual_usage["prompt_eval_count"] == 20
    assert generated.actual_usage["eval_count"] == 10


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


def test_dry_run_start_integrity_uses_the_requested_variant_count() -> None:
    corpus = _load_corpus(DEFAULT_CORPUS)
    base: dict[str, object] = {
        "variant_id": "telehealth-01",
        "available": True,
        "hidden_test_accuracy": 1.0,
        "end_to_end_success": True,
        "decision_accuracy": 1.0,
        "regression_free_completion": True,
        "critical_false_memory_count": 0,
        "hypothesis_precision": 1.0,
        "hypothesis_recall": 1.0,
        "repeated_error_count": 0,
        "self_correction_count": 1,
        "supersession_handled": True,
        "memory_precision": 1.0,
        "memory_recall": 1.0,
        "memory_f1": 1.0,
        "exact_value_integrity_rate": 1.0,
        "calibration_brier": 0.0,
        "actual_prompt_tokens": 1,
        "actual_output_tokens": 1,
        "actual_latency_ns": 1,
        "third_session_beyond_active_context": True,
        "poison_safe": True,
        "starting_state_sha256": "sha256:identical",
        "transcript_leakage_detected": False,
        "hidden_grader_rendered": False,
    }
    rows = [{**base, "condition": condition} for condition in ("S0", "SI", "SF", "SD")]

    result = evaluation.analyze(rows, corpus, expected_variant_count=1)

    gate_checks = result["gate_2_checks"]
    assert isinstance(gate_checks, dict)
    assert gate_checks["byte_identical_paired_start"] is True


def test_zero_model_token_deterministic_ceiling_reconciles_constraint_backed_checks() -> None:
    corpus = _load_corpus(DEFAULT_CORPUS)
    variant = _variant(corpus, 0)
    expected = _expected(variant)
    scope = evaluation._scope(str(variant["variant_id"]), "SV")
    atoms = tuple(
        SemanticMemoryAtom.create(
            scope=scope,
            kind=(
                SemanticAtomKind.DECISION
                if name == "timezone_mode"
                else SemanticAtomKind.CONSTRAINT
            ),
            subject="user",
            predicate=f"requires:{name}",
            object_value=f"{name}={evaluation._memory_literal(value)}",
            source_event_ids=(EventId.from_string(f"60000000-0000-4000-8000-{index:012d}"),),
            created_at=datetime(2026, 8, 16, tzinfo=UTC),
            confidence=0.9,
        )
        for index, (name, value) in enumerate(sorted(expected.items()), start=1)
    )

    diagnostic = deterministic_ceiling_diagnostic(
        candidate=dict(corpus["initial_config"]),
        expected=expected,
        atoms=atoms,
    )

    assert diagnostic["model_call_count"] == 0
    assert diagnostic["model_input_tokens"] == 0
    assert diagnostic["model_output_tokens"] == 0
    assert diagnostic["hidden_test_accuracy_after"] == 1.0
    assert diagnostic["constraint_backed_accuracy"] == 1.0


def test_sv_and_sd_persist_only_publicly_revealed_constraints_as_user_evidence() -> None:
    corpus = _load_corpus(DEFAULT_CORPUS)
    variant = _variant(corpus, 0)
    public = evaluation._session_public(corpus, variant, 1)

    sd = _trusted_constraint_events(variant=variant, condition="SD", public=public)
    sv = _trusted_constraint_events(variant=variant, condition="SV", public=public)

    assert [event.summary for event in sv] == [event.summary for event in sd]
    assert all(event.actor is TaskActivityActor.USER for event in sv)
    assert {event.summary for event in sv} == {
        (
            "constraint: authorization_role=scheduler ; authorize_before_lookup=true ; "
            "idempotency_key=IDEM-001-07919 ; idempotency_scope=tenant ; "
            "idempotent_replay=true"
        ),
        "decision: timezone_mode=offset",
    }


def test_sv_and_sd_use_byte_identical_prompts_before_any_verifier_report(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    corpus = _load_corpus(DEFAULT_CORPUS)
    variant = _variant(corpus, 0)
    active_condition = ""
    prompts: dict[str, list[str]] = {"SD": [], "SV": []}

    def fake_post(_base_url: str, _path: str, payload: dict[str, object]) -> dict[str, object]:
        prompt = str(payload["prompt"])
        prompts[active_condition].append(prompt)
        session = next(number for number in (1, 2, 3) if f"SESSION: {number} of 3" in prompt)
        return {
            "response": json.dumps(
                {
                    "changes": _expected(variant, session=session),
                    "analysis_summary": "checked",
                    "hypothesis": "apply exact evidence",
                    "evidence_used": [f"session-{session}"],
                    "uncertainty": "none",
                    "next_action": "apply",
                    "confidence": 1.0,
                }
            ),
            "prompt_eval_count": 10,
            "eval_count": 5,
        }

    monkeypatch.setattr(evaluation, "_post", fake_post)
    for condition in ("SD", "SV"):
        active_condition = condition
        evaluation._trajectory(
            corpus=corpus,
            variant=variant,
            condition=condition,
            model_url="http://127.0.0.1:11434",
            raw_sessions=tmp_path / f"{condition}.jsonl",
            attempt=1,
        )

    assert len(prompts["SD"]) == len(prompts["SV"]) == 3
    for session, (sd_prompt, sv_prompt) in enumerate(
        zip(prompts["SD"], prompts["SV"], strict=True),
        start=1,
    ):
        differing_lines = [
            (line_number, sd_line, sv_line)
            for line_number, (sd_line, sv_line) in enumerate(
                zip(sd_prompt.splitlines(), sv_prompt.splitlines(), strict=True),
                start=1,
            )
            if sd_line != sv_line
        ]
        assert not differing_lines, "\n".join(
            [f"session={session}"]
            + [
                f"line={line_number}\nSD={sd_line}\nSV={sv_line}"
                for line_number, sd_line, sv_line in differing_lines
            ]
        )


def test_offline_sv_trajectory_verifies_current_constraints_and_emits_ceiling(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    corpus = _load_corpus(DEFAULT_CORPUS)
    variant = _variant(corpus, 0)
    calls = 0

    def fake_post(_base_url: str, _path: str, payload: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        prompt = str(payload["prompt"])
        session = next(number for number in (1, 2, 3) if f"SESSION: {number} of 3" in prompt)
        return {
            "response": json.dumps(
                {
                    "changes": _expected(variant, session=session),
                    "analysis_summary": "checked public constraints",
                    "hypothesis": "apply exact evidence",
                    "evidence_used": [f"session-{session}"],
                    "uncertainty": "none",
                    "next_action": "save candidate",
                    "confidence": 1.0,
                }
            ),
            "prompt_eval_count": 10,
            "eval_count": 5,
        }

    monkeypatch.setattr(evaluation, "_post", fake_post)
    raw_sessions = tmp_path / "sessions.jsonl"
    trajectory = evaluation._trajectory(
        corpus=corpus,
        variant=variant,
        condition="SV",
        model_url="http://127.0.0.1:11434",
        raw_sessions=raw_sessions,
        attempt=1,
    )

    records = evaluation._read_jsonl(raw_sessions)
    assert calls == 3
    assert [record["model_call_count"] for record in records] == [1, 1, 1]
    diagnostic = trajectory["deterministic_ceiling_diagnostic"]
    assert isinstance(diagnostic, dict)
    assert diagnostic["model_call_count"] == 0
    assert diagnostic["hidden_test_accuracy_after"] == 1.0


def test_live_session_artifact_never_stores_prompts_response_bodies_or_reasoning(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    corpus = _load_corpus(DEFAULT_CORPUS)
    prompt_marker = "private prompt marker 78c1"
    reasoning_marker = "private reasoning marker 1da9"
    corpus["sessions"][0]["ticket_template"] += f" {prompt_marker}"

    def fake_post(_base_url: str, _path: str, _payload: dict[str, object]) -> dict[str, object]:
        return {
            "response": json.dumps(
                {
                    "changes": {},
                    "analysis_summary": reasoning_marker,
                    "hypothesis": reasoning_marker,
                    "evidence_used": [],
                    "uncertainty": "none",
                    "next_action": "none",
                    "confidence": 1.0,
                }
            ),
            "prompt_eval_count": 10,
            "eval_count": 5,
        }

    monkeypatch.setattr(evaluation, "_post", fake_post)
    raw_sessions = tmp_path / "sessions.jsonl"
    evaluation._trajectory(
        corpus=corpus,
        variant=_variant(corpus, 0),
        condition="SD",
        model_url="http://127.0.0.1:11434",
        raw_sessions=raw_sessions,
        attempt=1,
    )

    encoded = raw_sessions.read_text(encoding="utf-8")
    assert prompt_marker not in encoded
    assert reasoning_marker not in encoded
    assert all(
        {"prompt", "response_text", "parsed_response"}.isdisjoint(record)
        for record in evaluation._read_jsonl(raw_sessions)
    )
