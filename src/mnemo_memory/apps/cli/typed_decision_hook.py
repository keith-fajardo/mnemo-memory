"""Per-prompt typed-decision step for the automatic-memory hook (spec 2026-10-02 §3-§4).

The functions here turn guard outcomes into plain values and never read or write files.
``main.py`` prepares the local inputs, runs the step, and applies live answers on top of
today's rules result. Prompt text, note text and skill names never enter
``TypedTelemetryValues``.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

from mnemo_memory.packages.application import PersonalSettings
from mnemo_memory.packages.application.context_routing import (
    AutomaticContextNeed,
    AutomaticContextRoute,
    AutomaticContextShadowAction,
    AutomaticContextShadowPlan,
    LearnedRoutePhrase,
    plan_automatic_context_needs,
)
from mnemo_memory.packages.application.unified_context import is_requestable_item_id
from mnemo_memory.packages.domain import (
    ConflictState,
    ContextItem,
    ContextItemType,
    ContextPacket,
    OmissionNotice,
    OmissionReason,
    Sensitivity,
    TypedDecisionKind,
    TypedDecisionMode,
    TypedDecisionUnavailableReason,
)
from mnemo_memory.packages.model_gateway.cascade_router import (
    AxisRoutedClassifier,
    ClassifierAxis,
    ClassifierResult,
    PrecomputedClassifier,
)
from mnemo_memory.packages.model_gateway.decision_axes import (
    COMPLEXITY,
    FILLER_CHECK_BUDGET_SECONDS,
    HINT_TEXT,
    MEMORY_NEED,
    NOTE_SUBSTANCE,
    SKILL_PICK_NAME,
    SKILL_PICK_NONE,
    TOOL_NEED,
    accepted_choice,
    accepted_skill,
    hint_eligible,
    note_text,
    should_drop_note,
    skill_pick_axis,
    tier_committee,
)
from mnemo_memory.packages.model_gateway.rule_axes import RISK_AXIS, RiskTermClassifier
from mnemo_memory.packages.model_gateway.typed_decisions import (
    GuardedTypedDecisionClassifier,
    TierDecision,
    TypedDecisionOutcome,
    TypedDecisionRecord,
    TypedDecisionRecorder,
    decide_tier,
)
from mnemo_memory.packages.policy.content_safety import contains_high_confidence_secret

OFF = TypedDecisionMode.OFF
SHADOW = TypedDecisionMode.SHADOW
LIVE = TypedDecisionMode.LIVE
HOOK_KINDS: tuple[TypedDecisionKind, ...] = (
    TypedDecisionKind.FRONT_DOOR,
    TypedDecisionKind.RELEVANCE,
    TypedDecisionKind.TIER_HINT,
    TypedDecisionKind.SKILL,
)
MAXIMUM_FILLER_CHECKS = 16
FILLER_OMISSION_DETAIL = "judged filler; fetch with get_context item_ids"
APPROVED_EVENT_ITEM_PREFIX = "approved-episodic:"
UNSURE = "unsure"
_YES = AutomaticContextNeed.YES
_NO = AutomaticContextNeed.NO
_PINNED_MODEL_VERSION = re.compile(r"jev-[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}")
_RETRIEVAL_ROUTES = frozenset(
    {
        AutomaticContextRoute.PRIOR_MEMORY,
        AutomaticContextRoute.KNOWLEDGE,
        AutomaticContextRoute.STRUCTURE,
    }
)
# Spec §4.1: label -> (long_term, structural) and the retrieval route in live mode.
_NEEDS_BY_LABEL: dict[str, tuple[AutomaticContextNeed, AutomaticContextNeed]] = {
    "past_sessions": (_YES, _NO),
    "project_docs": (_YES, _NO),
    "code_structure": (_NO, _YES),
    "code_and_history": (_YES, _YES),
    "nothing": (_NO, _NO),
}
_ROUTE_BY_LABEL: dict[str, AutomaticContextRoute | None] = {
    "past_sessions": AutomaticContextRoute.PRIOR_MEMORY,
    "project_docs": AutomaticContextRoute.KNOWLEDGE,
    "code_structure": AutomaticContextRoute.STRUCTURE,
    "nothing": None,
}


@dataclass(frozen=True, slots=True)
class TypedHookModes:
    """The four hook decision modes; all ``off`` means today's path, byte for byte."""

    front_door: TypedDecisionMode = TypedDecisionMode.OFF
    relevance: TypedDecisionMode = TypedDecisionMode.OFF
    tier_hint: TypedDecisionMode = TypedDecisionMode.OFF
    skill: TypedDecisionMode = TypedDecisionMode.OFF

    def __post_init__(self) -> None:
        if any(not isinstance(mode, TypedDecisionMode) for mode in self._modes()):
            raise TypeError("typed hook modes are invalid")

    def _modes(self) -> tuple[TypedDecisionMode, ...]:
        return (self.front_door, self.relevance, self.tier_hint, self.skill)

    @property
    def any_on(self) -> bool:
        return any(mode is not TypedDecisionMode.OFF for mode in self._modes())


