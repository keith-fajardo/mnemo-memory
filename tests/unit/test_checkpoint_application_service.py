from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from mnemo_memory.packages.application import (
    AbandonCheckpoint,
    CheckpointApplicationBudgetExceeded,
    CheckpointApplicationDuplicate,
    CheckpointApplicationEpisodicEventConflict,
    CheckpointApplicationEpisodicEventNotFound,
    CheckpointApplicationInvalidContent,
    CheckpointApplicationInvalidLifecycle,
    CheckpointApplicationInvalidScope,
    CheckpointApplicationMissingProvenance,
    CheckpointApplicationNotFound,
    CheckpointApplicationRevisionConflict,
    CheckpointApplicationService,
    CheckpointApplicationStorageFailure,
    CheckpointView,
    CompleteCheckpoint,
    CorrectApprovedEpisodicEvent,
    CreateCheckpoint,
    ExpireCheckpoint,
    GetApprovedEpisodicEventRecord,
    GetCheckpoint,
    GetCheckpointContext,
    ListApprovedEpisodicEventRecords,
    RecordApprovedEpisodicEvent,
    RecordCheckpointLesson,
    RetractApprovedEpisodicEvent,
    ReviseCheckpoint,
)
from mnemo_memory.packages.domain import (
    ApprovedEventKind,
    ApprovedEventLifecycleStatus,
    CheckpointContent,
    CheckpointId,
    CheckpointLesson,
    CheckpointRevisionId,
    CheckpointStatus,
    ContextBudget,
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    MemoryScope,
    OwnerId,
    ProjectId,
    RequestId,
    ScopeLevel,
    SessionId,
    SourceId,
    SourceTrustClass,
    TaskId,
    VerificationStatus,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.storage import ReferenceCheckpointRepository
from mnemo_memory.packages.storage.contracts import RepositoryStorageFailure

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
HASH = "sha256:" + "a" * 64


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
        EvidenceId.new(),
        SourceId.new(),
        EvidenceSourceType.CHECKPOINT,
        SourceTrustClass.USER_AUTHORED,
        "synthetic://application-service",
        HASH,
        EvidenceLocation("fixture://application-service"),
        NOW,
        VerificationStatus.VERIFIED,
    )


def content(*, tokens: int = 100, complete: bool = False, suffix: str = "one") -> CheckpointContent:
    return CheckpointContent(
        task_objective="Resume the approved task",
        completed_work=(f"completed {suffix}",),
        current_state="complete" if complete else "active",
        remaining_work=() if complete else (f"next {suffix}",),
        decisions=(f"decision {suffix}",),
        failures=(),
        blockers=(),
        relevant_files=("packages/application/checkpoints.py",),
        relevant_artifacts=(),
        verification_performed=("pytest",),
        token_estimate=tokens,
    )


def service(
    repository: ReferenceCheckpointRepository | None = None,
) -> CheckpointApplicationService:
    ticks = iter(NOW + timedelta(minutes=number) for number in range(50))
    ids = iter(UUID(int=number + 1) for number in range(50))
    revisions = iter(UUID(int=number + 101) for number in range(50))
    requests = iter(UUID(int=number + 201) for number in range(50))
    target_repository = repository or ReferenceCheckpointRepository()
    return CheckpointApplicationService(
        target_repository,
        clock=lambda: next(ticks),
        event_repository=target_repository.events,
        approved_event_repository=target_repository.approved_events,
        checkpoint_id_factory=lambda: CheckpointId(next(ids)),
        revision_id_factory=lambda: CheckpointRevisionId(next(revisions)),
        request_id_factory=lambda: RequestId(next(requests)),
    )


def create(target: CheckpointApplicationService, scope_value: MemoryScope) -> CheckpointView:
    return target.create(CreateCheckpoint(scope_value, content(), (evidence(),)))


def test_create_revise_get_and_stale_revision_are_typed() -> None:
    target = service()
    scope_value = scope()
    initial = create(target, scope_value)
    assert initial.revision.revision_number == 1
    revised = target.revise(
        ReviseCheckpoint(
            scope_value,
            initial.aggregate.checkpoint_id,
            initial.revision.revision_id,
            content(suffix="two"),
            (evidence(),),
        )
    )
    assert revised.revision.revision_number == 2
    assert revised.revision.predecessor_revision_id == initial.revision.revision_id
    historical = target.get(
        GetCheckpoint(scope_value, initial.aggregate.checkpoint_id, revision_number=1)
    )
    assert historical.revision.content == initial.revision.content
    with pytest.raises(CheckpointApplicationRevisionConflict):
        target.revise(
            ReviseCheckpoint(
                scope_value,
                initial.aggregate.checkpoint_id,
                initial.revision.revision_id,
                content(suffix="stale"),
                (evidence(),),
            )
        )


