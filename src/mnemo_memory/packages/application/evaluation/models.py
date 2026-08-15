"""Versioned contracts for paired Mnemo viability evaluations."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, cast


class Horizon(StrEnum):
    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"


class ConditionId(StrEnum):
    FULL_HISTORY = "B0_full_history"
    SLIDING_WINDOW = "B1_sliding_window"
    ROLLING_SUMMARY = "B2_rolling_summary"
    PROVIDER_NATIVE = "B3_provider_native"
    MNEMO_COMPACT = "M1_mnemo_compact_200"
    MNEMO_ADAPTIVE = "M2_mnemo_adaptive_600"
    MNEMO_RETRIEVAL = "M3_mnemo_adaptive_retrieval"


class MeasurementSource(StrEnum):
    PROVIDER_REPORTED = "provider_reported"
    TOKENIZER_ESTIMATE = "tokenizer_estimate"
    OFFLINE_PROXY = "offline_proxy"
    NOT_AVAILABLE = "not_available"


class MetricClassification(StrEnum):
    """Closed evidence labels used by reports and raw metric catalogs."""

    ACTUALLY_OBSERVED = "Actually observed"
    DETERMINISTICALLY_MEASURED = "Deterministically measured"
    MODEL_GENERATED = "Model-generated"
    ESTIMATED = "Estimated"
    PROXY = "Proxy"
    SIMULATED = "Simulated"
    NOT_EVALUATED = "Not evaluated"


class ThresholdStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_EVALUATED = "NOT EVALUATED"


@dataclass(frozen=True, slots=True)
class EventSpec:
    event_key: str
    actor: str
    summary: str
    position: float

    def __post_init__(self) -> None:
        if not self.event_key or not self.actor or not self.summary:
            raise ValueError("scenario event fields must not be blank")
        if not 0.0 <= self.position <= 1.0:
            raise ValueError("scenario event position must be between zero and one")


@dataclass(frozen=True, slots=True)
class GroundTruth:
    required_active_facts: tuple[str, ...]
    required_constraints: tuple[str, ...]
    current_goals: tuple[str, ...]
    current_decisions: tuple[str, ...]
    superseded_decisions: tuple[str, ...]
    failed_approaches: tuple[str, ...]
    unresolved_questions: tuple[str, ...]
    next_actions: tuple[str, ...]
    protected_spans: tuple[str, ...]
    relevant_evidence: tuple[str, ...]
    critical_evidence: tuple[str, ...]
    irrelevant_or_obsolete: tuple[str, ...]
    expected_continuation: tuple[str, ...]
    critical_facts: tuple[str, ...]
    forbidden_facts: tuple[str, ...]
    retrieval_required: tuple[str, ...]

    @property
    def required_groups(self) -> tuple[str, ...]:
        return _unique(
            self.required_active_facts
            + self.required_constraints
            + self.current_goals
            + self.current_decisions
            + self.unresolved_questions
            + self.next_actions
        )


@dataclass(frozen=True, slots=True)
class Scenario:
    scenario_id: str
    template_id: str
    title: str
    category: str
    horizon: Horizon
    realistic: bool
    challenge_tags: tuple[str, ...]
    task_prompt: str
    events: tuple[EventSpec, ...]
    ground_truth: GroundTruth
    target_event_count: int

    def __post_init__(self) -> None:
        if len(self.events) != self.target_event_count:
            raise ValueError("materialized scenario event count does not match its horizon")
        if not self.ground_truth.critical_facts:
            raise ValueError("every scenario requires at least one critical fact")


@dataclass(frozen=True, slots=True)
class ScenarioCorpus:
    schema_version: str
    corpus_version: str
    provenance: str
    scenarios: tuple[Scenario, ...]


@dataclass(frozen=True, slots=True)
class EvaluationBudget:
    maximum_external_calls: int
    maximum_suite_cost_usd: float
    maximum_run_cost_usd: float

    def __post_init__(self) -> None:
        if self.maximum_external_calls < 0:
            raise ValueError("external-call budget cannot be negative")
        if self.maximum_suite_cost_usd < 0 or self.maximum_run_cost_usd < 0:
            raise ValueError("evaluation cost budget cannot be negative")

    @property
    def authorizes_live_calls(self) -> bool:
        return self.maximum_external_calls > 0 and self.maximum_suite_cost_usd > 0


@dataclass(frozen=True, slots=True)
class EconomicAssumption:
    name: str
    annual_eligible_runs: int
    input_cost_per_million: float
    output_cost_per_million: float
    annual_human_hours_saved: float
    human_hour_value: float
    annual_failures_avoided: float
    avoided_failure_value: float
    annual_development_cost: float
    annual_infrastructure_cost: float


@dataclass(frozen=True, slots=True)
class EvaluationConfig:
    schema_version: str
    seed: int
    reuse_counts: tuple[int, ...]
    sliding_window_tokens: int
    rolling_summary_tokens: int
    compact_tokens: int
    adaptive_tokens: int
    retrieval_tokens: int
    drift_cycles: int
    bootstrap_samples: int
    noninferiority_margin: float
    token_counter_id: str
    live_evaluation_enabled: bool
    provider_native_configured: bool
    model_grader_configured: bool
    budget: EvaluationBudget
    economic_assumptions: tuple[EconomicAssumption, ...]

    def __post_init__(self) -> None:
        if not self.reuse_counts or any(value < 1 for value in self.reuse_counts):
            raise ValueError("reuse counts must be positive")
        if tuple(sorted(set(self.reuse_counts))) != self.reuse_counts:
            raise ValueError("reuse counts must be unique and sorted")
        if not (
            1 <= self.compact_tokens <= self.adaptive_tokens
            and self.sliding_window_tokens >= 1
            and self.rolling_summary_tokens >= 1
            and self.retrieval_tokens >= 1
        ):
            raise ValueError("evaluation token budgets are invalid")
        if self.drift_cycles < 2 or self.bootstrap_samples < 100:
            raise ValueError("evaluation repetition settings are too small")
        if not 0.0 <= self.noninferiority_margin <= 1.0:
            raise ValueError("noninferiority margin is invalid")
        if self.live_evaluation_enabled and not self.budget.authorizes_live_calls:
            raise ValueError("live evaluation requires an explicit nonzero budget")


@dataclass(frozen=True, slots=True)
class TokenAccount:
    agent_work_input: int = 0
    agent_work_output: int = 0
    baseline_context: int = 0
    baseline_compaction_input: int = 0
    baseline_compaction_output: int = 0
    checkpoint_save_input: int = 0
    checkpoint_save_output: int = 0
    validation: int = 0
    checkpoint_recall: int = 0
    retrieval_query: int = 0
    retrieved_evidence: int = 0
    retry_and_repair: int = 0
    cached: int = 0
    reasoning: int = 0
    source: MeasurementSource = MeasurementSource.TOKENIZER_ESTIMATE
    tokenizer_id: str = "mnemo/conservative-lexical-v1"

    def __post_init__(self) -> None:
        values = self.to_dict(include_metadata=False).values()
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in values
        ):
            raise ValueError("token-account values must be non-negative integers")

    @property
    def total(self) -> int:
        return sum(cast(int, value) for value in self.to_dict(include_metadata=False).values())

    @property
    def input_total(self) -> int:
        return (
            self.agent_work_input
            + self.baseline_context
            + self.baseline_compaction_input
            + self.checkpoint_save_input
            + self.validation
            + self.checkpoint_recall
            + self.retrieval_query
            + self.retrieved_evidence
            + self.retry_and_repair
        )

    @property
    def output_total(self) -> int:
        return (
            self.agent_work_output + self.baseline_compaction_output + self.checkpoint_save_output
        )

    def to_dict(self, *, include_metadata: bool = True) -> dict[str, object]:
        result: dict[str, object] = {
            "agent_work_input": self.agent_work_input,
            "agent_work_output": self.agent_work_output,
            "baseline_context": self.baseline_context,
            "baseline_compaction_input": self.baseline_compaction_input,
            "baseline_compaction_output": self.baseline_compaction_output,
            "checkpoint_save_input": self.checkpoint_save_input,
            "checkpoint_save_output": self.checkpoint_save_output,
            "validation": self.validation,
            "checkpoint_recall": self.checkpoint_recall,
            "retrieval_query": self.retrieval_query,
            "retrieved_evidence": self.retrieved_evidence,
            "retry_and_repair": self.retry_and_repair,
            "cached": self.cached,
            "reasoning": self.reasoning,
        }
        if include_metadata:
            result.update(
                {
                    "total": self.total,
                    "input_total": self.input_total,
                    "output_total": self.output_total,
                    "source": self.source.value,
                    "tokenizer_id": self.tokenizer_id,
                }
            )
        return result


@dataclass(frozen=True, slots=True)
class ConditionOutput:
    condition: ConditionId
    available: bool
    unavailable_reason: str | None
    context: str
    token_account: TokenAccount
    context_tokens_per_reuse: int
    save_tokens: int
    validation_tokens: int
    latency_ms: float
    evidence_event_keys: tuple[str, ...]
    omission_notice_valid: bool
    deterministic: bool
    drift_resistance: float
    provider: str | None = None
    model: str | None = None
    estimated_cost_usd: float | None = None
    provider_reported_cost_usd: float | None = None


@dataclass(frozen=True, slots=True)
class GradeEvidence:
    metric: str
    matched: tuple[str, ...]
    missing: tuple[str, ...]
    explanation: str


@dataclass(frozen=True, slots=True)
class ContinuationGrade:
    continuation_fidelity: float
    required_knowledge_retention: float
    temporal_supersession_accuracy: float
    evidence_attribution_fidelity: float
    drift_resistance: float
    retrieval_f1: float
    false_memory_rate: float
    constraint_retention: float
    protected_span_fidelity: float
    task_success_proxy: float
    resume_speed_proxy: float
    avoided_rework_proxy: float
    human_intervention_proxy: float
    critical_violations: tuple[str, ...]
    failure_categories: tuple[str, ...]
    evidence: tuple[GradeEvidence, ...]
    human_review_required: bool


@dataclass(frozen=True, slots=True)
class EvaluationRun:
    run_id: str
    pair_id: str
    order_index: int
    scenario_id: str
    template_id: str
    category: str
    horizon: Horizon
    reuse_count: int
    condition: ConditionId
    available: bool
    unavailable_reason: str | None
    execution_failure: str | None
    task_prompt: str
    context: str | None
    token_account: TokenAccount
    context_tokens_per_reuse: int
    save_tokens: int
    validation_tokens: int
    latency_ms: float
    grade: ContinuationGrade | None
    lme_gated: float | None
    lme_ungated: float | None
    task_impact_proxy: float | None
    provider: str | None
    model: str | None
    estimated_cost_usd: float | None
    provider_reported_cost_usd: float | None


_SECRET_PATTERN = re.compile(
    r"(?:AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"(?i:(?:api[_-]?key|password|secret|token)\s*[:=]\s*[^<][^\s,]{8,}))"
)


def load_corpus(path: Path) -> ScenarioCorpus:
    raw = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    _require_keys(raw, {"schema_version", "corpus_version", "provenance", "templates"}, "corpus")
    if _SECRET_PATTERN.search(path.read_text(encoding="utf-8")):
        raise ValueError("evaluation corpus appears to contain a secret")
    scenarios: list[Scenario] = []
    templates = cast(list[dict[str, Any]], raw["templates"])
    for template in templates:
        scenarios.extend(_load_template(template))
    if not scenarios:
        raise ValueError("evaluation corpus must contain scenarios")
    return ScenarioCorpus(
        str(raw["schema_version"]),
        str(raw["corpus_version"]),
        str(raw["provenance"]),
        tuple(scenarios),
    )


def load_evaluation_config(path: Path) -> EvaluationConfig:
    raw = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    _require_keys(
        raw,
        {
            "schema_version",
            "seed",
            "reuse_counts",
            "sliding_window_tokens",
            "rolling_summary_tokens",
            "compact_tokens",
            "adaptive_tokens",
            "retrieval_tokens",
            "drift_cycles",
            "bootstrap_samples",
            "noninferiority_margin",
            "token_counter_id",
            "live_evaluation_enabled",
            "provider_native_configured",
            "model_grader_configured",
            "external_budget",
            "economic_assumptions",
        },
        "evaluation config",
    )
    budget = cast(dict[str, Any], raw["external_budget"])
    assumptions = tuple(
        EconomicAssumption(
            name=str(item["name"]),
            annual_eligible_runs=int(item["annual_eligible_runs"]),
            input_cost_per_million=float(item["input_cost_per_million"]),
            output_cost_per_million=float(item["output_cost_per_million"]),
            annual_human_hours_saved=float(item["annual_human_hours_saved"]),
            human_hour_value=float(item["human_hour_value"]),
            annual_failures_avoided=float(item["annual_failures_avoided"]),
            avoided_failure_value=float(item["avoided_failure_value"]),
            annual_development_cost=float(item["annual_development_cost"]),
            annual_infrastructure_cost=float(item["annual_infrastructure_cost"]),
        )
        for item in cast(list[dict[str, Any]], raw["economic_assumptions"])
    )
    return EvaluationConfig(
        schema_version=str(raw["schema_version"]),
        seed=int(raw["seed"]),
        reuse_counts=tuple(int(value) for value in cast(list[int], raw["reuse_counts"])),
        sliding_window_tokens=int(raw["sliding_window_tokens"]),
        rolling_summary_tokens=int(raw["rolling_summary_tokens"]),
        compact_tokens=int(raw["compact_tokens"]),
        adaptive_tokens=int(raw["adaptive_tokens"]),
        retrieval_tokens=int(raw["retrieval_tokens"]),
        drift_cycles=int(raw["drift_cycles"]),
        bootstrap_samples=int(raw["bootstrap_samples"]),
        noninferiority_margin=float(raw["noninferiority_margin"]),
        token_counter_id=str(raw["token_counter_id"]),
        live_evaluation_enabled=bool(raw["live_evaluation_enabled"]),
        provider_native_configured=bool(raw["provider_native_configured"]),
        model_grader_configured=bool(raw["model_grader_configured"]),
        budget=EvaluationBudget(
            maximum_external_calls=int(budget["maximum_external_calls"]),
            maximum_suite_cost_usd=float(budget["maximum_suite_cost_usd"]),
            maximum_run_cost_usd=float(budget["maximum_run_cost_usd"]),
        ),
        economic_assumptions=assumptions,
    )


def _load_template(raw: dict[str, Any]) -> list[Scenario]:
    required = {
        "template_id",
        "title",
        "category",
        "realistic",
        "challenge_tags",
        "task_prompt",
        "horizons",
        "events",
        "ground_truth",
    }
    _require_keys(raw, required, "scenario template")
    core_events = tuple(
        EventSpec(
            event_key=str(item["event_key"]),
            actor=str(item["actor"]),
            summary=str(item["summary"]),
            position=float(item["position"]),
        )
        for item in cast(list[dict[str, Any]], raw["events"])
    )
    truth_raw = cast(dict[str, Any], raw["ground_truth"])
    truth_fields = {
        "required_active_facts",
        "required_constraints",
        "current_goals",
        "current_decisions",
        "superseded_decisions",
        "failed_approaches",
        "unresolved_questions",
        "next_actions",
        "protected_spans",
        "relevant_evidence",
        "critical_evidence",
        "irrelevant_or_obsolete",
        "expected_continuation",
        "critical_facts",
        "forbidden_facts",
        "retrieval_required",
    }
    _require_keys(truth_raw, truth_fields, "scenario ground truth")
    truth = GroundTruth(
        **{
            field_name: tuple(str(value) for value in cast(list[object], truth_raw[field_name]))
            for field_name in truth_fields
        }
    )
    result: list[Scenario] = []
    horizons = cast(dict[str, int], raw["horizons"])
    for name, count in horizons.items():
        horizon = Horizon(name)
        events = _materialize_events(str(raw["template_id"]), core_events, int(count))
        result.append(
            Scenario(
                scenario_id=f"{raw['template_id']}:{horizon.value}",
                template_id=str(raw["template_id"]),
                title=str(raw["title"]),
                category=str(raw["category"]),
                horizon=horizon,
                realistic=bool(raw["realistic"]),
                challenge_tags=tuple(
                    str(value) for value in cast(list[object], raw["challenge_tags"])
                ),
                task_prompt=str(raw["task_prompt"]),
                events=events,
                ground_truth=truth,
                target_event_count=int(count),
            )
        )
    return result


def _materialize_events(
    template_id: str, core_events: tuple[EventSpec, ...], target_count: int
) -> tuple[EventSpec, ...]:
    if target_count < len(core_events):
        raise ValueError("scenario horizon is smaller than its required core event set")
    placed: dict[int, EventSpec] = {}
    for event in sorted(core_events, key=lambda item: (item.position, item.event_key)):
        slot = round(event.position * (target_count - 1))
        while slot in placed and slot < target_count - 1:
            slot += 1
        while slot in placed and slot > 0:
            slot -= 1
        if slot in placed:
            raise ValueError("scenario event positions cannot be materialized uniquely")
        placed[slot] = event
    result: list[EventSpec] = []
    for index in range(target_count):
        materialized_event = placed.get(index)
        if materialized_event is None:
            materialized_event = EventSpec(
                event_key=f"noise-{index:04d}",
                actor="agent",
                summary=(
                    f"Background conversation {index:04d} for synthetic workflow {template_id}; "
                    "it is unrelated and must not displace active task state."
                ),
                position=index / max(1, target_count - 1),
            )
        result.append(materialized_event)
    return tuple(result)


def _require_keys(value: dict[str, Any], keys: set[str], label: str) -> None:
    if set(value) != keys:
        missing = sorted(keys - set(value))
        extra = sorted(set(value) - keys)
        raise ValueError(f"{label} fields are invalid; missing={missing}, extra={extra}")


def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
