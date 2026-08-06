"""Portable exact-scope production episodic export coverage."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mnemo_memory.packages.domain import (
    EPISODIC_EXPORT_FORMAT,
    EpisodicCandidateReviewAction,
    EpisodicCandidateReviewDecision,
    EpisodicExportBundle,
    EpisodicExtractionProposal,
    EpisodicMemoryCandidate,
    EpisodicMemoryGovernanceAction,
    EpisodicMemoryKind,
    EpisodicMemoryRevision,
    EventId,
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    MemoryScope,
    OwnerId,
    ProjectId,
    RetentionPolicyId,
    RetentionSchedule,
    ScopeLevel,
    Sensitivity,
    SessionId,
    SourceId,
    SourceTrustClass,
    TaskActivityActor,
    TaskActivityEvent,
    TaskActivityEventKind,
    TaskId,
    VerificationStatus,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.episodic import (
    EpisodicDeletionService,
    EpisodicExportService,
    EpisodicImportConflict,
    EpisodicImportService,
    EpisodicImportStorageFailure,
    EpisodicImportUnsupportedLifecycle,
    EpisodicRetentionService,
    TaskActivityRetentionService,
)
from mnemo_memory.packages.storage import (
    EpisodicLifecycleImportResult,
    InvalidEpisodicExportScope,
    ReferenceEpisodicMemoryCandidateRepository,
    ReferenceTaskActivityEventRepository,
    SQLiteCheckpointRepository,
    TaskActivityEventPage,
    TaskActivityEventRepository,
    TaskActivityEventStorageFailure,
    TaskActivityEventStoreResult,
)

NOW = datetime(2026, 8, 5, 19, 0, tzinfo=UTC)
DUE = NOW + timedelta(days=1)
SWEEP = DUE + timedelta(hours=1)
PURGED = SWEEP + timedelta(minutes=1)
EXPORTED = PURGED + timedelta(minutes=1)

Repository = ReferenceEpisodicMemoryCandidateRepository | SQLiteCheckpointRepository
EventRepository = ReferenceTaskActivityEventRepository | SQLiteCheckpointRepository


def _scope(seed: int = 1) -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string(f"09000000-0000-4000-8000-{seed:012d}"),
        ScopeLevel.TASK,
        Visibility.PROJECT,
        WorkspaceId.from_string(f"19000000-0000-4000-8000-{seed:012d}"),
        ProjectId.from_string(f"29000000-0000-4000-8000-{seed:012d}"),
        SessionId.from_string(f"39000000-0000-4000-8000-{seed:012d}"),
        TaskId.from_string(f"49000000-0000-4000-8000-{seed:012d}"),
    )


def _evidence(seed: int, *, user: bool = False) -> EvidenceReference:
    return EvidenceReference(
        EvidenceId.from_string(f"{'5b' if user else '5a'}000000-0000-4000-8000-{seed:012d}"),
        SourceId.from_string(f"{'6b' if user else '6a'}000000-0000-4000-8000-{seed:012d}"),
        EvidenceSourceType.USER_CORRECTION if user else EvidenceSourceType.AGENT_EVENT,
        SourceTrustClass.USER_CORRECTION if user else SourceTrustClass.APPROVED_CHECKPOINT,
        f"fixture://episodic-export/{seed}",
        "sha256:" + ("b" if user else "a") * 64,
        EvidenceLocation(f"fixture://episodic-export/{seed}"),
        NOW,
        VerificationStatus.VERIFIED,
    )


def _event(scope: MemoryScope, *, seed: int, expiring: bool = False) -> TaskActivityEvent:
    return TaskActivityEvent.create(
        scope=scope,
        kind=TaskActivityEventKind.TASK_OUTCOME,
        actor=TaskActivityActor.AGENT,
        summary=f"The export fixture outcome {seed} passed verification.",
        source_event_key=f"episodic-export:{seed}",
        sensitivity=Sensitivity.RESTRICTED,
        retention=RetentionSchedule(
            RetentionPolicyId.from_string(f"79000000-0000-4000-8000-{seed:012d}"),
            not expiring,
            NOW,
            NOW,
            NOW,
            None,
            DUE if expiring else None,
        ),
        occurred_at=NOW,
        evidence_references=(_evidence(seed),),
    )


def _candidate(event: TaskActivityEvent, *, seed: int) -> EpisodicMemoryCandidate:
    return EpisodicMemoryCandidate.create(
        source_event=event,
        proposal=EpisodicExtractionProposal(
            EpisodicMemoryKind.OUTCOME,
            f"The export fixture memory {seed} retains exact provenance.",
            0.93,
            Sensitivity.RESTRICTED,
        ),
        proposal_index=0,
        sensitivity=Sensitivity.RESTRICTED,
        extractor_version=f"export-extractor-v{seed}",
        provider_id="luna-fixture",
        model_id="gpt-5.6-luna-fixture",
        prompt_version="export-prompt-v1",
        created_at=NOW,
    )


def _store(
    events: EventRepository, memories: Repository, scope: MemoryScope, *, seed: int
) -> tuple[TaskActivityEvent, EpisodicMemoryCandidate]:
    event = _event(scope, seed=seed)
    candidate = _candidate(event, seed=seed)
    events.append_task_activity_event(event)
    memories.store_episodic_memory_candidates((candidate,))
    return event, candidate


def _approve_and_correct(
    memories: Repository, candidate: EpisodicMemoryCandidate, *, seed: int
) -> EpisodicMemoryGovernanceAction:
    approval = EpisodicCandidateReviewAction.create(
        scope=candidate.scope,
        candidate_id=candidate.memory_id,
        decision=EpisodicCandidateReviewDecision.APPROVED,
        source_action_key=f"export-approve:{seed}",
        reason="I verified this memory for portable export.",
        reviewed_at=NOW + timedelta(minutes=1),
        evidence_references=(_evidence(seed + 100, user=True),),
    )
    memories.review_episodic_memory_candidate(approval)
    correction = EpisodicMemoryGovernanceAction.correct(
        scope=candidate.scope,
        memory_id=candidate.memory_id,
        expected_revision_id=approval.action_id,
        source_action_key=f"export-correct:{seed}",
        reason="I verified the corrected export wording.",
        corrected_claim="The corrected export fixture retains its full revision chain.",
        corrected_sensitivity=Sensitivity.RESTRICTED,
        occurred_at=NOW + timedelta(minutes=2),
        evidence_references=(_evidence(seed + 200, user=True),),
    )
    memories.govern_episodic_memory(correction)
    return correction


def _populate(events: EventRepository, memories: Repository, scope: MemoryScope) -> None:
    _, live = _store(events, memories, scope, seed=1)
    _approve_and_correct(memories, live, seed=1)

    _, rejected = _store(events, memories, scope, seed=2)
    memories.review_episodic_memory_candidate(
        EpisodicCandidateReviewAction.create(
            scope=scope,
            candidate_id=rejected.memory_id,
            decision=EpisodicCandidateReviewDecision.REJECTED,
            source_action_key="export-reject:2",
            reason="I verified that this candidate should remain inactive.",
            reviewed_at=NOW + timedelta(minutes=1),
            evidence_references=(_evidence(302, user=True),),
        )
    )

    expired_event = _event(scope, seed=3, expiring=True)
    expired_candidate = _candidate(expired_event, seed=3)
    events.append_task_activity_event(expired_event)
    memories.store_episodic_memory_candidates((expired_candidate,))
    EpisodicRetentionService(memories).expire_due(scope, as_of=SWEEP)
    EpisodicRetentionService(memories).purge_expired(scope, purged_at=PURGED)
    TaskActivityRetentionService(memories).expire_due(scope, as_of=SWEEP)
    TaskActivityRetentionService(memories).purge_expired(scope, purged_at=PURGED)

    deleted_event, _ = _store(events, memories, scope, seed=4)
    EpisodicDeletionService(memories).delete_task_event(
        scope=scope,
        event_id=deleted_event.event_id,
        source_action_key="export-delete-source:4",
        deleted_at=NOW + timedelta(minutes=3),
    )

    _, deleted_memory = _store(events, memories, scope, seed=5)
    EpisodicDeletionService(memories).delete_memory(
        scope=scope,
        memory_id=deleted_memory.memory_id,
        source_event_id=deleted_memory.source_event_id,
        source_action_key="export-delete-memory:5",
        deleted_at=NOW + timedelta(minutes=3),
    )


def _populate_live(events: EventRepository, memories: Repository, scope: MemoryScope) -> None:
    _, live = _store(events, memories, scope, seed=1)
    _approve_and_correct(memories, live, seed=1)
    _, rejected = _store(events, memories, scope, seed=2)
    memories.review_episodic_memory_candidate(
        EpisodicCandidateReviewAction.create(
            scope=scope,
            candidate_id=rejected.memory_id,
            decision=EpisodicCandidateReviewDecision.REJECTED,
            source_action_key="export-reject:2",
            reason="I verified that this candidate should remain inactive.",
            reviewed_at=NOW + timedelta(minutes=1),
            evidence_references=(_evidence(302, user=True),),
        )
    )


class _FailSecondEventWrite:
    def __init__(self, delegate: TaskActivityEventRepository) -> None:
        self._delegate = delegate
        self._writes = 0

    def append_task_activity_event(self, event: TaskActivityEvent) -> TaskActivityEventStoreResult:
        self._writes += 1
        if self._writes == 2:
            raise TaskActivityEventStorageFailure("injected private adapter detail")
        return self._delegate.append_task_activity_event(event)

    def get_task_activity_event(self, scope: MemoryScope, event_id: EventId) -> TaskActivityEvent:
        return self._delegate.get_task_activity_event(scope, event_id)

    def list_task_activity_events(
        self, scope: MemoryScope, *, offset: int = 0, limit: int = 50
    ) -> TaskActivityEventPage:
        return self._delegate.list_task_activity_events(scope, offset=offset, limit=limit)


class _LifecycleTarget:
    def __init__(self, delegate: Repository) -> None:
        self._delegate = delegate
        self._imported: EpisodicExportBundle | None = None
        self._source_digest: str | None = None

    def export_episodic_state(
        self, scope: MemoryScope, *, exported_at: datetime
    ) -> EpisodicExportBundle:
        if self._imported is not None and self._imported.scope == scope:
            assert self._imported.exported_at == exported_at
            return self._imported
        return self._delegate.export_episodic_state(scope, exported_at=exported_at)

    def import_episodic_lifecycle(
        self, source: EpisodicExportBundle, target: EpisodicExportBundle
    ) -> EpisodicLifecycleImportResult:
        count = sum(
            len(getattr(target, name))
            for name in (
                "memory_expirations",
                "memory_purges",
                "task_expirations",
                "task_purges",
                "memory_deletions",
                "task_deletions",
            )
        )
        if self._imported is not None:
            assert self._imported == target
            assert self._source_digest == source.content_digest
            return EpisodicLifecycleImportResult(count, True)
        self._imported = target
        self._source_digest = source.content_digest
        return EpisodicLifecycleImportResult(count, False)


def _reference() -> tuple[EventRepository, Repository]:
    events = ReferenceTaskActivityEventRepository()
    return events, ReferenceEpisodicMemoryCandidateRepository(events)


def _sqlite(tmp_path: Path) -> tuple[EventRepository, Repository]:
    sqlite = SQLiteCheckpointRepository(
        tmp_path / "episodic-export.sqlite3", base_directory=tmp_path
    )
    sqlite.migrate()
    return sqlite, sqlite


def test_revision_serialization_is_strict_and_round_trips(tmp_path: Path) -> None:
    events, memories = _reference()
    _, candidate = _store(events, memories, _scope(), seed=6)
    _approve_and_correct(memories, candidate, seed=6)
    revision = memories.list_episodic_memory_revisions(candidate.scope, candidate.memory_id)[1]

    assert EpisodicMemoryRevision.from_dict(revision.to_dict()) == revision
    invalid = revision.to_dict()
    invalid["unexpected"] = True
    with pytest.raises(ValueError, match="fields"):
        EpisodicMemoryRevision.from_dict(invalid)


def test_reference_and_sqlite_export_identical_portable_state_with_tombstones(
    tmp_path: Path,
) -> None:
    scope = _scope()
    reference_events, reference = _reference()
    sqlite_events, sqlite = _sqlite(tmp_path)
    _populate(reference_events, reference, scope)
    _populate(sqlite_events, sqlite, scope)
    unrelated_event, _ = _store(sqlite_events, sqlite, _scope(2), seed=8)

    reference_bundle = EpisodicExportService(reference).export(scope, exported_at=EXPORTED)
    sqlite_bundle = EpisodicExportService(sqlite).export(scope, exported_at=EXPORTED)

    assert sqlite_bundle == reference_bundle
    assert sqlite_bundle.format_version == EPISODIC_EXPORT_FORMAT
    assert sqlite_bundle.content_digest.startswith("sha256:")
    assert EpisodicExportBundle.from_json(sqlite_bundle.canonical_json()) == sqlite_bundle
    assert len(sqlite_bundle.task_events) == 3
    assert len(sqlite_bundle.candidates) == 2
    assert len(sqlite_bundle.reviews) == 2
    assert len(sqlite_bundle.governance_actions) == 1
    assert tuple(item.revision_number for item in sqlite_bundle.revisions) == (1, 2)
    assert len(sqlite_bundle.memory_expirations) == 1
    assert len(sqlite_bundle.memory_purges) == 1
    assert len(sqlite_bundle.task_expirations) == 1
    assert len(sqlite_bundle.task_purges) == 1
    assert len(sqlite_bundle.memory_deletions) == 2
    assert len(sqlite_bundle.task_deletions) == 1
    assert unrelated_event.event_id not in {item.event_id for item in sqlite_bundle.task_events}

    assert isinstance(sqlite, SQLiteCheckpointRepository)
    reopened = SQLiteCheckpointRepository(sqlite.path, base_directory=tmp_path)
    reopened.migrate()
    assert EpisodicExportService(reopened).export(scope, exported_at=EXPORTED) == sqlite_bundle


def test_export_is_stable_and_rejects_tampering_duplicates_and_cross_scope(
    tmp_path: Path,
) -> None:
    events, memories = _reference()
    scope = _scope()
    _populate(events, memories, scope)
    service = EpisodicExportService(memories)
    first = service.export(scope, exported_at=EXPORTED)
    second = service.export(scope, exported_at=EXPORTED)

    assert first.canonical_json() == second.canonical_json()
    assert first.content_digest == second.content_digest
    assert (
        service.export(scope, exported_at=EXPORTED + timedelta(seconds=1)).content_digest
        != first.content_digest
    )

    tampered = deepcopy(first.to_dict())
    candidates = tampered["candidates"]
    assert isinstance(candidates, list)
    memory = candidates[0]["memory"]
    assert isinstance(memory, dict)
    memory["claim"] = "tampered"
    with pytest.raises(ValueError, match="digest"):
        EpisodicExportBundle.from_dict(tampered)
    with pytest.raises(ValueError, match="duplicate"):
        EpisodicExportBundle.create(
            scope=scope,
            exported_at=EXPORTED,
            task_events=first.task_events,
            candidates=(*first.candidates, first.candidates[0]),
            reviews=first.reviews,
            governance_actions=first.governance_actions,
            revisions=first.revisions,
            memory_expirations=first.memory_expirations,
            memory_purges=first.memory_purges,
            task_expirations=first.task_expirations,
            task_purges=first.task_purges,
            memory_deletions=first.memory_deletions,
            task_deletions=first.task_deletions,
        )
    with pytest.raises(ValueError, match="cross-scope"):
        EpisodicExportBundle.create(
            scope=_scope(2),
            exported_at=EXPORTED,
            task_events=first.task_events,
        )
    with pytest.raises(ValueError, match="canonically ordered"):
        replace(first, task_events=tuple(reversed(first.task_events)))
    with pytest.raises(ValueError, match="revision replay"):
        EpisodicExportBundle.create(
            scope=scope,
            exported_at=EXPORTED,
            task_events=first.task_events,
            candidates=first.candidates,
            reviews=first.reviews,
            governance_actions=first.governance_actions,
            revisions=(),
            memory_expirations=first.memory_expirations,
            memory_purges=first.memory_purges,
            task_expirations=first.task_expirations,
            task_purges=first.task_purges,
            memory_deletions=first.memory_deletions,
            task_deletions=first.task_deletions,
        )

    project_scope = MemoryScope(
        scope.owner_id,
        ScopeLevel.PROJECT,
        scope.visibility,
        scope.workspace_id,
        scope.project_id,
    )
    with pytest.raises(InvalidEpisodicExportScope):
        service.export(project_scope, exported_at=EXPORTED)


def test_live_export_import_rebases_scope_and_verifies_counts_hash_and_idempotency() -> None:
    source_events, source = _reference()
    source_scope = _scope()
    target_scope = _scope(2)
    _populate_live(source_events, source, source_scope)
    bundle = EpisodicExportService(source).export(source_scope, exported_at=EXPORTED)

    target_events, target = _reference()
    service = EpisodicImportService(target_events, target, target, target, target)
    result = service.import_bundle(bundle, target_scope=target_scope)
    imported = EpisodicExportService(target).export(target_scope, exported_at=EXPORTED)

    assert result.source_content_digest == bundle.content_digest
    assert result.target_content_digest == imported.content_digest
    assert result.source_content_digest != result.target_content_digest
    assert dict(result.counts) == {
        "task_events": 2,
        "candidates": 2,
        "reviews": 2,
        "governance_actions": 1,
        "revisions": 2,
        "memory_expirations": 0,
        "memory_purges": 0,
        "task_expirations": 0,
        "task_purges": 0,
        "memory_deletions": 0,
        "task_deletions": 0,
    }
    assert not result.idempotent
    assert all(item.scope == target_scope for item in imported.task_events)
    assert {item.summary for item in imported.task_events} == {
        item.summary for item in bundle.task_events
    }
    assert {item.event_id for item in imported.task_events}.isdisjoint(
        item.event_id for item in bundle.task_events
    )

    repeated = service.import_bundle(bundle, target_scope=target_scope)
    assert repeated.idempotent
    assert repeated.target_content_digest == result.target_content_digest


def test_live_import_rejects_lifecycle_or_unrelated_target_without_mutating() -> None:
    source_events, source = _reference()
    _populate(source_events, source, _scope())
    lifecycle_bundle = EpisodicExportService(source).export(_scope(), exported_at=EXPORTED)
    target_events, target = _reference()
    service = EpisodicImportService(target_events, target, target, target, target)

    with pytest.raises(EpisodicImportUnsupportedLifecycle, match="lifecycle tombstones"):
        service.import_bundle(lifecycle_bundle, target_scope=_scope(2))
    assert EpisodicExportService(target).export(_scope(2), exported_at=EXPORTED).task_events == ()

    clean_source_events, clean_source = _reference()
    _populate_live(clean_source_events, clean_source, _scope())
    live_bundle = EpisodicExportService(clean_source).export(_scope(), exported_at=EXPORTED)
    unrelated, _ = _store(target_events, target, _scope(2), seed=99)
    before = EpisodicExportService(target).export(_scope(2), exported_at=EXPORTED)
    with pytest.raises(EpisodicImportConflict, match="conflicting state"):
        service.import_bundle(live_bundle, target_scope=_scope(2))
    after = EpisodicExportService(target).export(_scope(2), exported_at=EXPORTED)
    assert after == before
    assert unrelated.event_id in {item.event_id for item in after.task_events}


def test_interrupted_live_import_is_sanitized_and_retry_converges() -> None:
    source_events, source = _reference()
    _populate_live(source_events, source, _scope())
    bundle = EpisodicExportService(source).export(_scope(), exported_at=EXPORTED)
    target_events, target = _reference()
    failing = EpisodicImportService(
        _FailSecondEventWrite(target_events), target, target, target, target
    )

    with pytest.raises(EpisodicImportStorageFailure) as raised:
        failing.import_bundle(bundle, target_scope=_scope(2))
    assert "private adapter detail" not in str(raised.value)

    resumed = EpisodicImportService(target_events, target, target, target, target).import_bundle(
        bundle, target_scope=_scope(2)
    )
    assert not resumed.idempotent
    final = EpisodicExportService(target).export(_scope(2), exported_at=EXPORTED)
    assert final.content_digest == resumed.target_content_digest


def test_full_lifecycle_import_rebases_tombstones_and_verifies_complete_bundle() -> None:
    source_events, source = _reference()
    source_scope = _scope()
    target_scope = _scope(2)
    _populate(source_events, source, source_scope)
    bundle = EpisodicExportService(source).export(source_scope, exported_at=EXPORTED)
    target_events, target = _reference()
    lifecycle = _LifecycleTarget(target)
    service = EpisodicImportService(
        target_events,
        target,
        target,
        target,
        lifecycle,
        lifecycle,
    )

    result = service.import_bundle(bundle, target_scope=target_scope)
    imported = lifecycle.export_episodic_state(target_scope, exported_at=EXPORTED)

    assert not result.idempotent
    assert result.target_content_digest == imported.content_digest
    assert dict(result.counts) == {
        "task_events": 3,
        "candidates": 2,
        "reviews": 2,
        "governance_actions": 1,
        "revisions": 2,
        "memory_expirations": 1,
        "memory_purges": 1,
        "task_expirations": 1,
        "task_purges": 1,
        "memory_deletions": 2,
        "task_deletions": 1,
    }
    assert imported.scope == target_scope
    assert {item.event_id for item in imported.task_expirations}.isdisjoint(
        item.event_id for item in bundle.task_expirations
    )
    assert {item.memory_id for item in imported.memory_deletions}.isdisjoint(
        item.memory_id for item in bundle.memory_deletions
    )
    assert service.import_bundle(bundle, target_scope=target_scope).idempotent
