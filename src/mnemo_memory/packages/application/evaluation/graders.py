"""Condition-blind deterministic and configurable semantic graders."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .models import ContinuationGrade, GradeEvidence, GroundTruth

_LEAKAGE_MARKERS = (
    '"required_active_facts"',
    '"expected_continuation"',
    '"critical_facts"',
    '"forbidden_facts"',
    "viability-corpus-v1.json",
    "GROUND_TRUTH_ANSWER",
)


class SemanticContinuationGrader(Protocol):
    """Blind grader boundary: condition identity is intentionally absent."""

    grader_id: str

    def grade(
        self,
        *,
        context: str,
        evidence_event_keys: tuple[str, ...],
        ground_truth: GroundTruth,
        context_tokens: int,
        full_history_tokens: int,
        deterministic: bool,
        drift_resistance: float,
        omission_notice_valid: bool,
    ) -> ContinuationGrade: ...


@dataclass(frozen=True, slots=True)
class CallableSemanticContinuationGrader:
    """Adapter for an authorized external grader that returns the closed grade schema."""

    grader_id: str
    callback: Callable[..., ContinuationGrade]

    def grade(
        self,
        *,
        context: str,
        evidence_event_keys: tuple[str, ...],
        ground_truth: GroundTruth,
        context_tokens: int,
        full_history_tokens: int,
        deterministic: bool,
        drift_resistance: float,
        omission_notice_valid: bool,
    ) -> ContinuationGrade:
        result = self.callback(
            context=context,
            evidence_event_keys=evidence_event_keys,
            ground_truth=ground_truth,
            context_tokens=context_tokens,
            full_history_tokens=full_history_tokens,
            deterministic=deterministic,
            drift_resistance=drift_resistance,
            omission_notice_valid=omission_notice_valid,
        )
        if not isinstance(result, ContinuationGrade):
            raise TypeError("semantic grader returned an invalid result")
        return result


@dataclass(frozen=True, slots=True)
class DeterministicContinuationGrader:
    """Exact grader for integrity plus an explicitly labelled availability proxy."""

    grader_id: str = "mnemo-deterministic-continuation-v1"

    def grade(
        self,
        *,
        context: str,
        evidence_event_keys: tuple[str, ...],
        ground_truth: GroundTruth,
        context_tokens: int,
        full_history_tokens: int,
        deterministic: bool,
        drift_resistance: float,
        omission_notice_valid: bool,
    ) -> ContinuationGrade:
        leakage = tuple(marker for marker in _LEAKAGE_MARKERS if marker in context)
        required = _match(context, ground_truth.required_groups)
        constraints = _match(context, ground_truth.required_constraints)
        protected = _match(context, ground_truth.protected_spans)
        expected = _match(context, ground_truth.expected_continuation)
        failures = _match(context, ground_truth.failed_approaches)
        questions = _match(context, ground_truth.unresolved_questions)
        next_actions = _match(context, ground_truth.next_actions)
        temporal_score, temporal_missing = _temporal_score(context, ground_truth)
        evidence = set(evidence_event_keys)
        relevant = set(ground_truth.relevant_evidence)
        critical_evidence = set(ground_truth.critical_evidence)
        evidence_score = _ratio(len(evidence & relevant), len(relevant))
        precision = _ratio(len(evidence & relevant), len(evidence)) if evidence else 0.0
        recall = evidence_score
        retrieval_f1 = (
            0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
        )
        false_memories = tuple(value for value in ground_truth.forbidden_facts if value in context)
        false_memory_rate = _ratio(len(false_memories), len(ground_truth.forbidden_facts))
        critical_missing = tuple(
            value for value in ground_truth.critical_facts if value not in context
        )
        critical_evidence_missing = tuple(sorted(critical_evidence - evidence))
        critical_violations: list[str] = []
        if constraints.missing:
            critical_violations.append("active_hard_constraint_omitted")
        if critical_missing:
            critical_violations.append("critical_fact_omitted")
        if temporal_missing:
            critical_violations.append("supersession_or_active_decision_error")
        if critical_evidence_missing:
            critical_violations.append("critical_evidence_association_broken")
        if false_memories:
            critical_violations.append("fabricated_or_cross_scope_fact")
        if leakage:
            critical_violations.append("evaluation_answer_leakage")
        failure_categories: list[str] = []
        if protected.score < 1.0:
            failure_categories.append("protected_span_loss")
        if not deterministic:
            failure_categories.append("nondeterministic_output")
        if drift_resistance < 1.0:
            failure_categories.append("checkpoint_drift")
        if not omission_notice_valid:
            failure_categories.append("invalid_or_silent_omission")
        if failures.score < 1.0:
            failure_categories.append("failed_approach_missing")
        failure_categories.extend(critical_violations)
        continuation = _mean(
            (
                required.score,
                expected.score,
                failures.score,
                questions.score,
                next_actions.score,
                temporal_score,
            )
        )
        task_success = 1.0 if not critical_violations and expected.score == 1.0 else continuation
        resume_speed = (
            0.0
            if full_history_tokens <= 0
            else max(0.0, min(1.0, 1.0 - context_tokens / full_history_tokens))
        )
        return ContinuationGrade(
            continuation_fidelity=continuation,
            required_knowledge_retention=required.score,
            temporal_supersession_accuracy=temporal_score,
            evidence_attribution_fidelity=evidence_score,
            drift_resistance=drift_resistance,
            retrieval_f1=retrieval_f1,
            false_memory_rate=false_memory_rate,
            constraint_retention=constraints.score,
            protected_span_fidelity=protected.score,
            task_success_proxy=task_success,
            resume_speed_proxy=resume_speed,
            avoided_rework_proxy=failures.score,
            human_intervention_proxy=_mean((questions.score, next_actions.score)),
            critical_violations=tuple(dict.fromkeys(critical_violations)),
            failure_categories=tuple(dict.fromkeys(failure_categories)),
            evidence=(
                GradeEvidence(
                    "required_knowledge", required.matched, required.missing, "exact groups"
                ),
                GradeEvidence(
                    "protected_spans", protected.matched, protected.missing, "byte equality"
                ),
                GradeEvidence(
                    "expected_continuation",
                    expected.matched,
                    expected.missing,
                    "availability proxy",
                ),
                GradeEvidence(
                    "critical_evidence",
                    tuple(sorted(critical_evidence & evidence)),
                    critical_evidence_missing,
                    "source-event association",
                ),
                GradeEvidence(
                    "false_memory",
                    (),
                    false_memories,
                    "forbidden fact probe; missing lists detected false facts",
                ),
                GradeEvidence(
                    "evaluation_leakage",
                    (),
                    leakage,
                    "ground-truth field and filename probes",
                ),
            ),
            human_review_required=True,
        )


@dataclass(frozen=True, slots=True)
class _Matches:
    score: float
    matched: tuple[str, ...]
    missing: tuple[str, ...]


def _match(context: str, expected: tuple[str, ...]) -> _Matches:
    matched = tuple(value for value in expected if value in context)
    missing = tuple(value for value in expected if value not in context)
    return _Matches(_ratio(len(matched), len(expected)), matched, missing)


def _temporal_score(context: str, truth: GroundTruth) -> tuple[float, tuple[str, ...]]:
    failures: list[str] = []
    for current in truth.current_decisions:
        if current not in context:
            failures.append(current)
    for superseded in truth.superseded_decisions:
        if superseded not in context:
            continue
        current_positions = [context.rfind(current) for current in truth.current_decisions]
        if not current_positions or max(current_positions) < context.rfind(superseded):
            failures.append(superseded)
    total = len(truth.current_decisions) + len(truth.superseded_decisions)
    return (1.0 if total == 0 else 1.0 - len(failures) / total), tuple(failures)


def _ratio(numerator: int, denominator: int) -> float:
    return 1.0 if denominator == 0 else numerator / denominator


def _mean(values: tuple[float, ...]) -> float:
    return sum(values) / len(values) if values else 1.0
