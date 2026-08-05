"""Conservative deterministic final selection for an authorized context packet."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import replace

from mnemo_memory.packages.domain import (
    ConflictNotice,
    ConflictState,
    ContextItem,
    ContextItemType,
    ContextPacket,
    EvidenceReference,
    OmissionNotice,
    OmissionReason,
    ProvenanceNotice,
    SourceTrustClass,
    ValidityState,
)

_TRUST_PRIORITY = {
    SourceTrustClass.CURRENT_STRUCTURAL: 0,
    SourceTrustClass.VERIFIED_TOOL_RESULT: 1,
    SourceTrustClass.USER_CORRECTION: 2,
    SourceTrustClass.USER_AUTHORED: 3,
    SourceTrustClass.APPROVED_CHECKPOINT: 4,
    SourceTrustClass.EXTERNAL: 5,
    SourceTrustClass.ASSISTANT_INFERENCE: 6,
}
_VALIDITY_PRIORITY = {
    ValidityState.CURRENT: 0,
    ValidityState.UNKNOWN: 1,
    ValidityState.STALE: 2,
    ValidityState.SUPERSEDED: 3,
    ValidityState.EXPIRED: 4,
}
_DIVERSITY_TYPES = {ContextItemType.EPISODIC_MEMORY, ContextItemType.KNOWLEDGE}


def finalize_context_packet(packet: ContextPacket) -> ContextPacket:
    """Apply exact deduplication, source diversity, and declared conflict state.

    The input packet is already authorized. This function performs no retrieval and only removes
    items; checkpoints, mandatory procedures, and every declared conflict participant are retained.
    """

    provenance_by_item = {notice.item_id: notice for notice in packet.provenance}
    source_conflicts = _source_integrity_conflicts(packet.provenance, packet.conflicts)
    conflicts = (*packet.conflicts, *source_conflicts)
    protected_ids = {item_id for conflict in conflicts for item_id in conflict.item_ids} | {
        item.item_id
        for item in packet.items
        if item.item_type
        in {ContextItemType.ACTIVE_TASK_CHECKPOINT, ContextItemType.MANDATORY_PROCEDURE}
    }

    conflict_states = _conflict_states(conflicts)
    items_by_id = {
        item.item_id: replace(
            item, conflict_state=conflict_states.get(item.item_id, item.conflict_state)
        )
        for item in packet.items
    }
    removed_ids: set[str] = set()
    omissions = list(packet.omissions)

    duplicate_groups: dict[tuple[str, ...], list[ContextItem]] = defaultdict(list)
    for item in items_by_id.values():
        notice = provenance_by_item[item.item_id]
        duplicate_groups[_duplicate_key(item, notice)].append(item)
    for group in duplicate_groups.values():
        if len(group) < 2 or any(item.item_id in protected_ids for item in group):
            continue
        survivor = min(group, key=_authority_rank_key)
        merged_evidence = _unique_evidence(
            evidence
            for item in group
            for evidence in (
                *item.evidence_references,
                *provenance_by_item[item.item_id].evidence_references,
            )
        )
        items_by_id[survivor.item_id] = replace(survivor, evidence_references=merged_evidence)
        provenance_by_item[survivor.item_id] = replace(
            provenance_by_item[survivor.item_id], evidence_references=merged_evidence
        )
        for duplicate in sorted(group, key=lambda item: item.item_id):
            if duplicate.item_id == survivor.item_id:
                continue
            removed_ids.add(duplicate.item_id)
            omissions.append(
                OmissionNotice(
                    duplicate.item_id,
                    OmissionReason.DUPLICATE,
                    "exact same-source duplicate retained under one higher-authority identity",
                )
            )

    diversity_groups: dict[tuple[ContextItemType, tuple[str, ...]], list[ContextItem]] = (
        defaultdict(list)
    )
    for item in items_by_id.values():
        if (
            item.item_id in removed_ids
            or item.item_id in protected_ids
            or item.item_type not in _DIVERSITY_TYPES
        ):
            continue
        source_ids = tuple(
            sorted({str(evidence.source_id) for evidence in item.evidence_references})
        )
        diversity_groups[(item.item_type, source_ids)].append(item)
    for group in diversity_groups.values():
        for lower_ranked in sorted(group, key=_rank_key)[2:]:
            removed_ids.add(lower_ranked.item_id)
            omissions.append(
                OmissionNotice(
                    lower_ranked.item_id,
                    OmissionReason.LOWER_RANK,
                    "source diversity limit retained two higher-ranked items",
                )
            )

    def selected(items: tuple[ContextItem, ...]) -> tuple[ContextItem, ...]:
        return tuple(items_by_id[item.item_id] for item in items if item.item_id not in removed_ids)

    checkpoint = packet.active_task_checkpoint
    if checkpoint is not None:
        checkpoint = items_by_id[checkpoint.item_id]
    provenance = tuple(
        provenance_by_item[notice.item_id]
        for notice in packet.provenance
        if notice.item_id not in removed_ids
    )
    episodic_memories = selected(packet.episodic_memories)
    knowledge_items = selected(packet.knowledge_items)
    structural_items = selected(packet.structural_items)
    skills_and_procedures = selected(packet.skills_and_procedures)
    declared_total_tokens = (
        sum(
            item.token_estimate
            for item in (
                *((checkpoint,) if checkpoint is not None else ()),
                *episodic_memories,
                *knowledge_items,
                *structural_items,
                *skills_and_procedures,
            )
        )
        + sum(item.token_estimate for item in provenance)
        + sum(item.token_estimate for item in conflicts)
    )
    return replace(
        packet,
        declared_total_tokens=declared_total_tokens,
        active_task_checkpoint=checkpoint,
        episodic_memories=episodic_memories,
        knowledge_items=knowledge_items,
        structural_items=structural_items,
        skills_and_procedures=skills_and_procedures,
        provenance=provenance,
        conflicts=conflicts,
        omissions=tuple(omissions),
    )


def _source_integrity_conflicts(
    provenance: tuple[ProvenanceNotice, ...], existing: tuple[ConflictNotice, ...]
) -> tuple[ConflictNotice, ...]:
    by_reference: dict[str, list[ProvenanceNotice]] = defaultdict(list)
    for notice in provenance:
        by_reference[notice.source_reference].append(notice)
    existing_keys = {
        tuple(sorted(conflict.item_ids))
        for conflict in existing
        if conflict.state is ConflictState.UNRESOLVED
    }
    conflicts: list[ConflictNotice] = []
    for source_reference, notices in sorted(by_reference.items()):
        if len({notice.source_digest for notice in notices}) < 2:
            continue
        item_ids = tuple(sorted(notice.item_id for notice in notices))
        if item_ids in existing_keys:
            continue
        digest = hashlib.sha256(
            json.dumps(
                [source_reference, *item_ids], ensure_ascii=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        conflicts.append(
            ConflictNotice(
                f"conflict:source-integrity:{digest}",
                item_ids,
                _unique_evidence(
                    evidence for notice in notices for evidence in notice.evidence_references
                ),
                ConflictState.UNRESOLVED,
            )
        )
    return tuple(conflicts)


def _conflict_states(conflicts: tuple[ConflictNotice, ...]) -> dict[str, ConflictState]:
    states: dict[str, ConflictState] = {}
    for conflict in conflicts:
        for item_id in conflict.item_ids:
            if conflict.state is ConflictState.UNRESOLVED or item_id not in states:
                states[item_id] = conflict.state
    return states


def _duplicate_key(item: ContextItem, provenance: ProvenanceNotice) -> tuple[str, ...]:
    return (
        json.dumps(item.source_scope.to_dict(), sort_keys=True, separators=(",", ":")),
        item.sensitivity.value,
        item.content,
        provenance.source_reference,
        provenance.source_digest,
    )


def _authority_rank_key(item: ContextItem) -> tuple[object, ...]:
    return (
        _TRUST_PRIORITY[item.source_trust],
        _VALIDITY_PRIORITY[item.validity],
        *_rank_key(item),
    )


def _rank_key(item: ContextItem) -> tuple[object, ...]:
    rank = item.ranking.rank if item.ranking is not None else None
    score = item.ranking.score if item.ranking is not None else None
    return (
        rank is None,
        rank if rank is not None else 0,
        -(score if score is not None else 0.0),
        item.item_id,
    )


def _unique_evidence(evidence: Iterable[EvidenceReference]) -> tuple[EvidenceReference, ...]:
    unique: dict[str, EvidenceReference] = {}
    for reference in evidence:
        existing = unique.get(str(reference.evidence_id))
        if existing is not None and existing != reference:
            raise ValueError("one evidence identity cannot describe multiple sources")
        unique[str(reference.evidence_id)] = reference
    return tuple(unique[key] for key in sorted(unique))
