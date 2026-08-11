"""In-memory reference repository for semantic checkpoint behavior."""

from __future__ import annotations

from threading import Lock

from mnemo_memory.packages.domain import (
    CheckpointId,
    EventId,
    MaterializedSemanticCheckpoint,
    MemoryScope,
    ScopeLevel,
    SemanticCheckpoint,
    SemanticCheckpointAtom,
    SemanticCheckpointPatch,
    SemanticMemoryAtom,
    apply_semantic_checkpoint_patch,
)

from .contracts import (
    SemanticCheckpointConflict,
    SemanticCheckpointNotFound,
    TaskActivityEventRepository,
)


class ReferenceSemanticCheckpointRepository:
    """Atomic reference implementation with exact-scope parent checks."""

    def __init__(self, events: TaskActivityEventRepository) -> None:
        self._events = events
        self._checkpoints: dict[CheckpointId, MaterializedSemanticCheckpoint] = {}
        self._current: dict[MemoryScope, CheckpointId] = {}
        self._ledger: dict[MemoryScope, dict[object, SemanticMemoryAtom]] = {}
        self._compiled_events: dict[MemoryScope, set[EventId]] = {}
        self._lock = Lock()

    def get_current_semantic_checkpoint(self, scope: MemoryScope) -> SemanticCheckpoint | None:
        self._require_scope(scope)
        checkpoint_id = self._current.get(scope)
        return None if checkpoint_id is None else self._checkpoints[checkpoint_id].checkpoint

    def get_semantic_checkpoint(
        self, scope: MemoryScope, checkpoint_id: CheckpointId
    ) -> SemanticCheckpoint:
        self._require_scope(scope)
        item = self._checkpoints.get(checkpoint_id)
        if item is None or item.checkpoint.scope != scope:
            raise SemanticCheckpointNotFound("semantic checkpoint was not found")
        return item.checkpoint

    def list_semantic_atoms(self, scope: MemoryScope) -> tuple[SemanticMemoryAtom, ...]:
        self._require_scope(scope)
        return tuple(
            sorted(self._ledger.get(scope, {}).values(), key=lambda item: str(item.atom_id))
        )

    def list_compiled_semantic_event_ids(self, scope: MemoryScope) -> frozenset[EventId]:
        self._require_scope(scope)
        return frozenset(self._compiled_events.get(scope, set()))

    def materialize_semantic_checkpoint(
        self, scope: MemoryScope, checkpoint_id: CheckpointId
    ) -> MaterializedSemanticCheckpoint:
        self._require_scope(scope)
        item = self._checkpoints.get(checkpoint_id)
        if item is None or item.checkpoint.scope != scope:
            raise SemanticCheckpointNotFound("semantic checkpoint was not found")
        return item

    def store_semantic_checkpoint(
        self,
        patch: SemanticCheckpointPatch,
        materialized: MaterializedSemanticCheckpoint,
    ) -> bool:
        scope = materialized.checkpoint.scope
        self._require_scope(scope)
        with self._lock:
            existing = self._checkpoints.get(materialized.checkpoint.checkpoint_id)
            if existing is not None:
                if existing == materialized:
                    return True
                raise SemanticCheckpointConflict("semantic checkpoint identity conflicts")
            current_id = self._current.get(scope)
            if patch.base_checkpoint_id != current_id:
                raise SemanticCheckpointConflict("semantic checkpoint parent changed")
            if materialized.checkpoint.parent_checkpoint_id != current_id:
                raise SemanticCheckpointConflict("semantic checkpoint parent is invalid")
            expected_generation = 1
            active_refs: tuple[SemanticCheckpointAtom, ...] = ()
            if current_id is not None:
                current = self._checkpoints[current_id]
                expected_generation = current.checkpoint.generation + 1
                active_refs = current.references
            if materialized.checkpoint.generation != expected_generation:
                raise SemanticCheckpointConflict("semantic checkpoint generation is invalid")
            event_ids = frozenset(
                event_id
                for operation in patch.operations
                if operation.atom is not None
                for event_id in operation.atom.source_event_ids
            )
            event_ids |= frozenset(patch.processed_event_ids)
            self._verify_events(scope, event_ids)
            if set(patch.processed_event_ids) & self._compiled_events.get(scope, set()):
                raise SemanticCheckpointConflict("semantic event was already compiled")
            ledger, references = apply_semantic_checkpoint_patch(
                scope=scope,
                ledger_atoms=tuple(self._ledger.get(scope, {}).values()),
                active_references=active_refs,
                patch=patch,
                available_event_ids=event_ids,
                applied_at=materialized.checkpoint.created_at,
            )
            active_by_id = {item.atom_id: item for item in ledger}
            expected_atoms = tuple(active_by_id[item.atom_id] for item in references)
            if references != materialized.references or expected_atoms != materialized.atoms:
                raise SemanticCheckpointConflict("materialized semantic state does not match patch")
            self._ledger[scope] = {item.atom_id: item for item in ledger}
            self._checkpoints[materialized.checkpoint.checkpoint_id] = materialized
            self._current[scope] = materialized.checkpoint.checkpoint_id
            self._compiled_events.setdefault(scope, set()).update(patch.processed_event_ids)
            return False

    def _verify_events(self, scope: MemoryScope, event_ids: frozenset[EventId]) -> None:
        for event_id in event_ids:
            self._events.get_task_activity_event(scope, event_id)

    @staticmethod
    def _require_scope(scope: MemoryScope) -> None:
        if not isinstance(scope, MemoryScope) or scope.level is not ScopeLevel.TASK:
            raise ValueError("semantic checkpoints require exact task scope")