def typed_hook_modes(settings: PersonalSettings) -> TypedHookModes:
    """Read the four hook modes from settings; the master switch off means all off."""

    if not settings.experimental_typed_decisions_enabled:
        return TypedHookModes()
    return TypedHookModes(
        settings.typed_decision_mode(TypedDecisionKind.FRONT_DOOR),
        settings.typed_decision_mode(TypedDecisionKind.RELEVANCE),
        settings.typed_decision_mode(TypedDecisionKind.TIER_HINT),
        settings.typed_decision_mode(TypedDecisionKind.SKILL),
    )


@dataclass(frozen=True, slots=True)
class FillerCandidate:
    """One eligible pre-fetched note: its item ID and the bounded text Jev judges."""

    item_id: str
    text: str


@dataclass(frozen=True, slots=True)
class TypedStepInput:
    """Everything the step needs, already bounded and read locally (spec §3 step 2)."""

    prompt: str
    modes: TypedHookModes
    hard_rule: bool
    rules_plan: AutomaticContextShadowPlan
    rules_route: AutomaticContextRoute
    learned_phrases: tuple[LearnedRoutePhrase, ...] = ()
    skill_names: tuple[str, ...] = ()
    skills_over_limit: bool = False
    keyword_skill_names: tuple[str, ...] = ()
    filler_candidates: tuple[FillerCandidate, ...] = ()


@dataclass(frozen=True, slots=True)
class TypedAnswers:
    """Guard outcomes for one prompt: the front door (or not asked), each note, the tier."""

    front_door: TypedDecisionOutcome | None
    fillers: tuple[TypedDecisionOutcome, ...] = ()
    tier: TierDecision | None = None


@dataclass(frozen=True, slots=True)
class MemoryNeedOutcome:
    """An accepted memory-need label as ``(long_term, structural)`` needs and a route."""

    label: str | None
    needs: tuple[AutomaticContextNeed, AutomaticContextNeed] | None
    route: AutomaticContextRoute | None


class SkillComparison(StrEnum):
    """Skill pick compared with keyword matching; never a skill name (spec §4.4)."""

    AGREED = "agreed"
    DIFFERS = "differs"
    UNSURE = "unsure"
    SKIPPED = "skipped"
    NOT_ASKED = "not_asked"


@dataclass(frozen=True, slots=True)
class TypedTelemetryValues:
    """The spec §7 ``typed_v1`` values for one prompt: closed values, counts and booleans."""

    front_door_mode: str
    relevance_mode: str
    tier_hint_mode: str
    skill_mode: str
    front_door_outcome: str
    step_ms: int
    model_version: str | None
    memory_label: str | None
    memory_confidence_bucket: str | None
    action: str | None
    agrees_with_rules: bool | None
    notes_checked: int
    notes_dropped: int
    notes_unanswered: int
    tier: str | None
    hint: str
    skill: str


@dataclass(frozen=True, slots=True)
class TypedPromptDecisions:
    """What to apply live (only for live modes) plus the telemetry for every mode."""

    typed_needs: tuple[AutomaticContextNeed, AutomaticContextNeed] | None
    memory_route: AutomaticContextRoute | None
    drop_item_ids: tuple[str, ...]
    skill: str | None
    show_hint: bool
    telemetry: TypedTelemetryValues


GuardFactory = Callable[[TypedDecisionRecorder], GuardedTypedDecisionClassifier | None]
DecisionObserver = Callable[[TypedStepInput, TypedPromptDecisions], None]


