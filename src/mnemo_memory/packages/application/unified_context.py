"""Application-level assembly of durable checkpoint and dbt structural context."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import cast
from uuid import UUID, uuid5

from mnemo_memory.packages.application.checkpoints import (
    CheckpointApplicationService,
    GetCheckpointContext,
)
from mnemo_memory.packages.application.dbt import (
    DbtManifestApplicationService,
    LineageDirection,
    QueryLineage,
    ResolveManifestFile,
)
from mnemo_memory.packages.domain import (
    CheckpointId,
    CheckpointRevisionId,
    CodeEdge,
    CodeEdgeKind,
    CodeFile,
    CodeSnapshot,
    CodeSnapshotId,
    CodeSymbol,
    CodeSymbolId,
    ConflictState,
    ContentRepresentation,
    ContextBudget,
    ContextItem,
    ContextItemType,
    ContextPacket,
    DbtNodeId,
    DbtSnapshotId,
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    MemoryScope,
    OmissionNotice,
    OmissionReason,
    ProvenanceNotice,
    ScopeLevel,
    Sensitivity,
    SourceId,
    SourceTrustClass,
    ValidityState,
    VerificationStatus,
)
from mnemo_memory.packages.domain.dbt_manifest import ArtifactCurrentness, SourceStateFingerprint
from mnemo_memory.packages.storage.contracts import (
    CheckpointSourceObservationNotFound,
    CheckpointSourceObservationRepository,
    SourceStructureRepository,
)


@dataclass(frozen=True, slots=True)
class ContextLineageQuery:
    unique_id: DbtNodeId | None
    direction: LineageDirection
    transitive: bool = True
    maximum_depth: int | None = None
    maximum_nodes: int = 500
    maximum_edges: int = 1_000
    snapshot_id: DbtSnapshotId | None = None
    current_content_digest: str | None = None
    current_source_state: SourceStateFingerprint | None = None
    require_current: bool = False
    relative_path: str | None = None

    def __post_init__(self) -> None:
        if (self.unique_id is None) == (self.relative_path is None):
            raise ValueError("dbt lineage requires exactly one unique_id or relative_path")
        if self.relative_path is not None:
            _validate_source_relative_path(self.relative_path)


@dataclass(frozen=True, slots=True)
class ContextSourceImpactQuery:
    """A bounded static impact request; dynamic behavior is intentionally excluded."""

    symbol: str
    direction: str = "dependents"
    transitive: bool = True
    maximum_depth: int | None = None
    maximum_symbols: int = 100
    maximum_edges: int = 200
    snapshot_id: CodeSnapshotId | None = None
    current_source_digest: str | None = None
    require_current: bool = False

    def __post_init__(self) -> None:
        if not self.symbol.strip() or len(self.symbol) > 512:
            raise ValueError("source impact requires a bounded symbol or relative path")
        if self.direction not in {"dependents", "dependencies"}:
            raise ValueError("source impact direction must be dependents or dependencies")
        if self.maximum_depth is not None and self.maximum_depth < 0:
            raise ValueError("source impact depth must be non-negative")
        if self.maximum_symbols < 1 or self.maximum_edges < 1:
            raise ValueError("source impact limits must be positive")
        if self.current_source_digest is not None and (
            not self.current_source_digest.startswith("sha256:")
            or len(self.current_source_digest) != 71
        ):
            raise ValueError("source impact digest must be a sha256 digest")


@dataclass(frozen=True, slots=True)
class ContextSourceChangeQuery:
    """Request bounded explicitly recorded source-snapshot transitions.

    This is intentionally a transition summary rather than a source-file replay:
    only bounded relative file paths plus declaration and relationship identities are rendered.
    """

    maximum_declarations: int = 24
    maximum_relationships: int = 24
    maximum_files: int = 24
    maximum_transitions: int = 1
    relative_path: str | None = None
    current_source_digest: str | None = None
    require_current: bool = False
    before_snapshot_id: CodeSnapshotId | None = None
    after_snapshot_id: CodeSnapshotId | None = None

    def __post_init__(self) -> None:
        if (
            self.maximum_declarations < 1
            or self.maximum_relationships < 1
            or self.maximum_files < 1
        ):
            raise ValueError("source change limits must be positive")
        if (
            self.maximum_declarations > 100
            or self.maximum_relationships > 100
            or self.maximum_files > 100
        ):
            raise ValueError("source change limits must not exceed 100")
        if self.maximum_transitions < 1 or self.maximum_transitions > 16:
            raise ValueError("source change transition limit must be between 1 and 16")
        if self.relative_path is not None:
            _validate_source_relative_path(self.relative_path)
        if self.current_source_digest is not None and (
            not self.current_source_digest.startswith("sha256:")
            or len(self.current_source_digest) != 71
        ):
            raise ValueError("source change digest must be a sha256 digest")
        if (self.before_snapshot_id is None) != (self.after_snapshot_id is None):
            raise ValueError("source changes require both historical snapshot IDs")
        if (
            self.before_snapshot_id is not None
            and self.before_snapshot_id == self.after_snapshot_id
        ):
            raise ValueError("source changes require distinct historical snapshot IDs")
        if self.before_snapshot_id is not None and self.maximum_transitions != 1:
            raise ValueError("an explicit source transition cannot request history")


@dataclass(frozen=True, slots=True)
class GetUnifiedContext:
    scope: MemoryScope
    checkpoint_id: object | None = None
    lineage: ContextLineageQuery | None = None
    source_query: str | None = None
    budget: ContextBudget = field(default_factory=ContextBudget)
    source_impact: ContextSourceImpactQuery | None = None
    source_changes: ContextSourceChangeQuery | None = None
    include_lifecycle_events: bool = False
    include_approved_events: bool = False


class UnifiedContextService:
    """Combines separately-authoritative checkpoint and structural evidence deterministically."""

    def __init__(
        self,
        checkpoints: CheckpointApplicationService,
        dbt: DbtManifestApplicationService | None,
        source: SourceStructureRepository | None = None,
        checkpoint_source_observations: CheckpointSourceObservationRepository | None = None,
    ) -> None:
        self._checkpoints = checkpoints
        self._dbt = dbt
        self._source = source
        self._checkpoint_source_observations = checkpoint_source_observations

    def get_context(self, request: GetUnifiedContext) -> ContextPacket:
        packet = self._checkpoints.get_context(
            GetCheckpointContext(
                request.scope,
                cast(CheckpointId | None, request.checkpoint_id),
                request.budget,
                request.include_lifecycle_events,
                8,
                request.include_approved_events,
            )
        )
        packet = self._with_checkpoint_source_observation(packet, request)
        if (
            request.lineage is None
            and request.source_query is None
            and request.source_impact is None
            and request.source_changes is None
        ):
            return packet
        if request.lineage is None:
            return self._with_requested_source_facts(packet, request)
        return self._with_dbt_lineage(packet, request)

    def _with_dbt_lineage(self, packet: ContextPacket, request: GetUnifiedContext) -> ContextPacket:
        """Attach requested dbt lineage, then any requested source facts."""
        query = request.lineage
        assert query is not None
        if self._dbt is None:
            result_packet = _with_omission(
                packet, "dbt-lineage", OmissionReason.LOWER_RANK, "dbt index is unavailable"
            )
            return self._with_requested_source_facts(result_packet, request)
        unique_id = query.unique_id
        if unique_id is None:
            assert query.relative_path is not None
            unique_id = self._dbt.resolve_file(
                ResolveManifestFile(
                    _project_scope(request.scope), query.relative_path, query.snapshot_id
                )
            ).node.unique_id
        result = self._dbt.query(
            QueryLineage(
                _project_scope(request.scope),
                unique_id,
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
            result_packet = _with_omission(
                packet, "dbt-lineage", OmissionReason.STALE, "structural facts are not current"
            )
            return self._with_requested_source_facts(result_packet, request)
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
        result_packet = ContextPacket(
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
        return self._with_requested_source_facts(result_packet, request)

    def _with_checkpoint_source_observation(
        self, packet: ContextPacket, request: GetUnifiedContext
    ) -> ContextPacket:
        """Attach an exact co-observed source snapshot without claiming it explains a change."""
        if (
            self._source is None
            or self._checkpoint_source_observations is None
            or packet.active_task_checkpoint is None
        ):
            return packet
        checkpoint_id, revision_id = _checkpoint_reference_from_context_item(
            packet.active_task_checkpoint.item_id
        )
        if checkpoint_id is None or revision_id is None:
            return packet
        try:
            observation = self._checkpoint_source_observations.get_checkpoint_source_observation(
                request.scope, checkpoint_id, revision_id
            )
            snapshot = self._source.get_snapshot(
                _project_scope(request.scope), observation.source_snapshot_id
            )
        except CheckpointSourceObservationNotFound:
            return packet
        except Exception:
            return _with_omission(
                packet,
                "checkpoint-source-observation",
                OmissionReason.LOWER_RANK,
                "checkpoint source observation is unavailable",
            )
        content = json.dumps(
            {
                "checkpoint_revision_id": str(observation.revision_id),
                "observation": "source_snapshot_observed_after_checkpoint_revision_persisted",
                "observed_at": observation.observed_at.isoformat(),
                "source_digest": snapshot.source_digest,
                "source_snapshot_id": str(snapshot.snapshot_id),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        tokens = (len(content) + 3) // 4
        remaining = min(
            packet.budget.structural,
            packet.budget.total_limit - packet.declared_total_tokens,
        ) - sum(item.token_estimate for item in packet.structural_items)
        if tokens > remaining:
            return _with_omission(
                packet,
                "checkpoint-source-observation",
                OmissionReason.TOKEN_BUDGET,
                "checkpoint source observation exceeds remaining structural budget",
            )
        evidence = (
            *packet.active_task_checkpoint.evidence_references,
            _source_snapshot_evidence(packet, snapshot),
        )
        item = ContextItem(
            f"source-observation:{observation.revision_id}:{snapshot.snapshot_id}",
            ContextItemType.STRUCTURAL_FACT,
            request.scope,
            content,
            ContentRepresentation.UNTRUSTED_EVIDENCE,
            tokens,
            evidence,
            SourceTrustClass.CURRENT_STRUCTURAL,
            Sensitivity.NORMAL,
            ValidityState.UNKNOWN,
            None,
            ConflictState.NONE,
            observation.observed_at,
        )
        notice = ProvenanceNotice(
            f"provenance:{item.item_id}",
            item.item_id,
            f"mnemo:checkpoint-source-observation/{observation.revision_id}/{snapshot.snapshot_id}",
            hashlib.sha256(content.encode()).hexdigest(),
            evidence,
        )
        return ContextPacket(
            packet.schema_version,
            packet.request_id,
            packet.owner_scope,
            packet.query_id,
            packet.task_id,
            packet.created_at,
            packet.expires_at,
            packet.declared_total_tokens + tokens,
            packet.budget,
            packet.producer_version,
            active_task_checkpoint=packet.active_task_checkpoint,
            episodic_memories=packet.episodic_memories,
            knowledge_items=packet.knowledge_items,
            structural_items=(*packet.structural_items, item),
            skills_and_procedures=packet.skills_and_procedures,
            provenance=(*packet.provenance, notice),
            omissions=packet.omissions,
            conflicts=packet.conflicts,
        )

    def _with_requested_source_facts(
        self, packet: ContextPacket, request: GetUnifiedContext
    ) -> ContextPacket:
        if request.source_impact is not None or request.source_query:
            packet = self._with_source_facts(packet, request)
        if request.source_changes is not None:
            packet = self._with_recent_source_changes(packet, request, request.source_changes)
        return packet

    def _with_source_facts(
        self, packet: ContextPacket, request: GetUnifiedContext
    ) -> ContextPacket:
        if request.source_impact is not None:
            return self._with_source_impact_facts(packet, request, request.source_impact)
        if self._source is None or not request.source_query or not request.source_query.strip():
            return _with_omission(
                packet, "source-structure", OmissionReason.LOWER_RANK, "no source query"
            )
        project_scope = _project_scope(request.scope)
        snapshot = self._source.get_active_snapshot(project_scope)
        if snapshot is None:
            return _with_omission(
                packet, "source-structure", OmissionReason.LOWER_RANK, "no source snapshot"
            )
        query = request.source_query.casefold()
        symbols = self._source.find_symbols(project_scope, snapshot.snapshot_id, query, limit=256)
        modules = self._source.module_symbols_for_paths(
            project_scope,
            snapshot.snapshot_id,
            tuple(symbol.relative_path for symbol in symbols),
        )
        module_symbols = {item.relative_path: item for item in modules}
        source_ids = tuple(
            dict.fromkeys(
                [*(item.symbol_id for item in modules), *(item.symbol_id for item in symbols)]
            )
        )
        edges = tuple(
            edge
            for edge in self._source.edges_from_symbols(
                project_scope, snapshot.snapshot_id, source_ids
            )
            if edge.kind in {CodeEdgeKind.IMPORTS, CodeEdgeKind.CALLS}
        )
        resolved_symbols = self._source.symbols_by_ids(
            project_scope,
            snapshot.snapshot_id,
            tuple(edge.target_symbol_id for edge in edges if edge.target_symbol_id is not None),
        )
        selected_symbols = tuple(
            {symbol.symbol_id: symbol for symbol in (*symbols, *resolved_symbols)}.values()
        )
        return _append_source_items(
            packet, request.scope, snapshot, selected_symbols, edges, module_symbols
        )

    def _with_source_impact_facts(
        self,
        packet: ContextPacket,
        request: GetUnifiedContext,
        query: ContextSourceImpactQuery,
    ) -> ContextPacket:
        if self._source is None:
            return _with_omission(
                packet, "source-impact", OmissionReason.LOWER_RANK, "no source snapshot"
            )
        scope = _project_scope(request.scope)
        snapshot = (
            self._source.get_snapshot(scope, query.snapshot_id)
            if query.snapshot_id is not None
            else self._source.get_active_snapshot(scope)
        )
        if snapshot is None:
            return _with_omission(
                packet, "source-impact", OmissionReason.LOWER_RANK, "no source snapshot"
            )
        currentness = _source_currentness(snapshot, query.current_source_digest)
        if query.require_current and currentness is not ValidityState.CURRENT:
            return _with_omission(
                packet,
                "source-impact",
                OmissionReason.STALE,
                "source snapshot is not proven current",
            )
        candidates = self._source.find_symbols(scope, snapshot.snapshot_id, query.symbol, limit=64)
        starts = (
            tuple(
                item
                for item in candidates
                if item.qualified_name == query.symbol or item.relative_path == query.symbol
            )
            or candidates
        )
        if not starts:
            return _with_omission(
                packet, "source-impact", OmissionReason.LOWER_RANK, "source symbol was not found"
            )
        visited = {item.symbol_id for item in starts}
        selected = {item.symbol_id: item for item in starts}
        depths = {item.symbol_id: 0 for item in starts}
        frontier = tuple(visited)
        edges: list[CodeEdge] = []
        depth = 0
        truncated: str | None = None
        while frontier and (query.transitive or depth == 0):
            depth += 1
            if query.maximum_depth is not None and depth > query.maximum_depth:
                truncated = "maximum depth reached"
                break
            boundary = (
                self._source.edges_to_symbols(scope, snapshot.snapshot_id, frontier)
                if query.direction == "dependents"
                else tuple(
                    edge
                    for edge in self._source.edges_from_symbols(
                        scope, snapshot.snapshot_id, frontier
                    )
                    if edge.target_symbol_id is not None
                )
            )
            if len(edges) + len(boundary) > query.maximum_edges:
                truncated = "maximum edge count reached"
                break
            edges.extend(boundary)
            if query.direction == "dependents":
                next_ids = tuple(
                    sorted(
                        {
                            edge.source_symbol_id
                            for edge in boundary
                            if edge.source_symbol_id not in visited
                        },
                        key=str,
                    )
                )
            else:
                next_ids = tuple(
                    sorted(
                        {
                            edge.target_symbol_id
                            for edge in boundary
                            if edge.target_symbol_id is not None
                            and edge.target_symbol_id not in visited
                        },
                        key=str,
                    )
                )
            if len(selected) + len(next_ids) > query.maximum_symbols:
                truncated = "maximum symbol count reached"
                break
            resolved = self._source.symbols_by_ids(scope, snapshot.snapshot_id, next_ids)
            for item in resolved:
                visited.add(item.symbol_id)
                selected[item.symbol_id] = item
                depths[item.symbol_id] = depth
            frontier = tuple(item.symbol_id for item in resolved)
        ordered = tuple(
            sorted(
                selected.values(),
                key=lambda item: (depths[item.symbol_id], item.relative_path, item.qualified_name),
            )
        )
        result = _append_source_items(
            packet,
            request.scope,
            snapshot,
            ordered,
            tuple(dict.fromkeys(edges)),
            {},
            impact_direction=query.direction,
            impact_depths=depths,
            currentness=currentness,
        )
        return (
            result
            if truncated is None
            else _with_omission(result, "source-impact", OmissionReason.LOWER_RANK, truncated)
        )

    def _with_recent_source_changes(
        self,
        packet: ContextPacket,
        request: GetUnifiedContext,
        query: ContextSourceChangeQuery,
    ) -> ContextPacket:
        if self._source is None:
            return _with_omission(
                packet, "source-changes", OmissionReason.LOWER_RANK, "no source snapshot"
            )
        scope = _project_scope(request.scope)
        transitions: tuple[tuple[CodeSnapshot, CodeSnapshot], ...]
        if query.before_snapshot_id is not None and query.after_snapshot_id is not None:
            transitions = (
                (
                    self._source.get_snapshot(scope, query.before_snapshot_id),
                    self._source.get_snapshot(scope, query.after_snapshot_id),
                ),
            )
        else:
            history = self._source.list_activation_history(
                scope, limit=query.maximum_transitions + 1
            )
            if len(history) < 2:
                return _with_omission(
                    packet,
                    "source-changes",
                    OmissionReason.LOWER_RANK,
                    "no prior source transition",
                )
            transitions = tuple(
                (history[index + 1], history[index]) for index in range(len(history) - 1)
            )

        result = packet
        appended = 0
        bounded = False
        rejected_as_not_current = False
        for before, after in transitions:
            currentness = _source_currentness(after, query.current_source_digest)
            if query.require_current and currentness is not ValidityState.CURRENT:
                rejected_as_not_current = True
                continue
            result, included, transition_bounded = _append_source_change_transition(
                result,
                request.scope,
                self._source,
                scope,
                before,
                after,
                query,
                currentness,
            )
            appended += int(included)
            bounded = bounded or transition_bounded
        if appended == 0:
            if rejected_as_not_current:
                return _with_omission(
                    result,
                    "source-changes",
                    OmissionReason.STALE,
                    "source transition is not proven current",
                )
            if bounded:
                return _with_omission(
                    result,
                    "source-changes",
                    OmissionReason.TOKEN_BUDGET,
                    "source transition summary exceeds remaining structural budget",
                )
            detail = (
                "no recorded source changes for requested relative path"
                if query.relative_path is not None
                else "no source transition entries fit the requested bounds"
            )
            return _with_omission(result, "source-changes", OmissionReason.LOWER_RANK, detail)
        return (
            result
            if not bounded
            else _with_omission(
                result,
                "source-changes",
                OmissionReason.LOWER_RANK,
                "source transition entries were bounded",
            )
        )


def _append_source_change_transition(
    packet: ContextPacket,
    task_scope: MemoryScope,
    repository: SourceStructureRepository,
    project_scope: MemoryScope,
    before: CodeSnapshot,
    after: CodeSnapshot,
    query: ContextSourceChangeQuery,
    currentness: ValidityState,
) -> tuple[ContextPacket, bool, bool]:
    """Add one immutable transition summary, with optional safe file-path filtering."""
    (
        files_available,
        added_files,
        removed_files,
        modified_files,
        added_symbols,
        removed_symbols,
        added_edges,
        removed_edges,
    ) = _source_snapshot_difference(repository, project_scope, before, after)
    if query.relative_path is not None:
        path = query.relative_path
        before_paths = {
            item.symbol_id: item.relative_path
            for item in repository.iter_symbols(project_scope, before.snapshot_id)
        }
        after_paths = {
            item.symbol_id: item.relative_path
            for item in repository.iter_symbols(project_scope, after.snapshot_id)
        }
        added_files = tuple(item for item in added_files if item.relative_path == path)
        removed_files = tuple(item for item in removed_files if item.relative_path == path)
        modified_files = tuple(item for item in modified_files if item.relative_path == path)
        added_symbols = tuple(item for item in added_symbols if item.relative_path == path)
        removed_symbols = tuple(item for item in removed_symbols if item.relative_path == path)
        added_edges = tuple(
            item for item in added_edges if after_paths.get(item.source_symbol_id) == path
        )
        removed_edges = tuple(
            item for item in removed_edges if before_paths.get(item.source_symbol_id) == path
        )
    if not any(
        (
            added_files,
            removed_files,
            modified_files,
            added_symbols,
            removed_symbols,
            added_edges,
            removed_edges,
        )
    ):
        return packet, False, False
    added_file_paths = tuple(item.relative_path for item in added_files)[: query.maximum_files]
    removed_file_paths = tuple(item.relative_path for item in removed_files)[: query.maximum_files]
    modified_file_paths = tuple(item.relative_path for item in modified_files)[
        : query.maximum_files
    ]
    added_declarations = tuple(
        f"{item.relative_path}:{item.qualified_name}" for item in added_symbols
    )[: query.maximum_declarations]
    removed_declarations = tuple(
        f"{item.relative_path}:{item.qualified_name}" for item in removed_symbols
    )[: query.maximum_declarations]
    added_relationships = tuple(f"{item.kind.value}:{item.target}" for item in added_edges)[
        : query.maximum_relationships
    ]
    removed_relationships = tuple(f"{item.kind.value}:{item.target}" for item in removed_edges)[
        : query.maximum_relationships
    ]
    omitted_declaration_count = (
        len(added_symbols)
        + len(removed_symbols)
        - len(added_declarations)
        - len(removed_declarations)
    )
    omitted_relationship_count = (
        len(added_edges)
        + len(removed_edges)
        - len(added_relationships)
        - len(removed_relationships)
    )
    omitted_file_count = (
        len(added_files)
        + len(removed_files)
        + len(modified_files)
        - len(added_file_paths)
        - len(removed_file_paths)
        - len(modified_file_paths)
    )
    content = json.dumps(
        {
            "after_snapshot_id": str(after.snapshot_id),
            "before_snapshot_id": str(before.snapshot_id),
            "currentness": currentness.value,
            "file_fingerprints_available": files_available,
            "added_files": added_file_paths,
            "removed_files": removed_file_paths,
            "modified_files": modified_file_paths,
            "added_declarations": added_declarations,
            "removed_declarations": removed_declarations,
            "added_relationships": added_relationships,
            "removed_relationships": removed_relationships,
            "omitted_declaration_count": omitted_declaration_count,
            "omitted_relationship_count": omitted_relationship_count,
            "omitted_file_count": omitted_file_count,
            **(
                {"requested_relative_path": query.relative_path}
                if query.relative_path is not None
                else {}
            ),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    tokens = (len(content) + 3) // 4
    remaining = min(
        packet.budget.structural,
        packet.budget.total_limit - packet.declared_total_tokens,
    ) - sum(item.token_estimate for item in packet.structural_items)
    if tokens > remaining:
        return packet, False, True
    evidence = (
        _source_snapshot_evidence(packet, before),
        _source_snapshot_evidence(packet, after),
    )
    context_item = ContextItem(
        f"source-change:{before.snapshot_id}:{after.snapshot_id}",
        ContextItemType.STRUCTURAL_FACT,
        task_scope,
        content,
        ContentRepresentation.UNTRUSTED_EVIDENCE,
        tokens,
        evidence,
        SourceTrustClass.CURRENT_STRUCTURAL,
        Sensitivity.NORMAL,
        currentness,
        None,
        ConflictState.NONE,
        packet.created_at,
    )
    notice = ProvenanceNotice(
        f"provenance:{context_item.item_id}",
        context_item.item_id,
        f"mnemo:source-transition/{before.snapshot_id}/{after.snapshot_id}",
        hashlib.sha256(content.encode()).hexdigest(),
        evidence,
    )
    result = ContextPacket(
        packet.schema_version,
        packet.request_id,
        packet.owner_scope,
        packet.query_id,
        packet.task_id,
        packet.created_at,
        packet.expires_at,
        packet.declared_total_tokens + tokens,
        packet.budget,
        packet.producer_version,
        active_task_checkpoint=packet.active_task_checkpoint,
        episodic_memories=packet.episodic_memories,
        knowledge_items=packet.knowledge_items,
        structural_items=(*packet.structural_items, context_item),
        skills_and_procedures=packet.skills_and_procedures,
        provenance=(*packet.provenance, notice),
        omissions=packet.omissions,
        conflicts=packet.conflicts,
    )
    return (
        result,
        True,
        bool(omitted_file_count or omitted_declaration_count or omitted_relationship_count),
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
        episodic_memories=packet.episodic_memories,
        knowledge_items=packet.knowledge_items,
        structural_items=packet.structural_items,
        skills_and_procedures=packet.skills_and_procedures,
        provenance=packet.provenance,
        omissions=(*packet.omissions, OmissionNotice(item_id, reason, detail)),
        conflicts=packet.conflicts,
    )


def _project_scope(scope: MemoryScope) -> MemoryScope:
    """Structural snapshots are project-scoped even when a task requests context."""
    return MemoryScope(
        scope.owner_id,
        ScopeLevel.PROJECT,
        scope.visibility,
        scope.workspace_id,
        scope.project_id,
    )


def _checkpoint_reference_from_context_item(
    item_id: str,
) -> tuple[CheckpointId | None, CheckpointRevisionId | None]:
    """Decode only the canonical item identity emitted by ``CheckpointApplicationService``."""
    prefix = "checkpoint:"
    separator = ":revision:"
    if not isinstance(item_id, str) or not item_id.startswith(prefix) or separator not in item_id:
        return None, None
    checkpoint_value, revision_value = item_id[len(prefix) :].split(separator, maxsplit=1)
    try:
        return CheckpointId.from_string(checkpoint_value), CheckpointRevisionId.from_string(
            revision_value
        )
    except ValueError:
        return None, None


_SOURCE_EVIDENCE_NAMESPACE = UUID("55ee8cf3-d751-4bda-860e-a2452c270b98")


def _validate_source_relative_path(value: str) -> None:
    """Accept only a canonical, bounded, repository-relative POSIX identity."""
    if not value or len(value) > 512 or "\\" in value:
        raise ValueError("source change path must be a bounded relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value != path.as_posix():
        raise ValueError("source change path must be a canonical relative path")


def _source_currentness(snapshot: CodeSnapshot, supplied_digest: str | None) -> ValidityState:
    """Use only an exact source-tree digest as evidence that a snapshot is current."""
    if supplied_digest is None:
        return ValidityState.UNKNOWN
    return (
        ValidityState.CURRENT if supplied_digest == snapshot.source_digest else ValidityState.STALE
    )


def _source_snapshot_evidence(packet: ContextPacket, snapshot: CodeSnapshot) -> EvidenceReference:
    ref = f"source-snapshot:{snapshot.source_digest}"
    return EvidenceReference(
        EvidenceId(uuid5(_SOURCE_EVIDENCE_NAMESPACE, f"evidence:{ref}")),
        SourceId(uuid5(_SOURCE_EVIDENCE_NAMESPACE, f"source:{snapshot.source_digest}")),
        EvidenceSourceType.REPOSITORY,
        SourceTrustClass.CURRENT_STRUCTURAL,
        ref,
        snapshot.source_digest,
        EvidenceLocation(f"mnemo:source/{snapshot.snapshot_id}"),
        packet.created_at,
        VerificationStatus.VERIFIED,
    )


def _source_snapshot_difference(
    repository: SourceStructureRepository,
    scope: MemoryScope,
    before: CodeSnapshot,
    after: CodeSnapshot,
) -> tuple[
    bool,
    tuple[CodeFile, ...],
    tuple[CodeFile, ...],
    tuple[CodeFile, ...],
    tuple[CodeSymbol, ...],
    tuple[CodeSymbol, ...],
    tuple[CodeEdge, ...],
    tuple[CodeEdge, ...],
]:
    """Compare two immutable projections using only the storage-neutral source port."""
    before_symbols = {
        _source_symbol_key(item): item
        for item in repository.iter_symbols(scope, before.snapshot_id)
    }
    after_symbols = {
        _source_symbol_key(item): item for item in repository.iter_symbols(scope, after.snapshot_id)
    }
    before_edges = {
        _source_edge_key(item): item for item in repository.iter_edges(scope, before.snapshot_id)
    }
    after_edges = {
        _source_edge_key(item): item for item in repository.iter_edges(scope, after.snapshot_id)
    }
    before_files = {
        item.relative_path: item for item in repository.iter_files(scope, before.snapshot_id)
    }
    after_files = {
        item.relative_path: item for item in repository.iter_files(scope, after.snapshot_id)
    }
    files_available = (
        len(before_files) == before.file_count and len(after_files) == after.file_count
    )
    return (
        files_available,
        (
            tuple(after_files[key] for key in sorted(after_files.keys() - before_files.keys()))
            if files_available
            else ()
        ),
        (
            tuple(before_files[key] for key in sorted(before_files.keys() - after_files.keys()))
            if files_available
            else ()
        ),
        (
            tuple(
                after_files[key]
                for key in sorted(after_files.keys() & before_files.keys())
                if after_files[key].content_digest != before_files[key].content_digest
            )
            if files_available
            else ()
        ),
        tuple(after_symbols[key] for key in sorted(after_symbols.keys() - before_symbols.keys())),
        tuple(before_symbols[key] for key in sorted(before_symbols.keys() - after_symbols.keys())),
        tuple(after_edges[key] for key in sorted(after_edges.keys() - before_edges.keys())),
        tuple(before_edges[key] for key in sorted(before_edges.keys() - after_edges.keys())),
    )


def _source_symbol_key(symbol: CodeSymbol) -> tuple[str, str, str, int]:
    return (symbol.relative_path, symbol.qualified_name, symbol.kind.value, symbol.line)


def _source_edge_key(edge: CodeEdge) -> tuple[str, str, str, str]:
    return (str(edge.source_symbol_id), edge.target, edge.kind.value, str(edge.target_symbol_id))


def _append_source_items(
    packet: ContextPacket,
    task_scope: MemoryScope,
    snapshot: CodeSnapshot,
    symbols: tuple[CodeSymbol, ...],
    edges: tuple[CodeEdge, ...],
    module_symbols: dict[str, CodeSymbol],
    *,
    impact_direction: str | None = None,
    impact_depths: dict[CodeSymbolId, int] | None = None,
    currentness: ValidityState = ValidityState.UNKNOWN,
) -> ContextPacket:
    facts: list[ContextItem] = []
    remaining = min(
        packet.budget.structural, packet.budget.total_limit - packet.declared_total_tokens
    )
    notices: list[ProvenanceNotice] = list(packet.provenance)
    for symbol in symbols:
        content = json.dumps(
            {
                "snapshot_id": str(snapshot.snapshot_id),
                "currentness": currentness.value,
                "path": symbol.relative_path,
                "symbol": symbol.qualified_name,
                "kind": symbol.kind.value,
                "line": symbol.line,
                **(
                    {
                        "impact_direction": impact_direction,
                        "impact_depth": impact_depths[symbol.symbol_id],
                    }
                    if impact_direction is not None and impact_depths is not None
                    else {}
                ),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        tokens = (len(content) + 3) // 4
        if tokens > remaining:
            break
        ref = (
            f"source:{snapshot.source_digest}#{symbol.relative_path}:"
            f"{symbol.line}:{symbol.qualified_name}"
        )
        evidence = EvidenceReference(
            EvidenceId(uuid5(_SOURCE_EVIDENCE_NAMESPACE, f"evidence:{ref}")),
            SourceId(uuid5(_SOURCE_EVIDENCE_NAMESPACE, f"source:{snapshot.source_digest}")),
            EvidenceSourceType.REPOSITORY,
            SourceTrustClass.CURRENT_STRUCTURAL,
            ref,
            snapshot.source_digest,
            EvidenceLocation(f"mnemo:source/{snapshot.snapshot_id}/{symbol.relative_path}"),
            packet.created_at,
            VerificationStatus.VERIFIED,
        )
        context_item = ContextItem(
            f"source:{symbol.symbol_id}",
            ContextItemType.STRUCTURAL_FACT,
            task_scope,
            content,
            ContentRepresentation.UNTRUSTED_EVIDENCE,
            tokens,
            (evidence,),
            SourceTrustClass.CURRENT_STRUCTURAL,
            Sensitivity.NORMAL,
            currentness,
            None,
            ConflictState.NONE,
            packet.created_at,
        )
        facts.append(context_item)
        notices.append(
            ProvenanceNotice(
                f"provenance:{context_item.item_id}",
                context_item.item_id,
                f"mnemo:source/{snapshot.snapshot_id}/symbol/{symbol.symbol_id}",
                hashlib.sha256(content.encode()).hexdigest(),
                (evidence,),
            )
        )
        remaining -= tokens
    source_paths = {
        symbol.symbol_id: symbol.relative_path for symbol in (*module_symbols.values(), *symbols)
    }
    symbols_by_id = {symbol.symbol_id: symbol for symbol in symbols}
    for edge in edges:
        content = json.dumps(
            {
                "snapshot_id": str(snapshot.snapshot_id),
                "currentness": currentness.value,
                "path": source_paths[edge.source_symbol_id],
                "relationship": edge.kind.value,
                "target": edge.target,
                "resolved_target": (
                    {
                        "path": symbols_by_id[edge.target_symbol_id].relative_path,
                        "symbol": symbols_by_id[edge.target_symbol_id].qualified_name,
                    }
                    if edge.target_symbol_id in symbols_by_id
                    else None
                ),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        tokens = (len(content) + 3) // 4
        if tokens > remaining:
            break
        ref = f"source:{snapshot.source_digest}#{source_paths[edge.source_symbol_id]}:{edge.target}"
        evidence = EvidenceReference(
            EvidenceId(uuid5(_SOURCE_EVIDENCE_NAMESPACE, f"evidence:{ref}")),
            SourceId(uuid5(_SOURCE_EVIDENCE_NAMESPACE, f"source:{snapshot.source_digest}")),
            EvidenceSourceType.REPOSITORY,
            SourceTrustClass.CURRENT_STRUCTURAL,
            ref,
            snapshot.source_digest,
            EvidenceLocation(
                f"mnemo:source/{snapshot.snapshot_id}/{source_paths[edge.source_symbol_id]}"
            ),
            packet.created_at,
            VerificationStatus.VERIFIED,
        )
        context_item = ContextItem(
            f"source-edge:{snapshot.snapshot_id}:{edge.source_symbol_id}:{edge.target}",
            ContextItemType.STRUCTURAL_FACT,
            task_scope,
            content,
            ContentRepresentation.UNTRUSTED_EVIDENCE,
            tokens,
            (evidence,),
            SourceTrustClass.CURRENT_STRUCTURAL,
            Sensitivity.NORMAL,
            currentness,
            None,
            ConflictState.NONE,
            packet.created_at,
        )
        facts.append(context_item)
        notices.append(
            ProvenanceNotice(
                f"provenance:{context_item.item_id}",
                context_item.item_id,
                f"mnemo:source/{snapshot.snapshot_id}/edge/{edge.source_symbol_id}",
                hashlib.sha256(content.encode()).hexdigest(),
                (evidence,),
            )
        )
        remaining -= tokens
    omissions = packet.omissions
    if not facts:
        omissions += (
            OmissionNotice(
                "source-structure",
                OmissionReason.LOWER_RANK,
                "no matching source symbols or no remaining structural budget",
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
        packet.declared_total_tokens + sum(item.token_estimate for item in facts),
        packet.budget,
        packet.producer_version,
        active_task_checkpoint=packet.active_task_checkpoint,
        episodic_memories=packet.episodic_memories,
        knowledge_items=packet.knowledge_items,
        structural_items=(*packet.structural_items, *facts),
        skills_and_procedures=packet.skills_and_procedures,
        provenance=tuple(notices),
        omissions=omissions,
        conflicts=packet.conflicts,
    )
