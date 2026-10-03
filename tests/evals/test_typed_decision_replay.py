"""The synthetic hook replay: seeding, the child boundary and the §8.3 gates (no network)."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

import scripts.run_typed_decision_replay as replay_cli
from mnemo_memory.apps.cli import main as cli
from mnemo_memory.connectors.typesafe import jev_provider
from mnemo_memory.packages.application import (
    LocalConfig,
    PersonalSettings,
    PersonalSettingsStore,
    RecordApprovedEpisodicEvent,
    build_checkpoint_runtime,
)
from mnemo_memory.packages.application.automatic_memory import LocalMemoryProjectBindingStore
from mnemo_memory.packages.domain import ApprovedEventKind
from mnemo_memory.packages.storage import (
    MAXIMUM_NOTE_VERDICT_ATTEMPTS,
    LocalNoteJudgeQueue,
    LocalNoteVerdictCache,
)
from scripts.typed_decision_evaluation import relevance_notes
from scripts.typed_decision_replay import (
    NOTE_BATCH,
    SEED_MARKER,
    Primer,
    PrimingResult,
    ReplayCase,
    ReplayChildError,
    ReplayRefusedError,
    ReplayRequest,
    ReplayResult,
    ReplaySeed,
    evidence,
    find_case,
    prime_replay_seed,
    replay_cases,
    replay_notes,
    replay_request,
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
FAKE_ENVIRON = {"TYPESAFE_API_KEY": FAKE_TYPESAFE_KEY}
PRIMED = {"dev": PrimingResult("dev", 2, 2, False)}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Defense in depth: no replay test may reach the real Jev transport."""

    def refuse(url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> bytes:
        raise AssertionError("a replay test reached the network transport")

    monkeypatch.setattr(jev_provider, "_urllib_transport", refuse)


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
    assert NOTE_BATCH == 3
    assert counts[("dev", "notes")] == 23 and counts[("holdout", "notes")] == 8
    keys = [(case.set_name, case.group, case.case_id) for case in cases]
    assert len(keys) == len(set(keys))
    with pytest.raises(ValueError, match="synthetic fixture"):
        find_case("dev", "memory", "not-a-fixture-case")


def test_dev_replay_notes_are_the_phase_one_relevance_notes() -> None:
    phase_one = [note for notes in relevance_notes().values() for note in notes]
    replayed = [(note.note_id, note.summary, note.category) for note in replay_notes("dev")]
    assert replayed == phase_one
    assert len({note_id for note_id, _, _ in replayed}) == len(replayed) == 69


def test_seed_keeps_events_apart_from_notes_and_maps_every_category(tmp_path: Path) -> None:
    seed = seed_replay_set(tmp_path, "holdout")
    knowledge = [key for key in seed.categories if key.startswith("knowledge:")]
    events = [key for key in seed.categories if key.startswith("approved-episodic:")]
    assert len(knowledge) == len(replay_notes("holdout")) == 24
    assert len(events) == 4
    assert set(seed.categories.values()) == {"relevant", "noise"}
    assert (seed.data_directory / "settings.json").exists()
    settings = PersonalSettingsStore(seed.data_directory).load()
    # The seed never turns the typed step on, so nothing in the replay can start the judge.
    assert settings.experimental_typed_decisions_enabled is False
    assert (seed.data_directory / SEED_MARKER).exists()
    assert seed.project_directory != seed.notes_project_directory
    assert not (seed.notes_project_directory / "skills").exists()
    assert not (seed.project_directory / "notes").exists()
    probe = find_case("holdout", "notes", "notes-holdout-b00")
    assert replay_request(seed, probe, "typed").project_directory == str(
        seed.notes_project_directory
    )
    memory = find_case("holdout", "memory", "h-prior-01")
    assert replay_request(seed, memory, "rules").project_directory == str(seed.project_directory)


def _in_process(
    transport: Callable[[str, bytes, Mapping[str, str], float], bytes],
) -> Callable[[ReplayRequest], ReplayResult]:
    def run(request: ReplayRequest) -> ReplayResult:
        return run_replay_case(request, environ=FAKE_ENVIRON, jev_transport=transport)

    return run


def _primer(transport: Callable[[str, bytes, Mapping[str, str], float], bytes]) -> Primer:
    def prime(seed: ReplaySeed) -> PrimingResult:
        return prime_replay_seed(seed, environ=FAKE_ENVIRON, jev_transport=transport)

    return prime


@pytest.mark.parametrize("set_name", ["holdout", "dev"])
def test_every_seeded_note_is_checked_under_a_fake_that_answers_everything(
    tmp_path: Path, set_name: str
) -> None:
    """Spec 2026-10-03 §8: once primed, the fake-transport run passes every filler gate."""

    seed = seed_replay_set(tmp_path, set_name)
    probes = [case for case in replay_cases() if (case.set_name, case.group) == (set_name, "notes")]
    oracle = ReplayOracle()
    priming = prime_replay_seed(seed, environ=FAKE_ENVIRON, jev_transport=oracle)
    results = run_replay({set_name: seed}, probes, _in_process(oracle))
    typed = [result for result in results if result.arm == "typed"]
    notes = {key for key in seed.categories if key.startswith("knowledge:")}
    checked = {
        key
        for result in typed
        for item_id in result.checked_item_ids
        for key in notes
        if item_id.startswith(key)
    }
    assert {result.front_door_outcome for result in typed} == {"answered"}
    assert checked == notes
    filler = score_replay(probes, results, {set_name: seed}, priming={set_name: priming})["sets"][
        set_name
    ]["filler"]
    assert filler["noise_checks"] > 0 and filler["drops_applied"] > 0
    assert filler["gates"] == dict.fromkeys(
        (
            "relevant_dropped",
            "filler_removed",
            "all_notes_checked",
            "answered_share",
            "priming_answered_share",
        ),
        True,
    )


def test_in_process_replay_runs_both_arms_and_scores_them(tmp_path: Path) -> None:
    seed = seed_replay_set(tmp_path, "holdout")
    all_cases = [case for case in replay_cases() if case.set_name == "holdout"]
    hard_rule = find_case("holdout", "memory", "h-structure-04")
    subset = [
        *[case for case in all_cases if case.group == "memory"][:4],
        hard_rule,
        *[case for case in all_cases if case.group == "skill"][:3],
        find_case("holdout", "notes", "notes-holdout-b05"),  # one relevant note, two noise
    ]
    oracle = ReplayOracle()
    priming = prime_replay_seed(seed, environ=FAKE_ENVIRON, jev_transport=oracle)
    results = run_replay({"holdout": seed}, subset, _in_process(oracle))
    assert len(results) == 2 * len(subset)
    by_key = {(result.case_id, result.arm): result for result in results}

    for case in subset:
        rules = by_key[(case.case_id, "rules")]
        assert rules.checked_item_ids == () and rules.cap_hit is False
        assert rules.front_door_outcome is None and rules.hard_rule is None
        assert by_key[(case.case_id, "typed")].front_door_outcome == "answered"
    assert by_key[(hard_rule.case_id, "typed")].hard_rule is True
    for case in (case for case in subset if case.group == "skill"):
        assert by_key[(case.case_id, "typed")].skill_pick == case.expected_skill
    notes = by_key[(subset[-1].case_id, "typed")]
    assert notes.checked_item_ids
    for item_id in notes.checked_item_ids:
        category = next(
            (value for prefix, value in seed.categories.items() if item_id.startswith(prefix)),
            None,
        )
        assert category is not None  # the notes project holds only seeded notes
        assert (item_id in notes.dropped_item_ids) == (category == "noise")
    assert notes.dropped_item_ids  # the cached noise verdicts really drop notes
    assert set(notes.applied_drop_item_ids) <= set(notes.dropped_item_ids)
    assert notes.notes_cached == len(notes.checked_item_ids)

    report = score_replay(subset, results, {"holdout": seed}, priming={"holdout": priming})
    holdout = report["sets"]["holdout"]
    assert set(report["sets"]) == {"holdout"}
    assert holdout["filler"]["relevant_dropped"] == 0
    assert holdout["filler"]["gates"]["priming_answered_share"] is True
    assert holdout["front_door_outcomes"] == {"answered": len(subset)}
    assert holdout["memory_hard_rule"]["prompts"] == 1
    assert holdout["memory_typed"]["cases"] == 4
    assert report["gates"]["steps"] is True
    assert isinstance(report["complete"], bool)
    assert oracle.calls > 0


def test_skill_pick_scores_direct_keyword_discovery_when_jev_is_unsure(tmp_path: Path) -> None:
    """The hook skips keyword discovery on ``skill-01`` (ADR 0046 suppresses it) and ``skill-08``
    (a hard route); both arms still score the same direct keyword answer on those prompts."""

    seed = seed_replay_set(tmp_path, "dev")
    unsure = ScriptedJevTransport()  # every answer sits below the 0.6 bar
    keyword = {"skill-01": "release-notes", "skill-08": "api-endpoint"}
    cases = [find_case("dev", "skill", case_id) for case_id in keyword]
    results = run_replay({"dev": seed}, cases, _in_process(unsure))
    assert unsure.calls > 0
    assert {(result.case_id, result.arm): result.skill_pick for result in results} == {
        (case_id, arm): pick for case_id, pick in keyword.items() for arm in ("rules", "typed")
    }


def test_a_typed_step_error_is_reported_and_not_scored_as_jev(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hook falls back to the rules after the observer saw Jev's answers: nothing counts."""

    seed = seed_replay_set(tmp_path, "dev")
    oracle = ReplayOracle()
    skill = find_case("dev", "skill", "skill-02")  # Jev says release-notes; keywords say none
    probe = find_case("dev", "notes", "notes-dev-b00")
    rules = [_in_process(oracle)(replay_request(seed, case, "rules")) for case in (skill, probe)]

    def fail(*args: object, **kwargs: object) -> object:
        raise RuntimeError("apply failed")

    monkeypatch.setattr(cli, "_apply_typed_decisions", fail)
    typed = [_in_process(oracle)(replay_request(seed, case, "typed")) for case in (skill, probe)]
    assert oracle.calls > 0
    for result in typed:
        assert result.front_door_outcome == "typed_step_error"
        assert (result.checked_item_ids, result.dropped_item_ids) == ((), ())
    assert typed[0].skill_pick == "none"

    report = score_replay([skill, probe], [*rules, *typed], {"dev": seed})
    dev = report["sets"]["dev"]
    assert dev["front_door_outcomes"] == {"typed_step_error": 2}
    assert dev["skill"]["step_error_prompts"] == 1 and dev["skill"]["skill_prompts"] == 0
    assert dev["filler"]["notes_checked"] == 0
    assert report["steps"]["step_errors"] == 2
    assert report["gates"]["steps"] is False


def _result(
    case: ReplayCase,
    arm: str,
    *,
    tokens: int = 100,
    hook_ms: int = 100,
    cap_hit: bool = False,
    structural: str = "no",
    long_term: str = "yes",
    skill_pick: str = "none",
    checked: tuple[str, ...] = (),
    dropped: tuple[str, ...] = (),
    applied: tuple[str, ...] = (),
    outcome: str | None = "answered",
    hard_rule: bool | None = False,
    unanswered: int = 0,
    skill: str = "agreed",
    cached: int = 0,
) -> ReplayResult:
    typed = arm == "typed"
    return ReplayResult(
        case.set_name,
        case.group,
        case.case_id,
        arm,
        tokens,
        hook_ms,
        None,
        cap_hit,
        structural,
        long_term,
        "heavy",
        skill_pick,
        checked,
        dropped,
        applied,
        outcome if typed else None,
        hard_rule if typed else None,
        unanswered if typed else 0,
        skill if typed else None,
        notes_cached=cached if typed else 0,
    )


def _seed(categories: Mapping[str, str]) -> ReplaySeed:
    return ReplaySeed("dev", Path("."), Path("."), Path("."), categories)


def test_score_replay_applies_the_section_8_3_gates() -> None:
    skill = ReplayCase("dev", "skill", "s1", "p1", expected_skill="test-plan")
    none = ReplayCase("dev", "skill", "s2", "p2", expected_skill="none")
    notes = ReplayCase("dev", "notes", "n1", "p3")
    seeds = {"dev": _seed({"knowledge:good:": "relevant", "knowledge:noise:": "noise"})}
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
            cached=2,
        ),
    ]
    report = score_replay(cases, passing, seeds, priming=PRIMED)
    assert report["gates"] == {
        "filler_dev": True,
        "skill_dev": True,
        "steps": True,
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
    assert filler["priming"] == {"asked": 2, "answered": 2, "share": 1.0, "blocked": False}

    slow = [*passing[:-1], replace_result(passing[-1], hook_ms=1_200, cap_hit=True)]
    failing = score_replay(cases, slow, seeds, priming=PRIMED)
    assert failing["gates"]["latency"] is False
    assert failing["latency"]["added_hook_ms"]["max"] == 1_100

    leaky = [
        *passing[:-1],
        replace_result(passing[-1], dropped_item_ids=("knowledge:good:r:section:0",)),
    ]
    assert score_replay(cases, leaky, seeds, priming=PRIMED)["gates"]["filler_dev"] is False

    costly = [
        replace_result(result, attached_tokens=300) if result.arm == "typed" else result
        for result in passing
    ]
    assert score_replay(cases, costly, seeds, priming=PRIMED)["gates"]["tokens"] is False

    unchecked = [
        *passing[:-1],
        replace_result(passing[-1], checked_item_ids=(), dropped_item_ids=(), notes_cached=0),
    ]
    assert score_replay(cases, unchecked, seeds, priming=PRIMED)["gates"]["filler_dev"] is False

    uncached = [*passing[:-1], replace_result(passing[-1], notes_cached=1)]
    filler = score_replay(cases, uncached, seeds, priming=PRIMED)["sets"]["dev"]["filler"]
    assert filler["answered"] == {"asked": 2, "answered": 1, "share": 0.5}
    assert filler["gates"]["answered_share"] is False

    unprimed = score_replay(cases, passing, seeds)
    assert unprimed["sets"]["dev"]["filler"]["priming"] == {
        "asked": 0,
        "answered": 0,
        "share": 0.0,
    }
    assert unprimed["gates"]["filler_dev"] is False  # never primed is never evidence
    weak = score_replay(cases, passing, seeds, priming={"dev": PrimingResult("dev", 20, 18, False)})
    assert weak["sets"]["dev"]["filler"]["gates"]["priming_answered_share"] is False

    skipped = [
        replace_result(result, skill_comparison="skipped") if result.arm == "typed" else result
        for result in passing
    ]
    skill_section = score_replay(cases, skipped, seeds, priming=PRIMED)["sets"]["dev"]["skill"]
    assert skill_section["answered"] == {"asked": 0, "answered": 0, "share": 0.0}
    assert skill_section["gates"]["answered_share"] is False  # never asked is never evidence


def test_filler_gate_needs_every_seeded_note_checked() -> None:
    """A drop rate over a few checked notes says nothing about the notes never checked."""

    notes = ReplayCase("dev", "notes", "n1", "p3")
    seed = _seed(
        {
            "knowledge:good:": "relevant",
            "knowledge:noise:": "noise",
            "knowledge:unseen:": "noise",
            "approved-episodic:event": "relevant",
        }
    )
    results = [
        _result(notes, "rules", tokens=200),
        _result(
            notes,
            "typed",
            tokens=100,
            checked=("knowledge:good:r:section:0", "knowledge:noise:r:section:0"),
            dropped=("knowledge:noise:r:section:0",),
            cached=2,
        ),
    ]
    report = score_replay([notes], results, {"dev": seed}, priming=PRIMED)
    filler = report["sets"]["dev"]["filler"]
    assert (filler["notes_seeded"], filler["notes_checked"]) == (3, 2)
    assert (filler["events_seeded"], filler["events_checked"]) == (1, 0)
    assert filler["gates"] == {
        "relevant_dropped": True,
        "filler_removed": True,
        "all_notes_checked": False,
        "answered_share": True,
        "priming_answered_share": True,
    }
    assert report["gates"]["filler_dev"] is False


def test_memory_gates_count_only_prompts_jev_was_asked() -> None:
    """A hard-rule prompt is a rules finding (spec §4.1), never a Jev miss."""

    asked = ReplayCase("dev", "memory", "asked", "p1", expected_route="prior_memory")
    preempted = ReplayCase("dev", "memory", "preempted", "p2", expected_route="structure")
    results = [
        _result(asked, "rules", structural="no", long_term="unknown"),
        _result(asked, "typed", structural="no", long_term="yes"),
        _result(preempted, "rules", structural="no", long_term="no"),
        _result(preempted, "typed", structural="no", long_term="no", hard_rule=True),
    ]
    dev = score_replay([asked, preempted], results, {"dev": _seed({})})["sets"]["dev"]
    assert dev["memory_typed"]["cases"] == dev["memory_rules"]["cases"] == 1
    assert dev["memory_typed"]["missed_case_ids"] == []
    assert dev["memory_hard_rule"] == {"prompts": 1, "mismatched_case_ids": ["preempted"]}


class _HttpErrors:
    """Every Jev call fails at the transport, so the guard records ``http_error``."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> bytes:
        self.calls += 1
        raise OSError("synthetic transport failure")


def test_a_run_jev_never_answers_fails_every_answered_share_gate(tmp_path: Path) -> None:
    seed = seed_replay_set(tmp_path, "dev")
    cases = [
        *(find_case("dev", "memory", case_id) for case_id in ("prior-01", "structure-01")),
        *(find_case("dev", "tier", f"heavy-{number:02d}") for number in (1, 2, 3)),
        find_case("dev", "skill", "skill-02"),
        find_case("dev", "notes", "notes-dev-b00"),
    ]
    failing = _HttpErrors()
    results = run_replay({"dev": seed}, cases, _in_process(failing))
    assert failing.calls > 0
    assert {result.front_door_outcome for result in results if result.arm == "typed"} == {
        "http_error"
    }

    report = score_replay(cases, results, {"dev": seed})
    dev = report["sets"]["dev"]
    for section in (dev["memory_typed"], dev["filler"], dev["skill"], report["tier"]):
        assert section["answered"]["asked"] > 0 and section["answered"]["share"] == 0.0
        assert section["gates"]["answered_share"] is False
    # The rules' fallback answers are right here; they must not count as Jev's.
    assert dev["memory_rules"]["accuracy"] == 1.0 and dev["memory_typed"]["accuracy"] == 0.0
    assert report["tier"]["heavy_recall"] == 0.0
    assert not any(report["gates"][name] for name in ("memory_dev", "tier", "skill_dev"))
    assert report["gates"]["filler_dev"] is False and report["complete"] is False


def _fallback_rows(cases: list[ReplayCase], outcome: str) -> list[ReplayResult]:
    """Both arms with each case's right needs (as the rules decided them), all one outcome."""

    needs: dict[str | None, tuple[str, str]] = {
        "prior_memory": ("no", "yes"),
        "structure": ("yes", "no"),
        "none": ("no", "no"),
        None: ("no", "yes"),  # a tier case
    }
    rows: list[ReplayResult] = []
    for case in cases:
        structural, long_term = needs[case.expected_route]
        rows.extend(
            _result(case, arm, structural=structural, long_term=long_term, outcome=outcome)
            for arm in ("rules", "typed")
        )
    return rows


def test_rules_fallback_answers_never_pass_jev_memory_or_tier_gates() -> None:
    """A prompt Jev did not answer scores as not answered; a timeout (the cap working as
    designed, gated by the cap share) keeps the hook's own result but is still no answer."""

    cases = [
        ReplayCase("dev", "memory", "prior", "p1", expected_route="prior_memory"),
        ReplayCase("dev", "memory", "structure", "p2", expected_route="structure"),
        ReplayCase("dev", "memory", "none", "p3", expected_route="none"),
        ReplayCase("dev", "tier", "heavy", "p4", expected_tier="heavy"),
    ]
    seeds = {"dev": _seed({})}
    answered = score_replay(cases, _fallback_rows(cases, "answered"), seeds)
    assert answered["gates"]["memory_dev"] is True and answered["gates"]["tier"] is True

    for outcome in ("http_error", "no_credential", "budget_denied", "schema_invalid"):
        degraded = score_replay(cases, _fallback_rows(cases, outcome), seeds)
        assert degraded["sets"]["dev"]["memory_typed"]["accuracy"] == 0.0, outcome
        assert degraded["tier"]["heavy_recall"] == 0.0, outcome
        assert degraded["gates"]["memory_dev"] is False and degraded["gates"]["tier"] is False

    capped = score_replay(cases, _fallback_rows(cases, "timeout"), seeds)
    memory = capped["sets"]["dev"]["memory_typed"]
    assert memory["accuracy"] == 1.0 and capped["tier"]["heavy_recall"] == 1.0
    assert memory["answered"]["share"] == 0.0 and capped["gates"]["memory_dev"] is False


def test_score_replay_counts_outcomes_and_fails_on_missing_or_failed_steps() -> None:
    timed_out = ReplayCase("dev", "memory", "m1", "p1", expected_route="prior_memory")
    answered = ReplayCase("dev", "memory", "m2", "p2", expected_route="prior_memory")
    results = [
        _result(timed_out, "rules"),
        _result(timed_out, "typed", outcome="timeout"),
        _result(answered, "rules"),
        _result(answered, "typed"),
    ]
    report = score_replay([timed_out, answered], results, {"dev": _seed({})})
    dev = report["sets"]["dev"]
    assert dev["front_door_outcomes"] == {"answered": 1, "timeout": 1}
    assert dev["memory_typed"]["unavailable"] == 1 and dev["memory_rules"]["unavailable"] == 0
    assert report["gates"]["steps"] is True

    missing = [*results[:-1], replace_result(results[-1], front_door_outcome=None)]
    report = score_replay([timed_out, answered], missing, {"dev": _seed({})})
    assert report["sets"]["dev"]["front_door_outcomes"] == {"missing": 1, "timeout": 1}
    assert report["steps"]["missing_outcomes"] == 1 and report["gates"]["steps"] is False


def replace_result(result: ReplayResult, **changes: object) -> ReplayResult:
    values = {**json.loads(result.to_json()), **changes}
    return ReplayResult.from_json(json.dumps(values))


def _no_marker(seed: ReplaySeed) -> None:
    (seed.data_directory / SEED_MARKER).unlink()


def _other_digest(seed: ReplaySeed) -> None:
    path = seed.data_directory / SEED_MARKER
    marker = json.loads(path.read_text("utf-8"))
    path.write_text(json.dumps(marker | {"digest": "sha256:" + "0" * 64}), "utf-8")


def _other_set(seed: ReplaySeed) -> None:
    path = seed.data_directory / SEED_MARKER
    marker = json.loads(path.read_text("utf-8"))
    path.write_text(json.dumps(marker | {"set_name": "dev"}), "utf-8")


def _refresh_notes(seed: ReplaySeed) -> None:
    binding = LocalMemoryProjectBindingStore(seed.data_directory).get(seed.notes_project_directory)
    assert binding is not None
    cli._refresh_project_knowledge(seed.data_directory, binding)


def _extra_document(seed: ReplaySeed) -> None:
    (seed.notes_project_directory / "notes" / "extra.md").write_text(
        "# Personal\nNot a fixture note.\n", "utf-8"
    )
    _refresh_notes(seed)


def _changed_document(seed: ReplaySeed) -> None:
    note = next((seed.notes_project_directory / "notes" / "b00").glob("*.md"))
    note.write_text("# Replay batch b00 note\nNot the fixture text.\n", "utf-8")
    _refresh_notes(seed)


def _extra_event(seed: ReplaySeed) -> None:
    binding = LocalMemoryProjectBindingStore(seed.data_directory).get(seed.project_directory)
    assert binding is not None
    with build_checkpoint_runtime(LocalConfig.defaults(seed.data_directory)) as runtime:
        runtime.checkpoint_service.record_approved_event(
            RecordApprovedEpisodicEvent(
                binding.checkpoint_scope,
                ApprovedEventKind.DECISION,
                "Not a fixture note.",
                "replay:extra",
                (evidence("extra"),),
            )
        )


@pytest.mark.parametrize(
    ("alter", "case_id", "message"),
    [
        (_no_marker, "notes-holdout-b00", "no seed marker"),
        (_other_digest, "notes-holdout-b00", "does not match the fixtures"),
        (_other_set, "notes-holdout-b00", "does not match the request"),
        (_extra_document, "notes-holdout-b00", "document that was not seeded"),
        (_changed_document, "notes-holdout-b00", "document that was not seeded"),
        (_extra_event, "h-prior-01", "event that was not seeded"),
    ],
)
def test_typed_run_refuses_a_directory_that_is_not_exactly_the_seed(
    tmp_path: Path, alter: Callable[[ReplaySeed], None], case_id: str, message: str
) -> None:
    seed = seed_replay_set(tmp_path, "holdout")
    alter(seed)
    case = next(case for case in replay_cases() if case.case_id == case_id)
    transport = ScriptedJevTransport()
    with pytest.raises(ReplayRefusedError, match=message):
        run_replay_case(
            replay_request(seed, case, "typed"), environ=FAKE_ENVIRON, jev_transport=transport
        )
    assert transport.calls == 0


def test_typed_run_refuses_a_project_the_marker_does_not_name(tmp_path: Path) -> None:
    seed = seed_replay_set(tmp_path, "holdout")
    case = find_case("holdout", "memory", "h-prior-01")
    request = replay_request(seed, case, "typed")
    stray = ReplayRequest(
        request.set_name,
        request.group,
        request.case_id,
        request.arm,
        request.data_directory,
        str(tmp_path),
    )
    transport = ScriptedJevTransport()
    with pytest.raises(ReplayRefusedError, match="not a seeded project"):
        run_replay_case(stray, environ=FAKE_ENVIRON, jev_transport=transport)
    assert transport.calls == 0


def test_typed_run_without_a_test_transport_needs_authorization(tmp_path: Path) -> None:
    request = ReplayRequest("dev", "memory", "prior-01", "typed", str(tmp_path), str(tmp_path))
    with pytest.raises(ReplayRefusedError, match="needs authorization"):
        run_replay_case(request, environ=FAKE_ENVIRON)


def test_cli_refuses_without_authorization_or_key(tmp_path: Path) -> None:
    calls: list[ReplayRequest] = []

    def runner(request: ReplayRequest) -> ReplayResult:
        calls.append(request)
        raise AssertionError("no case may run without authorization")

    args = ["--run-id", "test-run", "--results-root", str(tmp_path)]
    assert replay_cli.main(args, environ=FAKE_ENVIRON, runner=runner) == 2
    assert replay_cli.main([*args, "--live-calls-authorized"], environ={}, runner=runner) == 2
    assert replay_cli.main(["--run-id", "bad id", "--live-calls-authorized"], environ={}) == 2
    assert calls == [] and not (tmp_path / "test-run").exists()


def _child(
    request: ReplayRequest | dict[str, str],
    *flags: str,
    environ: Mapping[str, str] | None = None,
) -> tuple[int, str]:
    value = request if isinstance(request, dict) else _as_dict(request)
    stdout = io.StringIO()
    code = replay_cli.main(
        ["--child", *flags],
        environ={} if environ is None else environ,
        stdin=io.StringIO(json.dumps(value)),
        stdout=stdout,
    )
    return code, stdout.getvalue()


def test_child_refuses_unknown_cases_and_unauthorized_typed_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    request = ReplayRequest("dev", "memory", "prior-01", "typed", str(tmp_path), str(tmp_path))
    assert _child(request, environ=FAKE_ENVIRON) == (2, "")
    assert _child(request, "--live-calls-authorized") == (2, "")
    unknown = _as_dict(request) | {"case_id": "not-a-fixture-case", "arm": "rules"}
    assert _child(unknown) == (2, "")
    assert "replay case is not in a synthetic fixture" in capsys.readouterr().err
    with_prompt = _as_dict(request) | {"arm": "rules", "prompt": "Pick up the release work."}
    assert _child(with_prompt) == (2, "")
    assert "replay child request is invalid" in capsys.readouterr().err


def test_child_refuses_an_unseeded_directory_with_a_fixed_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    unseeded = ReplayRequest("dev", "memory", "prior-01", "rules", str(tmp_path), str(tmp_path))
    assert _child(unseeded) == (2, "")
    assert "refusing: replay data directory has no seed marker" in capsys.readouterr().err


def test_child_reports_other_failures_by_type_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    prompt = find_case("dev", "memory", "prior-01").prompt

    def fail(request: ReplayRequest, **kwargs: object) -> ReplayResult:
        raise RuntimeError(prompt)

    monkeypatch.setattr(replay_cli, "run_replay_case", fail)
    request = ReplayRequest("dev", "memory", "prior-01", "rules", str(tmp_path), str(tmp_path))
    assert _child(request) == (2, "")
    error = capsys.readouterr().err
    assert "replay case failed (RuntimeError)" in error
    assert "synthetic fixture" not in error and prompt not in error


def test_child_reports_the_measured_hook_time_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cold typed import is already inside the timed hook call; nothing is added twice."""

    def fake(request: ReplayRequest, **kwargs: object) -> ReplayResult:
        return _result(find_case(request.set_name, request.group, request.case_id), request.arm)

    monkeypatch.setattr(replay_cli, "run_replay_case", fake)
    hook_ms = {}
    for arm in ("rules", "typed"):
        request = ReplayRequest("dev", "memory", "prior-01", arm, str(tmp_path), str(tmp_path))
        code, output = _child(request, "--live-calls-authorized", environ=FAKE_ENVIRON)
        assert code == 0
        hook_ms[arm] = ReplayResult.from_json(output).hook_ms
    assert hook_ms == {"rules": 100, "typed": 100}


_COLD_IMPORT_PROBE = """
import json
import sys
import tempfile
from pathlib import Path

import scripts.typed_decision_replay_child  # everything a child loads before its case runs

TYPED_PATH = (
    "asyncio",
    "mnemo_memory.apps.cli.typed_decision_composition",
    "mnemo_memory.apps.cli.typed_decision_hook",
    "mnemo_memory.connectors.typesafe",
    "mnemo_memory.packages.model_gateway",
)


def loaded():
    return sorted(name for name in sys.modules if name.startswith(TYPED_PATH))


at_import = loaded()
from mnemo_memory.apps.cli import main as cli
from scripts import typed_decision_replay as replay

seen = {}
hook = cli._automatic_prompt_context_for_hook


def timed(*args, **kwargs):
    seen["entry"] = loaded()
    try:
        return hook(*args, **kwargs)
    finally:
        seen["exit"] = "mnemo_memory.apps.cli.typed_decision_hook" in sys.modules


def unreachable(url, body, headers, timeout):
    raise OSError("tests never reach the network")


cli._automatic_prompt_context_for_hook = timed
with tempfile.TemporaryDirectory() as root:
    seed = replay.seed_replay_set(Path(root), "holdout")
    case = replay.find_case("holdout", "memory", "h-prior-01")
    replay.run_replay_case(
        replay.replay_request(seed, case, "typed"),
        environ={"TYPESAFE_API_KEY": "test-key-not-real-0000"},
        jev_transport=unreachable,
    )
print(json.dumps({"at_import": at_import, "entry": seen["entry"], "exit": seen["exit"]}))
"""


def test_a_child_imports_the_typed_step_cold_inside_the_timed_hook_call() -> None:
    """As in a real hook process, nothing of the typed path is loaded before the hook runs."""

    completed = subprocess.run(
        [sys.executable, "-c", _COLD_IMPORT_PROBE],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
        cwd=Path(__file__).parents[2],
    )
    seen = json.loads(completed.stdout.strip().splitlines()[-1])
    assert seen == {"at_import": [], "entry": [], "exit": True}


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
        replay_request(seed, case, "rules"), live_calls_authorized=False
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


def test_children_get_exactly_the_environment_they_are_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = find_case("dev", "memory", "prior-01")
    request = ReplayRequest("dev", "memory", "prior-01", "rules", str(tmp_path), str(tmp_path))
    seen: dict[str, Any] = {}

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.update(kwargs, command=command)
        return subprocess.CompletedProcess(command, 0, _result(case, "rules").to_json(), "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    run_case_in_fresh_process(request, live_calls_authorized=True, environ={"ONLY": "this"})
    assert seen["env"] == {"ONLY": "this"}
    assert seen["command"][1:] == [
        "-m",
        "scripts.typed_decision_replay_child",
        "--live-calls-authorized",
    ]
    monkeypatch.undo()

    forwarded: list[Mapping[str, str] | None] = []
    oracle = ReplayOracle()

    def fresh(request: ReplayRequest, **kwargs: Any) -> ReplayResult:
        forwarded.append(kwargs["environ"])
        assert kwargs["live_calls_authorized"] is True
        return run_replay_case(request, environ=FAKE_ENVIRON, jev_transport=oracle)

    small = (find_case("holdout", "memory", "h-prior-01"),)
    monkeypatch.setattr(replay_cli, "replay_cases", lambda: small)
    monkeypatch.setattr(replay_cli, "run_case_in_fresh_process", fresh)
    environ = FAKE_ENVIRON | {"MARK": "1"}
    args = ["--run-id", "env-run", "--results-root", str(tmp_path), "--live-calls-authorized"]
    assert replay_cli.main(args, environ=environ, primer=_primer(oracle)) in {0, 1}
    assert forwarded == [environ, environ]


def test_cli_writes_a_report_with_an_in_process_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    small = tuple(
        case
        for case in replay_cases()
        if case.case_id in {"prior-01", "skill-01", "notes-dev-b00", "h-prior-01", "light-01"}
    )
    monkeypatch.setattr(replay_cli, "replay_cases", lambda: small)
    code = replay_cli.main(
        ["--run-id", "test-run", "--results-root", str(tmp_path), "--live-calls-authorized"],
        environ=FAKE_ENVIRON,
        runner=_in_process(ReplayOracle()),
        primer=_primer(ReplayOracle()),
    )
    report = json.loads((tmp_path / "test-run" / "report.json").read_text("utf-8"))
    assert code in {0, 1}
    assert report["run"] == {"run_id": "test-run", "prompts": 5, "requests": 10}
    assert set(report["gates"]) >= {"latency", "tokens", "tier", "steps"}
    assert set(report["priming"]) == {"dev", "holdout"}
    assert report["priming"]["holdout"]["share"] == 1.0


def test_a_failing_child_is_recorded_and_the_report_is_still_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    small = (find_case("dev", "memory", "prior-01"), find_case("holdout", "memory", "h-prior-01"))
    monkeypatch.setattr(replay_cli, "replay_cases", lambda: small)
    in_process = _in_process(ReplayOracle())
    secret = find_case("dev", "memory", "prior-01").prompt

    def runner(request: ReplayRequest) -> ReplayResult:
        if (request.case_id, request.arm) == ("prior-01", "typed"):
            raise ReplayChildError(f"child failed: {secret}")
        return in_process(request)

    code = replay_cli.main(
        ["--run-id", "failed-run", "--results-root", str(tmp_path), "--live-calls-authorized"],
        environ=FAKE_ENVIRON,
        runner=runner,
        primer=_primer(ReplayOracle()),
    )
    output = tmp_path / "failed-run" / "report.json"
    report = json.loads(output.read_text("utf-8"))
    assert code == 1 and report["complete"] is False
    assert report["steps"]["case_failures"] == [
        {
            "set_name": "dev",
            "group": "memory",
            "case_id": "prior-01",
            "arm": "typed",
            "error_type": "ReplayChildError",
        }
    ]
    assert report["steps"]["gates"]["no_case_failures"] is False
    assert report["gates"]["steps"] is False
    assert set(report["sets"]) == {"holdout"}  # the failed case is left out of every score
    assert report["sets"]["holdout"]["memory_typed"]["cases"] == 1
    assert report["run"] == {"run_id": "failed-run", "prompts": 2, "requests": 4}
    assert secret not in output.read_text("utf-8")


def test_run_replay_records_a_real_child_failure_and_keeps_going(tmp_path: Path) -> None:
    """A typed child without the authorization flag refuses and exits; the run continues."""

    seed = seed_replay_set(tmp_path, "holdout")
    case = find_case("holdout", "memory", "h-prior-01")

    def fresh(request: ReplayRequest) -> ReplayResult:
        return run_case_in_fresh_process(request, live_calls_authorized=False)

    rules, typed = run_replay({"holdout": seed}, [case], fresh)
    assert (rules.arm, rules.error_type, rules.case_id) == ("rules", None, case.case_id)
    assert (typed.arm, typed.error_type, typed.case_id) == (
        "typed",
        "ReplayChildError",
        case.case_id,
    )
    steps = score_replay([case], [rules, typed], {"holdout": seed})["steps"]
    assert steps["gates"]["no_case_failures"] is False
    assert [failure["case_id"] for failure in steps["case_failures"]] == [case.case_id]


def test_priming_judges_every_seeded_note_and_never_the_pinned_one(tmp_path: Path) -> None:
    seed = seed_replay_set(tmp_path, "holdout")
    oracle = ReplayOracle()
    priming = prime_replay_seed(seed, environ=FAKE_ENVIRON, jev_transport=oracle)
    # 24 Markdown notes plus 3 of the 4 events: the pinned one is never sent; skills are not notes.
    assert (priming.notes, priming.answered, priming.blocked) == (27, 27, False)
    assert oracle.calls == 27
    assert LocalNoteVerdictCache(seed.data_directory).entry_count() == 27
    assert priming.to_dict() == {
        "notes": 27,
        "answered": 27,
        "share": 1.0,
        "blocked": False,
        "error_type": None,
    }


class _FailsEachNoteOnce:
    """Fail the first request about each distinct note at the transport, then answer."""

    def __init__(self, inner: ReplayOracle) -> None:
        self.inner = inner
        self.calls = 0
        self.seen: set[bytes] = set()

    def __call__(self, url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> bytes:
        self.calls += 1
        if body not in self.seen:
            self.seen.add(body)
            raise OSError("synthetic transport failure")
        return self.inner(url, body, headers, timeout)


def test_priming_retries_the_notes_a_pass_left_unanswered(tmp_path: Path) -> None:
    seed = seed_replay_set(tmp_path, "holdout")
    flaky = _FailsEachNoteOnce(ReplayOracle())
    priming = prime_replay_seed(seed, environ=FAKE_ENVIRON, jev_transport=flaky)
    assert (priming.notes, priming.answered, priming.blocked) == (27, 27, False)
    assert flaky.calls == 54  # one failed request and one retry per note
    assert LocalNoteVerdictCache(seed.data_directory).entry_count() == 27


def test_priming_stops_retrying_after_the_attempt_cap(tmp_path: Path) -> None:
    seed = seed_replay_set(tmp_path, "holdout")
    failing = _HttpErrors()
    priming = prime_replay_seed(seed, environ=FAKE_ENVIRON, jev_transport=failing)
    assert (priming.notes, priming.answered) == (27, 0)
    assert failing.calls == 27 * MAXIMUM_NOTE_VERDICT_ATTEMPTS


def test_a_seeded_note_the_reader_skips_still_counts_against_priming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The share is over the seeded notes, not over the notes the reader happened to return."""

    seed = seed_replay_set(tmp_path, "holdout")
    read = cli._note_candidates_by_id

    def drops_one(data: Path, scope: Any, item_ids: Any) -> Any:
        return read(data, scope, item_ids)[1:]

    monkeypatch.setattr(cli, "_note_candidates_by_id", drops_one)
    priming = prime_replay_seed(seed, environ=FAKE_ENVIRON, jev_transport=ReplayOracle())
    assert (priming.notes, priming.answered) == (27, 25)  # one skipped per project read


@pytest.mark.parametrize(
    ("alter", "message"),
    [
        (_no_marker, "no seed marker"),
        (_extra_document, "document that was not seeded"),
        (_extra_event, "event that was not seeded"),
    ],
)
def test_priming_refuses_a_directory_that_is_not_exactly_the_seed(
    tmp_path: Path, alter: Callable[[ReplaySeed], None], message: str
) -> None:
    seed = seed_replay_set(tmp_path, "holdout")
    alter(seed)
    transport = ScriptedJevTransport()
    with pytest.raises(ReplayRefusedError, match=message):
        prime_replay_seed(seed, environ=FAKE_ENVIRON, jev_transport=transport)
    assert transport.calls == 0


def test_priming_without_a_test_transport_needs_authorization(tmp_path: Path) -> None:
    seed = seed_replay_set(tmp_path, "holdout")
    with pytest.raises(ReplayRefusedError, match="needs authorization"):
        prime_replay_seed(seed, environ=FAKE_ENVIRON)


def test_a_jev_that_never_answers_fails_the_priming_gate(tmp_path: Path) -> None:
    seed = seed_replay_set(tmp_path, "holdout")
    failing = _HttpErrors()
    priming = prime_replay_seed(seed, environ=FAKE_ENVIRON, jev_transport=failing)
    assert failing.calls > 0 and (priming.notes, priming.answered) == (27, 0)
    probe = find_case("holdout", "notes", "notes-holdout-b00")
    results = run_replay({"holdout": seed}, [probe], _in_process(ReplayOracle()))
    report = score_replay([probe], results, {"holdout": seed}, priming={"holdout": priming})
    filler = report["sets"]["holdout"]["filler"]
    assert filler["priming"]["share"] == 0.0
    assert filler["gates"]["priming_answered_share"] is False


def test_the_prompt_path_sends_one_request_once_the_cache_is_warm(tmp_path: Path) -> None:
    seed = seed_replay_set(tmp_path, "holdout")
    oracle = ReplayOracle()
    prime_replay_seed(seed, environ=FAKE_ENVIRON, jev_transport=oracle)
    probe = find_case("holdout", "notes", "notes-holdout-b05")  # one relevant note, two noise
    before = oracle.calls
    result = _in_process(oracle)(replay_request(seed, probe, "typed"))
    assert oracle.calls - before == 1
    assert result.checked_item_ids
    assert result.notes_cached == len(result.checked_item_ids)
    assert result.notes_unanswered == 0
    assert result.dropped_item_ids  # a cached noise verdict drops a note on the prompt path
    assert set(result.dropped_item_ids) == {
        item_id
        for item_id in result.checked_item_ids
        for prefix, category in seed.categories.items()
        if item_id.startswith(prefix) and category == "noise"
    }


def test_the_replay_never_starts_the_background_judge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = seed_replay_set(tmp_path, "holdout")
    PersonalSettingsStore(seed.data_directory).save(
        PersonalSettings(
            experimental_semantic_memory_enabled=True, experimental_typed_decisions_enabled=True
        )
    )
    started: list[Path] = []
    monkeypatch.setattr(cli, "_judge_route_open", lambda settings: True)
    monkeypatch.setattr(cli, "_start_note_judge", started.append)
    probe = find_case("holdout", "notes", "notes-holdout-b00")
    result = _in_process(ReplayOracle())(replay_request(seed, probe, "typed"))  # cold cache
    assert result.checked_item_ids and result.notes_cached == 0
    assert LocalNoteJudgeQueue(seed.data_directory).length() == len(result.checked_item_ids)
    assert started == []


def test_a_priming_failure_is_recorded_and_the_run_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    small = (find_case("holdout", "notes", "notes-holdout-b00"),)
    monkeypatch.setattr(replay_cli, "replay_cases", lambda: small)

    def broken(seed: ReplaySeed) -> PrimingResult:
        raise RuntimeError("synthetic priming failure")

    code = replay_cli.main(
        ["--run-id", "unprimed", "--results-root", str(tmp_path), "--live-calls-authorized"],
        environ=FAKE_ENVIRON,
        runner=_in_process(ReplayOracle()),
        primer=broken,
    )
    report = json.loads((tmp_path / "unprimed" / "report.json").read_text("utf-8"))
    assert code == 1
    assert report["priming"]["holdout"] == {
        "notes": 0,
        "answered": 0,
        "share": 0.0,
        "blocked": True,
        "error_type": "RuntimeError",  # the class name only, never the message
    }
    assert "synthetic priming failure" not in json.dumps(report)
    assert report["sets"]["holdout"]["filler"]["gates"]["priming_answered_share"] is False
