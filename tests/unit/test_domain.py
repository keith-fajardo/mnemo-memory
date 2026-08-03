from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest

from mnemo_memory.packages.domain import (
    AgentId,
    ApprovedEpisodicEvent,
    ApprovedEventKind,
    Checkpoint,
    CheckpointEventKind,
    CheckpointId,
    CheckpointLifecycleEvent,
    CheckpointRevisionId,
    CheckpointStatus,
    DurableClaim,
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
    ProjectId,
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

NOW = datetime(2026, 8, 2, 9, 0, tzinfo=UTC)
LATER = NOW + timedelta(minutes=5)
CONTENT_HASH = "sha256:" + "a" * 64


def task_scope() -> MemoryScope:
    return MemoryScope(
        owner_id=OwnerId.new(),
        level=ScopeLevel.TASK,
        visibility=Visibility.PROJECT,
        workspace_id=WorkspaceId.new(),
        project_id=ProjectId.new(),
        session_id=SessionId.new(),
        task_id=TaskId.new(),
    )


def evidence() -> EvidenceReference:
    return EvidenceReference(
        evidence_id=EvidenceId.new(),
        source_id=SourceId.new(),
        source_type=EvidenceSourceType.REPOSITORY,
        trust_class=SourceTrustClass.CURRENT_STRUCTURAL,
        immutable_source_ref="git:abc123:packages/domain/models.py",
        content_hash=CONTENT_HASH,
        location=EvidenceLocation("repo://packages/domain/models.py", 1, 0, 5, 1),
        observed_at=NOW,
        verification_status=VerificationStatus.VERIFIED,
    )


def checkpoint(**changes: object) -> Checkpoint:
    values: dict[str, object] = {
        "checkpoint_id": CheckpointId.new(),
        "scope": task_scope(),
        "task_objective": "Complete the domain model",
        "completed_work": ("Defined identifiers",),
        "current_state": "Domain types are under review",
        "remaining_work": (),
        "decisions": ("Use immutable value objects",),
        "failures": (),
        "blockers": (),
        "relevant_files": ("packages/domain/models.py",),
        "relevant_artifacts": ("docs/implementation-plan.md",),
        "verification_performed": ("pytest",),
        "evidence_references": (evidence(),),
        "status": CheckpointStatus.DRAFT,
        "revision": 1,
        "supersedes_checkpoint_id": None,
        "superseded_by_checkpoint_id": None,
        "token_estimate": 250,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(changes)
    return Checkpoint(**values)  # type: ignore[arg-type]


def test_nominal_identifiers_are_validated_and_not_interchangeable() -> None:
    memory_id = MemoryId.new()
    event_id = EventId.from_string(str(memory_id))

    assert str(memory_id) == str(event_id)
    left: object = memory_id
    right: object = event_id
    assert left != right
    assert MemoryId.from_dict(memory_id.to_dict()) == memory_id
    with pytest.raises(ValueError, match="canonical UUID"):
        MemoryId.from_string("not-a-uuid")
    with pytest.raises(ValueError, match="canonical UUID"):
        MemoryId.from_string(str(memory_id).upper())
    with pytest.raises(TypeError):
        MemoryId("not-a-uuid")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="only"):
        MemoryId.from_dict({"value": str(memory_id), "extra": "field"})


def test_checkpoint_lifecycle_event_is_immutable_evidence_bearing_and_deterministic() -> None:
    scope = task_scope()
    checkpoint_id = CheckpointId.new()
    revision_id = CheckpointRevisionId.new()

    first = CheckpointLifecycleEvent.for_revision(
        scope=scope,
        kind=CheckpointEventKind.LESSON_RECORDED,
        checkpoint_id=checkpoint_id,
        revision_id=revision_id,
        revision_number=2,
        occurred_at=NOW,
        evidence_references=(evidence(),),
    )
    second = CheckpointLifecycleEvent.for_revision(
        scope=scope,
        kind=CheckpointEventKind.LESSON_RECORDED,
        checkpoint_id=checkpoint_id,
        revision_id=revision_id,
        revision_number=2,
        occurred_at=NOW,
        evidence_references=first.evidence_references,
    )

    assert first.event_id == second.event_id
    assert first.idempotency_key == second.idempotency_key
    assert CheckpointLifecycleEvent.from_dict(first.to_dict()) == first
    with pytest.raises(FrozenInstanceError):
        first.revision_number = 3  # type: ignore[misc]


def test_checkpoint_lifecycle_event_rejects_unscoped_raw_or_duplicate_evidence() -> None:
    checkpoint_id = CheckpointId.new()
    revision_id = CheckpointRevisionId.new()
    item = evidence()
    with pytest.raises(ValueError, match="task scope"):
        CheckpointLifecycleEvent.for_revision(
            scope=MemoryScope(
                OwnerId.new(), ScopeLevel.PROJECT, Visibility.PROJECT, project_id=ProjectId.new()
            ),
            kind=CheckpointEventKind.CREATED,
            checkpoint_id=checkpoint_id,
            revision_id=revision_id,
            revision_number=1,
            occurred_at=NOW,
            evidence_references=(item,),
        )
    with pytest.raises(ValueError, match="evidence must be unique"):
        CheckpointLifecycleEvent.for_revision(
            scope=task_scope(),
            kind=CheckpointEventKind.CREATED,
            checkpoint_id=checkpoint_id,
            revision_id=revision_id,
            revision_number=1,
            occurred_at=NOW,
            evidence_references=(item, item),
        )


def test_scope_combinations_and_serialization_are_strict() -> None:
    owner = OwnerId.new()
    workspace = WorkspaceId.new()
    project = ProjectId.new()
    scopes = (
        MemoryScope(owner, ScopeLevel.PERSONAL, Visibility.OWNER),
        MemoryScope(owner, ScopeLevel.WORKSPACE, Visibility.WORKSPACE, workspace_id=workspace),
        MemoryScope(owner, ScopeLevel.PROJECT, Visibility.PROJECT, project_id=project),
        MemoryScope(
            owner,
            ScopeLevel.SESSION,
            Visibility.PROJECT,
            project_id=project,
            session_id=SessionId.new(),
        ),
        task_scope(),
        MemoryScope(
            owner,
            ScopeLevel.AGENT,
            Visibility.PROJECT,
            project_id=project,
            agent_id=AgentId.new(),
        ),
    )

    for scope in scopes:
        assert MemoryScope.from_dict(scope.to_dict()) == scope
    with pytest.raises(ValueError, match="invalid identifier combination"):
        MemoryScope(owner, ScopeLevel.PROJECT, Visibility.OWNER)
    with pytest.raises(ValueError, match="workspace visibility"):
        MemoryScope(owner, ScopeLevel.PROJECT, Visibility.WORKSPACE, project_id=project)
    with pytest.raises(ValueError, match="personal scope"):
        MemoryScope(owner, ScopeLevel.PERSONAL, Visibility.PROJECT)
    serialized = scopes[0].to_dict()
    serialized["unexpected"] = "field"
    with pytest.raises(ValueError, match="unknown"):
        MemoryScope.from_dict(serialized)


def test_sensitivity_prevents_prohibited_content_from_becoming_active() -> None:
    candidate = MemoryClassification(Sensitivity.NORMAL, MemoryStatus.CANDIDATE)
    active = candidate.activate()

    assert active.can_be_embedded
    assert active.can_enter_context
    prohibited = MemoryClassification(Sensitivity.PROHIBITED, MemoryStatus.REJECTED)
    assert not prohibited.can_be_embedded
    assert not prohibited.can_enter_context
    with pytest.raises(ValueError, match="prohibited content"):
        MemoryClassification(Sensitivity.PROHIBITED, MemoryStatus.ACTIVE)
    with pytest.raises(ValueError, match="prohibited content"):
        prohibited.activate()


def test_retention_requires_explicit_policy_and_valid_temporal_intervals() -> None:
    expiring = RetentionSchedule(
        RetentionPolicyId.new(),
        False,
        NOW,
        NOW,
        NOW,
        NOW + timedelta(days=1),
        NOW + timedelta(days=2),
    )
    assert not expiring.is_expired(NOW)
    expired = expiring.expire(NOW + timedelta(days=2))
    assert expired.is_expired(NOW + timedelta(days=2))
    assert RetentionSchedule.from_dict(expiring.to_dict()) == expiring
    permanent = RetentionSchedule(RetentionPolicyId.new(), True, NOW, NOW, NOW, None, None)
    assert not permanent.is_expired(NOW + timedelta(days=365))
    with pytest.raises(ValueError, match="explicit expires_at"):
        RetentionSchedule(RetentionPolicyId.new(), False, NOW, NOW, NOW, None, None)
    with pytest.raises(ValueError, match="valid_to cannot"):
        RetentionSchedule(
            RetentionPolicyId.new(), True, NOW, NOW, NOW, NOW - timedelta(seconds=1), None
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        RetentionSchedule(RetentionPolicyId.new(), True, datetime(2026, 1, 1), NOW, NOW, None, None)


def test_evidence_and_durable_claims_require_structural_validity() -> None:
    reference = evidence()
    assert EvidenceReference.from_dict(reference.to_dict()) == reference
    claim = DurableClaim(
        MemoryId.new(),
        task_scope(),
        MemoryClassification(Sensitivity.NORMAL, MemoryStatus.CANDIDATE),
        RetentionSchedule(RetentionPolicyId.new(), True, NOW, NOW, NOW, None, None),
        "The domain package has no adapter imports.",
        (reference,),
    )
    assert DurableClaim.from_dict(claim.to_dict()) == claim
    with pytest.raises(ValueError, match="sha256"):
        EvidenceReference(
            EvidenceId.new(),
            SourceId.new(),
            EvidenceSourceType.REPOSITORY,
            SourceTrustClass.CURRENT_STRUCTURAL,
            "git:bad",
            "invalid",
            EvidenceLocation("repo://x"),
            NOW,
            VerificationStatus.VERIFIED,
        )
    with pytest.raises(ValueError, match="span"):
        EvidenceLocation("repo://x", start_line=1)
    with pytest.raises(ValueError, match="requires structurally valid evidence"):
        DurableClaim(
            MemoryId.new(),
            task_scope(),
            MemoryClassification(Sensitivity.NORMAL, MemoryStatus.CANDIDATE),
            RetentionSchedule(RetentionPolicyId.new(), True, NOW, NOW, NOW, None, None),
            "Claim",
            (),
        )
    serialized = reference.to_dict()
    serialized["unknown"] = "field"
    with pytest.raises(ValueError, match="unknown"):
        EvidenceReference.from_dict(serialized)


def test_checkpoint_lifecycle_enforces_terminal_state_invariants() -> None:
    draft = checkpoint()
    active = draft.activate(LATER)
    completed = active.complete(LATER + timedelta(minutes=1))

    assert completed.status is CheckpointStatus.COMPLETED
    assert completed.completed_at == LATER + timedelta(minutes=1)
    with pytest.raises(ValueError, match="only active"):
        draft.complete(LATER)
    with pytest.raises(ValueError, match="active blocker"):
        checkpoint(
            status=CheckpointStatus.COMPLETED,
            blockers=("Waiting for review",),
            completed_at=LATER,
            updated_at=LATER,
        )
    assert checkpoint().abandon(LATER).status is CheckpointStatus.ABANDONED
    assert checkpoint().expire(LATER).status is CheckpointStatus.EXPIRED
    with pytest.raises(ValueError, match="only draft or active"):
        completed.expire(LATER + timedelta(minutes=2))


def test_checkpoint_supersession_revision_and_serialization() -> None:
    original = checkpoint()
    replacement_id = CheckpointId.new()
    superseded, replacement = original.revise(replacement_id, LATER)

    assert superseded.status is CheckpointStatus.SUPERSEDED
    assert superseded.superseded_by_checkpoint_id == replacement_id
    assert replacement.checkpoint_id == replacement_id
    assert replacement.revision == 2
    assert replacement.supersedes_checkpoint_id == original.checkpoint_id
    assert Checkpoint.from_dict(replacement.to_dict()) == replacement
    with pytest.raises(ValueError, match="replaced revision"):
        checkpoint(revision=2)
    with pytest.raises(ValueError, match="cannot supersede itself"):
        original.supersede(original.checkpoint_id, LATER)
    serialized = replacement.to_dict()
    serialized["unknown"] = "field"
    with pytest.raises(ValueError, match="unknown"):
        Checkpoint.from_dict(serialized)


def test_domain_value_objects_are_immutable() -> None:
    scope = task_scope()
    with pytest.raises(FrozenInstanceError):
        scope.level = ScopeLevel.PERSONAL  # type: ignore[misc]


def test_approved_episodic_event_is_explicit_scoped_and_round_trips() -> None:
    item_scope = task_scope()
    event = ApprovedEpisodicEvent.create(
        scope=item_scope,
        kind=ApprovedEventKind.FAILURE,
        summary="The reconciliation query used the wrong business-date grain.",
        source_event_key="fixture:failure:1",
        occurred_at=NOW,
        evidence_references=(evidence(),),
    )

    assert ApprovedEpisodicEvent.from_dict(event.to_dict()) == event
    assert (
        ApprovedEpisodicEvent.create(
            scope=item_scope,
            kind=ApprovedEventKind.FAILURE,
            summary=event.summary,
            source_event_key="fixture:failure:1",
            occurred_at=NOW,
            evidence_references=event.evidence_references,
        ).event_id
        == event.event_id
    )
    malformed = event.to_dict()
    malformed["transcript"] = "not permitted"
    with pytest.raises(ValueError, match="fields"):
        ApprovedEpisodicEvent.from_dict(malformed)
