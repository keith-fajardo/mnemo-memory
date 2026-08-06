"""Explicit user deletion of one exact-scope checkpoint."""

from __future__ import annotations

from datetime import datetime

from mnemo_memory.packages.domain import CheckpointDeletion, CheckpointId, MemoryScope
from mnemo_memory.packages.storage.contracts import (
    CheckpointDeletionRepository,
    CheckpointDeletionResult,
)


class CheckpointDeletionService:
    def __init__(self, repository: CheckpointDeletionRepository) -> None:
        self._repository = repository

    def delete(
        self,
        *,
        scope: MemoryScope,
        checkpoint_id: CheckpointId,
        source_action_key: str,
        deleted_at: datetime,
    ) -> CheckpointDeletionResult:
        return self._repository.delete_checkpoint(
            CheckpointDeletion.create(
                scope=scope,
                checkpoint_id=checkpoint_id,
                source_action_key=source_action_key,
                deleted_at=deleted_at,
            )
        )
