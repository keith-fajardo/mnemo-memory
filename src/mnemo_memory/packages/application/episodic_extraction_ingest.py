"""Convert validated episodic-extraction proposals into approved episodic events."""

from __future__ import annotations

from ..domain.approved_episodic_events import ApprovedEventKind
from ..domain.episodic_candidates import EpisodicMemoryKind

_APPROVED_KIND: dict[EpisodicMemoryKind, ApprovedEventKind] = {
    EpisodicMemoryKind.DECISION: ApprovedEventKind.DECISION,
    EpisodicMemoryKind.FAILURE: ApprovedEventKind.FAILURE,
    EpisodicMemoryKind.OUTCOME: ApprovedEventKind.TOOL_OUTCOME,
}


def approved_kind_for_episodic(kind: EpisodicMemoryKind) -> ApprovedEventKind | None:
    """Map an episodic kind to an approved-event kind, or None if it has no equivalent."""
    return _APPROVED_KIND.get(kind)
