"""The typed step inside the real prompt hook (spec 2026-10-02 §3, §4, §7)."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from mnemo_memory.apps.cli import main as cli
from mnemo_memory.apps.cli import typed_decision_composition, typed_decision_hook
from mnemo_memory.apps.cli.typed_decision_hook import (
    FILLER_OMISSION_DETAIL,
    HOOK_KINDS,
    TypedHookModes,
    TypedHookOverrides,
    TypedPromptDecisions,
    TypedStepInput,
)
from mnemo_memory.connectors.automatic_memory.hook import PromptContextAttachment
from mnemo_memory.connectors.automatic_memory.learned_routes import LocalLearnedRouteStore
from mnemo_memory.connectors.typesafe import jev_provider
from mnemo_memory.packages.application import (
    CheckpointRuntime,
    LocalConfig,
    PersonalSettings,
    PersonalSettingsStore,
)
from mnemo_memory.packages.application.context_routing import (
    AUTOMATIC_CONTEXT_LAZY_PULL_HINT,
    AutomaticContextRoute,
    AutomaticContextRouteDecision,
    CompactMemoryRoute,
    LearnedRoutePhrase,
    typed_route_decision,
)
from mnemo_memory.packages.application.settings import with_typed_decision_mode
from mnemo_memory.packages.domain import (
    ContextPacket,
    EventId,
    KnowledgeDocumentRevision,
    MemoryScope,
    OmissionNotice,
    OmissionReason,
    TypedDecisionMode,
    TypedDecisionSource,
)
from mnemo_memory.packages.model_gateway.decision_axes import HINT_TEXT
from mnemo_memory.packages.model_gateway.typed_decisions import GuardedTypedDecisionClassifier
from mnemo_memory.packages.storage import (
    ApprovedEpisodicEventRecord,
    LocalNoteJudgeQueue,
    LocalNoteVerdictCache,
    NoteVerdictState,
    SQLiteCheckpointRepository,
    SQLiteKnowledgeDocumentRepository,
)
from mnemo_memory.packages.telemetry import (
    AutomaticRouteEvent,
    AutomaticRouteOutcome,
    LocalAutomaticRouteTelemetryStore,
)
from scripts.typed_decision_test_support import (
    FAKE_TYPESAFE_KEY,
    FILLER_MARKER,
    GREETING_PROMPT,
    HANDOFF_OBJECTIVE,
    KNOWLEDGE_PROMPT,
    LAZY_PROMPT,
    PINNED_EVENT,
    PRIOR_PROMPT,
    SKILL_PROMPT,
    HookFixture,
    ScriptedJevTransport,
    prime_note_verdicts,
    run_hook,
    seed_hook_fixture,
    synthetic_overrides,
)

OFF, SHADOW, LIVE = TypedDecisionMode.OFF, TypedDecisionMode.SHADOW, TypedDecisionMode.LIVE
ARCHITECTURE_PROMPT = "Explain the architecture of this repository."
EVERYTHING = {
    "memory_need": ("nothing", 0.95),
    "complexity": ("light", 0.95),
    "tool_need": ("read_heavy", 0.95),
    "skill_pick": ("test-plan", 0.95),
}


def _filler_omission_ids(context: str | None) -> list[str]:
    """Item IDs named by per-note ``lower_rank`` filler omissions in a rendered attachment."""

    assert context is not None
    ids: list[str] = []
    for line in context.split("\n"):
        if not line.startswith("MNEMO_OMISSION "):
            continue
        value = json.loads(line.removeprefix("MNEMO_OMISSION "))
        if value["reason"] == "lower_rank" and value["detail"] == FILLER_OMISSION_DETAIL:
            ids.append(value["item_id"])
    return ids


def _item_ids(context: str | None) -> set[str]:
    """Item IDs on the ``MNEMO_ITEM`` lines of a rendered attachment: what the agent sees."""

    assert context is not None
    return {
        json.loads(line.removeprefix("MNEMO_ITEM "))["item_id"]
        for line in context.split("\n")
        if line.startswith("MNEMO_ITEM ")
    }


def _latest_event(fixture: HookFixture) -> AutomaticRouteEvent:
    return LocalAutomaticRouteTelemetryStore(fixture.data).events(
        cli._automatic_route_scope(fixture.binding.checkpoint_scope), limit=1
    )[0]


@pytest.mark.parametrize("semantic_gate", [False, True])
def test_shadow_answers_change_nothing(tmp_path: Path, semantic_gate: bool) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=semantic_gate)
    transport = ScriptedJevTransport(EVERYTHING)
    shadow = synthetic_overrides(fixture, transport, TypedHookModes(SHADOW, SHADOW, SHADOW, SHADOW))
    for prompt in (KNOWLEDGE_PROMPT, SKILL_PROMPT, LAZY_PROMPT, GREETING_PROMPT):
        off = run_hook(fixture, prompt)
        seen = run_hook(fixture, prompt, shadow)
        assert (seen.context, seen.delivery_keys) == (off.context, off.delivery_keys), prompt
    assert transport.calls > 0


def test_each_live_filler_drop_leaves_its_own_lower_rank_omission(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    transport = ScriptedJevTransport()
    prime_note_verdicts(fixture, transport)
    primed = transport.calls
    live = synthetic_overrides(fixture, transport, TypedHookModes(relevance=LIVE))

    off = run_hook(fixture, KNOWLEDGE_PROMPT)
    dropped = run_hook(fixture, KNOWLEDGE_PROMPT, live)

    assert transport.calls == primed  # relevance alone sends nothing on the prompt path
    assert _filler_omission_ids(off.context) == []
    ids = _filler_omission_ids(dropped.context)
    assert len(ids) == 2
    assert fixture.filler_event_id in ids
    assert any(item_id.startswith(fixture.filler_note_prefix) for item_id in ids)
    assert not any(item_id.startswith(fixture.useful_note_prefix) for item_id in ids)
    assert fixture.pinned_event_id not in ids
    assert all(PINNED_EVENT not in state for state in transport.states)  # pinned never sent


def test_a_drop_is_cancelled_when_its_omission_line_does_not_fit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    prime_note_verdicts(fixture, ScriptedJevTransport())
    original = typed_decision_hook.filler_omission

    def oversized_for_the_note(item_id: str) -> OmissionNotice:
        if item_id.startswith(fixture.filler_note_prefix):
            return OmissionNotice(item_id, OmissionReason.LOWER_RANK, "x" * 6_000)
        return original(item_id)

    monkeypatch.setattr(typed_decision_hook, "filler_omission", oversized_for_the_note)
    live = synthetic_overrides(fixture, ScriptedJevTransport(), TypedHookModes(relevance=LIVE))
    partial = run_hook(fixture, KNOWLEDGE_PROMPT, live)
    # The note whose line cannot fit is kept; the event's drop still applies.
    assert _filler_omission_ids(partial.context) == [fixture.filler_event_id]

    def oversized(item_id: str) -> OmissionNotice:
        return OmissionNotice(item_id, OmissionReason.LOWER_RANK, "x" * 6_000)

    monkeypatch.setattr(typed_decision_hook, "filler_omission", oversized)
    assert run_hook(fixture, KNOWLEDGE_PROMPT, live).context == (
        run_hook(fixture, KNOWLEDGE_PROMPT).context
    )


def test_live_nothing_attaches_nothing(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    live = synthetic_overrides(
        fixture,
        ScriptedJevTransport({"memory_need": ("nothing", 0.95)}),
        TypedHookModes(front_door=LIVE),
    )
    assert run_hook(fixture, KNOWLEDGE_PROMPT).context is not None
    assert run_hook(fixture, KNOWLEDGE_PROMPT, live).context is None


def test_a_changed_route_is_fetched_after_the_answer_unfiltered(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True, with_handoff=True)
    prime_note_verdicts(fixture, ScriptedJevTransport())
    live = synthetic_overrides(
        fixture,
        ScriptedJevTransport({"memory_need": ("past_sessions", 0.95)}),
        TypedHookModes(front_door=LIVE, relevance=LIVE),
    )
    off = run_hook(fixture, KNOWLEDGE_PROMPT)
    fetched = run_hook(fixture, KNOWLEDGE_PROMPT, live)
    assert fetched.context is not None and fetched.context != off.context
    assert HANDOFF_OBJECTIVE in fetched.context
    # The prior-memory route was fetched after the answer: no knowledge note came with it.
    assert not any(item_id.startswith("knowledge:") for item_id in _item_ids(fetched.context))
    assert (_latest_event(fixture).route, _latest_event(fixture).reason) == (
        "prior_memory",
        "typed_decision",
    )
    assert _filler_omission_ids(fetched.context) == []


def _counted_route_fetches(
    monkeypatch: pytest.MonkeyPatch,
) -> list[AutomaticContextRouteDecision]:
    fetches: list[AutomaticContextRouteDecision] = []
    fetch = cli._automatic_prompt_context_for_route

    def counted(
        data_directory: Path,
        scope: MemoryScope,
        prompt: str,
        decision: AutomaticContextRouteDecision,
        *,
        experimental_semantic_memory_enabled: bool = False,
    ) -> cli._AutomaticPromptContextResult:
        fetches.append(decision)
        return fetch(
            data_directory,
            scope,
            prompt,
            decision,
            experimental_semantic_memory_enabled=experimental_semantic_memory_enabled,
        )

    monkeypatch.setattr(cli, "_automatic_prompt_context_for_route", counted)
    return fetches


def test_a_confirmed_route_whose_fetch_found_nothing_is_not_fetched_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)  # no handoff: nothing to recap
    scope = fixture.binding.checkpoint_scope
    typed_decision = typed_route_decision(AutomaticContextRoute.PRIOR_MEMORY)
    again = cli._automatic_prompt_context_for_route(
        fixture.data, scope, PRIOR_PROMPT, typed_decision, experimental_semantic_memory_enabled=True
    )
    assert (again.packet, again.failed) == (None, False)  # a second fetch would find nothing
    fetches = _counted_route_fetches(monkeypatch)
    live = synthetic_overrides(
        fixture,
        ScriptedJevTransport({"memory_need": ("past_sessions", 0.95)}),
        TypedHookModes(front_door=LIVE),
    )
    seen = run_hook(fixture, PRIOR_PROMPT, live)
    assert fetches == []
    event = _latest_event(fixture)
    assert (event.route, event.reason, event.maximum_attachment_tokens) == (
        "prior_memory",
        "typed_decision",
        typed_decision.maximum_attachment_tokens,
    )
    assert (event.outcome, seen.delivery_keys) == (AutomaticRouteOutcome.MISS, ())


def test_a_gate_suppressed_route_is_fetched_when_a_live_answer_pushes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Today's gate suppressed this prompt, so nothing was fetched to reuse."""

    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    fetches = _counted_route_fetches(monkeypatch)
    live = synthetic_overrides(
        fixture,
        ScriptedJevTransport({"memory_need": ("project_docs", 0.95)}),
        TypedHookModes(front_door=LIVE),
    )
    context = run_hook(fixture, LAZY_PROMPT, live).context
    assert fetches == [typed_route_decision(AutomaticContextRoute.KNOWLEDGE)]
    assert fixture.filler_event_id in _item_ids(context)


