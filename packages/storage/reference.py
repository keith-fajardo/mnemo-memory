"""Reference storage adapter for aggregate/revision repository contracts."""

from __future__ import annotations

from packages.domain import CheckpointAggregate, CheckpointId, CheckpointRevision, MemoryScope

from .contracts import CheckpointNotFound


class ReferenceCheckpointRepository:
    """In-memory reference; compound writes are copy-on-success in later 10A.3b parity work."""

    def __init__(self) -> None:
        self.aggregates: dict[CheckpointId, CheckpointAggregate] = {}
        self.revisions: dict[CheckpointId, tuple[CheckpointRevision, ...]] = {}

    def create_aggregate(
        self, aggregate: CheckpointAggregate, revision: CheckpointRevision
    ) -> None:
        if aggregate.checkpoint_id in self.aggregates:
            from .contracts import DuplicateCheckpoint

            raise DuplicateCheckpoint()
        self.aggregates[aggregate.checkpoint_id] = aggregate
        self.revisions[aggregate.checkpoint_id] = (revision,)

    def get_aggregate(self, scope: MemoryScope, checkpoint_id: CheckpointId) -> CheckpointAggregate:
        aggregate = self.aggregates.get(checkpoint_id)
        if aggregate is None or aggregate.scope != scope:
            raise CheckpointNotFound()
        return aggregate

    def get_current_revision(
        self, scope: MemoryScope, checkpoint_id: CheckpointId
    ) -> CheckpointRevision:
        aggregate = self.get_aggregate(scope, checkpoint_id)
        return self.revisions[checkpoint_id][aggregate.current_revision_number - 1]
