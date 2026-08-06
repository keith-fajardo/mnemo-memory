"""Portable approved-event export and verified personal-to-team import."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from mnemo_memory.packages.domain import (
    ApprovedEpisodicEvent,
    ApprovedEpisodicEventGovernance,
    ApprovedEpisodicEventPinAction,
    ApprovedEventExportBundle,
    EventId,
    MemoryScope,
    ScopeLevel,
    approved_event_import_identity,
)
from mnemo_memory.packages.storage.contracts import (
    ApprovedEpisodicEventRepositoryError,
    ApprovedEventExportRepository,
    ApprovedEventImportConflict,
    ApprovedEventImportRepository,
)


class ApprovedEventTransferError(Exception):
    """Safe application outcome for approved-event transfer."""


class ApprovedEventTransferConflict(ApprovedEventTransferError):
    pass


class ApprovedEventTransferStorageFailure(ApprovedEventTransferError):
    pass


@dataclass(frozen=True, slots=True)
class ApprovedEventTransferResult:
    source_content_digest: str
    target_content_digest: str
    event_count: int
    governance_count: int
    pin_action_count: int
    idempotent: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "source_content_digest": self.source_content_digest,
            "target_content_digest": self.target_content_digest,
            "event_count": self.event_count,
            "governance_count": self.governance_count,
            "pin_action_count": self.pin_action_count,
            "idempotent": self.idempotent,
        }


class ApprovedEventExportService:
    def __init__(self, repository: ApprovedEventExportRepository) -> None:
        self._repository = repository

    def export(self, scope: MemoryScope, *, exported_at: datetime) -> ApprovedEventExportBundle:
        if not isinstance(scope, MemoryScope) or scope.level is not ScopeLevel.TASK:
            raise ValueError("approved event export requires exact task scope")
        try:
            return self._repository.export_approved_event_history(scope, exported_at=exported_at)
        except ApprovedEpisodicEventRepositoryError as error:
            raise ApprovedEventTransferStorageFailure(
                "approved event export storage operation failed"
            ) from error


class ApprovedEventImportService:
    def __init__(
        self,
        exports: ApprovedEventExportRepository,
        imports: ApprovedEventImportRepository,
    ) -> None:
        self._exports = exports
        self._imports = imports

    def import_bundle(
        self, bundle: ApprovedEventExportBundle, *, target_scope: MemoryScope
    ) -> ApprovedEventTransferResult:
        if not isinstance(bundle, ApprovedEventExportBundle):
            raise TypeError("approved event import requires a validated export bundle")
        if not isinstance(target_scope, MemoryScope) or target_scope.level is not ScopeLevel.TASK:
            raise ValueError("approved event import requires exact target task scope")
        expected = rebase_approved_event_bundle(bundle, target_scope)
        try:
            before = self._exports.export_approved_event_history(
                target_scope, exported_at=bundle.exported_at
            )
            if _same_bundle(before, expected):
                return _result(bundle, before, idempotent=True)
            if before.events or before.governance_actions or before.pin_history:
                raise ApprovedEventTransferConflict(
                    "approved event import target contains conflicting state"
                )
            stored = self._imports.import_approved_event_history(bundle, expected)
            after = self._exports.export_approved_event_history(
                target_scope, exported_at=bundle.exported_at
            )
        except ApprovedEventTransferError:
            raise
        except ApprovedEventImportConflict as error:
            raise ApprovedEventTransferConflict(str(error)) from error
        except ApprovedEpisodicEventRepositoryError as error:
            raise ApprovedEventTransferStorageFailure(
                "approved event import storage operation failed"
            ) from error
        if not _same_bundle(after, expected):
            raise ApprovedEventTransferConflict(
                "approved event import target counts or canonical state do not match"
            )
        return _result(bundle, after, idempotent=stored.idempotent)


def rebase_approved_event_bundle(
    bundle: ApprovedEventExportBundle, target_scope: MemoryScope
) -> ApprovedEventExportBundle:
    mapped: dict[EventId, EventId] = {}
    events: list[ApprovedEpisodicEvent] = []
    for item in bundle.events:
        target = ApprovedEpisodicEvent.create(
            scope=target_scope,
            kind=item.kind,
            summary=item.summary,
            source_event_key=item.source_event_key,
            occurred_at=item.occurred_at,
            evidence_references=item.evidence_references,
        )
        mapped[item.event_id] = target.event_id
        events.append(target)
    for action in bundle.governance_actions:
        if action.target_event_id not in mapped:
            mapped[action.target_event_id] = approved_event_import_identity(
                target_scope, action.target_event_id
            )
    governance = tuple(
        ApprovedEpisodicEventGovernance.create(
            scope=target_scope,
            kind=item.kind,
            target_event_id=mapped[item.target_event_id],
            replacement_event_id=(
                None if item.replacement_event_id is None else mapped[item.replacement_event_id]
            ),
            reason=item.reason,
            source_action_key=item.source_action_key,
            occurred_at=item.occurred_at,
            evidence_references=item.evidence_references,
        )
        for item in bundle.governance_actions
    )
    pins = tuple(
        ApprovedEpisodicEventPinAction.create(
            scope=target_scope,
            event_id=mapped[item.action.event_id],
            pinned=item.action.pinned,
            source_action_key=item.action.source_action_key,
            occurred_at=item.action.occurred_at,
            evidence_references=item.action.evidence_references,
        )
        for item in bundle.pin_history
    )
    return ApprovedEventExportBundle.create(
        scope=target_scope,
        exported_at=bundle.exported_at,
        events=tuple(events),
        governance_actions=governance,
        pin_actions=pins,
    )


def _same_bundle(left: ApprovedEventExportBundle, right: ApprovedEventExportBundle) -> bool:
    return (
        left.format_version == right.format_version
        and left.scope == right.scope
        and left.exported_at == right.exported_at
        and left.events == right.events
        and left.governance_actions == right.governance_actions
        and left.pin_history == right.pin_history
    )


def _result(
    source: ApprovedEventExportBundle,
    target: ApprovedEventExportBundle,
    *,
    idempotent: bool,
) -> ApprovedEventTransferResult:
    return ApprovedEventTransferResult(
        source.content_digest,
        target.content_digest,
        len(target.events),
        len(target.governance_actions),
        len(target.pin_history),
        idempotent,
    )
