"""Incremental compilation and recall for semantic checkpoints."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from time import monotonic_ns, process_time_ns
from uuid import UUID

from mnemo_memory.packages.domain import (
    CheckpointId,
    ConflictState,
    ContentRepresentation,
    ContextItem,
    ContextItemType,
    EventId,
    MaterializedSemanticCheckpoint,
    MemoryCompiler,
    MemoryScope,
    ProvenanceNotice,
    RetentionPolicyId,
    RetentionSchedule,
    SemanticAtomKind,
    SemanticCheckpoint,
    SemanticCheckpointAtom,
    SemanticCheckpointPatch,
    SemanticCheckpointType,
    SemanticMemoryAtom,
    SemanticPatchOperation,
    SemanticPatchOperationKind,
    SemanticRendererProfile,
    Sensitivity,
    SourceTrustClass,
    TaskActivityActor,
    TaskActivityEvent,
    TaskActivityEventKind,
    ValidityState,
    apply_semantic_checkpoint_patch,
)
from mnemo_memory.packages.storage.contracts import (
    SemanticCheckpointNotFound,
    SemanticCheckpointRepository,
    TaskActivityEventRepository,
)

from .checkpoints import CheckpointView
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
CHECKPOINT_PROJECTION_SOURCE_PREFIX = "checkpoint-revision:"
_CHECKPOINT_PROJECTION_RETENTION_POLICY = RetentionPolicyId(
    UUID("1a965f61-3ec5-4e67-bf83-4c6b58146ff7")
)
_CHECKPOINT_KIND_ORDER = {kind: index for index, kind in enumerate(_PRIORITY)}


class SemanticMemoryApplicationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SemanticCheckpointSaveResult:
    checkpoint: MaterializedSemanticCheckpoint
    rendering: RenderedSemanticCheckpoint
    processed_event_count: int
    idempotent: bool
    lifecycle: SemanticLifecycleObservation | None = None


_LIFECYCLE_STAGES = frozenset(
    {
        "memory_creation",
        "validation",
        "serialization",
        "retrieval",
        "context_assembly",
        "repair",
        "persistence",
    }
)


@dataclass(frozen=True, slots=True)
class SemanticLifecycleObservation:
    """Content-free actual work for one deterministic semantic-memory operation."""

    operation: str
    stage_durations_ns: tuple[tuple[str, int], ...]
    wall_duration_ns: int
    deterministic_cpu_ns: int
    source_event_count: int
    changed_event_count: int
    rendered_tokens: int
    rendered_bytes: int
    model_input_tokens: int = 0
    model_output_tokens: int = 0
    continuation_duration_ns: int = 0
    local_inference_duration_ns: int = 0
    human_intervention_count: int = 0
    external_spend_usd: float = 0.0

    def __post_init__(self) -> None:
        if not self.operation or not self.operation.isascii():
            raise ValueError("semantic lifecycle operation is invalid")
        names = [name for name, _ in self.stage_durations_ns]
        if (
            len(names) != len(set(names))
            or any(name not in _LIFECYCLE_STAGES for name in names)
            or any(value < 0 for _, value in self.stage_durations_ns)
        ):
            raise ValueError("semantic lifecycle stages are invalid")
        integer_values = (
            self.wall_duration_ns,
            self.deterministic_cpu_ns,
            self.source_event_count,
            self.changed_event_count,
            self.rendered_tokens,
            self.rendered_bytes,
            self.model_input_tokens,
            self.model_output_tokens,
            self.continuation_duration_ns,
            self.local_inference_duration_ns,
            self.human_intervention_count,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in integer_values
        ):
            raise ValueError("semantic lifecycle measurement is invalid")
        if self.changed_event_count > self.source_event_count:
            raise ValueError("semantic lifecycle changed-event count is invalid")
        if self.external_spend_usd < 0:
            raise ValueError("semantic lifecycle external spend is invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "operation": self.operation,
            "stage_durations_ns": dict(self.stage_durations_ns),
            "wall_duration_ns": self.wall_duration_ns,
            "deterministic_cpu_ns": self.deterministic_cpu_ns,
            "source_event_count": self.source_event_count,
            "changed_event_count": self.changed_event_count,
            "rendered_tokens": self.rendered_tokens,
            "rendered_bytes": self.rendered_bytes,
            "model_input_tokens": self.model_input_tokens,
            "model_output_tokens": self.model_output_tokens,
            "continuation_duration_ns": self.continuation_duration_ns,
            "local_inference_duration_ns": self.local_inference_duration_ns,
            "human_intervention_count": self.human_intervention_count,
            "external_spend_usd": self.external_spend_usd,
            "measurement_note": (
                "stage durations are monotonic elapsed time; deterministic CPU uses process time; "
                "zero model, continuation, inference, human, and spend values mean this operation "
                "performed none of that work"
            ),
        }


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
        lifecycle_observer: Callable[[SemanticLifecycleObservation], object] | None = None,
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
        self._lifecycle_observer = lifecycle_observer

    def _lifecycle(
        self,
        *,
        operation: str,
        stages: dict[str, int],
        wall_started: int,
        cpu_started: int,
        source_event_count: int,
        changed_event_count: int,
        rendering: RenderedSemanticCheckpoint,
        notify: bool = True,
    ) -> SemanticLifecycleObservation:
        observation = SemanticLifecycleObservation(
            operation,
            tuple(sorted(stages.items())),
            monotonic_ns() - wall_started,
            process_time_ns() - cpu_started,
            source_event_count,
            changed_event_count,
            rendering.measured_tokens,
            len(rendering.text.encode("utf-8")),
        )
        if notify and self._lifecycle_observer is not None:
            with suppress(Exception):
                self._lifecycle_observer(observation)
        return observation

    @staticmethod
    def _add_stage(stages: dict[str, int], name: str, started: int) -> None:
        stages[name] = stages.get(name, 0) + monotonic_ns() - started

    @staticmethod
    def _merge_lifecycle(
        stages: dict[str, int], observation: SemanticLifecycleObservation | None
    ) -> None:
        if observation is None:
            return
        for name, duration in observation.stage_durations_ns:
            stages[name] = stages.get(name, 0) + duration

    def append_event(self, event: TaskActivityEvent) -> bool:
        return self._events.append_task_activity_event(event).idempotent

    def inspect_evidence(
        self, scope: MemoryScope, event_ids: tuple[EventId, ...]
    ) -> tuple[TaskActivityEvent, ...]:
        now = self._clock()
        events = tuple(self._events.get_task_activity_event(scope, item) for item in event_ids)
        if any(event.retention.is_expired(now) for event in events):
            raise SemanticMemoryApplicationError("MNEMO_SEMANTIC_EVIDENCE_EXPIRED")
        return events

    def get_checkpoint(
        self, scope: MemoryScope, checkpoint_id: CheckpointId
    ) -> MaterializedSemanticCheckpoint:
        materialized = self._checkpoints.materialize_semantic_checkpoint(scope, checkpoint_id)
        return self._filter_expired_atoms(materialized, self._all_events(scope))

    def list_atoms(self, scope: MemoryScope) -> tuple[SemanticMemoryAtom, ...]:
        return self._checkpoints.list_semantic_atoms(scope)

    def materialize_snapshot(
        self, scope: MemoryScope, checkpoint_id: CheckpointId
    ) -> MaterializedSemanticCheckpoint:
        return self.get_checkpoint(scope, checkpoint_id)

    def save_checkpoint_view(
        self,
        view: CheckpointView,
        *,
        retention_days: int,
    ) -> SemanticCheckpointSaveResult:
        """Project one accepted public checkpoint revision into exact-scope semantic memory."""

        wall_started = monotonic_ns()
        cpu_started = process_time_ns()
        stages: dict[str, int] = {}
        stage_started = monotonic_ns()
        if not isinstance(view, CheckpointView):
            raise TypeError("semantic checkpoint projection requires a checkpoint view")
        if (
            isinstance(retention_days, bool)
            or not isinstance(retention_days, int)
            or not 1 <= retention_days <= 3_650
        ):
            raise ValueError("semantic checkpoint retention must be between 1 and 3650 days")
        self._add_stage(stages, "validation", stage_started)

        stage_started = monotonic_ns()
        candidates = self._checkpoint_projection_events(view, retention_days)
        self._add_stage(stages, "memory_creation", stage_started)

        stage_started = monotonic_ns()
        scope = view.revision.scope
        current = self._checkpoints.get_current_semantic_checkpoint(scope)
        history = self._all_events(scope)
        compiled = self._checkpoints.list_compiled_semantic_event_ids(scope)
        active_atoms: tuple[SemanticMemoryAtom, ...] = ()
        if current is not None:
            active_atoms = self._filter_expired_atoms(
                self._checkpoints.materialize_semantic_checkpoint(scope, current.checkpoint_id),
                history,
            ).atoms
        projected_event_ids = {
            event.event_id
            for event in history
            if event.source_event_key.startswith(CHECKPOINT_PROJECTION_SOURCE_PREFIX)
        }
        projected_atoms = tuple(
            atom
            for atom in active_atoms
            if atom.source_event_ids and set(atom.source_event_ids).issubset(projected_event_ids)
        )
        desired_identities = {self._event_semantic_identity(event) for event in candidates}
        current_identities = {(atom.kind, atom.object_value) for atom in projected_atoms}
        existing_event_ids = {event.event_id for event in history}
        selected = tuple(
            event
            for event in candidates
            if event.event_id not in existing_event_ids
            and self._event_semantic_identity(event) not in current_identities
        )
        stale_projected = tuple(
            atom
            for atom in projected_atoms
            if (atom.kind, atom.object_value) not in desired_identities
        )
        is_new_revision = any(event.event_id not in existing_event_ids for event in candidates)
        if stale_projected and not selected and is_new_revision:
            selected = (candidates[0],)
        self._add_stage(stages, "retrieval", stage_started)

        stage_started = monotonic_ns()
        for event in selected:
            self.append_event(event)
        self._add_stage(stages, "memory_creation", stage_started)

        history = (*history, *selected)
        pending = tuple(
            event
            for event in candidates
            if event.event_id not in compiled
            and (event.event_id in existing_event_ids or event in selected)
        )
        if not pending:
            if current is None:
                raise SemanticMemoryApplicationError("MNEMO_SEMANTIC_EVIDENCE_REQUIRED")
            materialized = self._filter_expired_atoms(
                self._checkpoints.materialize_semantic_checkpoint(scope, current.checkpoint_id),
                history,
            )
            stage_started = monotonic_ns()
            rendering = self._render(materialized, history)
            self._add_stage(stages, "serialization", stage_started)
            lifecycle = self._lifecycle(
                operation="checkpoint_view_save",
                stages=stages,
                wall_started=wall_started,
                cpu_started=cpu_started,
                source_event_count=len(history),
                changed_event_count=0,
                rendering=rendering,
            )
            return SemanticCheckpointSaveResult(
                materialized,
                rendering,
                0,
                True,
                lifecycle,
            )

        stage_started = monotonic_ns()
        patch = self._compiler.compile(
            scope,
            pending,
            active_atoms,
            base_checkpoint_id=None if current is None else current.checkpoint_id,
        )
        self._add_stage(stages, "validation", stage_started)
        stage_started = monotonic_ns()
        pending_kinds = {
            self._event_semantic_identity(event)[0]
            for event in pending
            if event.source_event_key.startswith(CHECKPOINT_PROJECTION_SOURCE_PREFIX)
        }
        prior_projected = tuple(
            atom
            for atom in stale_projected
            if not (
                atom.kind in {SemanticAtomKind.GOAL, SemanticAtomKind.DECISION}
                and atom.kind in pending_kinds
            )
        )
        if prior_projected:
            patch = replace(
                patch,
                operations=(
                    *(
                        SemanticPatchOperation(
                            SemanticPatchOperationKind.REMOVE,
                            target_atom_id=atom.atom_id,
                            inclusion_reason="replaced_by_current_checkpoint_revision",
                        )
                        for atom in sorted(prior_projected, key=lambda item: str(item.atom_id))
                    ),
                    *patch.operations,
                ),
            )
        self._add_stage(stages, "repair", stage_started)
        result = self.apply_checkpoint_patch(
            scope,
            patch,
            head_event_id=pending[-1].event_id,
            all_events=history,
            _observe_lifecycle=False,
        )
        self._merge_lifecycle(stages, result.lifecycle)
        lifecycle = self._lifecycle(
            operation="checkpoint_view_save",
            stages=stages,
            wall_started=wall_started,
            cpu_started=cpu_started,
            source_event_count=len(history),
            changed_event_count=len(pending),
            rendering=result.rendering,
        )
        return SemanticCheckpointSaveResult(
            result.checkpoint,
            result.rendering,
            len(pending),
            result.idempotent,
            lifecycle,
        )

    def automatic_context_item(
        self,
        scope: MemoryScope,
        *,
        preferred_token_target: int,
        maximum_token_ceiling: int,
    ) -> tuple[ContextItem, ProvenanceNotice]:
        """Return a compact semantic handoff plus an exact fact-to-evidence trace."""

        wall_started = monotonic_ns()
        cpu_started = process_time_ns()
        stages: dict[str, int] = {}
        memory_ceiling = maximum_token_ceiling
        rendering: RenderedSemanticCheckpoint | None = None
        content = ""
        events: tuple[TaskActivityEvent, ...] = ()
        trace = ""
        for _ in range(3):
            rendering, recalled_events = self._recall_memory_with_events(
                scope,
                preferred_token_target=min(preferred_token_target, memory_ceiling),
                maximum_token_ceiling=memory_ceiling,
                mode=SemanticRendererProfile.COMPACT,
            )
            stage_started = monotonic_ns()
            aliases = tuple(
                (alias, event_id)
                for alias, event_id in rendering.evidence_aliases
                if re.search(rf"\b{re.escape(alias)}\b", rendering.text)
            )
            recalled_by_id = {event.event_id: event for event in recalled_events}
            try:
                events = tuple(recalled_by_id[event_id] for _, event_id in aliases)
            except KeyError as error:
                raise SemanticMemoryApplicationError("MNEMO_SEMANTIC_EVIDENCE_REQUIRED") from error
            event_by_id = {event.event_id: event for event in events}
            self._add_stage(stages, "retrieval", stage_started)
            stage_started = monotonic_ns()
            trace = "MNEMO_EVIDENCE_TRACE " + ";".join(
                f"{alias}="
                + ",".join(
                    str(item.evidence_id) for item in event_by_id[event_id].evidence_references
                )
                for alias, event_id in aliases
            )
            content = f"{rendering.text}\n{trace}"
            measured = self._tokenizer.count(content)
            self._add_stage(stages, "context_assembly", stage_started)
            if measured <= maximum_token_ceiling:
                break
            stage_started = monotonic_ns()
            overage = measured - maximum_token_ceiling
            reduced = memory_ceiling - overage - 4
            if reduced >= memory_ceiling:
                break
            memory_ceiling = max(1, reduced)
            self._add_stage(stages, "repair", stage_started)
        assert rendering is not None
        measured = self._tokenizer.count(content)
        if measured > maximum_token_ceiling:
            raise SemanticMemoryApplicationError("MNEMO_SEMANTIC_CONTEXT_BUDGET_EXCEEDED")
        evidence_by_id = {
            evidence.evidence_id: evidence
            for event in events
            for evidence in event.evidence_references
        }
        evidence = tuple(evidence_by_id[item] for item in sorted(evidence_by_id, key=str))
        if not evidence:
            raise SemanticMemoryApplicationError("MNEMO_SEMANTIC_EVIDENCE_REQUIRED")
        stage_started = monotonic_ns()
        current = self._checkpoints.get_current_semantic_checkpoint(scope)
        if current is None or str(current.checkpoint_id) != rendering.checkpoint_id:
            raise SemanticMemoryApplicationError("MNEMO_SEMANTIC_CHECKPOINT_CHANGED")
        item_id = f"semantic-checkpoint:{rendering.checkpoint_id}:live-m3"
        item = ContextItem(
            item_id=item_id,
            item_type=ContextItemType.ACTIVE_TASK_CHECKPOINT,
            source_scope=scope,
            content=content,
            content_representation=ContentRepresentation.UNTRUSTED_EVIDENCE,
            token_estimate=measured,
            evidence_references=evidence,
            source_trust=SourceTrustClass.APPROVED_CHECKPOINT,
            sensitivity=Sensitivity.NORMAL,
            validity=ValidityState.CURRENT,
            ranking=None,
            conflict_state=ConflictState.NONE,
            observed_at=current.created_at,
        )
        provenance = ProvenanceNotice(
            provenance_id=f"provenance:{item_id}",
            item_id=item_id,
            source_reference=(
                f"mnemo:semantic-checkpoint/{rendering.checkpoint_id}/render/live-m3"
            ),
            source_digest=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            evidence_references=evidence,
        )
        self._add_stage(stages, "context_assembly", stage_started)
        self._lifecycle(
            operation="automatic_context_assembly",
            stages=stages,
            wall_started=wall_started,
            cpu_started=cpu_started,
            source_event_count=len(events),
            changed_event_count=0,
            rendering=replace(rendering, measured_tokens=measured, text=content),
        )
        return item, provenance

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
            materialized = self._filter_expired_atoms(materialized, all_events)
            return SemanticCheckpointSaveResult(
                materialized,
                self._render(materialized, all_events),
                0,
                True,
            )
        active_atoms: tuple[SemanticMemoryAtom, ...] = ()
        if current is not None:
            active_atoms = self._filter_expired_atoms(
                self._checkpoints.materialize_semantic_checkpoint(scope, current.checkpoint_id),
                all_events,
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
            result.lifecycle,
        )

    def apply_checkpoint_patch(
        self,
        scope: MemoryScope,
        patch: SemanticCheckpointPatch,
        *,
        head_event_id: EventId,
        all_events: tuple[TaskActivityEvent, ...] | None = None,
        _observe_lifecycle: bool = True,
    ) -> SemanticCheckpointSaveResult:
        wall_started = monotonic_ns()
        cpu_started = process_time_ns()
        stages: dict[str, int] = {}
        stage_started = monotonic_ns()
        current = self._checkpoints.get_current_semantic_checkpoint(scope)
        current_id = None if current is None else current.checkpoint_id
        history = all_events or self._all_events(scope)
        ledger = self._checkpoints.list_semantic_atoms(scope)
        active_refs: tuple[SemanticCheckpointAtom, ...] = ()
        if current is not None:
            active_refs = self._filter_expired_atoms(
                self._checkpoints.materialize_semantic_checkpoint(scope, current.checkpoint_id),
                history,
            ).references
        self._add_stage(stages, "retrieval", stage_started)

        stage_started = monotonic_ns()
        if patch.base_checkpoint_id != current_id:
            raise SemanticMemoryApplicationError("MNEMO_SEMANTIC_PARENT_CONFLICT")
        event_by_id = {event.event_id: event for event in history}
        if head_event_id not in event_by_id:
            raise SemanticMemoryApplicationError("MNEMO_SEMANTIC_HEAD_EVENT_NOT_FOUND")
        if head_event_id not in patch.processed_event_ids:
            patch = replace(
                patch,
                processed_event_ids=(*patch.processed_event_ids, head_event_id),
            )
        self._add_stage(stages, "validation", stage_started)

        stage_started = monotonic_ns()
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
        self._add_stage(stages, "repair", stage_started)

        stage_started = monotonic_ns()
        history_text = self._history_text(history)
        self._add_stage(stages, "serialization", stage_started)
        stage_started = monotonic_ns()
        rendering = render_semantic_checkpoint(
            materialized,
            tokenizer=self._tokenizer,
            evidence_events={event.event_id: event for event in history},
            full_history_text=history_text,
        )
        self._add_stage(stages, "context_assembly", stage_started)
        final_checkpoint = replace(
            provisional,
            measured_tokens=rendering.measured_tokens,
            compression_ratio=rendering.compression_ratio,
        )
        materialized = replace(materialized, checkpoint=final_checkpoint)
        stage_started = monotonic_ns()
        idempotent = self._checkpoints.store_semantic_checkpoint(patch, materialized)
        self._add_stage(stages, "persistence", stage_started)
        lifecycle = self._lifecycle(
            operation="checkpoint_patch_apply",
            stages=stages,
            wall_started=wall_started,
            cpu_started=cpu_started,
            source_event_count=len(history),
            changed_event_count=len(patch.processed_event_ids),
            rendering=rendering,
            notify=_observe_lifecycle,
        )
        return SemanticCheckpointSaveResult(
            materialized, rendering, len(history), idempotent, lifecycle
        )

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
        rendering, _ = self._recall_memory_with_events(
            scope,
            query_or_task=query_or_task,
            preferred_token_target=preferred_token_target,
            maximum_token_ceiling=maximum_token_ceiling,
            mode=mode,
            tokenizer=tokenizer,
        )
        return rendering

    def _recall_memory_with_events(
        self,
        scope: MemoryScope,
        *,
        query_or_task: str = "",
        preferred_token_target: int = DEFAULT_PREFERRED_TOKENS,
        maximum_token_ceiling: int = DEFAULT_MAXIMUM_TOKENS,
        mode: SemanticRendererProfile = SemanticRendererProfile.COMPACT,
        tokenizer: CheckpointTokenCounter | None = None,
    ) -> tuple[RenderedSemanticCheckpoint, tuple[TaskActivityEvent, ...]]:
        wall_started = monotonic_ns()
        cpu_started = process_time_ns()
        stages: dict[str, int] = {}
        stage_started = monotonic_ns()
        current = self._checkpoints.get_current_semantic_checkpoint(scope)
        if current is None:
            raise SemanticCheckpointNotFound("semantic checkpoint was not found")
        history = self._all_events(scope)
        materialized = self._checkpoints.materialize_semantic_checkpoint(
            scope, current.checkpoint_id
        )
        materialized = self._filter_expired_atoms(materialized, history)
        if not materialized.atoms:
            raise SemanticCheckpointNotFound("semantic checkpoint has no current evidence")
        self._add_stage(stages, "retrieval", stage_started)
        stage_started = monotonic_ns()
        history_text = self._history_text(history)
        self._add_stage(stages, "serialization", stage_started)
        stage_started = monotonic_ns()
        rendering = render_semantic_checkpoint(
            materialized,
            query_or_task=query_or_task,
            preferred_token_target=preferred_token_target,
            maximum_token_ceiling=maximum_token_ceiling,
            mode=mode,
            tokenizer=tokenizer or self._tokenizer,
            evidence_events={event.event_id: event for event in history},
            full_history_text=history_text,
        )
        self._add_stage(stages, "context_assembly", stage_started)
        self._lifecycle(
            operation="checkpoint_recall",
            stages=stages,
            wall_started=wall_started,
            cpu_started=cpu_started,
            source_event_count=len(history),
            changed_event_count=0,
            rendering=rendering,
        )
        return rendering, history

    def render_checkpoint(
        self,
        checkpoint: MaterializedSemanticCheckpoint,
        *,
        mode: SemanticRendererProfile,
        query_or_task: str = "",
    ) -> RenderedSemanticCheckpoint:
        history = self._all_events(checkpoint.checkpoint.scope)
        checkpoint = self._filter_expired_atoms(checkpoint, history)
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
        now = self._clock()
        return tuple(
            event for event in reversed(newest_first) if not event.retention.is_expired(now)
        )

    @staticmethod
    def _filter_expired_atoms(
        checkpoint: MaterializedSemanticCheckpoint,
        events: tuple[TaskActivityEvent, ...],
    ) -> MaterializedSemanticCheckpoint:
        available = {event.event_id for event in events}
        atoms = tuple(
            atom for atom in checkpoint.atoms if set(atom.source_event_ids).issubset(available)
        )
        retained = {atom.atom_id for atom in atoms}
        references = tuple(
            reference for reference in checkpoint.references if reference.atom_id in retained
        )
        return replace(checkpoint, atoms=atoms, references=references)

    @staticmethod
    def _event_semantic_identity(
        event: TaskActivityEvent,
    ) -> tuple[SemanticAtomKind, str]:
        explicit = _EXPLICIT_KIND.match(event.summary)
        if explicit is None:
            raise SemanticMemoryApplicationError("MNEMO_SEMANTIC_PROJECTION_KIND_REQUIRED")
        return SemanticAtomKind(explicit.group(1).lower()), explicit.group(2)

    @staticmethod
    def _checkpoint_projection_events(
        view: CheckpointView,
        retention_days: int,
    ) -> tuple[TaskActivityEvent, ...]:
        revision = view.revision
        content = revision.content
        grouped: dict[SemanticAtomKind, list[str]] = {}

        def add(default_kind: SemanticAtomKind, values: tuple[str, ...]) -> None:
            for value in values:
                explicit = _EXPLICIT_KIND.match(value)
                kind = default_kind
                meaning = value
                if explicit is not None:
                    kind = SemanticAtomKind(explicit.group(1).lower())
                    meaning = explicit.group(2)
                grouped.setdefault(kind, []).append(meaning)

        add(SemanticAtomKind.GOAL, (content.task_objective,))
        add(SemanticAtomKind.STATE, (content.current_state,))
        add(SemanticAtomKind.RESULT, content.completed_work)
        add(SemanticAtomKind.NEXT_ACTION, content.remaining_work)
        add(SemanticAtomKind.DECISION, content.decisions)
        add(SemanticAtomKind.FAILURE, content.failures)
        add(SemanticAtomKind.CONSTRAINT, content.blockers)
        if content.relevant_files:
            add(
                SemanticAtomKind.FACT,
                ("Relevant files: " + ", ".join(content.relevant_files),),
            )
        if content.relevant_artifacts:
            add(
                SemanticAtomKind.FACT,
                ("Relevant artifacts: " + ", ".join(content.relevant_artifacts),),
            )
        add(SemanticAtomKind.RESULT, content.verification_performed)
        for lesson in content.lessons:
            add(
                SemanticAtomKind.FACT,
                (
                    "Correction lesson: trigger="
                    f"{lesson.trigger}; mistaken_assumption={lesson.mistaken_assumption}; "
                    f"correction={lesson.correction}; prevention={lesson.prevention}",
                ),
            )

        created_at = revision.created_at
        retention = RetentionSchedule(
            _CHECKPOINT_PROJECTION_RETENTION_POLICY,
            False,
            created_at,
            created_at,
            created_at,
            None,
            created_at + timedelta(days=retention_days),
        )
        return tuple(
            TaskActivityEvent.create(
                scope=revision.scope,
                kind=TaskActivityEventKind.CONVERSATION_HANDOFF,
                actor=TaskActivityActor.AGENT,
                summary=f"{kind.value}: {' ; '.join(grouped[kind])}",
                source_event_key=(
                    f"{CHECKPOINT_PROJECTION_SOURCE_PREFIX}{revision.checkpoint_id}:"
                    f"{revision.revision_id}:{kind.value}"
                ),
                sensitivity=Sensitivity.NORMAL,
                retention=retention,
                occurred_at=created_at,
                evidence_references=revision.evidence_references,
            )
            for kind in sorted(grouped, key=lambda item: _CHECKPOINT_KIND_ORDER[item])
        )

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
