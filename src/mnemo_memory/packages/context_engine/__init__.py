"""Authorization-first context planning and retrieval."""

from .engine import (
    DeterministicContextPlanner,
    QueryIntent,
    RetrievalCategory,
    RetrievalPlan,
    UnifiedContextEngine,
)
from .explanation import ContextExplanation, explain_context_packet
from .rendering import ContextClient, render_context_packet
from .selection import finalize_context_packet

__all__ = [
    "ContextClient",
    "ContextExplanation",
    "DeterministicContextPlanner",
    "QueryIntent",
    "RetrievalCategory",
    "RetrievalPlan",
    "UnifiedContextEngine",
    "explain_context_packet",
    "finalize_context_packet",
    "render_context_packet",
]
