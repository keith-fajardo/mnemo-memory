import json
from pathlib import Path
from typing import Any, cast

REPOSITORY_ROOT = Path(__file__).parents[2]
FIXTURE = REPOSITORY_ROOT / "tests/fixtures/evals/golden-workflows.json"
EXPECTED_CATEGORIES = {"episodic", "knowledge", "procedural", "project"}
QUESTION_FIELDS = {
    "id",
    "category",
    "prompt",
    "scope",
    "expected_sources",
    "expected_result",
    "must_not_include",
}
SCOPE_FIELDS = {"owner_id", "workspace_id", "project_id"}


def load_fixture() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(FIXTURE.read_text()))


def test_golden_fixture_has_required_baseline_coverage() -> None:
    fixture = load_fixture()
    workflows = cast(list[dict[str, Any]], fixture["workflows"])
    questions = [question for workflow in workflows for question in workflow["questions"]]

    assert fixture["schema_version"] == 1
    assert fixture["fixture_kind"] == "specification-only"
    assert len(workflows) >= 10
    assert len(questions) >= 50
    assert {question["category"] for question in questions} == EXPECTED_CATEGORIES


def test_every_golden_question_has_unique_id_scope_and_evidence() -> None:
    workflows = cast(list[dict[str, Any]], load_fixture()["workflows"])
    workflow_ids = [str(workflow["id"]) for workflow in workflows]
    question_ids: list[str] = []

    for workflow in workflows:
        assert set(workflow["scope"]) == SCOPE_FIELDS
        assert workflow["completion_criteria"]
        assert workflow["fixture_inputs"]
        assert len(workflow["questions"]) >= 5

        for question in workflow["questions"]:
            assert set(question) == QUESTION_FIELDS
            assert question["scope"] == workflow["scope"]
            assert question["expected_sources"]
            assert all(
                set(source) == {"source_id", "authority"}
                and source["source_id"]
                and source["authority"]
                for source in question["expected_sources"]
            )
            question_ids.append(str(question["id"]))

    assert len(workflow_ids) == len(set(workflow_ids))
    assert len(question_ids) == len(set(question_ids))


def test_golden_fixture_contains_only_synthetic_scope_ids() -> None:
    workflows = cast(list[dict[str, Any]], load_fixture()["workflows"])

    for workflow in workflows:
        scope = workflow["scope"]
        assert scope["owner_id"] == "owner-fixture"
        assert scope["workspace_id"] is None
        assert str(scope["project_id"]).startswith("project-")