def test_optional_lifecycle_history_is_bounded_and_provenance_bearing() -> None:
    target = service()
    scope_value = scope()
    initial = create(target, scope_value)
    target.revise(
        ReviseCheckpoint(
            scope_value,
            initial.aggregate.checkpoint_id,
            initial.revision.revision_id,
            content(suffix="two"),
            (evidence(),),
        )
    )

    ordinary = target.get_context(GetCheckpointContext(scope_value))
    history = target.get_context(
        GetCheckpointContext(scope_value, include_lifecycle_events=True, maximum_lifecycle_events=2)
    )

    assert ordinary.episodic_memories == ()
    assert [json.loads(item.content)["event_kind"] for item in history.episodic_memories] == [
        "checkpoint_revised",
        "checkpoint_created",
    ]
    assert all(item.evidence_references for item in history.episodic_memories)
    assert len(history.provenance) == 3


def test_explicit_approved_events_are_idempotent_evidenced_and_opt_in() -> None:
    target = service()
    scope_value = scope()
    create(target, scope_value)
    command = RecordApprovedEpisodicEvent(
        scope_value,
        ApprovedEventKind.FAILURE,
        "The comparison used a stale finance seed and must be rerun at source grain.",
        "reconciliation:stale-seed:1",
        (evidence(),),
    )

    stored = target.record_approved_event(command)
    assert stored.idempotent is False
    assert target.record_approved_event(command).idempotent is True
    assert target.get_context(GetCheckpointContext(scope_value)).episodic_memories == ()
    packet = target.get_context(GetCheckpointContext(scope_value, include_approved_events=True))
    facts = [
        json.loads(item.content)
        for item in packet.episodic_memories
        if item.item_id.startswith("approved-episodic:")
    ]
    assert facts == [
        {
            "event_kind": "failure",
            "occurred_at": stored.event.occurred_at.isoformat(),
            "summary": command.summary,
        }
    ]
    assert any(item.item_id.startswith("approved-episodic:") for item in packet.provenance)
    with pytest.raises(CheckpointApplicationEpisodicEventConflict):
        target.record_approved_event(
            RecordApprovedEpisodicEvent(
                scope_value,
                ApprovedEventKind.FAILURE,
                "A different fact must not replace the original one.",
                command.source_event_key,
                (evidence(),),
            )
        )


def test_approved_event_correction_and_retraction_govern_context_and_review() -> None:
    target = service()
    scope_value = scope()
    original = target.record_approved_event(
        RecordApprovedEpisodicEvent(
            scope_value,
            ApprovedEventKind.DECISION,
            "Use the account grain for the reconciliation.",
            "reconciliation:grain:original",
            (evidence(),),
        )
    ).event
    correction = CorrectApprovedEpisodicEvent(
        scope_value,
        original.event_id,
        "Use the verified transaction grain for the reconciliation.",
        "reconciliation:grain:corrected",
        "The account-grain statement was disproved by the verified fixture.",
        "reconciliation:grain:correction-action",
        (evidence(),),
    )

    corrected = target.correct_approved_event(correction)
    assert corrected.idempotent is False
    assert target.correct_approved_event(correction).idempotent is True
    assert corrected.target.status is ApprovedEventLifecycleStatus.CORRECTED
    assert corrected.replacement is not None
    replacement = corrected.replacement.event
    assert replacement is not None
    packet = target.get_context(GetCheckpointContext(scope_value, include_approved_events=True))
    assert len(packet.episodic_memories) == 1
    assert "verified transaction grain" in packet.episodic_memories[0].content
    assert "account grain" not in packet.episodic_memories[0].content
    records = target.list_approved_event_records(ListApprovedEpisodicEventRecords(scope_value))
    assert [item.status for item in records.items] == [
        ApprovedEventLifecycleStatus.ACTIVE,
        ApprovedEventLifecycleStatus.CORRECTED,
    ]

    retraction = RetractApprovedEpisodicEvent(
        scope_value,
        replacement.event_id,
        "The user withdrew the corrected fact from durable episodic memory.",
        "reconciliation:grain:retraction-action",
        (evidence(),),
    )
    retracted = target.retract_approved_event(retraction)
    assert retracted.idempotent is False
    assert target.retract_approved_event(retraction).idempotent is True
    assert (
        target.get_context(
            GetCheckpointContext(scope_value, include_approved_events=True)
        ).episodic_memories
        == ()
    )
    record = target.get_approved_event_record(
        GetApprovedEpisodicEventRecord(scope_value, replacement.event_id)
    )
    assert record.status is ApprovedEventLifecycleStatus.RETRACTED
    assert record.event is None


