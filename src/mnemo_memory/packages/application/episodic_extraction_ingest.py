"""Convert validated episodic-extraction proposals into approved episodic events."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from mnemo_memory.packages.domain.approved_episodic_events import ApprovedEventKind
from mnemo_memory.packages.domain.episodic_candidates import (
    EpisodicExtractionProposal,
    EpisodicMemoryKind,
)
from mnemo_memory.packages.domain.models import EvidenceReference, MemoryScope

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
    """Persist mapped proposals as approved episodic events; drop unmapped or rejected ones.

    Each proposal is persisted independently. An unmapped kind or a per-item persistence
    failure (for example the deterministic secret policy rejecting one candidate) drops only
    that proposal; it never aborts the batch, so earlier successful writes are never silently
    hidden behind one later failure.
    """
    persisted = 0
    dropped = 0
    for index, proposal in enumerate(proposals):
        mapped = approved_kind_for_episodic(proposal.kind)
        if mapped is None:
            dropped += 1
            continue
        try:
            service.record_approved_event(  # type: ignore[attr-defined]
                RecordApprovedEpisodicEvent(
                    scope,
                    mapped,
                    proposal.claim,
                    f"{source_event_key}:{index}",
                    evidence_references,
                )
            )
        except Exception:
            # Intentional fail-open: this also swallows genuine storage outages as "dropped",
            # not just policy rejections, so one bad candidate never aborts the batch.
            dropped += 1
            continue
        persisted += 1
    return IngestResult(persisted, dropped)
