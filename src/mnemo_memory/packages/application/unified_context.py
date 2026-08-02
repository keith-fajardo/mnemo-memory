"""Application-level assembly of durable checkpoint and dbt structural context."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from mnemo_memory.packages.application.checkpoints import (
    CheckpointApplicationService,
    GetCheckpointContext,
)
from mnemo_memory.packages.application.dbt import (
    DbtManifestApplicationService,
    LineageDirection,
    QueryLineage,
)
from mnemo_memory.packages.domain import (
    ConflictState,
    ContentRepresentation,
    ContextBudget,
    ContextItem,
    ContextItemType,
    ContextPacket,
    DbtNodeId,
    DbtSnapshotId,
    MemoryScope,
    OmissionNotice,
    OmissionReason,
    ProvenanceNotice,
    Sensitivity,
    SourceTrustClass,
    ValidityState,
)
from mnemo_memory.packages.domain.dbt_manifest import ArtifactCurrentness, SourceStateFingerprint


@dataclass(frozen=True, slots=True)
class ContextLineageQuery:
    unique_id: DbtNodeId
    direction: LineageDirection
    transitive: bool = True
    maximum_depth: int | None = None
    maximum_nodes: int = 500
    maximum_edges: int = 1_000
    snapshot_id: DbtSnapshotId | None = None
    current_content_digest: str | None = None
    current_source_state: SourceStateFingerprint | None = None
    require_current: bool = False


@dataclass(frozen=True, slots=True)
class GetUnifiedContext:
    scope: MemoryScope
    checkpoint_id: object | None = None
    lineage: ContextLineageQuery | None = None
    budget: ContextBudget = field(default_factory=ContextBudget)


class UnifiedContextService:
    """Combines separately-authoritative checkpoint and structural evidence deterministically."""

    def __init__(
        self, checkpoints: CheckpointApplicationService, dbt: DbtManifestApplicationService
    ) -> None:
        self._checkpoints = checkpoints
        self._dbt = dbt

    def get_context(self, request: GetUnifiedContext) -> ContextPacket:
        packet = self._checkpoints.get_context(
            GetCheckpointContext(request.scope, request.checkpoint_id, request.budget)  # type: ignore[arg-type]
        )
        if request.lineage is None:
            return packet
        query = request.lineage
        result = self._dbt.query(
            QueryLineage(
                request.scope,
                query.unique_id,
                query.direction,
                query.transitive,
                query.maximum_depth,
                query.maximum_nodes,
                query.maximum_edges,
                query.snapshot_id,
                True,
                query.current_content_digest,
                query.current_source_state,
            )
        )
        if query.require_current and result.currentness is not ArtifactCurrentness.CURRENT:
            return _with_omission(
                packet, "dbt-lineage", OmissionReason.STALE, "structural facts are not current"
            )
        facts: list[ContextItem] = []
        notices: list[ProvenanceNotice] = list(packet.provenance)
        remaining = min(
            request.budget.structural, request.budget.total_limit - packet.declared_total_tokens
        )
        for item in result.nodes:
            content = json.dumps(
                {
                    "snapshot_id": str(result.snapshot.snapshot_id),
                    "start_node": str(result.start_node.unique_id),
                    "node_unique_id": str(item.node.unique_id),
                    "resource_type": item.node.raw_resource_type,
                    "direction": result.direction.value,
                    "depth": item.depth,
                    "currentness": result.currentness.value,
                    "relative_file": item.node.original_file_path,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            tokens = (len(content) + 3) // 4
            if tokens > remaining:
                packet = _with_omission(
                    packet,
                    f"dbt:{item.node.unique_id}",
                    OmissionReason.TOKEN_BUDGET,
                    "structural facts exceed context budget",
                )
                break
            context_item = ContextItem(
                item_id=f"dbt:{result.snapshot.snapshot_id}:{item.node.unique_id}",
                item_type=ContextItemType.STRUCTURAL_FACT,
                source_scope=request.scope,
                content=content,
                content_representation=ContentRepresentation.UNTRUSTED_EVIDENCE,
                token_estimate=tokens,
                evidence_references=(item.node.evidence,),
                source_trust=SourceTrustClass.APPROVED_CHECKPOINT,
                sensitivity=Sensitivity.NORMAL,
                validity=ValidityState.CURRENT
                if result.currentness is ArtifactCurrentness.CURRENT
                else ValidityState.STALE,
                ranking=None,
                conflict_state=ConflictState.NONE,
                observed_at=result.snapshot.metadata.ingested_at,
            )
            facts.append(context_item)
            remaining -= tokens
            notices.append(
                ProvenanceNotice(
                    f"provenance:{context_item.item_id}",
                    context_item.item_id,
                    f"mnemo:dbt/snapshot/{result.snapshot.snapshot_id}/node/{item.node.unique_id}",
                    hashlib.sha256(content.encode()).hexdigest(),
                    (item.node.evidence,),
                )
            )
        omissions = packet.omissions
        if result.truncated:
            omissions += (
                OmissionNotice(
                    "dbt-lineage",
                    OmissionReason.LOWER_RANK,
                    result.truncation_reason or "lineage traversal truncated",
                ),
            )
        return ContextPacket(
            packet.schema_version,
            packet.request_id,
            packet.owner_scope,
            packet.query_id,
            packet.task_id,
            packet.created_at,
            packet.expires_at,
            packet.declared_total_tokens + sum(x.token_estimate for x in facts),
            packet.budget,
            packet.producer_version,
            active_task_checkpoint=packet.active_task_checkpoint,
            structural_items=tuple(facts),
            provenance=tuple(notices),
            omissions=omissions,
            conflicts=packet.conflicts,
        )


def _with_omission(
    packet: ContextPacket, item_id: str, reason: OmissionReason, detail: str
) -> ContextPacket:
    return ContextPacket(
        packet.schema_version,
        packet.request_id,
        packet.owner_scope,
        packet.query_id,
        packet.task_id,
        packet.created_at,
        packet.expires_at,
        packet.declared_total_tokens,
        packet.budget,
        packet.producer_version,
        active_task_checkpoint=packet.active_task_checkpoint,
        provenance=packet.provenance,
        omissions=(*packet.omissions, OmissionNotice(item_id, reason, detail)),
        conflicts=packet.conflicts,
    )