def test_approved_event_governance_rejects_stale_and_cross_scope_actions() -> None:
    target = service()
    scope_value = scope()
    original = target.record_approved_event(
        RecordApprovedEpisodicEvent(
            scope_value,
            ApprovedEventKind.FAILURE,
            "A stale comparison failed.",
            "comparison:failure:original",
            (evidence(),),
        )
    ).event
    target.retract_approved_event(
        RetractApprovedEpisodicEvent(
            scope_value,
            original.event_id,
            "The user withdrew the failed comparison fact.",
            "comparison:failure:retract",
            (evidence(),),
        )
    )
    with pytest.raises(CheckpointApplicationEpisodicEventNotFound):
        target.correct_approved_event(
            CorrectApprovedEpisodicEvent(
                scope_value,
                original.event_id,
                "A competing replacement.",
                "comparison:failure:replacement",
                "A stale action must not replace a retracted fact.",
                "comparison:failure:stale-correction",
                (evidence(),),
            )
        )
    other_scope = scope()
    with pytest.raises(CheckpointApplicationEpisodicEventNotFound):
        target.get_approved_event_record(
            GetApprovedEpisodicEventRecord(other_scope, original.event_id)
        )
    assert (
        target.list_approved_event_records(ListApprovedEpisodicEventRecords(other_scope)).items
        == ()
    )


def test_episodic_lifecycle_and_approved_facts_share_one_hard_budget() -> None:
    target = service()
    scope_value = scope()
    initial = create(target, scope_value)
    target.revise(
        ReviseCheckpoint(
            scope_value,
            initial.aggregate.checkpoint_id,
            initial.revision.revision_id,
            content(suffix="revised"),
            (evidence(),),
        )
    )
    target.record_approved_event(
        RecordApprovedEpisodicEvent(
            scope_value,
            ApprovedEventKind.DECISION,
            "Use the verified source grain before changing the reconciliation join.",
            "reconciliation:grain:1",
            (evidence(),),
        )
    )
    packet = target.get_context(
        GetCheckpointContext(
            scope_value,
            budget=ContextBudget(episodic_memories=1),
            include_lifecycle_events=True,
            include_approved_events=True,
        )
    )
    assert packet.episodic_memories == ()
    assert packet.active_task_checkpoint is not None
    assert packet.declared_total_tokens == packet.active_task_checkpoint.token_estimate
    assert {omission.item_id for omission in packet.omissions} == {
        "checkpoint-lifecycle:" + str(initial.aggregate.checkpoint_id),
        "approved-episodic-events",
    }


def test_approved_fact_is_available_without_an_active_checkpoint() -> None:
    target = service()
    scope_value = scope()
    target.record_approved_event(
        RecordApprovedEpisodicEvent(
            scope_value,
            ApprovedEventKind.TOOL_OUTCOME,
            "The scoped validation completed successfully before the handoff was created.",
            "validation:success:1",
            (evidence(),),
        )
    )
    packet = target.get_context(GetCheckpointContext(scope_value, include_approved_events=True))
    assert packet.active_task_checkpoint is None
    assert len(packet.episodic_memories) == 1
    assert "validation completed successfully" in packet.episodic_memories[0].content


def test_terminal_lifecycle_and_idempotent_retry() -> None:
    target = service()
    scope_value = scope()
    initial = create(target, scope_value)
    terminal = CompleteCheckpoint(
        scope_value,
        initial.aggregate.checkpoint_id,
        initial.revision.revision_id,
        content(complete=True, suffix="done"),
        (evidence(),),
    )
    completed = target.complete(terminal)
    assert completed.aggregate.lifecycle_status is CheckpointStatus.COMPLETED
    assert target.complete(terminal).revision == completed.revision
    with pytest.raises(CheckpointApplicationInvalidLifecycle):
        target.abandon(
            AbandonCheckpoint(
                scope_value,
                initial.aggregate.checkpoint_id,
                completed.revision.revision_id,
                "different terminal operation",
                content(suffix="abandoned"),
                (evidence(),),
            )
        )


