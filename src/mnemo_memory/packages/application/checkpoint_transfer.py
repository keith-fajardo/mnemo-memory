"""Portable checkpoint-history export and verified personal-to-team import."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from mnemo_memory.packages.domain import (
    CheckpointExportBundle,
    CheckpointLifecycleEvent,
    CheckpointRevision,
    MemoryScope,
    ScopeLevel,
)
from mnemo_memory.packages.storage.contracts import (
    CheckpointExportRepository,
    CheckpointExportRepositoryError,
    CheckpointImportRepository,
    CheckpointImportRepositoryError,
)


class CheckpointTransferError(Exception):
    """Safe application outcome for checkpoint export or import."""


class CheckpointTransferConflict(CheckpointTransferError):
    pass


class CheckpointTransferStorageFailure(CheckpointTransferError):
    pass


@dataclass(frozen=True, slots=True)
class CheckpointTransferResult:
    source_content_digest: str
    target_content_digest: str
    checkpoint_count: int
    revision_count: int
    event_count: int
    idempotent: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "source_content_digest": self.source_content_digest,
            "target_content_digest": self.target_content_digest,
            "checkpoint_count": self.checkpoint_count,
            "revision_count": self.revision_count,
            "event_count": self.event_count,
            "idempotent": self.idempotent,
        }


class CheckpointExportService:
    def __init__(self, repository: CheckpointExportRepository) -> None:
        self._repository = repository

    def export(self, scope: MemoryScope, *, exported_at: datetime) -> CheckpointExportBundle:
        if not isinstance(scope, MemoryScope) or scope.level is not ScopeLevel.TASK:
            raise ValueError("checkpoint export requires exact task scope")
        try:
            return self._repository.export_checkpoint_history(scope, exported_at=exported_at)
        except CheckpointExportRepositoryError as error:
            raise CheckpointTransferStorageFailure(
                "checkpoint export storage operation failed"
            ) from error


class CheckpointImportService:
    """Rebase only scope, atomically import exact history, and verify the target."""

    def __init__(
        self,
        exports: CheckpointExportRepository,
        imports: CheckpointImportRepository,
    ) -> None:
        self._exports = exports
        self._imports = imports

    def import_bundle(
        self, bundle: CheckpointExportBundle, *, target_scope: MemoryScope
    ) -> CheckpointTransferResult:
        if not isinstance(bundle, CheckpointExportBundle):
            raise TypeError("checkpoint import requires a validated export bundle")
        if not isinstance(target_scope, MemoryScope) or target_scope.level is not ScopeLevel.TASK:
            raise ValueError("checkpoint import requires exact target task scope")
        expected = _rebase_bundle(bundle, target_scope)
        try:
            before = self._exports.export_checkpoint_history(
                target_scope, exported_at=bundle.exported_at
            )
            if _same_bundle(before, expected):
                return _result(bundle, before, idempotent=True)
            if before.aggregates or before.revisions or before.lifecycle_events:
                raise CheckpointTransferConflict(
                    "checkpoint import target contains conflicting state"
                )
            stored = self._imports.import_checkpoint_history(bundle, expected)
            after = self._exports.export_checkpoint_history(
                target_scope, exported_at=bundle.exported_at
            )
        except CheckpointTransferError:
            raise
        except (CheckpointExportRepositoryError, CheckpointImportRepositoryError) as error:
            raise CheckpointTransferStorageFailure(
                "checkpoint import storage operation failed"
            ) from error
        if not _same_bundle(after, expected):
            raise CheckpointTransferConflict(
                "checkpoint import target counts or canonical state do not match"
            )
        return _result(bundle, after, idempotent=stored.idempotent)


def _rebase_bundle(
    bundle: CheckpointExportBundle, target_scope: MemoryScope
) -> CheckpointExportBundle:
    aggregates = tuple(replace(item, scope=target_scope) for item in bundle.aggregates)
    revisions = tuple(
        CheckpointRevision(
            item.revision_id,
            item.checkpoint_id,
            item.revision_number,
            item.predecessor_revision_id,
            target_scope,
            item.content,
            item.status,
            item.evidence_references,
            item.created_at,
        )
        for item in bundle.revisions
    )
    events = tuple(
        CheckpointLifecycleEvent(
            item.event_id,
            target_scope,
            item.kind,
            item.checkpoint_id,
            item.revision_id,
            item.revision_number,
            item.occurred_at,
            item.idempotency_key,
            item.evidence_references,
        )
        for item in bundle.lifecycle_events
    )
    return CheckpointExportBundle.create(
        scope=target_scope,
        exported_at=bundle.exported_at,
        aggregates=aggregates,
        revisions=revisions,
        lifecycle_events=events,
    )


def _same_bundle(left: CheckpointExportBundle, right: CheckpointExportBundle) -> bool:
    return (
        left.format_version == right.format_version
        and left.scope == right.scope
        and left.exported_at == right.exported_at
        and left.aggregates == right.aggregates
        and left.revisions == right.revisions
        and left.lifecycle_events == right.lifecycle_events
    )


def _result(
    source: CheckpointExportBundle,
    target: CheckpointExportBundle,
    *,
    idempotent: bool,
) -> CheckpointTransferResult:
    return CheckpointTransferResult(
        source.content_digest,
        target.content_digest,
        len(target.aggregates),
        len(target.revisions),
        len(target.lifecycle_events),
        idempotent,
    )
