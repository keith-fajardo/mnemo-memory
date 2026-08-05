"""Explicit user deletion of scoped production episodic state."""

from __future__ import annotations

from datetime import datetime

from mnemo_memory.packages.domain import (
    EpisodicMemoryDeletion,
    EventId,
    MemoryId,
    MemoryScope,
    TaskActivityEventDeletion,
)
from mnemo_memory.packages.storage.contracts import (
    EpisodicDeletionRepository,
    EpisodicMemoryDeletionResult,
    TaskActivityDeletionResult,
)


class EpisodicDeletionService:
    def __init__(self, repository: EpisodicDeletionRepository) -> None:
        self._repository = repository

    def delete_memory(
        self,
        *,
        scope: MemoryScope,
        memory_id: MemoryId,
        source_event_id: EventId,
        source_action_key: str,
        deleted_at: datetime,
    ) -> EpisodicMemoryDeletionResult:
        return self._repository.delete_episodic_memory(
            EpisodicMemoryDeletion.create(
                scope=scope,
                memory_id=memory_id,
                source_event_id=source_event_id,
                source_action_key=source_action_key,
                deleted_at=deleted_at,
            )
        )

    def delete_task_event(
        self,
        *,
        scope: MemoryScope,
        event_id: EventId,
        source_action_key: str,
        deleted_at: datetime,
    ) -> TaskActivityDeletionResult:
        return self._repository.delete_task_activity_event(
            TaskActivityEventDeletion.create(
                scope=scope,
                event_id=event_id,
                source_action_key=source_action_key,
                deleted_at=deleted_at,
            )
        )
