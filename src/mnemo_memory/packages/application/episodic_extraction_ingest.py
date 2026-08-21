"""Convert validated episodic-extraction proposals into approved episodic events."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..domain.approved_episodic_events import ApprovedEventKind
from ..domain.episodic_candidates import EpisodicExtractionProposal, EpisodicMemoryKind
from ..domain.models import EvidenceReference, MemoryScope
from .checkpoints import RecordApprovedEpisodicEvent

_APPROVED_KIND: dict[EpisodicMemoryKind, ApprovedEventKind] = {
    EpisodicMemoryKind.DECISION: ApprovedEventKind.DECISION,
    EpisodicMemoryKind.FAILURE: ApprovedEventKind.FAILURE,
    EpisodicMemoryKind.OUTCOME: ApprovedEventKind.TOOL_OUTCOME,
}


def approved_kind_for_episodic(kind: EpisodicMemoryKind) -> ApprovedEventKind | None:
    """Map an episodic kind to an approved-event kind, or None if it has no equivalent."""
    return _APPROVED_KIND.get(kind)


@dataclass(frozen=True, slots=True)
class IngestResult:
    persisted: int
    dropped: int


def ingest_episodic_proposals(
    *,
    service: object,
    scope: MemoryScope,
    source_event_key: str,
    evidence_references: tuple[EvidenceReference, ...],
    proposals: Sequence[EpisodicExtractionProposal],
) -> IngestResult:
    """Persist mapped proposals as approved episodic events; drop unmapped kinds."""
    persisted = 0
    dropped = 0
    for index, proposal in enumerate(proposals):
        mapped = approved_kind_for_episodic(proposal.kind)
        if mapped is None:
            dropped += 1
            continue
        service.record_approved_event(  # type: ignore[attr-defined]
            RecordApprovedEpisodicEvent(
                scope,
                mapped,
                proposal.claim,
                f"{source_event_key}:{index}",
                evidence_references,
            )
        )
        persisted += 1
    return IngestResult(persisted, dropped)
