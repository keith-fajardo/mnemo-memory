"""Minimal durable delivery metadata for canonical episodic events."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid5

from .identifiers import EventId, OutboxJobId
from .models import MemoryScope, ScopeLevel, _require_aware

_JOB_NAMESPACE = UUID("821cb49a-7709-451d-b46a-f0ce58ef780e")
_WORKER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_FAILURE_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}$")


class EventOutboxTopic(StrEnum):
    CHECKPOINT_LIFECYCLE = "checkpoint_lifecycle"
    APPROVED_EPISODIC = "approved_episodic"
    APPROVED_GOVERNANCE = "approved_governance"
    TASK_ACTIVITY = "task_activity"


@dataclass(frozen=True, slots=True)
class EventOutboxJob:
    job_id: OutboxJobId
    scope: MemoryScope
    topic: EventOutboxTopic
    source_event_id: EventId
    event_kind: str
    occurred_at: datetime
    created_at: datetime
    available_at: datetime
    attempt_count: int = 0
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    completed_at: datetime | None = None
    last_failure_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.job_id, OutboxJobId) or not isinstance(
            self.source_event_id, EventId
        ):
            raise TypeError("outbox job identifiers are invalid")
        if not isinstance(self.scope, MemoryScope) or self.scope.level is not ScopeLevel.TASK:
            raise ValueError("event outbox jobs require task scope")
        if not isinstance(self.topic, EventOutboxTopic):
            raise TypeError("event outbox topic is invalid")
        if not isinstance(self.event_kind, str) or not self.event_kind or len(self.event_kind) > 64:
            raise ValueError("event outbox kind is invalid")
        for name in ("occurred_at", "created_at", "available_at"):
            _require_aware(getattr(self, name), name)
        if self.attempt_count < 0:
            raise ValueError("outbox attempt count cannot be negative")
        if (self.lease_owner is None) != (self.lease_expires_at is None):
            raise ValueError("outbox lease owner and expiry must be present together")
        if self.lease_owner is not None:
            if _WORKER.fullmatch(self.lease_owner) is None:
                raise ValueError("outbox lease owner is invalid")
            assert self.lease_expires_at is not None
            _require_aware(self.lease_expires_at, "lease_expires_at")
        if self.completed_at is not None:
            _require_aware(self.completed_at, "completed_at")
            if self.lease_owner is not None:
                raise ValueError("completed outbox job cannot retain a lease")
        if (
            self.last_failure_code is not None
            and _FAILURE_CODE.fullmatch(self.last_failure_code) is None
        ):
            raise ValueError("outbox failure code is invalid")

    @classmethod
    def create(
        cls,
        *,
        scope: MemoryScope,
        topic: EventOutboxTopic,
        source_event_id: EventId,
        event_kind: str,
        occurred_at: datetime,
        created_at: datetime,
    ) -> EventOutboxJob:
        job_id = OutboxJobId(uuid5(_JOB_NAMESPACE, f"{topic.value}:{source_event_id}"))
        return cls(
            job_id,
            scope,
            topic,
            source_event_id,
            event_kind,
            occurred_at,
            created_at,
            created_at,
        )

    def claim(self, worker_id: str, lease_expires_at: datetime) -> EventOutboxJob:
        self.validate_worker_id(worker_id)
        _require_aware(lease_expires_at, "lease_expires_at")
        return replace(
            self,
            attempt_count=self.attempt_count + 1,
            lease_owner=worker_id,
            lease_expires_at=lease_expires_at,
        )

    def complete(self, completed_at: datetime) -> EventOutboxJob:
        return replace(
            self,
            completed_at=completed_at,
            lease_owner=None,
            lease_expires_at=None,
            last_failure_code=None,
        )

    def retry(self, available_at: datetime, failure_code: str) -> EventOutboxJob:
        self.validate_failure_code(failure_code)
        return replace(
            self,
            available_at=available_at,
            lease_owner=None,
            lease_expires_at=None,
            last_failure_code=failure_code,
        )

    @staticmethod
    def validate_worker_id(worker_id: str) -> None:
        if not isinstance(worker_id, str) or _WORKER.fullmatch(worker_id) is None:
            raise ValueError("outbox worker ID is invalid")

    @staticmethod
    def validate_failure_code(failure_code: str) -> None:
        if not isinstance(failure_code, str) or _FAILURE_CODE.fullmatch(failure_code) is None:
            raise ValueError("outbox failure code is invalid")
