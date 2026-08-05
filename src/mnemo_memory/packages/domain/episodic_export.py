"""Portable integrity-verifiable export of one exact production episodic scope."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Self, cast

from .episodic_candidates import (
    ActiveEpisodicMemory,
    EpisodicCandidateReviewAction,
    EpisodicCandidateReviewDecision,
    EpisodicMemoryCandidate,
    EpisodicMemoryGovernanceAction,
    EpisodicMemoryRevision,
    replay_episodic_memory_revisions,
)
from .episodic_deletion import EpisodicMemoryDeletion, TaskActivityEventDeletion
from .episodic_retention import EpisodicMemoryExpiration, EpisodicMemoryPurge
from .models import MemoryScope, ScopeLevel, _parse_datetime, _require_aware
from .task_activity_events import TaskActivityEvent
from .task_activity_retention import TaskActivityEventExpiration, TaskActivityEventPurge

EPISODIC_EXPORT_FORMAT = "mnemo.episodic-export.v1"


def _canonical_json(value: Mapping[str, object]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(value: Mapping[str, object]) -> str:
    encoded = _canonical_json(value).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class EpisodicExportBundle:
    format_version: str
    scope: MemoryScope
    exported_at: datetime
    task_events: tuple[TaskActivityEvent, ...]
    candidates: tuple[EpisodicMemoryCandidate, ...]
    reviews: tuple[EpisodicCandidateReviewAction, ...]
    governance_actions: tuple[EpisodicMemoryGovernanceAction, ...]
    revisions: tuple[EpisodicMemoryRevision, ...]
    memory_expirations: tuple[EpisodicMemoryExpiration, ...]
    memory_purges: tuple[EpisodicMemoryPurge, ...]
    task_expirations: tuple[TaskActivityEventExpiration, ...]
    task_purges: tuple[TaskActivityEventPurge, ...]
    memory_deletions: tuple[EpisodicMemoryDeletion, ...]
    task_deletions: tuple[TaskActivityEventDeletion, ...]
    content_digest: str

    def __post_init__(self) -> None:
        if self.format_version != EPISODIC_EXPORT_FORMAT:
            raise ValueError("episodic export format is unsupported")
        if not isinstance(self.scope, MemoryScope) or self.scope.level is not ScopeLevel.TASK:
            raise ValueError("episodic export requires exact task scope")
        _require_aware(self.exported_at, "exported_at")
        self._validate_types_and_scope()
        self._validate_canonical_order()
        self._validate_identities_and_relationships()
        expected = _digest(self._content_dict())
        if self.content_digest != expected:
            raise ValueError("episodic export content digest does not match")

    @classmethod
    def create(
        cls,
        *,
        scope: MemoryScope,
        exported_at: datetime,
        task_events: tuple[TaskActivityEvent, ...] = (),
        candidates: tuple[EpisodicMemoryCandidate, ...] = (),
        reviews: tuple[EpisodicCandidateReviewAction, ...] = (),
        governance_actions: tuple[EpisodicMemoryGovernanceAction, ...] = (),
        revisions: tuple[EpisodicMemoryRevision, ...] = (),
        memory_expirations: tuple[EpisodicMemoryExpiration, ...] = (),
        memory_purges: tuple[EpisodicMemoryPurge, ...] = (),
        task_expirations: tuple[TaskActivityEventExpiration, ...] = (),
        task_purges: tuple[TaskActivityEventPurge, ...] = (),
        memory_deletions: tuple[EpisodicMemoryDeletion, ...] = (),
        task_deletions: tuple[TaskActivityEventDeletion, ...] = (),
    ) -> Self:
        ordered_governance = tuple(
            action
            for memory_id in sorted({action.memory_id for action in governance_actions}, key=str)
            for action in governance_actions
            if action.memory_id == memory_id
        )
        content: dict[str, object] = {
            "format_version": EPISODIC_EXPORT_FORMAT,
            "scope": scope.to_dict(),
            "exported_at": exported_at.isoformat(),
            "task_events": [
                item.to_dict() for item in sorted(task_events, key=lambda item: str(item.event_id))
            ],
            "candidates": [
                item.to_dict() for item in sorted(candidates, key=lambda item: str(item.memory_id))
            ],
            "reviews": [
                item.to_dict() for item in sorted(reviews, key=lambda item: str(item.candidate_id))
            ],
            "governance_actions": [item.to_dict() for item in ordered_governance],
            "revisions": [
                item.to_dict()
                for item in sorted(
                    revisions,
                    key=lambda item: (str(item.memory_id), item.revision_number),
                )
            ],
            "memory_expirations": [
                item.to_dict()
                for item in sorted(memory_expirations, key=lambda item: str(item.memory_id))
            ],
            "memory_purges": [
                item.to_dict()
                for item in sorted(memory_purges, key=lambda item: str(item.memory_id))
            ],
            "task_expirations": [
                item.to_dict()
                for item in sorted(task_expirations, key=lambda item: str(item.event_id))
            ],
            "task_purges": [
                item.to_dict() for item in sorted(task_purges, key=lambda item: str(item.event_id))
            ],
            "memory_deletions": [
                item.to_dict()
                for item in sorted(memory_deletions, key=lambda item: str(item.memory_id))
            ],
            "task_deletions": [
                item.to_dict()
                for item in sorted(task_deletions, key=lambda item: str(item.event_id))
            ],
        }
        return cls._from_content(content, _digest(content))

    def _content_dict(self) -> dict[str, object]:
        return {
            "format_version": self.format_version,
            "scope": self.scope.to_dict(),
            "exported_at": self.exported_at.isoformat(),
            "task_events": [item.to_dict() for item in self.task_events],
            "candidates": [item.to_dict() for item in self.candidates],
            "reviews": [item.to_dict() for item in self.reviews],
            "governance_actions": [item.to_dict() for item in self.governance_actions],
            "revisions": [item.to_dict() for item in self.revisions],
            "memory_expirations": [item.to_dict() for item in self.memory_expirations],
            "memory_purges": [item.to_dict() for item in self.memory_purges],
            "task_expirations": [item.to_dict() for item in self.task_expirations],
            "task_purges": [item.to_dict() for item in self.task_purges],
            "memory_deletions": [item.to_dict() for item in self.memory_deletions],
            "task_deletions": [item.to_dict() for item in self.task_deletions],
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "content_digest": self.content_digest}

    def canonical_json(self) -> str:
        return _canonical_json(self.to_dict())

    @classmethod
    def from_json(cls, value: str) -> Self:
        if not isinstance(value, str):
            raise TypeError("episodic export JSON must be text")

        def reject_constant(constant: str) -> None:
            raise ValueError(f"invalid JSON constant: {constant}")

        decoded = json.loads(value, parse_constant=reject_constant)
        if not isinstance(decoded, Mapping):
            raise TypeError("episodic export JSON must contain an object")
        return cls.from_dict(decoded)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        expected = {
            "format_version",
            "scope",
            "exported_at",
            "task_events",
            "candidates",
            "reviews",
            "governance_actions",
            "revisions",
            "memory_expirations",
            "memory_purges",
            "task_expirations",
            "task_purges",
            "memory_deletions",
            "task_deletions",
            "content_digest",
        }
        if set(value) != expected:
            raise ValueError("episodic export fields are invalid")
        digest = value["content_digest"]
        if not isinstance(digest, str):
            raise TypeError("episodic export content digest is invalid")
        content = {name: value[name] for name in expected if name != "content_digest"}
        return cls._from_content(content, digest)

    @classmethod
    def _from_content(cls, value: Mapping[str, object], digest: str) -> Self:
        scope = value["scope"]
        format_version = value["format_version"]
        exported_at = value["exported_at"]
        list_fields = (
            "task_events",
            "candidates",
            "reviews",
            "governance_actions",
            "revisions",
            "memory_expirations",
            "memory_purges",
            "task_expirations",
            "task_purges",
            "memory_deletions",
            "task_deletions",
        )
        if (
            not isinstance(scope, Mapping)
            or not isinstance(format_version, str)
            or not isinstance(exported_at, str)
        ):
            raise TypeError("episodic export serialization is invalid")
        lists: dict[str, list[Mapping[str, object]]] = {}
        for name in list_fields:
            raw = value[name]
            if not isinstance(raw, list) or not all(isinstance(item, Mapping) for item in raw):
                raise TypeError("episodic export serialization is invalid")
            lists[name] = cast(list[Mapping[str, object]], raw)
        return cls(
            format_version,
            MemoryScope.from_dict(scope),
            _parse_datetime(exported_at, "exported_at"),
            tuple(TaskActivityEvent.from_dict(item) for item in lists["task_events"]),
            tuple(EpisodicMemoryCandidate.from_dict(item) for item in lists["candidates"]),
            tuple(EpisodicCandidateReviewAction.from_dict(item) for item in lists["reviews"]),
            tuple(
                EpisodicMemoryGovernanceAction.from_dict(item)
                for item in lists["governance_actions"]
            ),
            tuple(EpisodicMemoryRevision.from_dict(item) for item in lists["revisions"]),
            tuple(EpisodicMemoryExpiration.from_dict(item) for item in lists["memory_expirations"]),
            tuple(EpisodicMemoryPurge.from_dict(item) for item in lists["memory_purges"]),
            tuple(
                TaskActivityEventExpiration.from_dict(item) for item in lists["task_expirations"]
            ),
            tuple(TaskActivityEventPurge.from_dict(item) for item in lists["task_purges"]),
            tuple(EpisodicMemoryDeletion.from_dict(item) for item in lists["memory_deletions"]),
            tuple(TaskActivityEventDeletion.from_dict(item) for item in lists["task_deletions"]),
            digest,
        )

    def _validate_types_and_scope(self) -> None:
        groups: tuple[tuple[object, ...], ...] = (
            self.task_events,
            self.candidates,
            self.reviews,
            self.governance_actions,
            self.revisions,
            self.memory_expirations,
            self.memory_purges,
            self.task_expirations,
            self.task_purges,
            self.memory_deletions,
            self.task_deletions,
        )
        expected_types = (
            TaskActivityEvent,
            EpisodicMemoryCandidate,
            EpisodicCandidateReviewAction,
            EpisodicMemoryGovernanceAction,
            EpisodicMemoryRevision,
            EpisodicMemoryExpiration,
            EpisodicMemoryPurge,
            TaskActivityEventExpiration,
            TaskActivityEventPurge,
            EpisodicMemoryDeletion,
            TaskActivityEventDeletion,
        )
        for values, expected_type in zip(groups, expected_types, strict=True):
            if any(
                not isinstance(item, expected_type) or getattr(item, "scope", None) != self.scope
                for item in values
            ):
                raise ValueError("episodic export contains invalid or cross-scope state")

    def _validate_identities_and_relationships(self) -> None:
        task_ids = {item.event_id for item in self.task_events}
        candidate_ids = {item.memory_id for item in self.candidates}
        if len(task_ids) != len(self.task_events) or len(candidate_ids) != len(self.candidates):
            raise ValueError("episodic export contains duplicate payload identities")
        memory_expiration_ids = {item.memory_id for item in self.memory_expirations}
        memory_purge_ids = {item.memory_id for item in self.memory_purges}
        memory_deletion_ids = {item.memory_id for item in self.memory_deletions}
        task_expiration_ids = {item.event_id for item in self.task_expirations}
        task_purge_ids = {item.event_id for item in self.task_purges}
        task_deletion_ids = {item.event_id for item in self.task_deletions}
        groups = (
            (memory_expiration_ids, self.memory_expirations),
            (memory_purge_ids, self.memory_purges),
            (memory_deletion_ids, self.memory_deletions),
            (task_expiration_ids, self.task_expirations),
            (task_purge_ids, self.task_purges),
            (task_deletion_ids, self.task_deletions),
        )
        if any(len(identities) != len(values) for identities, values in groups):
            raise ValueError("episodic export contains duplicate tombstone identities")
        if candidate_ids & (memory_expiration_ids | memory_deletion_ids):
            raise ValueError("episodic export includes excluded memory payload")
        if task_ids & (task_expiration_ids | task_deletion_ids):
            raise ValueError("episodic export includes excluded task payload")
        if (
            not memory_purge_ids <= memory_expiration_ids
            or not task_purge_ids <= task_expiration_ids
        ):
            raise ValueError("episodic export purge has no matching expiration")
        memory_expiration_by_id = {item.memory_id: item for item in self.memory_expirations}
        if any(
            item.expiration_id != memory_expiration_by_id[item.memory_id].expiration_id
            for item in self.memory_purges
        ):
            raise ValueError("episodic export memory purge relationship is invalid")
        task_expiration_by_id = {item.event_id: item for item in self.task_expirations}
        if any(
            item.expiration_id != task_expiration_by_id[item.event_id].expiration_id
            for item in self.task_purges
        ):
            raise ValueError("episodic export task purge relationship is invalid")
        known_source_ids = task_ids | task_expiration_ids | task_deletion_ids
        if (
            any(item.source_event_id not in known_source_ids for item in self.candidates)
            or any(item.source_event_id not in known_source_ids for item in self.memory_expirations)
            or any(item.source_event_id not in known_source_ids for item in self.memory_deletions)
        ):
            raise ValueError("episodic export memory source relationship is invalid")
        source_deletion_by_id = {item.deletion_id: item for item in self.task_deletions}
        if any(
            item.source_deletion_id is not None
            and (
                item.source_deletion_id not in source_deletion_by_id
                or source_deletion_by_id[item.source_deletion_id].event_id != item.source_event_id
            )
            for item in self.memory_deletions
        ):
            raise ValueError("episodic export source deletion relationship is invalid")
        if any(item.source_event_id not in task_ids for item in self.candidates):
            raise ValueError("episodic export candidate source is unavailable")
        candidate_by_id = {item.memory_id: item for item in self.candidates}
        review_by_candidate = {item.candidate_id: item for item in self.reviews}
        if (
            len(review_by_candidate) != len(self.reviews)
            or not set(review_by_candidate) <= candidate_ids
        ):
            raise ValueError("episodic export review relationship is invalid")
        governance_by_memory: dict[object, list[EpisodicMemoryGovernanceAction]] = {}
        for action in self.governance_actions:
            governance_by_memory.setdefault(action.memory_id, []).append(action)
        revision_by_memory: dict[object, list[EpisodicMemoryRevision]] = {}
        for revision in self.revisions:
            revision_by_memory.setdefault(revision.memory_id, []).append(revision)
        if (
            not set(governance_by_memory) <= candidate_ids
            or not set(revision_by_memory) <= candidate_ids
        ):
            raise ValueError("episodic export revision relationship is invalid")
        for memory_id, candidate in candidate_by_id.items():
            review = review_by_candidate.get(memory_id)
            actions = tuple(governance_by_memory.get(memory_id, ()))
            revisions = tuple(revision_by_memory.get(memory_id, ()))
            if review is None or review.decision is EpisodicCandidateReviewDecision.REJECTED:
                if actions or revisions:
                    raise ValueError("inactive episodic export memory has revision state")
                continue
            expected = replay_episodic_memory_revisions(
                ActiveEpisodicMemory.approve(candidate, review), actions
            )
            if revisions != expected:
                raise ValueError("episodic export revision replay does not match")

    def _validate_canonical_order(self) -> None:
        ordered = (
            (
                self.task_events,
                tuple(sorted(self.task_events, key=lambda item: str(item.event_id))),
            ),
            (
                self.candidates,
                tuple(sorted(self.candidates, key=lambda item: str(item.memory_id))),
            ),
            (
                self.reviews,
                tuple(sorted(self.reviews, key=lambda item: str(item.candidate_id))),
            ),
            (
                self.revisions,
                tuple(
                    sorted(
                        self.revisions,
                        key=lambda item: (
                            str(item.memory_id),
                            item.revision_number,
                        ),
                    )
                ),
            ),
            (
                self.memory_expirations,
                tuple(
                    sorted(
                        self.memory_expirations,
                        key=lambda item: str(item.memory_id),
                    )
                ),
            ),
            (
                self.memory_purges,
                tuple(sorted(self.memory_purges, key=lambda item: str(item.memory_id))),
            ),
            (
                self.task_expirations,
                tuple(
                    sorted(
                        self.task_expirations,
                        key=lambda item: str(item.event_id),
                    )
                ),
            ),
            (
                self.task_purges,
                tuple(sorted(self.task_purges, key=lambda item: str(item.event_id))),
            ),
            (
                self.memory_deletions,
                tuple(
                    sorted(
                        self.memory_deletions,
                        key=lambda item: str(item.memory_id),
                    )
                ),
            ),
            (
                self.task_deletions,
                tuple(sorted(self.task_deletions, key=lambda item: str(item.event_id))),
            ),
        )
        if any(actual != expected for actual, expected in ordered):
            raise ValueError("episodic export arrays are not canonically ordered")
        governance_memory_ids = [str(item.memory_id) for item in self.governance_actions]
        if governance_memory_ids != sorted(governance_memory_ids):
            raise ValueError("episodic export governance is not canonically grouped")
