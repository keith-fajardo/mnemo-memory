"""Bounded, authorization-first checkpoint retention expiry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from mnemo_memory.packages.domain import (
    CheckpointRevision,
    CheckpointStatus,
    MemoryScope,
)
from mnemo_memory.packages.storage.contracts import (
    CheckpointNotFound,
    CheckpointRepository,
    CheckpointRepositoryError,
    InvalidLifecycleTransition,
    RevisionConflict,
)


class CheckpointRetentionError(RuntimeError):
    """Safe failure while discovering or expiring due checkpoints."""


@dataclass(frozen=True, slots=True)
class CheckpointRetentionSweepResult:
    scanned_count: int
    expired_revisions: tuple[CheckpointRevision, ...]
    skipped_changed_count: int

    @property
    def expired_count(self) -> int:
        return len(self.expired_revisions)


class CheckpointRetentionService:
    """Expire checkpoints after the configured period since their last canonical write."""

    def __init__(self, repository: CheckpointRepository) -> None:
        self._repository = repository

    def expire_due(
        self,
        scope: MemoryScope,
        *,
        as_of: datetime,
        retention_days: int,
        limit: int = 100,
    ) -> CheckpointRetentionSweepResult:
        if not isinstance(scope, MemoryScope):
            raise CheckpointRetentionError("checkpoint retention scope is invalid")
        if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None:
            raise CheckpointRetentionError("checkpoint retention time must be timezone-aware")
        if (
            not isinstance(retention_days, int)
            or isinstance(retention_days, bool)
            or not 1 <= retention_days <= 3_650
        ):
            raise CheckpointRetentionError("checkpoint retention must be between 1 and 3650 days")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise CheckpointRetentionError("checkpoint retention limit must be between 1 and 100")

        cutoff = as_of - timedelta(days=retention_days)
        try:
            due = self._repository.list_active_checkpoints_updated_before(
                scope,
                updated_before=cutoff,
                limit=limit,
            )
        except (CheckpointRepositoryError, TypeError, ValueError) as error:
            raise CheckpointRetentionError("checkpoint retention storage is unavailable") from error

        expired: list[CheckpointRevision] = []
        skipped_changed = 0
        for selected in due:
            try:
                current_aggregate = self._repository.get_aggregate(scope, selected.checkpoint_id)
                if (
                    current_aggregate.lifecycle_status is not CheckpointStatus.ACTIVE
                    or current_aggregate.current_revision_id != selected.current_revision_id
                    or current_aggregate.updated_at != selected.updated_at
                    or current_aggregate.updated_at > cutoff
                ):
                    skipped_changed += 1
                    continue
                current_revision = self._repository.get_current_revision(
                    scope, selected.checkpoint_id
                )
                if current_revision.revision_id != selected.current_revision_id:
                    skipped_changed += 1
                    continue
                expired.append(
                    self._repository.expire_checkpoint(
                        scope,
                        selected.checkpoint_id,
                        selected.current_revision_id,
                        current_revision.content,
                        current_revision.evidence_references,
                        as_of,
                    )
                )
            except (CheckpointNotFound, InvalidLifecycleTransition, RevisionConflict):
                # Another writer won after discovery. A later sweep evaluates its new timestamp.
                skipped_changed += 1
            except CheckpointRepositoryError as error:
                raise CheckpointRetentionError(
                    "checkpoint retention storage is unavailable"
                ) from error

        return CheckpointRetentionSweepResult(len(due), tuple(expired), skipped_changed)
