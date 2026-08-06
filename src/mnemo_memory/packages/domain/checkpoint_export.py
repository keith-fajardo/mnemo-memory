"""Portable integrity-verifiable checkpoint history for one exact task scope."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Self, cast

from .checkpoint_deletion import CheckpointDeletion
from .episodic_events import CheckpointEventKind, CheckpointLifecycleEvent
from .identifiers import CheckpointId, CheckpointRevisionId
from .models import (
    CheckpointAggregate,
    CheckpointRevision,
    CheckpointStatus,
    MemoryScope,
    ScopeLevel,
    _parse_datetime,
    _require_aware,
)

_CHECKPOINT_EXPORT_FORMAT_V1 = "mnemo.checkpoint-export.v1"
CHECKPOINT_EXPORT_FORMAT = "mnemo.checkpoint-export.v2"


def _canonical_json(value: Mapping[str, object]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(value: Mapping[str, object]) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _aggregate_to_dict(aggregate: CheckpointAggregate) -> dict[str, object]:
    return {
        "checkpoint_id": str(aggregate.checkpoint_id),
        "scope": aggregate.scope.to_dict(),
        "current_revision_id": str(aggregate.current_revision_id),
        "current_revision_number": aggregate.current_revision_number,
        "lifecycle_status": aggregate.lifecycle_status.value,
        "created_at": aggregate.created_at.isoformat(),
        "updated_at": aggregate.updated_at.isoformat(),
    }


def _aggregate_from_dict(value: Mapping[str, object]) -> CheckpointAggregate:
    expected = {
        "checkpoint_id",
        "scope",
        "current_revision_id",
        "current_revision_number",
        "lifecycle_status",
        "created_at",
        "updated_at",
    }
    if set(value) != expected:
        raise ValueError("checkpoint export aggregate fields are invalid")
    scope = value["scope"]
    checkpoint_id = value["checkpoint_id"]
    revision_id = value["current_revision_id"]
    revision_number = value["current_revision_number"]
    lifecycle_status = value["lifecycle_status"]
    if (
        not isinstance(scope, Mapping)
        or not isinstance(checkpoint_id, str)
        or not isinstance(revision_id, str)
        or not isinstance(revision_number, int)
        or isinstance(revision_number, bool)
        or not isinstance(lifecycle_status, str)
    ):
        raise TypeError("checkpoint export aggregate serialization is invalid")
    return CheckpointAggregate(
        CheckpointId.from_string(checkpoint_id),
        MemoryScope.from_dict(scope),
        CheckpointRevisionId.from_string(revision_id),
        revision_number,
        CheckpointStatus(lifecycle_status),
        _parse_datetime(value["created_at"], "created_at"),
        _parse_datetime(value["updated_at"], "updated_at"),
    )


@dataclass(frozen=True, slots=True)
class CheckpointExportBundle:
    """Every canonical checkpoint revision and lifecycle fact in one task scope."""

    format_version: str
    scope: MemoryScope
    exported_at: datetime
    aggregates: tuple[CheckpointAggregate, ...]
    revisions: tuple[CheckpointRevision, ...]
    lifecycle_events: tuple[CheckpointLifecycleEvent, ...]
    deletions: tuple[CheckpointDeletion, ...]
    content_digest: str

    def __post_init__(self) -> None:
        if self.format_version not in {_CHECKPOINT_EXPORT_FORMAT_V1, CHECKPOINT_EXPORT_FORMAT}:
            raise ValueError("checkpoint export format is unsupported")
        if self.format_version == _CHECKPOINT_EXPORT_FORMAT_V1 and self.deletions:
            raise ValueError("checkpoint export version 1 cannot contain deletions")
        if not isinstance(self.scope, MemoryScope) or self.scope.level is not ScopeLevel.TASK:
            raise ValueError("checkpoint export requires exact task scope")
        _require_aware(self.exported_at, "exported_at")
        self._validate_types_scope_and_order()
        self._validate_history()
        if self.content_digest != _digest(self._content_dict()):
            raise ValueError("checkpoint export content digest does not match")

    @classmethod
    def create(
        cls,
        *,
        scope: MemoryScope,
        exported_at: datetime,
        aggregates: tuple[CheckpointAggregate, ...] = (),
        revisions: tuple[CheckpointRevision, ...] = (),
        lifecycle_events: tuple[CheckpointLifecycleEvent, ...] = (),
        deletions: tuple[CheckpointDeletion, ...] = (),
    ) -> Self:
        content: dict[str, object] = {
            "format_version": CHECKPOINT_EXPORT_FORMAT,
            "scope": scope.to_dict(),
            "exported_at": exported_at.isoformat(),
            "aggregates": [
                _aggregate_to_dict(item)
                for item in sorted(aggregates, key=lambda item: str(item.checkpoint_id))
            ],
            "revisions": [
                item.to_dict()
                for item in sorted(
                    revisions,
                    key=lambda item: (str(item.checkpoint_id), item.revision_number),
                )
            ],
            "lifecycle_events": [
                item.to_dict()
                for item in sorted(
                    lifecycle_events,
                    key=lambda item: (str(item.checkpoint_id), item.revision_number),
                )
            ],
            "deletions": [
                item.to_dict()
                for item in sorted(deletions, key=lambda item: str(item.checkpoint_id))
            ],
        }
        return cls._from_content(content, _digest(content))

    def _content_dict(self) -> dict[str, object]:
        content: dict[str, object] = {
            "format_version": self.format_version,
            "scope": self.scope.to_dict(),
            "exported_at": self.exported_at.isoformat(),
            "aggregates": [_aggregate_to_dict(item) for item in self.aggregates],
            "revisions": [item.to_dict() for item in self.revisions],
            "lifecycle_events": [item.to_dict() for item in self.lifecycle_events],
        }
        if self.format_version == CHECKPOINT_EXPORT_FORMAT:
            content["deletions"] = [item.to_dict() for item in self.deletions]
        return content

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "content_digest": self.content_digest}

    def canonical_json(self) -> str:
        return _canonical_json(self.to_dict())

    @classmethod
    def from_json(cls, value: str) -> Self:
        if not isinstance(value, str):
            raise TypeError("checkpoint export JSON must be text")

        def reject_constant(constant: str) -> None:
            raise ValueError(f"invalid JSON constant: {constant}")

        decoded = json.loads(value, parse_constant=reject_constant)
        if not isinstance(decoded, Mapping):
            raise TypeError("checkpoint export JSON must contain an object")
        return cls.from_dict(decoded)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        format_version = value.get("format_version")
        if format_version == _CHECKPOINT_EXPORT_FORMAT_V1:
            expected = {
                "format_version",
                "scope",
                "exported_at",
                "aggregates",
                "revisions",
                "lifecycle_events",
                "content_digest",
            }
        elif format_version == CHECKPOINT_EXPORT_FORMAT:
            expected = {
                "format_version",
                "scope",
                "exported_at",
                "aggregates",
                "revisions",
                "lifecycle_events",
                "deletions",
                "content_digest",
            }
        else:
            raise ValueError("checkpoint export format is unsupported")
        if set(value) != expected:
            raise ValueError("checkpoint export fields are invalid")
        content = {name: value[name] for name in expected if name != "content_digest"}
        digest = value["content_digest"]
        if not isinstance(digest, str):
            raise TypeError("checkpoint export content digest is invalid")
        return cls._from_content(content, digest)

    @classmethod
    def _from_content(cls, value: Mapping[str, object], digest: str) -> Self:
        scope = value["scope"]
        format_version = value["format_version"]
        exported_at = value["exported_at"]
        if (
            not isinstance(scope, Mapping)
            or not isinstance(format_version, str)
            or not isinstance(exported_at, str)
        ):
            raise TypeError("checkpoint export serialization is invalid")
        lists: dict[str, list[Mapping[str, object]]] = {}
        names = ["aggregates", "revisions", "lifecycle_events"]
        if format_version == CHECKPOINT_EXPORT_FORMAT:
            names.append("deletions")
        for name in names:
            raw = value[name]
            if not isinstance(raw, list) or not all(isinstance(item, Mapping) for item in raw):
                raise TypeError("checkpoint export serialization is invalid")
            lists[name] = cast(list[Mapping[str, object]], raw)
        return cls(
            format_version,
            MemoryScope.from_dict(scope),
            _parse_datetime(exported_at, "exported_at"),
            tuple(_aggregate_from_dict(item) for item in lists["aggregates"]),
            tuple(CheckpointRevision.from_dict(item) for item in lists["revisions"]),
            tuple(CheckpointLifecycleEvent.from_dict(item) for item in lists["lifecycle_events"]),
            tuple(CheckpointDeletion.from_dict(item) for item in lists.get("deletions", [])),
            digest,
        )

    def _validate_types_scope_and_order(self) -> None:
        if any(
            not isinstance(item, CheckpointAggregate) or item.scope != self.scope
            for item in self.aggregates
        ):
            raise ValueError("checkpoint export contains invalid or cross-scope aggregates")
        if any(
            not isinstance(item, CheckpointRevision) or item.scope != self.scope
            for item in self.revisions
        ):
            raise ValueError("checkpoint export contains invalid or cross-scope revisions")
        if any(
            not isinstance(item, CheckpointLifecycleEvent) or item.scope != self.scope
            for item in self.lifecycle_events
        ):
            raise ValueError("checkpoint export contains invalid or cross-scope events")
        if any(
            not isinstance(item, CheckpointDeletion) or item.scope != self.scope
            for item in self.deletions
        ):
            raise ValueError("checkpoint export contains invalid or cross-scope deletions")
        if self.aggregates != tuple(
            sorted(self.aggregates, key=lambda item: str(item.checkpoint_id))
        ):
            raise ValueError("checkpoint export aggregates are not canonically ordered")
        if self.revisions != tuple(
            sorted(
                self.revisions,
                key=lambda item: (str(item.checkpoint_id), item.revision_number),
            )
        ):
            raise ValueError("checkpoint export revisions are not canonically ordered")
        if self.lifecycle_events != tuple(
            sorted(
                self.lifecycle_events,
                key=lambda item: (str(item.checkpoint_id), item.revision_number),
            )
        ):
            raise ValueError("checkpoint export events are not canonically ordered")
        if self.deletions != tuple(
            sorted(self.deletions, key=lambda item: str(item.checkpoint_id))
        ):
            raise ValueError("checkpoint export deletions are not canonically ordered")

    def _validate_history(self) -> None:
        aggregate_by_id = {item.checkpoint_id: item for item in self.aggregates}
        if len(aggregate_by_id) != len(self.aggregates):
            raise ValueError("checkpoint export contains duplicate aggregates")
        revision_ids = {item.revision_id for item in self.revisions}
        event_ids = {item.event_id for item in self.lifecycle_events}
        event_keys = {item.idempotency_key for item in self.lifecycle_events}
        if len(revision_ids) != len(self.revisions):
            raise ValueError("checkpoint export contains duplicate revision identities")
        if len(event_ids) != len(self.lifecycle_events) or len(event_keys) != len(
            self.lifecycle_events
        ):
            raise ValueError("checkpoint export contains duplicate lifecycle identities")
        deletion_ids = {item.deletion_id for item in self.deletions}
        deletion_checkpoints = {item.checkpoint_id for item in self.deletions}
        deletion_keys = {item.source_action_key for item in self.deletions}
        if (
            len(deletion_ids) != len(self.deletions)
            or len(deletion_checkpoints) != len(self.deletions)
            or len(deletion_keys) != len(self.deletions)
        ):
            raise ValueError("checkpoint export contains duplicate deletion state")
        if deletion_checkpoints.intersection(aggregate_by_id):
            raise ValueError("checkpoint export retains deleted checkpoint payload")
        revisions_by_checkpoint: dict[CheckpointId, list[CheckpointRevision]] = {}
        events_by_checkpoint: dict[CheckpointId, list[CheckpointLifecycleEvent]] = {}
        for revision in self.revisions:
            revisions_by_checkpoint.setdefault(revision.checkpoint_id, []).append(revision)
        for event in self.lifecycle_events:
            events_by_checkpoint.setdefault(event.checkpoint_id, []).append(event)
        if set(revisions_by_checkpoint) != set(aggregate_by_id) or set(events_by_checkpoint) != set(
            aggregate_by_id
        ):
            raise ValueError("checkpoint export history has an unknown or missing aggregate")

        for checkpoint_id, aggregate in aggregate_by_id.items():
            revisions = revisions_by_checkpoint[checkpoint_id]
            events = events_by_checkpoint[checkpoint_id]
            if len(revisions) != len(events):
                raise ValueError("checkpoint export revision and event counts do not match")
            if [item.revision_number for item in revisions] != list(range(1, len(revisions) + 1)):
                raise ValueError("checkpoint export revision chain is not contiguous")
            if aggregate.created_at != revisions[0].created_at:
                raise ValueError("checkpoint export aggregate creation time does not match")
            if (
                aggregate.current_revision_id != revisions[-1].revision_id
                or aggregate.current_revision_number != revisions[-1].revision_number
                or aggregate.lifecycle_status is not revisions[-1].status
                or aggregate.updated_at != revisions[-1].created_at
            ):
                raise ValueError("checkpoint export aggregate current state does not match")
            if revisions[0].status is not CheckpointStatus.ACTIVE:
                raise ValueError("checkpoint export initial revision must be active")
            for index, (revision, event) in enumerate(zip(revisions, events, strict=True)):
                predecessor = None if index == 0 else revisions[index - 1].revision_id
                if revision.predecessor_revision_id != predecessor:
                    raise ValueError("checkpoint export revision predecessor does not match")
                if index < len(revisions) - 1 and revision.status is not CheckpointStatus.ACTIVE:
                    raise ValueError("checkpoint export has a revision after terminal state")
                if index == 0:
                    valid_kind = event.kind is CheckpointEventKind.CREATED
                elif revision.status is CheckpointStatus.ACTIVE:
                    valid_kind = event.kind in (
                        CheckpointEventKind.REVISED,
                        CheckpointEventKind.LESSON_RECORDED,
                    )
                elif revision.status is CheckpointStatus.COMPLETED:
                    valid_kind = event.kind is CheckpointEventKind.COMPLETED
                elif revision.status is CheckpointStatus.ABANDONED:
                    valid_kind = event.kind is CheckpointEventKind.ABANDONED
                else:
                    valid_kind = (
                        revision.status is CheckpointStatus.EXPIRED
                        and event.kind is CheckpointEventKind.EXPIRED
                    )
                if not valid_kind:
                    raise ValueError("checkpoint export lifecycle kind does not match revision")
                expected_event = CheckpointLifecycleEvent.for_revision(
                    scope=self.scope,
                    kind=event.kind,
                    checkpoint_id=checkpoint_id,
                    revision_id=revision.revision_id,
                    revision_number=revision.revision_number,
                    occurred_at=revision.created_at,
                    evidence_references=revision.evidence_references,
                )
                if event != expected_event:
                    raise ValueError("checkpoint export lifecycle event does not match revision")
