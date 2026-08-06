"""Payload-free explicit deletion action for one canonical checkpoint."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Self
from uuid import UUID, uuid5

from .identifiers import CheckpointId, EventId
from .models import MemoryScope, ScopeLevel, _parse_datetime, _require_aware
from .task_activity_events import TaskActivityActor

_CHECKPOINT_DELETION_NAMESPACE = UUID("4aab2915-fef0-47ea-9f9d-f1d98d9308dd")


@dataclass(frozen=True, slots=True)
class CheckpointDeletion:
    deletion_id: EventId
    checkpoint_id: CheckpointId
    scope: MemoryScope
    actor: TaskActivityActor
    source_action_key: str
    deleted_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.deletion_id, EventId) or not isinstance(
            self.checkpoint_id, CheckpointId
        ):
            raise TypeError("checkpoint deletion identity is invalid")
        if not isinstance(self.scope, MemoryScope) or self.scope.level is not ScopeLevel.TASK:
            raise ValueError("checkpoint deletion requires task scope")
        if self.actor is not TaskActivityActor.USER:
            raise ValueError("only a user may delete a checkpoint")
        if (
            not isinstance(self.source_action_key, str)
            or not self.source_action_key.strip()
            or len(self.source_action_key) > 256
        ):
            raise ValueError("checkpoint deletion action key is invalid")
        _require_aware(self.deleted_at, "deleted_at")
        if self.deletion_id != self.identity(
            self.scope, self.checkpoint_id, self.source_action_key
        ):
            raise ValueError("checkpoint deletion identity is not deterministic")

    @staticmethod
    def identity(
        scope: MemoryScope, checkpoint_id: CheckpointId, source_action_key: str
    ) -> EventId:
        if not isinstance(scope, MemoryScope) or not isinstance(checkpoint_id, CheckpointId):
            raise TypeError("checkpoint deletion identity input is invalid")
        return EventId(
            uuid5(
                _CHECKPOINT_DELETION_NAMESPACE,
                f"{scope.to_dict()}:{checkpoint_id}:{source_action_key}",
            )
        )

    @classmethod
    def create(
        cls,
        *,
        scope: MemoryScope,
        checkpoint_id: CheckpointId,
        source_action_key: str,
        deleted_at: datetime,
    ) -> Self:
        return cls(
            cls.identity(scope, checkpoint_id, source_action_key),
            checkpoint_id,
            scope,
            TaskActivityActor.USER,
            source_action_key,
            deleted_at,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "deletion_id": str(self.deletion_id),
            "checkpoint_id": str(self.checkpoint_id),
            "scope": self.scope.to_dict(),
            "actor": self.actor.value,
            "source_action_key": self.source_action_key,
            "deleted_at": self.deleted_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        expected = {
            "deletion_id",
            "checkpoint_id",
            "scope",
            "actor",
            "source_action_key",
            "deleted_at",
        }
        if set(value) != expected:
            raise ValueError("checkpoint deletion fields are invalid")
        scope = value["scope"]
        if not isinstance(scope, Mapping):
            raise TypeError("checkpoint deletion scope is invalid")
        for name in ("deletion_id", "checkpoint_id", "actor", "source_action_key"):
            if not isinstance(value[name], str):
                raise TypeError("checkpoint deletion serialization is invalid")
        return cls(
            EventId.from_string(str(value["deletion_id"])),
            CheckpointId.from_string(str(value["checkpoint_id"])),
            MemoryScope.from_dict(scope),
            TaskActivityActor(str(value["actor"])),
            str(value["source_action_key"]),
            _parse_datetime(value["deleted_at"], "deleted_at"),
        )
