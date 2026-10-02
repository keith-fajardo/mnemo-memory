"""The synthetic hook replay: seeding, the child boundary and the §8.3 gates (no network)."""

from __future__ import annotations

import io
import json
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

import scripts.run_typed_decision_replay as replay_cli
from scripts.typed_decision_evaluation import relevance_notes
from scripts.typed_decision_replay import (
    ReplayCase,
    ReplayChildError,
    ReplayRequest,
    ReplayResult,
    ReplaySeed,
    find_case,
    replay_cases,
    replay_notes,
    run_case_in_fresh_process,
    run_replay,
    run_replay_case,
    score_replay,
    seed_replay_set,
)
from scripts.typed_decision_test_support import (
    FAKE_TYPESAFE_KEY,
    ScriptedJevTransport,
    choice_answer,
)

MEMORY_LABEL = {
    "prior_memory": "past_sessions",
    "knowledge": "project_docs",
    "structure": "code_structure",
    "none": "nothing",
}


class ReplayOracle:
    """Answer from fixture labels: expected memory route, expected skill, noise is filler."""

    def __init__(self) -> None:
        self.calls = 0
        cases = replay_cases()
        self.routes = {case.prompt: case.expected_route or "knowledge" for case in cases}
        self.skills = {case.prompt: case.expected_skill or "none" for case in cases}
        self.noise = tuple(
            note.summary
            for set_name in ("dev", "holdout")
            for note in replay_notes(set_name)
            if note.category == "noise"
        )

    def __call__(self, url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> bytes:
        self.calls += 1
        request = json.loads(body)
        state = request["state"]
        answers: dict[str, object] = {}
        for name, question in request["questions"].items():
            labels = list(question["criteria"])
            if name == "memory_need":
                label = MEMORY_LABEL[self.routes.get(state, "none")]
            elif name == "note_substance":
                noise = any(summary in state for summary in self.noise)
                label = "filler" if noise else "task_information"
            elif name == "skill_pick":
                label = self.skills.get(state, "none")
            elif name == "complexity":
                label = "heavy"
            else:
                label = "edit"
            answers[name] = choice_answer(labels, label, 0.9)
        return json.dumps(
            {"model": "jev-1.13.0", "answers": answers, "usage": {"input_tokens": 10}}
        ).encode()


def test_replay_cases_come_only_from_fixtures_and_are_unique() -> None:
    cases = replay_cases()
    groups = {(case.set_name, case.group) for case in cases}
    assert groups == {
        ("dev", "memory"),
        ("dev", "skill"),
        ("dev", "tier"),
        ("dev", "notes"),
        ("holdout", "memory"),
        ("holdout", "skill"),
        ("holdout", "notes"),
    }
    counts = {
        group: sum((case.set_name, case.group) == group for case in cases) for group in groups
    }
    assert counts[("dev", "memory")] == 60 and counts[("holdout", "memory")] == 40
    assert counts[("dev", "skill")] == 40 and counts[("holdout", "skill")] == 24
    assert counts[("dev", "tier")] == 40
    assert counts[("dev", "notes")] == 14 and counts[("holdout", "notes")] == 5
    keys = [(case.set_name, case.group, case.case_id) for case in cases]
    assert len(keys) == len(set(keys))
    with pytest.raises(ValueError, match="synthetic fixture"):
        find_case("dev", "memory", "not-a-fixture-case")


def test_dev_replay_notes_are_the_phase_one_relevance_notes() -> None:
    phase_one = [note for notes in relevance_notes().values() for note in notes]
    replayed = [(note.note_id, note.summary, note.category) for note in replay_notes("dev")]
    assert replayed == phase_one
    assert len({note_id for note_id, _, _ in replayed}) == len(replayed) == 69


def test_seed_maps_every_note_and_event_to_its_category(tmp_path: Path) -> None:
    seed = seed_replay_set(tmp_path, "holdout")
    knowledge = [key for key in seed.categories if key.startswith("knowledge:")]
    events = [key for key in seed.categories if key.startswith("approved-episodic:")]
    assert len(knowledge) == len(replay_notes("holdout")) == 24
    assert len(events) == 4
    assert set(seed.categories.values()) == {"relevant", "noise"}
    assert (seed.data_directory / "settings.json").exists()


def _in_process(oracle: ReplayOracle) -> Callable[[ReplayRequest], ReplayResult]:
    def run(request: ReplayRequest) -> ReplayResult:
        return run_replay_case(
            request, environ={"TYPESAFE_API_KEY": FAKE_TYPESAFE_KEY}, jev_transport=oracle
        )

    return run


def test_in_process_replay_runs_both_arms_and_scores_them(tmp_path: Path) -> None:
    seed = seed_replay_set(tmp_path, "holdout")
    all_cases = [case for case in replay_cases() if case.set_name == "holdout"]
    subset = (
        [case for case in all_cases if case.group == "memory"][:4]
        + [case for case in all_cases if case.group == "skill"][:3]
        + [case for case in all_cases if case.group == "notes"][:1]
    )
    oracle = ReplayOracle()
    results = run_replay({"holdout": seed}, subset, _in_process(oracle))
    assert len(results) == 2 * len(subset)
    by_key = {(result.case_id, result.arm): result for result in results}

    for case in subset:
        rules = by_key[(case.case_id, "rules")]
        assert rules.checked_item_ids == () and rules.cap_hit is False
    for case in (case for case in subset if case.group == "skill"):
        assert by_key[(case.case_id, "typed")].skill_pick == case.expected_skill
    notes = by_key[(subset[-1].case_id, "typed")]
    assert notes.checked_item_ids
    for item_id in notes.checked_item_ids:
        category = next(
            (value for prefix, value in seed.categories.items() if item_id.startswith(prefix)),
            None,
        )
        if category is None:
            continue  # a skill document matched the probe; it is not a scored note
        assert (item_id in notes.dropped_item_ids) == (category == "noise")
    assert set(notes.applied_drop_item_ids) <= set(notes.dropped_item_ids)

    report = score_replay(subset, results, {"holdout": seed})
    assert set(report["sets"]) == {"holdout"}
    assert report["sets"]["holdout"]["filler"]["relevant_dropped"] == 0
    assert isinstance(report["complete"], bool)
    assert oracle.calls > 0


def test_skill_pick_scores_direct_keyword_discovery_when_jev_is_unsure(tmp_path: Path) -> None:
    """The hook skips keyword discovery on ``skill-01`` (ADR 0046 suppresses it) and ``skill-08``
    (a hard route); both arms still score the same direct keyword answer on those prompts."""

    seed = seed_replay_set(tmp_path, "dev")
    unsure = ScriptedJevTransport()  # every answer sits below the 0.6 bar
    keyword = {"skill-01": "release-notes", "skill-08": "api-endpoint"}

    def run(request: ReplayRequest) -> ReplayResult:
        return run_replay_case(
            request, environ={"TYPESAFE_API_KEY": FAKE_TYPESAFE_KEY}, jev_transport=unsure
        )

    cases = [find_case("dev", "skill", case_id) for case_id in keyword]
    results = run_replay({"dev": seed}, cases, run)
    assert unsure.calls > 0
    assert {(result.case_id, result.arm): result.skill_pick for result in results} == {
        (case_id, arm): pick for case_id, pick in keyword.items() for arm in ("rules", "typed")
    }


def _result(
    case: ReplayCase,
    arm: str,
    *,
    tokens: int = 100,
    hook_ms: int = 100,
    cap_hit: bool = False,
    skill_pick: str = "none",
    checked: tuple[str, ...] = (),
    dropped: tuple[str, ...] = (),
    applied: tuple[str, ...] = (),
) -> ReplayResult:
    return ReplayResult(
        case.set_name,
        case.group,
        case.case_id,
        arm,
        tokens,
        hook_ms,
        None,
        cap_hit,
        "no",
        "yes",
        "heavy",
        skill_pick,
        checked,
        dropped,
        applied,
    )


def test_score_replay_applies_the_section_8_3_gates() -> None:
    skill = ReplayCase("dev", "skill", "s1", "p1", expected_skill="test-plan")
    none = ReplayCase("dev", "skill", "s2", "p2", expected_skill="none")
    notes = ReplayCase("dev", "notes", "n1", "p3")
    seed = ReplaySeed(
        "dev", Path("."), Path("."), {"knowledge:good:": "relevant", "knowledge:noise:": "noise"}
    )
    cases = [skill, none, notes]
    passing = [
        _result(skill, "rules", tokens=200, skill_pick="none"),
        _result(skill, "typed", tokens=100, hook_ms=500, skill_pick="test-plan"),
        _result(none, "rules", tokens=200, skill_pick="release-notes"),
        _result(none, "typed", tokens=100, hook_ms=500, skill_pick="none"),
        _result(notes, "rules", tokens=200),
        _result(
            notes,
            "typed",
            tokens=100,
            hook_ms=500,
            checked=("knowledge:good:r:section:0", "knowledge:noise:r:section:0"),
            dropped=("knowledge:noise:r:section:0",),
            applied=("knowledge:noise:r:section:0",),
        ),
    ]
    report = score_replay(cases, passing, {"dev": seed})
    assert report["gates"] == {
        "filler_dev": True,
        "skill_dev": True,
        "latency": True,
        "tokens": True,
    }
    assert report["complete"] is True
    filler = report["sets"]["dev"]["filler"]
    assert (filler["drops_judged"], filler["drops_applied"], filler["drops_not_applied"]) == (
        1,
        1,
        0,
    )

    slow = [*passing[:-1], replace_result(passing[-1], hook_ms=1_200, cap_hit=True)]
    failing = score_replay(cases, slow, {"dev": seed})
    assert failing["gates"]["latency"] is False
    assert failing["latency"]["added_hook_ms"]["max"] == 1_100

    leaky = [
        *passing[:-1],
        replace_result(passing[-1], dropped_item_ids=("knowledge:good:r:section:0",)),
    ]
    assert score_replay(cases, leaky, {"dev": seed})["gates"]["filler_dev"] is False

    costly = [
        replace_result(result, attached_tokens=300) if result.arm == "typed" else result
        for result in passing
    ]
    assert score_replay(cases, costly, {"dev": seed})["gates"]["tokens"] is False

    unchecked = [
        *passing[:-1],
        replace_result(passing[-1], checked_item_ids=(), dropped_item_ids=()),
    ]
    assert score_replay(cases, unchecked, {"dev": seed})["gates"]["filler_dev"] is False


def test_filler_gate_needs_every_seeded_note_checked() -> None:
    """A drop rate over a few checked notes says nothing about the notes never checked."""

    notes = ReplayCase("dev", "notes", "n1", "p3")
    seed = ReplaySeed(
        "dev",
        Path("."),
        Path("."),
        {
            "knowledge:good:": "relevant",
            "knowledge:noise:": "noise",
            "knowledge:unseen:": "noise",
            "approved-episodic:event": "relevant",
        },
    )
    results = [
        _result(notes, "rules", tokens=200),
        _result(
            notes,
            "typed",
            tokens=100,
            checked=("knowledge:good:r:section:0", "knowledge:noise:r:section:0"),
            dropped=("knowledge:noise:r:section:0",),
        ),
    ]
    report = score_replay([notes], results, {"dev": seed})
    filler = report["sets"]["dev"]["filler"]
    assert (filler["notes_seeded"], filler["notes_checked"]) == (3, 2)
    assert filler["gates"] == {
        "relevant_dropped": True,
        "filler_removed": True,
        "all_notes_checked": False,
    }
    assert report["gates"]["filler_dev"] is False


def replace_result(result: ReplayResult, **changes: object) -> ReplayResult:
    values = {**json.loads(result.to_json()), **changes}
    return ReplayResult.from_json(json.dumps(values))


def test_cli_refuses_without_authorization_or_key(tmp_path: Path) -> None:
    calls: list[ReplayRequest] = []

    def runner(request: ReplayRequest) -> ReplayResult:
        calls.append(request)
        raise AssertionError("no case may run without authorization")

    args = ["--run-id", "test-run", "--results-root", str(tmp_path)]
    assert (
        replay_cli.main(args, environ={"TYPESAFE_API_KEY": FAKE_TYPESAFE_KEY}, runner=runner) == 2
    )
    assert replay_cli.main([*args, "--live-calls-authorized"], environ={}, runner=runner) == 2
    assert replay_cli.main(["--run-id", "bad id", "--live-calls-authorized"], environ={}) == 2
    assert calls == [] and not (tmp_path / "test-run").exists()


def test_child_refuses_unknown_cases_and_unauthorized_typed_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    request = ReplayRequest("dev", "memory", "prior-01", "typed", str(tmp_path), str(tmp_path))
    unauthorized = replay_cli.main(
        ["--child"],
        environ={"TYPESAFE_API_KEY": FAKE_TYPESAFE_KEY},
        stdin=io.StringIO(json.dumps(_as_dict(request))),
        stdout=io.StringIO(),
    )
    assert unauthorized == 2
    no_key = replay_cli.main(
        ["--child", "--live-calls-authorized"],
        environ={},
        stdin=io.StringIO(json.dumps(_as_dict(request))),
        stdout=io.StringIO(),
    )
    assert no_key == 2
    unknown = _as_dict(request) | {"case_id": "not-a-fixture-case", "arm": "rules"}
    assert (
        replay_cli.main(
            ["--child"],
            environ={},
            stdin=io.StringIO(json.dumps(unknown)),
            stdout=io.StringIO(),
        )
        == 2
    )
    assert "replay case is not in a synthetic fixture" in capsys.readouterr().err
    with_prompt = _as_dict(request) | {"arm": "rules", "prompt": "Pick up the release work."}
    assert (
        replay_cli.main(
            ["--child"],
            environ={},
            stdin=io.StringIO(json.dumps(with_prompt)),
            stdout=io.StringIO(),
        )
        == 2
    )
    assert "replay child request is invalid" in capsys.readouterr().err


def test_child_reports_other_failures_generically_without_prompt_text(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    unbound = ReplayRequest("dev", "memory", "prior-01", "rules", str(tmp_path), str(tmp_path))
    stdout = io.StringIO()
    code = replay_cli.main(
        ["--child"], environ={}, stdin=io.StringIO(json.dumps(_as_dict(unbound))), stdout=stdout
    )
    error = capsys.readouterr().err
    assert code == 2 and stdout.getvalue() == ""
    assert "replay case failed" in error
    assert "synthetic fixture" not in error
    assert find_case("dev", "memory", "prior-01").prompt not in error


def _as_dict(request: ReplayRequest) -> dict[str, str]:
    return {
        "set_name": request.set_name,
        "group": request.group,
        "case_id": request.case_id,
        "arm": request.arm,
        "data_directory": request.data_directory,
        "project_directory": request.project_directory,
    }


def test_fresh_process_runner_runs_a_rules_case(tmp_path: Path) -> None:
    seed = seed_replay_set(tmp_path, "holdout")
    case = next(case for case in replay_cases() if case.set_name == "holdout")
    result = run_case_in_fresh_process(
        ReplayRequest(
            "holdout",
            case.group,
            case.case_id,
            "rules",
            str(seed.data_directory),
            str(seed.project_directory),
        ),
        live_calls_authorized=False,
    )
    assert (result.case_id, result.arm) == (case.case_id, "rules")
    assert result.hook_ms >= 0 and result.checked_item_ids == ()


def test_fresh_process_runner_raises_without_echoing_a_failed_child(tmp_path: Path) -> None:
    request = ReplayRequest(
        "holdout", "memory", "not-a-fixture-case", "rules", str(tmp_path), str(tmp_path)
    )
    with pytest.raises(ReplayChildError, match="not-a-fixture-case") as raised:
        run_case_in_fresh_process(request, live_calls_authorized=False)
    assert "refusing" not in str(raised.value)


def test_cli_writes_a_report_with_an_in_process_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    small = tuple(
        case
        for case in replay_cases()
        if case.case_id in {"prior-01", "skill-01", "notes-dev-b00", "h-prior-01", "light-01"}
    )
    monkeypatch.setattr(replay_cli, "replay_cases", lambda: small)
    oracle = ReplayOracle()
    code = replay_cli.main(
        ["--run-id", "test-run", "--results-root", str(tmp_path), "--live-calls-authorized"],
        environ={"TYPESAFE_API_KEY": FAKE_TYPESAFE_KEY},
        runner=_in_process(oracle),
    )
    report = json.loads((tmp_path / "test-run" / "report.json").read_text("utf-8"))
    assert code in {0, 1}
    assert report["run"] == {"run_id": "test-run", "prompts": 5, "requests": 10}
    assert set(report["gates"]) >= {"latency", "tokens", "tier"}
