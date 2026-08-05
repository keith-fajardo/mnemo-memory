"""Deterministic, authorization-first expiration of extracted episodic memory."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from mnemo_memory.packages.domain import (
    EpisodicMemoryExpiration,
    MemoryScope,
    ScopeLevel,
)
from mnemo_memory.packages.storage.contracts import EpisodicMemoryRetentionRepository


@dataclass(frozen=True, slots=True)
class EpisodicRetentionSweepResult:
    expirations: tuple[EpisodicMemoryExpiration, ...]
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
