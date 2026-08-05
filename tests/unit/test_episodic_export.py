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
    EpisodicRetentionService,
    TaskActivityRetentionService,
)
from mnemo_memory.packages.storage import (
    InvalidEpisodicExportScope,
    ReferenceEpisodicMemoryCandidateRepository,
    ReferenceTaskActivityEventRepository,
    SQLiteCheckpointRepository,
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
