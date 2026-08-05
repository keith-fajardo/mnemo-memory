"""Scope-first read model for the local approved-memory browser."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid5

from mnemo_memory.packages.application import (
    CheckpointApplicationEpisodicEventConflict,
    CheckpointApplicationEpisodicEventNotFound,
    CheckpointApplicationError,
    CheckpointApplicationInvalidContent,
    CorrectApprovedEpisodicEvent,
    ListApprovedEpisodicEventRecords,
    LocalConfig,
    LocalRuntimeError,
    RetractApprovedEpisodicEvent,
    build_checkpoint_runtime,
)
from mnemo_memory.packages.application.automatic_memory import (
    AutomaticMemoryBindingError,
    LocalMemoryProjectBindingStore,
    MemoryProjectBinding,
)
from mnemo_memory.packages.domain import (
    EventId,
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    SourceId,
    SourceTrustClass,
    VerificationStatus,
)
from mnemo_memory.packages.storage import ApprovedEpisodicEventRecord

_WEB_GOVERNANCE_NAMESPACE = UUID("104ca6b8-9d4f-438d-9a5e-e6b389c78e3e")


class ApprovedMemoryBrowserError(RuntimeError):
    """Stable failure reading the current project's approved memories."""


class ApprovedMemoryActionError(RuntimeError):
    """Safe base outcome for an explicit browser mutation."""


class ApprovedMemoryActionInvalid(ApprovedMemoryActionError):
    pass


class ApprovedMemoryActionNotFound(ApprovedMemoryActionError):
    pass


class ApprovedMemoryActionConflict(ApprovedMemoryActionError):
    pass


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


def correct_approved_memory(
    config: LocalConfig,
    event_id: str,
    value: object,
    *,
    project_directory: Path | None = None,
) -> dict[str, object]:
    summary, reason = _action_fields(value, correction=True)
    typed_event_id = _event_id(event_id)
    digest = _action_digest("corrected", typed_event_id, reason, summary)
    evidence = _governance_evidence(typed_event_id, digest, datetime.now(UTC))
    binding = _mutation_binding(config, project_directory)
    try:
        with build_checkpoint_runtime(config) as runtime:
            result = runtime.checkpoint_service.correct_approved_event(
                CorrectApprovedEpisodicEvent(
                    binding.checkpoint_scope,
                    typed_event_id,
                    summary,
                    f"web-correction-event:{typed_event_id}:{digest[:32]}",
                    reason,
                    f"web-correction-action:{typed_event_id}:{digest[:32]}",
                    (evidence,),
                )
            )
    except CheckpointApplicationEpisodicEventNotFound as error:
        raise ApprovedMemoryActionNotFound("MNEMO_MEMORY_NOT_FOUND") from error
    except CheckpointApplicationEpisodicEventConflict as error:
        raise ApprovedMemoryActionConflict("MNEMO_MEMORY_ACTION_CONFLICT") from error
    except CheckpointApplicationInvalidContent as error:
        raise ApprovedMemoryActionInvalid("MNEMO_MEMORY_ACTION_INVALID") from error
    except (CheckpointApplicationError, LocalRuntimeError, OSError, ValueError) as error:
        raise ApprovedMemoryActionError("MNEMO_MEMORY_ACTION_UNAVAILABLE") from error
    return {
        "idempotent": result.idempotent,
        "corrected": _record_value(result.target),
        "replacement": (None if result.replacement is None else _record_value(result.replacement)),
    }


def retract_approved_memory(
    config: LocalConfig,
    event_id: str,
    value: object,
    *,
    project_directory: Path | None = None,
) -> dict[str, object]:
    _, reason = _action_fields(value, correction=False)
    typed_event_id = _event_id(event_id)
    digest = _action_digest("retracted", typed_event_id, reason, None)
    evidence = _governance_evidence(typed_event_id, digest, datetime.now(UTC))
    binding = _mutation_binding(config, project_directory)
    try:
        with build_checkpoint_runtime(config) as runtime:
            result = runtime.checkpoint_service.retract_approved_event(
                RetractApprovedEpisodicEvent(
                    binding.checkpoint_scope,
                    typed_event_id,
                    reason,
                    f"web-retraction-action:{typed_event_id}:{digest[:32]}",
                    (evidence,),
                )
            )
    except CheckpointApplicationEpisodicEventNotFound as error:
        raise ApprovedMemoryActionNotFound("MNEMO_MEMORY_NOT_FOUND") from error
    except CheckpointApplicationEpisodicEventConflict as error:
        raise ApprovedMemoryActionConflict("MNEMO_MEMORY_ACTION_CONFLICT") from error
    except CheckpointApplicationInvalidContent as error:
        raise ApprovedMemoryActionInvalid("MNEMO_MEMORY_ACTION_INVALID") from error
    except (CheckpointApplicationError, LocalRuntimeError, OSError, ValueError) as error:
        raise ApprovedMemoryActionError("MNEMO_MEMORY_ACTION_UNAVAILABLE") from error
    return {"idempotent": result.idempotent, "retracted": _record_value(result.target)}


def _mutation_binding(config: LocalConfig, project_directory: Path | None) -> MemoryProjectBinding:
    try:
        binding = LocalMemoryProjectBindingStore(config.data_directory).get(
            project_directory or Path.cwd()
        )
    except (AutomaticMemoryBindingError, OSError, ValueError) as error:
        raise ApprovedMemoryActionError("MNEMO_MEMORY_ACTION_UNAVAILABLE") from error
    if binding is None:
        raise ApprovedMemoryActionNotFound("MNEMO_MEMORY_NOT_FOUND")
    return binding


def _event_id(value: str) -> EventId:
    try:
        return EventId.from_string(value)
    except (TypeError, ValueError) as error:
        raise ApprovedMemoryActionInvalid("MNEMO_MEMORY_ACTION_INVALID") from error


def _action_fields(value: object, *, correction: bool) -> tuple[str, str]:
    expected = {"reason", "summary"} if correction else {"reason"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ApprovedMemoryActionInvalid("MNEMO_MEMORY_ACTION_INVALID")
    summary_value = value.get("summary") if correction else ""
    reason_value = value.get("reason")
    if (
        not isinstance(summary_value, str)
        or not isinstance(reason_value, str)
        or (correction and not summary_value.strip())
        or not reason_value.strip()
        or len(summary_value) > 1_200
        or len(reason_value) > 1_200
    ):
        raise ApprovedMemoryActionInvalid("MNEMO_MEMORY_ACTION_INVALID")
    return summary_value.strip(), reason_value.strip()


def _action_digest(kind: str, event_id: EventId, reason: str, summary: str | None) -> str:
    material = json.dumps(
        {
            "event_id": str(event_id),
            "kind": kind,
            "reason": reason,
            "summary": summary,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode()).hexdigest()


def _governance_evidence(
    event_id: EventId, digest: str, observed_at: datetime
) -> EvidenceReference:
    return EvidenceReference(
        EvidenceId(uuid5(_WEB_GOVERNANCE_NAMESPACE, f"evidence:{digest}")),
        SourceId(uuid5(_WEB_GOVERNANCE_NAMESPACE, "source:user-correction")),
        EvidenceSourceType.USER_CORRECTION,
        SourceTrustClass.USER_CORRECTION,
        f"mnemo:user-correction/{digest}",
        f"sha256:{digest}",
        EvidenceLocation(f"mnemo:web/memory/event/{event_id}"),
        observed_at,
        VerificationStatus.VERIFIED,
    )


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
