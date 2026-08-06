"""Portable approved-event history for one exact task scope."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Self, cast
from uuid import UUID, uuid5

from .approved_episodic_events import (
    ApprovedEpisodicEvent,
    ApprovedEpisodicEventGovernance,
    ApprovedEpisodicEventPinAction,
    ApprovedEventGovernanceKind,
)
from .identifiers import EventId
from .models import MemoryScope, ScopeLevel, _parse_datetime, _require_aware

APPROVED_EVENT_EXPORT_FORMAT = "mnemo.approved-event-export.v1"
_ERASED_EVENT_NAMESPACE = UUID("23f2d29f-9b04-42e0-af70-58c5d539b648")


def approved_event_import_identity(scope: MemoryScope, source_event_id: EventId) -> EventId:
    """Map an erased source event identity into a target scope without payload."""
    if not isinstance(scope, MemoryScope) or scope.level is not ScopeLevel.TASK:
        raise ValueError("approved event import identity requires exact task scope")
    if not isinstance(source_event_id, EventId):
        raise TypeError("approved event import source identity is invalid")
    return EventId(uuid5(_ERASED_EVENT_NAMESPACE, f"{scope.to_dict()}:{source_event_id}"))


def _canonical_json(value: Mapping[str, object]) -> str:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )


def _digest(value: Mapping[str, object]) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ApprovedEventPinHistoryEntry:
    """One pin action with its portable mutation order."""

    sequence: int
    action: ApprovedEpisodicEventPinAction

    def __post_init__(self) -> None:
        if (
            not isinstance(self.sequence, int)
            or isinstance(self.sequence, bool)
            or self.sequence < 1
        ):
            raise ValueError("approved event pin history sequence must be positive")
        if not isinstance(self.action, ApprovedEpisodicEventPinAction):
            raise TypeError("approved event pin history action is invalid")

    def to_dict(self) -> dict[str, object]:
        return {"sequence": self.sequence, "action": self.action.to_dict()}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        if set(value) != {"sequence", "action"}:
            raise ValueError("approved event pin history fields are invalid")
        sequence = value["sequence"]
        action = value["action"]
        if (
            not isinstance(sequence, int)
            or isinstance(sequence, bool)
            or not isinstance(action, Mapping)
        ):
            raise TypeError("approved event pin history serialization is invalid")
        return cls(sequence, ApprovedEpisodicEventPinAction.from_dict(action))


@dataclass(frozen=True, slots=True)
class ApprovedEventExportBundle:
    """Live payload plus complete immutable governance for one task scope."""

    format_version: str
    scope: MemoryScope
    exported_at: datetime
    events: tuple[ApprovedEpisodicEvent, ...]
    governance_actions: tuple[ApprovedEpisodicEventGovernance, ...]
    pin_history: tuple[ApprovedEventPinHistoryEntry, ...]
    content_digest: str

    def __post_init__(self) -> None:
        if self.format_version != APPROVED_EVENT_EXPORT_FORMAT:
            raise ValueError("approved event export format is unsupported")
        if not isinstance(self.scope, MemoryScope) or self.scope.level is not ScopeLevel.TASK:
            raise ValueError("approved event export requires exact task scope")
        _require_aware(self.exported_at, "exported_at")
        self._validate()
        if self.content_digest != _digest(self._content_dict()):
            raise ValueError("approved event export content digest does not match")

    @classmethod
    def create(
        cls,
        *,
        scope: MemoryScope,
        exported_at: datetime,
        events: tuple[ApprovedEpisodicEvent, ...] = (),
        governance_actions: tuple[ApprovedEpisodicEventGovernance, ...] = (),
        pin_actions: tuple[ApprovedEpisodicEventPinAction, ...] = (),
    ) -> Self:
        content: dict[str, object] = {
            "format_version": APPROVED_EVENT_EXPORT_FORMAT,
            "scope": scope.to_dict(),
            "exported_at": exported_at.isoformat(),
            "events": [
                item.to_dict() for item in sorted(events, key=lambda item: str(item.event_id))
            ],
            "governance_actions": [
                item.to_dict()
                for item in sorted(governance_actions, key=lambda item: str(item.target_event_id))
            ],
            "pin_history": [
                ApprovedEventPinHistoryEntry(index, action).to_dict()
                for index, action in enumerate(pin_actions, 1)
            ],
        }
        return cls._from_content(content, _digest(content))

    def _content_dict(self) -> dict[str, object]:
        return {
            "format_version": self.format_version,
            "scope": self.scope.to_dict(),
            "exported_at": self.exported_at.isoformat(),
            "events": [item.to_dict() for item in self.events],
            "governance_actions": [item.to_dict() for item in self.governance_actions],
            "pin_history": [item.to_dict() for item in self.pin_history],
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "content_digest": self.content_digest}

    def canonical_json(self) -> str:
        return _canonical_json(self.to_dict())

    @classmethod
    def from_json(cls, value: str) -> Self:
        if not isinstance(value, str):
            raise TypeError("approved event export JSON must be text")

        def reject_constant(constant: str) -> None:
            raise ValueError(f"invalid JSON constant: {constant}")

        decoded = json.loads(value, parse_constant=reject_constant)
        if not isinstance(decoded, Mapping):
            raise TypeError("approved event export JSON must contain an object")
        return cls.from_dict(decoded)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        expected = {
            "format_version",
            "scope",
            "exported_at",
            "events",
            "governance_actions",
            "pin_history",
            "content_digest",
        }
        if set(value) != expected:
            raise ValueError("approved event export fields are invalid")
        digest = value["content_digest"]
        if not isinstance(digest, str):
            raise TypeError("approved event export digest is invalid")
        return cls._from_content(
            {name: value[name] for name in expected if name != "content_digest"}, digest
        )

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
            raise TypeError("approved event export serialization is invalid")
        raw_lists: dict[str, list[Mapping[str, object]]] = {}
        for name in ("events", "governance_actions", "pin_history"):
            raw = value[name]
            if not isinstance(raw, list) or not all(isinstance(item, Mapping) for item in raw):
                raise TypeError("approved event export serialization is invalid")
            raw_lists[name] = cast(list[Mapping[str, object]], raw)
        return cls(
            format_version,
            MemoryScope.from_dict(scope),
            _parse_datetime(exported_at, "exported_at"),
            tuple(ApprovedEpisodicEvent.from_dict(item) for item in raw_lists["events"]),
            tuple(
                ApprovedEpisodicEventGovernance.from_dict(item)
                for item in raw_lists["governance_actions"]
            ),
            tuple(
                ApprovedEventPinHistoryEntry.from_dict(item) for item in raw_lists["pin_history"]
            ),
            digest,
        )

    def _validate(self) -> None:
        if any(item.scope != self.scope for item in self.events):
            raise ValueError("approved event export contains cross-scope events")
        if any(item.scope != self.scope for item in self.governance_actions):
            raise ValueError("approved event export contains cross-scope governance")
        if any(item.action.scope != self.scope for item in self.pin_history):
            raise ValueError("approved event export contains cross-scope pin actions")
        if self.events != tuple(sorted(self.events, key=lambda item: str(item.event_id))):
            raise ValueError("approved event export events are not canonically ordered")
        if self.governance_actions != tuple(
            sorted(self.governance_actions, key=lambda item: str(item.target_event_id))
        ):
            raise ValueError("approved event export governance is not canonically ordered")
        if tuple(item.sequence for item in self.pin_history) != tuple(
            range(1, len(self.pin_history) + 1)
        ):
            raise ValueError("approved event export pin history is not contiguous")

        event_by_id = {item.event_id: item for item in self.events}
        event_ids = set(event_by_id)
        event_keys = {item.source_event_key for item in self.events}
        targets = {item.target_event_id for item in self.governance_actions}
        action_ids = {item.action_id for item in self.governance_actions}
        action_keys = {item.source_action_key for item in self.governance_actions}
        pin_ids = {item.action.action_id for item in self.pin_history}
        pin_keys = {item.action.source_action_key for item in self.pin_history}
        if len(event_ids) != len(self.events) or len(event_keys) != len(self.events):
            raise ValueError("approved event export contains duplicate events")
        if (
            len(targets) != len(self.governance_actions)
            or len(action_ids) != len(self.governance_actions)
            or len(action_keys) != len(self.governance_actions)
        ):
            raise ValueError("approved event export contains duplicate governance")
        if len(pin_ids) != len(self.pin_history) or len(pin_keys) != len(self.pin_history):
            raise ValueError("approved event export contains duplicate pin actions")
        for event in self.events:
            if (
                ApprovedEpisodicEvent.create(
                    scope=event.scope,
                    kind=event.kind,
                    summary=event.summary,
                    source_event_key=event.source_event_key,
                    occurred_at=event.occurred_at,
                    evidence_references=event.evidence_references,
                ).event_id
                != event.event_id
            ):
                raise ValueError("approved event export event identity is not deterministic")
        for action in self.governance_actions:
            rebuilt = ApprovedEpisodicEventGovernance.create(
                scope=action.scope,
                kind=action.kind,
                target_event_id=action.target_event_id,
                replacement_event_id=action.replacement_event_id,
                reason=action.reason,
                source_action_key=action.source_action_key,
                occurred_at=action.occurred_at,
                evidence_references=action.evidence_references,
            )
            if rebuilt.action_id != action.action_id:
                raise ValueError("approved event export governance identity is not deterministic")
            if action.kind is ApprovedEventGovernanceKind.RETRACTED:
                if action.target_event_id in event_ids:
                    raise ValueError("approved event export retains a retracted payload")
            else:
                if action.target_event_id not in event_ids:
                    raise ValueError("approved event export correction target is missing")
                replacement = action.replacement_event_id
                if replacement not in event_ids and replacement not in targets:
                    raise ValueError("approved event export correction replacement is missing")
                if (
                    replacement in event_by_id
                    and event_by_id[replacement].kind
                    is not event_by_id[action.target_event_id].kind
                ):
                    raise ValueError("approved event export correction changes event kind")
        correction_edges = {
            item.target_event_id: item.replacement_event_id
            for item in self.governance_actions
            if item.kind is ApprovedEventGovernanceKind.CORRECTED
        }
        for start in correction_edges:
            visited = {start}
            current = correction_edges[start]
            while current in correction_edges:
                if current in visited:
                    raise ValueError("approved event export correction chain is cyclic")
                visited.add(current)
                current = correction_edges[current]
        known_ids = event_ids | targets
        current_pin: dict[object, bool] = {}
        for entry in self.pin_history:
            if entry.action.event_id not in known_ids:
                raise ValueError("approved event export pin target is unknown")
            current_pin[entry.action.event_id] = entry.action.pinned
        if any(current_pin.get(target, False) for target in targets):
            raise ValueError("approved event export governed target remains pinned")
