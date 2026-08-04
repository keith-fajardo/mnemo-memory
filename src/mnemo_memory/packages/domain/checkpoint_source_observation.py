"""Immutable evidence that a source snapshot was observed after a checkpoint save.

This relationship deliberately records co-observation, not an inferred explanation.  The
checkpoint's own evidence-bearing content remains the authoritative record of *why* work changed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .code_structure import CodeSnapshotId
from .identifiers import CheckpointId, CheckpointRevisionId
from .models import MemoryScope, ScopeLevel, _require_aware


@dataclass(frozen=True, slots=True)
class CheckpointSourceObservation:
    """One source snapshot parsed immediately after an exact checkpoint revision persisted."""

    scope: MemoryScope
    checkpoint_id: CheckpointId
    revision_id: CheckpointRevisionId
    source_snapshot_id: CodeSnapshotId
    observed_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.scope, MemoryScope) or self.scope.level is not ScopeLevel.TASK:
            raise ValueError("checkpoint source observations require task scope")
        if not isinstance(self.checkpoint_id, CheckpointId):
            raise TypeError("checkpoint source observation requires checkpoint identity")
        if not isinstance(self.revision_id, CheckpointRevisionId):
            raise TypeError("checkpoint source observation requires revision identity")
        if not isinstance(self.source_snapshot_id, CodeSnapshotId):
            raise TypeError("checkpoint source observation requires source snapshot identity")
        _require_aware(self.observed_at, "observed_at")