@dataclass(frozen=True, slots=True)
class TypedHookOverrides:
    """Replay-only seam: a synthetic-source guard factory and fixed modes (spec §3).

    The ``automatic-memory-hook`` command never builds one, so the real hook always uses the
    runtime guard and the locked settings. ``observer`` lets the replay score decisions.
    """

    guard_factory: GuardFactory
    modes: TypedHookModes
    observer: DecisionObserver | None = None


_NO_MEMORY_ANSWER = MemoryNeedOutcome(None, None, None)


def confidence_bucket(confidence: float | None) -> str | None:
    if confidence is None:
        return None
    if confidence < 0.5:
        return "<0.5"
    if confidence < 0.6:
        return "0.5-0.6"
    if confidence < 0.8:
        return "0.6-0.8"
    if confidence < 0.9:
        return "0.8-0.9"
    return ">=0.9"


def memory_need_outcome(
    result: ClassifierResult | None, rules_route: AutomaticContextRoute
) -> MemoryNeedOutcome:
    """Map an accepted memory-need label to needs and a route (spec §4.1 table).

    A missing, foreign or below-bar answer returns no needs, so the rules' own needs stand.
    """

    if result is None or result.axis_name != MEMORY_NEED.name:
        return _NO_MEMORY_ANSWER
    label = accepted_choice(result)
    if label is None or label not in _NEEDS_BY_LABEL:
        return _NO_MEMORY_ANSWER
    route: AutomaticContextRoute | None
    if label == "code_and_history":
        # No single route fetches both today: keep a retrieving rules route, else knowledge.
        route = rules_route if rules_route in _RETRIEVAL_ROUTES else AutomaticContextRoute.KNOWLEDGE
    else:
        route = _ROUTE_BY_LABEL[label]
    return MemoryNeedOutcome(label, _NEEDS_BY_LABEL[label], route)


def skill_axis_for(step: TypedStepInput) -> ClassifierAxis | None:
    if step.modes.skill is OFF or step.skills_over_limit:
        return None
    return skill_pick_axis(step.skill_names)


def front_door_axes(step: TypedStepInput) -> tuple[ClassifierAxis, ...]:
    """Axes for the single front-door request; a hard rule leaves only the skill question."""

    axes: list[ClassifierAxis] = []
    if not step.hard_rule:
        if step.modes.front_door is not OFF:
            axes.append(MEMORY_NEED)
        if step.modes.tier_hint is not OFF:
            axes.extend((COMPLEXITY, TOOL_NEED))
    skill_axis = skill_axis_for(step)
    if skill_axis is not None:
        axes.append(skill_axis)
    return tuple(axes)


def filler_candidates(
    packet: ContextPacket,
    *,
    pinned_item_ids: frozenset[str],
    rendered_item_ids: frozenset[str],
) -> tuple[FillerCandidate, ...]:
    """Eligible pre-fetched notes: knowledge sections and approved events (spec §4.2).

    Only notes the agent actually sees are eligible: ``rendered_item_ids`` holds the IDs on
    the ``MNEMO_ITEM`` lines of the rules render. A note fetched into the packet but cut from
    the render is never a candidate, because dropping it would add an omission line naming an
    ID the agent never saw (spec §5.2). Only IDs ``get_context item_ids`` can fetch back are
    eligible, so every drop stays reachable (spec §5).

    Never sent, always kept: pinned items, conflict participants, non-``normal`` sensitivity,
    text the secret scan flags, and notes whose stored text cannot be read. The active
    checkpoint, procedures and skills are not in these sections at all.
    """

    protected = {item_id for conflict in packet.conflicts for item_id in conflict.item_ids}
    candidates: list[FillerCandidate] = []
    for item in (*packet.knowledge_items, *packet.episodic_memories):
        if (
            item.item_id not in rendered_item_ids
            or not is_requestable_item_id(item.item_id)
            or item.item_id in pinned_item_ids
            or item.item_id in protected
            or item.conflict_state is not ConflictState.NONE
            or item.sensitivity is not Sensitivity.NORMAL
        ):
            continue
        source = _note_source_text(item)
        if source is None:
            continue
        try:
            judged = note_text(source)
        except ValueError:
            continue
        if contains_high_confidence_secret(source, judged):
            continue
        candidates.append(FillerCandidate(item.item_id, judged))
        if len(candidates) == MAXIMUM_FILLER_CHECKS:
            break
    return tuple(candidates)


