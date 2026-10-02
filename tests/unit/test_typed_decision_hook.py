"""Pure combine functions for the Jev hook decisions (spec 2026-10-02 §4)."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from mnemo_memory.apps.cli.typed_decision_hook import (
    APPROVED_EVENT_ITEM_PREFIX,
    FILLER_OMISSION_DETAIL,
    MAXIMUM_FILLER_CHECKS,
    FillerCandidate,
    SkillComparison,
    TypedAnswers,
    TypedHookModes,
    TypedStepInput,
    combine_typed_decisions,
    confidence_bucket,
    effective_skill_names,
    filler_candidates,
    filler_omission,
    front_door_axes,
    memory_need_outcome,
    notes_to_drop,
    omission_line,
    skill_comparison,
    typed_hook_modes,
    typed_step_error_decisions,
    with_task_size_hint,
    without_filler_notes,
)
from mnemo_memory.packages.application import PersonalSettings
from mnemo_memory.packages.application.context_routing import (
    AutomaticContextNeed,
    AutomaticContextRoute,
    plan_automatic_context_needs,
)
from mnemo_memory.packages.domain import (
    ConflictNotice,
    ConflictState,
    ContentRepresentation,
    ContextBudget,
    ContextItem,
    ContextItemType,
    ContextPacket,
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    MemoryScope,
    OmissionReason,
    OwnerId,
    PacketSchemaVersion,
    ProjectId,
    ProvenanceNotice,
    RequestId,
    ScopeLevel,
    Sensitivity,
    SourceId,
    SourceTrustClass,
    TypedDecisionMode,
    TypedDecisionUnavailableReason,
    ValidityState,
    VerificationStatus,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.model_gateway.cascade_router import ClassifierResult
from mnemo_memory.packages.model_gateway.decision_axes import HINT_TEXT
from mnemo_memory.packages.model_gateway.typed_decisions import TierDecision, TypedDecisionOutcome

OFF, SHADOW, LIVE = TypedDecisionMode.OFF, TypedDecisionMode.SHADOW, TypedDecisionMode.LIVE
YES, NO = AutomaticContextNeed.YES, AutomaticContextNeed.NO
ALL_SHADOW = TypedHookModes(SHADOW, SHADOW, SHADOW, SHADOW)
ALL_LIVE = TypedHookModes(LIVE, LIVE, LIVE, LIVE)
LAZY_PROMPT = "finance reconciliation variance"
RULES_LAZY = plan_automatic_context_needs(LAZY_PROMPT)
NOW = datetime(2026, 10, 2, tzinfo=UTC)
SCOPE = MemoryScope(
    OwnerId.from_string("00000000-0000-4000-8000-000000000001"),
    ScopeLevel.PROJECT,
    Visibility.PROJECT,
    WorkspaceId.from_string("00000000-0000-4000-8001-000000000001"),
    ProjectId.from_string("00000000-0000-4000-8002-000000000001"),
)
EVIDENCE = EvidenceReference(
    EvidenceId.new(),
    SourceId.new(),
    EvidenceSourceType.TOOL_RESULT,
    SourceTrustClass.VERIFIED_TOOL_RESULT,
    "fixture://typed-hook",
    "sha256:" + "a" * 64,
    EvidenceLocation("fixture://typed-hook"),
    NOW,
    VerificationStatus.VERIFIED,
)


def _knowledge_id(index: int) -> str:
    """A knowledge-section ID in the shape ``get_context item_ids`` fetches."""

    return f"knowledge:{UUID(int=index)}:revision:{UUID(int=10_000 + index)}:section:0"


def _event_id(index: int) -> str:
    """An approved-event ID in the shape ``get_context item_ids`` fetches."""

    return f"{APPROVED_EVENT_ITEM_PREFIX}{UUID(int=20_000 + index)}"


USEFUL_NOTE = _knowledge_id(1)
FILLER_NOTE = _knowledge_id(2)
FILLER_EVENT = _event_id(1)
BASE_STEP = TypedStepInput(
    prompt=LAZY_PROMPT,
    modes=ALL_SHADOW,
    hard_rule=False,
    rules_plan=RULES_LAZY,
    rules_route=AutomaticContextRoute.KNOWLEDGE,
    skill_names=("release-notes", "test-plan"),
    keyword_skill_names=("release-notes",),
    filler_candidates=(
        FillerCandidate(USEFUL_NOTE, "Invoice export keeps ledger order."),
        FillerCandidate(FILLER_EVENT, "FILLER chatter about lunch."),
    ),
)


def _choice(axis: str, label: str, confidence: float, score: float = 0.0) -> ClassifierResult:
    return ClassifierResult(axis, label, math.log(confidence), score, confidence=confidence)


def _answered(*results: ClassifierResult) -> TypedDecisionOutcome:
    return TypedDecisionOutcome(tuple(results), None, 120, "jev-1.13.0")


def _blocked(reason: TypedDecisionUnavailableReason) -> TypedDecisionOutcome:
    return TypedDecisionOutcome((), reason, 0, None)


FILLER = _answered(_choice("note_substance", "filler", 0.95, 0.95))
KEEP = _answered(_choice("note_substance", "task_information", 0.95, 0.05))
FRONT = _answered(
    _choice("memory_need", "nothing", 0.95),
    _choice("complexity", "light", 0.95, 0.05),
    _choice("tool_need", "read_heavy", 0.95, 0.2625),
    _choice("skill_pick", "test-plan", 0.9),
)
LIGHT = TierDecision("light", "light", None)


def _item(
    item_id: str,
    content: str,
    *,
    item_type: ContextItemType = ContextItemType.KNOWLEDGE,
    sensitivity: Sensitivity = Sensitivity.NORMAL,
    conflict_state: ConflictState = ConflictState.NONE,
) -> ContextItem:
    return ContextItem(
        item_id,
        item_type,
        SCOPE,
        content,
        ContentRepresentation.UNTRUSTED_EVIDENCE,
        (len(content) + 3) // 4,
        (EVIDENCE,),
        SourceTrustClass.USER_AUTHORED,
        sensitivity,
        ValidityState.UNKNOWN,
        None,
        conflict_state,
        NOW,
    )


def _note(
    item_id: str,
    heading: str,
    content: str,
    *,
    sensitivity: Sensitivity = Sensitivity.NORMAL,
) -> ContextItem:
    body = json.dumps({"content": content, "heading": heading}, sort_keys=True)
    return _item(item_id, body, sensitivity=sensitivity)


def _event(item_id: str, summary: str) -> ContextItem:
    body = json.dumps(
        {"event_kind": "decision", "occurred_at": NOW.isoformat(), "summary": summary},
        sort_keys=True,
    )
    return _item(item_id, body, item_type=ContextItemType.EPISODIC_MEMORY)


def _packet(
    *items: ContextItem,
    conflicts: tuple[ConflictNotice, ...] = (),
    budget: ContextBudget | None = None,
) -> ContextPacket:
    knowledge = tuple(item for item in items if item.item_type is ContextItemType.KNOWLEDGE)
    episodic = tuple(item for item in items if item.item_type is ContextItemType.EPISODIC_MEMORY)
    provenance = tuple(
        ProvenanceNotice(
            f"provenance:{item.item_id}",
            item.item_id,
            "fixture://typed-hook",
            "a" * 64,
            (EVIDENCE,),
        )
        for item in (*episodic, *knowledge)
    )
    return ContextPacket(
        PacketSchemaVersion.V1,
        RequestId.new(),
        SCOPE,
        "typed hook",
        None,
        NOW,
        None,
        sum(item.token_estimate for item in items),
        budget or ContextBudget(),
        "mnemo-test/1",
        episodic_memories=episodic,
        knowledge_items=knowledge,
        provenance=provenance,
        conflicts=conflicts,
    )


def _all_rendered(packet: ContextPacket) -> frozenset[str]:
    return frozenset(item.item_id for item in packet.items)


def test_typed_hook_modes_follow_the_master_switch() -> None:
    assert typed_hook_modes(PersonalSettings()) == TypedHookModes()
    assert TypedHookModes().any_on is False
    switched = PersonalSettings.from_dict(
        {
            **PersonalSettings().to_dict(),
            "experimental_typed_decisions_enabled": True,
            "typed_decision_modes": {"skill": "shadow", "tier_hint": "shadow"},
        }
    )
    modes = typed_hook_modes(switched)
    assert modes == TypedHookModes(OFF, OFF, SHADOW, SHADOW)
    assert modes.any_on is True


@pytest.mark.parametrize(
    ("label", "needs", "route"),
    [
        ("past_sessions", (YES, NO), AutomaticContextRoute.PRIOR_MEMORY),
        ("project_docs", (YES, NO), AutomaticContextRoute.KNOWLEDGE),
        ("code_structure", (NO, YES), AutomaticContextRoute.STRUCTURE),
        ("code_and_history", (YES, YES), AutomaticContextRoute.PRIOR_MEMORY),
        ("nothing", (NO, NO), None),
    ],
)
def test_memory_label_table_maps_needs_and_routes(
    label: str,
    needs: tuple[AutomaticContextNeed, AutomaticContextNeed],
    route: AutomaticContextRoute | None,
) -> None:
    outcome = memory_need_outcome(
        _choice("memory_need", label, 0.9), AutomaticContextRoute.PRIOR_MEMORY
    )
    assert (outcome.label, outcome.needs, outcome.route) == (label, needs, route)


@pytest.mark.parametrize(
    ("rules_route", "expected"),
    [
        (AutomaticContextRoute.STRUCTURE, AutomaticContextRoute.STRUCTURE),
        (AutomaticContextRoute.KNOWLEDGE, AutomaticContextRoute.KNOWLEDGE),
        (AutomaticContextRoute.SKILL_DISCOVERY, AutomaticContextRoute.KNOWLEDGE),
        (AutomaticContextRoute.NONE, AutomaticContextRoute.KNOWLEDGE),
    ],
)
def test_code_and_history_keeps_a_retrieving_rules_route_else_knowledge(
    rules_route: AutomaticContextRoute, expected: AutomaticContextRoute
) -> None:
    outcome = memory_need_outcome(_choice("memory_need", "code_and_history", 0.9), rules_route)
    assert outcome.route is expected


def test_unsure_or_missing_memory_answer_leaves_the_rules_needs() -> None:
    for result in (
        _choice("memory_need", "past_sessions", 0.59),
        _choice("skill_pick", "nothing", 0.9),
        None,
    ):
        outcome = memory_need_outcome(result, AutomaticContextRoute.KNOWLEDGE)
        assert (outcome.label, outcome.needs, outcome.route) == (None, None, None)


@pytest.mark.parametrize(
    ("confidence", "bucket"),
    [
        (0.0, "<0.5"),
        (0.49, "<0.5"),
        (0.5, "0.5-0.6"),
        (0.59, "0.5-0.6"),
        (0.6, "0.6-0.8"),
        (0.79, "0.6-0.8"),
        (0.8, "0.8-0.9"),
        (0.9, ">=0.9"),
        (1.0, ">=0.9"),
        (None, None),
    ],
)
def test_confidence_buckets_split_at_the_spec_boundaries(
    confidence: float | None, bucket: str | None
) -> None:
    assert confidence_bucket(confidence) == bucket


def names(step: TypedStepInput) -> list[str]:
    return [axis.name for axis in front_door_axes(step)]


def test_front_door_axes_follow_modes_and_hard_rules() -> None:
    assert names(BASE_STEP) == ["memory_need", "complexity", "tool_need", "skill_pick"]
    assert names(replace(BASE_STEP, hard_rule=True)) == ["skill_pick"]
    assert names(replace(BASE_STEP, modes=TypedHookModes(tier_hint=SHADOW))) == [
        "complexity",
        "tool_need",
    ]
    assert names(replace(BASE_STEP, skills_over_limit=True)) == [
        "memory_need",
        "complexity",
        "tool_need",
    ]
    assert names(replace(BASE_STEP, skill_names=())) == ["memory_need", "complexity", "tool_need"]
    assert names(replace(BASE_STEP, hard_rule=True, modes=TypedHookModes(SHADOW))) == []


def test_filler_candidates_keep_exempt_notes_out() -> None:
    useful, secret, private = _knowledge_id(1), _knowledge_id(2), _knowledge_id(3)
    conflict_a, conflict_b, broken = _knowledge_id(4), _knowledge_id(5), _knowledge_id(6)
    pinned, open_event = _event_id(1), _event_id(2)
    conflict = ConflictNotice(
        "knowledge-conflict:a:b", (conflict_a, conflict_b), (EVIDENCE,), ConflictState.UNRESOLVED
    )
    packet = _packet(
        _note(useful, "Invoice export", "Keep ledger sequence numbers exactly."),
        _note(secret, "Keys", "Rotate AKIAABCDEFGHIJKLMNOP before Friday."),
        _note(private, "Private", "Owner notes.", sensitivity=Sensitivity.CONFIDENTIAL),
        _note(conflict_a, "Grain", "Daily grain."),
        _note(conflict_b, "Grain", "Hourly grain."),
        _item(broken, "not json at all"),
        _event(pinned, "Keep retries idempotent."),
        _event(open_event, "FILLER chatter about lunch."),
        _event("checkpoint-recap:one", "A saved handoff recap."),
        conflicts=(conflict,),
    )
    candidates = filler_candidates(
        packet, pinned_item_ids=frozenset({pinned}), rendered_item_ids=_all_rendered(packet)
    )
    assert [candidate.item_id for candidate in candidates] == [useful, open_event]
    assert candidates[0].text == "Invoice export Keep ledger sequence numbers exactly."
    assert candidates[1].text == "FILLER chatter about lunch."


def test_filler_candidates_come_only_from_rendered_items() -> None:
    notes = [_note(_knowledge_id(index), "Ledger", f"Ledger fact {index}.") for index in range(6)]
    events = [_event(_event_id(index), f"Decision {index}.") for index in range(3)]
    packet = _packet(*notes, *events)
    rendered = frozenset({notes[0].item_id, notes[2].item_id, events[1].item_id})
    assert len(packet.items) > len(rendered)
    candidates = filler_candidates(packet, pinned_item_ids=frozenset(), rendered_item_ids=rendered)
    assert [candidate.item_id for candidate in candidates] == [
        notes[0].item_id,
        notes[2].item_id,
        events[1].item_id,
    ]
    assert (
        filler_candidates(packet, pinned_item_ids=frozenset(), rendered_item_ids=frozenset()) == ()
    )


def test_only_ids_get_context_can_fetch_are_candidates_or_dropped() -> None:
    fetchable_note = _note(_knowledge_id(1), "Grain", "FILLER daily grain.")
    fetchable_event = _event(_event_id(1), "FILLER lunch.")
    engine_memory = _event(f"episodic-memory:{UUID(int=30_001)}", "FILLER engine memory.")
    lifecycle = _event(f"checkpoint-lifecycle:{UUID(int=30_002)}", "FILLER lifecycle.")
    semantic = _note(f"knowledge-semantic:{UUID(int=30_003)}", "Grain", "FILLER semantic.")
    short_knowledge = _note("knowledge:useful", "Grain", "FILLER short id.")
    packet = _packet(
        fetchable_note, semantic, short_knowledge, fetchable_event, engine_memory, lifecycle
    )
    every_id = _all_rendered(packet)
    candidates = filler_candidates(packet, pinned_item_ids=frozenset(), rendered_item_ids=every_id)
    assert [candidate.item_id for candidate in candidates] == [
        fetchable_note.item_id,
        fetchable_event.item_id,
    ]
    reduced, notices = without_filler_notes(packet, tuple(sorted(every_id)))
    assert notices == (
        filler_omission(fetchable_event.item_id),
        filler_omission(fetchable_note.item_id),
    )
    assert [item.item_id for item in reduced.items] == [
        engine_memory.item_id,
        lifecycle.item_id,
        semantic.item_id,
        short_knowledge.item_id,
    ]


def test_filler_candidates_are_bounded_to_sixteen_notes_of_300_characters() -> None:
    packet = _packet(
        *(_note(_knowledge_id(index), "Long", "word " * 70) for index in range(20)),
        budget=ContextBudget(knowledge=8_000, total_limit=8_000),
    )
    candidates = filler_candidates(
        packet, pinned_item_ids=frozenset(), rendered_item_ids=_all_rendered(packet)
    )
    assert len(candidates) == MAXIMUM_FILLER_CHECKS == 16
    assert all(len(candidate.text) <= 300 for candidate in candidates)


def test_notes_to_drop_only_drops_confident_filler() -> None:
    candidates = tuple(FillerCandidate(_knowledge_id(index), "note") for index in range(4))
    outcomes = (
        FILLER,
        _answered(_choice("note_substance", "filler", 0.65, 0.65)),
        KEEP,
        _blocked(TypedDecisionUnavailableReason.TIMEOUT),
    )
    assert notes_to_drop(candidates, outcomes) == (_knowledge_id(0),)


def test_filler_notes_leave_one_lower_rank_omission_each() -> None:
    packet = _packet(
        _note(USEFUL_NOTE, "Invoice export", "Keep ledger order."),
        _note(FILLER_NOTE, "Chatter", "FILLER talk."),
        _event(FILLER_EVENT, "FILLER lunch."),
    )
    reduced, notices = without_filler_notes(packet, (FILLER_EVENT, FILLER_NOTE, _knowledge_id(99)))
    assert notices == (filler_omission(FILLER_EVENT), filler_omission(FILLER_NOTE))
    assert FILLER_OMISSION_DETAIL == "judged filler; fetch with get_context item_ids"
    assert notices[0].to_dict() == {
        "item_id": FILLER_EVENT,
        "reason": "lower_rank",
        "detail": FILLER_OMISSION_DETAIL,
    }
    assert all(notice.reason is OmissionReason.LOWER_RANK for notice in notices)
    assert [item.item_id for item in reduced.items] == [USEFUL_NOTE]
    assert reduced.declared_total_tokens == reduced.computed_total_tokens
    assert reduced.omissions[-2:] == notices
    assert ContextPacket.from_dict(reduced.to_dict()) == reduced
    assert omission_line(notices[1]) == "MNEMO_OMISSION " + json.dumps(
        notices[1].to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    assert without_filler_notes(packet, ()) == (packet, ())


def test_conflict_participants_are_never_removed() -> None:
    first, second = _knowledge_id(4), _knowledge_id(5)
    conflict = ConflictNotice(
        "knowledge-conflict:a:b", (first, second), (EVIDENCE,), ConflictState.UNRESOLVED
    )
    packet = _packet(
        _note(first, "Grain", "FILLER daily grain."),
        _note(second, "Grain", "Hourly grain."),
        conflicts=(conflict,),
    )
    assert without_filler_notes(packet, (first,)) == (packet, ())


@pytest.mark.parametrize(
    ("mode", "axis_built", "accepted", "keyword_top", "expected"),
    [
        (OFF, True, "test-plan", "test-plan", SkillComparison.NOT_ASKED),
        (SHADOW, False, None, "none", SkillComparison.SKIPPED),
        (SHADOW, True, None, "test-plan", SkillComparison.UNSURE),
        (LIVE, True, "test-plan", "test-plan", SkillComparison.AGREED),
        (LIVE, True, "none", "none", SkillComparison.AGREED),
        (SHADOW, True, "none", "release-notes", SkillComparison.DIFFERS),
    ],
)
def test_skill_comparison_states(
    mode: TypedDecisionMode,
    axis_built: bool,
    accepted: str | None,
    keyword_top: str,
    expected: SkillComparison,
) -> None:
    assert (
        skill_comparison(mode, axis_built=axis_built, accepted=accepted, keyword_top=keyword_top)
        is expected
    )


def test_effective_skill_names_follow_section_4_4() -> None:
    assert effective_skill_names(("release-notes",), None) == ("release-notes",)
    assert effective_skill_names(("release-notes",), "none") == ()
    assert effective_skill_names(("release-notes",), "test-plan") == ("test-plan",)


def test_task_size_hint_is_appended_or_attached_alone() -> None:
    assert with_task_size_hint(None) == HINT_TEXT
    assert with_task_size_hint("MNEMO_CONTEXT_END") == "MNEMO_CONTEXT_END\n" + HINT_TEXT
    assert (len(HINT_TEXT) + 3) // 4 <= 40


def test_shadow_records_every_answer_and_changes_nothing() -> None:
    decisions = combine_typed_decisions(
        BASE_STEP,
        TypedAnswers(FRONT, (KEEP, FILLER), LIGHT),
        step_ms=310,
        model_version="jev-1.13.0",
    )
    assert decisions.typed_needs is None and decisions.memory_route is None
    assert decisions.drop_item_ids == () and decisions.skill is None
    assert decisions.show_hint is False
    assert asdict(decisions.telemetry) == {
        "front_door_mode": "shadow",
        "relevance_mode": "shadow",
        "tier_hint_mode": "shadow",
        "skill_mode": "shadow",
        "front_door_outcome": "answered",
        "step_ms": 310,
        "model_version": "jev-1.13.0",
        "memory_label": "nothing",
        "memory_confidence_bucket": ">=0.9",
        "action": "none",
        "agrees_with_rules": False,
        "notes_checked": 2,
        "notes_dropped": 1,
        "notes_unanswered": 0,
        "tier": "light",
        "hint": "would_show",
        "skill": "differs",
    }


def test_live_returns_only_the_decisions_to_apply() -> None:
    decisions = combine_typed_decisions(
        replace(BASE_STEP, modes=ALL_LIVE),
        TypedAnswers(FRONT, (KEEP, FILLER), LIGHT),
        step_ms=310,
        model_version="jev-1.13.0",
    )
    assert decisions.typed_needs == (NO, NO)
    assert decisions.memory_route is None
    assert decisions.drop_item_ids == (FILLER_EVENT,)
    assert decisions.skill == "test-plan"
    assert decisions.show_hint is True
    assert decisions.telemetry.hint == "shown"


def test_hard_rule_leaves_memory_need_to_the_rules() -> None:
    skill_only = _answered(_choice("skill_pick", "none", 0.9))
    decisions = combine_typed_decisions(
        replace(BASE_STEP, modes=ALL_LIVE, hard_rule=True, filler_candidates=()),
        TypedAnswers(skill_only, (), None),
        step_ms=5,
        model_version=None,
    )
    assert decisions.typed_needs is None
    assert decisions.telemetry.memory_label is None
    assert decisions.telemetry.action is None
    assert decisions.telemetry.tier is None
    assert decisions.skill == "none"


def test_an_unavailable_front_door_falls_back_everywhere() -> None:
    blocked = _blocked(TypedDecisionUnavailableReason.DATA_ROUTE_BLOCKED)
    decisions = combine_typed_decisions(
        replace(BASE_STEP, modes=ALL_LIVE),
        TypedAnswers(
            blocked,
            (blocked, blocked),
            TierDecision("heavy", "unavailable:data_route_blocked", None),
        ),
        step_ms=2,
        model_version=None,
    )
    assert decisions.typed_needs is None and decisions.drop_item_ids == ()
    assert decisions.skill is None and decisions.show_hint is False
    telemetry = decisions.telemetry
    assert telemetry.front_door_outcome == "data_route_blocked"
    assert telemetry.memory_label == "unsure" and telemetry.memory_confidence_bucket is None
    assert (telemetry.notes_checked, telemetry.notes_dropped, telemetry.notes_unanswered) == (
        2,
        0,
        2,
    )
    assert (telemetry.tier, telemetry.hint, telemetry.skill) == ("heavy", "none", "unsure")


def test_below_bar_memory_answer_is_unsure_and_keeps_the_rules_needs() -> None:
    unsure = _answered(_choice("memory_need", "past_sessions", 0.58))
    decisions = combine_typed_decisions(
        replace(BASE_STEP, modes=TypedHookModes(front_door=LIVE), filler_candidates=()),
        TypedAnswers(unsure, (), None),
        step_ms=5,
        model_version="jev-1.13.0",
    )
    assert decisions.typed_needs is None
    assert decisions.telemetry.memory_label == "unsure"
    assert decisions.telemetry.memory_confidence_bucket == "0.5-0.6"
    assert decisions.telemetry.action is None and decisions.telemetry.agrees_with_rules is None


def test_no_front_door_request_is_not_asked() -> None:
    decisions = combine_typed_decisions(
        replace(BASE_STEP, modes=TypedHookModes(relevance=SHADOW)),
        TypedAnswers(None, (KEEP, FILLER), None),
        step_ms=5,
        model_version=None,
    )
    assert decisions.telemetry.front_door_outcome == "not_asked"
    assert decisions.telemetry.skill == "not_asked"
    assert decisions.telemetry.hint == "none"


def test_typed_step_error_changes_nothing() -> None:
    decisions = typed_step_error_decisions(ALL_LIVE, 41)
    assert decisions.typed_needs is None and decisions.drop_item_ids == ()
    assert decisions.skill is None and decisions.show_hint is False
    assert decisions.telemetry.front_door_outcome == "typed_step_error"
    assert decisions.telemetry.step_ms == 41
    assert decisions.telemetry.skill == "unsure"
    assert typed_step_error_decisions(TypedHookModes(), 0).telemetry.skill == "not_asked"


def test_telemetry_values_never_carry_prompt_note_or_skill_text() -> None:
    step = replace(
        BASE_STEP,
        prompt="private-prompt-7f3a finance reconciliation variance",
        skill_names=("private-skill-9b1d", "test-plan"),
        keyword_skill_names=("private-skill-9b1d",),
        filler_candidates=(FillerCandidate(_knowledge_id(9), "private-note-21c9 FILLER"),),
    )
    decisions = combine_typed_decisions(
        step,
        TypedAnswers(FRONT, (FILLER,), LIGHT),
        step_ms=1,
        model_version="jev-1.13.0",
    )
    encoded = json.dumps(asdict(decisions.telemetry))
    for marker in ("private-prompt-7f3a", "private-skill-9b1d", "private-note-21c9", "test-plan"):
        assert marker not in encoded
