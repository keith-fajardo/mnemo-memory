"""Preregistered telehealth corpus, hidden grader, and paired analysis contracts."""

from scripts.run_long_horizon_evaluation import (
    _MNEMO,
    DEFAULT_CORPUS,
    _expected,
    _load_corpus,
    _memory_content,
    _valid_changes,
    _variant,
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
    config = {"timezone": "America/New_York"}

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
