"""Backend-neutral behavioral contract for canonical checkpoint repositories."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta

import pytest

from mnemo_memory.packages.domain import (
    CheckpointAggregate,
    CheckpointContent,
    CheckpointId,
    CheckpointRevision,
    CheckpointRevisionId,
    CheckpointStatus,
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    SessionId,
    SourceId,
    SourceTrustClass,
    TaskId,
    VerificationStatus,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.storage import CheckpointRepository
from mnemo_memory.packages.storage.contracts import (
    CheckpointNotFound,
    DuplicateCheckpoint,
    InvalidAbandonmentReason,
    InvalidCheckpointScope,
    InvalidLifecycleTransition,
    RevisionConflict,
)

NOW = datetime(2026, 8, 2, 10, 0, tzinfo=UTC)
HASH = "sha256:" + "e" * 64
RepositoryFactory = Callable[[], CheckpointRepository]


def scope() -> MemoryScope:
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
        source_type=EvidenceSourceType.CHECKPOINT,
        trust_class=SourceTrustClass.USER_AUTHORED,
        immutable_source_ref="synthetic://checkpoint-contract",
        content_hash=HASH,
        location=EvidenceLocation("fixture://checkpoint-contract"),
        observed_at=NOW,
        verification_status=VerificationStatus.VERIFIED,
    )


def content(*, complete: bool = False, suffix: str = "one") -> CheckpointContent:
    return CheckpointContent(
        task_objective="exercise repository contract",
        completed_work=(f"completed-{suffix}",),
        current_state="complete" if complete else "active",
        remaining_work=() if complete else (f"next-{suffix}",),
        decisions=(f"decision-{suffix}",),
        failures=(),
        blockers=(),
        relevant_files=("packages/storage/reference.py",),
        relevant_artifacts=(),
        verification_performed=("pytest",),
        token_estimate=11,
    )


def created(
    scope_value: MemoryScope, *, at: datetime = NOW
) -> tuple[CheckpointAggregate, CheckpointRevision]:
    checkpoint_id = CheckpointId.new()
    revision = CheckpointRevision(
        revision_id=CheckpointRevisionId.new(),
        checkpoint_id=checkpoint_id,
        revision_number=1,
        predecessor_revision_id=None,
        scope=scope_value,
        content=content(),
        status=CheckpointStatus.ACTIVE,
        evidence_references=(evidence(),),
        created_at=at,
    )
    return (
        CheckpointAggregate(
            checkpoint_id=checkpoint_id,
            scope=scope_value,
            current_revision_id=revision.revision_id,
            current_revision_number=1,
            lifecycle_status=CheckpointStatus.ACTIVE,
            created_at=at,
            updated_at=at,
        ),
        revision,
    )


def stored(
    repository_factory: RepositoryFactory,
) -> tuple[CheckpointRepository, MemoryScope, CheckpointAggregate, CheckpointRevision]:
    repository = repository_factory()
    scope_value = scope()
    aggregate, revision = created(scope_value)
    repository.create_checkpoint_aggregate(aggregate, revision)
    return repository, scope_value, aggregate, revision


def test_creation_and_scoped_retrieval(repository_factory: RepositoryFactory) -> None:
    repository, scope_value, aggregate, initial = stored(repository_factory)
    assert repository.get_aggregate(scope_value, aggregate.checkpoint_id) == aggregate
    assert repository.get_current_revision(scope_value, aggregate.checkpoint_id) == initial
    assert (
        repository.get_revision(scope_value, aggregate.checkpoint_id, revision_number=1) == initial
    )
    assert (
        repository.get_revision(
            scope_value, aggregate.checkpoint_id, revision_id=initial.revision_id
        )
        == initial
    )
    with pytest.raises(DuplicateCheckpoint):
        repository.create_checkpoint_aggregate(aggregate, initial)
    with pytest.raises(CheckpointNotFound):
        repository.get_aggregate(scope(), aggregate.checkpoint_id)
    with pytest.raises(CheckpointNotFound):
        repository.get_revision(scope_value, CheckpointId.new(), revision_number=1)


def test_creation_requires_task_scope_and_provenance(repository_factory: RepositoryFactory) -> None:
    repository = repository_factory()
    task_scope = scope()
    aggregate, initial = created(task_scope)
    personal_scope = MemoryScope(OwnerId.new(), ScopeLevel.PERSONAL, Visibility.OWNER)
    with pytest.raises(InvalidCheckpointScope):
        repository.create_checkpoint_aggregate(
            replace(aggregate, scope=personal_scope), replace(initial, scope=personal_scope)
        )
    with pytest.raises(ValueError, match="revision requires"):
        CheckpointRevision(
            revision_id=CheckpointRevisionId.new(),
            checkpoint_id=aggregate.checkpoint_id,
            revision_number=1,
            predecessor_revision_id=None,
            scope=task_scope,
            content=content(),
            status=CheckpointStatus.ACTIVE,
            evidence_references=(),
            created_at=NOW,
        )


def test_append_is_immutable_and_compare_and_swap_safe(
    repository_factory: RepositoryFactory,
) -> None:
    repository, scope_value, aggregate, initial = stored(repository_factory)
    revised = repository.append_revision(
        scope_value,
        aggregate.checkpoint_id,
        initial.revision_id,
        content(suffix="two"),
        (evidence(),),
        NOW + timedelta(minutes=1),
    )
    current = repository.get_current_revision(scope_value, aggregate.checkpoint_id)
    assert current == revised
    assert revised.checkpoint_id == aggregate.checkpoint_id
    assert revised.revision_id != initial.revision_id
    assert revised.revision_number == 2
    assert revised.predecessor_revision_id == initial.revision_id
    assert revised.content == content(suffix="two")
    attribute_name = "current_state"
    with pytest.raises(FrozenInstanceError):
        setattr(revised.content, attribute_name, "mutated")
    assert (
        repository.get_revision(scope_value, aggregate.checkpoint_id, revision_number=1) == initial
    )
    assert not hasattr(repository, "aggregates")
    with pytest.raises(RevisionConflict):
        repository.append_revision(
            scope_value,
            aggregate.checkpoint_id,
            initial.revision_id,
            content(suffix="three"),
            (evidence(),),
            NOW + timedelta(minutes=2),
        )
    assert repository.get_current_revision(scope_value, aggregate.checkpoint_id) == revised
    with pytest.raises(CheckpointNotFound):
        repository.append_revision(
            scope(),
            aggregate.checkpoint_id,
            revised.revision_id,
            content(suffix="wrong-scope"),
            (evidence(),),
            NOW + timedelta(minutes=3),
        )


@pytest.mark.parametrize("terminal", [CheckpointStatus.COMPLETED, CheckpointStatus.ABANDONED])
def test_terminal_transitions_are_immutable_and_idempotent(
    repository_factory: RepositoryFactory, terminal: CheckpointStatus
) -> None:
    repository, scope_value, aggregate, initial = stored(repository_factory)
    terminal_content = content(complete=terminal is CheckpointStatus.COMPLETED, suffix="terminal")
    terminal_evidence = (evidence(),)
    if terminal is CheckpointStatus.COMPLETED:
        result = repository.complete_checkpoint(
            scope_value,
            aggregate.checkpoint_id,
            initial.revision_id,
            terminal_content,
            terminal_evidence,
            NOW + timedelta(minutes=1),
        )
        retry = repository.complete_checkpoint(
            scope_value,
            aggregate.checkpoint_id,
            initial.revision_id,
            terminal_content,
            terminal_evidence,
            NOW + timedelta(minutes=2),
        )
    else:
        result = repository.abandon_checkpoint(
            scope_value,
            aggregate.checkpoint_id,
            initial.revision_id,
            "blocked by fixture",
            terminal_content,
            terminal_evidence,
            NOW + timedelta(minutes=1),
        )
        retry = repository.abandon_checkpoint(
            scope_value,
            aggregate.checkpoint_id,
            initial.revision_id,
            "blocked by fixture",
            terminal_content,
            terminal_evidence,
            NOW + timedelta(minutes=2),
        )
    assert result.status is terminal
    assert retry == result
    assert result.revision_number == 2
    assert (
        repository.get_aggregate(scope_value, aggregate.checkpoint_id).lifecycle_status is terminal
    )
    with pytest.raises(InvalidLifecycleTransition):
        repository.append_revision(
            scope_value,
            aggregate.checkpoint_id,
            result.revision_id,
            content(suffix="after-terminal"),
            (evidence(),),
            NOW + timedelta(minutes=3),
        )


def test_terminal_conflicts_and_abandonment_reason(repository_factory: RepositoryFactory) -> None:
    repository, scope_value, aggregate, initial = stored(repository_factory)
    with pytest.raises(InvalidAbandonmentReason):
        repository.abandon_checkpoint(
            scope_value,
            aggregate.checkpoint_id,
            initial.revision_id,
            " ",
            content(suffix="abandoned"),
            (evidence(),),
            NOW,
        )
    abandoned = repository.abandon_checkpoint(
        scope_value,
        aggregate.checkpoint_id,
        initial.revision_id,
        "stopped by operator",
        content(suffix="abandoned"),
        (evidence(),),
        NOW + timedelta(minutes=1),
    )
    assert abandoned.content.failures == ("stopped by operator",)
    assert abandoned.evidence_references
    with pytest.raises(InvalidLifecycleTransition):
        repository.complete_checkpoint(
            scope_value,
            aggregate.checkpoint_id,
            initial.revision_id,
            content(complete=True, suffix="different"),
            (evidence(),),
            NOW + timedelta(minutes=2),
        )
    with pytest.raises(InvalidLifecycleTransition):
        repository.abandon_checkpoint(
            scope_value,
            aggregate.checkpoint_id,
            initial.revision_id,
            "different reason",
            content(suffix="abandoned"),
            (evidence(),),
            NOW + timedelta(minutes=2),
        )
    assert repository.get_current_revision(scope_value, aggregate.checkpoint_id) == abandoned


def test_stale_and_opposite_terminal_transitions_fail_without_new_revision(
    repository_factory: RepositoryFactory,
) -> None:
    repository, scope_value, aggregate, initial = stored(repository_factory)
    revised = repository.append_revision(
        scope_value,
        aggregate.checkpoint_id,
        initial.revision_id,
        content(suffix="revised"),
        (evidence(),),
        NOW + timedelta(minutes=1),
    )
    with pytest.raises(RevisionConflict):
        repository.complete_checkpoint(
            scope_value,
            aggregate.checkpoint_id,
            initial.revision_id,
            content(complete=True, suffix="stale"),
            (evidence(),),
            NOW + timedelta(minutes=2),
        )
    assert repository.get_current_revision(scope_value, aggregate.checkpoint_id) == revised
    completed = repository.complete_checkpoint(
        scope_value,
        aggregate.checkpoint_id,
        revised.revision_id,
        content(complete=True, suffix="completed"),
        (evidence(),),
        NOW + timedelta(minutes=2),
    )
    with pytest.raises(InvalidLifecycleTransition):
        repository.abandon_checkpoint(
            scope_value,
            aggregate.checkpoint_id,
            completed.revision_id,
            "too late",
            content(suffix="abandoned"),
            (evidence(),),
            NOW + timedelta(minutes=3),
        )
    assert repository.get_current_revision(scope_value, aggregate.checkpoint_id) == completed


def test_listing_is_scoped_stable_and_excludes_terminal(
    repository_factory: RepositoryFactory,
) -> None:
    repository = repository_factory()
    scope_value = scope()
    first, first_revision = created(scope_value, at=NOW)
    second, second_revision = created(scope_value, at=NOW + timedelta(minutes=1))
    repository.create_checkpoint_aggregate(first, first_revision)
    repository.create_checkpoint_aggregate(second, second_revision)
    assert repository.list_current_checkpoints(scope_value, offset=0, limit=1).items == (second,)
    assert repository.list_current_checkpoints(scope_value, offset=0, limit=1).next_offset == 1
    repository.complete_checkpoint(
        scope_value,
        second.checkpoint_id,
        second_revision.revision_id,
        content(complete=True, suffix="complete"),
        (evidence(),),
        NOW + timedelta(minutes=2),
    )
    page = repository.list_current_checkpoints(scope_value)
    assert page.items == (first,)
    assert repository.select_current_checkpoint(scope_value) == first
    repository.abandon_checkpoint(
        scope_value,
        first.checkpoint_id,
        first_revision.revision_id,
        "fixture work ended",
        content(suffix="abandoned"),
        (evidence(),),
        NOW + timedelta(minutes=3),
    )
    assert repository.list_current_checkpoints(scope_value).items == ()
    assert repository.select_current_checkpoint(scope_value) is None
