"""Phase-1 typed-decision questions, thresholds and committee (spec §6).

Every threshold is a starting value, tuned on synthetic fixtures and recorded with the pinned
model version. Instructions stay short because they dominate billed input tokens.
"""

from __future__ import annotations

from enum import StrEnum

from mnemo_memory.packages.domain import EpisodicMemoryKind

from .cascade_router import (
    YES_NO_LABELS,
    AxisKind,
    CascadeCommittee,
    ClassifierAxis,
    ClassifierResult,
)
from .rule_axes import RISK_AXIS

NEED_YES_AT = 0.7
NEED_NO_AT = 0.3
RELEVANCE_DROP_AT = 0.2
RELEVANCE_SNIPPET_CHARACTERS = 300
WORTH_SKIP_AT = 0.3
CHOICE_CONFIDENCE_BAR = 0.6
TIER_ESCALATION_THRESHOLD = 0.5
_MAXIMUM_RELEVANCE_AXES = 32
_RELEVANCE_PREFIX = "This stored note would help answer the request: "

HINT_TEXT = "Mnemo: light, reading-heavy task; a Haiku subagent could do the reading."

NEEDS_LONG_TERM = ClassifierAxis(
    "needs_long_term",
    "Answering needs stored memory from earlier sessions (past decisions, notes, history) "
    "that is not in the current message",
    YES_NO_LABELS,
    0.0,
    kind=AxisKind.YES_NO,
)
NEEDS_STRUCTURE = ClassifierAxis(
    "needs_structure",
    "Answering needs knowledge of source code or database structure "
    "(files, symbols, migrations, lineage)",
    YES_NO_LABELS,
    0.0,
    kind=AxisKind.YES_NO,
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

FRONT_DOOR_AXES = (NEEDS_LONG_TERM, NEEDS_STRUCTURE, COMPLEXITY, TOOL_NEED)
TIER_AXES = (COMPLEXITY, TOOL_NEED, RISK_AXIS)


class NeedAnswer(StrEnum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


def tier_committee() -> CascadeCommittee:
    return CascadeCommittee(TIER_AXES, escalation_threshold=TIER_ESCALATION_THRESHOLD)


def need_from_result(result: ClassifierResult | None) -> NeedAnswer:
    """Map p(yes) to yes/no/unknown; a missing answer is unknown, which means lazy pull."""

    if result is None:
        return NeedAnswer.UNKNOWN
    if result.escalation_score >= NEED_YES_AT:
        return NeedAnswer.YES
    if result.escalation_score <= NEED_NO_AT:
        return NeedAnswer.NO
    return NeedAnswer.UNKNOWN


def relevance_axis(index: int, snippet: str) -> ClassifierAxis:
    """Build one per-candidate yes/no question carrying a bounded snippet."""

    if isinstance(index, bool) or not 0 <= index < _MAXIMUM_RELEVANCE_AXES:
        raise ValueError("relevance axis index is out of range")
    text = " ".join(snippet.split())[:RELEVANCE_SNIPPET_CHARACTERS]
    if not text:
        raise ValueError("relevance snippet is empty")
    return ClassifierAxis(
        f"helps_{index}", _RELEVANCE_PREFIX + text, YES_NO_LABELS, 0.0, kind=AxisKind.YES_NO
    )


def should_drop_candidate(result: ClassifierResult | None) -> bool:
    """Drop only on a confident no; keep is the safe side."""

    return result is not None and result.escalation_score <= RELEVANCE_DROP_AT


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
