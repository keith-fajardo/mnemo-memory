"""Comparable offline adapters for baseline and Mnemo memory conditions."""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast

from mnemo_memory.packages.application.semantic_memory import (
    SemanticCheckpointSaveResult,
    SemanticMemoryService,
)
from mnemo_memory.packages.application.semantic_rendering import (
    CheckpointTokenCounter,
    ConservativeTokenCounter,
)
from mnemo_memory.packages.domain import (
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    MemoryScope,
    OwnerId,
    ProjectId,
    RetentionPolicyId,
    RetentionSchedule,
    ScopeLevel,
    SemanticRendererProfile,
    Sensitivity,
    SessionId,
    SourceId,
    SourceTrustClass,
    TaskActivityActor,
    TaskActivityEvent,
    TaskActivityEventKind,
    TaskId,
    VerificationStatus,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.storage import (
    ReferenceSemanticCheckpointRepository,
    ReferenceTaskActivityEventRepository,
)

from .models import (
    ConditionId,
    ConditionOutput,
    EvaluationConfig,
    MeasurementSource,
    Scenario,
    TokenAccount,
)

_BASE_TIME = datetime(2026, 8, 12, 0, 0, tzinfo=UTC)
_WORD = re.compile(r"[a-z0-9_]+")
_SIGNAL_PRIORITY = {
    "goal": 0,
    "constraint": 1,
    "decision": 2,
    "open_question": 3,
    "next_action": 4,
    "failure": 5,
    "fact": 6,
    "state": 7,
    "preference": 8,
    "inference": 9,
    "result": 10,
}


class ConditionAdapter(Protocol):
    condition_id: ConditionId

    def evaluate(self, scenario: Scenario, reuse_count: int) -> ConditionOutput: ...


@dataclass(frozen=True, slots=True)
class _BaseAdapter:
    config: EvaluationConfig
    tokenizer: CheckpointTokenCounter

    @property
    def condition_id(self) -> ConditionId:
        raise NotImplementedError

    def _common_tokens(self, scenario: Scenario, reuse_count: int) -> tuple[int, int]:
        task = self.tokenizer.count(scenario.task_prompt) * reuse_count
        expected = self.tokenizer.count("\n".join(scenario.ground_truth.expected_continuation))
        return task, expected * reuse_count


@dataclass(frozen=True, slots=True)
class FullHistoryAdapter(_BaseAdapter):
    condition_id = ConditionId.FULL_HISTORY

    def evaluate(self, scenario: Scenario, reuse_count: int) -> ConditionOutput:
        started = time.perf_counter_ns()
        history = render_usable_history(scenario)
        context_tokens = self.tokenizer.count(history)
        work_input, work_output = self._common_tokens(scenario, reuse_count)
        return ConditionOutput(
            self.condition_id,
            True,
            None,
            history,
            TokenAccount(
                agent_work_input=work_input,
                agent_work_output=work_output,
                baseline_context=context_tokens * reuse_count,
                tokenizer_id=self.tokenizer.tokenizer_id,
            ),
            context_tokens,
            0,
            0,
            _elapsed_ms(started),
            tuple(event.event_key for event in scenario.events),
            True,
            True,
            1.0,
        )


@dataclass(frozen=True, slots=True)
class SlidingWindowAdapter(_BaseAdapter):
    condition_id = ConditionId.SLIDING_WINDOW

    def evaluate(self, scenario: Scenario, reuse_count: int) -> ConditionOutput:
        started = time.perf_counter_ns()
        selected: list[str] = []
        evidence: list[str] = []
        for event in reversed(scenario.events):
            line = render_event(event.event_key, event.actor, event.summary)
            candidate = "\n".join(reversed([*selected, line]))
            if self.tokenizer.count(candidate) > self.config.sliding_window_tokens:
                break
            selected.append(line)
            evidence.append(event.event_key)
        context = "\n".join(reversed(selected))
        context_tokens = self.tokenizer.count(context)
        work_input, work_output = self._common_tokens(scenario, reuse_count)
        return ConditionOutput(
            self.condition_id,
            True,
            None,
            context,
            TokenAccount(
                agent_work_input=work_input,
                agent_work_output=work_output,
                baseline_context=context_tokens * reuse_count,
                tokenizer_id=self.tokenizer.tokenizer_id,
            ),
            context_tokens,
            0,
            0,
            _elapsed_ms(started),
            tuple(reversed(evidence)),
            True,
            True,
            1.0,
        )


@dataclass(frozen=True, slots=True)
class RollingSummaryAdapter(_BaseAdapter):
    condition_id = ConditionId.ROLLING_SUMMARY

    def evaluate(self, scenario: Scenario, reuse_count: int) -> ConditionOutput:
        started = time.perf_counter_ns()
        history = render_usable_history(scenario)
        summary, evidence = rolling_natural_language_summary(
            scenario, self.config.rolling_summary_tokens, self.tokenizer
        )
        history_tokens = self.tokenizer.count(history)
        summary_tokens = self.tokenizer.count(summary)
        work_input, work_output = self._common_tokens(scenario, reuse_count)
        return ConditionOutput(
            self.condition_id,
            True,
            None,
            summary,
            TokenAccount(
                agent_work_input=work_input,
                agent_work_output=work_output,
                baseline_context=summary_tokens * reuse_count,
                baseline_compaction_input=history_tokens,
                baseline_compaction_output=summary_tokens,
                tokenizer_id=self.tokenizer.tokenizer_id,
            ),
            summary_tokens,
            history_tokens + summary_tokens,
            0,
            _elapsed_ms(started),
            evidence,
            True,
            True,
            1.0,
        )


@dataclass(frozen=True, slots=True)
class ProviderNativeAdapter(_BaseAdapter):
    condition_id = ConditionId.PROVIDER_NATIVE

    def evaluate(self, scenario: Scenario, reuse_count: int) -> ConditionOutput:
        reason = (
            "provider-native compaction adapter is not configured"
            if not self.config.provider_native_configured
            else "provider-native compaction requires a live provider adapter"
        )
        return ConditionOutput(
            self.condition_id,
            False,
            reason,
            "",
            TokenAccount(
                source=MeasurementSource.NOT_AVAILABLE,
                tokenizer_id=self.tokenizer.tokenizer_id,
            ),
            0,
            0,
            0,
            0.0,
            (),
            False,
            False,
            0.0,
        )


@dataclass(frozen=True, slots=True)
class _MnemoScenarioState:
    service: SemanticMemoryService
    scope: MemoryScope
    save: SemanticCheckpointSaveResult
    key_by_event_id: Mapping[str, str]
    history_tokens: int
    validation_tokens: int
    build_latency_ms: float


@dataclass(slots=True)
class _MnemoStateCache:
    tokenizer: CheckpointTokenCounter
    states: dict[str, _MnemoScenarioState] = field(default_factory=dict)

    def get(self, scenario: Scenario) -> _MnemoScenarioState:
        existing = self.states.get(scenario.scenario_id)
        if existing is not None:
            return existing
        started = time.perf_counter_ns()
        scope = scenario_scope(scenario.scenario_id)
        events = ReferenceTaskActivityEventRepository()
        checkpoints = ReferenceSemanticCheckpointRepository(events)
        service = SemanticMemoryService(
            events,
            checkpoints,
            clock=lambda: _BASE_TIME + timedelta(days=1),
            tokenizer=self.tokenizer,
        )
        domain_events, key_by_event_id = build_domain_events(scenario, scope)
        canary_scope = scenario_scope(f"{scenario.scenario_id}:other-project")
        canary = build_domain_events(scenario, canary_scope, canary_only=True)[0][0]
        service.append_event(canary)
        save = service.save_checkpoint(scope, events=domain_events)
        history_tokens = self.tokenizer.count(render_usable_history(scenario))
        validation_text = "\n".join(
            json.dumps(atom.to_dict(), sort_keys=True, separators=(",", ":"))
            for atom in save.checkpoint.atoms
        )
        state = _MnemoScenarioState(
            service,
            scope,
            save,
            key_by_event_id,
            history_tokens,
            self.tokenizer.count(validation_text),
            _elapsed_ms(started),
        )
        self.states[scenario.scenario_id] = state
        return state


@dataclass(frozen=True, slots=True)
class MnemoAdapter(_BaseAdapter):
    memory_condition: ConditionId
    preferred_tokens: int
    maximum_tokens: int
    selective_retrieval: bool = False
    state_cache: _MnemoStateCache | None = None

    @property
    def condition_id(self) -> ConditionId:
        return self.memory_condition

    def evaluate(self, scenario: Scenario, reuse_count: int) -> ConditionOutput:
        started = time.perf_counter_ns()
        cache = self.state_cache or _MnemoStateCache(self.tokenizer)
        state = cache.get(scenario)
        rendering = state.service.recall_memory(
            state.scope,
            query_or_task=scenario.task_prompt,
            preferred_token_target=self.preferred_tokens,
            maximum_token_ceiling=self.maximum_tokens,
            mode=SemanticRendererProfile.COMPACT,
            tokenizer=self.tokenizer,
        )
        if rendering.omission is not None and self.maximum_tokens > self.preferred_tokens:
            rendering = state.service.recall_memory(
                state.scope,
                query_or_task=scenario.task_prompt,
                preferred_token_target=self.maximum_tokens,
                maximum_token_ceiling=self.maximum_tokens,
                mode=SemanticRendererProfile.COMPACT,
                tokenizer=self.tokenizer,
            )
        repeated = tuple(
            state.service.recall_memory(
                state.scope,
                query_or_task=scenario.task_prompt,
                preferred_token_target=rendering.preferred_target,
                maximum_token_ceiling=self.maximum_tokens,
                mode=SemanticRendererProfile.COMPACT,
                tokenizer=self.tokenizer,
            ).text
            for _ in range(self.config.drift_cycles)
        )
        context = rendering.text
        included_keys = _included_evidence_keys(
            rendering.text, rendering.evidence_aliases, state.key_by_event_id
        )
        query_tokens = retrieved_tokens = 0
        if self.selective_retrieval:
            retrieved, retrieval_keys = select_evidence(
                scenario,
                excluded=frozenset(included_keys),
                maximum_tokens=self.config.retrieval_tokens,
                tokenizer=self.tokenizer,
            )
            if retrieved:
                context = f"{context}\nEVIDENCE_EXPANSION\n{retrieved}"
                included_keys = tuple(dict.fromkeys((*included_keys, *retrieval_keys)))
                query_tokens = self.tokenizer.count(scenario.task_prompt) * reuse_count
                retrieved_tokens = self.tokenizer.count(retrieved) * reuse_count
        checkpoint_tokens = self.tokenizer.count(state.save.rendering.text)
        context_tokens = self.tokenizer.count(context)
        work_input, work_output = self._common_tokens(scenario, reuse_count)
        omission_valid = rendering.omission is None or bool(
            rendering.omission.omitted_unit_count
            and rendering.omission.omitted_unit_kinds
            and rendering.omission.reason
            and rendering.omission.retrieval_handles
        )
        return ConditionOutput(
            self.condition_id,
            True,
            None,
            context,
            TokenAccount(
                agent_work_input=work_input,
                agent_work_output=work_output,
                checkpoint_save_input=state.history_tokens,
                checkpoint_save_output=checkpoint_tokens,
                validation=state.validation_tokens,
                checkpoint_recall=rendering.measured_tokens * reuse_count,
                retrieval_query=query_tokens,
                retrieved_evidence=retrieved_tokens,
                tokenizer_id=self.tokenizer.tokenizer_id,
            ),
            context_tokens,
            state.history_tokens + checkpoint_tokens,
            state.validation_tokens,
            state.build_latency_ms + _elapsed_ms(started),
            included_keys,
            omission_valid,
            len(set(repeated)) == 1,
            1.0 if len(set(repeated)) == 1 else 0.0,
        )


def build_condition_adapters(
    config: EvaluationConfig, tokenizer: CheckpointTokenCounter | None = None
) -> tuple[ConditionAdapter, ...]:
    counter = tokenizer or ConservativeTokenCounter()
    if counter.tokenizer_id != config.token_counter_id:
        raise ValueError("configured tokenizer does not match the supplied counter")
    state_cache = _MnemoStateCache(counter)
    adapters = (
        FullHistoryAdapter(config, counter),
        SlidingWindowAdapter(config, counter),
        RollingSummaryAdapter(config, counter),
        ProviderNativeAdapter(config, counter),
        MnemoAdapter(
            config,
            counter,
            ConditionId.MNEMO_COMPACT,
            config.compact_tokens,
            config.compact_tokens,
            False,
            state_cache,
        ),
        MnemoAdapter(
            config,
            counter,
            ConditionId.MNEMO_ADAPTIVE,
            config.compact_tokens,
            config.adaptive_tokens,
            False,
            state_cache,
        ),
        MnemoAdapter(
            config,
            counter,
            ConditionId.MNEMO_RETRIEVAL,
            config.compact_tokens,
            config.adaptive_tokens,
            True,
            state_cache,
        ),
    )
    return cast(tuple[ConditionAdapter, ...], adapters)


def render_event(event_key: str, actor: str, summary: str) -> str:
    return f"EVENT evidence={event_key} actor={actor} | {summary}"


def render_usable_history(scenario: Scenario) -> str:
    return "\n".join(
        render_event(event.event_key, event.actor, event.summary) for event in scenario.events
    )


def rolling_natural_language_summary(
    scenario: Scenario,
    maximum_tokens: int,
    tokenizer: CheckpointTokenCounter,
) -> tuple[str, tuple[str, ...]]:
    """Build a conventional whole-bullet rolling summary without semantic-ledger state."""

    latest_singleton: dict[tuple[str, str], tuple[int, str, str]] = {}
    retained: list[tuple[int, int, str, str]] = []
    for index, event in enumerate(scenario.events):
        prefix, separator, meaning = event.summary.partition(":")
        kind = prefix.strip().lower() if separator else "other"
        if kind not in _SIGNAL_PRIORITY:
            continue
        line = (
            f"{kind.replace('_', ' ').title()} "
            f"({event.actor}; evidence={event.event_key}): {meaning.strip()}"
        )
        if kind in {"decision", "open_question", "next_action"}:
            latest_singleton[(kind, event.actor)] = (index, line, event.event_key)
        else:
            retained.append((_SIGNAL_PRIORITY[kind], index, line, event.event_key))
    retained.extend(
        (_SIGNAL_PRIORITY[key[0]], index, line, evidence)
        for key, (index, line, evidence) in latest_singleton.items()
    )
    ordered = sorted(retained, key=lambda item: (item[0], -item[1], item[3]))
    selected: list[str] = ["Rolling task summary:"]
    evidence_keys: list[str] = []
    for _, _, line, evidence in ordered:
        candidate = "\n".join((*selected, f"- {line}"))
        if tokenizer.count(candidate) <= maximum_tokens:
            selected.append(f"- {line}")
            evidence_keys.append(evidence)
    omitted = len(ordered) - len(evidence_keys)
    if omitted:
        notice = f"- {omitted} older or lower-priority summary units omitted."
        if tokenizer.count("\n".join((*selected, notice))) <= maximum_tokens:
            selected.append(notice)
    return "\n".join(selected), tuple(evidence_keys)


def select_evidence(
    scenario: Scenario,
    *,
    excluded: frozenset[str],
    maximum_tokens: int,
    tokenizer: CheckpointTokenCounter,
) -> tuple[str, tuple[str, ...]]:
    query_words = set(_WORD.findall(scenario.task_prompt.lower()))
    latest_decision_by_actor: dict[str, int] = {}
    for index, event in enumerate(scenario.events):
        if event.summary.partition(":")[0].strip().lower() == "decision":
            latest_decision_by_actor[event.actor] = index
    candidates: list[tuple[int, int, str, str, str]] = []
    for index, event in enumerate(scenario.events):
        if event.event_key in excluded or event.event_key.startswith("noise-"):
            continue
        words = set(_WORD.findall(event.summary.lower()))
        overlap = len(words & query_words)
        prefix = event.summary.partition(":")[0].strip().lower()
        if prefix == "decision" and latest_decision_by_actor.get(event.actor) != index:
            continue
        signal = 2 if prefix in {"fact", "failure", "constraint", "decision"} else 0
        candidates.append((-(overlap + signal), index, event.event_key, event.actor, event.summary))
    lines: list[str] = []
    evidence: list[str] = []
    for _, _, key, actor, summary in sorted(candidates):
        line = render_event(key, actor, summary)
        if tokenizer.count("\n".join((*lines, line))) > maximum_tokens:
            continue
        lines.append(line)
        evidence.append(key)
    return "\n".join(lines), tuple(evidence)


def scenario_scope(seed_text: str) -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string(str(uuid.uuid5(uuid.NAMESPACE_URL, f"owner:{seed_text}"))),
        ScopeLevel.TASK,
        Visibility.PROJECT,
        WorkspaceId.from_string(str(uuid.uuid5(uuid.NAMESPACE_URL, f"workspace:{seed_text}"))),
        ProjectId.from_string(str(uuid.uuid5(uuid.NAMESPACE_URL, f"project:{seed_text}"))),
        SessionId.from_string(str(uuid.uuid5(uuid.NAMESPACE_URL, f"session:{seed_text}"))),
        TaskId.from_string(str(uuid.uuid5(uuid.NAMESPACE_URL, f"task:{seed_text}"))),
    )


