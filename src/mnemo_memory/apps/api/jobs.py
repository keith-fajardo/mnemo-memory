"""Exact-project adapter for content-free event-job controls."""

from __future__ import annotations

from pathlib import Path

from mnemo_memory.packages.application import (
    EventOutboxApplicationError,
    EventOutboxInspectionService,
    LocalConfig,
    LocalRuntimeError,
    build_checkpoint_runtime,
)
from mnemo_memory.packages.application.automatic_memory import (
    AutomaticMemoryBindingError,
    LocalMemoryProjectBindingStore,
)


class EventJobControlError(RuntimeError):
    """Sanitized failure for local event-job operations."""


class EventJobProjectNotFound(EventJobControlError):
    pass


def retry_failed_event_jobs(
    config: LocalConfig,
    *,
    project_directory: Path | None = None,
    maximum_jobs: int = 100,
) -> dict[str, object]:
    """Requeue a bounded set of failed jobs in only the registered current project."""
    try:
        binding = LocalMemoryProjectBindingStore(config.data_directory).get(
            project_directory or Path.cwd()
        )
    except (AutomaticMemoryBindingError, OSError, ValueError) as error:
        raise EventJobControlError("MNEMO_JOB_RETRY_UNAVAILABLE") from error
    if binding is None:
        raise EventJobProjectNotFound("MNEMO_JOB_PROJECT_NOT_FOUND")
    try:
        with build_checkpoint_runtime(config) as runtime:
            result = EventOutboxInspectionService(runtime.repository).retry_failed(
                binding.scope, maximum_jobs=maximum_jobs
            )
    except (EventOutboxApplicationError, LocalRuntimeError, OSError, ValueError) as error:
        raise EventJobControlError("MNEMO_JOB_RETRY_UNAVAILABLE") from error
    return {"requeued": result.requeued}
