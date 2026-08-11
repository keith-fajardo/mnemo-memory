"""Deterministic semantic-checkpoint evaluation metrics for local and external runs."""

from __future__ import annotations

from dataclasses import dataclass

from .semantic_rendering import CheckpointTokenCounter, RenderedSemanticCheckpoint


@dataclass(frozen=True, slots=True)
class SemanticEvaluationExpectation:
    required_fact_groups: tuple[str, ...]
    protected_spans: tuple[str, ...] = ()
    constraint_groups: tuple[str, ...] = ()
    decision_rationale_groups: tuple[str, ...] = ()
    temporal_groups: tuple[str, ...] = ()
    forbidden_inversions: tuple[str, ...] = ()
    superseded_groups: tuple[str, ...] = ()
    forbidden_false_memories: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SemanticCheckpointEvaluation:
    full_history_tokens: tuple[tuple[str, int], ...]
    compact_tokens: tuple[tuple[str, int], ...]
    portable_tokens: tuple[tuple[str, int], ...]
    compression_ratio: tuple[tuple[str, float], ...]
    continuation_fidelity: float
    portable_continuation_fidelity: float
    protected_span_fidelity: float
    meaning_inversion_count: int
    required_fact_group_retention: float
    critical_omission_count: int
    false_memory_count: int
    constraint_retention: float
    decision_rationale_retention: float
    supersession_accuracy: float
    temporal_accuracy: float
    provenance_coverage: float
    determinism: bool
    drift_cycle_count: int
    fresh_session_task_success: bool
    held_out_case_count: int
    external_model_count: int
    production_ready: bool


def evaluate_semantic_checkpoint(
    *,
    full_history: str,
    compact: RenderedSemanticCheckpoint,
    portable: RenderedSemanticCheckpoint,
    repeated_compact_texts: tuple[str, ...],
    expectation: SemanticEvaluationExpectation,
    tokenizers: tuple[CheckpointTokenCounter, ...],
    held_out_case_count: int,
    external_model_count: int = 0,
) -> SemanticCheckpointEvaluation:
    """Compare history and renderings without an evaluator-model dependency."""

    if not tokenizers:
        raise ValueError("semantic evaluation requires at least one tokenizer")
    if held_out_case_count < 1 or external_model_count < 0:
        raise ValueError("semantic evaluation corpus metadata is invalid")
    history_counts = tuple(
        (tokenizer.tokenizer_id, tokenizer.count(full_history)) for tokenizer in tokenizers
    )
    compact_counts = tuple(
        (tokenizer.tokenizer_id, tokenizer.count(compact.text)) for tokenizer in tokenizers
    )
    portable_counts = tuple(
        (tokenizer.tokenizer_id, tokenizer.count(portable.text)) for tokenizer in tokenizers
    )
    compression = tuple(
        (
            tokenizer_id,
            0.0 if compact_tokens == 0 else history_tokens / compact_tokens,
        )
        for (tokenizer_id, history_tokens), (_, compact_tokens) in zip(
            history_counts, compact_counts, strict=True
        )
    )
    required = expectation.required_fact_groups
    compact_required = _retention(compact.text, required)
    portable_required = _retention(portable.text, required)
    protected = _retention(compact.text, expectation.protected_spans)
    constraints = _retention(compact.text, expectation.constraint_groups)
    decisions = _retention(compact.text, expectation.decision_rationale_groups)
    temporal = _retention(compact.text, expectation.temporal_groups)
    inversions = _present_count(compact.text, expectation.forbidden_inversions)
    superseded = _present_count(compact.text, expectation.superseded_groups)
    false_memories = _present_count(compact.text, expectation.forbidden_false_memories)
    critical_omissions = len(required) - sum(fragment in compact.text for fragment in required)
    content_lines = tuple(
        line for line in compact.text.splitlines()[1:] if line and not line.startswith("OMISSION ")
    )
    cited_lines = sum("e=" in line or "evidence=" in line for line in content_lines)
    provenance = 1.0 if not content_lines else cited_lines / len(content_lines)
    determinism = bool(repeated_compact_texts) and len(set(repeated_compact_texts)) == 1
    drift_cycles = max(0, len(set(repeated_compact_texts)) - 1)
    fresh_success = (
        compact_required == 1.0 and inversions == 0 and superseded == 0 and false_memories == 0
    )
    production_ready = (
        held_out_case_count >= 50
        and external_model_count >= 2
        and fresh_success
        and protected == 1.0
        and constraints == 1.0
        and decisions == 1.0
        and temporal == 1.0
        and provenance == 1.0
        and determinism
    )
    return SemanticCheckpointEvaluation(
        history_counts,
        compact_counts,
        portable_counts,
        compression,
        compact_required,
        portable_required,
        protected,
        inversions,
        compact_required,
        critical_omissions,
        false_memories,
        constraints,
        decisions,
        1.0 if superseded == 0 else 0.0,
        temporal,
        provenance,
        determinism,
        drift_cycles,
        fresh_success,
        held_out_case_count,
        external_model_count,
        production_ready,
    )


def _retention(text: str, fragments: tuple[str, ...]) -> float:
    return (
        1.0 if not fragments else sum(fragment in text for fragment in fragments) / len(fragments)
    )


def _present_count(text: str, fragments: tuple[str, ...]) -> int:
    return sum(fragment in text for fragment in fragments)
