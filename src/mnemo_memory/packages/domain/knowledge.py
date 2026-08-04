"""Immutable, scoped local-knowledge document candidates and revisions.

Document text is explicitly untrusted data. It gains no authority merely by entering this model;
policy and repository layers must validate it before persistence or context retrieval.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from math import isfinite

from .identifiers import KnowledgeDocumentId, KnowledgeDocumentRevisionId
from .models import MemoryScope, ScopeLevel


class KnowledgeDocumentSourceKind(StrEnum):
    MARKDOWN = "markdown"
    OBSIDIAN = "obsidian"


class KnowledgeLinkDirection(StrEnum):
    """One bounded current-revision navigation direction; no graph inference is implied."""

    OUTBOUND = "outbound"
    BACKLINKS = "backlinks"


@dataclass(frozen=True, slots=True)
class KnowledgeDocumentLink:
    """One declared Markdown or Obsidian link, retained as untrusted text evidence."""

    target: str
    kind: str

    def __post_init__(self) -> None:
        if self.kind not in {"markdown", "wiki"} or not self.target or len(self.target) > 512:
            raise ValueError("knowledge document link is invalid")


@dataclass(frozen=True, slots=True)
class KnowledgeDocumentSection:
    """A bounded literal section. ``content`` remains untrusted data, never instructions."""

    heading: str
    level: int
    content: str

    def __post_init__(self) -> None:
        if not self.heading or not 0 <= self.level <= 6:
            raise ValueError("knowledge document section heading is invalid")
        if len(self.content) > 12_000:
            raise ValueError("knowledge document section exceeds the domain bound")


@dataclass(frozen=True, slots=True)
class KnowledgeDocument:
    """Parsed local document candidate with explicit provenance identity and untrusted payload."""

    document_id: KnowledgeDocumentId
    scope: MemoryScope
    relative_path: str
    source_kind: KnowledgeDocumentSourceKind
    content_digest: str
    title: str
    frontmatter: tuple[tuple[str, str], ...]
    sections: tuple[KnowledgeDocumentSection, ...]
    links: tuple[KnowledgeDocumentLink, ...]
    is_untrusted: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.document_id, KnowledgeDocumentId):
            raise TypeError("knowledge document identity is invalid")
        if not self.content_digest.startswith("sha256:") or len(self.content_digest) != 71:
            raise ValueError("knowledge document requires a sha256 digest")
        if not self.relative_path or self.relative_path.startswith("/"):
            raise ValueError("knowledge document path is invalid")
        if not self.title or len(self.title) > 512:
            raise ValueError("knowledge document title is invalid")
        if not self.is_untrusted:
            raise ValueError("parsed knowledge documents must remain untrusted")


@dataclass(frozen=True, slots=True)
class KnownKnowledgeDocument:
    """Minimal active source state used for incremental synchronization.

    It contains no frontmatter, sections, links, or other document payload.
    """

    document_id: KnowledgeDocumentId
    scope: MemoryScope
    relative_path: str
    content_digest: str
    current_revision_id: KnowledgeDocumentRevisionId
    revision_number: int

    def __post_init__(self) -> None:
        if not isinstance(self.document_id, KnowledgeDocumentId):
            raise TypeError("known knowledge document identity is invalid")
        if not isinstance(self.current_revision_id, KnowledgeDocumentRevisionId):
            raise TypeError("known knowledge document revision identity is invalid")
        if not self.relative_path or self.relative_path.startswith("/"):
            raise ValueError("known knowledge document path is invalid")
        if not self.content_digest.startswith("sha256:") or len(self.content_digest) != 71:
            raise ValueError("known knowledge document requires a sha256 digest")
        if self.revision_number < 1:
            raise ValueError("known knowledge document revision number must be positive")


@dataclass(frozen=True, slots=True)
class KnowledgeDocumentRevision:
    """One immutable accepted revision of a scoped local knowledge source."""

    revision_id: KnowledgeDocumentRevisionId
    document: KnowledgeDocument
    revision_number: int
    predecessor_revision_id: KnowledgeDocumentRevisionId | None
    created_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.revision_id, KnowledgeDocumentRevisionId):
            raise TypeError("knowledge document revision identity is invalid")
        if self.revision_number < 1:
            raise ValueError("knowledge document revision number must be positive")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("knowledge document revision timestamp must be timezone-aware")
        if self.revision_number == 1 and self.predecessor_revision_id is not None:
            raise ValueError("first knowledge document revision cannot have a predecessor")
        if self.revision_number > 1 and self.predecessor_revision_id is None:
            raise ValueError("later knowledge document revision requires a predecessor")
        if self.predecessor_revision_id == self.revision_id:
            raise ValueError("knowledge document revision cannot precede itself")


@dataclass(frozen=True, slots=True)
class KnowledgeDocumentTombstone:
    """Minimal deletion record; document title, sections, frontmatter, and links are excluded."""

    document_id: KnowledgeDocumentId
    scope: MemoryScope
    relative_path: str
    content_digest: str
    expected_revision_id: KnowledgeDocumentRevisionId
    deleted_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.document_id, KnowledgeDocumentId):
            raise TypeError("knowledge tombstone document identity is invalid")
        if not isinstance(self.expected_revision_id, KnowledgeDocumentRevisionId):
            raise TypeError("knowledge tombstone expected revision is invalid")
        if not self.relative_path or self.relative_path.startswith("/"):
            raise ValueError("knowledge tombstone path is invalid")
        if not self.content_digest.startswith("sha256:") or len(self.content_digest) != 71:
            raise ValueError("knowledge tombstone requires a sha256 digest")
        if self.deleted_at.tzinfo is None or self.deleted_at.utcoffset() is None:
            raise ValueError("knowledge tombstone timestamp must be timezone-aware")


@dataclass(frozen=True, slots=True)
class KnowledgeDocumentSectionMatch:
    """One scored literal section from a scoped retained revision; text remains untrusted data."""

    revision: KnowledgeDocumentRevision
    section_index: int
    section: KnowledgeDocumentSection
    score: int

    def __post_init__(self) -> None:
        if self.section_index < 0 or self.score < 1:
            raise ValueError("knowledge section match is invalid")


@dataclass(frozen=True, slots=True)
class CurrentKnowledgeDocumentSection:
    """One current, scoped section made available to a rebuildable retrieval projection."""

    revision: KnowledgeDocumentRevision
    section_index: int
    section: KnowledgeDocumentSection

    def __post_init__(self) -> None:
        if self.section_index < 0 or self.section_index >= len(self.revision.document.sections):
            raise ValueError("current knowledge section is invalid")
        if self.revision.document.sections[self.section_index] != self.section:
            raise ValueError("current knowledge section does not match its revision")


@dataclass(frozen=True, slots=True)
class KnowledgeSectionEmbedding:
    """One rebuildable local vector projection for an immutable document section.

    This is not canonical memory and it has no authority over document evidence.  It is retained
    only after an explicit local semantic-index action, is always scope-bound, and is deleted with
    its revision when the source is tombstoned.  ``section_digest`` lets the projection be safely
    invalidated without persisting a second copy of the text.
    """

    scope: MemoryScope
    revision_id: KnowledgeDocumentRevisionId
    section_index: int
    model_id: str
    section_digest: str
    vector: tuple[float, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.scope, MemoryScope) or self.scope.level is not ScopeLevel.PROJECT:
            raise ValueError("knowledge embedding requires an explicit project scope")
        if not isinstance(self.revision_id, KnowledgeDocumentRevisionId) or self.section_index < 0:
            raise ValueError("knowledge embedding identity is invalid")
        if (
            not isinstance(self.model_id, str)
            or not self.model_id.strip()
            or len(self.model_id) > 256
            or any(character.isspace() for character in self.model_id)
        ):
            raise ValueError("knowledge embedding model is invalid")
        if not self.section_digest.startswith("sha256:") or len(self.section_digest) != 71:
            raise ValueError("knowledge embedding section digest is invalid")
        if not 8 <= len(self.vector) <= 4_096 or not all(isfinite(value) for value in self.vector):
            raise ValueError("knowledge embedding vector is invalid")


@dataclass(frozen=True, slots=True)
class KnowledgeDocumentRelation:
    """One exact resolved current-document link with immutable revision evidence on both ends."""

    source_document_id: KnowledgeDocumentId
    source_revision_id: KnowledgeDocumentRevisionId
    source_relative_path: str
    target_document_id: KnowledgeDocumentId
    target_revision_id: KnowledgeDocumentRevisionId
    target_relative_path: str
    link_kind: str
    declared_target: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.source_document_id, KnowledgeDocumentId)
            or not isinstance(self.target_document_id, KnowledgeDocumentId)
            or not isinstance(self.source_revision_id, KnowledgeDocumentRevisionId)
            or not isinstance(self.target_revision_id, KnowledgeDocumentRevisionId)
            or not self.source_relative_path
            or not self.target_relative_path
            or self.link_kind not in {"markdown", "wiki"}
            or not self.declared_target
            or len(self.declared_target) > 512
        ):
            raise ValueError("knowledge document relation is invalid")


_KNOWLEDGE_TERM_PATTERN = re.compile(r"[^\W_][\w-]{1,63}", re.UNICODE)


def _knowledge_search_token(value: str) -> str:
    """Normalize one literal token to SQLite unicode61-compatible search text."""
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def normalize_knowledge_query(query: str, *, maximum_terms: int = 12) -> tuple[str, ...]:
    """Return bounded, stable literal terms without semantic interpretation or model use."""
    if not isinstance(query, str) or not 1 <= len(query) <= 512:
        raise ValueError("knowledge query is invalid")
    if not 1 <= maximum_terms <= 24:
        raise ValueError("knowledge query term limit is invalid")
    terms: list[str] = []
    for raw_term in _KNOWLEDGE_TERM_PATTERN.findall(query.casefold()):
        term = _knowledge_search_token(raw_term)
        if term not in terms:
            terms.append(term)
        if len(terms) == maximum_terms:
            break
    return tuple(terms)


def knowledge_search_tokens(text: str) -> tuple[str, ...]:
    """Return all literal tokens for deterministic lexical scoring.

    The token-based rule deliberately shares SQLite FTS5's search semantics with the reference
    adapter. Document text remains untrusted data; tokenization grants it no authority.
    """
    if not isinstance(text, str):
        raise TypeError("knowledge search text must be a string")
    return tuple(_knowledge_search_token(term) for term in _KNOWLEDGE_TERM_PATTERN.findall(text))


def knowledge_section_digest(section: KnowledgeDocumentSection) -> str:
    """Return a deterministic identity for one literal section without retaining another payload."""
    if not isinstance(section, KnowledgeDocumentSection):
        raise TypeError("knowledge section is invalid")
    encoded = (section.heading + "\n" + section.content).encode("utf-8")
    return "sha256:" + sha256(encoded).hexdigest()
