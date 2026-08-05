"""Scope-first read model for the local approved-memory browser."""

from __future__ import annotations

from pathlib import Path

from mnemo_memory.packages.application import (
    CheckpointApplicationError,
    ListApprovedEpisodicEventRecords,
    LocalConfig,
    LocalRuntimeError,
    build_checkpoint_runtime,
)
from mnemo_memory.packages.application.automatic_memory import (
    AutomaticMemoryBindingError,
    LocalMemoryProjectBindingStore,
)
from mnemo_memory.packages.domain import EvidenceReference
from mnemo_memory.packages.storage import ApprovedEpisodicEventRecord


class ApprovedMemoryBrowserError(RuntimeError):
    """Stable failure reading the current project's approved memories."""


def build_approved_memory_page(
    config: LocalConfig,
    *,
    project_directory: Path | None = None,
    offset: int = 0,
    limit: int = 50,
) -> dict[str, object]:
    """Return one bounded page after resolving the exact registered task scope."""
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("memory offset must be non-negative")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("memory limit must be between 1 and 100")
    try:
        binding = LocalMemoryProjectBindingStore(config.data_directory).get(
            project_directory or Path.cwd()
        )
    except (AutomaticMemoryBindingError, OSError, ValueError) as error:
        raise ApprovedMemoryBrowserError("MNEMO_MEMORY_BROWSER_UNAVAILABLE") from error
    if binding is None:
        return {
            "project_registered": False,
            "items": [],
            "next_offset": None,
        }
    try:
        with build_checkpoint_runtime(config) as runtime:
            page = runtime.checkpoint_service.list_approved_event_records(
                ListApprovedEpisodicEventRecords(binding.checkpoint_scope, offset, limit)
            )
    except (CheckpointApplicationError, LocalRuntimeError, OSError, ValueError) as error:
        raise ApprovedMemoryBrowserError("MNEMO_MEMORY_BROWSER_UNAVAILABLE") from error
    return {
        "project_registered": True,
        "items": [_record_value(item) for item in page.items],
        "next_offset": page.next_offset,
    }


def _record_value(record: ApprovedEpisodicEventRecord) -> dict[str, object]:
    event = record.event
    governance = record.governance
    return {
        "event_id": str(record.event_id),
        "status": record.status.value,
        "kind": None if event is None else event.kind.value,
        "summary": None if event is None else event.summary,
        "occurred_at": None if event is None else event.occurred_at.isoformat(),
        "evidence": []
        if event is None
        else [_evidence_value(item) for item in event.evidence_references],
        "governance": None
        if governance is None
        else {
            "action_id": str(governance.action_id),
            "kind": governance.kind.value,
            "replacement_event_id": (
                None
                if governance.replacement_event_id is None
                else str(governance.replacement_event_id)
            ),
            "reason": governance.reason,
            "occurred_at": governance.occurred_at.isoformat(),
            "evidence": [_evidence_value(item) for item in governance.evidence_references],
        },
    }


def _evidence_value(reference: EvidenceReference) -> dict[str, object]:
    return {
        "evidence_id": str(reference.evidence_id),
        "source_type": reference.source_type.value,
        "trust_class": reference.trust_class.value,
        "immutable_source_ref": reference.immutable_source_ref,
        "content_hash": reference.content_hash,
        "location": reference.location.to_dict(),
        "observed_at": reference.observed_at.isoformat(),
        "verification_status": reference.verification_status.value,
    }
