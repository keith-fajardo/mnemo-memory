"""Deterministic, bounded Markdown and Obsidian-document parsing.

This module deliberately creates *untrusted input candidates*, not active memory.  It never
executes Markdown, follows links, evaluates frontmatter, reads a filesystem, or makes a model or
network call.  A later scoped connector and policy layer decide whether a candidate is safe to
persist and retrieve.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from uuid import UUID, uuid5

from mnemo_memory.packages.domain import KnowledgeDocumentId, MemoryScope

_DOCUMENT_NAMESPACE = UUID("193d4055-6458-4577-b1cf-1ae4a9458678")
_HEADING_PATTERN = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_FRONTMATTER_KEY_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_MARKDOWN_LINK_PATTERN = re.compile(r"(?<!!)\[[^\]\r\n]{0,256}\]\(([^()\r\n]{1,512})\)")
_WIKI_LINK_PATTERN = re.compile(
    r"\[\[([^\]|#\r\n]{1,512})(?:#[^\]|\r\n]{1,512})?(?:\|[^\]\r\n]{0,256})?\]\]"
)
_MAX_FRONTMATTER_VALUE = 512


class KnowledgeDocumentSourceKind(StrEnum):
    MARKDOWN = "markdown"
    OBSIDIAN = "obsidian"


class KnowledgeDocumentParseError(ValueError):
    """Sanitized invalid-document outcome; never includes document content."""


@dataclass(frozen=True, slots=True)
class KnowledgeDocumentParseLimits:
    """Bounds for personal local-document parsing before any persistence decision."""

    max_bytes: int = 1_000_000
    max_sections: int = 500
    max_section_characters: int = 12_000
    max_links: int = 1_000
    max_frontmatter_fields: int = 64

    def __post_init__(self) -> None:
        if any(
            value < 1
            for value in (
                self.max_bytes,
                self.max_sections,
                self.max_section_characters,
                self.max_links,
                self.max_frontmatter_fields,
            )
        ):
            raise ValueError("knowledge document limits must be positive")


@dataclass(frozen=True, slots=True)
class KnowledgeDocumentParseRequest:
    """Explicit scope and safe relative source identity for one Markdown input."""

    scope: MemoryScope
    relative_path: str
    source_kind: KnowledgeDocumentSourceKind = KnowledgeDocumentSourceKind.MARKDOWN
    limits: KnowledgeDocumentParseLimits = KnowledgeDocumentParseLimits()

    def __post_init__(self) -> None:
        if not isinstance(self.scope, MemoryScope):
            raise TypeError("knowledge parsing requires an explicit scope")
        if (
            not isinstance(self.relative_path, str)
            or not self.relative_path
            or self.relative_path.startswith("/")
            or ".." in self.relative_path.split("/")
            or "\\" in self.relative_path
        ):
            raise KnowledgeDocumentParseError("MNEMO_KNOWLEDGE_PATH_INVALID")
        if not isinstance(self.source_kind, KnowledgeDocumentSourceKind):
            raise TypeError("knowledge source kind is invalid")


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
    """A bounded literal section.  ``content`` remains untrusted data, never instructions."""

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


class KnowledgeDocumentParser:
    """Parse one local Markdown byte/string input without a filesystem or execution boundary."""

    def parse(
        self, request: KnowledgeDocumentParseRequest, content: bytes | str
    ) -> KnowledgeDocument:
        raw = self._bytes(content)
        if len(raw) > request.limits.max_bytes:
            raise KnowledgeDocumentParseError("MNEMO_KNOWLEDGE_BYTES_LIMIT")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise KnowledgeDocumentParseError("MNEMO_KNOWLEDGE_UTF8_INVALID") from error
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        frontmatter, body = self._frontmatter(text, request.limits)
        sections = self._sections(body, request.limits)
        links = self._links(body, request.limits)
        title = self._title(frontmatter, sections, request.relative_path)
        digest = f"sha256:{sha256(raw).hexdigest()}"
        document_id = KnowledgeDocumentId(
            uuid5(_DOCUMENT_NAMESPACE, f"{request.scope.to_dict()}:{request.relative_path}")
        )
        return KnowledgeDocument(
            document_id=document_id,
            scope=request.scope,
            relative_path=request.relative_path,
            source_kind=request.source_kind,
            content_digest=digest,
            title=title,
            frontmatter=frontmatter,
            sections=sections,
            links=links,
        )

    @staticmethod
    def _bytes(content: bytes | str) -> bytes:
        if isinstance(content, bytes):
            return content
        if isinstance(content, str):
            return content.encode("utf-8")
        raise TypeError("knowledge document content must be bytes or str")

    @staticmethod
    def _frontmatter(
        text: str, limits: KnowledgeDocumentParseLimits
    ) -> tuple[tuple[tuple[str, str], ...], str]:
        if not text.startswith("---\n"):
            return (), text
        closing = text.find("\n---\n", 4)
        if closing == -1:
            raise KnowledgeDocumentParseError("MNEMO_KNOWLEDGE_FRONTMATTER_INVALID")
        values: list[tuple[str, str]] = []
        for line in text[4:closing].split("\n"):
            if not line or line.lstrip().startswith("#"):
                continue
            if ":" not in line:
                raise KnowledgeDocumentParseError("MNEMO_KNOWLEDGE_FRONTMATTER_INVALID")
            key, value = line.split(":", maxsplit=1)
            key, value = key.strip(), value.strip()
            if (
                not _FRONTMATTER_KEY_PATTERN.fullmatch(key)
                or not value
                or len(value) > _MAX_FRONTMATTER_VALUE
            ):
                raise KnowledgeDocumentParseError("MNEMO_KNOWLEDGE_FRONTMATTER_INVALID")
            values.append((key, value))
        if len(values) > limits.max_frontmatter_fields or len({key for key, _ in values}) != len(
            values
        ):
            raise KnowledgeDocumentParseError("MNEMO_KNOWLEDGE_FRONTMATTER_LIMIT")
        return tuple(values), text[closing + len("\n---\n") :]

    @staticmethod
    def _sections(
        text: str, limits: KnowledgeDocumentParseLimits
    ) -> tuple[KnowledgeDocumentSection, ...]:
        sections: list[KnowledgeDocumentSection] = []
        heading, level = "Document", 0
        content: list[str] = []
        for line in text.split("\n"):
            match = _HEADING_PATTERN.match(line)
            if match is None:
                content.append(line)
                continue
            sections.append(KnowledgeDocumentParser._section(heading, level, content, limits))
            heading, level, content = match.group(2).strip(), len(match.group(1)), []
        sections.append(KnowledgeDocumentParser._section(heading, level, content, limits))
        normalized = tuple(section for section in sections if section.content or section.level > 0)
        if len(normalized) > limits.max_sections:
            raise KnowledgeDocumentParseError("MNEMO_KNOWLEDGE_SECTION_LIMIT")
        return normalized or (KnowledgeDocumentSection("Document", 0, ""),)

    @staticmethod
    def _section(
        heading: str, level: int, lines: list[str], limits: KnowledgeDocumentParseLimits
    ) -> KnowledgeDocumentSection:
        content = "\n".join(lines).strip()
        if len(content) > limits.max_section_characters:
            raise KnowledgeDocumentParseError("MNEMO_KNOWLEDGE_SECTION_LIMIT")
        return KnowledgeDocumentSection(heading, level, content)

    @staticmethod
    def _links(
        text: str, limits: KnowledgeDocumentParseLimits
    ) -> tuple[KnowledgeDocumentLink, ...]:
        links = {
            *(
                KnowledgeDocumentLink(match.group(1).strip(), "markdown")
                for match in _MARKDOWN_LINK_PATTERN.finditer(text)
            ),
            *(
                KnowledgeDocumentLink(match.group(1).strip(), "wiki")
                for match in _WIKI_LINK_PATTERN.finditer(text)
            ),
        }
        if len(links) > limits.max_links:
            raise KnowledgeDocumentParseError("MNEMO_KNOWLEDGE_LINK_LIMIT")
        return tuple(sorted(links, key=lambda item: (item.kind, item.target)))

    @staticmethod
    def _title(
        frontmatter: tuple[tuple[str, str], ...],
        sections: tuple[KnowledgeDocumentSection, ...],
        relative_path: str,
    ) -> str:
        frontmatter_title = next((value for key, value in frontmatter if key == "title"), None)
        if frontmatter_title is not None:
            return frontmatter_title
        heading = next((section.heading for section in sections if section.level == 1), None)
        return heading or relative_path.rsplit("/", maxsplit=1)[-1].rsplit(".", maxsplit=1)[0]
