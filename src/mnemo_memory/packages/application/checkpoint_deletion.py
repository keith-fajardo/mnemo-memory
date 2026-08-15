"""Explicit user deletion of one exact-scope checkpoint."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, cast

from mnemo_memory.packages.domain import (
    CheckpointDeletion,
    CheckpointId,
    EventId,
    MemoryScope,
    TaskActivityEventDeletion,
)
from mnemo_memory.packages.storage.contracts import (
    CheckpointDeletionRepository,
    CheckpointDeletionResult,
    TaskActivityDeletionResult,
    TaskActivityEventPage,
)

from .semantic_memory import CHECKPOINT_PROJECTION_SOURCE_PREFIX


class _CheckpointProjectionEventRepository(Protocol):
    def list_task_activity_events(
        self, scope: MemoryScope, *, offset: int = 0, limit: int = 50
    ) -> TaskActivityEventPage: ...

    def delete_task_activity_event(
        self, deletion: TaskActivityEventDeletion
    ) -> TaskActivityDeletionResult: ...


class CheckpointDeletionService:
    def __init__(self, repository: CheckpointDeletionRepository) -> None:
        self._repository = repository
        self._projection_events = (
            cast(_CheckpointProjectionEventRepository, repository)
            if hasattr(repository, "list_task_activity_events")
            and hasattr(repository, "delete_task_activity_event")
            else None
        )

    def delete(
        self,
        *,
        scope: MemoryScope,
        checkpoint_id: CheckpointId,
        source_action_key: str,
        deleted_at: datetime,
    ) -> CheckpointDeletionResult:
        deletion = CheckpointDeletion.create(
            scope=scope,
            checkpoint_id=checkpoint_id,
            source_action_key=source_action_key,
            deleted_at=deleted_at,
        )
        projection_event_ids = self._projection_event_ids(scope, checkpoint_id)
        result = self._repository.delete_checkpoint(deletion)
        if self._projection_events is not None:
            for event_id in projection_event_ids:
                self._projection_events.delete_task_activity_event(
                    TaskActivityEventDeletion.create(
                        scope=scope,
                        event_id=event_id,
                        source_action_key=f"checkpoint-delete:{checkpoint_id}:{event_id}",
                        deleted_at=deleted_at,
                    )
                )
        return result

    def _projection_event_ids(
        self,
        scope: MemoryScope,
        checkpoint_id: CheckpointId,
    ) -> tuple[EventId, ...]:
        if self._projection_events is None:
            return ()
        prefix = f"{CHECKPOINT_PROJECTION_SOURCE_PREFIX}{checkpoint_id}:"
        offset = 0
        selected: list[EventId] = []
        while True:
            page = self._projection_events.list_task_activity_events(
                scope, offset=offset, limit=100
            )
            selected.extend(
                event.event_id for event in page.items if event.source_event_key.startswith(prefix)
            )
            if page.next_offset is None:
                break
            offset = page.next_offset
        return tuple(selected)