def _note_source_text(item: ContextItem) -> str | None:
    try:
        value = json.loads(item.content)
    except ValueError:
        return None
    if not isinstance(value, dict):
        return None
    if item.item_type is ContextItemType.KNOWLEDGE:
        heading, content = value.get("heading"), value.get("content")
        if not isinstance(content, str):
            return None
        return f"{heading}\n{content}" if isinstance(heading, str) and heading.strip() else content
    summary = value.get("summary")
    return summary if isinstance(summary, str) else None


def notes_to_drop(
    candidates: Sequence[FillerCandidate], outcomes: Sequence[TypedDecisionOutcome]
) -> tuple[str, ...]:
    """Item IDs judged confident filler (p(filler) >= 0.7); unanswered or unsure notes stay."""

    drops: list[str] = []
    for candidate, outcome in zip(candidates, outcomes, strict=True):
        result = next(
            (value for value in outcome.results if value.axis_name == NOTE_SUBSTANCE.name), None
        )
        if should_drop_note(result):
            drops.append(candidate.item_id)
    return tuple(drops)


def filler_omission(item_id: str) -> OmissionNotice:
    """One standard omission for one dropped note, valid under the unchanged v1 schema (§5)."""

    return OmissionNotice(item_id, OmissionReason.LOWER_RANK, FILLER_OMISSION_DETAIL)


def without_filler_notes(
    packet: ContextPacket, item_ids: Sequence[str]
) -> tuple[ContextPacket, tuple[OmissionNotice, ...]]:
    """Remove judged-filler notes and add one ``lower_rank`` omission per removed note.

    Only knowledge sections and approved events whose IDs ``get_context item_ids`` can fetch
    back, and that are not conflict participants, can be removed; anything else in
    ``item_ids`` is ignored and kept. Freed space is not refilled.
    """

    requested = frozenset(item_ids)
    protected = {item_id for conflict in packet.conflicts for item_id in conflict.item_ids}
    removable = tuple(
        item.item_id
        for item in (*packet.episodic_memories, *packet.knowledge_items)
        if item.item_id in requested
        and item.item_id not in protected
        and is_requestable_item_id(item.item_id)
    )
    if not removable:
        return packet, ()
    dropped = frozenset(removable)
    notices = tuple(filler_omission(item_id) for item_id in removable)
    removed_tokens = sum(
        item.token_estimate for item in packet.items if item.item_id in dropped
    ) + sum(notice.token_estimate for notice in packet.provenance if notice.item_id in dropped)
    reduced = replace(
        packet,
        declared_total_tokens=packet.declared_total_tokens - removed_tokens,
        knowledge_items=tuple(
            item for item in packet.knowledge_items if item.item_id not in dropped
        ),
        episodic_memories=tuple(
            item for item in packet.episodic_memories if item.item_id not in dropped
        ),
        provenance=tuple(item for item in packet.provenance if item.item_id not in dropped),
        omissions=(*packet.omissions, *notices),
    )
    return reduced, notices


def omission_line(notice: OmissionNotice) -> str:
    """The exact line ``render_automatic_context_packet`` writes for ``notice``."""

    return "MNEMO_OMISSION " + json.dumps(
        notice.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )


def skill_comparison(
    mode: TypedDecisionMode, *, axis_built: bool, accepted: str | None, keyword_top: str
) -> SkillComparison:
    if mode is OFF:
        return SkillComparison.NOT_ASKED
    if not axis_built:
        return SkillComparison.SKIPPED
    if accepted is None:
        return SkillComparison.UNSURE
    return SkillComparison.AGREED if accepted == keyword_top else SkillComparison.DIFFERS


def effective_skill_names(keyword_names: Sequence[str], accepted: str | None) -> tuple[str, ...]:
    """Candidates after a live answer: a skill, none, or (unsure) keyword matching (§4.4)."""

    if accepted is None:
        return tuple(keyword_names)
    if accepted == SKILL_PICK_NONE:
        return ()
    return (accepted,)


