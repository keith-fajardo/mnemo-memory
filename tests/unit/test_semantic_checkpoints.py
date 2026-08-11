"""Semantic ledger, patch, rendering, and reference-service coverage."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from mnemo_memory.packages.application import (
    DeterministicMemoryCompiler,
    SemanticMemoryService,
)
from mnemo_memory.packages.context_engine import (
    CallableTokenCounter,
    ConservativeTokenCounter,
    detect_protected_spans,
    reduce_checkpoint_phrases,
)
from mnemo_memory.packages.domain import (
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
    SemanticAtomKind,
    SemanticAtomStatus,
    SemanticCheckpointAtom,
    SemanticCheckpointPatch,
    SemanticMemoryAtom,
    SemanticPatchOperation,
    SemanticPatchOperationKind,
    SemanticRendererProfile,
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
    apply_semantic_checkpoint_patch,
)
from mnemo_memory.packages.storage import (
    ReferenceSemanticCheckpointRepository,
    ReferenceTaskActivityEventRepository,
    SemanticCheckpointNotFound,
    TaskActivityEventConflict,
    TaskActivityEventNotFound,
)

NOW = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)


def _scope(seed: int = 1) -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string(f"10000000-0000-4000-8000-{seed:012d}"),
        ScopeLevel.TASK,
        Visibility.PROJECT,
        WorkspaceId.from_string(f"20000000-0000-4000-8000-{seed:012d}"),
        ProjectId.from_string(f"30000000-0000-4000-8000-{seed:012d}"),
        SessionId.from_string(f"40000000-0000-4000-8000-{seed:012d}"),
        TaskId.from_string(f"50000000-0000-4000-8000-{seed:012d}"),
    )


def _evidence(seed: int, at: datetime) -> EvidenceReference:
    return EvidenceReference(
        EvidenceId.from_string(f"60000000-0000-4000-8000-{seed:012d}"),
        SourceId.from_string(f"70000000-0000-4000-8000-{seed:012d}"),
        EvidenceSourceType.AGENT_EVENT,
        SourceTrustClass.APPROVED_CHECKPOINT,
        f"fixture://semantic/{seed}",
        "sha256:" + f"{seed:064x}",
        EvidenceLocation(f"fixture://semantic/{seed}"),
        at,
        VerificationStatus.VERIFIED,
    )


def _event(
    scope: MemoryScope,
    seed: int,
    summary: str,
    *,
    actor: TaskActivityActor = TaskActivityActor.USER,
    kind: TaskActivityEventKind = TaskActivityEventKind.TASK_ACTIVITY,
) -> TaskActivityEvent:
    at = NOW + timedelta(seconds=seed)
    return TaskActivityEvent.create(
        scope=scope,
        kind=kind,
        actor=actor,
        summary=summary,
        source_event_key=f"semantic:{seed}",
        sensitivity=Sensitivity.NORMAL,
        retention=RetentionSchedule(
            RetentionPolicyId.from_string("80000000-0000-4000-8000-000000000001"),
            True,
            at,
            at,
            at,
            None,
            None,
        ),
        occurred_at=at,
        evidence_references=(_evidence(seed, at),),
    )


def _service(
    *, snapshot_interval: int = 3
) -> tuple[SemanticMemoryService, ReferenceTaskActivityEventRepository]:
    events = ReferenceTaskActivityEventRepository()
    checkpoints = ReferenceSemanticCheckpointRepository(events)
    return (
        SemanticMemoryService(
            events,
            checkpoints,
            clock=lambda: NOW + timedelta(hours=1),
            compiler=DeterministicMemoryCompiler(),
            snapshot_interval=snapshot_interval,
        ),
        events,
    )


def test_raw_event_is_immutable_and_conflicting_rewrite_is_rejected() -> None:
    scope = _scope()
    _, events = _service()
    original = _event(scope, 1, "goal: Preserve the immutable evidence archive.")
    events.append_task_activity_event(original)
    changed = replace(original, summary="goal: Rewrite the source evidence.")

    with pytest.raises(TaskActivityEventConflict):
        events.append_task_activity_event(changed)
    assert events.get_task_activity_event(scope, original.event_id) == original


def test_patch_application_is_deterministic_idempotent_and_evidence_validated() -> None:
    scope = _scope()
    event = _event(scope, 1, "constraint: Do not deploy.")
    atom = SemanticMemoryAtom.create(
        scope=scope,
        kind=SemanticAtomKind.CONSTRAINT,
        subject="user",
        predicate="requires",
        object_value="Do not deploy.",
        source_event_ids=(event.event_id,),
        created_at=event.occurred_at,
        priority=100,
    )
    patch = SemanticCheckpointPatch(
        None,
        (
            SemanticPatchOperation(SemanticPatchOperationKind.ADD, atom=atom),
            SemanticPatchOperation(
                SemanticPatchOperationKind.ACTIVATE,
                target_atom_id=atom.atom_id,
            ),
        ),
    )
    first = apply_semantic_checkpoint_patch(
        scope=scope,
        ledger_atoms=(),
        active_references=(),
        patch=patch,
        available_event_ids=frozenset({event.event_id}),
        applied_at=NOW,
    )
    second = apply_semantic_checkpoint_patch(
        scope=scope,
        ledger_atoms=first[0],
        active_references=first[1],
        patch=patch,
        available_event_ids=frozenset({event.event_id}),
        applied_at=NOW,
    )

    assert first == second
    with pytest.raises(ValueError, match="evidence reference"):
        apply_semantic_checkpoint_patch(
            scope=scope,
            ledger_atoms=(),
            active_references=(),
            patch=patch,
            available_event_ids=frozenset(),
            applied_at=NOW,
        )


@pytest.mark.parametrize(
    ("operation_kind", "expected_status"),
    (
        (SemanticPatchOperationKind.RESOLVE, SemanticAtomStatus.RESOLVED),
        (SemanticPatchOperationKind.EXPIRE, SemanticAtomStatus.EXPIRED),
    ),
)
def test_patch_lifecycle_operations_remove_only_whole_active_atom(
    operation_kind: SemanticPatchOperationKind,
    expected_status: SemanticAtomStatus,
) -> None:
    scope = _scope()
    event = _event(scope, 1, "open_question: Is the gate complete?")
    atom = SemanticMemoryAtom.create(
        scope=scope,
        kind=SemanticAtomKind.OPEN_QUESTION,
        subject="user",
        predicate="unresolved",
        object_value="Is the gate complete?",
        source_event_ids=(event.event_id,),
        created_at=event.occurred_at,
    )
    initial = apply_semantic_checkpoint_patch(
        scope=scope,
        ledger_atoms=(),
        active_references=(),
        patch=SemanticCheckpointPatch(
            None,
            (
                SemanticPatchOperation(SemanticPatchOperationKind.ADD, atom=atom),
                SemanticPatchOperation(
                    SemanticPatchOperationKind.ACTIVATE,
                    target_atom_id=atom.atom_id,
                ),
            ),
        ),
        available_event_ids=frozenset({event.event_id}),
        applied_at=NOW,
    )
    final = apply_semantic_checkpoint_patch(
        scope=scope,
        ledger_atoms=initial[0],
        active_references=initial[1],
        patch=SemanticCheckpointPatch(
            None,
            (SemanticPatchOperation(operation_kind, target_atom_id=atom.atom_id),),
        ),
        available_event_ids=frozenset(),
        applied_at=NOW + timedelta(seconds=1),
    )

    assert final[0][0].status is expected_status
    assert final[1] == ()


def test_patch_metadata_update_and_checkpoint_removal_preserve_meaning() -> None:
    scope = _scope()
    event = _event(scope, 1, "fact: Exact meaning remains unchanged.")
    atom = SemanticMemoryAtom.create(
        scope=scope,
        kind=SemanticAtomKind.FACT,
        subject="user",
        predicate="states",
        object_value="Exact meaning remains unchanged.",
        source_event_ids=(event.event_id,),
        created_at=event.occurred_at,
    )
    initial = apply_semantic_checkpoint_patch(
        scope=scope,
        ledger_atoms=(atom,),
        active_references=(SemanticCheckpointAtom(atom.atom_id, "fixture", 50),),
        patch=SemanticCheckpointPatch(
            None,
            (
                SemanticPatchOperation(
                    SemanticPatchOperationKind.UPDATE_METADATA,
                    atom=replace(
                        atom,
                        confidence=0.75,
                        priority=80,
                        updated_at=NOW + timedelta(seconds=1),
                    ),
                    target_atom_id=atom.atom_id,
                ),
                SemanticPatchOperation(
                    SemanticPatchOperationKind.REMOVE,
                    target_atom_id=atom.atom_id,
                ),
            ),
        ),
        available_event_ids=frozenset({event.event_id}),
        applied_at=NOW + timedelta(seconds=1),
    )

    assert initial[0][0].object_value == atom.object_value
    assert initial[0][0].confidence == 0.75
    assert initial[0][0].priority == 80
    assert initial[1] == ()


def test_changed_goal_and_decision_supersede_without_erasing_history() -> None:
    scope = _scope()
    service, _ = _service()
    events = (
        _event(scope, 1, "goal: Ship version 1 after tests."),
        _event(scope, 2, "goal: Ship version 2 after approval."),
        _event(scope, 3, "decision: Use SQLite because personal mode is local."),
        _event(scope, 4, "decision: Use SQLite with WAL because readers must continue."),
    )
    saved = service.save_checkpoint(scope, events=events)
    ledger = service.list_atoms(scope)

    assert saved.checkpoint.checkpoint.checkpoint_type.value == "snapshot"
    assert len(saved.checkpoint.atoms) == 2
    assert {item.object_value for item in saved.checkpoint.atoms} == {
        "Ship version 2 after approval.",
        "Use SQLite with WAL because readers must continue.",
    }
    assert sum(item.status is SemanticAtomStatus.SUPERSEDED for item in ledger) == 2
    assert len(ledger) == 4
    assert all(atom.source_event_ids for atom in ledger)


def test_conflicting_speaker_claims_remain_distinct_and_attributed() -> None:
    scope = _scope()
    service, _ = _service()
    saved = service.save_checkpoint(
        scope,
        events=(
            _event(scope, 1, "fact: The gate passed.", actor=TaskActivityActor.USER),
            _event(scope, 2, "fact: The gate failed.", actor=TaskActivityActor.AGENT),
        ),
    )
    portable = service.recall_memory(
        scope,
        mode=SemanticRendererProfile.PORTABLE,
        preferred_token_target=600,
        maximum_token_ceiling=600,
    )

    assert "subject=user" in portable.text
    assert "subject=agent" in portable.text
    assert "The gate passed." in portable.text
    assert "The gate failed." in portable.text
    assert len(saved.checkpoint.atoms) == 2


@pytest.mark.parametrize(
    ("source", "expected"),
    (
        ("We utilize SQLite.", "We use SQLite."),
        ("We work in order to verify.", "We work to verify."),
    ),
)
def test_phrase_table_rules_are_idempotent(source: str, expected: str) -> None:
    reduced = reduce_checkpoint_phrases(source)
    assert reduced == expected
    assert reduce_checkpoint_phrases(reduced) == reduced


def test_phrase_table_never_changes_protected_spans() -> None:
    protected = (
        'Do not utilize "in order to" inside `utilize`; run git commit -m utilize\n'
        "/tmp/utilize v1.2.3 2026-08-12 42 MB "
        "123e4567-e89b-12d3-a456-426614174000 aabbccddeeff0011."
    )
    spans = detect_protected_spans(protected)
    reduced = reduce_checkpoint_phrases(protected)

    assert spans
    for span in spans:
        assert span.value in reduced
    assert "Do not" in reduced
    assert "`utilize`" in reduced
    assert '"in order to"' in reduced


def test_logical_and_literal_fidelity_survives_compact_rendering_exactly() -> None:
    scope = _scope()
    service, _ = _service()
    meaning = (
        "Do not deploy; may deploy only after user approval if all 3 tests pass. "
        "Uncertain claim: use `uv run pytest -q` in /tmp/mnemo at v1.2.3 on 2026-08-12; "
        "keep 123e4567-e89b-12d3-a456-426614174000, aabbccddeeff0011, and 42 MB exact."
    )
    saved = service.save_checkpoint(scope, events=(_event(scope, 1, f"constraint: {meaning}"),))
    compact = saved.rendering

    assert meaning in compact.text
    assert "by=user" in compact.text
    assert "e=E1" in compact.text
    assert "..." not in compact.text
    assert "…" not in compact.text


def test_old_active_constraint_survives_many_new_optional_events() -> None:
    scope = _scope()
    service, _ = _service()
    events = [_event(scope, 1, "constraint: Never deploy without explicit approval.")]
    events.extend(
        _event(
            scope,
            seed,
            f"fact: Optional historical observation number {seed} was recorded.",
        )
        for seed in range(2, 20)
    )
    service.save_checkpoint(scope, events=tuple(events))
    compact = service.recall_memory(scope)

    assert "Never deploy without explicit approval." in compact.text
    assert compact.omission is not None
    assert compact.omission.omitted_unit_count > 0
    assert compact.omission.retrieval_handles
    assert "OMISSION count=" in compact.text


def test_repeated_checkpoint_without_new_evidence_has_no_drift() -> None:
    scope = _scope()
    service, _ = _service()
    first = service.save_checkpoint(
        scope,
        events=(
            _event(scope, 1, "goal: Continue correctly."),
            _event(scope, 2, "next_action: Run the complete gate."),
        ),
    )
    second = service.save_checkpoint(scope)

    assert second.idempotent is True
    assert second.processed_event_count == 0
    assert second.checkpoint == first.checkpoint
    assert second.rendering.text == first.rendering.text


def test_delta_chain_periodically_materializes_snapshot_with_equal_state() -> None:
    scope = _scope()
    service, _ = _service(snapshot_interval=3)
    first = service.save_checkpoint(scope, events=(_event(scope, 1, "goal: Validate replay."),))
    second = service.save_checkpoint(scope, events=(_event(scope, 2, "fact: Delta two."),))
    third = service.save_checkpoint(scope, events=(_event(scope, 3, "result: Snapshot three."),))

    assert first.checkpoint.checkpoint.checkpoint_type.value == "snapshot"
    assert second.checkpoint.checkpoint.checkpoint_type.value == "delta"
    assert third.checkpoint.checkpoint.checkpoint_type.value == "snapshot"
    assert {item.atom_id for item in third.checkpoint.atoms} == {
        item.atom_id
        for item in service.materialize_snapshot(
            scope, third.checkpoint.checkpoint.checkpoint_id
        ).atoms
    }


def test_dense_checkpoint_expands_past_200_and_never_discards_mandatory_units() -> None:
    scope = _scope()
    service, _ = _service()
    events = tuple(
        _event(
            scope,
            seed,
            "constraint: Mandatory constraint "
            f"{seed} must remain active until explicit approval on 2026-08-12.",
        )
        for seed in range(1, 9)
    )
    service.save_checkpoint(scope, events=events)
    rendered = service.recall_memory(scope)

    assert rendered.measured_tokens > 200
    assert rendered.included_unit_count == len(events)
    assert rendered.omitted_unit_count == 0
    assert all(f"Mandatory constraint {seed}" in rendered.text for seed in range(1, 9))


def test_mandatory_state_can_overrun_600_with_explicit_status() -> None:
    scope = _scope()
    service, _ = _service()
    constraints = tuple(
        _event(
            scope,
            seed,
            "constraint: Must retain this authority boundary after explicit approval "
            + "with exact conditions and qualifiers " * 8
            + str(seed),
        )
        for seed in range(1, 13)
    )
    events = (*constraints, _event(scope, 13, "fact: Optional history remains retrievable."))
    service.save_checkpoint(scope, events=events)
    rendered = service.recall_memory(scope)

    assert rendered.measured_tokens > 600
    assert rendered.mandatory_overrun is True
    assert rendered.included_unit_count == len(constraints)
    assert rendered.omitted_unit_count == 1
    assert rendered.omission is not None
    assert rendered.omission.reason == "mandatory_state_exceeds_ceiling"
    assert rendered.omission.retrieval_handles


def test_portable_is_self_describing_and_audit_expands_provenance() -> None:
    scope = _scope()
    service, _ = _service()
    event = _event(scope, 1, "decision: Use a semantic ledger because provenance matters.")
    saved = service.save_checkpoint(scope, events=(event,))
    portable = service.render_checkpoint(saved.checkpoint, mode=SemanticRendererProfile.PORTABLE)
    audit = service.render_checkpoint(saved.checkpoint, mode=SemanticRendererProfile.AUDIT)

    assert portable.text.startswith("mnemo-checkpoint/1.0")
    assert "decision A1 | subject=user" in portable.text
    assert "evidence=E1" in portable.text
    assert str(event.event_id) not in portable.text
    assert str(event.event_id) in audit.text
    assert str(event.evidence_references[0].evidence_id) in audit.text


def test_provider_tokenizer_measurement_is_used_instead_of_character_count() -> None:
    scope = _scope()
    events = ReferenceTaskActivityEventRepository()
    service = SemanticMemoryService(
        events,
        ReferenceSemanticCheckpointRepository(events),
        clock=lambda: NOW,
        tokenizer=CallableTokenCounter("fixture/word-tokenizer", lambda text: len(text.split())),
    )
    saved = service.save_checkpoint(
        scope, events=(_event(scope, 1, "goal: Measure provider tokens."),)
    )

    assert saved.rendering.target_tokenizer == "fixture/word-tokenizer"
    assert saved.rendering.measured_tokens == len(saved.rendering.text.split())
    assert saved.rendering.measured_tokens != len(saved.rendering.text)


def test_scope_isolation_blocks_cross_tenant_recall_and_evidence() -> None:
    scope = _scope(1)
    other = _scope(2)
    service, _ = _service()
    event = _event(scope, 1, "goal: Remain inside tenant one.")
    saved = service.save_checkpoint(scope, events=(event,))

    with pytest.raises(SemanticCheckpointNotFound):
        service.get_checkpoint(other, saved.checkpoint.checkpoint.checkpoint_id)
    with pytest.raises(TaskActivityEventNotFound):
        service.inspect_evidence(other, (event.event_id,))


def test_conservative_counter_counts_lexical_tokens_not_visible_characters() -> None:
    counter = ConservativeTokenCounter()
    text = "goal: use SQLite"
    assert counter.count(text) < len(text)
    assert counter.count(text) == counter.count(text)