def test_expiry_preserves_current_handoff_and_removes_it_from_selection() -> None:
    repository = ReferenceCheckpointRepository()
    target = service(repository)
    scope_value = scope()
    initial = create(target, scope_value)
    command = ExpireCheckpoint(
        scope_value,
        initial.aggregate.checkpoint_id,
        initial.revision.revision_id,
    )

    expired = target.expire(command)

    assert expired.aggregate.lifecycle_status is CheckpointStatus.EXPIRED
    assert expired.revision.status is CheckpointStatus.EXPIRED
    assert expired.revision.content == initial.revision.content
    assert expired.revision.evidence_references == initial.revision.evidence_references
    assert repository.select_current_checkpoint(scope_value) is None
    assert target.expire(command).revision == expired.revision


def test_abandonment_requires_reason_and_blocks_future_revisions() -> None:
    target = service()
    scope_value = scope()
    initial = create(target, scope_value)
    with pytest.raises(CheckpointApplicationInvalidContent):
        target.abandon(
            AbandonCheckpoint(
                scope_value,
                initial.aggregate.checkpoint_id,
                initial.revision.revision_id,
                " ",
                content(),
                (evidence(),),
            )
        )
    abandoned = target.abandon(
        AbandonCheckpoint(
            scope_value,
            initial.aggregate.checkpoint_id,
            initial.revision.revision_id,
            "waiting for a decision",
            content(suffix="abandoned"),
            (evidence(),),
        )
    )
    assert abandoned.revision.status is CheckpointStatus.ABANDONED
    with pytest.raises(CheckpointApplicationInvalidLifecycle):
        target.revise(
            ReviseCheckpoint(
                scope_value,
                initial.aggregate.checkpoint_id,
                abandoned.revision.revision_id,
                content(suffix="late"),
                (evidence(),),
            )
        )


def test_write_validation_and_repository_error_translation() -> None:
    target = service()
    scope_value = scope()
    with pytest.raises(CheckpointApplicationMissingProvenance):
        target.create(CreateCheckpoint(scope_value, content(), ()))
    with pytest.raises(CheckpointApplicationBudgetExceeded):
        target.create(CreateCheckpoint(scope_value, content(tokens=601), (evidence(),)))
    with pytest.raises(CheckpointApplicationInvalidScope):
        target.create(
            CreateCheckpoint(
                MemoryScope(OwnerId.new(), ScopeLevel.PERSONAL, Visibility.OWNER),
                content(),
                (evidence(),),
            )
        )
    initial = create(target, scope_value)
    with pytest.raises(CheckpointApplicationDuplicate):
        target.create(
            CreateCheckpoint(scope_value, content(), (evidence(),), initial.aggregate.checkpoint_id)
        )
    with pytest.raises(CheckpointApplicationNotFound):
        target.get(GetCheckpoint(scope_value, CheckpointId.new()))


def test_context_uses_exact_current_revision_and_is_stable() -> None:
    target = service()
    scope_value = scope()
    initial = create(target, scope_value)
    revised = target.revise(
        ReviseCheckpoint(
            scope_value,
            initial.aggregate.checkpoint_id,
            initial.revision.revision_id,
            content(tokens=600, suffix="two"),
            (evidence(),),
        )
    )
    packet = target.get_context(GetCheckpointContext(scope_value))
    assert packet.active_task_checkpoint is not None
    assert str(revised.revision.revision_id) in packet.active_task_checkpoint.item_id
    assert packet.active_task_checkpoint.token_estimate == 600
    assert packet.provenance[0].source_reference.endswith(str(revised.revision.revision_id))
    assert json.loads(packet.active_task_checkpoint.content) == revised.revision.content.to_dict()
    assert packet.to_json() == packet.to_json()


