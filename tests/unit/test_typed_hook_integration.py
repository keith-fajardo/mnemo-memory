"""The typed step inside the real prompt hook (spec 2026-10-02 §3, §4, §7)."""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest

from mnemo_memory.apps.cli import main as cli
from mnemo_memory.apps.cli import typed_decision_composition, typed_decision_hook
from mnemo_memory.apps.cli.typed_decision_hook import (
    FILLER_OMISSION_DETAIL,
    HOOK_KINDS,
    TypedHookModes,
    TypedPromptDecisions,
    TypedStepInput,
)
from mnemo_memory.connectors.automatic_memory.hook import PromptContextAttachment
from mnemo_memory.connectors.typesafe import jev_provider
from mnemo_memory.packages.application import (
    CheckpointRuntime,
    LocalConfig,
    PersonalSettings,
    PersonalSettingsStore,
)
from mnemo_memory.packages.application.context_routing import (
    AUTOMATIC_CONTEXT_LAZY_PULL_HINT,
    AutomaticContextRouteDecision,
)
from mnemo_memory.packages.application.settings import with_typed_decision_mode
from mnemo_memory.packages.domain import (
    ContextPacket,
    MemoryScope,
    OmissionNotice,
    OmissionReason,
    TypedDecisionMode,
    TypedDecisionSource,
)
from mnemo_memory.packages.model_gateway.decision_axes import HINT_TEXT
from mnemo_memory.packages.model_gateway.typed_decisions import GuardedTypedDecisionClassifier
from mnemo_memory.packages.skills_registry import KnowledgeDocumentSkillRegistry
from mnemo_memory.packages.storage import SQLiteCheckpointRepository
from mnemo_memory.packages.telemetry import AutomaticRouteEvent, LocalAutomaticRouteTelemetryStore
from scripts.typed_decision_test_support import (
    FAKE_TYPESAFE_KEY,
    FILLER_EVENT,
    FILLER_MARKER,
    GREETING_PROMPT,
    HANDOFF_OBJECTIVE,
    KNOWLEDGE_PROMPT,
    LAZY_PROMPT,
    PINNED_EVENT,
    SKILL_PROMPT,
    HookFixture,
    ScriptedJevTransport,
    run_hook,
    seed_hook_fixture,
    synthetic_overrides,
)

SHADOW, LIVE = TypedDecisionMode.SHADOW, TypedDecisionMode.LIVE
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
    live = synthetic_overrides(fixture, transport, TypedHookModes(relevance=LIVE))

    off = run_hook(fixture, KNOWLEDGE_PROMPT)
    dropped = run_hook(fixture, KNOWLEDGE_PROMPT, live)

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


def test_drops_never_apply_to_a_route_fetched_after_the_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)

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
    """The standard project plus two long filler notes the 1,300-token render cuts.

    Each long note renders larger than the space one dropped event frees, but smaller than the
    space an event and a short note free together.
    """

    fixture = seed_hook_fixture(root, semantic_gate=False)
    padding = " ".join(f"w{index}" for index in range(110))
    for name in ("extra-filler-a.md", "extra-filler-b.md"):
        (fixture.project / "notes" / name).write_text(
            f"# Invoice export note\n{FILLER_MARKER}: invoice export chatter {padding}\n", "utf-8"
        )
    cli._refresh_project_knowledge(fixture.data, fixture.binding)
    return fixture


def test_filler_work_covers_only_rendered_notes_and_never_refills(tmp_path: Path) -> None:
    fixture = _seed_overflowing_project(tmp_path)
    packet = cli._automatic_prompt_context_result(
        fixture.data, fixture.binding.checkpoint_scope, KNOWLEDGE_PROMPT, "codex"
    ).packet
    assert packet is not None
    off = run_hook(fixture, KNOWLEDGE_PROMPT).context
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
    [(step, decisions)] = observed
    omitted = set(_filler_omission_ids(context))

    # Only rendered notes are filler candidates.
    checked = {candidate.item_id for candidate in step.filler_candidates}
    assert checked and checked <= shown
    # After the drops no previously unrendered note appears: exactly the dropped notes leave.
    assert omitted
    assert _item_ids(context) == shown - omitted
    # The omission lines name only previously rendered IDs.
    assert omitted <= shown
    # A judged-filler note whose freed space would let a cut note in is kept (no refill).
    assert omitted < set(decisions.drop_item_ids)


def test_skill_none_lets_live_memory_need_decide_retrieval(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True, with_handoff=True)
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

    def unreadable(*args: object, **kwargs: object) -> None:
        raise RuntimeError("synthetic storage failure")

    monkeypatch.setattr(SQLiteCheckpointRepository, "get_approved_event_record", unreadable)
    transport = ScriptedJevTransport()
    live = synthetic_overrides(fixture, transport, TypedHookModes(relevance=LIVE))
    ids = _filler_omission_ids(run_hook(fixture, KNOWLEDGE_PROMPT, live).context)
    assert len(ids) == 1 and ids[0].startswith(fixture.filler_note_prefix)
    assert all(FILLER_EVENT not in state for state in transport.states)


def test_filler_checks_run_only_on_push_actions(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    off = run_hook(fixture, LAZY_PROMPT).context
    # Without the semantic gate today's hook still attaches notes for this lazy-pull prompt.
    assert off is not None and fixture.filler_event_id in _item_ids(off)
    transport = ScriptedJevTransport()
    live = synthetic_overrides(fixture, transport, TypedHookModes(relevance=LIVE))
    assert run_hook(fixture, LAZY_PROMPT, live).context == off
    assert transport.calls == 0


def test_the_step_adds_no_local_work_its_modes_do_not_need(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    counts: Counter[str] = Counter()
    registry = KnowledgeDocumentSkillRegistry
    discover, listing = registry.discover_current_skills, registry.current_skill_listing

    def counted_discover(self: object, *args: object, **kwargs: object) -> object:
        counts["discover"] += 1
        return discover(self, *args, **kwargs)  # type: ignore[arg-type]

    def counted_listing(self: object, *args: object, **kwargs: object) -> object:
        counts["listing"] += 1
        return listing(self, *args, **kwargs)  # type: ignore[arg-type]

    pin_reads: list[ContextPacket] = []
    pinned = cli._pinned_approved_item_ids

    def counted_pins(runtime: CheckpointRuntime, packet: ContextPacket) -> frozenset[str]:
        pin_reads.append(packet)
        return pinned(runtime, packet)

    monkeypatch.setattr(registry, "discover_current_skills", counted_discover)
    monkeypatch.setattr(registry, "current_skill_listing", counted_listing)
    monkeypatch.setattr(cli, "_pinned_approved_item_ids", counted_pins)

    quiet = TypedHookModes(front_door=SHADOW, tier_hint=SHADOW)
    run_hook(fixture, KNOWLEDGE_PROMPT, synthetic_overrides(fixture, ScriptedJevTransport(), quiet))
    assert (counts, pin_reads) == (Counter(discover=1), [])  # the rules path's own discovery

    counts.clear()
    skill = synthetic_overrides(fixture, ScriptedJevTransport(), TypedHookModes(skill=SHADOW))
    run_hook(fixture, SKILL_PROMPT, skill)
    assert counts == Counter(discover=1, listing=1)  # keyword discovery is reused, not re-run
    run_hook(fixture, GREETING_PROMPT, skill)
    assert counts == Counter(discover=1, listing=2)  # a hard route never runs discovery
    assert pin_reads == []


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
