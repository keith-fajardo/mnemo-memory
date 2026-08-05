"""Payload-free explicit deletion actions for production episodic state."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID, uuid5

from .identifiers import EventId, MemoryId
from .models import MemoryScope, ScopeLevel, _parse_datetime, _require_aware
from .task_activity_events import TaskActivityActor

_SOURCE_DELETION_NAMESPACE = UUID("5cb11498-76d0-4b25-b55f-339591cff887")
_MEMORY_DELETION_NAMESPACE = UUID("61fc7a6c-0f33-471d-8bbd-ecc11d283fff")


class EpisodicDeletionCause(StrEnum):
    USER = "user"
    SOURCE_DELETED = "source_deleted"


def _validate_action(
    scope: MemoryScope,
    actor: TaskActivityActor,
    source_action_key: str,
    deleted_at: datetime,
) -> None:
    if not isinstance(scope, MemoryScope) or scope.level is not ScopeLevel.TASK:
        raise ValueError("episodic deletion requires task scope")
    if actor is not TaskActivityActor.USER:
        raise ValueError("only a user may delete episodic state")
    if (
        not isinstance(source_action_key, str)
        or not source_action_key.strip()
        or len(source_action_key) > 256
    ):
        raise ValueError("episodic deletion action key is invalid")
    _require_aware(deleted_at, "deleted_at")


@dataclass(frozen=True, slots=True)
class TaskActivityEventDeletion:
    deletion_id: EventId
    event_id: EventId
    scope: MemoryScope
    actor: TaskActivityActor
    source_action_key: str
    deleted_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.deletion_id, EventId) or not isinstance(self.event_id, EventId):
            raise TypeError("task activity deletion identity is invalid")
        _validate_action(self.scope, self.actor, self.source_action_key, self.deleted_at)
        if self.deletion_id != self.identity(self.scope, self.event_id, self.source_action_key):
            raise ValueError("task activity deletion identity is not deterministic")

    @staticmethod
    def identity(scope: MemoryScope, event_id: EventId, source_action_key: str) -> EventId:
        if not isinstance(scope, MemoryScope) or not isinstance(event_id, EventId):
            raise TypeError("task activity deletion identity input is invalid")
        return EventId(
            uuid5(
                _SOURCE_DELETION_NAMESPACE,
                f"{scope.to_dict()}:{event_id}:{source_action_key}",
            )
        )

    @classmethod
    def create(
        cls,
        *,
        scope: MemoryScope,
        event_id: EventId,
        source_action_key: str,
        deleted_at: datetime,
    ) -> Self:
        return cls(
            cls.identity(scope, event_id, source_action_key),
            event_id,
            scope,
            TaskActivityActor.USER,
            source_action_key,
            deleted_at,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "deletion_id": str(self.deletion_id),
            "event_id": str(self.event_id),
            "scope": self.scope.to_dict(),
            "actor": self.actor.value,
            "source_action_key": self.source_action_key,
            "deleted_at": self.deleted_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        expected = {
            "deletion_id",
            "event_id",
            "scope",
            "actor",
            "source_action_key",
            "deleted_at",
        }
        if set(value) != expected:
            raise ValueError("task activity deletion fields are invalid")
        scope = value["scope"]
        text = tuple(
            value[name] for name in ("deletion_id", "event_id", "actor", "source_action_key")
        )
        if not isinstance(scope, Mapping) or not all(isinstance(item, str) for item in text):
            raise TypeError("task activity deletion serialization is invalid")
        return cls(
            EventId.from_string(str(value["deletion_id"])),
            EventId.from_string(str(value["event_id"])),
            MemoryScope.from_dict(scope),
            TaskActivityActor(str(value["actor"])),
            str(value["source_action_key"]),
            _parse_datetime(value["deleted_at"], "deleted_at"),
        )


@dataclass(frozen=True, slots=True)
class EpisodicMemoryDeletion:
    deletion_id: EventId
    memory_id: MemoryId
    source_event_id: EventId
    scope: MemoryScope
    cause: EpisodicDeletionCause
    actor: TaskActivityActor
    source_action_key: str
    deleted_at: datetime
    source_deletion_id: EventId | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.deletion_id, EventId) or not isinstance(self.memory_id, MemoryId):
            raise TypeError("episodic memory deletion identity is invalid")
        if not isinstance(self.source_event_id, EventId) or not isinstance(
            self.cause, EpisodicDeletionCause
        ):
            raise TypeError("episodic memory deletion source is invalid")
        _validate_action(self.scope, self.actor, self.source_action_key, self.deleted_at)
        if self.cause is EpisodicDeletionCause.USER:
            if self.source_deletion_id is not None:
                raise ValueError("user memory deletion cannot name a source deletion")
        elif not isinstance(self.source_deletion_id, EventId):
            raise ValueError("source-dependent memory deletion requires source deletion identity")
        if self.deletion_id != self.identity(
            self.scope,
            self.memory_id,
            self.cause,
            self.source_action_key,
        ):
            raise ValueError("episodic memory deletion identity is not deterministic")

    @staticmethod
    def identity(
        scope: MemoryScope,
        memory_id: MemoryId,
        cause: EpisodicDeletionCause,
        source_action_key: str,
    ) -> EventId:
        if (
            not isinstance(scope, MemoryScope)
            or not isinstance(memory_id, MemoryId)
            or not isinstance(cause, EpisodicDeletionCause)
        ):
            raise TypeError("episodic memory deletion identity input is invalid")
        return EventId(
            uuid5(
                _MEMORY_DELETION_NAMESPACE,
                f"{scope.to_dict()}:{memory_id}:{cause.value}:{source_action_key}",
            )
        )

    @classmethod
    def create(
        cls,
        *,
        scope: MemoryScope,
        memory_id: MemoryId,
        source_event_id: EventId,
        source_action_key: str,
        deleted_at: datetime,
    ) -> Self:
        return cls(
            cls.identity(scope, memory_id, EpisodicDeletionCause.USER, source_action_key),
            memory_id,
            source_event_id,
            scope,
            EpisodicDeletionCause.USER,
            TaskActivityActor.USER,
            source_action_key,
            deleted_at,
        )

    @classmethod
    def from_source(
        cls,
        source: TaskActivityEventDeletion,
        *,
        memory_id: MemoryId,
        source_event_id: EventId,
    ) -> Self:
        if source.event_id != source_event_id:
            raise ValueError("dependent memory deletion source does not match")
        source_action_key = f"source:{source.deletion_id}:{memory_id}"
        return cls(
            cls.identity(
                source.scope,
                memory_id,
                EpisodicDeletionCause.SOURCE_DELETED,
                source_action_key,
            ),
            memory_id,
            source_event_id,
            source.scope,
            EpisodicDeletionCause.SOURCE_DELETED,
            TaskActivityActor.USER,
            source_action_key,
            source.deleted_at,
            source.deletion_id,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "deletion_id": str(self.deletion_id),
            "memory_id": str(self.memory_id),
            "source_event_id": str(self.source_event_id),
            "scope": self.scope.to_dict(),
            "cause": self.cause.value,
            "actor": self.actor.value,
            "source_action_key": self.source_action_key,
            "deleted_at": self.deleted_at.isoformat(),
            "source_deletion_id": (
                None if self.source_deletion_id is None else str(self.source_deletion_id)
            ),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        expected = {
            "deletion_id",
            "memory_id",
            "source_event_id",
            "scope",
            "cause",
            "actor",
            "source_action_key",
            "deleted_at",
            "source_deletion_id",
        }
        if set(value) != expected:
            raise ValueError("episodic memory deletion fields are invalid")
        scope = value["scope"]
        source_deletion_id = value["source_deletion_id"]
        text = tuple(
            value[name]
            for name in (
                "deletion_id",
                "memory_id",
                "source_event_id",
                "cause",
                "actor",
                "source_action_key",
            )
        )
        if (
            not isinstance(scope, Mapping)
            or not all(isinstance(item, str) for item in text)
            or (source_deletion_id is not None and not isinstance(source_deletion_id, str))
        ):
            raise TypeError("episodic memory deletion serialization is invalid")
        return cls(
            EventId.from_string(str(value["deletion_id"])),
            MemoryId.from_string(str(value["memory_id"])),
            EventId.from_string(str(value["source_event_id"])),
            MemoryScope.from_dict(scope),
            EpisodicDeletionCause(str(value["cause"])),
            TaskActivityActor(str(value["actor"])),
            str(value["source_action_key"]),
            _parse_datetime(value["deleted_at"], "deleted_at"),
            (None if source_deletion_id is None else EventId.from_string(source_deletion_id)),
        )
