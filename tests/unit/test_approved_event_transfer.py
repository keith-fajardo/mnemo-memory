"""Portable approved-event history and verified import coverage."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mnemo_memory.packages.application import (
    ApprovedEventExportService,
    ApprovedEventImportService,
    ApprovedEventTransferConflict,
)
from mnemo_memory.packages.domain import (
    ApprovedEpisodicEvent,
    ApprovedEpisodicEventGovernance,
    ApprovedEpisodicEventPinAction,
    ApprovedEventExportBundle,
    ApprovedEventGovernanceKind,
    ApprovedEventKind,
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
from mnemo_memory.packages.storage import (
    ReferenceApprovedEpisodicEventRepository,
    SQLiteCheckpointRepository,
)

NOW = datetime(2026, 8, 6, 5, 0, tzinfo=UTC)


def _scope(seed: int) -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string(f"00000000-0000-0000-0000-{seed:012d}"),
        ScopeLevel.TASK,
        Visibility.PROJECT,
        WorkspaceId.from_string(f"10000000-0000-0000-0000-{seed:012d}"),
        ProjectId.from_string(f"20000000-0000-0000-0000-{seed:012d}"),
        SessionId.from_string(f"30000000-0000-0000-0000-{seed:012d}"),
        TaskId.from_string(f"40000000-0000-0000-0000-{seed:012d}"),
    )


def _evidence(seed: str, *, user: bool = False) -> EvidenceReference:
    return EvidenceReference(
        EvidenceId.new(),
        SourceId.new(),
        EvidenceSourceType.USER_CORRECTION if user else EvidenceSourceType.CHECKPOINT,
        SourceTrustClass.USER_CORRECTION if user else SourceTrustClass.USER_AUTHORED,
        f"approved-transfer:{seed}",
        "sha256:" + seed * 64,
        EvidenceLocation(f"fixture://approved-transfer/{seed}"),
        NOW,
        VerificationStatus.VERIFIED,
    )


def _event(scope: MemoryScope, key: str, at: datetime) -> ApprovedEpisodicEvent:
    return ApprovedEpisodicEvent.create(
        scope=scope,
        kind=ApprovedEventKind.DECISION,
        summary=f"Approved fact {key} remains bounded and evidence backed.",
        source_event_key=key,
        occurred_at=at,
        evidence_references=(_evidence(key[-1]),),
    )


def _governance(
    scope: MemoryScope,
    target: ApprovedEpisodicEvent,
    *,
    kind: ApprovedEventGovernanceKind,
    key: str,
    at: datetime,
    replacement: ApprovedEpisodicEvent | None = None,
) -> ApprovedEpisodicEventGovernance:
    return ApprovedEpisodicEventGovernance.create(
        scope=scope,
        kind=kind,
        target_event_id=target.event_id,
        replacement_event_id=None if replacement is None else replacement.event_id,
        reason="The user explicitly governed this retained fact.",
        source_action_key=key,
        occurred_at=at,
        evidence_references=(_evidence(key[-1]),),
    )


def _populate(
    repository: ReferenceApprovedEpisodicEventRepository | SQLiteCheckpointRepository,
    scope: MemoryScope,
) -> None:
    original = _event(scope, "event:1", NOW)
    replacement = _event(scope, "event:2", NOW + timedelta(minutes=1))
    retracted = _event(scope, "event:3", NOW + timedelta(minutes=2))
    repository.append_approved_event(original)
    repository.set_approved_event_pin(
        ApprovedEpisodicEventPinAction.create(
            scope=scope,
            event_id=original.event_id,
            pinned=True,
            source_action_key="pin:1",
            occurred_at=NOW + timedelta(seconds=1),
            evidence_references=(_evidence("d", user=True),),
        )
    )
    repository.correct_approved_event(
        replacement,
        _governance(
            scope,
            original,
            kind=ApprovedEventGovernanceKind.CORRECTED,
            key="correct:1",
            at=NOW + timedelta(minutes=1),
            replacement=replacement,
        ),
    )
    repository.append_approved_event(retracted)
    repository.set_approved_event_pin(
        ApprovedEpisodicEventPinAction.create(
            scope=scope,
            event_id=retracted.event_id,
            pinned=True,
            source_action_key="pin:3",
            occurred_at=NOW + timedelta(minutes=2, seconds=1),
            evidence_references=(_evidence("e", user=True),),
        )
    )
    repository.retract_approved_event(
        _governance(
            scope,
            retracted,
            kind=ApprovedEventGovernanceKind.RETRACTED,
            key="retract:3",
            at=NOW + timedelta(minutes=3),
        )
    )


def test_approved_event_export_is_complete_strict_and_tamper_evident() -> None:
    scope = _scope(1)
    repository = ReferenceApprovedEpisodicEventRepository()
    _populate(repository, scope)
    bundle = ApprovedEventExportService(repository).export(
        scope, exported_at=NOW + timedelta(minutes=4)
    )

    assert len(bundle.events) == 2
    assert len(bundle.governance_actions) == 2
    assert len(bundle.pin_history) == 5
    assert ApprovedEventExportBundle.from_json(bundle.canonical_json()) == bundle
    assert all(
        item.target_event_id not in {event.event_id for event in bundle.events}
        for item in bundle.governance_actions
        if item.kind is ApprovedEventGovernanceKind.RETRACTED
    )

    tampered = json.loads(bundle.canonical_json())
    tampered["events"][0]["summary"] = "tampered"
    with pytest.raises(ValueError, match="digest"):
        ApprovedEventExportBundle.from_dict(tampered)
    reordered = json.loads(bundle.canonical_json())
    reordered["pin_history"].reverse()
    with pytest.raises(ValueError, match="pin history"):
        ApprovedEventExportBundle.from_dict(reordered)


def test_approved_event_import_rebases_identity_and_is_idempotent() -> None:
    source_scope = _scope(1)
    target_scope = _scope(2)
    source = ReferenceApprovedEpisodicEventRepository()
    _populate(source, source_scope)
    bundle = ApprovedEventExportService(source).export(
        source_scope, exported_at=NOW + timedelta(minutes=4)
    )
    target = ReferenceApprovedEpisodicEventRepository()
    service = ApprovedEventImportService(target, target)

    result = service.import_bundle(bundle, target_scope=target_scope)
    imported = ApprovedEventExportService(target).export(
        target_scope, exported_at=bundle.exported_at
    )

    assert not result.idempotent
    assert (result.event_count, result.governance_count, result.pin_action_count) == (2, 2, 5)
    assert result.source_content_digest == bundle.content_digest
    assert result.target_content_digest == imported.content_digest
    assert result.source_content_digest != result.target_content_digest
    assert all(item.scope == target_scope for item in imported.events)
    assert {item.source_event_key for item in imported.events} == {
        item.source_event_key for item in bundle.events
    }
    assert {item.event_id for item in imported.events}.isdisjoint(
        item.event_id for item in bundle.events
    )
    assert service.import_bundle(bundle, target_scope=target_scope).idempotent

    conflict = ReferenceApprovedEpisodicEventRepository()
    conflict.append_approved_event(_event(target_scope, "existing:9", NOW))
    with pytest.raises(ApprovedEventTransferConflict, match="conflicting state"):
        ApprovedEventImportService(conflict, conflict).import_bundle(
            bundle, target_scope=target_scope
        )


def test_sqlite_export_survives_restart_without_retracted_payload(tmp_path: Path) -> None:
    scope = _scope(1)
    path = tmp_path / "approved-transfer.sqlite3"
    repository = SQLiteCheckpointRepository(path, base_directory=tmp_path)
    repository.migrate()
    _populate(repository, scope)
    first = ApprovedEventExportService(repository).export(
        scope, exported_at=NOW + timedelta(minutes=4)
    )

    restarted = SQLiteCheckpointRepository(path, base_directory=tmp_path)
    restarted.migrate()
    second = ApprovedEventExportService(restarted).export(scope, exported_at=first.exported_at)

    assert second == first
    assert len(second.events) == 2
    assert any(
        item.kind is ApprovedEventGovernanceKind.RETRACTED
        and item.target_event_id not in {event.event_id for event in second.events}
        for item in second.governance_actions
    )
