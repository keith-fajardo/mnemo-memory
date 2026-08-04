"""Fail-open source observation immediately after a durable checkpoint revision.

The adapter knows local project paths and parsers.  It deliberately records an observed snapshot
reference, never an inferred reason for a source change.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from mnemo_memory.packages.application.automatic_memory import LocalMemoryProjectBindingStore
from mnemo_memory.packages.application.checkpoints import CheckpointView
from mnemo_memory.packages.domain import CheckpointSourceObservation
from mnemo_memory.packages.project_index import SourceStructureParser, SourceStructureParseRequest
from mnemo_memory.packages.storage.contracts import (
    CheckpointSourceObservationRepository,
    SourceStructureRepository,
)


@dataclass(frozen=True, slots=True)
class CheckpointSourceObserver:
    """Best-effort local observer; checkpoint success never depends on source indexing."""

    bindings: LocalMemoryProjectBindingStore
    source_repository: SourceStructureRepository
    association_repository: CheckpointSourceObservationRepository
    clock: Callable[[], datetime]

    def observe(self, view: CheckpointView) -> bool:
        try:
            binding = self.bindings.get_for_scope(view.aggregate.scope)
            if binding is None:
                return False
            stored = self.source_repository.store_and_activate(
                SourceStructureParser().parse(
                    SourceStructureParseRequest(binding.scope, binding.project_root)
                )
            )
            self.association_repository.append_checkpoint_source_observation(
                CheckpointSourceObservation(
                    scope=view.aggregate.scope,
                    checkpoint_id=view.aggregate.checkpoint_id,
                    revision_id=view.revision.revision_id,
                    source_snapshot_id=stored.snapshot.snapshot_id,
                    observed_at=self.clock(),
                )
            )
            return True
        except Exception:
            # This is an optional, local projection refresh after the checkpoint transaction has
            # completed. It must never disclose parser/storage details or turn a successful save
            # into a client-visible failure.
            return False
