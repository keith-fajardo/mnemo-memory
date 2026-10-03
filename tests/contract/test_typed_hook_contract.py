"""Shadow and off produce today's hook output byte for byte (spec 2026-10-02 §8.1)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from importlib import resources
from pathlib import Path

import pytest

from mnemo_memory.apps.cli import main as cli
from mnemo_memory.apps.cli.typed_decision_hook import FILLER_OMISSION_DETAIL, TypedHookModes
from mnemo_memory.packages.application import PersonalSettings, PersonalSettingsStore
from mnemo_memory.packages.domain import TypedDecisionMode
from mnemo_memory.packages.storage import (
    LocalNoteJudgeQueue,
    LocalNoteVerdictCache,
    NoteVerdictState,
)
from mnemo_memory.packages.telemetry import LocalAutomaticRouteTelemetryStore
from scripts.typed_decision_test_support import (
    KNOWLEDGE_PROMPT,
    ScriptedJevTransport,
    prime_note_verdicts,
    run_hook,
    seed_hook_fixture,
    synthetic_overrides,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "evals"
ROUTING = json.loads((FIXTURES / "automatic-context-routing-v1.json").read_text("utf-8"))
HOLDOUT = json.loads((FIXTURES / "typed-decision-holdout-v1.json").read_text("utf-8"))
PROMPTS: tuple[str, ...] = tuple(case["prompt"] for case in ROUTING["cases"]) + tuple(
    case["prompt"] for case in HOLDOUT["front_door_cases"]
)
SHADOW = TypedDecisionMode.SHADOW
ANSWER_EVERYTHING = {
    "memory_need": ("nothing", 0.95),
    "complexity": ("light", 0.95),
    "tool_need": ("read_heavy", 0.95),
    "skill_pick": ("test-plan", 0.95),
}


@pytest.mark.parametrize("semantic_gate", [False, True])
def test_shadow_output_is_byte_identical_to_off(tmp_path: Path, semantic_gate: bool) -> None:
    assert len(PROMPTS) == 100
    fixture = seed_hook_fixture(tmp_path, semantic_gate=semantic_gate)
    transport = ScriptedJevTransport(ANSWER_EVERYTHING)
    shadow = synthetic_overrides(fixture, transport, TypedHookModes(SHADOW, SHADOW, SHADOW, SHADOW))
    for prompt in PROMPTS:
        off = run_hook(fixture, prompt)
        seen = run_hook(fixture, prompt, shadow)
        assert (seen.context, seen.delivery_keys) == (off.context, off.delivery_keys), prompt
    # Every prompt asked at least the skill question, so shadow really received answers.
    assert transport.calls >= len(PROMPTS)
    events = LocalAutomaticRouteTelemetryStore(fixture.data).events(
        cli._automatic_route_scope(fixture.binding.checkpoint_scope), limit=100
    )
    typed = [event.typed for event in events if event.typed is not None]
    assert typed
    # A request that hits the 0.8 s cap under load reports timeout; it must stay rare.
    assert all(value.front_door_outcome in {"answered", "timeout"} for value in typed)
    answered = sum(value.front_door_outcome == "answered" for value in typed)
    assert answered / len(typed) >= 0.95


def test_off_never_enters_the_typed_step(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("the typed step must not run while every mode is off")

    baseline = [run_hook(fixture, prompt) for prompt in PROMPTS[::5]]
    assert any(seen.context is not None for seen in baseline)
    monkeypatch.setattr(cli, "_typed_prompt_render", forbidden)
    for prompt, expected in zip(PROMPTS[::5], baseline, strict=True):
        seen = run_hook(fixture, prompt)
        assert (seen.context, seen.delivery_keys) == (expected.context, expected.delivery_keys)
    PersonalSettingsStore(fixture.data).save(
        PersonalSettings(
            experimental_semantic_memory_enabled=True, experimental_typed_decisions_enabled=True
        )
    )
    for prompt, expected in zip(PROMPTS[::5], baseline, strict=True):
        seen = run_hook(fixture, prompt)
        assert (seen.context, seen.delivery_keys) == (expected.context, expected.delivery_keys)


def test_canonical_packets_keep_their_wire_shape(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    attached = cli._automatic_prompt_context_attachment(
        fixture.data, fixture.binding.checkpoint_scope, KNOWLEDGE_PROMPT
    )
    assert attached is not None
    omissions = json.loads(attached)["omissions"]
    assert omissions
    assert all(set(omission) == {"item_id", "reason", "detail"} for omission in omissions)


def test_live_filler_omission_lines_fit_the_unchanged_v1_schema(tmp_path: Path) -> None:
    schema = json.loads(
        resources.files("mnemo_memory")
        .joinpath("resources/schemas/context-packet-v1.json")
        .read_text(encoding="utf-8")
    )
    definition = schema["$defs"]["omission"]
    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    prime_note_verdicts(fixture, ScriptedJevTransport())
    live = synthetic_overrides(
        fixture, ScriptedJevTransport(), TypedHookModes(relevance=TypedDecisionMode.LIVE)
    )
    context = run_hook(fixture, KNOWLEDGE_PROMPT, live).context
    assert context is not None
    omissions = [
        json.loads(line.removeprefix("MNEMO_OMISSION "))
        for line in context.split("\n")
        if line.startswith("MNEMO_OMISSION ")
    ]
    filler = [omission for omission in omissions if omission["detail"] == FILLER_OMISSION_DETAIL]
    assert len(filler) == 2
    assert all(omission["reason"] == "lower_rank" for omission in filler)
    for omission in omissions:
        assert set(omission) == set(definition["required"]) == set(definition["properties"])
        assert omission["reason"] in definition["properties"]["reason"]["enum"]


def test_shadow_with_a_warm_cache_is_byte_identical_to_off(tmp_path: Path) -> None:
    """Shadow records the cached drops and changes no output (spec 2026-10-03 §3)."""

    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    transport = ScriptedJevTransport(ANSWER_EVERYTHING)
    prime_note_verdicts(fixture, transport)
    shadow = synthetic_overrides(fixture, transport, TypedHookModes(SHADOW, SHADOW, SHADOW, SHADOW))
    for prompt in (*PROMPTS[::4], KNOWLEDGE_PROMPT):
        off = run_hook(fixture, prompt)
        seen = run_hook(fixture, prompt, shadow)
        assert (seen.context, seen.delivery_keys) == (off.context, off.delivery_keys), prompt
    typed = (
        LocalAutomaticRouteTelemetryStore(fixture.data)
        .events(cli._automatic_route_scope(fixture.binding.checkpoint_scope), limit=1)[0]
        .typed
    )
    assert typed is not None and (typed.notes_cached, typed.notes_dropped) == (3, 2)


def test_off_reads_and_writes_no_verdict_cache_or_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    reads: list[int] = []

    def counted(self: LocalNoteVerdictCache, keys: Sequence[str]) -> tuple[NoteVerdictState, ...]:
        reads.append(len(keys))
        return ()

    monkeypatch.setattr(LocalNoteVerdictCache, "states", counted)
    for prompt in PROMPTS[::5]:
        run_hook(fixture, prompt)
    PersonalSettingsStore(fixture.data).save(
        PersonalSettings(
            experimental_semantic_memory_enabled=True, experimental_typed_decisions_enabled=True
        )
    )
    for prompt in PROMPTS[::5]:
        run_hook(fixture, prompt)
    assert reads == []
    assert not LocalNoteVerdictCache(fixture.data).path.exists()
    assert not LocalNoteJudgeQueue(fixture.data).path.exists()