def test_an_overview_fetch_is_not_reused_for_a_query_fetch_of_the_same_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Today's architecture fetch asks for a source overview; the typed structure route asks a
    query, so its empty overview is not reused."""

    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    fetches = _counted_route_fetches(monkeypatch)
    live = synthetic_overrides(
        fixture,
        ScriptedJevTransport({"memory_need": ("code_structure", 0.95)}),
        TypedHookModes(front_door=LIVE),
    )
    run_hook(fixture, ARCHITECTURE_PROMPT, live)
    assert fetches == [typed_route_decision(AutomaticContextRoute.STRUCTURE)]


def test_drops_never_apply_to_a_route_fetched_after_the_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    prime_note_verdicts(fixture, ScriptedJevTransport())

    def same_notes(
        data_directory: Path,
        scope: MemoryScope,
        prompt: str,
        decision: AutomaticContextRouteDecision,
        *,
        experimental_semantic_memory_enabled: bool = False,
    ) -> cli._AutomaticPromptContextResult:
        """A changed route whose fetch happens to return today's notes, judged filler included."""

        today = cli._automatic_prompt_context_result(
            data_directory,
            scope,
            prompt,
            "codex",
            experimental_semantic_memory_enabled=experimental_semantic_memory_enabled,
        )
        return replace(today, decision=decision)

    monkeypatch.setattr(cli, "_automatic_prompt_context_for_route", same_notes)
    live = synthetic_overrides(
        fixture,
        ScriptedJevTransport({"memory_need": ("past_sessions", 0.95)}),
        TypedHookModes(front_door=LIVE, relevance=LIVE),
    )
    context = run_hook(fixture, KNOWLEDGE_PROMPT, live).context
    assert fixture.filler_event_id in _item_ids(context)
    assert _filler_omission_ids(context) == []


