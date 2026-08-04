"""Storage-independent, untrusted local-knowledge parsing contracts."""

from mnemo_memory.packages.domain import (
    KnowledgeDocument,
    KnowledgeDocumentLink,
    KnowledgeDocumentSection,
    KnowledgeDocumentSourceKind,
    KnownKnowledgeDocument,
)

from .links import (
    KnowledgeLinkNavigationError,
    KnowledgeLinkNavigationRequest,
    KnowledgeLinkNavigationResult,
    KnowledgeLinkNavigator,
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
from .semantic import (
    LocalEmbeddingError,
    LocalEmbeddingProvider,
    LocalSemanticKnowledgeIndexer,
    LocalSemanticKnowledgeRetriever,
    SemanticKnowledgeIndexRequest,
    SemanticKnowledgeIndexResult,
    SemanticKnowledgeMatch,
    SemanticKnowledgeSearchRequest,
    SemanticKnowledgeSearchResult,
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
    "KnowledgeLinkNavigationError",
    "KnowledgeLinkNavigationRequest",
    "KnowledgeLinkNavigationResult",
    "KnowledgeLinkNavigator",
    "KnowledgeRetrievalError",
    "KnowledgeSearchMatch",
    "KnowledgeSearchRequest",
    "KnowledgeSearchResult",
    "KnowledgeSyncAction",
    "KnowledgeSyncActionKind",
    "KnowledgeSyncPlan",
    "KnowledgeSyncPlanner",
    "KnownKnowledgeDocument",
    "LocalEmbeddingError",
    "LocalEmbeddingProvider",
    "LocalSemanticKnowledgeIndexer",
    "LocalSemanticKnowledgeRetriever",
    "SemanticKnowledgeIndexRequest",
    "SemanticKnowledgeIndexResult",
    "SemanticKnowledgeMatch",
    "SemanticKnowledgeSearchRequest",
    "SemanticKnowledgeSearchResult",
]
