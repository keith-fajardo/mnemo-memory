"""Preregistered telehealth corpus, hidden grader, and paired analysis contracts."""

from scripts.run_long_horizon_evaluation import (
    DEFAULT_CORPUS,
    _expected,
    _load_corpus,
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
