"""Payload-free deterministic expiry for extracted episodic memory."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Self
from uuid import UUID, uuid5

from .identifiers import EventId, MemoryId, RetentionPolicyId
from .models import MemoryScope, RetentionSchedule, ScopeLevel, _parse_datetime, _require_aware

_EXPIRATION_NAMESPACE = UUID("18bd4af5-9992-4d30-a559-752e98631b19")
_PURGE_NAMESPACE = UUID("1ea4554d-e7c6-47f3-b7cc-cea58f1610a1")


@dataclass(frozen=True, slots=True)
class EpisodicMemoryRetentionTarget:
    memory_id: MemoryId
    source_event_id: EventId
    scope: MemoryScope
    retention: RetentionSchedule

    def __post_init__(self) -> None:
        if not isinstance(self.memory_id, MemoryId) or not isinstance(
            self.source_event_id, EventId
        ):
            raise TypeError("episodic retention target identity is invalid")
        if not isinstance(self.scope, MemoryScope) or self.scope.level is not ScopeLevel.TASK:
            raise ValueError("episodic retention target requires task scope")
        if not isinstance(self.retention, RetentionSchedule):
            raise TypeError("episodic retention target schedule is invalid")


@dataclass(frozen=True, slots=True)
class EpisodicMemoryExpiration:
    expiration_id: EventId
    memory_id: MemoryId
    source_event_id: EventId
    scope: MemoryScope
    retention_policy_id: RetentionPolicyId
    scheduled_expires_at: datetime
    expired_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.expiration_id, EventId) or not isinstance(self.memory_id, MemoryId):
            raise TypeError("episodic memory expiration identity is invalid")
        if not isinstance(self.source_event_id, EventId):
            raise TypeError("episodic memory expiration source identity is invalid")
        if not isinstance(self.scope, MemoryScope) or self.scope.level is not ScopeLevel.TASK:
            raise ValueError("episodic memory expiration requires task scope")
        if not isinstance(self.retention_policy_id, RetentionPolicyId):
            raise TypeError("episodic memory expiration policy identity is invalid")
        _require_aware(self.scheduled_expires_at, "scheduled_expires_at")
        _require_aware(self.expired_at, "expired_at")
        if self.expired_at < self.scheduled_expires_at:
            raise ValueError("episodic memory cannot expire before its schedule")
        if self.expiration_id != self.identity(
            self.memory_id, self.retention_policy_id, self.scheduled_expires_at
        ):
            raise ValueError("episodic memory expiration identity is not deterministic")

    @staticmethod
    def identity(
        memory_id: MemoryId,
        retention_policy_id: RetentionPolicyId,
        scheduled_expires_at: datetime,
    ) -> EventId:
        _require_aware(scheduled_expires_at, "scheduled_expires_at")
        return EventId(
            uuid5(
                _EXPIRATION_NAMESPACE,
                f"{memory_id}:{retention_policy_id}:{scheduled_expires_at.isoformat()}",
            )
        )

    @classmethod
    def create(cls, target: EpisodicMemoryRetentionTarget, expired_at: datetime) -> Self:
        if not isinstance(target, EpisodicMemoryRetentionTarget):
            raise TypeError("episodic memory expiration requires a retention target")
        if target.retention.permanent or target.retention.expires_at is None:
            raise ValueError("permanent episodic memory cannot expire")
        _require_aware(expired_at, "expired_at")
        if not target.retention.is_expired(expired_at):
            raise ValueError("episodic memory retention is not due")
        scheduled = target.retention.expires_at
        return cls(
            cls.identity(target.memory_id, target.retention.policy_id, scheduled),
            target.memory_id,
            target.source_event_id,
            target.scope,
            target.retention.policy_id,
            scheduled,
            expired_at,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "expiration_id": str(self.expiration_id),
            "memory_id": str(self.memory_id),
            "source_event_id": str(self.source_event_id),
            "scope": self.scope.to_dict(),
            "retention_policy_id": str(self.retention_policy_id),
            "scheduled_expires_at": self.scheduled_expires_at.isoformat(),
            "expired_at": self.expired_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        expected = {
            "expiration_id",
            "memory_id",
            "source_event_id",
            "scope",
            "retention_policy_id",
            "scheduled_expires_at",
            "expired_at",
        }
        if set(value) != expected:
            raise ValueError("episodic memory expiration fields are invalid")
        scope = value["scope"]
        text = tuple(
            value[name]
            for name in (
                "expiration_id",
                "memory_id",
                "source_event_id",
                "retention_policy_id",
            )
        )
        if not isinstance(scope, Mapping) or not all(isinstance(item, str) for item in text):
            raise TypeError("episodic memory expiration serialization is invalid")
        return cls(
            EventId.from_string(str(value["expiration_id"])),
            MemoryId.from_string(str(value["memory_id"])),
            EventId.from_string(str(value["source_event_id"])),
            MemoryScope.from_dict(scope),
            RetentionPolicyId.from_string(str(value["retention_policy_id"])),
            _parse_datetime(value["scheduled_expires_at"], "scheduled_expires_at"),
            _parse_datetime(value["expired_at"], "expired_at"),
        )


@dataclass(frozen=True, slots=True)
class EpisodicMemoryPurge:
    purge_id: EventId
    expiration_id: EventId
    memory_id: MemoryId
    scope: MemoryScope
    purged_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.purge_id, EventId) or not isinstance(self.expiration_id, EventId):
            raise TypeError("episodic memory purge identity is invalid")
        if not isinstance(self.memory_id, MemoryId):
            raise TypeError("episodic memory purge target identity is invalid")
        if not isinstance(self.scope, MemoryScope) or self.scope.level is not ScopeLevel.TASK:
            raise ValueError("episodic memory purge requires task scope")
        _require_aware(self.purged_at, "purged_at")
        if self.purge_id != self.identity(self.expiration_id):
            raise ValueError("episodic memory purge identity is not deterministic")

    @staticmethod
    def identity(expiration_id: EventId) -> EventId:
        if not isinstance(expiration_id, EventId):
            raise TypeError("episodic memory purge expiration identity is invalid")
        return EventId(uuid5(_PURGE_NAMESPACE, str(expiration_id)))

    @classmethod
    def create(cls, expiration: EpisodicMemoryExpiration, purged_at: datetime) -> Self:
        if not isinstance(expiration, EpisodicMemoryExpiration):
            raise TypeError("episodic memory purge requires an expiration")
        _require_aware(purged_at, "purged_at")
        if purged_at < expiration.expired_at:
            raise ValueError("episodic memory cannot be purged before expiration")
        return cls(
            cls.identity(expiration.expiration_id),
            expiration.expiration_id,
            expiration.memory_id,
            expiration.scope,
            purged_at,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "purge_id": str(self.purge_id),
            "expiration_id": str(self.expiration_id),
            "memory_id": str(self.memory_id),
            "scope": self.scope.to_dict(),
            "purged_at": self.purged_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        expected = {"purge_id", "expiration_id", "memory_id", "scope", "purged_at"}
        if set(value) != expected:
            raise ValueError("episodic memory purge fields are invalid")
        scope = value["scope"]
        text = tuple(value[name] for name in ("purge_id", "expiration_id", "memory_id"))
        if not isinstance(scope, Mapping) or not all(isinstance(item, str) for item in text):
            raise TypeError("episodic memory purge serialization is invalid")
        return cls(
            EventId.from_string(str(value["purge_id"])),
            EventId.from_string(str(value["expiration_id"])),
            MemoryId.from_string(str(value["memory_id"])),
            MemoryScope.from_dict(scope),
            _parse_datetime(value["purged_at"], "purged_at"),
        )
