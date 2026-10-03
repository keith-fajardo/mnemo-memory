"""Shape checks for the synthetic skill-pick fixtures (spec 2026-10-02 §8.3)."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from mnemo_memory.packages.model_gateway.decision_axes import skill_pick_axis
from scripts.typed_decision_evaluation import load_synthetic_fixture

FIXTURES = Path(__file__).parents[1] / "fixtures/evals"
SKILLS = FIXTURES / "typed-decision-skills-v1.json"
HOLDOUT = FIXTURES / "typed-decision-skills-holdout-v1.json"
OTHER_PROMPT_FIXTURES = (
    "automatic-context-routing-v1.json",
    "typed-decision-v1.json",
    "typed-decision-holdout-v1.json",
)
_NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}")
_TAG = re.compile(r"[a-z][a-z0-9_-]{0,31}")


def _prompts(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "prompt" and isinstance(item, str):
                found.add(item)
            else:
                found |= _prompts(item)
    elif isinstance(value, list):
        for item in value:
            found |= _prompts(item)
    return found


def test_both_fixtures_declare_synthetic_provenance() -> None:
    assert load_synthetic_fixture(SKILLS)["fixture_kind"] == "synthetic-typed-decision-skills"
    holdout = load_synthetic_fixture(HOLDOUT)
    assert holdout["fixture_kind"] == "synthetic-typed-decision-skills-holdout"
    assert holdout["skills_fixture"] == SKILLS.name


def test_dev_set_has_twelve_skills_and_forty_prompts_with_ten_needing_none() -> None:
    value = load_synthetic_fixture(SKILLS)
    skills = value["skills"]
    names = [skill["name"] for skill in skills]
    assert len(names) == len(set(names)) == 12
    assert all(_NAME.fullmatch(name) and name != "none" for name in names)
    assert all(1 <= len(skill["tags"]) <= 8 for skill in skills)
    assert all(_TAG.fullmatch(tag) for skill in skills for tag in skill["tags"])
    assert all(0 < len(skill["when"]) <= 500 for skill in skills)
    cases = value["cases"]
    assert len(cases) == 40
    assert len({case["id"] for case in cases}) == len({case["prompt"] for case in cases}) == 40
    counts = Counter(case["expected_skill"] for case in cases)
    assert counts["none"] >= 10
    assert set(counts) - {"none"} == set(names)


def test_holdout_has_twenty_four_new_prompts_with_six_needing_none() -> None:
    names = {skill["name"] for skill in load_synthetic_fixture(SKILLS)["skills"]}
    cases = load_synthetic_fixture(HOLDOUT)["cases"]
    assert len(cases) == 24
    assert len({case["id"] for case in cases}) == len({case["prompt"] for case in cases}) == 24
    counts = Counter(case["expected_skill"] for case in cases)
    assert counts["none"] >= 6
    assert set(counts) - {"none"} <= names


def test_skill_prompts_are_new_text() -> None:
    dev = _prompts(load_synthetic_fixture(SKILLS))
    holdout = _prompts(load_synthetic_fixture(HOLDOUT))
    assert not dev & holdout
    for name in OTHER_PROMPT_FIXTURES:
        other = _prompts(json.loads((FIXTURES / name).read_text(encoding="utf-8")))
        assert not (dev | holdout) & other, name


def test_skill_names_build_a_valid_skill_pick_axis() -> None:
    names = [skill["name"] for skill in load_synthetic_fixture(SKILLS)["skills"]]
    axis = skill_pick_axis(names)
    assert axis is not None
    assert axis.allowed_labels == (*names, "none")