def test_record_lesson_appends_current_handoff_without_resubmitting_it() -> None:
    target = service()
    scope_value = scope()
    initial_evidence = evidence()
    initial = target.create(CreateCheckpoint(scope_value, content(), (initial_evidence,)))
    lesson_evidence = evidence()
    lesson = CheckpointLesson(
        "The focused validation test contradicted the per-command catch.",
        "Every CLI command should translate its own ValueError.",
        "Keep validation in LocalConfig and translate at the CLI boundary.",
        "Check the shared validation owner before adding a CLI exception catch.",
        (lesson_evidence.evidence_id,),
    )
    recorded = target.record_lesson(
        RecordCheckpointLesson(
            scope_value,
            initial.aggregate.checkpoint_id,
            initial.revision.revision_id,
            lesson,
            (lesson_evidence,),
        )
    )
    assert recorded.revision.revision_number == 2
    assert recorded.revision.content.task_objective == initial.revision.content.task_objective
    assert recorded.revision.content.lessons == (lesson,)
    assert recorded.revision.evidence_references == (initial_evidence, lesson_evidence)
    assert recorded.revision.content.token_estimate <= 600

    retried = target.record_lesson(
        RecordCheckpointLesson(
            scope_value,
            initial.aggregate.checkpoint_id,
            recorded.revision.revision_id,
            lesson,
            (lesson_evidence,),
        )
    )
    assert retried.revision == recorded.revision

    with pytest.raises(CheckpointApplicationRevisionConflict):
        target.record_lesson(
            RecordCheckpointLesson(
                scope_value,
                initial.aggregate.checkpoint_id,
                initial.revision.revision_id,
                lesson,
                (lesson_evidence,),
            )
        )


def test_record_lesson_rejects_missing_evidence_terminal_and_full_lesson_history() -> None:
    target = service()
    scope_value = scope()
    initial_evidence = evidence()
    initial = target.create(CreateCheckpoint(scope_value, content(), (initial_evidence,)))
    missing_evidence_lesson = CheckpointLesson(
        "A test contradicted the selected validation boundary.",
        "The existing evidence could support a new unrelated conclusion.",
        "Attach evidence for the exact correction before saving it.",
        "Keep every lesson tied to evidence retained by its revision.",
        (EvidenceId.new(),),
    )
    with pytest.raises(CheckpointApplicationMissingProvenance):
        target.record_lesson(
            RecordCheckpointLesson(
                scope_value,
                initial.aggregate.checkpoint_id,
                initial.revision.revision_id,
                missing_evidence_lesson,
                (initial_evidence,),
            )
        )

    completed = target.complete(
        CompleteCheckpoint(
            scope_value,
            initial.aggregate.checkpoint_id,
            initial.revision.revision_id,
            content(complete=True),
            (evidence(),),
        )
    )
    lesson_evidence = evidence()
    terminal_lesson = CheckpointLesson(
        "A terminal checkpoint cannot be amended.",
        "A completed task remains an active handoff.",
        "Start a new checkpoint for follow-up work.",
        "Do not append lifecycle history after a terminal transition.",
        (lesson_evidence.evidence_id,),
    )
    with pytest.raises(CheckpointApplicationInvalidLifecycle):
        target.record_lesson(
            RecordCheckpointLesson(
                scope_value,
                initial.aggregate.checkpoint_id,
                completed.revision.revision_id,
                terminal_lesson,
                (lesson_evidence,),
            )
        )

    full_target = service()
    full_scope = scope()
    full_evidence = evidence()
    full_content = replace(
        content(),
        lessons=tuple(
            CheckpointLesson(
                f"trigger {index}",
                f"assumption {index}",
                f"correction {index}",
                f"prevention {index}",
                (full_evidence.evidence_id,),
            )
            for index in range(16)
        ),
    )
    full = full_target.create(CreateCheckpoint(full_scope, full_content, (full_evidence,)))
    with pytest.raises(CheckpointApplicationInvalidContent, match="maximum number of lessons"):
        full_target.record_lesson(
            RecordCheckpointLesson(
                full_scope,
                full.aggregate.checkpoint_id,
                full.revision.revision_id,
                terminal_lesson,
                (lesson_evidence,),
            )
        )


