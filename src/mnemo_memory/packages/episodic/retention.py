"""Deterministic, authorization-first expiration of extracted episodic memory."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from mnemo_memory.packages.domain import (
    EpisodicMemoryExpiration,
    EpisodicMemoryPurge,
    MemoryScope,
    ScopeLevel,
    TaskActivityEventExpiration,
    TaskActivityEventPurge,
)
from mnemo_memory.packages.storage.contracts import (
    EpisodicMemoryRetentionRepository,
    TaskActivityRetentionRepository,
)


@dataclass(frozen=True, slots=True)
class EpisodicRetentionSweepResult:
    expirations: tuple[EpisodicMemoryExpiration, ...]
    idempotent: bool


@dataclass(frozen=True, slots=True)
class EpisodicPurgeSweepResult:
    purges: tuple[EpisodicMemoryPurge, ...]
    idempotent: bool


@dataclass(frozen=True, slots=True)
class TaskActivityRetentionSweepResult:
    expirations: tuple[TaskActivityEventExpiration, ...]
    idempotent: bool


@dataclass(frozen=True, slots=True)
class TaskActivityPurgeSweepResult:
    purges: tuple[TaskActivityEventPurge, ...]
    idempotent: bool


class EpisodicRetentionService:
    def __init__(self, repository: EpisodicMemoryRetentionRepository) -> None:
        self._repository = repository

    def expire_due(self, scope: MemoryScope, *, as_of: datetime) -> EpisodicRetentionSweepResult:
        if not isinstance(scope, MemoryScope) or scope.level is not ScopeLevel.TASK:
            raise ValueError("episodic retention requires explicit task scope")
        if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        targets = self._repository.list_due_episodic_memory_retention(scope, as_of=as_of)
        expirations = tuple(EpisodicMemoryExpiration.create(target, as_of) for target in targets)
        stored = self._repository.apply_episodic_memory_expirations(expirations)
        return EpisodicRetentionSweepResult(stored.expirations, stored.idempotent)

    def purge_expired(self, scope: MemoryScope, *, purged_at: datetime) -> EpisodicPurgeSweepResult:
        if not isinstance(scope, MemoryScope) or scope.level is not ScopeLevel.TASK:
            raise ValueError("episodic purge requires explicit task scope")
        if (
            not isinstance(purged_at, datetime)
            or purged_at.tzinfo is None
            or purged_at.utcoffset() is None
        ):
            raise ValueError("purged_at must be timezone-aware")
        expirations = self._repository.list_unpurged_episodic_memory_expirations(scope)
        purges = tuple(
            EpisodicMemoryPurge.create(expiration, purged_at) for expiration in expirations
        )
        stored = self._repository.apply_episodic_memory_purges(purges)
        return EpisodicPurgeSweepResult(stored.purges, stored.idempotent)


class TaskActivityRetentionService:
    def __init__(self, repository: TaskActivityRetentionRepository) -> None:
        self._repository = repository

    def expire_due(
        self, scope: MemoryScope, *, as_of: datetime
    ) -> TaskActivityRetentionSweepResult:
        _require_task_scope_time(scope, as_of, "task activity retention", "as_of")
        targets = self._repository.list_due_task_activity_retention(scope, as_of=as_of)
        expirations = tuple(TaskActivityEventExpiration.create(target, as_of) for target in targets)
        stored = self._repository.apply_task_activity_expirations(expirations)
        return TaskActivityRetentionSweepResult(stored.expirations, stored.idempotent)

    def purge_expired(
        self, scope: MemoryScope, *, purged_at: datetime
    ) -> TaskActivityPurgeSweepResult:
        _require_task_scope_time(scope, purged_at, "task activity purge", "purged_at")
        expirations = self._repository.list_unpurged_task_activity_expirations(scope)
        purges = tuple(
            TaskActivityEventPurge.create(expiration, purged_at) for expiration in expirations
        )
        stored = self._repository.apply_task_activity_purges(purges)
        return TaskActivityPurgeSweepResult(stored.purges, stored.idempotent)


def _require_task_scope_time(
    scope: MemoryScope, value: datetime, operation: str, time_name: str
) -> None:
    if not isinstance(scope, MemoryScope) or scope.level is not ScopeLevel.TASK:
        raise ValueError(f"{operation} requires explicit task scope")
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{time_name} must be timezone-aware")