def with_task_size_hint(rendered: str | None) -> str:
    """Append the ~20-token hint to whatever is attached, or attach it alone (spec §4.3)."""

    return HINT_TEXT if rendered is None else f"{rendered}\n{HINT_TEXT}"


def combine_typed_decisions(
    step: TypedStepInput,
    answers: TypedAnswers,
    *,
    step_ms: int,
    model_version: str | None,
) -> TypedPromptDecisions:
    """Turn guard outcomes into live decisions and content-free telemetry (pure)."""

    modes = step.modes
    front = answers.front_door
    results = {} if front is None else {result.axis_name: result for result in front.results}

    memory_asked = front is not None and modes.front_door is not OFF and not step.hard_rule
    memory_result = results.get(MEMORY_NEED.name)
    memory = (
        memory_need_outcome(memory_result, step.rules_route) if memory_asked else _NO_MEMORY_ANSWER
    )
    typed_action: AutomaticContextShadowAction | None = None
    if memory.needs is not None:
        typed_action = plan_automatic_context_needs(
            step.prompt, learned_phrases=step.learned_phrases, typed_needs=memory.needs
        ).action

    drops = (
        notes_to_drop(step.filler_candidates, answers.fillers) if modes.relevance is not OFF else ()
    )
    unanswered = sum(outcome.unavailable_reason is not None for outcome in answers.fillers)

    tier = None if answers.tier is None else answers.tier.route
    eligible = tier is not None and hint_eligible(tier, results.get(TOOL_NEED.name))
    if eligible and modes.tier_hint is LIVE:
        hint = "shown"
    elif eligible and modes.tier_hint is SHADOW:
        hint = "would_show"
    else:
        hint = "none"

    axis_built = skill_axis_for(step) is not None
    accepted = accepted_skill(results.get(SKILL_PICK_NAME)) if axis_built else None
    keyword_top = step.keyword_skill_names[0] if step.keyword_skill_names else SKILL_PICK_NONE
    comparison = skill_comparison(
        modes.skill, axis_built=axis_built, accepted=accepted, keyword_top=keyword_top
    )

    if front is None:
        front_outcome = "not_asked"
    elif front.unavailable_reason is None:
        front_outcome = "answered"
    else:
        front_outcome = front.unavailable_reason.value

    telemetry = TypedTelemetryValues(
        front_door_mode=modes.front_door.value,
        relevance_mode=modes.relevance.value,
        tier_hint_mode=modes.tier_hint.value,
        skill_mode=modes.skill.value,
        front_door_outcome=front_outcome,
        step_ms=step_ms,
        model_version=model_version,
        memory_label=None if not memory_asked else (memory.label or UNSURE),
        memory_confidence_bucket=(
            None
            if not memory_asked or memory_result is None
            else confidence_bucket(memory_result.confidence)
        ),
        action=None if typed_action is None else typed_action.value,
        agrees_with_rules=(
            None if typed_action is None else typed_action is step.rules_plan.action
        ),
        notes_checked=len(answers.fillers),
        notes_dropped=len(drops),
        notes_unanswered=unanswered,
        tier=tier,
        hint=hint,
        skill=comparison.value,
    )
    live_needs = memory.needs if modes.front_door is LIVE else None
    return TypedPromptDecisions(
        typed_needs=live_needs,
        memory_route=memory.route if live_needs is not None else None,
        drop_item_ids=drops if modes.relevance is LIVE else (),
        skill=accepted if modes.skill is LIVE else None,
        show_hint=eligible and modes.tier_hint is LIVE,
        telemetry=telemetry,
    )


def typed_step_error_decisions(modes: TypedHookModes, step_ms: int) -> TypedPromptDecisions:
    """Whole-step fallback: no live change, telemetry says ``typed_step_error`` (spec §7)."""

    return TypedPromptDecisions(
        None,
        None,
        (),
        None,
        False,
        TypedTelemetryValues(
            front_door_mode=modes.front_door.value,
            relevance_mode=modes.relevance.value,
            tier_hint_mode=modes.tier_hint.value,
            skill_mode=modes.skill.value,
            front_door_outcome="typed_step_error",
            step_ms=step_ms,
            model_version=None,
            memory_label=None,
            memory_confidence_bucket=None,
            action=None,
            agrees_with_rules=None,
            notes_checked=0,
            notes_dropped=0,
            notes_unanswered=0,
            tier=None,
            hint="none",
            skill=(
                SkillComparison.NOT_ASKED.value
                if modes.skill is OFF
                else SkillComparison.UNSURE.value
            ),
        ),
    )


