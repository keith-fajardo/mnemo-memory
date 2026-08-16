"""Semantic ledger, patch, rendering, and reference-service coverage."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from mnemo_memory.packages.application import (
    CheckpointApplicationService,
    CreateCheckpoint,
    DeterministicMemoryCompiler,
    ReviseCheckpoint,
    SemanticLifecycleObservation,
    SemanticMemoryApplicationError,
    SemanticMemoryService,
)
from mnemo_memory.packages.context_engine import (
    CallableTokenCounter,
    ConservativeTokenCounter,
    detect_protected_spans,
    reduce_checkpoint_phrases,
)
from mnemo_memory.packages.domain import (
    CheckpointContent,
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
    ReferenceCheckpointRepository,
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


def test_structured_constraint_is_canonical_field_value_without_guessing_from_prose() -> None:
    scope = _scope()
    service, _ = _service()
    saved = service.save_checkpoint(
        scope,
        events=(
            _event(scope, 1, "constraint: timezone_mode = iana"),
            _event(scope, 2, "constraint: Never deploy without explicit approval."),
            _event(scope, 3, "decision: conflict_status = 409"),
        ),
    )
    by_kind = {
        kind: [atom.object_value for atom in saved.checkpoint.atoms if atom.kind is kind]
        for kind in (SemanticAtomKind.CONSTRAINT, SemanticAtomKind.DECISION)
    }

    assert by_kind[SemanticAtomKind.CONSTRAINT] == [
        "timezone_mode=iana",
        "Never deploy without explicit approval.",
    ]
    assert by_kind[SemanticAtomKind.DECISION] == ["conflict_status=409"]


def test_structured_decisions_supersede_only_the_same_named_field() -> None:
    scope = _scope()
    service, _ = _service()
    saved = service.save_checkpoint(
        scope,
        events=(
            _event(scope, 1, "decision: timezone_mode=offset"),
            _event(scope, 2, "decision: conflict_status=409"),
            _event(scope, 3, "decision: timezone_mode=iana"),
        ),
    )

    assert sorted(atom.object_value for atom in saved.checkpoint.atoms) == [
        "conflict_status=409",
        "timezone_mode=iana",
    ]
    assert (
        sum(atom.status is SemanticAtomStatus.SUPERSEDED for atom in service.list_atoms(scope)) == 1
    )


def test_public_checkpoint_projection_replaces_state_and_preserves_audit_evidence() -> None:
    scope = _scope()
    checkpoints = CheckpointApplicationService(ReferenceCheckpointRepository(), clock=lambda: NOW)
    semantic, _ = _service()
    source = _evidence(1, NOW)
    initial = checkpoints.create(
        CreateCheckpoint(
            scope,
            CheckpointContent(
                task_objective="Complete scheduling for tenant 042.",
                completed_work=("Validated request hash aabbccddeeff0011.",),
                current_state="Uncertain whether the provider can return 409.",
                remaining_work=("Run `uv run pytest -q` within 90 seconds.",),
                decisions=("Use UTC offsets only.",),
                failures=("A duplicate request bypassed the idempotency key.",),
                blockers=("Must not write without scheduler authorization.",),
                relevant_files=("services/scheduling.py",),
                relevant_artifacts=(),
                verification_performed=("3 concurrency checks passed.",),
                token_estimate=180,
            ),
            (source,),
        )
    )
    first = semantic.save_checkpoint_view(initial, retention_days=180)
    revised = checkpoints.revise(
        ReviseCheckpoint(
            scope,
            initial.aggregate.checkpoint_id,
            initial.revision.revision_id,
            replace(
                initial.revision.content,
                decisions=(
                    "Use IANA zone America/New_York; this supersedes the UTC-only decision.",
                ),
                remaining_work=("Run `uv run pytest -q` and inspect status 409.",),
            ),
            (source,),
        )
    )
    second = semantic.save_checkpoint_view(revised, retention_days=180)
    item, provenance = semantic.automatic_context_item(
        scope, preferred_token_target=600, maximum_token_ceiling=800
    )

    assert first.processed_event_count == 8
    assert second.processed_event_count == 2
    assert "Use UTC offsets only." not in item.content
    assert "America/New_York" in item.content
    assert "Must not write without scheduler authorization." in item.content
    assert "Uncertain whether the provider can return 409." in item.content
    assert "epistemic=agent_inference" in item.content
    assert "confidence=0.5" in item.content
    assert "supersedes=" in item.content
    assert "MNEMO_EVIDENCE_TRACE" in item.content
    assert str(source.evidence_id) in item.content
    assert provenance.item_id == item.item_id
    assert provenance.source_reference.startswith("mnemo:semantic-checkpoint/")
    assert (
        sum(atom.status is SemanticAtomStatus.SUPERSEDED for atom in semantic.list_atoms(scope))
        == 1
    )
    retry = semantic.save_checkpoint_view(revised, retention_days=180)
    assert retry.idempotent is True
    assert retry.processed_event_count == 0


def test_changed_current_state_is_volatile_and_supersedes_without_fact_authority() -> None:
    scope = _scope()
    checkpoints = CheckpointApplicationService(ReferenceCheckpointRepository(), clock=lambda: NOW)
    semantic, _ = _service()
    source = _evidence(1, NOW)
    initial = checkpoints.create(
        CreateCheckpoint(
            scope,
            CheckpointContent(
                task_objective="Keep the scheduler configuration current.",
                completed_work=(),
                current_state='fact: Current config {"timezone":"UTC+00:00"}',
                remaining_work=(),
                decisions=(),
                failures=(),
                blockers=(),
                relevant_files=(),
                relevant_artifacts=(),
                verification_performed=(),
                token_estimate=80,
            ),
            (source,),
        )
    )
    semantic.save_checkpoint_view(initial, retention_days=180)
    revised = checkpoints.revise(
        ReviseCheckpoint(
            scope,
            initial.aggregate.checkpoint_id,
            initial.revision.revision_id,
            replace(
                initial.revision.content,
                current_state='fact: Current config {"timezone":"America/New_York"}',
            ),
            (source,),
        )
    )
    semantic.save_checkpoint_view(revised, retention_days=180)
    item, _ = semantic.automatic_context_item(
        scope,
        preferred_token_target=600,
        maximum_token_ceiling=800,
    )

    assert 'Current config {"timezone":"UTC+00:00"}' not in item.content
    assert 'Current config {"timezone":"America/New_York"}' in item.content
    assert "NOW " in item.content
    assert "supersedes=" in item.content
    atoms = semantic.list_atoms(scope)
    assert all(atom.kind is not SemanticAtomKind.FACT for atom in atoms)
    states = [atom for atom in atoms if atom.kind is SemanticAtomKind.STATE]
    assert len(states) == 2
    replacement = next(atom for atom in states if atom.supersedes_atom_id is not None)
    assert replacement.object_value == 'Current config {"timezone":"America/New_York"}'
    assert replacement.supersedes_atom_id in {atom.atom_id for atom in states}


def test_checkpoint_failures_carry_forward_in_one_bounded_recent_avoid_ledger() -> None:
    scope = _scope()
    clock = [NOW]
    checkpoints = CheckpointApplicationService(
        ReferenceCheckpointRepository(), clock=lambda: clock[0]
    )
    semantic, _ = _service()
    source = _evidence(1, NOW)
    view = checkpoints.create(
        CreateCheckpoint(
            scope,
            CheckpointContent(
                task_objective="Complete the migration without repeating failed approaches.",
                completed_work=(),
                current_state="Migration remains active.",
                remaining_work=("Try the next bounded approach.",),
                decisions=(),
                failures=("Approach 01 failed because the transaction was stale.",),
                blockers=(),
                relevant_files=(),
                relevant_artifacts=(),
                verification_performed=(),
                token_estimate=100,
            ),
            (source,),
        )
    )
    semantic.save_checkpoint_view(view, retention_days=180)
    clock[0] += timedelta(seconds=1)
    view = checkpoints.revise(
        ReviseCheckpoint(
            scope,
            view.aggregate.checkpoint_id,
            view.revision.revision_id,
            replace(view.revision.content, failures=()),
            (source,),
        )
    )
    carried_result = semantic.save_checkpoint_view(view, retention_days=180)

    carried = [
        atom for atom in carried_result.checkpoint.atoms if atom.kind is SemanticAtomKind.FAILURE
    ]
    assert [atom.object_value for atom in carried] == [
        "Approach 01 failed because the transaction was stale."
    ]

    latest_result = carried_result
    for number in range(2, 19):
        clock[0] += timedelta(seconds=1)
        view = checkpoints.revise(
            ReviseCheckpoint(
                scope,
                view.aggregate.checkpoint_id,
                view.revision.revision_id,
                replace(
                    view.revision.content,
                    failures=(f"Approach {number:02d} failed because the transaction was stale.",),
                ),
                (source,),
            )
        )
        latest_result = semantic.save_checkpoint_view(view, retention_days=180)

    rendered = semantic.recall_memory(
        scope,
        preferred_token_target=4_000,
        maximum_token_ceiling=5_000,
    )
    failures = [
        atom for atom in semantic.list_atoms(scope) if atom.kind is SemanticAtomKind.FAILURE
    ]
    active_failures = [
        atom for atom in latest_result.checkpoint.atoms if atom.kind is SemanticAtomKind.FAILURE
    ]
    avoid_lines = [line for line in rendered.text.splitlines() if line.startswith("AVOID ")]

    assert len(active_failures) == 16
    assert all(dict(atom.qualifiers).get("critical_uncertainty") == "true" for atom in failures)
    assert "Approach 01 failed" not in rendered.text
    assert "Approach 02 failed" not in rendered.text
    assert "Approach 03 failed" in rendered.text
    assert "Approach 18 failed" in rendered.text
    assert len(avoid_lines) == 1
    assert avoid_lines[0].startswith("AVOID (already failed): ")
    assert rendered.text.splitlines()[-1] == avoid_lines[0]


def test_semantic_lifecycle_observations_separate_cpu_stages_from_model_work() -> None:
    scope = _scope()
    observations: list[SemanticLifecycleObservation] = []
    events = ReferenceTaskActivityEventRepository()
    service = SemanticMemoryService(
        events,
        ReferenceSemanticCheckpointRepository(events),
        clock=lambda: NOW,
        lifecycle_observer=observations.append,
    )
    saved = service.save_checkpoint(
        scope,
        events=(_event(scope, 1, "constraint: Never write without authorization."),),
    )
    service.recall_memory(scope)
    item, _ = service.automatic_context_item(
        scope,
        preferred_token_target=600,
        maximum_token_ceiling=800,
    )

    assert saved.lifecycle is not None
    assert [item.operation for item in observations] == [
        "checkpoint_patch_apply",
        "checkpoint_recall",
        "checkpoint_recall",
        "automatic_context_assembly",
    ]
    for observation in observations:
        value = observation.to_dict()
        assert observation.wall_duration_ns >= 0
        assert observation.deterministic_cpu_ns >= 0
        assert observation.stage_durations_ns
        assert value["model_input_tokens"] == 0
        assert value["model_output_tokens"] == 0
        assert value["local_inference_duration_ns"] == 0
        assert value["human_intervention_count"] == 0
        assert value["external_spend_usd"] == 0.0
    automatic = observations[-1].to_dict()
    injected_tokens = automatic["injected_context_tokens"]
    model_input_tokens = automatic["model_input_tokens"]
    assert isinstance(injected_tokens, int)
    assert isinstance(model_input_tokens, int)
    assert injected_tokens == item.token_estimate
    assert injected_tokens > model_input_tokens


def test_recall_rejects_atoms_after_source_retention_expires() -> None:
    scope = _scope()
    clock = [NOW]
    events = ReferenceTaskActivityEventRepository()
    service = SemanticMemoryService(
        events,
        ReferenceSemanticCheckpointRepository(events),
        clock=lambda: clock[0],
    )
    source = _event(scope, 1, "fact: This short-lived fact must expire.")
    expiring = replace(
        source,
        retention=RetentionSchedule(
            RetentionPolicyId.from_string("80000000-0000-4000-8000-000000000002"),
            False,
            NOW,
            NOW,
            NOW,
            None,
            NOW + timedelta(minutes=1),
        ),
    )
    service.save_checkpoint(scope, events=(expiring,))
    clock[0] = NOW + timedelta(minutes=2)

    with pytest.raises(SemanticCheckpointNotFound, match="no current evidence"):
        service.recall_memory(scope)
    with pytest.raises(SemanticMemoryApplicationError, match="EVIDENCE_EXPIRED"):
        service.inspect_evidence(scope, (expiring.event_id,))


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


def test_compact_render_bookends_terse_guardrails_and_preserves_literals() -> None:
    scope = _scope()
    service, _ = _service()
    protected_id = "123e4567-e89b-12d3-a456-426614174000"
    saved = service.save_checkpoint(
        scope,
        events=(
            _event(scope, 1, "goal: Finish the migration safely."),
            _event(
                scope,
                2,
                f"constraint: Preserve job {protected_id} and exactly 42 items.",
            ),
            _event(scope, 3, "fact: The current migration target is SQLite."),
            _event(
                scope,
                4,
                "next_action: Run uv run pytest tests/unit/test_semantic_checkpoints.py.",
            ),
        ),
    )
    compact = saved.rendering
    lines = compact.text.splitlines()

    assert all(noise not in lines[0] for noise in ("n=", "target=", "omit="))
    constraint = next(line for line in lines if protected_id in line)
    assert constraint.startswith("MUST ")
    assert "42 items" in constraint
    assert lines[-1].startswith("DO ")
    assert "Run uv run pytest tests/unit/test_semantic_checkpoints.py." in lines[-1]
    assert compact.measured_tokens <= 199  # At least 20% below the 249-token legacy fixture.


def test_context_index_resolves_handles_and_queries_only_optional_matches() -> None:
    scope = _scope()
    service, _ = _service()
    events = [
        _event(scope, 1, "goal: Complete the bounded database migration."),
        _event(scope, 2, "next_action: Verify the selected database slice."),
        _event(scope, 3, "fact: SQLite transaction state is ready for tenant alpha."),
    ]
    events.extend(
        _event(
            scope,
            seed,
            f"fact: Historical Redis shard observation {seed} is unrelated background evidence.",
        )
        for seed in range(4, 15)
    )
    saved = service.save_checkpoint(scope, events=tuple(events))

    index, provenance = service.automatic_context_index(scope)
    checkpoint_id = str(saved.checkpoint.checkpoint.checkpoint_id)
    handle = f"memory:{checkpoint_id[:8]}:fact"
    full, _ = service.automatic_context_item(
        scope,
        preferred_token_target=2_000,
        maximum_token_ceiling=2_000,
    )
    queried, _ = service.automatic_context_item(
        scope,
        query_or_task="SQLite transaction",
        preferred_token_target=600,
        maximum_token_ceiling=800,
    )
    fact_slice, _ = service.automatic_context_item(
        scope,
        handle=handle,
        preferred_token_target=2_000,
        maximum_token_ceiling=2_000,
    )

    assert index.content.startswith(f"MNEMO_INDEX_V1 rev={checkpoint_id[:8]}")
    assert "goal1" in index.content
    assert "fact12" in index.content
    assert "next1" in index.content
    assert f"handle=memory:{checkpoint_id[:8]}:<kind>" in index.content
    assert index.token_estimate <= 80
    assert index.token_estimate * 8 <= full.token_estimate
    assert provenance.item_id == index.item_id
    assert "SQLite transaction state" in queried.content
    assert "Historical Redis shard" not in queried.content
    assert "Complete the bounded database migration." in queried.content
    assert "Verify the selected database slice." in queried.content
    assert "SQLite transaction state" in fact_slice.content
    assert "Historical Redis shard observation 14" in fact_slice.content
    assert "Complete the bounded database migration." not in fact_slice.content
    assert "Verify the selected database slice." not in fact_slice.content


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
