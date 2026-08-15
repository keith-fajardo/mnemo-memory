"""Contract checks for the preregistered live semantic evaluation fixture and scorers."""

from __future__ import annotations

from scripts.run_live_semantic_evaluation import (
    DEFAULT_FIXTURE,
    LiveEvaluationError,
    _validate_loopback_url,
    load_fixture,
    score_context,
    score_continuation,
)


def test_live_fixture_scores_only_preregistered_exact_values() -> None:
    fixture = load_fixture(DEFAULT_FIXTURE)
    required = fixture["context_assertions"]["required_exact"]
    content = "\n".join(required)
    context = score_context(content, fixture)
    assert context["critical_fidelity"] == 1.0
    assert context["critical_false_memory_count"] == 0

    continuation = score_continuation(
        {
            "authorization_requirement": "Must not write without scheduler authorization",
            "idempotency_key": "K-42",
            "expected_status": "status 409 remains uncertain",
            "timezone": "America/New_York",
            "uncertainty": "Uncertain whether the provider returns 409",
            "next_action": "Run uv run pytest -q within 90 seconds",
            "obsolete_decision_rejected": True,
        },
        fixture,
    )
    assert continuation["fidelity"] == 1.0
    assert continuation["all_required"] is True


def test_live_fixture_rejects_false_memory_and_missing_continuation_value() -> None:
    fixture = load_fixture(DEFAULT_FIXTURE)
    context = score_context("tenant 999", fixture)
    assert context["critical_fidelity"] == 0.0
    assert context["critical_false_memory_count"] == 1
    continuation = score_continuation({}, fixture)
    assert continuation["fidelity"] == 0.0
    assert continuation["all_required"] is False


def test_live_runner_permits_only_explicit_loopback_model_urls() -> None:
    assert _validate_loopback_url("http://127.0.0.1:11434/") == "http://127.0.0.1:11434"
    try:
        _validate_loopback_url("https://example.com")
    except LiveEvaluationError as error:
        assert "loopback" in str(error)
    else:
        raise AssertionError("non-loopback evaluation URL was accepted")
