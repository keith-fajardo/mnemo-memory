"""Paired offline experiment runner with fail-closed external-call budgets."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

from mnemo_memory.packages.application.semantic_rendering import (
    CheckpointTokenCounter,
    ConservativeTokenCounter,
)

from .analysis import long_term_memory_efficiency, task_impact
from .conditions import ConditionAdapter, build_condition_adapters, render_usable_history
from .graders import DeterministicContinuationGrader, SemanticContinuationGrader
from .models import (
    ConditionId,
    ConditionOutput,
    EvaluationBudget,
    EvaluationConfig,
    EvaluationRun,
    EventSpec,
    MeasurementSource,
    Scenario,
    ScenarioCorpus,
    TokenAccount,
)

_TRACE_SECRET = re.compile(
    r"(?:AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"(?i:(?:api[_-]?key|password|secret)\s*[:=]\s*\S{8,}))"
)


class EvaluationBudgetExceeded(RuntimeError):
    pass


@dataclass(slots=True)
class EvaluationBudgetLedger:
    budget: EvaluationBudget
    external_calls: int = 0
    reserved_cost_usd: float = 0.0

    def reserve(self, *, expected_cost_usd: float) -> None:
        if expected_cost_usd < 0:
            raise ValueError("expected external-call cost cannot be negative")
        if not self.budget.authorizes_live_calls:
            raise EvaluationBudgetExceeded("live evaluation has no explicit nonzero budget")
        if expected_cost_usd > self.budget.maximum_run_cost_usd:
            raise EvaluationBudgetExceeded("external call exceeds the per-run cost budget")
        if self.external_calls + 1 > self.budget.maximum_external_calls:
            raise EvaluationBudgetExceeded("external call count budget exhausted")
        if self.reserved_cost_usd + expected_cost_usd > self.budget.maximum_suite_cost_usd:
            raise EvaluationBudgetExceeded("external suite cost budget exhausted")
        self.external_calls += 1
        self.reserved_cost_usd += expected_cost_usd


@dataclass(slots=True)
class EvaluationRunner:
    config: EvaluationConfig
    corpus: ScenarioCorpus
    adapters: tuple[ConditionAdapter, ...]
    grader: SemanticContinuationGrader
    tokenizer: CheckpointTokenCounter
    budget_ledger: EvaluationBudgetLedger

    @classmethod
    def offline(
        cls,
        config: EvaluationConfig,
        corpus: ScenarioCorpus,
        *,
        tokenizer: CheckpointTokenCounter | None = None,
        grader: SemanticContinuationGrader | None = None,
    ) -> EvaluationRunner:
        counter = tokenizer or ConservativeTokenCounter()
        selected_grader = cast(
            SemanticContinuationGrader,
            grader if grader is not None else DeterministicContinuationGrader(),
        )
        return cls(
            config,
            corpus,
            build_condition_adapters(config, counter),
            selected_grader,
            counter,
            EvaluationBudgetLedger(config.budget),
        )

    @property
    def fairness_control_digest(self) -> str:
        value = {
            "seed": self.config.seed,
            "tokenizer": self.tokenizer.tokenizer_id,
            "grader": self.grader.grader_id,
            "reuse_counts": self.config.reuse_counts,
            "system_instructions": "identical synthetic fresh-session continuation rubric v1",
            "tools": "none for offline condition-availability grading",
            "reasoning": "not applicable; no model call",
            "temperature": "not applicable; no model call",
        }
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"

    def run(self, evaluation_run_id: str) -> tuple[EvaluationRun, ...]:
        if not evaluation_run_id.strip():
            raise ValueError("evaluation run ID must not be blank")
        runs: list[EvaluationRun] = []
        for scenario_index, scenario in enumerate(self.corpus.scenarios):
            full_history_tokens = self.tokenizer.count(render_usable_history(scenario))
            cached_outputs: dict[ConditionId, ConditionOutput | str] = {}
            for adapter in self.adapters:
                try:
                    cached_outputs[adapter.condition_id] = adapter.evaluate(scenario, 1)
                except (OSError, RuntimeError, TypeError, ValueError) as error:
                    cached_outputs[adapter.condition_id] = type(error).__name__
            for reuse_count in self.config.reuse_counts:
                pair_id = f"{scenario.scenario_id}:reuse-{reuse_count}"
                ordered = self._counterbalanced_adapters(scenario_index, reuse_count)
                for order_index, adapter in enumerate(ordered):
                    cached = cached_outputs[adapter.condition_id]
                    if isinstance(cached, str):
                        runs.append(
                            self._failure_run(
                                evaluation_run_id,
                                pair_id,
                                order_index,
                                scenario,
                                reuse_count,
                                adapter.condition_id,
                                cached,
                            )
                        )
                        continue
                    output = _scale_output(cached, reuse_count)
                    runs.append(
                        self._grade_output(
                            evaluation_run_id,
                            pair_id,
                            order_index,
                            scenario,
                            reuse_count,
                            output,
                            full_history_tokens,
                        )
                    )
        return tuple(runs)

    def _failure_run(
        self,
        evaluation_run_id: str,
        pair_id: str,
        order_index: int,
        scenario: Scenario,
        reuse_count: int,
        condition: ConditionId,
        failure_name: str,
    ) -> EvaluationRun:
        return EvaluationRun(
            run_id=f"{evaluation_run_id}:{pair_id}:{condition.value}",
            pair_id=pair_id,
            order_index=order_index,
            scenario_id=scenario.scenario_id,
            template_id=scenario.template_id,
            category=scenario.category,
            horizon=scenario.horizon,
            reuse_count=reuse_count,
            condition=condition,
            available=False,
            unavailable_reason="condition adapter failed",
            execution_failure=failure_name,
            task_prompt=scenario.task_prompt,
            context=None,
            token_account=_empty_token_account(self.tokenizer.tokenizer_id),
            context_tokens_per_reuse=0,
            save_tokens=0,
            validation_tokens=0,
            latency_ms=0.0,
            grade=None,
            lme_gated=None,
            lme_ungated=None,
            task_impact_proxy=None,
            provider=None,
            model=None,
            estimated_cost_usd=None,
            provider_reported_cost_usd=None,
        )

    def _grade_output(
        self,
        evaluation_run_id: str,
        pair_id: str,
        order_index: int,
        scenario: Scenario,
        reuse_count: int,
        output: ConditionOutput,
        full_history_tokens: int,
    ) -> EvaluationRun:
        if not output.available:
            return EvaluationRun(
                run_id=f"{evaluation_run_id}:{pair_id}:{output.condition.value}",
                pair_id=pair_id,
                order_index=order_index,
                scenario_id=scenario.scenario_id,
                template_id=scenario.template_id,
                category=scenario.category,
                horizon=scenario.horizon,
                reuse_count=reuse_count,
                condition=output.condition,
                available=False,
                unavailable_reason=output.unavailable_reason,
                execution_failure=None,
                task_prompt=scenario.task_prompt,
                context=None,
                token_account=output.token_account,
                context_tokens_per_reuse=0,
                save_tokens=0,
                validation_tokens=0,
                latency_ms=output.latency_ms,
                grade=None,
                lme_gated=None,
                lme_ungated=None,
                task_impact_proxy=None,
                provider=output.provider,
                model=output.model,
                estimated_cost_usd=output.estimated_cost_usd,
                provider_reported_cost_usd=output.provider_reported_cost_usd,
            )
        grade = self.grader.grade(
            context=output.context,
            evidence_event_keys=output.evidence_event_keys,
            ground_truth=scenario.ground_truth,
            context_tokens=output.context_tokens_per_reuse,
            full_history_tokens=full_history_tokens,
            deterministic=output.deterministic,
            drift_resistance=output.drift_resistance,
            omission_notice_valid=output.omission_notice_valid,
        )
        repair_tokens = round(
            (1.0 - grade.task_success_proxy)
            * max(100, self.tokenizer.count(render_usable_history(scenario)) // 4)
            * reuse_count
        )
        account = replace(output.token_account, retry_and_repair=repair_tokens)
        lme_gated, lme_ungated = long_term_memory_efficiency(
            continuation_fidelity=grade.continuation_fidelity,
            required_knowledge_retention=grade.required_knowledge_retention,
            temporal_supersession_accuracy=grade.temporal_supersession_accuracy,
            evidence_attribution_fidelity=grade.evidence_attribution_fidelity,
            drift_resistance=grade.drift_resistance,
            retrieval_f1=grade.retrieval_f1,
            false_memory_rate=grade.false_memory_rate,
            critical_violation=bool(grade.critical_violations),
        )
        impact = task_impact(
            grade.task_success_proxy,
            grade.resume_speed_proxy,
            grade.avoided_rework_proxy,
            grade.human_intervention_proxy,
        )
        return EvaluationRun(
            run_id=f"{evaluation_run_id}:{pair_id}:{output.condition.value}",
            pair_id=pair_id,
            order_index=order_index,
            scenario_id=scenario.scenario_id,
            template_id=scenario.template_id,
            category=scenario.category,
            horizon=scenario.horizon,
            reuse_count=reuse_count,
            condition=output.condition,
            available=True,
            unavailable_reason=None,
            execution_failure=None,
            task_prompt=scenario.task_prompt,
            context=output.context,
            token_account=account,
            context_tokens_per_reuse=output.context_tokens_per_reuse,
            save_tokens=output.save_tokens,
            validation_tokens=output.validation_tokens,
            latency_ms=output.latency_ms,
            grade=grade,
            lme_gated=lme_gated,
            lme_ungated=lme_ungated,
            task_impact_proxy=impact,
            provider=output.provider,
            model=output.model,
            estimated_cost_usd=output.estimated_cost_usd,
            provider_reported_cost_usd=output.provider_reported_cost_usd,
        )

    def _counterbalanced_adapters(
        self, scenario_index: int, reuse_count: int
    ) -> tuple[ConditionAdapter, ...]:
        offset = (self.config.seed + scenario_index + reuse_count) % len(self.adapters)
        return (*self.adapters[offset:], *self.adapters[:offset])


def import_anonymized_trace(path: Path) -> tuple[str, tuple[EventSpec, ...]]:
    """Load a future real trace only after explicit anonymization metadata and secret checks."""

    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) < 2:
        raise ValueError("anonymized trace requires metadata and at least one event")
    if _TRACE_SECRET.search("\n".join(lines)):
        raise ValueError("anonymized trace appears to contain prohibited secret material")
    metadata = cast(dict[str, Any], json.loads(lines[0]))
    if set(metadata) != {"schema_version", "trace_id", "anonymized"}:
        raise ValueError("anonymized trace metadata fields are invalid")
    if (
        metadata["schema_version"] != "mnemo-anonymized-trace/1.0"
        or metadata["anonymized"] is not True
    ):
        raise ValueError("trace must affirm the supported anonymization contract")
    events: list[EventSpec] = []
    for index, line in enumerate(lines[1:]):
        raw = cast(dict[str, Any], json.loads(line))
        if set(raw) != {"source_event_key", "actor", "summary"}:
            raise ValueError("anonymized trace event fields are invalid")
        events.append(
            EventSpec(
                str(raw["source_event_key"]),
                str(raw["actor"]),
                str(raw["summary"]),
                index / max(1, len(lines) - 2),
            )
        )
    return str(metadata["trace_id"]), tuple(events)


def _empty_token_account(tokenizer_id: str) -> TokenAccount:
    return TokenAccount(source=MeasurementSource.NOT_AVAILABLE, tokenizer_id=tokenizer_id)


def _scale_output(output: ConditionOutput, reuse_count: int) -> ConditionOutput:
    if reuse_count < 1:
        raise ValueError("reuse count must be positive")
    if reuse_count == 1 or not output.available:
        return output
    account = output.token_account
    scaled = replace(
        account,
        agent_work_input=account.agent_work_input * reuse_count,
        agent_work_output=account.agent_work_output * reuse_count,
        baseline_context=account.baseline_context * reuse_count,
        checkpoint_recall=account.checkpoint_recall * reuse_count,
        retrieval_query=account.retrieval_query * reuse_count,
        retrieved_evidence=account.retrieved_evidence * reuse_count,
    )
    return replace(output, token_account=scaled)
