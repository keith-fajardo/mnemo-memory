"""Payload-free deterministic retention for minimized task activity events."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Self
from uuid import UUID, uuid5

from .identifiers import EventId, RetentionPolicyId
from .models import MemoryScope, RetentionSchedule, ScopeLevel, _parse_datetime, _require_aware

_EXPIRATION_NAMESPACE = UUID("02fc5010-a206-45ad-bb80-2c49625144df")
_PURGE_NAMESPACE = UUID("8a6fc6b8-91af-4fb1-9b8b-5ea29697e63e")


@dataclass(frozen=True, slots=True)
class TaskActivityEventRetentionTarget:
    event_id: EventId
    scope: MemoryScope
    retention: RetentionSchedule

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, EventId):
            raise TypeError("task activity retention target identity is invalid")
        if not isinstance(self.scope, MemoryScope) or self.scope.level is not ScopeLevel.TASK:
            raise ValueError("task activity retention target requires task scope")
        if not isinstance(self.retention, RetentionSchedule):
            raise TypeError("task activity retention target schedule is invalid")


@dataclass(frozen=True, slots=True)
class TaskActivityEventExpiration:
    expiration_id: EventId
    event_id: EventId
    scope: MemoryScope
    retention_policy_id: RetentionPolicyId
    scheduled_expires_at: datetime
    expired_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.expiration_id, EventId) or not isinstance(self.event_id, EventId):
            raise TypeError("task activity expiration identity is invalid")
        if not isinstance(self.scope, MemoryScope) or self.scope.level is not ScopeLevel.TASK:
            raise ValueError("task activity expiration requires task scope")
        if not isinstance(self.retention_policy_id, RetentionPolicyId):
            raise TypeError("task activity expiration policy identity is invalid")
        _require_aware(self.scheduled_expires_at, "scheduled_expires_at")
        _require_aware(self.expired_at, "expired_at")
        if self.expired_at < self.scheduled_expires_at:
            raise ValueError("task activity event cannot expire before its schedule")
        if self.expiration_id != self.identity(
            self.event_id, self.retention_policy_id, self.scheduled_expires_at
        ):
            raise ValueError("task activity expiration identity is not deterministic")

    @staticmethod
    def identity(
        event_id: EventId,
        retention_policy_id: RetentionPolicyId,
        scheduled_expires_at: datetime,
    ) -> EventId:
        if not isinstance(event_id, EventId) or not isinstance(
            retention_policy_id, RetentionPolicyId
        ):
            raise TypeError("task activity expiration identity input is invalid")
        _require_aware(scheduled_expires_at, "scheduled_expires_at")
        return EventId(
            uuid5(
                _EXPIRATION_NAMESPACE,
                f"{event_id}:{retention_policy_id}:{scheduled_expires_at.isoformat()}",
            )
        )

    @classmethod
    def create(cls, target: TaskActivityEventRetentionTarget, expired_at: datetime) -> Self:
        if not isinstance(target, TaskActivityEventRetentionTarget):
            raise TypeError("task activity expiration requires a retention target")
        if target.retention.permanent or target.retention.expires_at is None:
            raise ValueError("permanent task activity event cannot expire")
        _require_aware(expired_at, "expired_at")
        if not target.retention.is_expired(expired_at):
            raise ValueError("task activity retention is not due")
        scheduled = target.retention.expires_at
        return cls(
            cls.identity(target.event_id, target.retention.policy_id, scheduled),
            target.event_id,
            target.scope,
            target.retention.policy_id,
            scheduled,
            expired_at,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "expiration_id": str(self.expiration_id),
            "event_id": str(self.event_id),
            "scope": self.scope.to_dict(),
            "retention_policy_id": str(self.retention_policy_id),
            "scheduled_expires_at": self.scheduled_expires_at.isoformat(),
            "expired_at": self.expired_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        expected = {
            "expiration_id",
            "event_id",
            "scope",
            "retention_policy_id",
            "scheduled_expires_at",
            "expired_at",
        }
        if set(value) != expected:
            raise ValueError("task activity expiration fields are invalid")
        scope = value["scope"]
        text = tuple(value[name] for name in ("expiration_id", "event_id", "retention_policy_id"))
        if not isinstance(scope, Mapping) or not all(isinstance(item, str) for item in text):
            raise TypeError("task activity expiration serialization is invalid")
        return cls(
            EventId.from_string(str(value["expiration_id"])),
            EventId.from_string(str(value["event_id"])),
            MemoryScope.from_dict(scope),
            RetentionPolicyId.from_string(str(value["retention_policy_id"])),
            _parse_datetime(value["scheduled_expires_at"], "scheduled_expires_at"),
            _parse_datetime(value["expired_at"], "expired_at"),
        )


@dataclass(frozen=True, slots=True)
class TaskActivityEventPurge:
    purge_id: EventId
    expiration_id: EventId
    event_id: EventId
    scope: MemoryScope
    purged_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.purge_id, EventId) or not isinstance(self.expiration_id, EventId):
            raise TypeError("task activity purge identity is invalid")
        if not isinstance(self.event_id, EventId):
            raise TypeError("task activity purge target identity is invalid")
        if not isinstance(self.scope, MemoryScope) or self.scope.level is not ScopeLevel.TASK:
            raise ValueError("task activity purge requires task scope")
        _require_aware(self.purged_at, "purged_at")
        if self.purge_id != self.identity(self.expiration_id):
            raise ValueError("task activity purge identity is not deterministic")

    @staticmethod
    def identity(expiration_id: EventId) -> EventId:
        if not isinstance(expiration_id, EventId):
            raise TypeError("task activity purge expiration identity is invalid")
        return EventId(uuid5(_PURGE_NAMESPACE, str(expiration_id)))

    @classmethod
    def create(cls, expiration: TaskActivityEventExpiration, purged_at: datetime) -> Self:
        if not isinstance(expiration, TaskActivityEventExpiration):
            raise TypeError("task activity purge requires an expiration")
        _require_aware(purged_at, "purged_at")
        if purged_at < expiration.expired_at:
            raise ValueError("task activity event cannot be purged before expiration")
        return cls(
            cls.identity(expiration.expiration_id),
            expiration.expiration_id,
            expiration.event_id,
            expiration.scope,
            purged_at,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "purge_id": str(self.purge_id),
            "expiration_id": str(self.expiration_id),
            "event_id": str(self.event_id),
            "scope": self.scope.to_dict(),
            "purged_at": self.purged_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        expected = {"purge_id", "expiration_id", "event_id", "scope", "purged_at"}
        if set(value) != expected:
            raise ValueError("task activity purge fields are invalid")
        scope = value["scope"]
        text = tuple(value[name] for name in ("purge_id", "expiration_id", "event_id"))
        if not isinstance(scope, Mapping) or not all(isinstance(item, str) for item in text):
            raise TypeError("task activity purge serialization is invalid")
        return cls(
            EventId.from_string(str(value["purge_id"])),
            EventId.from_string(str(value["expiration_id"])),
            EventId.from_string(str(value["event_id"])),
            MemoryScope.from_dict(scope),
            _parse_datetime(value["purged_at"], "purged_at"),
        )
