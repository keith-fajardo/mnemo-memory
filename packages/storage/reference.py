"""Reference adapter for the aggregate/revision checkpoint repository contract."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from packages.domain import (
    CheckpointAggregate,
    CheckpointContent,
    CheckpointId,
    CheckpointRevision,
    CheckpointRevisionId,
    CheckpointStatus,
    EvidenceReference,
    MemoryScope,
    ScopeLevel,
)

from .contracts import (
    CheckpointNotFound,
    CheckpointPage,
    DuplicateCheckpoint,
    InvalidAbandonmentReason,
    InvalidCheckpointScope,
    InvalidLifecycleTransition,
    RevisionConflict,
)


class ReferenceCheckpointRepository:
    """Storage-independent behavior reference with validate-before-mutate writes."""

    def __init__(self) -> None:
        self._aggregates: dict[CheckpointId, CheckpointAggregate] = {}
        self._revisions: dict[CheckpointId, tuple[CheckpointRevision, ...]] = {}

    def create_aggregate(
        self, aggregate: CheckpointAggregate, revision: CheckpointRevision
    ) -> None:
        """Compatibility alias for callers migrating to create_checkpoint_aggregate in 10A.3c."""
        self.create_checkpoint_aggregate(aggregate, revision)

    def create_checkpoint_aggregate(
        self, aggregate: CheckpointAggregate, initial_revision: CheckpointRevision
    ) -> None:
        self._require_scope(aggregate.scope)
        if aggregate.checkpoint_id in self._aggregates:
            raise DuplicateCheckpoint()
        if (
            initial_revision.checkpoint_id != aggregate.checkpoint_id
            or initial_revision.scope != aggregate.scope
            or initial_revision.revision_number != 1
            or initial_revision.predecessor_revision_id is not None
            or aggregate.current_revision_id != initial_revision.revision_id
            or aggregate.current_revision_number != 1
            or aggregate.lifecycle_status is not CheckpointStatus.ACTIVE
            or initial_revision.status is not CheckpointStatus.ACTIVE
        ):
            raise InvalidLifecycleTransition(
                "initial aggregate and revision must be active revision one"
            )
        # Both assignments happen only after all validation succeeds.
        try:
            self._aggregates[aggregate.checkpoint_id] = aggregate
            self._revisions[aggregate.checkpoint_id] = (initial_revision,)
        except BaseException:
            self._aggregates.pop(aggregate.checkpoint_id, None)
            self._revisions.pop(aggregate.checkpoint_id, None)
            raise

    def get_aggregate(self, scope: MemoryScope, checkpoint_id: CheckpointId) -> CheckpointAggregate:
        self._require_scope(scope)
        aggregate = self._aggregates.get(checkpoint_id)
        if aggregate is None or aggregate.scope != scope:
            raise CheckpointNotFound()
        return aggregate

    def get_current_revision(
        self, scope: MemoryScope, checkpoint_id: CheckpointId
    ) -> CheckpointRevision:
        aggregate = self.get_aggregate(scope, checkpoint_id)
        return self._revisions[checkpoint_id][aggregate.current_revision_number - 1]

    def get_revision(
        self,
        scope: MemoryScope,
        checkpoint_id: CheckpointId,
        *,
        revision_number: int | None = None,
        revision_id: CheckpointRevisionId | None = None,
    ) -> CheckpointRevision:
        self.get_aggregate(scope, checkpoint_id)
        if (revision_number is None) == (revision_id is None):
            raise ValueError("provide exactly one revision selector")
        revisions = self._revisions[checkpoint_id]
        if revision_number is not None:
            if revision_number < 1 or revision_number > len(revisions):
                raise CheckpointNotFound()
            return revisions[revision_number - 1]
        assert revision_id is not None
        for revision in revisions:
            if revision.revision_id == revision_id:
                return revision
        raise CheckpointNotFound()

    def append_revision(
        self,
        scope: MemoryScope,
        checkpoint_id: CheckpointId,
        expected_revision_id: CheckpointRevisionId,
        content: CheckpointContent,
        evidence_references: tuple[EvidenceReference, ...],
        created_at: datetime,
    ) -> CheckpointRevision:
        aggregate = self.get_aggregate(scope, checkpoint_id)
        self._require_active_expected(aggregate, expected_revision_id)
        current = self.get_current_revision(scope, checkpoint_id)
        revision = CheckpointRevision(
            revision_id=CheckpointRevisionId.new(),
            checkpoint_id=checkpoint_id,
            revision_number=current.revision_number + 1,
            predecessor_revision_id=current.revision_id,
            scope=scope,
            content=content,
            status=CheckpointStatus.ACTIVE,
            evidence_references=evidence_references,
            created_at=created_at,
        )
        self._replace_current(aggregate, revision)
        return revision

    def complete_checkpoint(
        self,
        scope: MemoryScope,
        checkpoint_id: CheckpointId,
        expected_revision_id: CheckpointRevisionId,
        content: CheckpointContent,
        evidence_references: tuple[EvidenceReference, ...],
        created_at: datetime,
    ) -> CheckpointRevision:
        return self._transition(
            scope,
            checkpoint_id,
            expected_revision_id,
            CheckpointStatus.COMPLETED,
            content,
            evidence_references,
            created_at,
            reason=None,
        )

    def abandon_checkpoint(
        self,
        scope: MemoryScope,
        checkpoint_id: CheckpointId,
        expected_revision_id: CheckpointRevisionId,
        reason: str,
        content: CheckpointContent,
        evidence_references: tuple[EvidenceReference, ...],
        created_at: datetime,
    ) -> CheckpointRevision:
        if not isinstance(reason, str) or not reason.strip():
            raise InvalidAbandonmentReason("abandonment reason must not be blank")
        terminal_content = content
        if reason not in terminal_content.failures:
            terminal_content = replace(
                terminal_content, failures=(*terminal_content.failures, reason)
            )
        return self._transition(
            scope,
            checkpoint_id,
            expected_revision_id,
            CheckpointStatus.ABANDONED,
            terminal_content,
            evidence_references,
            created_at,
            reason=reason,
        )

    def list_current_checkpoints(
        self, scope: MemoryScope, *, offset: int = 0, limit: int = 50
    ) -> CheckpointPage:
        self._require_scope(scope)
        if offset < 0 or limit < 1:
            raise ValueError("offset must be non-negative and limit must be positive")
        active = [
            aggregate
            for aggregate in self._aggregates.values()
            if aggregate.scope == scope and aggregate.lifecycle_status is CheckpointStatus.ACTIVE
        ]
        active.sort(key=lambda item: (-item.updated_at.timestamp(), str(item.checkpoint_id)))
        items = tuple(active[offset : offset + limit])
        next_offset = offset + limit if offset + limit < len(active) else None
        return CheckpointPage(items=items, next_offset=next_offset)

    def select_current_checkpoint(self, scope: MemoryScope) -> CheckpointAggregate | None:
        items = self.list_current_checkpoints(scope, limit=1).items
        return items[0] if items else None

    def _transition(
        self,
        scope: MemoryScope,
        checkpoint_id: CheckpointId,
        expected_revision_id: CheckpointRevisionId,
        status: CheckpointStatus,
        content: CheckpointContent,
        evidence_references: tuple[EvidenceReference, ...],
        created_at: datetime,
        *,
        reason: str | None,
    ) -> CheckpointRevision:
        aggregate = self.get_aggregate(scope, checkpoint_id)
        current = self.get_current_revision(scope, checkpoint_id)
        if aggregate.lifecycle_status is not CheckpointStatus.ACTIVE:
            if self._is_identical_terminal_retry(
                current, expected_revision_id, status, content, evidence_references, reason
            ):
                return current
            raise InvalidLifecycleTransition("checkpoint is already terminal")
        self._require_active_expected(aggregate, expected_revision_id)
        if status is CheckpointStatus.COMPLETED and (content.blockers or content.remaining_work):
            raise InvalidLifecycleTransition(
                "completed checkpoint cannot contain blockers or remaining work"
            )
        revision = CheckpointRevision(
            revision_id=CheckpointRevisionId.new(),
            checkpoint_id=checkpoint_id,
            revision_number=current.revision_number + 1,
            predecessor_revision_id=current.revision_id,
            scope=scope,
            content=content,
            status=status,
            evidence_references=evidence_references,
            created_at=created_at,
        )
        self._replace_current(aggregate, revision)
        return revision

    def _require_active_expected(
        self, aggregate: CheckpointAggregate, expected_revision_id: CheckpointRevisionId
    ) -> None:
        if aggregate.lifecycle_status is not CheckpointStatus.ACTIVE:
            raise InvalidLifecycleTransition("checkpoint is not active")
        if aggregate.current_revision_id != expected_revision_id:
            raise RevisionConflict("expected revision is not current")

    def _replace_current(
        self, aggregate: CheckpointAggregate, revision: CheckpointRevision
    ) -> None:
        next_aggregate = replace(
            aggregate,
            current_revision_id=revision.revision_id,
            current_revision_number=revision.revision_number,
            lifecycle_status=revision.status,
            updated_at=revision.created_at,
        )
        next_revisions = self._revisions[aggregate.checkpoint_id] + (revision,)
        # Build all immutable replacements before changing either map.
        previous_revisions = self._revisions[aggregate.checkpoint_id]
        previous_aggregate = self._aggregates[aggregate.checkpoint_id]
        try:
            self._revisions[aggregate.checkpoint_id] = next_revisions
            self._aggregates[aggregate.checkpoint_id] = next_aggregate
        except BaseException:
            self._revisions[aggregate.checkpoint_id] = previous_revisions
            self._aggregates[aggregate.checkpoint_id] = previous_aggregate
            raise

    def _is_identical_terminal_retry(
        self,
        current: CheckpointRevision,
        expected_revision_id: CheckpointRevisionId,
        status: CheckpointStatus,
        content: CheckpointContent,
        evidence_references: tuple[EvidenceReference, ...],
        reason: str | None,
    ) -> bool:
        return (
            current.status is status
            and current.predecessor_revision_id == expected_revision_id
            and current.content == content
            and current.evidence_references == tuple(evidence_references)
            and (reason is None or reason in current.content.failures)
        )

    @staticmethod
    def _require_scope(scope: MemoryScope) -> None:
        if not isinstance(scope, MemoryScope) or scope.level is not ScopeLevel.TASK:
            raise InvalidCheckpointScope("checkpoint operations require explicit task scope")