class RuntimeTypedDecisionRecorder:
    """Collect one prompt's per-request guard records in memory (content-free)."""

    def __init__(self) -> None:
        self.records: list[TypedDecisionRecord] = []

    def record(self, record: TypedDecisionRecord) -> None:
        self.records.append(record)


def pinned_model_version(records: Sequence[TypedDecisionRecord]) -> str | None:
    """Fold per-request records into the one model version telemetry may hold."""

    for record in records:
        version = record.model_version
        if (
            record.outcome == "answered"
            and version is not None
            and _PINNED_MODEL_VERSION.fullmatch(version) is not None
        ):
            return version
    return None


async def ask_typed_questions(
    guard: GuardedTypedDecisionClassifier, step: TypedStepInput
) -> TypedAnswers:
    """Send the front-door request and every note request at once under one 0.8 s cap."""

    axes = front_door_axes(step)
    requests: list[tuple[Sequence[ClassifierAxis], str]] = []
    if axes:
        requests.append((axes, step.prompt))
    requests.extend(((NOTE_SUBSTANCE,), candidate.text) for candidate in step.filler_candidates)
    outcomes: tuple[TypedDecisionOutcome, ...] = ()
    if requests:
        outcomes = await guard.ask_each(
            requests, total_deadline_seconds=FILLER_CHECK_BUDGET_SECONDS
        )
    front = outcomes[0] if axes else None
    fillers = outcomes[1:] if axes else outcomes
    tier: TierDecision | None = None
    if front is not None and step.modes.tier_hint is not OFF and not step.hard_rule:
        tier = await _tier(front, step.prompt)
    return TypedAnswers(front, tuple(fillers), tier)


async def _tier(front: TypedDecisionOutcome, prompt: str) -> TierDecision:
    """The phase-1 committee over answers already in hand; unavailable resolves to heavy."""

    if front.unavailable_reason is not None:
        return TierDecision("heavy", f"unavailable:{front.unavailable_reason.value}", None)
    answered = PrecomputedClassifier(front.results)
    classifier = AxisRoutedClassifier(
        {
            COMPLEXITY.name: answered,
            TOOL_NEED.name: answered,
            RISK_AXIS.name: RiskTermClassifier(),
        }
    )
    return await decide_tier(tier_committee(), classifier, prompt)


def decide_typed_prompt(
    guard_factory: GuardFactory,
    step: TypedStepInput,
    *,
    started: float,
    clock: Callable[[], float] = time.monotonic,
) -> TypedPromptDecisions:
    """Run the step with exactly one ``asyncio.run``; any exception is ``typed_step_error``."""

    try:
        recorder = RuntimeTypedDecisionRecorder()
        guard = guard_factory(recorder)
        if guard is None:
            answers = _unavailable_answers(step, TypedDecisionUnavailableReason.DISABLED)
        else:
            coroutine = ask_typed_questions(guard, step)
            try:
                answers = asyncio.run(coroutine)
            finally:
                coroutine.close()  # no "never awaited" warning when a loop is already running
        return combine_typed_decisions(
            step,
            answers,
            step_ms=_elapsed_ms(started, clock),
            model_version=pinned_model_version(recorder.records),
        )
    except Exception:
        return typed_step_error_decisions(step.modes, _elapsed_ms(started, clock))


def _unavailable_answers(
    step: TypedStepInput, reason: TypedDecisionUnavailableReason
) -> TypedAnswers:
    unavailable = TypedDecisionOutcome((), reason, 0, None)
    front = unavailable if front_door_axes(step) else None
    tier: TierDecision | None = None
    if front is not None and step.modes.tier_hint is not OFF and not step.hard_rule:
        tier = TierDecision("heavy", f"unavailable:{reason.value}", None)
    return TypedAnswers(front, tuple(unavailable for _ in step.filler_candidates), tier)


def _elapsed_ms(started: float, clock: Callable[[], float]) -> int:
    return max(0, min(10_000_000, round((clock() - started) * 1_000)))
