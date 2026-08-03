"""Storage-independent checkpoint lifecycle and context application use cases."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import TypeVar

from mnemo_memory.packages.domain import (
    DEFAULT_CONTEXT_BUDGET,
    CheckpointAggregate,
    CheckpointContent,
    CheckpointId,
    CheckpointLesson,
    CheckpointRevision,
    CheckpointRevisionId,
    CheckpointStatus,
    ConflictState,
    ContentRepresentation,
    ContextBudget,
    ContextItem,
    ContextItemType,
    ContextPacket,
    EvidenceId,
    EvidenceReference,
    MemoryScope,
    OmissionNotice,
    OmissionReason,
    PacketSchemaVersion,
    ProvenanceNotice,
    RequestId,
    ScopeLevel,
    Sensitivity,
    SourceTrustClass,
    ValidityState,
)
from mnemo_memory.packages.storage.contracts import (
    CheckpointNotFound,
    CheckpointRepository,
    DuplicateCheckpoint,
    InvalidAbandonmentReason,
    InvalidCheckpointScope,
    InvalidLifecycleTransition,
    RepositoryStorageFailure,
    RevisionConflict,
)

_ACTIVE_CHECKPOINT_PRODUCER = "mnemo-application/0.1.0"
_Result = TypeVar("_Result")
_MAX_HISTORICAL_LESSON_REVISIONS = 16
_MAX_HISTORICAL_LESSONS = 16


class CheckpointApplicationError(Exception):
    """Safe, storage-neutral outcome for checkpoint application callers."""


class CheckpointApplicationNotFound(CheckpointApplicationError):
    pass


class CheckpointApplicationDuplicate(CheckpointApplicationError):
    pass


class CheckpointApplicationRevisionConflict(CheckpointApplicationError):
    pass


class CheckpointApplicationInvalidLifecycle(CheckpointApplicationError):
    pass


class CheckpointApplicationInvalidScope(CheckpointApplicationError):
    pass


class CheckpointApplicationMissingProvenance(CheckpointApplicationError):
    pass


class CheckpointApplicationInvalidContent(CheckpointApplicationError):
    pass


class CheckpointApplicationBudgetExceeded(CheckpointApplicationError):
    pass


class CheckpointApplicationStorageFailure(CheckpointApplicationError):
    pass


@dataclass(frozen=True, slots=True)
class CreateCheckpoint:
    scope: MemoryScope
    content: CheckpointContent
    evidence_references: tuple[EvidenceReference, ...]
    checkpoint_id: CheckpointId | None = None


@dataclass(frozen=True, slots=True)
class ReviseCheckpoint:
    scope: MemoryScope
    checkpoint_id: CheckpointId
    expected_revision_id: CheckpointRevisionId
    content: CheckpointContent
    evidence_references: tuple[EvidenceReference, ...]


@dataclass(frozen=True, slots=True)
class CompleteCheckpoint:
    scope: MemoryScope
    checkpoint_id: CheckpointId
    expected_revision_id: CheckpointRevisionId
    content: CheckpointContent
    evidence_references: tuple[EvidenceReference, ...]


@dataclass(frozen=True, slots=True)
class AbandonCheckpoint:
    scope: MemoryScope
    checkpoint_id: CheckpointId
    expected_revision_id: CheckpointRevisionId
    reason: str
    content: CheckpointContent
    evidence_references: tuple[EvidenceReference, ...]


@dataclass(frozen=True, slots=True)
class RecordCheckpointLesson:
    """Append one correction lesson without making an agent resubmit the whole handoff."""

    scope: MemoryScope
    checkpoint_id: CheckpointId
    expected_revision_id: CheckpointRevisionId
    lesson: CheckpointLesson
    evidence_references: tuple[EvidenceReference, ...]


@dataclass(frozen=True, slots=True)
class GetCheckpoint:
    scope: MemoryScope
    checkpoint_id: CheckpointId
    revision_id: CheckpointRevisionId | None = None
    revision_number: int | None = None


@dataclass(frozen=True, slots=True)
class GetCheckpointContext:
    scope: MemoryScope
    checkpoint_id: CheckpointId | None = None
    budget: ContextBudget = DEFAULT_CONTEXT_BUDGET


@dataclass(frozen=True, slots=True)
class CheckpointView:
    aggregate: CheckpointAggregate
    revision: CheckpointRevision


class CheckpointApplicationService:
    """Coordinates canonical checkpoint commands without knowing a storage adapter."""

    def __init__(
        self,
        repository: CheckpointRepository,
        *,
        clock: Callable[[], datetime],
        checkpoint_id_factory: Callable[[], CheckpointId] = CheckpointId.new,
        revision_id_factory: Callable[[], CheckpointRevisionId] = CheckpointRevisionId.new,
        request_id_factory: Callable[[], RequestId] = RequestId.new,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._checkpoint_id_factory = checkpoint_id_factory
        self._revision_id_factory = revision_id_factory
        self._request_id_factory = request_id_factory

    def create(self, command: CreateCheckpoint) -> CheckpointView:
        self._validate_write(command.scope, command.content, command.evidence_references)
        created_at = self._now()
        checkpoint_id = command.checkpoint_id or self._checkpoint_id_factory()
        revision = CheckpointRevision(
            revision_id=self._revision_id_factory(),
            checkpoint_id=checkpoint_id,
            revision_number=1,
            predecessor_revision_id=None,
            scope=command.scope,
            content=command.content,
            status=CheckpointStatus.ACTIVE,
            evidence_references=tuple(command.evidence_references),
            created_at=created_at,
        )
        aggregate = CheckpointAggregate(
            checkpoint_id=checkpoint_id,
            scope=command.scope,
            current_revision_id=revision.revision_id,
            current_revision_number=1,
            lifecycle_status=CheckpointStatus.ACTIVE,
            created_at=created_at,
            updated_at=created_at,
        )
        self._call(lambda: self._repository.create_checkpoint_aggregate(aggregate, revision))
        return CheckpointView(aggregate, revision)

    def revise(self, command: ReviseCheckpoint) -> CheckpointView:
        self._validate_write(command.scope, command.content, command.evidence_references)
        revision = self._call(
            lambda: self._repository.append_revision(
                command.scope,
                command.checkpoint_id,
                command.expected_revision_id,
                command.content,
                tuple(command.evidence_references),
                self._now(),
            )
        )
        return CheckpointView(
            self._call(
                lambda: self._repository.get_aggregate(command.scope, command.checkpoint_id)
            ),
            revision,
        )

    def complete(self, command: CompleteCheckpoint) -> CheckpointView:
        self._validate_write(command.scope, command.content, command.evidence_references)
        revision = self._call(
            lambda: self._repository.complete_checkpoint(
                command.scope,
                command.checkpoint_id,
                command.expected_revision_id,
                command.content,
                tuple(command.evidence_references),
                self._now(),
            )
        )
        return CheckpointView(
            self._call(
                lambda: self._repository.get_aggregate(command.scope, command.checkpoint_id)
            ),
            revision,
        )

    def abandon(self, command: AbandonCheckpoint) -> CheckpointView:
        self._validate_write(command.scope, command.content, command.evidence_references)
        if not isinstance(command.reason, str) or not command.reason.strip():
            raise CheckpointApplicationInvalidContent("abandonment reason must not be blank")
        revision = self._call(
            lambda: self._repository.abandon_checkpoint(
                command.scope,
                command.checkpoint_id,
                command.expected_revision_id,
                command.reason,
                command.content,
                tuple(command.evidence_references),
                self._now(),
            )
        )
        return CheckpointView(
            self._call(
                lambda: self._repository.get_aggregate(command.scope, command.checkpoint_id)
            ),
            revision,
        )

    def record_lesson(self, command: RecordCheckpointLesson) -> CheckpointView:
        """Record a single new correction against the active revision atomically.

        The caller supplies only the new lesson and its evidence.  The service takes the current
        canonical handoff as its base, preserves its existing content, and uses the normal
        expected-revision append path rather than adding a second mutable lesson store.
        """
        self._validate_scope(command.scope)
        if not isinstance(command.lesson, CheckpointLesson):
            raise CheckpointApplicationInvalidContent("checkpoint lesson must be canonical")
        if not command.evidence_references:
            raise CheckpointApplicationMissingProvenance(
                "checkpoint lesson requires evidence-bearing provenance"
            )
        if any(
            not isinstance(reference, EvidenceReference)
            for reference in command.evidence_references
        ):
            raise CheckpointApplicationMissingProvenance(
                "checkpoint lesson requires valid evidence-bearing provenance"
            )
        aggregate = self._call(
            lambda: self._repository.get_aggregate(command.scope, command.checkpoint_id)
        )
        current = self._call(
            lambda: self._repository.get_current_revision(command.scope, command.checkpoint_id)
        )
        if current.revision_id != command.expected_revision_id:
            raise CheckpointApplicationRevisionConflict("checkpoint revision is not current")
        if aggregate.lifecycle_status is not CheckpointStatus.ACTIVE:
            raise CheckpointApplicationInvalidLifecycle(
                "checkpoint lifecycle transition is invalid"
            )
        if command.lesson in current.content.lessons:
            return CheckpointView(aggregate, current)
        if len(current.content.lessons) >= _MAX_HISTORICAL_LESSONS:
            raise CheckpointApplicationInvalidContent(
                "checkpoint has the maximum number of lessons"
            )
        evidence = _combined_evidence(current.evidence_references, command.evidence_references)
        if not set(command.lesson.evidence_ids).issubset(
            {reference.evidence_id for reference in evidence}
        ):
            raise CheckpointApplicationMissingProvenance(
                "checkpoint lesson evidence must belong to the saved revision"
            )
        try:
            content = replace(
                current.content,
                lessons=(*current.content.lessons, command.lesson),
            )
        except (TypeError, ValueError) as error:
            raise CheckpointApplicationInvalidContent("checkpoint lesson is invalid") from error
        content = replace(content, token_estimate=_checkpoint_token_estimate(content))
        self._validate_write(command.scope, content, evidence)
        revision = self._call(
            lambda: self._repository.append_revision(
                command.scope,
                command.checkpoint_id,
                command.expected_revision_id,
                content,
                evidence,
                self._now(),
            )
        )
        return CheckpointView(
            self._call(
                lambda: self._repository.get_aggregate(command.scope, command.checkpoint_id)
            ),
            revision,
        )

    def get(self, query: GetCheckpoint) -> CheckpointView:
        self._validate_scope(query.scope)
        if query.revision_id is not None and query.revision_number is not None:
            raise CheckpointApplicationInvalidContent("provide at most one revision selector")
        aggregate = self._call(
            lambda: self._repository.get_aggregate(query.scope, query.checkpoint_id)
        )
        if query.revision_id is None and query.revision_number is None:
            revision = self._call(
                lambda: self._repository.get_current_revision(query.scope, query.checkpoint_id)
            )
        else:
            revision = self._call(
                lambda: self._repository.get_revision(
                    query.scope,
                    query.checkpoint_id,
                    revision_id=query.revision_id,
                    revision_number=query.revision_number,
                )
            )
        return CheckpointView(aggregate, revision)

    def get_context(self, query: GetCheckpointContext) -> ContextPacket:
        self._validate_scope(query.scope)
        if query.budget.active_task_checkpoint < 0:
            raise CheckpointApplicationInvalidContent(
                "active checkpoint budget must be non-negative"
            )
        if query.checkpoint_id is None:
            aggregate = self._call(lambda: self._repository.select_current_checkpoint(query.scope))
        else:
            checkpoint_id = query.checkpoint_id
            aggregate = self._call(
                lambda: self._repository.get_aggregate(query.scope, checkpoint_id)
            )
        if aggregate is None or aggregate.lifecycle_status is not CheckpointStatus.ACTIVE:
            return self._empty_packet(query.scope, query.budget)
        revision = self._call(
            lambda: self._repository.get_current_revision(query.scope, aggregate.checkpoint_id)
        )
        if revision.content.token_estimate > min(
            query.budget.active_task_checkpoint, query.budget.total_limit
        ):
            return self._empty_packet(
                query.scope,
                query.budget,
                OmissionNotice(
                    f"checkpoint:{aggregate.checkpoint_id}:revision:{revision.revision_id}",
                    OmissionReason.TOKEN_BUDGET,
                    "active checkpoint exceeds the configured packet budget",
                ),
            )
        item = self._context_item(revision)
        notices = [
            ProvenanceNotice(
                provenance_id=f"provenance:{item.item_id}",
                item_id=item.item_id,
                source_reference=(
                    f"mnemo:checkpoint/{revision.checkpoint_id}/revision/{revision.revision_id}"
                ),
                source_digest=hashlib.sha256(item.content.encode()).hexdigest(),
                evidence_references=revision.evidence_references,
            )
        ]
        historical_lessons, lesson_notices, lesson_omissions = self._historical_lesson_items(
            query.scope, revision, query.budget, initial_tokens=item.token_estimate
        )
        notices.extend(lesson_notices)
        return ContextPacket(
            PacketSchemaVersion.V1,
            self._request_id_factory(),
            query.scope,
            None,
            query.scope.task_id,
            self._now(),
            None,
            item.token_estimate + sum(lesson.token_estimate for lesson in historical_lessons),
            query.budget,
            _ACTIVE_CHECKPOINT_PRODUCER,
            active_task_checkpoint=item,
            episodic_memories=historical_lessons,
            provenance=tuple(notices),
            omissions=lesson_omissions,
        )

    def _empty_packet(
        self,
        scope: MemoryScope,
        budget: ContextBudget,
        omission: OmissionNotice | None = None,
    ) -> ContextPacket:
        return ContextPacket(
            PacketSchemaVersion.V1,
            self._request_id_factory(),
            scope,
            None,
            scope.task_id,
            self._now(),
            None,
            0,
            budget,
            _ACTIVE_CHECKPOINT_PRODUCER,
            omissions=() if omission is None else (omission,),
        )

    @staticmethod
    def _context_item(revision: CheckpointRevision) -> ContextItem:
        return ContextItem(
            item_id=f"checkpoint:{revision.checkpoint_id}:revision:{revision.revision_id}",
            item_type=ContextItemType.ACTIVE_TASK_CHECKPOINT,
            source_scope=revision.scope,
            content=json.dumps(revision.content.to_dict(), sort_keys=True, separators=(",", ":")),
            content_representation=ContentRepresentation.UNTRUSTED_EVIDENCE,
            token_estimate=revision.content.token_estimate,
            evidence_references=revision.evidence_references,
            source_trust=SourceTrustClass.APPROVED_CHECKPOINT,
            sensitivity=Sensitivity.NORMAL,
            validity=ValidityState.CURRENT,
            ranking=None,
            conflict_state=ConflictState.NONE,
            observed_at=revision.created_at,
        )

    def _historical_lesson_items(
        self,
        scope: MemoryScope,
        current: CheckpointRevision,
        budget: ContextBudget,
        *,
        initial_tokens: int,
    ) -> tuple[tuple[ContextItem, ...], tuple[ProvenanceNotice, ...], tuple[OmissionNotice, ...]]:
        """Surface prior correction lessons even when a later handoff omits them.

        Checkpoint revisions are immutable snapshots.  A later revision may focus on new work and
        omit a previously recorded lesson; this bounded walk retains the evidence-backed caution
        without mutating either revision or pretending that a historical lesson is current
        repository structure.
        """
        remaining = min(
            budget.episodic_memories,
            max(0, budget.total_limit - initial_tokens),
        )
        seen_lessons = set(current.content.lessons)
        items: list[ContextItem] = []
        notices: list[ProvenanceNotice] = []
        predecessor = current.predecessor_revision_id
        revisions_seen = 0
        omitted = False

        while (
            predecessor is not None
            and revisions_seen < _MAX_HISTORICAL_LESSON_REVISIONS
            and len(seen_lessons) < _MAX_HISTORICAL_LESSONS
        ):
            revision = self.get(
                GetCheckpoint(scope, current.checkpoint_id, revision_id=predecessor)
            ).revision
            revisions_seen += 1
            predecessor = revision.predecessor_revision_id
            evidence_by_id = {item.evidence_id: item for item in revision.evidence_references}
            for lesson in revision.content.lessons:
                if lesson in seen_lessons:
                    continue
                evidence = tuple(evidence_by_id[item] for item in lesson.evidence_ids)
                content = json.dumps(
                    {
                        "checkpoint_id": str(revision.checkpoint_id),
                        "lesson": lesson.to_dict(),
                        "revision_id": str(revision.revision_id),
                        "revision_number": revision.revision_number,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                tokens = (len(content) + 3) // 4
                if tokens > remaining:
                    omitted = True
                    continue
                item = ContextItem(
                    item_id=(
                        f"checkpoint-lesson:{revision.checkpoint_id}:"
                        f"revision:{revision.revision_id}:index:{len(items)}"
                    ),
                    item_type=ContextItemType.EPISODIC_MEMORY,
                    source_scope=scope,
                    content=content,
                    content_representation=ContentRepresentation.UNTRUSTED_EVIDENCE,
                    token_estimate=tokens,
                    evidence_references=evidence,
                    source_trust=SourceTrustClass.APPROVED_CHECKPOINT,
                    sensitivity=Sensitivity.NORMAL,
                    validity=ValidityState.UNKNOWN,
                    ranking=None,
                    conflict_state=ConflictState.NONE,
                    observed_at=revision.created_at,
                )
                items.append(item)
                notices.append(
                    ProvenanceNotice(
                        provenance_id=f"provenance:{item.item_id}",
                        item_id=item.item_id,
                        source_reference=(
                            f"mnemo:checkpoint/{revision.checkpoint_id}/"
                            f"revision/{revision.revision_id}"
                        ),
                        source_digest=hashlib.sha256(content.encode()).hexdigest(),
                        evidence_references=evidence,
                    )
                )
                seen_lessons.add(lesson)
                remaining -= tokens

        omissions = (
            ()
            if not omitted
            else (
                OmissionNotice(
                    "checkpoint-lesson-history",
                    OmissionReason.TOKEN_BUDGET,
                    "historical correction lessons exceed the remaining context budget",
                ),
            )
        )
        return tuple(items), tuple(notices), omissions

    def _validate_write(
        self,
        scope: MemoryScope,
        content: CheckpointContent,
        evidence_references: tuple[EvidenceReference, ...],
    ) -> None:
        self._validate_scope(scope)
        if not isinstance(content, CheckpointContent):
            raise CheckpointApplicationInvalidContent("checkpoint content must be canonical")
        if content.token_estimate > DEFAULT_CONTEXT_BUDGET.active_task_checkpoint:
            raise CheckpointApplicationBudgetExceeded(
                "checkpoint content exceeds the active checkpoint budget"
            )
        if not evidence_references or any(
            not isinstance(item, EvidenceReference) for item in evidence_references
        ):
            raise CheckpointApplicationMissingProvenance(
                "checkpoint revisions require evidence-bearing provenance"
            )

    @staticmethod
    def _validate_scope(scope: MemoryScope) -> None:
        if not isinstance(scope, MemoryScope) or scope.level is not ScopeLevel.TASK:
            raise CheckpointApplicationInvalidScope(
                "checkpoint operations require explicit task scope"
            )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise CheckpointApplicationInvalidContent(
                "clock must return a timezone-aware timestamp"
            )
        return value

    @staticmethod
    def _translate(error: Exception) -> CheckpointApplicationError:
        if isinstance(error, CheckpointNotFound):
            return CheckpointApplicationNotFound("checkpoint was not found in the requested scope")
        if isinstance(error, DuplicateCheckpoint):
            return CheckpointApplicationDuplicate("checkpoint already exists")
        if isinstance(error, RevisionConflict):
            return CheckpointApplicationRevisionConflict("checkpoint revision is not current")
        if isinstance(error, InvalidLifecycleTransition):
            return CheckpointApplicationInvalidLifecycle(
                "checkpoint lifecycle transition is invalid"
            )
        if isinstance(error, InvalidCheckpointScope):
            return CheckpointApplicationInvalidScope("checkpoint scope is invalid")
        if isinstance(error, InvalidAbandonmentReason):
            return CheckpointApplicationInvalidContent("abandonment reason is invalid")
        if isinstance(error, RepositoryStorageFailure):
            return CheckpointApplicationStorageFailure("checkpoint storage is unavailable")
        return CheckpointApplicationStorageFailure("checkpoint storage operation failed")

    def _call(self, operation: Callable[[], _Result]) -> _Result:
        try:
            return operation()
        except CheckpointApplicationError:
            raise
        except Exception as error:
            translated = self._translate(error)
            raise translated from error


def _checkpoint_token_estimate(content: CheckpointContent) -> int:
    """Use Mnemo's deterministic cold-input heuristic for a lesson-only revision."""
    payload = content.to_dict()
    payload["token_estimate"] = 0
    return (len(json.dumps(payload, sort_keys=True, separators=(",", ":"))) + 2) // 3


def _combined_evidence(
    existing: tuple[EvidenceReference, ...],
    added: tuple[EvidenceReference, ...],
) -> tuple[EvidenceReference, ...]:
    """Keep immutable evidence IDs unique without accepting conflicting payloads."""
    values: dict[EvidenceId, EvidenceReference] = {}
    for reference in (*existing, *added):
        previous = values.get(reference.evidence_id)
        if previous is not None and previous != reference:
            raise CheckpointApplicationInvalidContent("checkpoint evidence identifiers conflict")
        values[reference.evidence_id] = reference
    return tuple(values.values())