def test_a_failed_post_answer_fetch_keeps_todays_render_and_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    prime_note_verdicts(fixture, ScriptedJevTransport())
    off = run_hook(fixture, KNOWLEDGE_PROMPT)
    rules_event = _latest_event(fixture)

    def failed(
        data_directory: Path,
        scope: MemoryScope,
        prompt: str,
        decision: AutomaticContextRouteDecision,
        *,
        experimental_semantic_memory_enabled: bool = False,
    ) -> cli._AutomaticPromptContextResult:
        return cli._AutomaticPromptContextResult(decision, None, (), 0, failed=True)

    monkeypatch.setattr(cli, "_automatic_prompt_context_for_route", failed)
    live = synthetic_overrides(
        fixture,
        ScriptedJevTransport({"memory_need": ("past_sessions", 0.95)}),
        TypedHookModes(front_door=LIVE, relevance=LIVE),
    )
    seen = run_hook(fixture, KNOWLEDGE_PROMPT, live)
    assert off.context is not None
    assert (seen.context, seen.delivery_keys) == (off.context, off.delivery_keys)
    event = _latest_event(fixture)
    assert (event.route, event.outcome, event.shadow_reason, event.shadow_action) == (
        rules_event.route,
        rules_event.outcome,
        rules_event.shadow_reason,
        rules_event.shadow_action,
    )


