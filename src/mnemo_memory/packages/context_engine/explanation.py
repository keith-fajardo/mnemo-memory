"""Content-free explanation of one already validated canonical context packet."""

from __future__ import annotations

from dataclasses import dataclass

from mnemo_memory.packages.domain import ContextPacket, ValidityState


@dataclass(frozen=True, slots=True)
class ContextExplanation:
    """Inspectable selection metadata that deliberately excludes retrieved content."""

    value: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return self.value


def explain_context_packet(packet: ContextPacket) -> ContextExplanation:
    """Explain packet metadata without retrieving data or reproducing content-bearing fields."""
    provenance = {notice.item_id: notice for notice in packet.provenance}
    included: list[dict[str, object]] = []
    non_current: list[dict[str, str]] = []
    for item in packet.items:
        notice = provenance[item.item_id]
        ranking = item.ranking
        included.append(
            {
                "item_id": item.item_id,
                "item_type": item.item_type.value,
                "source_scope": item.source_scope.to_dict(),
                "source_trust": item.source_trust.value,
                "sensitivity": item.sensitivity.value,
                "validity": item.validity.value,
                "observed_at": item.observed_at.isoformat(),
                "token_estimate": item.token_estimate,
                "rank": None if ranking is None else ranking.rank,
                "score": None if ranking is None else ranking.score,
                "retrieval_method": None if ranking is None else ranking.retrieval_method,
                "conflict_state": item.conflict_state.value,
                "source_reference": notice.source_reference,
                "source_digest": notice.source_digest,
                "evidence": [
                    {
                        "evidence_id": str(evidence.evidence_id),
                        "source_id": str(evidence.source_id),
                        "source_type": evidence.source_type.value,
                        "trust_class": evidence.trust_class.value,
                        "content_hash": evidence.content_hash,
                        "observed_at": evidence.observed_at.isoformat(),
                        "verification_status": evidence.verification_status.value,
                    }
                    for evidence in notice.evidence_references
                ],
            }
        )
        if item.validity is not ValidityState.CURRENT:
            non_current.append(
                {
                    "item_id": item.item_id,
                    "validity": item.validity.value,
                    "observed_at": item.observed_at.isoformat(),
                }
            )

    return ContextExplanation(
        {
            "schema_version": "1.0",
            "basis": "caller_supplied_canonical_packet",
            "request_id": str(packet.request_id),
            "owner_scope": packet.owner_scope.to_dict(),
            "included": included,
            "exclusions": [item.to_dict() for item in packet.omissions],
            "conflicts": [
                {
                    "conflict_id": item.conflict_id,
                    "item_ids": list(item.item_ids),
                    "state": item.state.value,
                    "evidence_ids": [
                        str(evidence.evidence_id) for evidence in item.evidence_references
                    ],
                    "token_estimate": item.token_estimate,
                }
                for item in packet.conflicts
            ],
            "staleness": {
                "non_current_count": len(non_current),
                "non_current_items": non_current,
            },
            "token_accounting": {
                "declared_total": packet.declared_total_tokens,
                "computed_total": packet.computed_total_tokens,
                "remaining": packet.remaining_budget,
                "sections": packet.section_tokens,
                "budget": packet.budget.to_dict(),
            },
        }
    )
