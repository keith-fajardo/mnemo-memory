"""Bounded application runner for durable canonical-event delivery."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from mnemo_memory.packages.domain import EventOutboxJob, MemoryScope
from mnemo_memory.packages.storage.contracts import (
    EventOutboxLeaseConflict,
    EventOutboxRepository,
    EventOutboxRepositoryError,
)


class EventOutboxApplicationError(Exception):
    """Safe application-level event-delivery outcome."""


class EventOutboxApplicationStorageFailure(EventOutboxApplicationError):
    pass


class EventOutboxHandlerFailure(Exception):
    """A retryable handler failure represented only by a stable bounded code."""

    def __init__(self, failure_code: str) -> None:
        EventOutboxJob.validate_failure_code(failure_code)
        self.failure_code = failure_code
        super().__init__(failure_code)


class EventOutboxHandler(Protocol):
    """At-least-once handler whose effects must be idempotent by ``job.job_id``."""

    def handle(self, job: EventOutboxJob) -> None: ...


@dataclass(frozen=True, slots=True)
class EventOutboxRunResult:
    claimed: int
    completed: int
    retried: int
    lease_conflicts: int


class EventOutboxRunner:
    """Claim one bounded batch and acknowledge each handler outcome."""

    def __init__(
        self,
        repository: EventOutboxRepository,
        handler: EventOutboxHandler,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._handler = handler
        self._clock = clock or (lambda: datetime.now(UTC))

    def run(
        self,
        scope: MemoryScope,
        *,
        worker_id: str,
        maximum_jobs: int = 16,
        lease_seconds: int = 30,
        retry_delay_seconds: int = 60,
    ) -> EventOutboxRunResult:
        if not 1 <= maximum_jobs <= 100:
            raise ValueError("event outbox batch size must be between 1 and 100")
        if not 1 <= lease_seconds <= 86_400:
            raise ValueError("event outbox lease must be between 1 and 86400 seconds")
        if not 1 <= retry_delay_seconds <= 86_400:
            raise ValueError("event outbox retry delay must be between 1 and 86400 seconds")
        claimed_at = self._clock()
        try:
            jobs = self._repository.claim_event_jobs(
                scope,
                worker_id=worker_id,
                now=claimed_at,
                lease_expires_at=claimed_at + timedelta(seconds=lease_seconds),
                limit=maximum_jobs,
            )
        except EventOutboxRepositoryError as error:
            raise EventOutboxApplicationStorageFailure("event outbox claim failed") from error

        completed = 0
        retried = 0
        lease_conflicts = 0
        for job in jobs:
            failure_code: str | None = None
            try:
                self._handler.handle(job)
            except EventOutboxHandlerFailure as error:
                failure_code = error.failure_code
            except Exception:
                failure_code = "MNEMO_JOB_HANDLER_FAILED"

            acknowledged_at = self._clock()
            try:
                if failure_code is None:
                    self._repository.complete_event_job(
                        scope,
                        job.job_id,
                        worker_id=worker_id,
                        completed_at=acknowledged_at,
                    )
                    completed += 1
                else:
                    self._repository.retry_event_job(
                        scope,
                        job.job_id,
                        worker_id=worker_id,
                        now=acknowledged_at,
                        available_at=acknowledged_at + timedelta(seconds=retry_delay_seconds),
                        failure_code=failure_code,
                    )
                    retried += 1
            except EventOutboxLeaseConflict:
                lease_conflicts += 1
            except EventOutboxRepositoryError as error:
                raise EventOutboxApplicationStorageFailure(
                    "event outbox acknowledgement failed"
                ) from error
        return EventOutboxRunResult(len(jobs), completed, retried, lease_conflicts)
