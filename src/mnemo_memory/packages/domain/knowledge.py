"""Immutable, scoped local-knowledge document candidates and revisions.

Document text is explicitly untrusted data. It gains no authority merely by entering this model;
policy and repository layers must validate it before persistence or context retrieval.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .identifiers import KnowledgeDocumentId, KnowledgeDocumentRevisionId
from .models import MemoryScope


class KnowledgeDocumentSourceKind(StrEnum):
    MARKDOWN = "markdown"
    OBSIDIAN = "obsidian"


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
