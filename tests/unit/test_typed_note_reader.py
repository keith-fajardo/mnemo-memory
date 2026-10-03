"""The judge's note reader: the scoped ``get_context item_ids`` lookup plus the hook's own
exemptions, so verdicts are keyed on exactly the text the hook looks up (spec §4)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from mnemo_memory.apps.cli import main as cli
from mnemo_memory.apps.cli.typed_decision_hook import (
    TypedHookModes,
    TypedPromptDecisions,
    TypedStepInput,
)
from mnemo_memory.packages.domain import ProjectId, TypedDecisionMode
from scripts.typed_decision_replay import approved_event_item_ids, knowledge_note_item_ids
from scripts.typed_decision_test_support import (
    FILLER_EVENT,
    KNOWLEDGE_PROMPT,
    PINNED_EVENT,
    USEFUL_NOTE,
    ScriptedJevTransport,
    run_hook,
    seed_hook_fixture,
    synthetic_overrides,
)


def test_the_reader_returns_exactly_the_text_the_hook_judges(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    steps: list[TypedStepInput] = []

    def observe(step: TypedStepInput, decisions: TypedPromptDecisions) -> None:
        steps.append(step)

    shadow = synthetic_overrides(
        fixture, ScriptedJevTransport(), TypedHookModes(relevance=TypedDecisionMode.SHADOW), observe
    )
    run_hook(fixture, KNOWLEDGE_PROMPT, shadow)
    [step] = steps
    hook = {candidate.item_id: candidate.text for candidate in step.filler_candidates}
    assert len(hook) == 3
    read = cli._note_candidates_by_id(fixture.data, fixture.binding.checkpoint_scope, tuple(hook))
    assert {candidate.item_id: candidate.text for candidate in read} == hook


def test_the_reader_keeps_the_hooks_exemptions(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    notes = knowledge_note_item_ids(fixture.data, fixture.binding)
    events = approved_event_item_ids(fixture.data, fixture.binding)
    assert (len(notes), len(events)) == (4, 2)  # two notes and two skill documents; two events
    texts = [
        candidate.text
        for candidate in cli._note_candidates_by_id(
            fixture.data, fixture.binding.checkpoint_scope, (*notes, *events)
        )
    ]
    assert len(texts) == 5
    assert any(USEFUL_NOTE in text for text in texts)
    assert any(FILLER_EVENT in text for text in texts)
    assert not any(PINNED_EVENT in text for text in texts)  # a pinned event is never judged


def test_the_reader_skips_ids_it_cannot_serve_in_this_scope(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    scope = fixture.binding.checkpoint_scope
    ids = (
        *knowledge_note_item_ids(fixture.data, fixture.binding),
        *approved_event_item_ids(fixture.data, fixture.binding),
    )
    foreign = replace(
        scope, project_id=ProjectId.from_string("00000000-0000-4000-8002-00000000beef")
    )
    assert cli._note_candidates_by_id(fixture.data, foreign, ids) == ()
    assert cli._note_candidates_by_id(fixture.data, scope, ("not-an-item", "")) == ()
