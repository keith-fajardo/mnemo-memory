"""Shape checks for the held-out synthetic typed-decision fixture."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parents[1] / "fixtures/evals"
HOLDOUT = FIXTURES / "typed-decision-holdout-v1.json"
EXISTING = (
    "automatic-context-routing-v1.json",
    "typed-decision-v1.json",
    "viability-corpus-v1.json",
)


def _holdout() -> dict[str, Any]:
    value = json.loads(HOLDOUT.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _strings(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return set().union(*(_strings(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_strings(item) for item in value))
    return set()


def test_holdout_declares_synthetic_provenance() -> None:
    value = _holdout()
    assert value["schema_version"] == 1
    assert value["fixture_kind"] == "synthetic-typed-decision-holdout"
    assert value["provenance"] == {
        "origin": "Mnemo-owned original synthetic prompts",
        "competing_product_artifacts_used": False,
    }


def test_front_door_cases_are_balanced_and_unique() -> None:
    cases = _holdout()["front_door_cases"]
    assert Counter(case["expected_route"] for case in cases) == {
        "prior_memory": 10,
        "knowledge": 10,
        "structure": 10,
        "none": 10,
    }
    assert len({case["id"] for case in cases}) == 40
    assert len({case["prompt"] for case in cases}) == 40
    assert all(case["prompt"].strip() for case in cases)


def test_notes_are_split_and_unique() -> None:
    notes = _holdout()["notes"]
    assert Counter(note["category"] for note in notes) == {"relevant": 16, "noise": 8}
    assert len({note["id"] for note in notes}) == 24
    assert len({note["summary"] for note in notes}) == 24
    assert not any("Background conversation" in note["summary"] for note in notes)


def test_no_holdout_text_appears_in_the_existing_fixtures() -> None:
    existing: set[str] = set()
    for name in EXISTING:
        existing |= _strings(json.loads((FIXTURES / name).read_text(encoding="utf-8")))
    holdout = _holdout()
    texts = {case["prompt"] for case in holdout["front_door_cases"]}
    texts |= {note["summary"] for note in holdout["notes"]}
    assert not texts & existing


def _tokens(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9]+", text.lower()))


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    return len(left & right) / len(left | right) if left | right else 0.0


def test_no_holdout_prompt_is_a_near_duplicate_of_a_dev_prompt() -> None:
    existing: set[str] = set()
    for name in EXISTING:
        existing |= _strings(json.loads((FIXTURES / name).read_text(encoding="utf-8")))
    holdout = _holdout()
    texts = [case["prompt"] for case in holdout["front_door_cases"]]
    texts += [note["summary"] for note in holdout["notes"]]
    worst = max(
        (
            (_jaccard(_tokens(text), _tokens(other)), text, other)
            for text in texts
            for other in existing
        ),
        key=lambda item: item[0],
    )
    assert worst[0] < 0.5, f"near-duplicate (jaccard {worst[0]:.2f}): {worst[1]!r} ~ {worst[2]!r}"


def test_at_least_half_of_the_relevant_notes_have_no_category_prefix() -> None:
    prefixes = ("goal:", "failure:", "decision:", "open question:", "next:", "result:", "do not")
    relevant = [n["summary"] for n in _holdout()["notes"] if n["category"] == "relevant"]
    plain = [text for text in relevant if not text.lower().startswith(prefixes)]
    assert len(plain) >= 8
    assert (
        sum(any(w in text.lower() for w in ("do not", "never", "must not")) for text in relevant)
        >= 3
    )