def test_context_retains_a_prior_evidence_backed_lesson_after_revision() -> None:
    target = service()
    scope_value = scope()
    first_evidence = evidence()
    lesson = CheckpointLesson(
        "The reconciliation test diverged after the timestamp join.",
        "The source values were assumed to use the same date grain.",
        "Use the documented business-date grain for the comparison.",
        "Verify grain and null behavior before proposing a join change.",
        (first_evidence.evidence_id,),
    )
    initial = target.create(
        CreateCheckpoint(scope_value, replace(content(), lessons=(lesson,)), (first_evidence,))
    )
    revised = target.revise(
        ReviseCheckpoint(
            scope_value,
            initial.aggregate.checkpoint_id,
            initial.revision.revision_id,
            content(suffix="next"),
            (evidence(),),
        )
    )
    assert revised.revision.content.lessons == ()

    packet = target.get_context(GetCheckpointContext(scope_value))
    assert len(packet.episodic_memories) == 1
    item = packet.episodic_memories[0]
    remembered = json.loads(item.content)
    assert remembered["lesson"] == lesson.to_dict()
    assert remembered["revision_id"] == str(initial.revision.revision_id)
    assert item.evidence_references == (first_evidence,)
    assert item.validity.value == "unknown"
    assert packet.provenance[1].source_reference.endswith("/lesson/0")
    assert str(initial.revision.revision_id) in packet.provenance[1].source_reference
    assert packet.declared_total_tokens == packet.computed_total_tokens


def test_context_omits_historical_lesson_when_episodic_budget_is_exhausted() -> None:
    target = service()
    scope_value = scope()
    first_evidence = evidence()
    lesson = CheckpointLesson(
        "A test contradicted the selected reconciliation join.",
        "The two values were assumed to share a date grain.",
        "Use the documented business-date grain.",
        "Verify input grain before changing a reconciliation join.",
        (first_evidence.evidence_id,),
    )
    initial = target.create(
        CreateCheckpoint(scope_value, replace(content(), lessons=(lesson,)), (first_evidence,))
    )
    target.revise(
        ReviseCheckpoint(
            scope_value,
            initial.aggregate.checkpoint_id,
            initial.revision.revision_id,
            content(suffix="next"),
            (evidence(),),
        )
    )

    packet = target.get_context(
        GetCheckpointContext(scope_value, budget=ContextBudget(episodic_memories=1))
    )
    assert packet.active_task_checkpoint is not None
    assert packet.episodic_memories == ()
    assert packet.omissions[0].item_id == "checkpoint-lesson-history"
    assert packet.omissions[0].reason.value == "token_budget"


def test_context_empty_omits_terminal_and_over_budget_content() -> None:
    target = service()
    scope_value = scope()
    assert target.get_context(GetCheckpointContext(scope_value)).active_task_checkpoint is None
    initial = create(target, scope_value)
    packet = target.get_context(
        GetCheckpointContext(
            scope_value, initial.aggregate.checkpoint_id, ContextBudget(active_task_checkpoint=99)
        )
    )
    assert packet.active_task_checkpoint is None
    assert packet.omissions[0].reason.value == "token_budget"
    target.complete(
        CompleteCheckpoint(
            scope_value,
            initial.aggregate.checkpoint_id,
            initial.revision.revision_id,
            content(complete=True),
            (evidence(),),
        )
    )
    assert target.get_context(GetCheckpointContext(scope_value)).active_task_checkpoint is None


def test_context_total_limit_and_cross_scope_do_not_disclose_checkpoint() -> None:
    target = service()
    scope_value = scope()
    initial = create(target, scope_value)
    packet = target.get_context(
        GetCheckpointContext(
            scope_value, initial.aggregate.checkpoint_id, ContextBudget(total_limit=99)
        )
    )
    assert packet.active_task_checkpoint is None
    with pytest.raises(CheckpointApplicationNotFound) as cross_scope:
        target.get(GetCheckpoint(scope(), initial.aggregate.checkpoint_id))
    with pytest.raises(CheckpointApplicationNotFound) as unknown:
        target.get(GetCheckpoint(scope_value, CheckpointId.new()))
    assert str(cross_scope.value) == str(unknown.value)


def test_application_boundary_has_no_adapter_or_client_imports() -> None:
    text = Path("src/mnemo_memory/packages/application/checkpoints.py").read_text()
    forbidden = ("sqlite", "mcp", "fastapi", "typer", "apps.", "connectors.")
    assert not any(term in text.lower() for term in forbidden)


class FailingRepository(ReferenceCheckpointRepository):
    def create_checkpoint_aggregate(self, *args: object) -> None:
        raise RepositoryStorageFailure("private database detail")


def test_storage_failure_is_safe_and_keeps_cause() -> None:
    target = service(FailingRepository())
    with pytest.raises(CheckpointApplicationStorageFailure) as raised:
        target.create(CreateCheckpoint(scope(), content(), (evidence(),)))
    assert "database" not in str(raised.value)
    assert isinstance(raised.value.__cause__, RepositoryStorageFailure)
