"""Deterministic pre-persistence safety policy for local knowledge documents.

This deliberately conservative gate is not an attempt to classify every secret. It rejects a
small set of high-confidence credential signatures before Mnemo stores document payloads. Later
policy can add a reviewed classifier, but must not weaken these deterministic checks.
"""

from __future__ import annotations

from dataclasses import dataclass

from mnemo_memory.packages.domain import KnowledgeDocument, Sensitivity

from .content_safety import ContentSafetyClassifier, ContentSafetyPolicy
from .content_safety import contains_high_confidence_secret as contains_high_confidence_secret


@dataclass(frozen=True, slots=True)
class KnowledgeDocumentSafetyDecision:
    """A content-free safety result suitable for diagnostics and tests."""

    accepted: bool
    sensitivity: Sensitivity
    code: str | None = None

    def __post_init__(self) -> None:
        if self.accepted != (self.code is None) or (
            self.accepted == (self.sensitivity is Sensitivity.PROHIBITED)
        ):
            raise ValueError("knowledge safety decisions must have one consistent code state")


class KnowledgeDocumentSafetyPolicy:
    """Reject high-confidence secrets without exposing matched content."""

    def __init__(self, additional_classifiers: tuple[ContentSafetyClassifier, ...] = ()) -> None:
        self._content_safety = ContentSafetyPolicy(additional_classifiers)

    def assess(self, document: KnowledgeDocument) -> KnowledgeDocumentSafetyDecision:
        if not isinstance(document, KnowledgeDocument) or not document.is_untrusted:
            raise TypeError("knowledge safety policy requires an untrusted knowledge document")
        values = (
            document.relative_path,
            document.title,
            *(value for pair in document.frontmatter for value in pair),
            *(value for item in document.sections for value in (item.heading, item.content)),
        )
        decision = self._content_safety.assess(*values)
        code = decision.code
        if code == "MNEMO_CONTENT_SECRET_REJECTED":
            code = "MNEMO_KNOWLEDGE_SECRET_REJECTED"
        return KnowledgeDocumentSafetyDecision(decision.accepted, decision.sensitivity, code)
