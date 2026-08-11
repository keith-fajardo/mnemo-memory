"""Authorization-first context planning and retrieval."""

from mnemo_memory.packages.application.semantic_evaluation import (
    SemanticCheckpointEvaluation,
    SemanticEvaluationExpectation,
    evaluate_semantic_checkpoint,
)
from mnemo_memory.packages.application.semantic_rendering import (
    DEFAULT_MAXIMUM_TOKENS,
    DEFAULT_PREFERRED_TOKENS,
    PHRASE_TABLE_VERSION,
    CallableTokenCounter,
    CheckpointTokenCounter,
    ConservativeTokenCounter,
    ProtectedSpan,
    RenderedSemanticCheckpoint,
    SemanticOmissionNotice,
    detect_protected_spans,
    measure_checkpoint_tokens,
    reduce_checkpoint_phrases,
    render_semantic_checkpoint,
)

from .engine import (
    DeterministicContextPlanner,
    QueryIntent,
    RetrievalCategory,
    RetrievalPlan,
    UnifiedContextEngine,
)
from .explanation import ContextExplanation, explain_context_packet
from .rendering import ContextClient, render_automatic_context_packet, render_context_packet
from .selection import finalize_context_packet

__all__ = [
    "DEFAULT_MAXIMUM_TOKENS",
    "DEFAULT_PREFERRED_TOKENS",
    "PHRASE_TABLE_VERSION",
    "CallableTokenCounter",
    "CheckpointTokenCounter",
    "ConservativeTokenCounter",
    "ContextClient",
    "ContextExplanation",
    "DeterministicContextPlanner",
    "ProtectedSpan",
    "QueryIntent",
    "RenderedSemanticCheckpoint",
    "RetrievalCategory",
    "RetrievalPlan",
    "SemanticCheckpointEvaluation",
    "SemanticEvaluationExpectation",
    "SemanticOmissionNotice",
    "UnifiedContextEngine",
    "detect_protected_spans",
    "evaluate_semantic_checkpoint",
    "explain_context_packet",
    "finalize_context_packet",
    "measure_checkpoint_tokens",
    "reduce_checkpoint_phrases",
    "render_automatic_context_packet",
    "render_context_packet",
    "render_semantic_checkpoint",
]
