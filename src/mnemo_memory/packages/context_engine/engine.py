"""Deterministic, authorization-first assembly around the canonical context packet."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from mnemo_memory.packages.application.unified_context import GetUnifiedContext
from mnemo_memory.packages.domain import (
    ActiveEpisodicMemory,
    ConflictState,
    ContentRepresentation,
    ContextItem,
    ContextItemType,
    ContextPacket,
    EpisodicMemoryKind,
    MemoryScope,
    OmissionNotice,
    OmissionReason,
    ProvenanceNotice,
    RankingMetadata,
    ScopeLevel,
    SourceTrustClass,
    ValidityState,
)
from mnemo_memory.packages.storage import (
    ActiveEpisodicMemoryPage,
    EpisodicMemoryReviewRepositoryError,
)

from .selection import finalize_context_packet

_WORD = re.compile(r"[a-z0-9_./:-]+")
_QUESTION_TERMS = frozenset(
    {
        "a",
        "about",
        "an",
        "do",
        "does",
        "find",
        "for",
        "how",
        "in",
        "is",
        "me",
        "of",
        "please",
        "show",
        "the",
        "to",
        "used",
        "using",
        "what",
        "where",
        "which",
    }
)


class RetrievalCategory(StrEnum):
    CHECKPOINT = "checkpoint"
    EPISODIC = "episodic"
    KNOWLEDGE = "knowledge"
    STRUCTURAL = "structural"
    PROCEDURAL = "procedural"


class QueryIntent(StrEnum):
    TASK_RESUMPTION = "task_resumption"
    PRIOR_WORK = "prior_work"
    PERSONAL_KNOWLEDGE = "personal_knowledge"
    PROJECT_STRUCTURE = "project_structure"
    PROCEDURE = "procedure"
    GENERAL = "general"


@dataclass(frozen=True, slots=True)
class RetrievalPlan:
    """Inspectible deterministic plan; query text is transient and deliberately excluded."""

    intents: tuple[QueryIntent, ...]
    categories: tuple[RetrievalCategory, ...]
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "intents": [item.value for item in self.intents],
            "categories": [item.value for item in self.categories],
            "reasons": list(self.reasons),
        }


class DeterministicContextPlanner:
    """Classify bounded input without a model call or authority-bearing inference."""

    _EPISODIC_TERMS = frozenset(
        {"before", "decision", "failed", "failure", "lesson", "outcome", "previous", "resume"}
    )
    _KNOWLEDGE_TERMS = frozenset(
        {"adr", "document", "knowledge", "meeting", "note", "notes", "obsidian"}
    )
    _STRUCTURAL_TERMS = frozenset(
        {
            "code",
            "class",
            "dbt",
            "defined",
            "dependency",
            "downstream",
            "file",
            "function",
            "impact",
            "implementation",
            "implemented",
            "lineage",
            "located",
            "model",
            "module",
            "schema",
            "source",
            "symbol",
            "upstream",
        }
    )
    _PROCEDURAL_TERMS = frozenset(
        {"agent", "instruction", "procedure", "rule", "skill", "workflow"}
    )

    def plan(self, request: GetUnifiedContext) -> RetrievalPlan:
        intents = {QueryIntent.TASK_RESUMPTION}
        categories = {RetrievalCategory.CHECKPOINT}
        reasons = ["checkpoint context is the deterministic task-resumption baseline"]
        if request.scope.level is ScopeLevel.TASK:
            categories.add(RetrievalCategory.EPISODIC)
            reasons.append("complete task scope permits authorization-first episodic retrieval")

        if request.knowledge_query is not None or request.semantic_knowledge_query is not None:
            intents.add(QueryIntent.PERSONAL_KNOWLEDGE)
            categories.add(RetrievalCategory.KNOWLEDGE)
            reasons.append("an explicit knowledge query selected the knowledge index")
        if (
            request.lineage is not None
            or request.dbt_test_coverage is not None
            or request.dbt_selector is not None
            or request.dbt_freshness is not None
            or request.dbt_changes is not None
            or request.source_query is not None
            or request.source_impact is not None
            or request.source_changes is not None
            or request.source_overview is not None
            or request.checkpoint_source_impact is not None
        ):
            intents.add(QueryIntent.PROJECT_STRUCTURE)
            categories.add(RetrievalCategory.STRUCTURAL)
            reasons.append("an explicit structural query selected the project index")
        if request.procedure_tags:
            intents.add(QueryIntent.PROCEDURE)
            categories.add(RetrievalCategory.PROCEDURAL)
            reasons.append("explicit procedure tags selected the checked-in registry")

        terms = _query_terms(request.query)
        if terms:
            matched = False
            for vocabulary, intent, category, reason in (
                (
                    self._EPISODIC_TERMS,
                    QueryIntent.PRIOR_WORK,
                    RetrievalCategory.EPISODIC,
                    "literal prior-work terms selected episodic memory",
                ),
                (
                    self._KNOWLEDGE_TERMS,
                    QueryIntent.PERSONAL_KNOWLEDGE,
                    RetrievalCategory.KNOWLEDGE,
                    "literal document terms selected personal knowledge",
                ),
                (
                    self._STRUCTURAL_TERMS,
                    QueryIntent.PROJECT_STRUCTURE,
                    RetrievalCategory.STRUCTURAL,
                    "literal code or data terms selected project structure",
                ),
                (
                    self._PROCEDURAL_TERMS,
                    QueryIntent.PROCEDURE,
                    RetrievalCategory.PROCEDURAL,
                    "literal workflow terms selected procedural memory",
                ),
            ):
                if terms & vocabulary:
                    intents.add(intent)
                    categories.add(category)
                    reasons.append(reason)
                    matched = True
            if not matched:
                intents.add(QueryIntent.GENERAL)
                categories.update({RetrievalCategory.KNOWLEDGE, RetrievalCategory.STRUCTURAL})
                reasons.append("no specialized literal intent matched the bounded query")
                reasons.append(
                    "general queries select bounded lexical knowledge and source candidates"
                )

        return RetrievalPlan(
            tuple(sorted(intents, key=lambda item: item.value)),
            tuple(sorted(categories, key=lambda item: item.value)),
            tuple(dict.fromkeys(reasons)),
        )


class ContextPacketAssemblerPort(Protocol):
    def get_context(self, request: GetUnifiedContext) -> ContextPacket: ...


class ActiveEpisodicMemoryRepositoryPort(Protocol):
    def list_active_episodic_memories(
        self, scope: MemoryScope, *, offset: int = 0, limit: int = 50
    ) -> ActiveEpisodicMemoryPage: ...


class UnifiedContextEngine:
    """Add production episodic retrieval to the existing canonical packet assembler."""

    def __init__(
        self,
        assembler: ContextPacketAssemblerPort,
        episodic_memories: ActiveEpisodicMemoryRepositoryPort,
        planner: DeterministicContextPlanner | None = None,
    ) -> None:
        self._assembler = assembler
        self._episodic_memories = episodic_memories
        self._planner = planner or DeterministicContextPlanner()

    def plan(self, request: GetUnifiedContext) -> RetrievalPlan:
        return self._planner.plan(request)

    def get_context(self, request: GetUnifiedContext) -> ContextPacket:
        plan = self.plan(request)
        packet = self._assembler.get_context(_planned_request(request, plan))
        if RetrievalCategory.EPISODIC not in plan.categories:
            return finalize_context_packet(packet)
        try:
            page = self._episodic_memories.list_active_episodic_memories(request.scope, limit=50)
        except EpisodicMemoryReviewRepositoryError:
            return finalize_context_packet(
                _with_omission(
                    packet,
                    "episodic-memory",
                    OmissionReason.LOWER_RANK,
                    "episodic memory storage is unavailable",
                )
            )

        ranked = sorted(
            page.items,
            key=lambda item: (
                -_episodic_score(item, request.query, packet.created_at),
                -item.activated_at.timestamp(),
                str(item.memory_id),
            ),
        )
        remaining = min(
            request.budget.episodic_memories
            - sum(item.token_estimate for item in packet.episodic_memories),
            request.budget.total_limit - packet.declared_total_tokens,
        )
        items: list[ContextItem] = []
        provenance = list(packet.provenance)
        omissions = list(packet.omissions)
        for rank, memory in enumerate(ranked, start=1):
            item = _episodic_context_item(memory, request.query, packet.created_at, rank)
            if item.token_estimate > remaining:
                omissions.append(
                    OmissionNotice(
                        item.item_id,
                        OmissionReason.TOKEN_BUDGET,
                        "episodic memory exceeds the remaining episodic or total budget",
                    )
                )
                continue
            items.append(item)
            provenance.append(_episodic_provenance(item, memory))
            remaining -= item.token_estimate
        if page.next_offset is not None:
            omissions.append(
                OmissionNotice(
                    "episodic-memory:remaining",
                    OmissionReason.LOWER_RANK,
                    "authorization-first episodic candidate retrieval reached its 50-item bound",
                )
            )
        if not items and tuple(omissions) == packet.omissions:
            return finalize_context_packet(packet)
        return finalize_context_packet(
            ContextPacket(
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
                episodic_memories=(*packet.episodic_memories, *items),
                knowledge_items=packet.knowledge_items,
                structural_items=packet.structural_items,
                skills_and_procedures=packet.skills_and_procedures,
                provenance=tuple(provenance),
                conflicts=packet.conflicts,
                omissions=tuple(omissions),
            )
        )


def _query_terms(query: str | None) -> frozenset[str]:
    if query is None:
        return frozenset()
    return frozenset(_WORD.findall(query.casefold()))


def _planned_request(request: GetUnifiedContext, plan: RetrievalPlan) -> GetUnifiedContext:
    query = request.query
    if query is None:
        return request
    knowledge_query = request.knowledge_query
    source_query = request.source_query
    if (
        RetrievalCategory.KNOWLEDGE in plan.categories
        and knowledge_query is None
        and not request.include_checkpoint_file_knowledge
    ):
        knowledge_query = query
    if RetrievalCategory.STRUCTURAL in plan.categories and not _has_structural_query(request):
        source_query = _source_identity_query(query)
    if knowledge_query == request.knowledge_query and source_query == request.source_query:
        return request
    return replace(request, knowledge_query=knowledge_query, source_query=source_query)


def _has_structural_query(request: GetUnifiedContext) -> bool:
    return any(
        value is not None
        for value in (
            request.lineage,
            request.dbt_test_coverage,
            request.dbt_selector,
            request.dbt_freshness,
            request.dbt_changes,
            request.source_query,
            request.source_impact,
            request.source_changes,
            request.source_overview,
            request.checkpoint_source_impact,
        )
    )


def _source_identity_query(query: str) -> str:
    generic = (
        _QUESTION_TERMS
        | DeterministicContextPlanner._STRUCTURAL_TERMS
        | DeterministicContextPlanner._EPISODIC_TERMS
        | DeterministicContextPlanner._KNOWLEDGE_TERMS
        | DeterministicContextPlanner._PROCEDURAL_TERMS
    )
    retained = tuple(term for term in _WORD.findall(query.casefold()) if term not in generic)[:8]
    return " ".join(retained) if retained else query


def _episodic_score(memory: ActiveEpisodicMemory, query: str | None, now: datetime) -> float:
    query_terms = _query_terms(query)
    claim_terms = _query_terms(memory.memory.claim)
    lexical = 0.0 if not query_terms else len(query_terms & claim_terms) / len(query_terms)
    observed_at = max(
        (memory.activated_at, *(item.observed_at for item in memory.memory.evidence_references))
    )
    age_days = max(0.0, (now - observed_at).total_seconds()) / 86_400
    recency = 1.0 / (1.0 + age_days / 30.0)
    kind_priority = {
        EpisodicMemoryKind.LESSON: 1.0,
        EpisodicMemoryKind.DECISION: 0.95,
        EpisodicMemoryKind.FAILURE: 0.9,
        EpisodicMemoryKind.OUTCOME: 0.8,
        EpisodicMemoryKind.PREFERENCE: 0.7,
    }[memory.kind]
    if query_terms:
        score = 0.45 * lexical + 0.25 * memory.confidence + 0.2 * recency + 0.1 * kind_priority
    else:
        score = 0.55 * memory.confidence + 0.3 * recency + 0.15 * kind_priority
    return round(score, 6)


def _episodic_context_item(
    memory: ActiveEpisodicMemory, query: str | None, now: datetime, rank: int
) -> ContextItem:
    content = json.dumps(
        {
            "activated_at": memory.activated_at.isoformat(),
            "claim": memory.memory.claim,
            "confidence": memory.confidence,
            "kind": memory.kind.value,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return ContextItem(
        item_id=f"episodic-memory:{memory.memory_id}",
        item_type=ContextItemType.EPISODIC_MEMORY,
        source_scope=memory.scope,
        content=content,
        content_representation=ContentRepresentation.UNTRUSTED_EVIDENCE,
        token_estimate=(len(content) + 3) // 4,
        evidence_references=memory.memory.evidence_references,
        source_trust=_strongest_trust(memory),
        sensitivity=memory.memory.classification.sensitivity,
        validity=ValidityState.CURRENT,
        ranking=RankingMetadata(
            rank,
            _episodic_score(memory, query, now),
            "episodic-lexical+temporal+confidence+kind-priority/v1",
        ),
        conflict_state=ConflictState.NONE,
        observed_at=max(
            (memory.activated_at, *(item.observed_at for item in memory.memory.evidence_references))
        ),
    )


def _strongest_trust(memory: ActiveEpisodicMemory) -> SourceTrustClass:
    order = {
        SourceTrustClass.CURRENT_STRUCTURAL: 0,
        SourceTrustClass.VERIFIED_TOOL_RESULT: 1,
        SourceTrustClass.USER_CORRECTION: 2,
        SourceTrustClass.USER_AUTHORED: 3,
        SourceTrustClass.APPROVED_CHECKPOINT: 4,
        SourceTrustClass.EXTERNAL: 5,
        SourceTrustClass.ASSISTANT_INFERENCE: 6,
    }
    return min(
        (item.trust_class for item in memory.memory.evidence_references),
        key=order.__getitem__,
    )


def _episodic_provenance(item: ContextItem, memory: ActiveEpisodicMemory) -> ProvenanceNotice:
    return ProvenanceNotice(
        f"provenance:{item.item_id}",
        item.item_id,
        f"mnemo:episodic/{memory.memory_id}/candidate/{memory.candidate_id}",
        "sha256:" + hashlib.sha256(item.content.encode("utf-8")).hexdigest(),
        item.evidence_references,
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
        conflicts=packet.conflicts,
        omissions=(*packet.omissions, OmissionNotice(item_id, reason, detail)),
    )
