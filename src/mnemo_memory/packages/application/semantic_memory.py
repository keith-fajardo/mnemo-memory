"""Incremental compilation and recall for semantic checkpoints."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime

from mnemo_memory.packages.domain import (
    CheckpointId,
    EventId,
    MaterializedSemanticCheckpoint,
    MemoryCompiler,
    MemoryScope,
    SemanticAtomKind,
    SemanticCheckpoint,
    SemanticCheckpointAtom,
    SemanticCheckpointPatch,
    SemanticCheckpointType,
    SemanticMemoryAtom,
    SemanticPatchOperation,
    SemanticPatchOperationKind,
    SemanticRendererProfile,
    TaskActivityActor,
    TaskActivityEvent,
    TaskActivityEventKind,
    apply_semantic_checkpoint_patch,
)
from mnemo_memory.packages.storage.contracts import (
    SemanticCheckpointNotFound,
    SemanticCheckpointRepository,
    TaskActivityEventRepository,
)

from .semantic_rendering import (
    DEFAULT_MAXIMUM_TOKENS,
    DEFAULT_PREFERRED_TOKENS,
    CheckpointTokenCounter,
    ConservativeTokenCounter,
    RenderedSemanticCheckpoint,
    measure_checkpoint_tokens,
    render_semantic_checkpoint,
)

_EXPLICIT_KIND = re.compile(
    r"^\s*(goal|fact|state|decision|constraint|preference|open_question|next_action|result|failure|inference)\s*:\s*(.+)$",
    re.IGNORECASE | re.DOTALL,
)
_UNCERTAINTY = re.compile(r"\b(?:uncertain|possibly|probably|likely|might|may)\b", re.I)
_CONDITION = re.compile(r"\b(?:if|unless|only after|provided that|when)\b", re.I)
_AUTHORITY = re.compile(r"\b(?:approve|approval|authority|authorized|permission|consent)\b", re.I)
_PRIORITY = {
    SemanticAtomKind.GOAL: 100,
    SemanticAtomKind.CONSTRAINT: 100,
    SemanticAtomKind.DECISION: 95,
    SemanticAtomKind.OPEN_QUESTION: 90,
    SemanticAtomKind.NEXT_ACTION: 90,
    SemanticAtomKind.STATE: 80,
    SemanticAtomKind.FACT: 75,
    SemanticAtomKind.FAILURE: 70,
    SemanticAtomKind.RESULT: 65,
    SemanticAtomKind.PREFERENCE: 60,
    SemanticAtomKind.INFERENCE: 50,
}


class SemanticMemoryApplicationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SemanticCheckpointSaveResult:
    checkpoint: MaterializedSemanticCheckpoint
    rendering: RenderedSemanticCheckpoint
    processed_event_count: int
    idempotent: bool


class DeterministicMemoryCompiler:
    """Conservative baseline: explicit labels are typed; unlabeled claims retain attribution."""

    compiler_version = "deterministic-semantic-v1"

    def compile(
        self,
        scope: MemoryScope,
        events: tuple[TaskActivityEvent, ...],
        active_atoms: tuple[SemanticMemoryAtom, ...],
        *,
        base_checkpoint_id: CheckpointId | None,
    ) -> SemanticCheckpointPatch:
        if not events:
            raise ValueError("semantic compiler requires at least one event")
        if any(event.scope != scope for event in events) or any(
            atom.scope != scope for atom in active_atoms
        ):
            raise ValueError("semantic compiler input crosses scope")
        active = {atom.atom_id: atom for atom in active_atoms}
        operations: list[SemanticPatchOperation] = []
        for event in events:
            kind, meaning, explicit = self._classify(event)
            predicate = self._predicate(kind, explicit)
            qualifiers = self._qualifiers(event, meaning, explicit)
            comparable = tuple(
                atom
                for atom in active.values()
                if atom.kind is kind
                and atom.subject == event.actor.value
                and atom.predicate == predicate
            )
            duplicate = next((atom for atom in comparable if atom.object_value == meaning), None)
            if duplicate is not None:
                operations.append(
                    SemanticPatchOperation(
                        SemanticPatchOperationKind.ACTIVATE,
                        target_atom_id=duplicate.atom_id,
                        inclusion_reason="deduplicated_active_meaning",
                        checkpoint_priority=duplicate.priority,
                    )
                )
                continue
            superseded = (
                max(comparable, key=lambda atom: (atom.updated_at, str(atom.atom_id)))
                if comparable and kind in {SemanticAtomKind.GOAL, SemanticAtomKind.DECISION}
                else None
            )
            confidence = self._confidence(event, explicit, meaning)
            atom = SemanticMemoryAtom.create(
                scope=scope,
                kind=kind,
                subject=event.actor.value,
                predicate=predicate,
                object_value=meaning,
                qualifiers=qualifiers,
                confidence=confidence,
                priority=_PRIORITY[kind],
                source_event_ids=(event.event_id,),
                supersedes_atom_id=None if superseded is None else superseded.atom_id,
                valid_from=event.occurred_at,
                created_at=event.occurred_at,
            )
            if superseded is None:
                operations.extend(
                    (
                        SemanticPatchOperation(SemanticPatchOperationKind.ADD, atom=atom),
                        SemanticPatchOperation(
                            SemanticPatchOperationKind.ACTIVATE,
                            target_atom_id=atom.atom_id,
                            inclusion_reason="compiled_from_immutable_event",
                            checkpoint_priority=atom.priority,
                        ),
                    )
                )
            else:
                operations.append(
                    SemanticPatchOperation(
                        SemanticPatchOperationKind.SUPERSEDE,
                        atom=atom,
                        target_atom_id=superseded.atom_id,
                        inclusion_reason="newer_explicit_goal_or_decision",
                        checkpoint_priority=atom.priority,
                    )
                )
                active.pop(superseded.atom_id)
            active[atom.atom_id] = atom
        return SemanticCheckpointPatch(
            base_checkpoint_id,
            tuple(operations),
            tuple(event.event_id for event in events),
        )

    @staticmethod
    def _classify(
        event: TaskActivityEvent,
    ) -> tuple[SemanticAtomKind, str, bool]:
        explicit = _EXPLICIT_KIND.match(event.summary)
        if explicit is not None:
            return SemanticAtomKind(explicit.group(1).lower()), explicit.group(2), True
        if event.kind is TaskActivityEventKind.TASK_OUTCOME:
            return SemanticAtomKind.RESULT, event.summary, False
        if event.kind is TaskActivityEventKind.TOOL_INVOCATION:
            return SemanticAtomKind.RESULT, event.summary, False
        return SemanticAtomKind.INFERENCE, event.summary, False

    @staticmethod
    def _predicate(kind: SemanticAtomKind, explicit: bool) -> str:
        if explicit:
            return {
                SemanticAtomKind.GOAL: "objective",
                SemanticAtomKind.DECISION: "decided",
                SemanticAtomKind.CONSTRAINT: "requires",
                SemanticAtomKind.NEXT_ACTION: "will_do",
                SemanticAtomKind.OPEN_QUESTION: "unresolved",
            }.get(kind, "states")
        return "observed" if kind is SemanticAtomKind.RESULT else "reported"

    @staticmethod
    def _qualifiers(
        event: TaskActivityEvent, meaning: str, explicit: bool
    ) -> tuple[tuple[str, str], ...]:
        epistemic = {
            TaskActivityActor.USER: "user_claim",
            TaskActivityActor.TOOL: "tool_observation",
            TaskActivityActor.ASSISTANT: "assistant_inference",
            TaskActivityActor.AGENT: "agent_inference",
        }[event.actor]
        values = {
            "attribution": event.actor.value,
            "epistemic": epistemic,
            "event_kind": event.kind.value,
            "explicit_type": str(explicit).lower(),
        }
        if _UNCERTAINTY.search(meaning):
            values["critical_uncertainty"] = "true"
        if _CONDITION.search(meaning):
            values["condition"] = "present_in_meaning"
        if _AUTHORITY.search(meaning):
            values["authority_boundary"] = "true"
        return tuple(values.items())

    @staticmethod
    def _confidence(event: TaskActivityEvent, explicit: bool, meaning: str) -> float:
        if _UNCERTAINTY.search(meaning):
            return 0.5
        if event.actor is TaskActivityActor.TOOL:
            return 1.0
        return 0.9 if explicit and event.actor is TaskActivityActor.USER else 0.6


class SemanticMemoryService:
    """Append, compile, checkpoint, inspect, and recall exact-scope semantic memory."""

    def __init__(
        self,
        events: TaskActivityEventRepository,
        checkpoints: SemanticCheckpointRepository,
        *,
        clock: Callable[[], datetime],
        compiler: MemoryCompiler | None = None,
        tokenizer: CheckpointTokenCounter | None = None,
        snapshot_interval: int = 8,
    ) -> None:
        if (
            isinstance(snapshot_interval, bool)
            or not isinstance(snapshot_interval, int)
            or snapshot_interval < 2
        ):
            raise ValueError("semantic snapshot interval must be at least two")
        self._events = events
        self._checkpoints = checkpoints
        self._clock = clock
        self._compiler = compiler or DeterministicMemoryCompiler()
        self._tokenizer = tokenizer or ConservativeTokenCounter()
        self._snapshot_interval = snapshot_interval

    def append_event(self, event: TaskActivityEvent) -> bool:
        return self._events.append_task_activity_event(event).idempotent

    def inspect_evidence(
        self, scope: MemoryScope, event_ids: tuple[EventId, ...]
    ) -> tuple[TaskActivityEvent, ...]:
        return tuple(self._events.get_task_activity_event(scope, item) for item in event_ids)

    def get_checkpoint(
        self, scope: MemoryScope, checkpoint_id: CheckpointId
    ) -> MaterializedSemanticCheckpoint:
        return self._checkpoints.materialize_semantic_checkpoint(scope, checkpoint_id)

    def list_atoms(self, scope: MemoryScope) -> tuple[SemanticMemoryAtom, ...]:
        return self._checkpoints.list_semantic_atoms(scope)

    def materialize_snapshot(
        self, scope: MemoryScope, checkpoint_id: CheckpointId
    ) -> MaterializedSemanticCheckpoint:
        return self.get_checkpoint(scope, checkpoint_id)

    def save_checkpoint(
        self,
        scope: MemoryScope,
        *,
        events: tuple[TaskActivityEvent, ...] = (),
    ) -> SemanticCheckpointSaveResult:
        for event in events:
            self.append_event(event)
        current = self._checkpoints.get_current_semantic_checkpoint(scope)
        all_events = self._all_events(scope)
        compiled_event_ids = self._checkpoints.list_compiled_semantic_event_ids(scope)
        pending = self._pending_events(all_events, compiled_event_ids)
        if not pending:
            if current is None:
                raise SemanticMemoryApplicationError("MNEMO_SEMANTIC_EVIDENCE_REQUIRED")
            materialized = self._checkpoints.materialize_semantic_checkpoint(
                scope, current.checkpoint_id
            )
            return SemanticCheckpointSaveResult(
                materialized,
                self._render(materialized, all_events),
                0,
                True,
            )
        active_atoms: tuple[SemanticMemoryAtom, ...] = ()
        if current is not None:
            active_atoms = self._checkpoints.materialize_semantic_checkpoint(
                scope, current.checkpoint_id
            ).atoms
        patch = self._compiler.compile(
            scope,
            pending,
            active_atoms,
            base_checkpoint_id=None if current is None else current.checkpoint_id,
        )
        result = self.apply_checkpoint_patch(
            scope,
            patch,
            head_event_id=pending[-1].event_id,
            all_events=all_events,
        )
        return SemanticCheckpointSaveResult(
            result.checkpoint,
            result.rendering,
            len(pending),
            result.idempotent,
        )

    def apply_checkpoint_patch(
        self,
        scope: MemoryScope,
        patch: SemanticCheckpointPatch,
        *,
        head_event_id: EventId,
        all_events: tuple[TaskActivityEvent, ...] | None = None,
    ) -> SemanticCheckpointSaveResult:
        current = self._checkpoints.get_current_semantic_checkpoint(scope)
        current_id = None if current is None else current.checkpoint_id
        if patch.base_checkpoint_id != current_id:
            raise SemanticMemoryApplicationError("MNEMO_SEMANTIC_PARENT_CONFLICT")
        history = all_events or self._all_events(scope)
        event_by_id = {event.event_id: event for event in history}
        if head_event_id not in event_by_id:
            raise SemanticMemoryApplicationError("MNEMO_SEMANTIC_HEAD_EVENT_NOT_FOUND")
        if head_event_id not in patch.processed_event_ids:
            patch = replace(
                patch,
                processed_event_ids=(*patch.processed_event_ids, head_event_id),
            )
        ledger = self._checkpoints.list_semantic_atoms(scope)
        active_refs: tuple[SemanticCheckpointAtom, ...] = ()
        if current is not None:
            active_refs = self._checkpoints.materialize_semantic_checkpoint(
                scope, current.checkpoint_id
            ).references
        now = self._clock()
        updated_ledger, references = apply_semantic_checkpoint_patch(
            scope=scope,
            ledger_atoms=ledger,
            active_references=active_refs,
            patch=patch,
            available_event_ids=frozenset(event_by_id),
            applied_at=now,
        )
        atoms_by_id = {atom.atom_id: atom for atom in updated_ledger}
        active_atoms = tuple(atoms_by_id[item.atom_id] for item in references)
        generation = 1 if current is None else current.generation + 1
        checkpoint_type = (
            SemanticCheckpointType.SNAPSHOT
            if generation == 1 or generation % self._snapshot_interval == 0
            else SemanticCheckpointType.DELTA
        )
        provisional = SemanticCheckpoint.create(
            scope=scope,
            parent_checkpoint_id=current_id,
            generation=generation,
            checkpoint_type=checkpoint_type,
            head_event_id=head_event_id,
            created_at=now,
            renderer_profile=SemanticRendererProfile.COMPACT,
            target_tokenizer=self._tokenizer.tokenizer_id,
            measured_tokens=0,
            compression_ratio=0.0,
            patch_digest=patch.digest,
        )
        materialized = MaterializedSemanticCheckpoint(provisional, active_atoms, references)
        rendering = self._render(materialized, history)
        final_checkpoint = replace(
            provisional,
            measured_tokens=rendering.measured_tokens,
            compression_ratio=rendering.compression_ratio,
        )
        materialized = replace(materialized, checkpoint=final_checkpoint)
        idempotent = self._checkpoints.store_semantic_checkpoint(patch, materialized)
        return SemanticCheckpointSaveResult(materialized, rendering, len(history), idempotent)

    def recall_memory(
        self,
        scope: MemoryScope,
        *,
        query_or_task: str = "",
        preferred_token_target: int = DEFAULT_PREFERRED_TOKENS,
        maximum_token_ceiling: int = DEFAULT_MAXIMUM_TOKENS,
        mode: SemanticRendererProfile = SemanticRendererProfile.COMPACT,
        tokenizer: CheckpointTokenCounter | None = None,
    ) -> RenderedSemanticCheckpoint:
        current = self._checkpoints.get_current_semantic_checkpoint(scope)
        if current is None:
            raise SemanticCheckpointNotFound("semantic checkpoint was not found")
        history = self._all_events(scope)
        materialized = self._checkpoints.materialize_semantic_checkpoint(
            scope, current.checkpoint_id
        )
        return render_semantic_checkpoint(
            materialized,
            query_or_task=query_or_task,
            preferred_token_target=preferred_token_target,
            maximum_token_ceiling=maximum_token_ceiling,
            mode=mode,
            tokenizer=tokenizer or self._tokenizer,
            evidence_events={event.event_id: event for event in history},
            full_history_text=self._history_text(history),
        )

    def render_checkpoint(
        self,
        checkpoint: MaterializedSemanticCheckpoint,
        *,
        mode: SemanticRendererProfile,
        query_or_task: str = "",
    ) -> RenderedSemanticCheckpoint:
        history = self._all_events(checkpoint.checkpoint.scope)
        return render_semantic_checkpoint(
            checkpoint,
            query_or_task=query_or_task,
            mode=mode,
            tokenizer=self._tokenizer,
            evidence_events={event.event_id: event for event in history},
            full_history_text=self._history_text(history),
        )

    def measure_checkpoint_tokens(self, rendered: str) -> int:
        return measure_checkpoint_tokens(rendered, self._tokenizer)

    def _render(
        self,
        materialized: MaterializedSemanticCheckpoint,
        history: tuple[TaskActivityEvent, ...],
    ) -> RenderedSemanticCheckpoint:
        return render_semantic_checkpoint(
            materialized,
            tokenizer=self._tokenizer,
            evidence_events={event.event_id: event for event in history},
            full_history_text=self._history_text(history),
        )

    def _all_events(self, scope: MemoryScope) -> tuple[TaskActivityEvent, ...]:
        offset = 0
        newest_first: list[TaskActivityEvent] = []
        while True:
            page = self._events.list_task_activity_events(scope, offset=offset, limit=100)
            newest_first.extend(page.items)
            if page.next_offset is None:
                break
            offset = page.next_offset
        return tuple(reversed(newest_first))

    @staticmethod
    def _history_text(events: tuple[TaskActivityEvent, ...]) -> str:
        return "\n".join(
            json.dumps(event.to_dict(), sort_keys=True, separators=(",", ":")) for event in events
        )

    @staticmethod
    def _pending_events(
        events: tuple[TaskActivityEvent, ...], compiled_event_ids: frozenset[EventId]
    ) -> tuple[TaskActivityEvent, ...]:
        return tuple(event for event in events if event.event_id not in compiled_event_ids)
