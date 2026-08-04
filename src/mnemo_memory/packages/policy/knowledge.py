"""Deterministic pre-persistence safety policy for local knowledge documents.

This deliberately conservative gate is not an attempt to classify every secret. It rejects a
small set of high-confidence credential signatures before Mnemo stores document payloads. Later
policy can add a reviewed classifier, but must not weaken these deterministic checks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from mnemo_memory.packages.domain import KnowledgeDocument

_HIGH_CONFIDENCE_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN(?: [A-Z]+)? PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(
        r"(?im)^\s*(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|secret)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{16,}"
    ),
)


def contains_high_confidence_secret(*values: str) -> bool:
    """Return a content-free deterministic decision shared by persistence boundaries."""
    if any(not isinstance(value, str) for value in values):
        raise TypeError("secret policy values must be strings")
    return any(
        pattern.search(value) is not None
        for value in values
        for pattern in _HIGH_CONFIDENCE_SECRET_PATTERNS
    )


@dataclass(frozen=True, slots=True)
class KnowledgeDocumentSafetyDecision:
    """A content-free safety result suitable for diagnostics and tests."""

    accepted: bool
    code: str | None = None

    def __post_init__(self) -> None:
        if self.accepted != (self.code is None):
            raise ValueError("knowledge safety decisions must have one consistent code state")


class KnowledgeDocumentSafetyPolicy:
    """Reject high-confidence secrets without exposing matched content."""

    def assess(self, document: KnowledgeDocument) -> KnowledgeDocumentSafetyDecision:
        if not isinstance(document, KnowledgeDocument) or not document.is_untrusted:
            raise TypeError("knowledge safety policy requires an untrusted knowledge document")
        # The future repository persists frontmatter and section payloads, so both enter this
        # deterministic gate. Link targets are kept metadata-only and cannot carry a secret value.
        values = (
            *tuple(value for _, value in document.frontmatter),
            *(item.content for item in document.sections),
        )
        if contains_high_confidence_secret(*values):
            return KnowledgeDocumentSafetyDecision(False, "MNEMO_KNOWLEDGE_SECRET_REJECTED")
        return KnowledgeDocumentSafetyDecision(True)