def build_domain_events(
    scenario: Scenario,
    scope: MemoryScope,
    *,
    canary_only: bool = False,
) -> tuple[tuple[TaskActivityEvent, ...], Mapping[str, str]]:
    specs = scenario.events
    if canary_only:
        specs = (
            type(scenario.events[0])(
                "cross-scope-canary",
                "user",
                "constraint: CROSS-SCOPE-CANARY must never appear in another project.",
                0.0,
            ),
        )
    events: list[TaskActivityEvent] = []
    keys: dict[str, str] = {}
    for index, spec in enumerate(specs):
        at = _BASE_TIME + timedelta(seconds=index)
        source_uri = f"fixture://viability/{scenario.scenario_id}/{spec.event_key}"
        digest = hashlib.sha256(spec.summary.encode("utf-8")).hexdigest()
        event = TaskActivityEvent.create(
            scope=scope,
            kind=TaskActivityEventKind.TASK_ACTIVITY,
            actor=TaskActivityActor(spec.actor),
            summary=spec.summary,
            source_event_key=f"{scenario.scenario_id}:{spec.event_key}",
            sensitivity=Sensitivity.NORMAL,
            retention=RetentionSchedule(
                RetentionPolicyId.from_string(
                    str(uuid.uuid5(uuid.NAMESPACE_URL, f"retention:{scenario.scenario_id}"))
                ),
                True,
                at,
                at,
                at,
                None,
                None,
            ),
            occurred_at=at,
            evidence_references=(
                EvidenceReference(
                    EvidenceId.from_string(
                        str(uuid.uuid5(uuid.NAMESPACE_URL, f"evidence:{source_uri}"))
                    ),
                    SourceId.from_string(
                        str(uuid.uuid5(uuid.NAMESPACE_URL, f"source:{source_uri}"))
                    ),
                    EvidenceSourceType.AGENT_EVENT,
                    SourceTrustClass.APPROVED_CHECKPOINT,
                    source_uri,
                    f"sha256:{digest}",
                    EvidenceLocation(source_uri),
                    at,
                    VerificationStatus.VERIFIED,
                ),
            ),
        )
        events.append(event)
        keys[str(event.event_id)] = spec.event_key
    return tuple(events), keys


def _included_evidence_keys(
    text: str,
    aliases: tuple[tuple[str, object], ...],
    key_by_event_id: Mapping[str, str],
) -> tuple[str, ...]:
    alias_map = {alias: str(event_id) for alias, event_id in aliases}
    present_aliases = set(re.findall(r"(?:e=|evidence=)(E\d+)(?:,|\b)", text))
    return tuple(
        key_by_event_id[alias_map[alias]]
        for alias in sorted(present_aliases, key=lambda value: int(value[1:]))
        if alias in alias_map and alias_map[alias] in key_by_event_id
    )


def _elapsed_ms(started_ns: int) -> float:
    return (time.perf_counter_ns() - started_ns) / 1_000_000