def test_front_door_live_without_the_semantic_gate_is_not_applied(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    live = synthetic_overrides(
        fixture,
        ScriptedJevTransport({"memory_need": ("nothing", 0.95)}),
        TypedHookModes(front_door=LIVE),
    )
    assert run_hook(fixture, KNOWLEDGE_PROMPT, live).context == (
        run_hook(fixture, KNOWLEDGE_PROMPT).context
    )


def test_hard_rule_prompt_asks_only_the_skill_question(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    transport = ScriptedJevTransport(EVERYTHING)
    shadow = synthetic_overrides(fixture, transport, TypedHookModes(SHADOW, SHADOW, SHADOW, SHADOW))
    run_hook(fixture, GREETING_PROMPT, shadow)
    assert transport.questions == [("skill_pick",)]


def _skill_names(context: str | None) -> list[str]:
    if context is None or not context.startswith("MNEMO_SKILL_DISCOVERY_V1 "):
        return []
    value = json.loads(context.removeprefix("MNEMO_SKILL_DISCOVERY_V1 "))
    return [candidate["name"] for candidate in value["candidates"]]


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (("test-plan", 0.95), ["test-plan"]),
        (("none", 0.95), []),
        (("test-plan", 0.55), ["release-notes"]),
    ],
)
def test_live_skill_pick_replaces_keyword_matching_only_when_sure(
    tmp_path: Path, answer: tuple[str, float], expected: list[str]
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    live = synthetic_overrides(
        fixture, ScriptedJevTransport({"skill_pick": answer}), TypedHookModes(skill=LIVE)
    )
    assert _skill_names(run_hook(fixture, SKILL_PROMPT).context) == ["release-notes"]
    assert _skill_names(run_hook(fixture, SKILL_PROMPT, live).context) == expected


def test_live_hint_is_appended_and_merged_with_lazy_pull(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    live = synthetic_overrides(
        fixture,
        ScriptedJevTransport({"complexity": ("light", 0.95), "tool_need": ("read_heavy", 0.95)}),
        TypedHookModes(tier_hint=LIVE),
    )
    pushed = run_hook(fixture, KNOWLEDGE_PROMPT, live).context
    lazy = run_hook(fixture, LAZY_PROMPT, live).context
    assert pushed is not None and pushed.endswith("\n" + HINT_TEXT)
    assert lazy == AUTOMATIC_CONTEXT_LAZY_PULL_HINT + "\n" + HINT_TEXT
    assert all((len(line) + 3) // 4 <= 40 for line in lazy.split("\n"))


def test_an_exception_inside_the_step_keeps_todays_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    off = run_hook(fixture, KNOWLEDGE_PROMPT)

    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("synthetic local failure")

    monkeypatch.setattr(cli, "_typed_local_inputs", broken)
    live = synthetic_overrides(
        fixture, ScriptedJevTransport(EVERYTHING), TypedHookModes(LIVE, LIVE, LIVE, LIVE)
    )
    assert run_hook(fixture, KNOWLEDGE_PROMPT, live).context == off.context


def test_the_hook_command_never_passes_replay_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    calls: list[dict[str, object]] = []

    def spy(*args: object, **kwargs: object) -> PromptContextAttachment:
        calls.append(dict(kwargs))
        return PromptContextAttachment(None)

    monkeypatch.setattr(cli, "_automatic_prompt_context_for_hook", spy)
    hook = cli.build_automatic_memory_hook(LocalConfig.defaults(fixture.data), "codex")
    assert hook.prompt_context_loader is not None
    hook.prompt_context_loader(fixture.binding.checkpoint_scope, KNOWLEDGE_PROMPT)
    assert calls == [{}]


def test_typed_modes_off_never_run_the_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    calls: list[object] = []

    def forbidden(*args: object, **kwargs: object) -> None:
        calls.append(args)
        raise AssertionError("the typed step must not run while every mode is off")

    monkeypatch.setattr(cli, "_typed_prompt_render", forbidden)
    assert run_hook(fixture, KNOWLEDGE_PROMPT).context is not None
    assert (
        run_hook(
            fixture,
            KNOWLEDGE_PROMPT,
            synthetic_overrides(fixture, ScriptedJevTransport(), TypedHookModes()),
        ).context
        is not None
    )
    assert calls == []


def test_modes_off_never_import_the_typed_step(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    child = (
        "import sys\n"
        "from pathlib import Path\n"
        "from mnemo_memory.apps.cli import main as cli\n"
        "from mnemo_memory.packages.application.automatic_memory import (\n"
        "    LocalMemoryProjectBindingStore,\n"
        ")\n"
        "data, project, prompt = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]\n"
        "binding = LocalMemoryProjectBindingStore(data).get(project)\n"
        "attached = cli._automatic_prompt_context_for_hook(\n"
        "    data, binding.checkpoint_scope, prompt, 'codex'\n"
        ")\n"
        "assert attached.context is not None\n"
        "print('mnemo_memory.apps.cli.typed_decision_hook' in sys.modules)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", child, str(fixture.data), str(fixture.project), KNOWLEDGE_PROMPT],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "False"


def _seed_overflowing_project(root: Path) -> HookFixture:
    """The standard project plus short notes a 1,300-token automatic render cannot all show.

    Every note renders at a similar size, so any drop frees room for a note the render cut.
    """

    fixture = seed_hook_fixture(root, semantic_gate=False)
    for index in range(6):
        text = (
            f"{FILLER_MARKER}: invoice export chatter number {index} about the weather."
            if index % 2
            else f"The invoice export ledger rule number {index} stays fixed."
        )
        (fixture.project / "notes" / f"extra-{index}.md").write_text(
            f"# Invoice export note {index}\n{text}\n", "utf-8"
        )
    cli._refresh_project_knowledge(fixture.data, fixture.binding)
    return fixture


def test_filler_work_covers_only_rendered_notes_and_never_refills(tmp_path: Path) -> None:
    fixture = _seed_overflowing_project(tmp_path)
    prime_note_verdicts(fixture, ScriptedJevTransport())
    today = cli._automatic_prompt_context_result(
        fixture.data, fixture.binding.checkpoint_scope, KNOWLEDGE_PROMPT, "codex"
    )
    packet = today.packet
    assert packet is not None
    off = run_hook(fixture, KNOWLEDGE_PROMPT).context
    assert off is not None
    shown = _item_ids(off)
    assert any(
        item.item_id not in shown and FILLER_MARKER in item.content for item in packet.items
    ), "the packet must hold filler notes the render cuts"

    observed: list[tuple[TypedStepInput, TypedPromptDecisions]] = []
    live = synthetic_overrides(
        fixture,
        ScriptedJevTransport(),
        TypedHookModes(relevance=LIVE),
        lambda step, decisions: observed.append((step, decisions)),
    )
    context = run_hook(fixture, KNOWLEDGE_PROMPT, live).context
    assert context is not None
    [(step, decisions)] = observed
    omitted = set(_filler_omission_ids(context))

    # Without pinning, the freed space would admit a note the rules render cut.
    reduced, _ = typed_decision_hook.without_filler_notes(packet, decisions.drop_item_ids)
    unpinned, _ = cli._render_automatic_prompt_result(
        replace(today, packet=reduced), "codex", today.decision.maximum_attachment_tokens
    )
    assert not _item_ids(unpinned) <= shown
    # Only rendered notes are filler candidates.
    checked = {candidate.item_id for candidate in step.filler_candidates}
    assert checked and checked <= shown
    # Every judged drop applies, and no previously unrendered note appears.
    assert omitted and omitted == set(decisions.drop_item_ids)
    assert _item_ids(context) == shown - omitted
    # The omission lines name only previously rendered IDs; the cut notes stay in the
    # aggregate token-budget omission, and the attachment is smaller than today's.
    assert omitted <= shown
    assert '"item_id":"automatic-render"' in off and '"item_id":"automatic-render"' in context
    assert (len(context) + 3) // 4 < (len(off) + 3) // 4


def test_skill_none_lets_live_memory_need_decide_retrieval(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True, with_handoff=True)
    prime_note_verdicts(fixture, ScriptedJevTransport())
    prompt = "Create the changelog entry the release docs describe."
    assert _skill_names(run_hook(fixture, prompt).context) == ["release-notes"]
    live = synthetic_overrides(
        fixture,
        ScriptedJevTransport(
            {"memory_need": ("past_sessions", 0.95), "skill_pick": ("none", 0.95)}
        ),
        TypedHookModes(front_door=LIVE, relevance=LIVE, skill=LIVE),
    )
    context = run_hook(fixture, prompt, live).context
    assert context is not None and HANDOFF_OBJECTIVE in context
    assert _latest_event(fixture).route == "prior_memory"
    assert _filler_omission_ids(context) == []


def test_an_unreadable_pin_state_keeps_the_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    transport = ScriptedJevTransport()
    prime_note_verdicts(fixture, transport)  # the event has a cached filler verdict
    primed = transport.calls

    def unreadable(*args: object, **kwargs: object) -> None:
        raise RuntimeError("synthetic storage failure")

    monkeypatch.setattr(SQLiteCheckpointRepository, "get_approved_event_records", unreadable)
    monkeypatch.setattr(SQLiteCheckpointRepository, "get_approved_event_record", unreadable)
    live = synthetic_overrides(fixture, transport, TypedHookModes(relevance=LIVE))
    ids = _filler_omission_ids(run_hook(fixture, KNOWLEDGE_PROMPT, live).context)
    assert len(ids) == 1 and ids[0].startswith(fixture.filler_note_prefix)
    assert transport.calls == primed  # nothing about any note is sent on the prompt path


def test_pin_states_are_read_in_one_pass_and_one_by_one_only_after_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    prime_note_verdicts(fixture, ScriptedJevTransport())
    live = synthetic_overrides(fixture, ScriptedJevTransport(), TypedHookModes(relevance=LIVE))
    together_reads: list[int] = []
    one_reads: list[EventId] = []
    together = SQLiteCheckpointRepository.get_approved_event_records
    one = SQLiteCheckpointRepository.get_approved_event_record

    def counted_together(
        repository: SQLiteCheckpointRepository, scope: MemoryScope, event_ids: tuple[EventId, ...]
    ) -> tuple[ApprovedEpisodicEventRecord, ...]:
        together_reads.append(len(event_ids))
        return together(repository, scope, event_ids)

    def counted_one(
        repository: SQLiteCheckpointRepository, scope: MemoryScope, event_id: EventId
    ) -> ApprovedEpisodicEventRecord:
        one_reads.append(event_id)
        return one(repository, scope, event_id)

    monkeypatch.setattr(SQLiteCheckpointRepository, "get_approved_event_records", counted_together)
    monkeypatch.setattr(SQLiteCheckpointRepository, "get_approved_event_record", counted_one)
    context = run_hook(fixture, KNOWLEDGE_PROMPT, live).context
    assert (together_reads, one_reads) == ([2], [])  # both events' pin states, one read
    assert fixture.filler_event_id in _filler_omission_ids(context)

    def unreadable(*args: object, **kwargs: object) -> None:
        raise RuntimeError("synthetic storage failure")

    monkeypatch.setattr(SQLiteCheckpointRepository, "get_approved_event_records", unreadable)
    assert run_hook(fixture, KNOWLEDGE_PROMPT, live).context == context
    assert len(one_reads) == 2  # each event read on its own, so one failure hides no other


def test_without_the_gate_every_retrieved_packet_is_filler_checked(tmp_path: Path) -> None:
    """With the semantic gate off there is no plan to gate on, so "push" means the rules
    retrieved a packet (spec §4.2): this router-uncertain prompt plans ``lazy_pull`` but today's
    hook still attaches its notes, so they are looked up in the cache."""

    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    off = run_hook(fixture, LAZY_PROMPT).context
    assert off is not None and fixture.filler_event_id in _item_ids(off)
    transport = ScriptedJevTransport()
    prime_note_verdicts(fixture, transport)
    primed = transport.calls
    live = synthetic_overrides(fixture, transport, TypedHookModes(relevance=LIVE))
    context = run_hook(fixture, LAZY_PROMPT, live).context
    assert transport.calls == primed
    assert _filler_omission_ids(context) == [fixture.filler_event_id]
    assert _item_ids(context) == _item_ids(off) - {fixture.filler_event_id}
    typed = _latest_event(fixture).typed
    assert typed is not None
    assert (typed.notes_checked, typed.notes_dropped, typed.notes_cached, typed.notes_queued) == (
        1,
        1,
        1,
        0,
    )
    # Shadow looks up the same notes and still changes nothing.
    shadow = synthetic_overrides(fixture, transport, TypedHookModes(relevance=SHADOW))
    assert run_hook(fixture, LAZY_PROMPT, shadow).context == off
    shadowed = _latest_event(fixture).typed
    assert shadowed is not None and shadowed.notes_dropped == 1
    assert transport.calls == primed


def test_behind_the_gate_filler_checks_run_only_on_push_actions(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    off = run_hook(fixture, LAZY_PROMPT).context
    transport = ScriptedJevTransport()
    live = synthetic_overrides(fixture, transport, TypedHookModes(relevance=LIVE))
    assert run_hook(fixture, LAZY_PROMPT, live).context == off
    assert transport.calls == 0
    typed = _latest_event(fixture).typed
    assert typed is not None and typed.notes_checked == 0
    assert not LocalNoteJudgeQueue(fixture.data).path.exists()


def test_the_step_adds_no_local_work_its_modes_do_not_need(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skill documents are read once per prompt: the typed step reuses the rules path's listing
    and lists them itself only where the rules path did not (a hard route)."""

    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    reads: list[MemoryScope] = []
    current = SQLiteKnowledgeDocumentRepository.list_current_revisions

    def counted_reads(
        repository: SQLiteKnowledgeDocumentRepository, scope: MemoryScope
    ) -> tuple[KnowledgeDocumentRevision, ...]:
        reads.append(scope)
        return current(repository, scope)

    pin_reads: list[ContextPacket] = []
    pinned = cli._pinned_approved_item_ids

    def counted_pins(runtime: CheckpointRuntime, packet: ContextPacket) -> frozenset[str]:
        pin_reads.append(packet)
        return pinned(runtime, packet)

    monkeypatch.setattr(SQLiteKnowledgeDocumentRepository, "list_current_revisions", counted_reads)
    monkeypatch.setattr(cli, "_pinned_approved_item_ids", counted_pins)

    quiet = TypedHookModes(front_door=SHADOW, tier_hint=SHADOW)
    run_hook(fixture, KNOWLEDGE_PROMPT, synthetic_overrides(fixture, ScriptedJevTransport(), quiet))
    assert (len(reads), pin_reads) == (1, [])  # the rules path's own discovery

    reads.clear()
    skill = synthetic_overrides(fixture, ScriptedJevTransport(), TypedHookModes(skill=SHADOW))
    run_hook(fixture, SKILL_PROMPT, skill)
    assert len(reads) == 1  # the rules path's listing is reused, not read again
    run_hook(fixture, GREETING_PROMPT, skill)
    assert len(reads) == 2  # a hard route never runs discovery, so the step lists the skills
    assert pin_reads == []


def test_the_typed_step_reuses_the_traced_learned_phrases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    LocalLearnedRouteStore(fixture.data).learn(
        fixture.binding.scope, "blast radius", CompactMemoryRoute.STRUCTURE
    )
    learned = cli._learned_route_phrases(fixture.data, fixture.binding.checkpoint_scope)
    assert learned
    reads: list[MemoryScope] = []
    read = cli._learned_route_phrases

    def counted(data_directory: Path, scope: MemoryScope) -> tuple[LearnedRoutePhrase, ...]:
        reads.append(scope)
        return read(data_directory, scope)

    monkeypatch.setattr(cli, "_learned_route_phrases", counted)
    steps: list[TypedStepInput] = []

    def observe(step: TypedStepInput, decisions: TypedPromptDecisions) -> None:
        steps.append(step)

    live = synthetic_overrides(
        fixture, ScriptedJevTransport(EVERYTHING), TypedHookModes(LIVE, LIVE, LIVE, LIVE), observe
    )
    run_hook(fixture, KNOWLEDGE_PROMPT, live)
    assert len(reads) == 1  # the shadow trace's read; the typed step does not read again
    assert [step.learned_phrases for step in steps] == [learned]


def test_the_step_clock_starts_at_the_mode_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slow mode read (where a cold hook imports the typed module) counts against the cap."""

    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    read = cli._typed_modes

    def slow_read(
        settings: PersonalSettings, overrides: TypedHookOverrides | None
    ) -> TypedHookModes | None:
        time.sleep(0.3)
        return read(settings, overrides)

    deadlines: list[float] = []
    deadline = typed_decision_hook.step_deadline_seconds

    def recorded(started: float, clock: Callable[[], float] = time.monotonic) -> float:
        deadlines.append(deadline(started, clock))
        return deadlines[-1]

    monkeypatch.setattr(cli, "_typed_modes", slow_read)
    monkeypatch.setattr(typed_decision_hook, "step_deadline_seconds", recorded)
    live = synthetic_overrides(fixture, ScriptedJevTransport(), TypedHookModes(relevance=LIVE))
    run_hook(fixture, KNOWLEDGE_PROMPT, live)
    assert len(deadlines) == 1 and deadlines[0] <= 0.5
    typed = _latest_event(fixture).typed
    assert typed is not None and typed.step_ms >= 300


@pytest.mark.parametrize("failure", ["import", "mode_read"])
def test_a_failing_mode_read_keeps_todays_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    off = run_hook(fixture, KNOWLEDGE_PROMPT)
    PersonalSettingsStore(fixture.data).save(
        PersonalSettings(
            experimental_semantic_memory_enabled=True, experimental_typed_decisions_enabled=True
        )
    )
    if failure == "import":
        monkeypatch.setitem(sys.modules, "mnemo_memory.apps.cli.typed_decision_hook", None)
    else:

        def broken(settings: PersonalSettings) -> TypedHookModes:
            raise RuntimeError("synthetic settings failure")

        monkeypatch.setattr(typed_decision_hook, "typed_hook_modes", broken)
    seen = run_hook(fixture, KNOWLEDGE_PROMPT)
    assert (seen.context, seen.delivery_keys) == (off.context, off.delivery_keys)


def test_a_live_skill_pick_never_changes_a_hard_route(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    prompt = "Which mnemo version is installed?"
    off = run_hook(fixture, prompt).context
    assert off is not None and off.startswith("MNEMO_LOCAL_DIAGNOSTICS_V1 ")
    live = synthetic_overrides(
        fixture,
        ScriptedJevTransport({"skill_pick": ("test-plan", 0.95)}),
        TypedHookModes(skill=LIVE),
    )
    assert run_hook(fixture, prompt, live).context == off


def test_a_confirmed_live_route_keeps_its_prefetched_notes_and_their_drops(
    tmp_path: Path,
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    prime_note_verdicts(fixture, ScriptedJevTransport())
    live = synthetic_overrides(
        fixture,
        ScriptedJevTransport({"memory_need": ("project_docs", 0.95)}),
        TypedHookModes(front_door=LIVE, relevance=LIVE),
    )
    ids = _filler_omission_ids(run_hook(fixture, KNOWLEDGE_PROMPT, live).context)
    assert len(ids) == 2 and fixture.filler_event_id in ids
    event = _latest_event(fixture)
    assert (event.route, event.shadow_reason) == ("knowledge", "typed_decision")


def test_the_real_hook_path_builds_the_runtime_guard_and_keeps_todays_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    off = run_hook(fixture, KNOWLEDGE_PROMPT)
    settings = PersonalSettings(
        experimental_semantic_memory_enabled=True, experimental_typed_decisions_enabled=True
    )
    for kind in HOOK_KINDS:
        settings = with_typed_decision_mode(settings, kind, SHADOW)
    PersonalSettingsStore(fixture.data).save(settings)
    sent: list[str] = []

    def transport(url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> bytes:
        sent.append(url)
        raise AssertionError("runtime text reached the Jev transport")

    built: list[GuardedTypedDecisionClassifier] = []
    original = typed_decision_composition.build_runtime_typed_decision_classifier

    def recording(*args: object, **kwargs: object) -> GuardedTypedDecisionClassifier | None:
        guard = original(*args, **kwargs)  # type: ignore[arg-type]
        if guard is not None:
            built.append(guard)
        return guard

    monkeypatch.setenv("TYPESAFE_API_KEY", FAKE_TYPESAFE_KEY)
    monkeypatch.setattr(jev_provider, "_urllib_transport", transport)
    monkeypatch.setattr(
        typed_decision_composition, "build_runtime_typed_decision_classifier", recording
    )
    seen = run_hook(fixture, KNOWLEDGE_PROMPT)
    assert (seen.context, seen.delivery_keys) == (off.context, off.delivery_keys)
    assert built and all(guard._source is TypedDecisionSource.RUNTIME for guard in built)
    assert sent == []


def test_shadow_writes_the_typed_v1_group(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    transport = ScriptedJevTransport(EVERYTHING)
    prime_note_verdicts(fixture, transport)
    shadow = synthetic_overrides(fixture, transport, TypedHookModes(SHADOW, SHADOW, SHADOW, SHADOW))
    run_hook(fixture, KNOWLEDGE_PROMPT, shadow)
    typed = _latest_event(fixture).typed
    assert typed is not None
    assert typed.front_door_outcome == "answered"
    assert typed.model_version == "jev-1.13.0"
    assert (typed.memory_label, typed.memory_confidence_bucket, typed.action) == (
        "nothing",
        ">=0.9",
        "none",
    )
    assert typed.agrees_with_rules is False
    assert (typed.notes_checked, typed.notes_dropped, typed.notes_unanswered) == (3, 2, 0)
    assert (typed.notes_cached, typed.notes_queued) == (3, 0)
    assert (typed.tier, typed.hint, typed.skill) == ("light", "would_show", "differs")
    assert 0 <= typed.step_ms <= 10_000


def test_live_typed_plan_is_recorded_as_typed_decision(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    live = synthetic_overrides(
        fixture,
        ScriptedJevTransport({"memory_need": ("project_docs", 0.95)}),
        TypedHookModes(front_door=LIVE),
    )
    run_hook(fixture, LAZY_PROMPT, live)
    event = _latest_event(fixture)
    assert event.shadow_reason == "typed_decision"
    assert event.shadow_action == "push_long_term"
    assert event.typed is not None and event.typed.agrees_with_rules is False


def test_live_notes_dropped_counts_only_drops_that_happened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    prime_note_verdicts(fixture, ScriptedJevTransport())
    live = synthetic_overrides(fixture, ScriptedJevTransport(), TypedHookModes(relevance=LIVE))
    run_hook(fixture, KNOWLEDGE_PROMPT, live)
    applied = _latest_event(fixture).typed
    assert applied is not None and applied.notes_dropped == 2

    def oversized(item_id: str) -> OmissionNotice:
        return OmissionNotice(item_id, OmissionReason.LOWER_RANK, "x" * 6_000)

    monkeypatch.setattr(typed_decision_hook, "filler_omission", oversized)
    run_hook(fixture, KNOWLEDGE_PROMPT, live)
    cancelled = _latest_event(fixture).typed
    assert cancelled is not None and cancelled.notes_dropped == 0


def test_a_step_error_is_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)

    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("synthetic local failure")

    monkeypatch.setattr(cli, "_typed_local_inputs", broken)
    run_hook(
        fixture,
        KNOWLEDGE_PROMPT,
        synthetic_overrides(
            fixture, ScriptedJevTransport(EVERYTHING), TypedHookModes(LIVE, LIVE, LIVE, LIVE)
        ),
    )
    typed = _latest_event(fixture).typed
    assert typed is not None and typed.front_door_outcome == "typed_step_error"


def test_a_step_error_with_no_buildable_record_writes_no_typed_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)

    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("synthetic local failure")

    monkeypatch.setattr(cli, "_typed_local_inputs", broken)
    monkeypatch.setattr(typed_decision_hook, "typed_step_error_decisions", broken)
    live = synthetic_overrides(fixture, ScriptedJevTransport(EVERYTHING), TypedHookModes(LIVE))
    off = run_hook(fixture, KNOWLEDGE_PROMPT)
    assert run_hook(fixture, KNOWLEDGE_PROMPT, live).context == off.context
    assert _latest_event(fixture).typed is None


@pytest.mark.parametrize("failure", ["raises", "invalid_object"])
def test_invalid_typed_telemetry_never_costs_the_context_or_the_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    prime_note_verdicts(fixture, ScriptedJevTransport())
    live = synthetic_overrides(fixture, ScriptedJevTransport(), TypedHookModes(relevance=LIVE))
    dropped = run_hook(fixture, KNOWLEDGE_PROMPT, live).context
    assert len(_filler_omission_ids(dropped)) == 2

    def raises(values: object) -> object:
        raise ValueError("synthetic invalid typed value")

    def invalid_object(values: object) -> object:
        return object()

    broken = raises if failure == "raises" else invalid_object
    monkeypatch.setattr(typed_decision_hook, "route_telemetry", broken)
    seen = run_hook(fixture, KNOWLEDGE_PROMPT, live)
    assert seen.context == dropped  # the applied live context survives
    assert seen.telemetry_event_id is not None
    event = _latest_event(fixture)
    assert event.event_id == seen.telemetry_event_id and event.typed is None


def test_unusable_typed_values_never_cost_the_applied_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The live drop count is folded into the values inside the telemetry guard: a failure
    there keeps the applied render and records the event without a typed group."""

    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    prime_note_verdicts(fixture, ScriptedJevTransport())
    live = synthetic_overrides(fixture, ScriptedJevTransport(), TypedHookModes(relevance=LIVE))
    dropped = run_hook(fixture, KNOWLEDGE_PROMPT, live).context
    assert len(_filler_omission_ids(dropped)) == 2
    decide = typed_decision_hook.decide_typed_prompt
    not_values: Any = object()  # ``dataclasses.replace`` refuses it

    def unusable_values(
        guard_factory: typed_decision_hook.GuardFactory, step: TypedStepInput, *, started: float
    ) -> TypedPromptDecisions:
        return replace(decide(guard_factory, step, started=started), telemetry=not_values)

    monkeypatch.setattr(typed_decision_hook, "decide_typed_prompt", unusable_values)
    seen = run_hook(fixture, KNOWLEDGE_PROMPT, live)
    assert seen.context == dropped
    assert seen.telemetry_event_id is not None
    event = _latest_event(fixture)
    assert event.event_id == seen.telemetry_event_id and event.typed is None


HINT_ANSWERS = {"complexity": ("light", 0.95), "tool_need": ("read_heavy", 0.95)}


def test_hint_only_attachment_with_the_gate_off_is_no_attachment(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    live = synthetic_overrides(
        fixture, ScriptedJevTransport(HINT_ANSWERS), TypedHookModes(tier_hint=LIVE)
    )
    attached = run_hook(fixture, "Refactor this function to be shorter", live)
    assert attached.context == HINT_TEXT  # the hint is all that went out
    event = _latest_event(fixture)
    assert event.outcome is AutomaticRouteOutcome.NO_ATTACHMENT
    assert event.typed is not None and event.typed.hint == "shown"


def test_hint_only_attachment_after_a_gate_none_action_is_no_attachment(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    live = synthetic_overrides(
        fixture,
        ScriptedJevTransport({**HINT_ANSWERS, "memory_need": ("nothing", 0.95)}),
        TypedHookModes(front_door=LIVE, tier_hint=LIVE),
    )
    attached = run_hook(fixture, KNOWLEDGE_PROMPT, live)
    assert attached.context == HINT_TEXT
    event = _latest_event(fixture)
    assert event.shadow_action == "none"
    assert event.outcome is AutomaticRouteOutcome.NO_ATTACHMENT
    assert event.typed is not None and event.typed.hint == "shown"


def test_a_hint_beside_memory_keeps_the_hit_label(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    live = synthetic_overrides(
        fixture,
        ScriptedJevTransport({"complexity": ("light", 0.95), "tool_need": ("read_heavy", 0.95)}),
        TypedHookModes(tier_hint=LIVE),
    )
    run_hook(fixture, KNOWLEDGE_PROMPT, live)
    assert _latest_event(fixture).outcome is AutomaticRouteOutcome.HIT


def test_hook_telemetry_never_holds_prompt_note_or_skill_text(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    shadow = synthetic_overrides(
        fixture, ScriptedJevTransport(EVERYTHING), TypedHookModes(SHADOW, SHADOW, SHADOW, SHADOW)
    )
    run_hook(fixture, KNOWLEDGE_PROMPT + " private-marker-5d2e", shadow)
    encoded = LocalAutomaticRouteTelemetryStore(fixture.data).path.read_text("utf-8")
    assert "typed_front_door_mode" in encoded
    for marker in (
        "private-marker-5d2e",
        "invoice",
        "ledger",
        "FILLER",
        "release-notes",
        "test-plan",
    ):
        assert marker not in encoded


def test_the_prompt_path_sends_exactly_one_request(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    transport = ScriptedJevTransport(EVERYTHING)
    prime_note_verdicts(fixture, transport)
    for modes in (
        TypedHookModes(SHADOW, SHADOW, SHADOW, SHADOW),
        TypedHookModes(LIVE, LIVE, LIVE, LIVE),
    ):
        before = transport.calls
        run_hook(fixture, KNOWLEDGE_PROMPT, synthetic_overrides(fixture, transport, modes))
        assert transport.calls - before == 1
        assert transport.questions[-1] == ("memory_need", "complexity", "tool_need", "skill_pick")


def test_a_missing_or_stale_verdict_keeps_the_note_and_queues_its_id(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    off = run_hook(fixture, KNOWLEDGE_PROMPT).context
    live = synthetic_overrides(fixture, ScriptedJevTransport(), TypedHookModes(relevance=LIVE))
    assert run_hook(fixture, KNOWLEDGE_PROMPT, live).context == off  # nothing cached: keep all
    typed = _latest_event(fixture).typed
    assert typed is not None
    assert (typed.notes_checked, typed.notes_cached, typed.notes_queued, typed.notes_dropped) == (
        3,
        0,
        3,
        0,
    )
    queue = LocalNoteJudgeQueue(fixture.data)
    assert queue.length() == 3
    encoded = queue.path.read_text("utf-8")
    for text in ("invoice", "ledger", FILLER_MARKER, "lunch"):
        assert text not in encoded

    prime_note_verdicts(fixture, ScriptedJevTransport())
    cache = LocalNoteVerdictCache(fixture.data)
    stored = json.loads(cache.path.read_text("utf-8"))
    month_ago = int((datetime.now(UTC) - timedelta(days=31)).timestamp())
    stored["entries"] = {
        key: [value[0], month_ago, value[2]] for key, value in stored["entries"].items()
    }
    cache.path.write_text(json.dumps(stored), encoding="utf-8")
    assert run_hook(fixture, KNOWLEDGE_PROMPT, live).context == off  # stale: keep all
    stale = _latest_event(fixture).typed
    assert stale is not None and (stale.notes_cached, stale.notes_queued) == (0, 3)
    assert queue.length() == 3  # deduplicated, not doubled


def test_an_edited_note_is_kept_until_its_new_text_is_judged(tmp_path: Path) -> None:
    """Review focus 2: an old verdict never drops a note whose text changed."""

    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    prime_note_verdicts(fixture, ScriptedJevTransport())
    (fixture.project / "notes" / "chatter.md").write_text(
        "# Invoice export chatter\nFILLER: the invoice export chatter moved to Friday's lunch.\n",
        "utf-8",
    )
    cli._refresh_project_knowledge(fixture.data, fixture.binding)
    live = synthetic_overrides(fixture, ScriptedJevTransport(), TypedHookModes(relevance=LIVE))
    context = run_hook(fixture, KNOWLEDGE_PROMPT, live).context
    assert _filler_omission_ids(context) == [fixture.filler_event_id]
    typed = _latest_event(fixture).typed
    assert typed is not None and (typed.notes_cached, typed.notes_queued) == (2, 1)


def test_a_note_that_failed_three_times_is_not_queued_again(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    steps: list[TypedStepInput] = []

    def observe(step: TypedStepInput, decisions: TypedPromptDecisions) -> None:
        steps.append(step)

    live = synthetic_overrides(
        fixture, ScriptedJevTransport(), TypedHookModes(relevance=LIVE), observe
    )
    run_hook(fixture, KNOWLEDGE_PROMPT, live)
    keys = [
        typed_decision_hook.filler_verdict_key(candidate, "jev-1.13.0")
        for candidate in steps[0].filler_candidates
    ]
    cache = LocalNoteVerdictCache(fixture.data)
    for _ in range(3):
        cache.record([(key, None) for key in keys])
    queue = LocalNoteJudgeQueue(fixture.data)
    queue.take(256)
    run_hook(fixture, KNOWLEDGE_PROMPT, live)
    typed = _latest_event(fixture).typed
    assert typed is not None
    assert (typed.notes_checked, typed.notes_cached, typed.notes_queued) == (3, 0, 0)
    assert queue.length() == 0


def test_an_unreadable_cache_keeps_every_note(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    off = run_hook(fixture, KNOWLEDGE_PROMPT).context
    prime_note_verdicts(fixture, ScriptedJevTransport())
    LocalNoteVerdictCache(fixture.data).path.write_text('{"version": 1, "entries": ', "utf-8")
    live = synthetic_overrides(fixture, ScriptedJevTransport(), TypedHookModes(relevance=LIVE))
    assert run_hook(fixture, KNOWLEDGE_PROMPT, live).context == off
    typed = _latest_event(fixture).typed
    assert typed is not None and typed.front_door_outcome != "typed_step_error"
    assert (typed.notes_cached, typed.notes_queued) == (0, 3)


def test_a_failed_queue_write_keeps_the_answers_and_queues_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)

    def broken(self: LocalNoteJudgeQueue, scope: MemoryScope, item_ids: Sequence[str]) -> int:
        raise OSError("synthetic queue failure")

    monkeypatch.setattr(LocalNoteJudgeQueue, "append", broken)
    shadow = synthetic_overrides(
        fixture, ScriptedJevTransport(EVERYTHING), TypedHookModes(SHADOW, SHADOW, SHADOW, SHADOW)
    )
    run_hook(fixture, KNOWLEDGE_PROMPT, shadow)
    typed = _latest_event(fixture).typed
    assert typed is not None and typed.front_door_outcome == "answered"
    assert (typed.notes_checked, typed.notes_queued) == (3, 0)


def test_relevance_off_reads_and_queues_no_verdicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    reads: list[int] = []

    def counted(self: LocalNoteVerdictCache, keys: Sequence[str]) -> tuple[NoteVerdictState, ...]:
        reads.append(len(keys))
        return ()

    monkeypatch.setattr(LocalNoteVerdictCache, "states", counted)
    quiet = synthetic_overrides(
        fixture, ScriptedJevTransport(EVERYTHING), TypedHookModes(SHADOW, OFF, SHADOW, SHADOW)
    )
    run_hook(fixture, KNOWLEDGE_PROMPT, quiet)
    assert reads == []
    assert not LocalNoteJudgeQueue(fixture.data).path.exists()
    typed = _latest_event(fixture).typed
    assert typed is not None and typed.front_door_outcome == "answered"
    assert (typed.notes_checked, typed.notes_cached, typed.notes_queued) == (0, 0, 0)


def test_a_step_error_with_queued_notes_keeps_its_typed_record(tmp_path: Path) -> None:
    """The queued count is clamped to the checked notes, so a step error is still recorded."""

    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)

    def broken(recorder: object) -> GuardedTypedDecisionClassifier:
        raise RuntimeError("synthetic guard failure")

    shadow = TypedHookOverrides(broken, TypedHookModes(relevance=SHADOW))
    off = run_hook(fixture, KNOWLEDGE_PROMPT).context
    assert run_hook(fixture, KNOWLEDGE_PROMPT, shadow).context == off
    typed = _latest_event(fixture).typed
    assert typed is not None and typed.front_door_outcome == "typed_step_error"
    assert (typed.notes_checked, typed.notes_cached, typed.notes_queued) == (0, 0, 0)
    assert LocalNoteJudgeQueue(fixture.data).length() == 3  # the unjudged notes still wait
