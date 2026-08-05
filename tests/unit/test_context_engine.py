"""Deterministic planning and authorization-first episodic retrieval for Issue 17A."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from mnemo_memory.packages.application.unified_context import GetUnifiedContext
from mnemo_memory.packages.context_engine import (
    DeterministicContextPlanner,
    QueryIntent,
    RetrievalCategory,
    UnifiedContextEngine,
)
from mnemo_memory.packages.domain import (
    ActiveEpisodicMemory,
    ContextBudget,
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
