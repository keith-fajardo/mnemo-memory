"""Storage-independent, untrusted local-knowledge parsing contracts."""

from mnemo_memory.packages.domain import (
    KnowledgeDocument,
    KnowledgeDocumentLink,
    KnowledgeDocumentSection,
    KnowledgeDocumentSourceKind,
    KnownKnowledgeDocument,
)

from .markdown import (
    KnowledgeDocumentParseError,
    KnowledgeDocumentParseLimits,
    KnowledgeDocumentParser,
    KnowledgeDocumentParseRequest,
)
from .retrieval import (
    KnowledgeLexicalRetriever,
    KnowledgeRetrievalError,
    KnowledgeSearchMatch,
    KnowledgeSearchRequest,
    KnowledgeSearchResult,
)
from .sync import (
    KnowledgeSyncAction,
    KnowledgeSyncActionKind,
    KnowledgeSyncPlan,
    KnowledgeSyncPlanner,
)

__all__ = [
    "KnowledgeDocument",
    "KnowledgeDocumentLink",
    "KnowledgeDocumentParseError",
    "KnowledgeDocumentParseLimits",
    "KnowledgeDocumentParseRequest",
    "KnowledgeDocumentParser",
    "KnowledgeDocumentSection",
    "KnowledgeDocumentSourceKind",
    "KnowledgeLexicalRetriever",
    "KnowledgeRetrievalError",
    "KnowledgeSearchMatch",
    "KnowledgeSearchRequest",
    "KnowledgeSearchResult",
    "KnowledgeSyncAction",
    "KnowledgeSyncActionKind",
    "KnowledgeSyncPlan",
    "KnowledgeSyncPlanner",
    "KnownKnowledgeDocument",
]
