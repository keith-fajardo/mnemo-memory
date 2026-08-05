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
    SetApprovedEpisodicEventPin,
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


class ApprovedMemoryExportError(RuntimeError):
    """Stable failure exporting the current project's approved memories."""


class ApprovedMemoryExportNotFound(ApprovedMemoryExportError):
    pass


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


def build_approved_memory_export(
    config: LocalConfig,
    *,
    project_directory: Path | None = None,
    exported_at: datetime | None = None,
) -> str:
    """Return a canonical exact-scope snapshot of every approved-memory record."""
    timestamp = datetime.now(UTC) if exported_at is None else exported_at
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("memory export timestamp must be timezone-aware")
    try:
        binding = LocalMemoryProjectBindingStore(config.data_directory).get(
            project_directory or Path.cwd()
        )
    except (AutomaticMemoryBindingError, OSError, ValueError) as error:
        raise ApprovedMemoryExportError("MNEMO_MEMORY_EXPORT_UNAVAILABLE") from error
    if binding is None:
        raise ApprovedMemoryExportNotFound("MNEMO_MEMORY_EXPORT_NOT_FOUND")
    records: list[dict[str, object]] = []
    try:
        with build_checkpoint_runtime(config) as runtime:
            offset = 0
            while True:
                page = runtime.checkpoint_service.list_approved_event_records(
                    ListApprovedEpisodicEventRecords(binding.checkpoint_scope, offset, 100)
                )
                records.extend(_export_record_value(item) for item in page.items)
                if page.next_offset is None:
                    break
                offset = page.next_offset
    except (CheckpointApplicationError, LocalRuntimeError, OSError, ValueError) as error:
        raise ApprovedMemoryExportError("MNEMO_MEMORY_EXPORT_UNAVAILABLE") from error
    content: dict[str, object] = {
        "format_version": "mnemo.approved-memory-export.v1",
        "scope": binding.checkpoint_scope.to_dict(),
        "exported_at": timestamp.astimezone(UTC).isoformat(),
        "records": records,
    }
    digest = "sha256:" + hashlib.sha256(_canonical_json(content).encode("utf-8")).hexdigest()
    return _canonical_json({**content, "content_digest": digest})


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


def set_approved_memory_pin(
    config: LocalConfig,
    event_id: str,
    value: object,
    *,
    project_directory: Path | None = None,
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {"pinned"}:
        raise ApprovedMemoryActionInvalid("MNEMO_MEMORY_ACTION_INVALID")
    pinned = value["pinned"]
    if not isinstance(pinned, bool):
        raise ApprovedMemoryActionInvalid("MNEMO_MEMORY_ACTION_INVALID")
    typed_event_id = _event_id(event_id)
    digest = _action_digest("pinned" if pinned else "unpinned", typed_event_id, "", None)
    evidence = _governance_evidence(typed_event_id, digest, datetime.now(UTC))
    binding = _mutation_binding(config, project_directory)
    try:
        with build_checkpoint_runtime(config) as runtime:
            result = runtime.checkpoint_service.set_approved_event_pin(
                SetApprovedEpisodicEventPin(
                    binding.checkpoint_scope,
                    typed_event_id,
                    pinned,
                    f"web-pin-action:{typed_event_id}:{digest[:32]}",
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
    return {"idempotent": result.idempotent, "record": _record_value(result.record)}


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
        "pinned": record.pinned,
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


def _export_record_value(record: ApprovedEpisodicEventRecord) -> dict[str, object]:
    return {
        "event_id": str(record.event_id),
        "scope": record.scope.to_dict(),
        "status": record.status.value,
        "pinned": record.pinned,
        "event": None if record.event is None else record.event.to_dict(),
        "governance": None if record.governance is None else record.governance.to_dict(),
    }


def _canonical_json(value: dict[str, object]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


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
