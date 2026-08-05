"""Application-level assembly of durable checkpoint and dbt structural context."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Protocol, cast
from uuid import UUID, uuid5

from mnemo_memory.packages.application.checkpoints import (
    CheckpointApplicationService,
    GetCheckpointContext,
)
from mnemo_memory.packages.application.dbt import (
    DbtApplicationError,
    DbtManifestApplicationService,
    GetDbtSupplementalArtifacts,
    LineageDirection,
    QueryLineage,
    QueryManifestSelector,
    QuerySourceFreshness,
    QueryTestCoverage,
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
    CodeSymbolKind,
    ConflictNotice,
    ConflictState,
    ContentRepresentation,
    ContextBudget,
    ContextItem,
    ContextItemType,
    ContextPacket,
    CurrentKnowledgeDocumentSection,
    DbtFreshnessThreshold,
    DbtNodeId,
    DbtSnapshotId,
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    KnowledgeDocumentSectionMatch,
    MemoryScope,
    OmissionNotice,
    OmissionReason,
    ProjectClientProfile,
    ProjectProcedure,
    ProvenanceNotice,
    RankingMetadata,
    ScopeLevel,
    Sensitivity,
    SourceFileRename,
    SourceId,
    SourceTrustClass,
    ValidityState,
    VerificationStatus,
    normalize_knowledge_query,
    normalize_procedure_tags,
    unique_file_renames,
)
from mnemo_memory.packages.domain.dbt_manifest import ArtifactCurrentness, SourceStateFingerprint
from mnemo_memory.packages.storage.contracts import (
    CheckpointSourceObservationNotFound,
    CheckpointSourceObservationRepository,
    KnowledgeDocumentNotFound,
    KnowledgeDocumentRepository,
    ProjectProcedureRegistry,
    SourceStructureRepository,
)


class SemanticKnowledgeRetrieverPort(Protocol):
    """Application port for an explicitly enabled, already-local semantic projection."""

    def search_sections(
        self, scope: MemoryScope, query: str
    ) -> tuple[tuple[CurrentKnowledgeDocumentSection, float], ...]: ...


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
    destination_unique_id: DbtNodeId | None = None

    def __post_init__(self) -> None:
        if (self.unique_id is None) == (self.relative_path is None):
            raise ValueError("dbt lineage requires exactly one unique_id or relative_path")
        if self.relative_path is not None:
            _validate_source_relative_path(self.relative_path)
        if self.destination_unique_id is not None and self.destination_unique_id == self.unique_id:
            raise ValueError("dbt path requires distinct start and destination nodes")


@dataclass(frozen=True, slots=True)
class ContextDbtTestCoverageQuery:
    unique_id: DbtNodeId | None
    maximum_tests: int = 32
    snapshot_id: DbtSnapshotId | None = None
    current_content_digest: str | None = None
    current_source_state: SourceStateFingerprint | None = None
    require_current: bool = False
    relative_path: str | None = None

    def __post_init__(self) -> None:
        if (self.unique_id is None) == (self.relative_path is None):
            raise ValueError("dbt test coverage requires exactly one unique_id or relative_path")
        if self.relative_path is not None:
            _validate_source_relative_path(self.relative_path)
        if not 1 <= self.maximum_tests <= 100:
            raise ValueError("dbt test coverage limit must be between 1 and 100")


@dataclass(frozen=True, slots=True)
class ContextDbtSelectorQuery:
    resource_type: str | None = None
    package_name: str | None = None
    tag: str | None = None
    maximum_nodes: int = 32
    snapshot_id: DbtSnapshotId | None = None
    current_content_digest: str | None = None
    current_source_state: SourceStateFingerprint | None = None
    require_current: bool = False

    def __post_init__(self) -> None:
        if self.resource_type is None and self.package_name is None and self.tag is None:
            raise ValueError("dbt selector requires at least one exact filter")
        for field_name in ("resource_type", "package_name", "tag"):
            value = getattr(self, field_name)
            if value is not None and (not value.strip() or len(value) > 256):
                raise ValueError(f"dbt selector {field_name} must be a bounded non-empty string")
        if not 1 <= self.maximum_nodes <= 100:
            raise ValueError("dbt selector node limit must be between 1 and 100")


@dataclass(frozen=True, slots=True)
class ContextDbtFreshnessQuery:
    unique_id: DbtNodeId | None
    snapshot_id: DbtSnapshotId | None = None
    current_content_digest: str | None = None
    current_source_state: SourceStateFingerprint | None = None
    require_current: bool = False
    relative_path: str | None = None

    def __post_init__(self) -> None:
        if (self.unique_id is None) == (self.relative_path is None):
            raise ValueError("dbt freshness requires exactly one unique_id or relative_path")
        if self.relative_path is not None:
            _validate_source_relative_path(self.relative_path)


@dataclass(frozen=True, slots=True)
class ContextSourceImpactQuery:
    """A bounded static impact request; dynamic behavior is intentionally excluded."""

    symbol: str | None
    direction: str = "dependents"
    transitive: bool = True
    maximum_depth: int | None = None
    maximum_symbols: int = 100
    maximum_edges: int = 200
    snapshot_id: CodeSnapshotId | None = None
    current_source_digest: str | None = None
    require_current: bool = False
    relative_path: str | None = None

    def __post_init__(self) -> None:
        if (self.symbol is None) == (self.relative_path is None):
            raise ValueError("source impact requires exactly one symbol or relative path")
        if self.symbol is not None and (not self.symbol.strip() or len(self.symbol) > 512):
            raise ValueError("source impact requires a bounded symbol")
        if self.relative_path is not None:
            _validate_source_relative_path(self.relative_path)
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
class ContextSourceOverviewQuery:
    """Request a small deterministic inventory of one scoped source snapshot.

    This is intentionally an inventory of persisted file/declaration identities and counts, not a
    source replay or a claim about runtime behavior. It is bounded before packet rendering so an
    automatic session can learn repository shape without consuming a full structural section.
    """

    maximum_files: int = 12
    maximum_modules: int = 12
    maximum_declarations: int = 24
    snapshot_id: CodeSnapshotId | None = None
    current_source_digest: str | None = None
    require_current: bool = False

    def __post_init__(self) -> None:
        if self.maximum_files < 1 or self.maximum_modules < 1 or self.maximum_declarations < 1:
            raise ValueError("source overview limits must be positive")
        if self.maximum_files > 32 or self.maximum_modules > 32 or self.maximum_declarations > 64:
            raise ValueError("source overview limits exceed bounded inventory")
        if self.current_source_digest is not None and (
            not self.current_source_digest.startswith("sha256:")
            or len(self.current_source_digest) != 71
        ):
            raise ValueError("source overview digest must be a sha256 digest")


@dataclass(frozen=True, slots=True)
class ContextCheckpointSourceImpact:
    """Bounded static dependents of checkpoint-declared relevant source files.

    ``relevant_files`` is a task handoff hint, not structural authority. Mnemo uses it only to
    select a small ordered set of exact relative paths from the active source snapshot. Every
    returned relationship is still derived from that immutable syntax projection and carries its
    own evidence.
    """

    maximum_symbols: int = 4
    maximum_edges: int = 2
    maximum_depth: int = 1
    maximum_files: int = 2
    current_source_digest: str | None = None
    require_current: bool = False

    def __post_init__(self) -> None:
        if not 1 <= self.maximum_symbols <= 12:
            raise ValueError("checkpoint source impact symbol limit must be between 1 and 12")
        if not 1 <= self.maximum_edges <= 24:
            raise ValueError("checkpoint source impact edge limit must be between 1 and 24")
        if not 0 <= self.maximum_depth <= 4:
            raise ValueError("checkpoint source impact depth must be between 0 and 4")
        if not 1 <= self.maximum_files <= 4:
            raise ValueError("checkpoint source impact file limit must be between 1 and 4")
        if self.current_source_digest is not None and (
            not self.current_source_digest.startswith("sha256:")
            or len(self.current_source_digest) != 71
        ):
            raise ValueError("checkpoint source impact digest must be a sha256 digest")


@dataclass(frozen=True, slots=True)
class GetUnifiedContext:
    scope: MemoryScope
    checkpoint_id: object | None = None
    lineage: ContextLineageQuery | None = None
    source_query: str | None = None
    budget: ContextBudget = field(default_factory=ContextBudget)
    source_impact: ContextSourceImpactQuery | None = None
    source_changes: ContextSourceChangeQuery | None = None
    source_overview: ContextSourceOverviewQuery | None = None
    checkpoint_source_impact: ContextCheckpointSourceImpact | None = None
    knowledge_query: str | None = None
    semantic_knowledge_query: str | None = None
    include_checkpoint_file_knowledge: bool = False
    include_lifecycle_events: bool = False
    include_approved_events: bool = False
    procedure_tags: tuple[str, ...] = ()
    procedure_profile: ProjectClientProfile | None = None
    dbt_test_coverage: ContextDbtTestCoverageQuery | None = None
    dbt_selector: ContextDbtSelectorQuery | None = None
    dbt_freshness: ContextDbtFreshnessQuery | None = None

    def __post_init__(self) -> None:
        if (
            sum(
                query is not None
                for query in (
                    self.lineage,
                    self.dbt_test_coverage,
                    self.dbt_selector,
                    self.dbt_freshness,
                )
            )
            > 1
        ):
            raise ValueError("request only one dbt structural query at a time")
        if self.procedure_tags:
            object.__setattr__(
                self, "procedure_tags", normalize_procedure_tags(self.procedure_tags)
            )
        if self.procedure_profile is not None and (
            not self.procedure_tags or self.procedure_profile.procedure_tags != self.procedure_tags
        ):
            raise ValueError("procedure profile must match explicit procedure tags")


class UnifiedContextService:
    """Combines separately-authoritative checkpoint and structural evidence deterministically."""

    def __init__(
        self,
        checkpoints: CheckpointApplicationService,
        dbt: DbtManifestApplicationService | None,
        source: SourceStructureRepository | None = None,
        checkpoint_source_observations: CheckpointSourceObservationRepository | None = None,
        knowledge: KnowledgeDocumentRepository | None = None,
        semantic_knowledge: SemanticKnowledgeRetrieverPort | None = None,
        procedures: ProjectProcedureRegistry | None = None,
    ) -> None:
        self._checkpoints = checkpoints
        self._dbt = dbt
        self._source = source
        self._checkpoint_source_observations = checkpoint_source_observations
        self._knowledge = knowledge
        self._semantic_knowledge = semantic_knowledge
        self._procedures = procedures

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
        packet = self._with_requested_knowledge(packet, request)
        packet = self._with_requested_procedures(packet, request)
        if (
            request.lineage is None
            and request.dbt_test_coverage is None
            and request.dbt_selector is None
            and request.dbt_freshness is None
            and request.source_query is None
            and request.source_impact is None
            and request.source_changes is None
            and request.source_overview is None
            and request.checkpoint_source_impact is None
            and request.knowledge_query is None
            and request.semantic_knowledge_query is None
            and not request.procedure_tags
        ):
            return packet
        if request.lineage is not None:
            return self._with_dbt_lineage(packet, request)
        if request.dbt_test_coverage is not None:
            return self._with_dbt_test_coverage(packet, request)
        if request.dbt_selector is not None:
            return self._with_dbt_selector(packet, request)
        if request.dbt_freshness is not None:
            return self._with_dbt_freshness(packet, request)
        return self._with_requested_source_facts(packet, request)

    def _with_requested_procedures(
        self, packet: ContextPacket, request: GetUnifiedContext
    ) -> ContextPacket:
        """Attach explicitly requested checked-in procedures as bounded cited evidence.

        A tag selects a procedure; Mnemo never derives tags from a prompt, task name, agent, or
        source body. The Markdown body remains untrusted evidence even when its frontmatter marks
        it mandatory relative to other project procedures.
        """
        if not request.procedure_tags:
            return packet
        if self._procedures is None:
            return _with_omission(
                packet,
                "procedures",
                OmissionReason.LOWER_RANK,
                "procedure registry is unavailable",
            )
        procedures = self._procedures.find_current_procedures(
            _project_scope(request.scope), request.procedure_tags, 8
        )
        remaining = min(
            request.budget.skills_and_procedures
            - sum(item.token_estimate for item in packet.skills_and_procedures),
            request.budget.total_limit - packet.declared_total_tokens,
        )
        items: list[ContextItem] = []
        notices: list[ProvenanceNotice] = list(packet.provenance)
        for rank, procedure in enumerate(procedures, start=1):
            item, notice = _procedure_context_item(
                request.scope, procedure, rank, request.procedure_profile
            )
            if item.token_estimate > remaining:
                packet = _with_omission(
                    packet,
                    item.item_id,
                    OmissionReason.TOKEN_BUDGET,
                    "procedure exceeds the remaining skills and procedures budget",
                )
                continue
            items.append(item)
            notices.append(notice)
            remaining -= item.token_estimate
        if not items:
            return packet
        return ContextPacket(
            packet.schema_version,
            packet.request_id,
            packet.owner_scope,
            packet.query_id,
            packet.task_id,
            packet.created_at,
            packet.expires_at,
            packet.declared_total_tokens + sum(item.token_estimate for item in items),
            packet.budget,
            packet.producer_version,
            active_task_checkpoint=packet.active_task_checkpoint,
            episodic_memories=packet.episodic_memories,
            knowledge_items=packet.knowledge_items,
            structural_items=packet.structural_items,
            skills_and_procedures=(*packet.skills_and_procedures, *items),
            provenance=tuple(notices),
            omissions=packet.omissions,
            conflicts=packet.conflicts,
        )

    def _with_requested_knowledge(
        self, packet: ContextPacket, request: GetUnifiedContext
    ) -> ContextPacket:
        """Attach bounded literal note sections with exact revision provenance.

        Local documents are user-authored but remain untrusted evidence. Selecting an active stored
        revision does not prove that its filesystem source is current, so items label that fact as
        unknown rather than presenting a note as present-day structural authority.
        """
        query = request.knowledge_query
        if query is None and request.include_checkpoint_file_knowledge:
            query = _checkpoint_file_knowledge_query(packet)
        semantic_query = request.semantic_knowledge_query
        if query is None and semantic_query is None:
            return packet
        if query is not None and self._knowledge is None:
            return _with_omission(
                packet, "knowledge", OmissionReason.LOWER_RANK, "knowledge index is unavailable"
            )
        matches: list[KnowledgeDocumentSectionMatch] = []
        if query is not None:
            terms = normalize_knowledge_query(query)
            if not terms:
                raise ValueError("knowledge query requires at least one searchable term")
            assert self._knowledge is not None
            matches.extend(
                self._knowledge.search_current_sections(
                    _project_scope(request.scope), terms, 8, 128
                )
            )
        if semantic_query is not None:
            if self._semantic_knowledge is None:
                return _with_omission(
                    packet,
                    "semantic-knowledge",
                    OmissionReason.LOWER_RANK,
                    "local semantic knowledge index is unavailable",
                )
            semantic = self._semantic_knowledge.search_sections(
                _project_scope(request.scope), semantic_query
            )
            matches.extend(
                KnowledgeDocumentSectionMatch(
                    section.revision,
                    section.section_index,
                    section.section,
                    max(1, int(similarity * 10_000)),
                )
                for section, similarity in semantic
            )
        selected: dict[tuple[object, int], KnowledgeDocumentSectionMatch] = {}
        for match in matches:
            section_key = (match.revision.revision_id, match.section_index)
            previous = selected.get(section_key)
            if previous is None or match.score > previous.score:
                selected[section_key] = match
        conflict_pairs: list[tuple[tuple[object, int], tuple[object, int]]] = []
        conflict_documents: set[tuple[object, object]] = set()
        if self._knowledge is not None:
            for selected_key, match in tuple(selected.items()):
                target_path = _knowledge_conflict_target(match)
                if target_path is None:
                    continue
                try:
                    target = self._knowledge.get_current_revision_by_path(
                        _project_scope(request.scope), target_path
                    )
                except KnowledgeDocumentNotFound:
                    continue
                if not target.document.sections:
                    continue
                target_key: tuple[object, int] = (target.revision_id, 0)
                selected.setdefault(
                    target_key,
                    KnowledgeDocumentSectionMatch(target, 0, target.document.sections[0], 1),
                )
                document_pair = (match.revision.document.document_id, target.document.document_id)
                if (
                    target_key != selected_key
                    and document_pair not in conflict_documents
                    and len(conflict_pairs) < 4
                ):
                    conflict_pairs.append((selected_key, target_key))
                    conflict_documents.add(document_pair)
        matches = sorted(
            selected.values(),
            key=lambda item: (
                -item.score,
                item.revision.document.relative_path,
                item.section_index,
            ),
        )[:8]
        remaining = min(
            request.budget.knowledge - sum(item.token_estimate for item in packet.knowledge_items),
            request.budget.total_limit - packet.declared_total_tokens,
        )
        items: list[ContextItem] = []
        notices: list[ProvenanceNotice] = list(packet.provenance)
        item_ids: dict[tuple[object, int], ContextItem] = {}
        for rank, match in enumerate(matches, start=1):
            item, notice = _knowledge_context_item(packet, request.scope, match, rank)
            if item.token_estimate > remaining:
                packet = _with_omission(
                    packet,
                    item.item_id,
                    OmissionReason.TOKEN_BUDGET,
                    "knowledge section exceeds the remaining knowledge budget",
                )
                continue
            items.append(item)
            item_ids[(match.revision.revision_id, match.section_index)] = item
            notices.append(notice)
            remaining -= item.token_estimate
        if not items and matches:
            return packet
        conflicts = list(packet.conflicts)
        for left_key, right_key in conflict_pairs:
            left, right = item_ids.get(left_key), item_ids.get(right_key)
            if left is None or right is None:
                continue
            conflicts.append(
                ConflictNotice(
                    f"knowledge-conflict:{left.item_id}:{right.item_id}",
                    (left.item_id, right.item_id),
                    (*left.evidence_references, *right.evidence_references),
                    ConflictState.UNRESOLVED,
                )
            )
        return ContextPacket(
            packet.schema_version,
            packet.request_id,
            packet.owner_scope,
            packet.query_id,
            packet.task_id,
            packet.created_at,
            packet.expires_at,
            packet.declared_total_tokens + sum(item.token_estimate for item in items),
            packet.budget,
            packet.producer_version,
            active_task_checkpoint=packet.active_task_checkpoint,
            episodic_memories=packet.episodic_memories,
            knowledge_items=(*packet.knowledge_items, *items),
            structural_items=packet.structural_items,
            skills_and_procedures=packet.skills_and_procedures,
            provenance=tuple(notices),
            omissions=packet.omissions,
            conflicts=tuple(conflicts),
        )

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
                query.destination_unique_id,
            )
        )
        if query.require_current and result.currentness is not ArtifactCurrentness.CURRENT:
            result_packet = _with_omission(
                packet, "dbt-lineage", OmissionReason.STALE, "structural facts are not current"
            )
            return self._with_requested_source_facts(result_packet, request)
        if not result.path_found:
            result_packet = _with_omission(
                packet,
                "dbt-path",
                OmissionReason.LOWER_RANK,
                result.truncation_reason or "no directed dbt path",
            )
            return self._with_requested_source_facts(result_packet, request)
        supplemental = None
        try:
            supplemental = self._dbt.get_supplemental(
                GetDbtSupplementalArtifacts(
                    _project_scope(request.scope), result.snapshot.snapshot_id
                )
            )
        except DbtApplicationError:
            packet = _with_omission(
                packet,
                "dbt-supplemental",
                OmissionReason.LOWER_RANK,
                "supplemental dbt facts are unavailable",
            )
        catalog_by_id = (
            {relation.unique_id: relation for relation in supplemental.catalog.relations}
            if supplemental is not None and supplemental.catalog is not None
            else {}
        )
        run_by_id = (
            {node.unique_id: node for node in supplemental.run_results.results}
            if supplemental is not None and supplemental.run_results is not None
            else {}
        )
        facts: list[ContextItem] = []
        notices: list[ProvenanceNotice] = list(packet.provenance)
        remaining = min(
            request.budget.structural
            - sum(item.token_estimate for item in packet.structural_items),
            request.budget.total_limit - packet.declared_total_tokens,
        )
        for item in result.nodes:
            lineage_edges = tuple(
                edge
                for edge in result.edges
                if (
                    edge.parent_id == item.node.unique_id
                    if result.direction is LineageDirection.UPSTREAM
                    else edge.child_id == item.node.unique_id
                )
            )
            content_value: dict[str, object] = {
                "query_kind": "path" if result.destination_node is not None else "lineage",
                "snapshot_id": str(result.snapshot.snapshot_id),
                "start_node": str(result.start_node.unique_id),
                "node_unique_id": str(item.node.unique_id),
                "resource_type": item.node.raw_resource_type,
                "direction": result.direction.value,
                "depth": item.depth,
                "currentness": result.currentness.value,
                "relative_file": item.node.original_file_path,
                "lineage_edge_types": sorted({edge.edge_type.value for edge in lineage_edges}),
            }
            if result.destination_node is not None:
                content_value["path_destination"] = str(result.destination_node.unique_id)
            evidence = [item.node.evidence]
            for edge in lineage_edges:
                if edge.evidence not in evidence:
                    evidence.append(edge.evidence)
            relation = catalog_by_id.get(item.node.unique_id)
            if relation is not None:
                columns = relation.columns[:12]
                content_value["catalog"] = {
                    "relation_type": relation.relation_type,
                    "database": relation.database,
                    "schema": relation.schema_name,
                    "name": relation.name,
                    "column_count": len(relation.columns),
                    "columns": [
                        {
                            "index": column.index,
                            "name": column.name,
                            "data_type": column.data_type,
                        }
                        for column in columns
                    ],
                    "columns_omitted": len(relation.columns) - len(columns),
                }
                evidence.append(relation.evidence)
            node_run = run_by_id.get(item.node.unique_id)
            if node_run is not None:
                content_value["latest_run"] = {
                    "status": node_run.status.value,
                    "execution_time_seconds": node_run.execution_time_seconds,
                    "failures": node_run.failures,
                }
                evidence.append(node_run.evidence)
            content = json.dumps(
                content_value,
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
                evidence_references=tuple(evidence),
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
                    tuple(evidence),
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
            episodic_memories=packet.episodic_memories,
            knowledge_items=packet.knowledge_items,
            structural_items=(*packet.structural_items, *facts),
            skills_and_procedures=packet.skills_and_procedures,
            provenance=tuple(notices),
            omissions=omissions,
            conflicts=packet.conflicts,
        )
        return self._with_requested_source_facts(result_packet, request)

    def _with_dbt_test_coverage(
        self, packet: ContextPacket, request: GetUnifiedContext
    ) -> ContextPacket:
        """Attach direct manifest tests and their latest persisted execution status."""
        query = request.dbt_test_coverage
        assert query is not None
        if self._dbt is None:
            return self._with_requested_source_facts(
                _with_omission(
                    packet,
                    "dbt-test-coverage",
                    OmissionReason.LOWER_RANK,
                    "dbt index is unavailable",
                ),
                request,
            )
        unique_id = query.unique_id
        if unique_id is None:
            assert query.relative_path is not None
            unique_id = self._dbt.resolve_file(
                ResolveManifestFile(
                    _project_scope(request.scope), query.relative_path, query.snapshot_id
                )
            ).node.unique_id
        result = self._dbt.query_test_coverage(
            QueryTestCoverage(
                _project_scope(request.scope),
                unique_id,
                query.maximum_tests,
                query.snapshot_id,
                query.current_content_digest,
                query.current_source_state,
            )
        )
        if query.require_current and result.currentness is not ArtifactCurrentness.CURRENT:
            return self._with_requested_source_facts(
                _with_omission(
                    packet,
                    "dbt-test-coverage",
                    OmissionReason.STALE,
                    "structural facts are not current",
                ),
                request,
            )
        if not result.test_nodes:
            return self._with_requested_source_facts(
                _with_omission(
                    packet,
                    "dbt-test-coverage",
                    OmissionReason.LOWER_RANK,
                    "no directly attached enabled manifest tests",
                ),
                request,
            )
        try:
            supplemental = self._dbt.get_supplemental(
                GetDbtSupplementalArtifacts(
                    _project_scope(request.scope), result.snapshot.snapshot_id
                )
            )
        except DbtApplicationError:
            supplemental = None
        run_by_id = (
            {node.unique_id: node for node in supplemental.run_results.results}
            if supplemental is not None and supplemental.run_results is not None
            else {}
        )
        edges_by_test = {edge.child_id: edge for edge in result.edges}
        facts: list[ContextItem] = []
        notices = list(packet.provenance)
        omissions = packet.omissions
        remaining = min(
            request.budget.structural
            - sum(item.token_estimate for item in packet.structural_items),
            request.budget.total_limit - packet.declared_total_tokens,
        )
        for node in result.test_nodes:
            edge = edges_by_test[node.unique_id]
            content_value: dict[str, object] = {
                "query_kind": "test_coverage",
                "snapshot_id": str(result.snapshot.snapshot_id),
                "subject_node": str(result.subject_node.unique_id),
                "test_unique_id": str(node.unique_id),
                "resource_type": node.raw_resource_type,
                "relative_file": node.original_file_path,
                "currentness": result.currentness.value,
            }
            evidence = [node.evidence, edge.evidence]
            node_run = run_by_id.get(node.unique_id)
            if node_run is not None:
                content_value["latest_run"] = {
                    "status": node_run.status.value,
                    "execution_time_seconds": node_run.execution_time_seconds,
                    "failures": node_run.failures,
                }
                evidence.append(node_run.evidence)
            content = json.dumps(content_value, sort_keys=True, separators=(",", ":"))
            tokens = (len(content) + 3) // 4
            if tokens > remaining:
                omissions += (
                    OmissionNotice(
                        f"dbt-test:{node.unique_id}",
                        OmissionReason.TOKEN_BUDGET,
                        "dbt test coverage exceeds context budget",
                    ),
                )
                break
            context_item = ContextItem(
                item_id=f"dbt-test:{result.snapshot.snapshot_id}:{node.unique_id}",
                item_type=ContextItemType.STRUCTURAL_FACT,
                source_scope=request.scope,
                content=content,
                content_representation=ContentRepresentation.UNTRUSTED_EVIDENCE,
                token_estimate=tokens,
                evidence_references=tuple(evidence),
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
                    f"mnemo:dbt/snapshot/{result.snapshot.snapshot_id}/test/{node.unique_id}",
                    hashlib.sha256(content.encode()).hexdigest(),
                    tuple(evidence),
                )
            )
        if result.truncated:
            omissions += (
                OmissionNotice(
                    "dbt-test-coverage",
                    OmissionReason.LOWER_RANK,
                    "dbt test coverage result limit reached",
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
        return self._with_requested_source_facts(result_packet, request)

    def _with_dbt_selector(
        self, packet: ContextPacket, request: GetUnifiedContext
    ) -> ContextPacket:
        """Attach exact enabled manifest matches for a small structured selector."""
        query = request.dbt_selector
        assert query is not None
        if self._dbt is None:
            return self._with_requested_source_facts(
                _with_omission(
                    packet, "dbt-selector", OmissionReason.LOWER_RANK, "dbt index is unavailable"
                ),
                request,
            )
        result = self._dbt.query_selector(
            QueryManifestSelector(
                _project_scope(request.scope),
                query.resource_type,
                query.package_name,
                query.tag,
                query.maximum_nodes,
                query.snapshot_id,
                query.current_content_digest,
                query.current_source_state,
            )
        )
        if query.require_current and result.currentness is not ArtifactCurrentness.CURRENT:
            return self._with_requested_source_facts(
                _with_omission(
                    packet,
                    "dbt-selector",
                    OmissionReason.STALE,
                    "structural facts are not current",
                ),
                request,
            )
        if not result.nodes:
            return self._with_requested_source_facts(
                _with_omission(
                    packet,
                    "dbt-selector",
                    OmissionReason.LOWER_RANK,
                    "no enabled manifest nodes match the exact selector",
                ),
                request,
            )
        facts: list[ContextItem] = []
        notices = list(packet.provenance)
        omissions = packet.omissions
        remaining = min(
            request.budget.structural
            - sum(item.token_estimate for item in packet.structural_items),
            request.budget.total_limit - packet.declared_total_tokens,
        )
        for node in result.nodes:
            content = json.dumps(
                {
                    "query_kind": "selector",
                    "snapshot_id": str(result.snapshot.snapshot_id),
                    "node_unique_id": str(node.unique_id),
                    "resource_type": node.raw_resource_type,
                    "package_name": node.package_name,
                    "tags": list(node.tags),
                    "relative_file": node.original_file_path,
                    "currentness": result.currentness.value,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            tokens = (len(content) + 3) // 4
            if tokens > remaining:
                omissions += (
                    OmissionNotice(
                        f"dbt-selector:{node.unique_id}",
                        OmissionReason.TOKEN_BUDGET,
                        "dbt selector results exceed context budget",
                    ),
                )
                break
            context_item = ContextItem(
                item_id=f"dbt-selector:{result.snapshot.snapshot_id}:{node.unique_id}",
                item_type=ContextItemType.STRUCTURAL_FACT,
                source_scope=request.scope,
                content=content,
                content_representation=ContentRepresentation.UNTRUSTED_EVIDENCE,
                token_estimate=tokens,
                evidence_references=(node.evidence,),
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
                    f"mnemo:dbt/snapshot/{result.snapshot.snapshot_id}/selector/{node.unique_id}",
                    hashlib.sha256(content.encode()).hexdigest(),
                    (node.evidence,),
                )
            )
        if result.truncated:
            omissions += (
                OmissionNotice(
                    "dbt-selector",
                    OmissionReason.LOWER_RANK,
                    "dbt selector node limit reached",
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
        return self._with_requested_source_facts(result_packet, request)

    def _with_dbt_freshness(
        self, packet: ContextPacket, request: GetUnifiedContext
    ) -> ContextPacket:
        """Attach one observed source-freshness result from a persisted sources.json."""
        query = request.dbt_freshness
        assert query is not None
        if self._dbt is None:
            return self._with_requested_source_facts(
                _with_omission(
                    packet, "dbt-freshness", OmissionReason.LOWER_RANK, "dbt index is unavailable"
                ),
                request,
            )
        unique_id = query.unique_id
        if unique_id is None:
            assert query.relative_path is not None
            unique_id = self._dbt.resolve_file(
                ResolveManifestFile(
                    _project_scope(request.scope), query.relative_path, query.snapshot_id
                )
            ).node.unique_id
        result = self._dbt.query_source_freshness(
            QuerySourceFreshness(
                _project_scope(request.scope),
                unique_id,
                query.snapshot_id,
                query.current_content_digest,
                query.current_source_state,
            )
        )
        if query.require_current and result.currentness is not ArtifactCurrentness.CURRENT:
            return self._with_requested_source_facts(
                _with_omission(
                    packet,
                    "dbt-freshness",
                    OmissionReason.STALE,
                    "manifest structural state is not current",
                ),
                request,
            )
        observation = result.observation
        artifact = result.artifact
        if observation is None or artifact is None:
            return self._with_requested_source_facts(
                _with_omission(
                    packet,
                    "dbt-freshness",
                    OmissionReason.LOWER_RANK,
                    "no persisted sources.json observation exists for this source",
                ),
                request,
            )

        def threshold(value: DbtFreshnessThreshold | None) -> dict[str, object] | None:
            if value is None:
                return None
            return {
                "count": value.count,
                "period": value.period.value,
            }

        content = json.dumps(
            {
                "query_kind": "source_freshness",
                "snapshot_id": str(result.snapshot.snapshot_id),
                "source_unique_id": str(result.source_node.unique_id),
                "status": observation.status.value,
                "max_loaded_at": None
                if observation.max_loaded_at is None
                else observation.max_loaded_at.isoformat(),
                "snapshotted_at": None
                if observation.snapshotted_at is None
                else observation.snapshotted_at.isoformat(),
                "age_seconds": observation.age_seconds,
                "warn_after": threshold(observation.warn_after),
                "error_after": threshold(observation.error_after),
                "execution_time_seconds": observation.execution_time_seconds,
                "artifact_generated_at": None
                if artifact.metadata.generated_at is None
                else artifact.metadata.generated_at.isoformat(),
                "manifest_currentness": result.currentness.value,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        tokens = (len(content) + 3) // 4
        if tokens > min(
            request.budget.structural
            - sum(item.token_estimate for item in packet.structural_items),
            request.budget.total_limit - packet.declared_total_tokens,
        ):
            return self._with_requested_source_facts(
                _with_omission(
                    packet,
                    "dbt-freshness",
                    OmissionReason.TOKEN_BUDGET,
                    "dbt freshness fact exceeds context budget",
                ),
                request,
            )
        evidence = (result.source_node.evidence, observation.evidence)
        context_item = ContextItem(
            item_id=(f"dbt-freshness:{result.snapshot.snapshot_id}:{result.source_node.unique_id}"),
            item_type=ContextItemType.STRUCTURAL_FACT,
            source_scope=request.scope,
            content=content,
            content_representation=ContentRepresentation.UNTRUSTED_EVIDENCE,
            token_estimate=tokens,
            evidence_references=evidence,
            source_trust=SourceTrustClass.APPROVED_CHECKPOINT,
            sensitivity=Sensitivity.NORMAL,
            validity=ValidityState.CURRENT
            if result.currentness is ArtifactCurrentness.CURRENT
            else ValidityState.STALE,
            ranking=None,
            conflict_state=ConflictState.NONE,
            observed_at=artifact.metadata.ingested_at,
        )
        notice = ProvenanceNotice(
            f"provenance:{context_item.item_id}",
            context_item.item_id,
            (
                f"mnemo:dbt/snapshot/{result.snapshot.snapshot_id}/"
                f"source-freshness/{result.source_node.unique_id}"
            ),
            hashlib.sha256(content.encode()).hexdigest(),
            evidence,
        )
        result_packet = ContextPacket(
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
        if request.checkpoint_source_impact is not None:
            packet = self._with_checkpoint_source_impact(
                packet, request, request.checkpoint_source_impact
            )
        if request.source_impact is not None or request.source_query:
            packet = self._with_source_facts(packet, request)
        if request.source_changes is not None:
            packet = self._with_recent_source_changes(packet, request, request.source_changes)
        if request.source_overview is not None:
            packet = self._with_source_overview(packet, request, request.source_overview)
        return packet

    def _with_checkpoint_source_impact(
        self,
        packet: ContextPacket,
        request: GetUnifiedContext,
        query: ContextCheckpointSourceImpact,
    ) -> ContextPacket:
        """Attach bounded relevant-file impact candidates without treating checkpoint text as truth.

        The checkpoint writer controls the order of its ``relevant_files`` list. We keep that
        order, select a small set of canonical paths that exists in the scoped active snapshot, and
        apply the ordinary source-impact contract to each. This avoids broad file search, preserves
        a small session budget, and keeps a stale checkpoint from overriding structural evidence.
        """
        if self._source is None or packet.active_task_checkpoint is None:
            return packet
        paths = _checkpoint_relevant_source_paths(packet.active_task_checkpoint.content)
        if not paths:
            return packet
        scope = _project_scope(request.scope)
        snapshot = self._source.get_active_snapshot(scope)
        if snapshot is None:
            return packet
        snapshot_paths = {
            item.relative_path for item in self._source.iter_files(scope, snapshot.snapshot_id)
        }
        selected_paths = tuple(dict.fromkeys(path for path in paths if path in snapshot_paths))[
            : query.maximum_files
        ]
        if not selected_paths:
            return packet
        result = packet
        for selected_path in selected_paths:
            result = self._with_source_impact_facts(
                result,
                request,
                ContextSourceImpactQuery(
                    symbol=None,
                    relative_path=selected_path,
                    direction="dependents",
                    transitive=True,
                    maximum_depth=query.maximum_depth,
                    maximum_symbols=query.maximum_symbols,
                    maximum_edges=query.maximum_edges,
                    current_source_digest=query.current_source_digest,
                    require_current=query.require_current,
                ),
            )
        return result

    def _with_source_overview(
        self, packet: ContextPacket, request: GetUnifiedContext, query: ContextSourceOverviewQuery
    ) -> ContextPacket:
        if self._source is None:
            return _with_omission(
                packet, "source-overview", OmissionReason.LOWER_RANK, "no source snapshot"
            )
        scope = _project_scope(request.scope)
        snapshot = (
            self._source.get_snapshot(scope, query.snapshot_id)
            if query.snapshot_id
            else self._source.get_active_snapshot(scope)
        )
        if snapshot is None:
            return _with_omission(
                packet, "source-overview", OmissionReason.LOWER_RANK, "no source snapshot"
            )
        currentness = _source_currentness(snapshot, query.current_source_digest)
        if query.require_current and currentness is not ValidityState.CURRENT:
            return _with_omission(
                packet,
                "source-overview",
                OmissionReason.STALE,
                "source snapshot is not proven current",
            )
        files = tuple(
            sorted(
                self._source.iter_files(scope, snapshot.snapshot_id),
                key=lambda item: item.relative_path,
            )
        )[: query.maximum_files]
        all_symbols = tuple(
            sorted(
                self._source.iter_symbols(scope, snapshot.snapshot_id),
                key=lambda item: (
                    item.relative_path,
                    item.line,
                    item.qualified_name,
                    str(item.symbol_id),
                ),
            )
        )
        modules = tuple(item for item in all_symbols if item.kind is CodeSymbolKind.MODULE)[
            : query.maximum_modules
        ]
        declarations = tuple(
            item for item in all_symbols if item.kind is not CodeSymbolKind.MODULE
        )[: query.maximum_declarations]
        packet = _with_source_overview_summary(
            packet,
            request.scope,
            snapshot,
            currentness,
            len(files),
            len(modules),
            len(declarations),
            snapshot.file_count - len(files),
            sum(item.kind is CodeSymbolKind.MODULE for item in all_symbols) - len(modules),
            sum(item.kind is not CodeSymbolKind.MODULE for item in all_symbols) - len(declarations),
        )
        if not packet.structural_items or not any(
            item.item_id == f"source-overview:{snapshot.snapshot_id}"
            for item in packet.structural_items
        ):
            return packet
        prior_source_item_ids = {
            item.item_id for item in packet.structural_items if item.item_id.startswith("source:")
        }
        prior_file_item_ids = {
            item.item_id
            for item in packet.structural_items
            if item.item_id.startswith("source-file:")
        }
        result = _append_source_file_items(
            packet,
            request.scope,
            snapshot,
            files,
            currentness=currentness,
        )
        result = _append_source_items(
            result,
            request.scope,
            snapshot,
            (*modules, *declarations),
            (),
            {item.relative_path: item for item in modules},
            currentness=currentness,
        )
        requested_symbol_count = len(modules) + len(declarations)
        rendered_symbol_count = sum(
            1
            for item in result.structural_items
            if item.item_id.startswith("source:") and item.item_id not in prior_source_item_ids
        )
        rendered_file_count = sum(
            1
            for item in result.structural_items
            if item.item_id.startswith("source-file:") and item.item_id not in prior_file_item_ids
        )
        if rendered_symbol_count < requested_symbol_count or rendered_file_count < len(files):
            return _with_omission(
                result,
                "source-overview-items",
                OmissionReason.TOKEN_BUDGET,
                "source overview inventory exceeds remaining structural budget",
            )
        return result

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
        exact_file = _exact_scoped_source_file(
            self._source, project_scope, snapshot.snapshot_id, request.source_query
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
            if edge.kind
            in {CodeEdgeKind.IMPORTS, CodeEdgeKind.CALLS, CodeEdgeKind.PACKAGE_DEPENDENCY}
        )
        resolved_symbols = self._source.symbols_by_ids(
            project_scope,
            snapshot.snapshot_id,
            tuple(edge.target_symbol_id for edge in edges if edge.target_symbol_id is not None),
        )
        selected_symbols = tuple(
            {symbol.symbol_id: symbol for symbol in (*symbols, *resolved_symbols)}.values()
        )
        result = packet
        if exact_file is not None:
            result = _append_source_file_items(
                result,
                request.scope,
                snapshot,
                (exact_file,),
                currentness=ValidityState.UNKNOWN,
            )
        return _append_source_items(
            result, request.scope, snapshot, selected_symbols, edges, module_symbols
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
        if query.relative_path is not None:
            candidates = tuple(
                item
                for item in self._source.find_symbols(
                    scope, snapshot.snapshot_id, query.relative_path, limit=256
                )
                if item.relative_path == query.relative_path
            )
        else:
            assert query.symbol is not None
            candidates = self._source.find_symbols(
                scope, snapshot.snapshot_id, query.symbol, limit=64
            )
            candidates = (
                tuple(
                    item
                    for item in candidates
                    if item.qualified_name == query.symbol or item.relative_path == query.symbol
                )
                or candidates
            )
        if not candidates:
            return _with_omission(
                packet, "source-impact", OmissionReason.LOWER_RANK, "source symbol was not found"
            )
        starts = tuple(
            sorted(
                candidates,
                key=lambda item: (
                    item.relative_path,
                    item.line,
                    item.qualified_name,
                    str(item.symbol_id),
                ),
            )
        )
        starts_truncated = len(starts) > query.maximum_symbols
        starts = starts[: query.maximum_symbols]
        visited = {item.symbol_id for item in starts}
        selected = {item.symbol_id: item for item in starts}
        depths = {item.symbol_id: 0 for item in starts}
        frontier = tuple(visited)
        edges: list[CodeEdge] = []
        depth = 0
        truncated: str | None = "maximum symbol count reached" if starts_truncated else None
        while frontier and truncated is None and (query.transitive or depth == 0):
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
            # Do not expose a relationship whose dependent/dependency symbol cannot fit in the
            # same bounded result. Besides making the limit truthful, this keeps edge rendering
            # from referring to an omitted source symbol.
            edges.extend(boundary)
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
        renamed_files,
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
        renamed_files = tuple(
            item
            for item in renamed_files
            if path in {item.before.relative_path, item.after.relative_path}
        )
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
            renamed_files,
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
    renamed_file_paths = tuple(
        f"{item.before.relative_path} → {item.after.relative_path}" for item in renamed_files
    )[: query.maximum_files]
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
        + len(renamed_files)
        + len(modified_files)
        - len(added_file_paths)
        - len(removed_file_paths)
        - len(renamed_file_paths)
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
            "renamed_files": renamed_file_paths,
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
_KNOWLEDGE_EVIDENCE_NAMESPACE = UUID("6b2c7437-752e-4d43-8b19-5156e50120fb")


def _exact_scoped_source_file(
    repository: SourceStructureRepository,
    scope: MemoryScope,
    snapshot_id: CodeSnapshotId,
    query: str,
) -> CodeFile | None:
    """Resolve a query as one exact safe relative file, without broad path matching.

    Free-text symbol matching remains available to ``source_query``. This narrow additional path
    treats an exact canonical relative identity as a file request, which makes file-only snapshot
    evidence (for example a dependency manifest) retrievable without inventing symbols.
    """
    try:
        _validate_source_relative_path(query)
    except ValueError:
        return None
    return repository.get_file(scope, snapshot_id, query)


def _validate_source_relative_path(value: str) -> None:
    """Accept only a canonical, bounded, repository-relative POSIX identity."""
    if not value or len(value) > 512 or "\\" in value:
        raise ValueError("source change path must be a bounded relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value != path.as_posix():
        raise ValueError("source change path must be a canonical relative path")


def _checkpoint_relevant_source_paths(content: str) -> tuple[str, ...]:
    """Read canonical checkpoint file hints defensively, without surfacing malformed payloads."""
    try:
        value = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return ()
    raw_paths = value.get("relevant_files") if isinstance(value, dict) else None
    if not isinstance(raw_paths, list):
        return ()
    result: list[str] = []
    for path in raw_paths:
        if not isinstance(path, str) or path in result:
            continue
        try:
            _validate_source_relative_path(path)
        except ValueError:
            continue
        result.append(path)
    return tuple(result)


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


def _procedure_context_item(
    task_scope: MemoryScope,
    procedure: ProjectProcedure,
    rank: int,
    profile: ProjectClientProfile | None,
) -> tuple[ContextItem, ProvenanceNotice]:
    """Render one exact procedure revision without granting Markdown execution authority."""
    revision, document = procedure.revision, procedure.revision.document
    item_id = f"procedure:{document.document_id}:revision:{revision.revision_id}"
    content = json.dumps(
        {
            "document_path": document.relative_path,
            "mandatory": procedure.mandatory,
            "procedure_tags": procedure.tags,
            "revision_id": str(revision.revision_id),
            "selected_by_profile": None
            if profile is None
            else {
                "client": profile.client,
                "document_path": profile.revision.document.relative_path,
                "revision_id": str(profile.revision.revision_id),
            },
            "sections": tuple(
                {"heading": section.heading, "content": section.content}
                for section in document.sections
            ),
            "title": document.title,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    reference = f"procedure:{document.document_id}/revision/{revision.revision_id}"
    evidence = EvidenceReference(
        EvidenceId(uuid5(_KNOWLEDGE_EVIDENCE_NAMESPACE, f"evidence:{reference}")),
        SourceId(uuid5(_KNOWLEDGE_EVIDENCE_NAMESPACE, f"source:{document.document_id}")),
        EvidenceSourceType.REPOSITORY,
        SourceTrustClass.USER_AUTHORED,
        reference,
        document.content_digest,
        EvidenceLocation(f"mnemo:{reference}"),
        revision.created_at,
        VerificationStatus.UNVERIFIED,
    )
    evidence_references = (evidence,) if profile is None else (evidence, _profile_evidence(profile))
    item = ContextItem(
        item_id,
        ContextItemType.MANDATORY_PROCEDURE if procedure.mandatory else ContextItemType.SKILL,
        task_scope,
        content,
        ContentRepresentation.UNTRUSTED_EVIDENCE,
        (len(content) + 3) // 4,
        evidence_references,
        SourceTrustClass.USER_AUTHORED,
        Sensitivity.NORMAL,
        ValidityState.UNKNOWN,
        RankingMetadata(rank, float(len(procedure.tags)), "scoped-procedure-tags"),
        ConflictState.NONE,
        revision.created_at,
    )
    return (
        item,
        ProvenanceNotice(
            f"provenance:{item_id}",
            item_id,
            f"mnemo:procedure/{document.document_id}/revision/{revision.revision_id}",
            document.content_digest.removeprefix("sha256:"),
            evidence_references,
        ),
    )


def _profile_evidence(profile: ProjectClientProfile) -> EvidenceReference:
    revision, document = profile.revision, profile.revision.document
    reference = f"agent-profile:{document.document_id}/revision/{revision.revision_id}"
    return EvidenceReference(
        EvidenceId(uuid5(_KNOWLEDGE_EVIDENCE_NAMESPACE, f"evidence:{reference}")),
        SourceId(uuid5(_KNOWLEDGE_EVIDENCE_NAMESPACE, f"source:{document.document_id}")),
        EvidenceSourceType.REPOSITORY,
        SourceTrustClass.USER_AUTHORED,
        reference,
        document.content_digest,
        EvidenceLocation(f"mnemo:{reference}"),
        revision.created_at,
        VerificationStatus.UNVERIFIED,
    )


def _knowledge_context_item(
    packet: ContextPacket,
    task_scope: MemoryScope,
    match: KnowledgeDocumentSectionMatch,
    rank: int,
) -> tuple[ContextItem, ProvenanceNotice]:
    """Render one exact stored note section without private absolute paths or implicit authority."""
    revision, document = match.revision, match.revision.document
    item_id = (
        f"knowledge:{document.document_id}:revision:{revision.revision_id}:"
        f"section:{match.section_index}"
    )
    content = json.dumps(
        {
            "content": match.section.content,
            "currentness": "unknown",
            "document_path": document.relative_path,
            "heading": match.section.heading,
            "revision_id": str(revision.revision_id),
            "section_index": match.section_index,
            "source_kind": document.source_kind.value,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    reference = (
        f"knowledge:{document.document_id}/revision/{revision.revision_id}/"
        f"section/{match.section_index}"
    )
    evidence = EvidenceReference(
        EvidenceId(uuid5(_KNOWLEDGE_EVIDENCE_NAMESPACE, f"evidence:{reference}")),
        SourceId(uuid5(_KNOWLEDGE_EVIDENCE_NAMESPACE, f"source:{document.document_id}")),
        EvidenceSourceType.USER_DOCUMENT,
        SourceTrustClass.USER_AUTHORED,
        reference,
        document.content_digest,
        EvidenceLocation(f"mnemo:{reference}"),
        revision.created_at,
        VerificationStatus.UNVERIFIED,
    )
    item = ContextItem(
        item_id,
        ContextItemType.KNOWLEDGE,
        task_scope,
        content,
        ContentRepresentation.UNTRUSTED_EVIDENCE,
        (len(content) + 3) // 4,
        (evidence,),
        SourceTrustClass.USER_AUTHORED,
        Sensitivity.NORMAL,
        ValidityState.UNKNOWN,
        RankingMetadata(rank, float(match.score), "scoped-literal-knowledge"),
        ConflictState.NONE,
        revision.created_at,
    )
    return (
        item,
        ProvenanceNotice(
            f"provenance:{item_id}",
            item_id,
            f"mnemo:knowledge/{document.document_id}/revision/{revision.revision_id}",
            document.content_digest.removeprefix("sha256:"),
            (evidence,),
        ),
    )


def _knowledge_conflict_target(match: KnowledgeDocumentSectionMatch) -> str | None:
    """Read one explicit, safe user-authored conflict declaration without interpreting prose.

    The declaration is intentionally narrow: a note can say ``mnemo_conflicts_with:
    docs/other-note.md``.  Mnemo does not infer disagreement from note text.  It simply preserves
    the two cited current revisions as unresolved evidence when both are in the same scope.
    """
    value = next(
        (
            frontmatter_value
            for key, frontmatter_value in match.revision.document.frontmatter
            if key == "mnemo_conflicts_with"
        ),
        None,
    )
    if (
        value is None
        or not value
        or len(value) > 512
        or value.startswith("/")
        or "\\" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        return None
    return value


def _checkpoint_file_knowledge_query(packet: ContextPacket) -> str | None:
    """Derive a tiny literal note query from explicit checkpoint file identities only.

    This never inspects a user's current prompt, source bodies, note bodies, or environment.  A
    checkpoint's relevant-files field is durable task evidence already authorized for this packet;
    extracting literal file stems only selects same-scope untrusted note candidates.
    """
    checkpoint = packet.active_task_checkpoint
    if checkpoint is None:
        return None
    try:
        content = json.loads(checkpoint.content)
    except (TypeError, ValueError):
        return None
    files = content.get("relevant_files") if isinstance(content, dict) else None
    if not isinstance(files, list):
        return None
    terms: list[str] = []
    for value in files:
        if not isinstance(value, str) or len(value) > 512 or value.startswith("/"):
            continue
        path = PurePosixPath(value)
        if any(part in {"", ".", ".."} for part in path.parts):
            continue
        name = path.name.rsplit(".", maxsplit=1)[0]
        for term in normalize_knowledge_query(name):
            if term not in terms:
                terms.append(term)
            if len(terms) == 12:
                break
        if len(terms) == 12:
            break
    return " ".join(terms) or None


def _source_snapshot_difference(
    repository: SourceStructureRepository,
    scope: MemoryScope,
    before: CodeSnapshot,
    after: CodeSnapshot,
) -> tuple[
    bool,
    tuple[CodeFile, ...],
    tuple[CodeFile, ...],
    tuple[SourceFileRename, ...],
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
    added_paths = after_files.keys() - before_files.keys()
    removed_paths = before_files.keys() - after_files.keys()
    renamed_files = (
        unique_file_renames(
            tuple(after_files[key] for key in sorted(added_paths)),
            tuple(before_files[key] for key in sorted(removed_paths)),
        )
        if files_available
        else ()
    )
    renamed_after_paths = {item.after.relative_path for item in renamed_files}
    renamed_before_paths = {item.before.relative_path for item in renamed_files}
    return (
        files_available,
        (
            tuple(after_files[key] for key in sorted(added_paths - renamed_after_paths))
            if files_available
            else ()
        ),
        (
            tuple(before_files[key] for key in sorted(removed_paths - renamed_before_paths))
            if files_available
            else ()
        ),
        renamed_files,
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


def _with_source_overview_summary(
    packet: ContextPacket,
    task_scope: MemoryScope,
    snapshot: CodeSnapshot,
    currentness: ValidityState,
    selected_file_count: int,
    selected_module_count: int,
    selected_declaration_count: int,
    omitted_file_count: int,
    omitted_module_count: int,
    omitted_declaration_count: int,
) -> ContextPacket:
    """Attach a bounded snapshot-level inventory before individual declaration facts.

    The summary deliberately carries only counts and immutable snapshot identity.  It gives an
    automatic session a reliable answer to "what has been indexed?" without retaining source
    text, a project root, or an inferred runtime relationship.
    """
    content = json.dumps(
        {
            "currentness": currentness.value,
            "edge_count": snapshot.edge_count,
            "file_count": snapshot.file_count,
            "kind": "source_snapshot_overview",
            "omitted_declaration_count": omitted_declaration_count,
            "omitted_file_count": omitted_file_count,
            "omitted_module_count": omitted_module_count,
            "selected_declaration_count": selected_declaration_count,
            "selected_file_count": selected_file_count,
            "selected_module_count": selected_module_count,
            "snapshot_id": str(snapshot.snapshot_id),
            "symbol_count": snapshot.symbol_count,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    tokens = (len(content) + 3) // 4
    remaining = min(
        packet.budget.structural - sum(item.token_estimate for item in packet.structural_items),
        packet.budget.total_limit - packet.declared_total_tokens,
    )
    if tokens > remaining:
        return _with_omission(
            packet,
            "source-overview",
            OmissionReason.TOKEN_BUDGET,
            "source overview summary exceeds remaining structural budget",
        )
    evidence = _source_snapshot_evidence(packet, snapshot)
    item = ContextItem(
        f"source-overview:{snapshot.snapshot_id}",
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
    notice = ProvenanceNotice(
        f"provenance:{item.item_id}",
        item.item_id,
        f"mnemo:source/{snapshot.snapshot_id}/overview",
        hashlib.sha256(content.encode()).hexdigest(),
        (evidence,),
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


def _append_source_file_items(
    packet: ContextPacket,
    task_scope: MemoryScope,
    snapshot: CodeSnapshot,
    files: tuple[CodeFile, ...],
    *,
    currentness: ValidityState,
) -> ContextPacket:
    """Render bounded source-file identities without exposing their bytes.

    File facts keep the automatic overview useful for file-only inputs such as dbt SQL and
    unparsed languages. They deliberately establish no declaration, import, or runtime edge.
    """
    facts: list[ContextItem] = []
    notices: list[ProvenanceNotice] = list(packet.provenance)
    remaining = min(
        packet.budget.structural - sum(item.token_estimate for item in packet.structural_items),
        packet.budget.total_limit - packet.declared_total_tokens,
    )
    for file in files:
        content = json.dumps(
            {
                "currentness": currentness.value,
                "kind": "source_file",
                "path": file.relative_path,
                "snapshot_id": str(snapshot.snapshot_id),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        tokens = (len(content) + 3) // 4
        if tokens > remaining:
            break
        reference = f"source:{snapshot.source_digest}#{file.relative_path}"
        evidence = EvidenceReference(
            EvidenceId(uuid5(_SOURCE_EVIDENCE_NAMESPACE, f"evidence:{reference}")),
            SourceId(uuid5(_SOURCE_EVIDENCE_NAMESPACE, f"source:{snapshot.source_digest}")),
            EvidenceSourceType.REPOSITORY,
            SourceTrustClass.CURRENT_STRUCTURAL,
            reference,
            file.content_digest,
            EvidenceLocation(f"mnemo:source/{snapshot.snapshot_id}/{file.relative_path}"),
            packet.created_at,
            VerificationStatus.VERIFIED,
        )
        item = ContextItem(
            f"source-file:{snapshot.snapshot_id}:{file.relative_path}",
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
        facts.append(item)
        notices.append(
            ProvenanceNotice(
                f"provenance:{item.item_id}",
                item.item_id,
                f"mnemo:source/{snapshot.snapshot_id}/file/{file.relative_path}",
                hashlib.sha256(content.encode()).hexdigest(),
                (evidence,),
            )
        )
        remaining -= tokens
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
        omissions=packet.omissions,
        conflicts=packet.conflicts,
    )


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
    existing_item_ids = {item.item_id for item in packet.structural_items}
    remaining = min(
        packet.budget.structural - sum(item.token_estimate for item in packet.structural_items),
        packet.budget.total_limit - packet.declared_total_tokens,
    )
    notices: list[ProvenanceNotice] = list(packet.provenance)
    for symbol in symbols:
        item_id = f"source:{symbol.symbol_id}"
        if item_id in existing_item_ids:
            continue
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
            item_id,
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
        existing_item_ids.add(item_id)
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
        item_id = f"source-edge:{snapshot.snapshot_id}:{edge.source_symbol_id}:{edge.target}"
        if item_id in existing_item_ids:
            continue
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
            item_id,
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
        existing_item_ids.add(item_id)
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
