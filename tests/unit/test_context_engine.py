"""Deterministic planning and authorization-first episodic retrieval for Issue 17A."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from mnemo_memory.packages.application.unified_context import (
    ContextDbtSelectorQuery,
    GetUnifiedContext,
)
from mnemo_memory.packages.context_engine import (
    DeterministicContextPlanner,
    QueryIntent,
    RetrievalCategory,
    UnifiedContextEngine,
    explain_context_packet,
    finalize_context_packet,
    render_context_packet,
)
from mnemo_memory.packages.domain import (
    ActiveEpisodicMemory,
    ConflictNotice,
    ConflictState,
    ContextBudget,
    ContextItemType,
    ContextPacket,
    DurableClaim,
    EpisodicMemoryKind,
    EventId,
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    MemoryClassification,
    MemoryId,
    MemoryScope,
    MemoryStatus,
    OmissionNotice,
    OmissionReason,
    OwnerId,
    PacketSchemaVersion,
    ProjectId,
    RequestId,
    RetentionPolicyId,
    RetentionSchedule,
    ScopeLevel,
    Sensitivity,
    SessionId,
    SourceId,
    SourceTrustClass,
    TaskId,
    ValidityState,
    VerificationStatus,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.storage import (
    ActiveEpisodicMemoryPage,
    EpisodicMemoryReviewStorageFailure,
)

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


def _scope(seed: int = 1, *, level: ScopeLevel = ScopeLevel.TASK) -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string(f"10000000-0000-4000-8000-{seed:012d}"),
        level,
        Visibility.PROJECT,
        WorkspaceId.from_string(f"20000000-0000-4000-8000-{seed:012d}"),
        ProjectId.from_string(f"30000000-0000-4000-8000-{seed:012d}"),
        session_id=(
            SessionId.from_string(f"40000000-0000-4000-8000-{seed:012d}")
            if level is ScopeLevel.TASK
            else None
        ),
        task_id=(
            TaskId.from_string(f"50000000-0000-4000-8000-{seed:012d}")
            if level is ScopeLevel.TASK
            else None
        ),
    )


def _evidence(seed: int, observed_at: datetime) -> EvidenceReference:
    return EvidenceReference(
        EvidenceId.from_string(f"60000000-0000-4000-8000-{seed:012d}"),
        SourceId.from_string(f"70000000-0000-4000-8000-{seed:012d}"),
        EvidenceSourceType.CHECKPOINT,
        SourceTrustClass.APPROVED_CHECKPOINT,
        f"fixture://context-engine/{seed}",
        "sha256:" + f"{seed:064x}",
        EvidenceLocation(f"fixture://context-engine/{seed}"),
        observed_at,
        VerificationStatus.VERIFIED,
    )


def _memory(
    scope: MemoryScope,
    seed: int,
    claim: str,
    *,
    kind: EpisodicMemoryKind,
    confidence: float,
    activated_at: datetime,
) -> ActiveEpisodicMemory:
    memory_id = MemoryId.from_string(f"80000000-0000-4000-8000-{seed:012d}")
    evidence = _evidence(seed, activated_at)
    claim_value = DurableClaim(
        memory_id,
        scope,
        MemoryClassification(Sensitivity.NORMAL, MemoryStatus.ACTIVE),
        RetentionSchedule(
            RetentionPolicyId.from_string(f"90000000-0000-4000-8000-{seed:012d}"),
            True,
            activated_at,
            activated_at,
            activated_at,
            None,
            None,
        ),
        claim,
        (evidence,),
    )
    return ActiveEpisodicMemory(
        claim_value,
        kind,
        memory_id,
        EventId.from_string(f"a0000000-0000-4000-8000-{seed:012d}"),
        confidence,
        "extractor-v1",
        "provider-fixture",
        "model-fixture",
        "prompt-v1",
        EventId.from_string(f"b0000000-0000-4000-8000-{seed:012d}"),
        activated_at,
    )


class EmptyAssembler:
    def __init__(self) -> None:
        self.requests: list[GetUnifiedContext] = []

    def get_context(self, request: GetUnifiedContext) -> ContextPacket:
        self.requests.append(request)
        assert request.scope.task_id is not None
        return ContextPacket(
            PacketSchemaVersion.V1,
            RequestId.from_string("c0000000-0000-4000-8000-000000000001"),
            request.scope,
            None,
            request.scope.task_id,
            NOW,
            None,
            0,
            request.budget,
            "mnemo-test/1",
        )


class ScopedMemoryRepository:
    def __init__(
        self,
        scope: MemoryScope,
        items: tuple[ActiveEpisodicMemory, ...],
        *,
        fail: bool = False,
        truncated: bool = False,
    ) -> None:
        self.scope = scope
        self.items = items
        self.fail = fail
        self.truncated = truncated
        self.calls: list[tuple[MemoryScope, int, int]] = []

    def list_active_episodic_memories(
        self, scope: MemoryScope, *, offset: int = 0, limit: int = 50
    ) -> ActiveEpisodicMemoryPage:
        self.calls.append((scope, offset, limit))
        if self.fail:
            raise EpisodicMemoryReviewStorageFailure("fixture payload must not escape")
        visible = self.items if scope == self.scope else ()
        return ActiveEpisodicMemoryPage(visible, 50 if self.truncated else None)


def test_planner_is_deterministic_explicit_and_does_not_need_a_model() -> None:
    request = GetUnifiedContext(
        _scope(),
        query="Resume the dbt lineage decision using the project workflow notes",
        knowledge_query="project notes",
        procedure_tags=("review",),
    )
    planner = DeterministicContextPlanner()

    first = planner.plan(request)

    assert first == planner.plan(request)
    assert set(first.intents) == {
        QueryIntent.PERSONAL_KNOWLEDGE,
        QueryIntent.PRIOR_WORK,
        QueryIntent.PROCEDURE,
        QueryIntent.PROJECT_STRUCTURE,
        QueryIntent.TASK_RESUMPTION,
    }
    assert set(first.categories) == set(RetrievalCategory)
    assert "Resume" not in str(first.to_dict())
    assert GetUnifiedContext(_scope(level=ScopeLevel.PROJECT)).query is None
    assert (
        RetrievalCategory.EPISODIC
        not in planner.plan(GetUnifiedContext(_scope(level=ScopeLevel.PROJECT))).categories
    )
    with pytest.raises(ValueError, match="between 1 and 512"):
        GetUnifiedContext(_scope(), query="   ")


def test_general_query_routes_only_to_existing_authorized_lexical_categories() -> None:
    scope = _scope()
    assembler = EmptyAssembler()
    engine = UnifiedContextEngine(assembler, ScopedMemoryRepository(scope, ()))

    engine.get_context(GetUnifiedContext(scope, query="oauth callback"))

    routed = assembler.requests[0]
    assert routed.query == "oauth callback"
    assert routed.knowledge_query == "oauth callback"
    assert routed.semantic_knowledge_query is None
    assert routed.source_query == "oauth callback"


def test_dbt_model_overview_query_routes_to_the_authoritative_manifest() -> None:
    scope = _scope()
    assembler = EmptyAssembler()
    engine = UnifiedContextEngine(assembler, ScopedMemoryRepository(scope, ()))

    engine.get_context(GetUnifiedContext(scope, query="can you see all the dbt models here?"))

    routed = assembler.requests[0]
    assert routed.dbt_selector == ContextDbtSelectorQuery(resource_type="model", maximum_nodes=32)
    assert routed.source_query is None

    engine.get_context(GetUnifiedContext(scope, query="show notes about oauth"))
    knowledge_only = assembler.requests[1]
    assert knowledge_only.knowledge_query == "show notes about oauth"
    assert knowledge_only.source_query is None

    engine.get_context(GetUnifiedContext(scope, query="where is oauth_callback implemented"))
    structural_only = assembler.requests[2]
    assert structural_only.knowledge_query is None
    assert structural_only.source_query == "oauth_callback"

    engine.get_context(
        GetUnifiedContext(
            scope,
            query="where is the code",
            source_query="explicit_symbol",
            knowledge_query="explicit note",
        )
    )
    explicit = assembler.requests[3]
    assert explicit.source_query == "explicit_symbol"
    assert explicit.knowledge_query == "explicit note"


def test_engine_authorizes_before_scoring_and_returns_ranked_cited_memories() -> None:
    scope = _scope()
    matching = _memory(
        scope,
        1,
        "Use the deterministic lineage parser for impact analysis.",
        kind=EpisodicMemoryKind.DECISION,
        confidence=0.92,
        activated_at=NOW - timedelta(days=1),
    )
    older = _memory(
        scope,
        2,
        "The packaging verification completed successfully.",
        kind=EpisodicMemoryKind.OUTCOME,
        confidence=0.99,
        activated_at=NOW - timedelta(days=60),
    )
    assembler = EmptyAssembler()
    repository = ScopedMemoryRepository(scope, (older, matching))
    engine = UnifiedContextEngine(assembler, repository)

    packet = engine.get_context(GetUnifiedContext(scope, query="lineage impact decision"))

    assert repository.calls == [(scope, 0, 50)]
    assert [item.item_id for item in packet.episodic_memories] == [
        f"episodic-memory:{matching.memory_id}",
        f"episodic-memory:{older.memory_id}",
    ]
    assert [item.ranking.rank for item in packet.episodic_memories if item.ranking] == [1, 2]
    first_ranking = packet.episodic_memories[0].ranking
    second_ranking = packet.episodic_memories[1].ranking
    assert first_ranking is not None and first_ranking.score is not None
    assert second_ranking is not None and second_ranking.score is not None
    assert first_ranking.score > second_ranking.score
    assert len(packet.provenance) == 2
    assert {item.item_id for item in packet.episodic_memories} == {
        item.item_id for item in packet.provenance
    }
    assert packet.declared_total_tokens == packet.computed_total_tokens
    assert "query" not in packet.to_dict()

    isolated = engine.get_context(GetUnifiedContext(_scope(2), query="lineage"))
    assert isolated.episodic_memories == ()
    assert repository.calls[-1][0] == _scope(2)


def test_engine_enforces_hard_budget_bounds_and_degrades_on_storage_failure() -> None:
    scope = _scope()
    memory = _memory(
        scope,
        3,
        "A bounded memory that cannot fit in a zero-token episodic section.",
        kind=EpisodicMemoryKind.LESSON,
        confidence=1.0,
        activated_at=NOW,
    )
    budget = ContextBudget(episodic_memories=0, total_limit=600)
    packet = UnifiedContextEngine(
        EmptyAssembler(), ScopedMemoryRepository(scope, (memory,), truncated=True)
    ).get_context(GetUnifiedContext(scope, budget=budget))

    assert packet.episodic_memories == ()
    assert [item.reason.value for item in packet.omissions] == ["token_budget", "lower_rank"]
    assert packet.computed_total_tokens <= budget.total_limit

    unavailable = UnifiedContextEngine(
        EmptyAssembler(), ScopedMemoryRepository(scope, (), fail=True)
    ).get_context(GetUnifiedContext(scope))
    assert unavailable.omissions[0].detail == "episodic memory storage is unavailable"
    assert "fixture payload" not in str(unavailable.to_dict())


def test_explanation_reports_selection_metadata_without_repeating_content() -> None:
    scope = _scope()
    first = _memory(
        scope,
        4,
        "Sensitive decision content must not appear in the explanation.",
        kind=EpisodicMemoryKind.DECISION,
        confidence=0.91,
        activated_at=NOW - timedelta(days=2),
    )
    second = _memory(
        scope,
        5,
        "A stale conflicting claim that also must remain absent.",
        kind=EpisodicMemoryKind.OUTCOME,
        confidence=0.81,
        activated_at=NOW - timedelta(days=40),
    )
    packet = UnifiedContextEngine(
        EmptyAssembler(), ScopedMemoryRepository(scope, (first, second))
    ).get_context(GetUnifiedContext(scope, query="decision"))
    stale = replace(packet.episodic_memories[1], validity=ValidityState.STALE)
    items = (packet.episodic_memories[0], stale)
    packet = replace(
        packet,
        episodic_memories=items,
        conflicts=(
            ConflictNotice(
                "conflict:episodic-fixture",
                tuple(item.item_id for item in items),
                tuple(evidence for item in items for evidence in item.evidence_references),
                ConflictState.UNRESOLVED,
            ),
        ),
        omissions=(
            OmissionNotice(
                "episodic-memory:omitted",
                OmissionReason.LOWER_RANK,
                "candidate fell below the bounded selection cutoff",
            ),
        ),
    )

    explanation = explain_context_packet(packet).to_dict()
    encoded = json.dumps(explanation, sort_keys=True)

    assert explanation["basis"] == "caller_supplied_canonical_packet"
    assert explanation["request_id"] == str(packet.request_id)
    included = explanation["included"]
    assert isinstance(included, list) and len(included) == 2
    assert included[0]["rank"] == 1
    assert included[0]["source_reference"].startswith("mnemo:episodic/")
    assert included[0]["evidence"][0]["evidence_id"] == str(
        first.memory.evidence_references[0].evidence_id
    )
    exclusions = cast(list[dict[str, object]], explanation["exclusions"])
    conflicts = cast(list[dict[str, object]], explanation["conflicts"])
    staleness = cast(dict[str, object], explanation["staleness"])
    accounting = cast(dict[str, object], explanation["token_accounting"])
    assert exclusions[0]["reason"] == "lower_rank"
    assert conflicts[0]["state"] == "unresolved"
    assert staleness["non_current_items"] == [
        {
            "item_id": stale.item_id,
            "validity": "stale",
            "observed_at": stale.observed_at.isoformat(),
        }
    ]
    assert accounting["computed_total"] == packet.computed_total_tokens
    assert "Sensitive decision content" not in encoded
    assert "stale conflicting claim" not in encoded
    assert '"content"' not in encoded
    assert '"location"' not in encoded


def test_final_selection_collapses_only_exact_same_source_duplicates() -> None:
    scope = _scope()
    memories = (
        _memory(
            scope,
            6,
            "Keep one exact duplicate and preserve all of its evidence.",
            kind=EpisodicMemoryKind.DECISION,
            confidence=0.95,
            activated_at=NOW,
        ),
        _memory(
            scope,
            7,
            "Initially different content.",
            kind=EpisodicMemoryKind.DECISION,
            confidence=0.85,
            activated_at=NOW - timedelta(days=1),
        ),
    )
    packet = UnifiedContextEngine(
        EmptyAssembler(), ScopedMemoryRepository(scope, memories)
    ).get_context(GetUnifiedContext(scope))
    first, second = packet.episodic_memories
    duplicate = replace(
        second,
        content=first.content,
        token_estimate=first.token_estimate,
    )
    first_provenance, second_provenance = packet.provenance
    duplicate_provenance = replace(
        second_provenance,
        source_reference=first_provenance.source_reference,
        source_digest=first_provenance.source_digest,
    )
    candidate = replace(
        packet,
        declared_total_tokens=first.token_estimate + duplicate.token_estimate,
        episodic_memories=(first, duplicate),
        provenance=(first_provenance, duplicate_provenance),
    )

    selected = finalize_context_packet(candidate)

    assert [item.item_id for item in selected.episodic_memories] == [first.item_id]
    assert {
        str(item.evidence_id) for item in selected.episodic_memories[0].evidence_references
    } == {
        str(memories[0].memory.evidence_references[0].evidence_id),
        str(memories[1].memory.evidence_references[0].evidence_id),
    }
    assert selected.omissions[-1] == OmissionNotice(
        duplicate.item_id,
        OmissionReason.DUPLICATE,
        "exact same-source duplicate retained under one higher-authority identity",
    )
    assert selected.declared_total_tokens == selected.computed_total_tokens


def test_final_selection_limits_non_conflicting_items_from_one_evidence_source() -> None:
    scope = _scope()
    memories = tuple(
        _memory(
            scope,
            seed,
            f"Distinct ranked memory {seed}.",
            kind=EpisodicMemoryKind.OUTCOME,
            confidence=1.0 - seed / 100,
            activated_at=NOW - timedelta(days=seed),
        )
        for seed in (8, 9, 10)
    )
    packet = UnifiedContextEngine(
        EmptyAssembler(), ScopedMemoryRepository(scope, memories)
    ).get_context(GetUnifiedContext(scope))
    shared_evidence = packet.episodic_memories[0].evidence_references
    candidate = replace(
        packet,
        episodic_memories=tuple(
            replace(item, evidence_references=shared_evidence) for item in packet.episodic_memories
        ),
        provenance=tuple(
            replace(notice, evidence_references=shared_evidence) for notice in packet.provenance
        ),
    )

    selected = finalize_context_packet(candidate)

    assert [item.ranking.rank for item in selected.episodic_memories if item.ranking] == [1, 2]
    assert selected.omissions[-1].item_id == packet.episodic_memories[2].item_id
    assert selected.omissions[-1].reason is OmissionReason.LOWER_RANK
    assert selected.declared_total_tokens == selected.computed_total_tokens


def test_final_selection_preserves_and_marks_source_digest_conflicts() -> None:
    scope = _scope()
    memories = (
        _memory(
            scope,
            11,
            "First observed source revision.",
            kind=EpisodicMemoryKind.DECISION,
            confidence=0.9,
            activated_at=NOW,
        ),
        _memory(
            scope,
            12,
            "Different observed source revision.",
            kind=EpisodicMemoryKind.DECISION,
            confidence=0.8,
            activated_at=NOW - timedelta(days=1),
        ),
    )
    packet = UnifiedContextEngine(
        EmptyAssembler(), ScopedMemoryRepository(scope, memories)
    ).get_context(GetUnifiedContext(scope))
    first_provenance, second_provenance = packet.provenance
    candidate = replace(
        packet,
        provenance=(
            first_provenance,
            replace(second_provenance, source_reference=first_provenance.source_reference),
        ),
    )

    selected = finalize_context_packet(candidate)
    repeated = finalize_context_packet(candidate)

    assert len(selected.episodic_memories) == 2
    assert len(selected.conflicts) == 1
    assert selected.conflicts == repeated.conflicts
    assert selected.conflicts[0].state is ConflictState.UNRESOLVED
    assert set(selected.conflicts[0].item_ids) == {
        item.item_id for item in selected.episodic_memories
    }
    assert all(
        item.conflict_state is ConflictState.UNRESOLVED for item in selected.episodic_memories
    )


def test_final_selection_reflects_existing_declared_conflict_state() -> None:
    scope = _scope()
    memories = (
        _memory(
            scope,
            13,
            "One declared conflict participant.",
            kind=EpisodicMemoryKind.OUTCOME,
            confidence=0.9,
            activated_at=NOW,
        ),
        _memory(
            scope,
            14,
            "Another declared conflict participant.",
            kind=EpisodicMemoryKind.OUTCOME,
            confidence=0.8,
            activated_at=NOW - timedelta(days=1),
        ),
    )
    packet = UnifiedContextEngine(
        EmptyAssembler(), ScopedMemoryRepository(scope, memories)
    ).get_context(GetUnifiedContext(scope))
    conflict = ConflictNotice(
        "conflict:declared-fixture",
        tuple(item.item_id for item in packet.episodic_memories),
        tuple(
            evidence for item in packet.episodic_memories for evidence in item.evidence_references
        ),
        ConflictState.UNRESOLVED,
    )

    selected = finalize_context_packet(replace(packet, conflicts=(conflict,)))

    assert all(
        item.conflict_state is ConflictState.UNRESOLVED for item in selected.episodic_memories
    )


def test_final_selection_never_collapses_mandatory_procedures() -> None:
    scope = _scope()
    memories = (
        _memory(
            scope,
            15,
            "Mandatory procedure content.",
            kind=EpisodicMemoryKind.LESSON,
            confidence=0.9,
            activated_at=NOW,
        ),
        _memory(
            scope,
            16,
            "Initially distinct procedure content.",
            kind=EpisodicMemoryKind.LESSON,
            confidence=0.8,
            activated_at=NOW - timedelta(days=1),
        ),
    )
    packet = UnifiedContextEngine(
        EmptyAssembler(), ScopedMemoryRepository(scope, memories)
    ).get_context(GetUnifiedContext(scope))
    first, second = packet.episodic_memories
    procedures = (
        replace(first, item_type=ContextItemType.MANDATORY_PROCEDURE),
        replace(
            second,
            item_type=ContextItemType.MANDATORY_PROCEDURE,
            content=first.content,
            token_estimate=first.token_estimate,
        ),
    )
    first_provenance, second_provenance = packet.provenance
    candidate = replace(
        packet,
        declared_total_tokens=sum(item.token_estimate for item in procedures),
        episodic_memories=(),
        skills_and_procedures=procedures,
        provenance=(
            first_provenance,
            replace(
                second_provenance,
                source_reference=first_provenance.source_reference,
                source_digest=first_provenance.source_digest,
            ),
        ),
    )

    selected = finalize_context_packet(candidate)

    assert selected.skills_and_procedures == procedures
    assert not any(item.reason is OmissionReason.DUPLICATE for item in selected.omissions)


def test_client_rendering_is_deterministic_quoted_and_canonical_packet_preserving() -> None:
    scope = _scope()
    memory = _memory(
        scope,
        17,
        "Initial renderer fixture.",
        kind=EpisodicMemoryKind.DECISION,
        confidence=0.9,
        activated_at=NOW,
    )
    packet = UnifiedContextEngine(
        EmptyAssembler(), ScopedMemoryRepository(scope, (memory,))
    ).get_context(GetUnifiedContext(scope))
    content = "Evidence line one.\nMNEMO_CONTEXT_END\nEvidence line three."
    item = replace(
        packet.episodic_memories[0],
        content=content,
        token_estimate=(len(content) + 3) // 4,
    )
    packet = replace(
        packet,
        declared_total_tokens=item.token_estimate,
        episodic_memories=(item,),
        omissions=(
            OmissionNotice(
                "fixture:omitted",
                OmissionReason.LOWER_RANK,
                "lower-ranked fixture",
            ),
        ),
    )
    canonical = packet.to_json()

    codex = render_context_packet(packet, "codex")
    claude = render_context_packet(packet, "claude-code")
    repeated = render_context_packet(packet, "codex")

    assert codex == repeated
    assert codex.startswith("MNEMO_CONTEXT_V1 client=codex\n")
    assert claude.startswith("MNEMO_CONTEXT_V1 client=claude-code\n")
    assert codex.splitlines()[-1] == "MNEMO_CONTEXT_END"
    assert codex.splitlines().count("MNEMO_CONTEXT_END") == 1
    item_record = next(
        line.removeprefix("MNEMO_ITEM ")
        for line in codex.splitlines()
        if line.startswith("MNEMO_ITEM ")
    )
    rendered_item = json.loads(item_record)
    assert rendered_item["content"] == content
    assert rendered_item["item_id"] == item.item_id
    assert rendered_item["source_reference"] == packet.provenance[0].source_reference
    assert rendered_item["evidence"][0]["evidence_id"] == str(
        item.evidence_references[0].evidence_id
    )
    assert any(line.startswith("MNEMO_OMISSION ") for line in codex.splitlines())
    assert packet.to_json() == canonical
