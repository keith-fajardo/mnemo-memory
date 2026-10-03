"""Phase-1 typed-decision questions, thresholds and committee (spec §6).

Every threshold is a starting value, tuned on synthetic fixtures and recorded with the pinned
model version. Instructions stay short because they dominate billed input tokens.

The memory need is a single five-way choice question (two interacting yes/no questions failed
the first live run). The filler check runs once per stored note, with the note itself as the
judged text; it cannot judge whether a note is relevant to a request, only whether it carries
task information at all.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from enum import StrEnum

from mnemo_memory.packages.domain import EpisodicMemoryKind

from .cascade_router import (
    YES_NO_LABELS,
    AxisKind,
    CascadeCommittee,
    CascadeRouterError,
    ClassifierAxis,
    ClassifierResult,
)
from .rule_axes import RISK_AXIS

FILLER_DROP_AT = 0.7
# Bump when NOTE_SUBSTANCE's wording, labels or scores change: verdicts cached for the old
# question then stop counting (spec 2026-10-03 §5).
FILLER_QUESTION_VERSION = 1
FILLER_CHECK_BUDGET_SECONDS = 0.8  # the prompt hook's cap for its one front-door request
NOTE_TEXT_CHARACTERS = 300
WORTH_SKIP_AT = 0.3
CHOICE_CONFIDENCE_BAR = 0.6
TIER_ESCALATION_THRESHOLD = 0.5

HINT_TEXT = "Mnemo: light, reading-heavy task; a Haiku subagent could do the reading."

MEMORY_NEED = ClassifierAxis(
    "memory_need",
    "What must the assistant look up, beyond this message, to answer it",
    ("past_sessions", "project_docs", "code_structure", "code_and_history", "nothing"),
    0.0,
    criteria=(
        ("past_sessions", "Decisions, progress or history from earlier work sessions"),
        ("project_docs", "Project documents, policies, ADRs, runbooks or notes"),
        ("code_structure", "Which files, modules, symbols, call paths, schemas or lineage exist"),
        ("code_and_history", "Both code structure and earlier decisions or documents"),
        ("nothing", "Everything needed is in the message or is general knowledge"),
    ),
)
NOTE_SUBSTANCE = ClassifierAxis(
    "note_substance",
    "Does this stored note carry real task information",
    ("task_information", "filler"),
    0.0,
    criteria=(
        (
            "task_information",
            "A goal, constraint, warning, failure, decision, fact, result, next step or "
            "open question",
        ),
        ("filler", "Chit-chat or background noise with no task information"),
    ),
    label_scores=(0.0, 1.0),  # escalation_score == p(filler)
)
COMPLEXITY = ClassifierAxis(
    "complexity",
    "How hard is this task for an AI coding assistant",
    ("light", "heavy"),
    0.6,
    criteria=(
        ("light", "Simple lookup, rename, formatting or summarizing"),
        ("heavy", "Needs reasoning across several facts, design judgement, or risky changes"),
    ),
    label_scores=(0.0, 1.0),
)
TOOL_NEED = ClassifierAxis(
    "tool_need",
    "What kind of tool work the task needs",
    ("none", "read_heavy", "edit"),
    0.4,
    criteria=(
        ("none", "Answerable without tools"),
        ("read_heavy", "Mostly reading or searching files"),
        ("edit", "Changing files or running commands"),
    ),
    label_scores=(0.0, 0.25, 0.75),
)
WORTH_REMEMBERING = ClassifierAxis(
    "worth_remembering",
    "This event records a durable decision, failure, outcome, lesson or preference worth "
    "remembering in a later session",
    YES_NO_LABELS,
    0.0,
    kind=AxisKind.YES_NO,
)
EPISODIC_KIND = ClassifierAxis(
    "episodic_kind",
    "Which kind of memory this event is",
    tuple(kind.value for kind in EpisodicMemoryKind),
    0.0,
    criteria=(
        ("decision", "A choice that was made"),
        ("failure", "Something that broke, or an approach that did not work"),
        ("outcome", "A result that was achieved, shipped or fixed"),
        ("lesson", "Something learned for next time"),
        ("preference", "How someone likes things done"),
    ),
)

FRONT_DOOR_AXES = (MEMORY_NEED, COMPLEXITY, TOOL_NEED)
TIER_AXES = (COMPLEXITY, TOOL_NEED, RISK_AXIS)


class NeedAnswer(StrEnum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


def tier_committee() -> CascadeCommittee:
    return CascadeCommittee(TIER_AXES, escalation_threshold=TIER_ESCALATION_THRESHOLD)


def needs_from_memory_choice(result: ClassifierResult | None) -> tuple[NeedAnswer, NeedAnswer]:
    """Map the memory-need choice to ``(long_term, structure)`` answers.

    A missing or low-confidence answer means unknown for both, which leads to lazy pull.
    """

    label = accepted_choice(result)
    if label is None:
        return NeedAnswer.UNKNOWN, NeedAnswer.UNKNOWN
    if label in ("past_sessions", "project_docs"):
        return NeedAnswer.YES, NeedAnswer.NO
    if label == "code_structure":
        return NeedAnswer.NO, NeedAnswer.YES
    if label == "code_and_history":
        return NeedAnswer.YES, NeedAnswer.YES
    if label == "nothing":
        return NeedAnswer.NO, NeedAnswer.NO
    return NeedAnswer.UNKNOWN, NeedAnswer.UNKNOWN


def note_text(snippet: str) -> str:
    """Collapse whitespace and cap a stored note so it can be judged on its own."""

    text = " ".join(snippet.split())[:NOTE_TEXT_CHARACTERS]
    if not text:
        raise ValueError("note text is empty")
    return text


def should_drop_filler(p_filler: float | None) -> bool:
    """Drop only confident filler: a p(filler) at or above the bar; no verdict means keep."""

    return p_filler is not None and p_filler >= FILLER_DROP_AT


def should_drop_note(result: ClassifierResult | None) -> bool:
    """Drop only confident filler; keep is the safe side."""

    return result is not None and should_drop_filler(result.escalation_score)


def worth_extracting(result: ClassifierResult | None) -> bool:
    """Skip extraction only on a confident no; an unavailable answer runs the model."""

    return result is None or result.escalation_score > WORTH_SKIP_AT


def accepted_choice(result: ClassifierResult | None) -> str | None:
    """Use a choice label only when its confidence reaches the bar."""

    if result is None or result.confidence is None or result.confidence < CHOICE_CONFIDENCE_BAR:
        return None
    return result.label


def hint_eligible(route: str, tool_need: ClassifierResult | None) -> bool:
    """Hint only light, reading-heavy tasks; delegating tiny or hard tasks wastes tokens."""

    return route == "light" and accepted_choice(tool_need) == "read_heavy"


SKILL_PICK_NAME = "skill_pick"
SKILL_PICK_NONE = "none"
SKILL_PICK_MAXIMUM_SKILLS = 32
_SKILL_LABEL = re.compile(r"[a-z][a-z0-9_-]{0,63}")


def skill_pick_axis(skill_names: Sequence[str]) -> ClassifierAxis | None:
    """Build the skill-pick choice: the current skill names plus ``none`` (spec 2026-10-02 §4.4).

    Returns ``None`` — keyword matching stays in charge — for no skills, more than 32, a name
    that is not a registry name, a duplicate, or a skill literally named ``none``.
    """

    names = tuple(skill_names)
    if not 1 <= len(names) <= SKILL_PICK_MAXIMUM_SKILLS:
        return None
    if any(not isinstance(name, str) or _SKILL_LABEL.fullmatch(name) is None for name in names):
        return None
    try:
        return ClassifierAxis(
            SKILL_PICK_NAME,
            "Which listed project skill, if any, fits this request",
            (*names, SKILL_PICK_NONE),
            0.0,
        )
    except CascadeRouterError:
        return None


def accepted_skill(result: ClassifierResult | None) -> str | None:
    """Return the accepted skill label (a skill name or ``none``), or ``None`` when unsure."""

    if result is None or result.axis_name != SKILL_PICK_NAME:
        return None
    return accepted_choice(result)
