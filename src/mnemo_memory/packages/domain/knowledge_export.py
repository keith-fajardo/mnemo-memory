"""Portable retained knowledge history and payload-free deletions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Self, cast
from uuid import UUID, uuid5

from .identifiers import KnowledgeDocumentId, KnowledgeDocumentRevisionId
from .knowledge import (
    KnowledgeDocument,
    KnowledgeDocumentLink,
    KnowledgeDocumentRevision,
    KnowledgeDocumentSection,
    KnowledgeDocumentSourceKind,
    KnownKnowledgeDocument,
)
from .models import MemoryScope, ScopeLevel, _parse_datetime, _require_aware

KNOWLEDGE_EXPORT_FORMAT = "mnemo.knowledge-export.v1"
_IMPORTED_DOCUMENT_NAMESPACE = UUID("c495c2a6-d60b-48bf-86d4-b349028740ee")


def knowledge_import_document_identity(
    scope: MemoryScope, source_document_id: KnowledgeDocumentId
) -> KnowledgeDocumentId:
    if not isinstance(scope, MemoryScope) or scope.level is not ScopeLevel.PROJECT:
        raise ValueError("knowledge import identity requires exact project scope")
    if not isinstance(source_document_id, KnowledgeDocumentId):
        raise TypeError("knowledge import source identity is invalid")
    return KnowledgeDocumentId(
        uuid5(_IMPORTED_DOCUMENT_NAMESPACE, f"{scope.to_dict()}:{source_document_id}")
    )


def _canonical_json(value: Mapping[str, object]) -> str:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )


def _digest(value: Mapping[str, object]) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _document_to_dict(document: KnowledgeDocument) -> dict[str, object]:
    return {
        "document_id": str(document.document_id),
        "scope": document.scope.to_dict(),
        "relative_path": document.relative_path,
        "source_kind": document.source_kind.value,
        "content_digest": document.content_digest,
        "title": document.title,
        "frontmatter": [list(item) for item in document.frontmatter],
        "sections": [
            {"heading": item.heading, "level": item.level, "content": item.content}
            for item in document.sections
        ],
        "links": [{"target": item.target, "kind": item.kind} for item in document.links],
        "is_untrusted": document.is_untrusted,
    }


def _document_from_dict(value: Mapping[str, object]) -> KnowledgeDocument:
    expected = {
        "document_id",
        "scope",
        "relative_path",
        "source_kind",
        "content_digest",
        "title",
        "frontmatter",
        "sections",
        "links",
        "is_untrusted",
    }
    if set(value) != expected:
        raise ValueError("knowledge export document fields are invalid")
    scope = value["scope"]
    frontmatter = value["frontmatter"]
    sections = value["sections"]
    links = value["links"]
    strings = tuple(
        value[name]
        for name in ("document_id", "relative_path", "source_kind", "content_digest", "title")
    )
    if (
        not isinstance(scope, Mapping)
        or not all(isinstance(item, str) for item in strings)
        or not isinstance(frontmatter, list)
        or not all(
            isinstance(item, list)
            and len(item) == 2
            and all(isinstance(part, str) for part in item)
            for item in frontmatter
        )
        or not isinstance(sections, list)
        or not all(isinstance(item, Mapping) for item in sections)
        or not isinstance(links, list)
        or not all(isinstance(item, Mapping) for item in links)
        or value["is_untrusted"] is not True
    ):
        raise TypeError("knowledge export document serialization is invalid")
    parsed_sections: list[KnowledgeDocumentSection] = []
    for item in cast(list[Mapping[str, object]], sections):
        if set(item) != {"heading", "level", "content"}:
            raise ValueError("knowledge export section fields are invalid")
        heading, level, content = item["heading"], item["level"], item["content"]
        if (
            not isinstance(heading, str)
            or not isinstance(level, int)
            or isinstance(level, bool)
            or not isinstance(content, str)
        ):
            raise TypeError("knowledge export section serialization is invalid")
        parsed_sections.append(KnowledgeDocumentSection(heading, level, content))
    parsed_links: list[KnowledgeDocumentLink] = []
    for item in cast(list[Mapping[str, object]], links):
        if set(item) != {"target", "kind"}:
            raise ValueError("knowledge export link fields are invalid")
        target, kind = item["target"], item["kind"]
        if not isinstance(target, str) or not isinstance(kind, str):
            raise TypeError("knowledge export link serialization is invalid")
        parsed_links.append(KnowledgeDocumentLink(target, kind))
    return KnowledgeDocument(
        KnowledgeDocumentId.from_string(cast(str, strings[0])),
        MemoryScope.from_dict(scope),
        cast(str, strings[1]),
        KnowledgeDocumentSourceKind(cast(str, strings[2])),
        cast(str, strings[3]),
        cast(str, strings[4]),
        tuple((cast(str, item[0]), cast(str, item[1])) for item in frontmatter),
        tuple(parsed_sections),
        tuple(parsed_links),
    )


def _revision_to_dict(revision: KnowledgeDocumentRevision) -> dict[str, object]:
    return {
        "revision_id": str(revision.revision_id),
        "document": _document_to_dict(revision.document),
        "revision_number": revision.revision_number,
        "predecessor_revision_id": (
            None
            if revision.predecessor_revision_id is None
            else str(revision.predecessor_revision_id)
        ),
        "created_at": revision.created_at.isoformat(),
    }


def _revision_from_dict(value: Mapping[str, object]) -> KnowledgeDocumentRevision:
    if set(value) != {
        "revision_id",
        "document",
        "revision_number",
        "predecessor_revision_id",
        "created_at",
    }:
        raise ValueError("knowledge export revision fields are invalid")
    revision_id = value["revision_id"]
    document = value["document"]
    number = value["revision_number"]
    predecessor = value["predecessor_revision_id"]
    if (
        not isinstance(revision_id, str)
        or not isinstance(document, Mapping)
        or not isinstance(number, int)
        or isinstance(number, bool)
        or (predecessor is not None and not isinstance(predecessor, str))
    ):
        raise TypeError("knowledge export revision serialization is invalid")
    return KnowledgeDocumentRevision(
        KnowledgeDocumentRevisionId.from_string(revision_id),
        _document_from_dict(document),
        number,
        None if predecessor is None else KnowledgeDocumentRevisionId.from_string(predecessor),
        _parse_datetime(value["created_at"], "created_at"),
    )


def _known_to_dict(value: KnownKnowledgeDocument) -> dict[str, object]:
    return {
        "document_id": str(value.document_id),
        "scope": value.scope.to_dict(),
        "relative_path": value.relative_path,
        "content_digest": value.content_digest,
        "current_revision_id": str(value.current_revision_id),
        "revision_number": value.revision_number,
    }


def _known_from_dict(value: Mapping[str, object]) -> KnownKnowledgeDocument:
    if set(value) != {
        "document_id",
        "scope",
        "relative_path",
        "content_digest",
        "current_revision_id",
        "revision_number",
    }:
        raise ValueError("knowledge export active-source fields are invalid")
    scope = value["scope"]
    strings = tuple(
        value[name]
        for name in ("document_id", "relative_path", "content_digest", "current_revision_id")
    )
    number = value["revision_number"]
    if (
        not isinstance(scope, Mapping)
        or not all(isinstance(item, str) for item in strings)
        or not isinstance(number, int)
        or isinstance(number, bool)
    ):
        raise TypeError("knowledge export active-source serialization is invalid")
    return KnownKnowledgeDocument(
        KnowledgeDocumentId.from_string(cast(str, strings[0])),
        MemoryScope.from_dict(scope),
        cast(str, strings[1]),
        cast(str, strings[2]),
        KnowledgeDocumentRevisionId.from_string(cast(str, strings[3])),
        number,
    )


@dataclass(frozen=True, slots=True)
class KnowledgeDeletionRecord:
    """Canonical payload-free knowledge deletion state."""

    document_id: KnowledgeDocumentId
    scope: MemoryScope
    relative_path: str
    content_digest: str
    deleted_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.document_id, KnowledgeDocumentId):
            raise TypeError("knowledge deletion document identity is invalid")
        if not isinstance(self.scope, MemoryScope) or self.scope.level is not ScopeLevel.PROJECT:
            raise ValueError("knowledge deletion requires exact project scope")
        if not self.relative_path or self.relative_path.startswith("/"):
            raise ValueError("knowledge deletion path is invalid")
        if not self.content_digest.startswith("sha256:") or len(self.content_digest) != 71:
            raise ValueError("knowledge deletion digest is invalid")
        _require_aware(self.deleted_at, "deleted_at")

    def to_dict(self) -> dict[str, object]:
        return {
            "document_id": str(self.document_id),
            "scope": self.scope.to_dict(),
            "relative_path": self.relative_path,
            "content_digest": self.content_digest,
            "deleted_at": self.deleted_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        if set(value) != {"document_id", "scope", "relative_path", "content_digest", "deleted_at"}:
            raise ValueError("knowledge deletion fields are invalid")
        scope = value["scope"]
        strings = tuple(
            value[name] for name in ("document_id", "relative_path", "content_digest", "deleted_at")
        )
        if not isinstance(scope, Mapping) or not all(isinstance(item, str) for item in strings):
            raise TypeError("knowledge deletion serialization is invalid")
        return cls(
            KnowledgeDocumentId.from_string(cast(str, strings[0])),
            MemoryScope.from_dict(scope),
            cast(str, strings[1]),
            cast(str, strings[2]),
            _parse_datetime(strings[3], "deleted_at"),
        )


def _normalized_revision(revision: KnowledgeDocumentRevision) -> KnowledgeDocumentRevision:
    document = revision.document
    normalized = KnowledgeDocument(
        document.document_id,
        document.scope,
        document.relative_path,
        document.source_kind,
        document.content_digest,
        document.title,
        document.frontmatter,
        document.sections,
        tuple(sorted(document.links, key=lambda item: (item.kind, item.target))),
    )
    return KnowledgeDocumentRevision(
        revision.revision_id,
        normalized,
        revision.revision_number,
        revision.predecessor_revision_id,
        revision.created_at,
    )


@dataclass(frozen=True, slots=True)
class KnowledgeExportBundle:
    format_version: str
    scope: MemoryScope
    exported_at: datetime
    last_synced_at: datetime | None
    active_documents: tuple[KnownKnowledgeDocument, ...]
    revisions: tuple[KnowledgeDocumentRevision, ...]
    deletions: tuple[KnowledgeDeletionRecord, ...]
    content_digest: str

    def __post_init__(self) -> None:
        if self.format_version != KNOWLEDGE_EXPORT_FORMAT:
            raise ValueError("knowledge export format is unsupported")
        if not isinstance(self.scope, MemoryScope) or self.scope.level is not ScopeLevel.PROJECT:
            raise ValueError("knowledge export requires exact project scope")
        _require_aware(self.exported_at, "exported_at")
        if self.last_synced_at is not None:
            _require_aware(self.last_synced_at, "last_synced_at")
        self._validate()
        if self.content_digest != _digest(self._content_dict()):
            raise ValueError("knowledge export content digest does not match")

    @classmethod
    def create(
        cls,
        *,
        scope: MemoryScope,
        exported_at: datetime,
        last_synced_at: datetime | None,
        active_documents: tuple[KnownKnowledgeDocument, ...] = (),
        revisions: tuple[KnowledgeDocumentRevision, ...] = (),
        deletions: tuple[KnowledgeDeletionRecord, ...] = (),
    ) -> Self:
        content: dict[str, object] = {
            "format_version": KNOWLEDGE_EXPORT_FORMAT,
            "scope": scope.to_dict(),
            "exported_at": exported_at.isoformat(),
            "last_synced_at": None if last_synced_at is None else last_synced_at.isoformat(),
            "active_documents": [
                _known_to_dict(item)
                for item in sorted(active_documents, key=lambda item: str(item.document_id))
            ],
            "revisions": [
                _revision_to_dict(_normalized_revision(item))
                for item in sorted(
                    revisions,
                    key=lambda item: (str(item.document.document_id), item.revision_number),
                )
            ],
            "deletions": [
                item.to_dict() for item in sorted(deletions, key=lambda item: str(item.document_id))
            ],
        }
        return cls._from_content(content, _digest(content))

    def _content_dict(self) -> dict[str, object]:
        return {
            "format_version": self.format_version,
            "scope": self.scope.to_dict(),
            "exported_at": self.exported_at.isoformat(),
            "last_synced_at": (
                None if self.last_synced_at is None else self.last_synced_at.isoformat()
            ),
            "active_documents": [_known_to_dict(item) for item in self.active_documents],
            "revisions": [_revision_to_dict(item) for item in self.revisions],
            "deletions": [item.to_dict() for item in self.deletions],
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "content_digest": self.content_digest}

    def canonical_json(self) -> str:
        return _canonical_json(self.to_dict())

    @classmethod
    def from_json(cls, value: str) -> Self:
        if not isinstance(value, str):
            raise TypeError("knowledge export JSON must be text")

        def reject_constant(constant: str) -> None:
            raise ValueError(f"invalid JSON constant: {constant}")

        decoded = json.loads(value, parse_constant=reject_constant)
        if not isinstance(decoded, Mapping):
            raise TypeError("knowledge export JSON must contain an object")
        return cls.from_dict(decoded)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        expected = {
            "format_version",
            "scope",
            "exported_at",
            "last_synced_at",
            "active_documents",
            "revisions",
            "deletions",
            "content_digest",
        }
        if set(value) != expected:
            raise ValueError("knowledge export fields are invalid")
        digest = value["content_digest"]
        if not isinstance(digest, str):
            raise TypeError("knowledge export digest is invalid")
        return cls._from_content(
            {name: value[name] for name in expected if name != "content_digest"}, digest
        )

    @classmethod
    def _from_content(cls, value: Mapping[str, object], digest: str) -> Self:
        scope = value["scope"]
        format_version = value["format_version"]
        exported_at = value["exported_at"]
        last_synced_at = value["last_synced_at"]
        if (
            not isinstance(scope, Mapping)
            or not isinstance(format_version, str)
            or not isinstance(exported_at, str)
            or (last_synced_at is not None and not isinstance(last_synced_at, str))
        ):
            raise TypeError("knowledge export serialization is invalid")
        raw_lists: dict[str, list[Mapping[str, object]]] = {}
        for name in ("active_documents", "revisions", "deletions"):
            raw = value[name]
            if not isinstance(raw, list) or not all(isinstance(item, Mapping) for item in raw):
                raise TypeError("knowledge export serialization is invalid")
            raw_lists[name] = cast(list[Mapping[str, object]], raw)
        return cls(
            format_version,
            MemoryScope.from_dict(scope),
            _parse_datetime(exported_at, "exported_at"),
            None if last_synced_at is None else _parse_datetime(last_synced_at, "last_synced_at"),
            tuple(_known_from_dict(item) for item in raw_lists["active_documents"]),
            tuple(_revision_from_dict(item) for item in raw_lists["revisions"]),
            tuple(KnowledgeDeletionRecord.from_dict(item) for item in raw_lists["deletions"]),
            digest,
        )

    def _validate(self) -> None:
        if any(item.scope != self.scope for item in self.active_documents):
            raise ValueError("knowledge export contains cross-scope active documents")
        if any(item.document.scope != self.scope for item in self.revisions):
            raise ValueError("knowledge export contains cross-scope revisions")
        if any(item.scope != self.scope for item in self.deletions):
            raise ValueError("knowledge export contains cross-scope deletions")
        if self.active_documents != tuple(
            sorted(self.active_documents, key=lambda item: str(item.document_id))
        ):
            raise ValueError("knowledge export active documents are not canonically ordered")
        if self.revisions != tuple(
            sorted(
                self.revisions,
                key=lambda item: (str(item.document.document_id), item.revision_number),
            )
        ):
            raise ValueError("knowledge export revisions are not canonically ordered")
        if self.deletions != tuple(sorted(self.deletions, key=lambda item: str(item.document_id))):
            raise ValueError("knowledge export deletions are not canonically ordered")
        active_by_id = {item.document_id: item for item in self.active_documents}
        deletion_ids = {item.document_id for item in self.deletions}
        if len(active_by_id) != len(self.active_documents) or len(deletion_ids) != len(
            self.deletions
        ):
            raise ValueError("knowledge export contains duplicate source identities")
        if set(active_by_id) & deletion_ids:
            raise ValueError("knowledge export source is both active and deleted")
        if len({item.relative_path for item in self.active_documents}) != len(
            self.active_documents
        ):
            raise ValueError("knowledge export contains duplicate active paths")
        revision_ids = {item.revision_id for item in self.revisions}
        if len(revision_ids) != len(self.revisions):
            raise ValueError("knowledge export contains duplicate revision identities")
        revisions_by_document: dict[KnowledgeDocumentId, list[KnowledgeDocumentRevision]] = {}
        for revision in self.revisions:
            revisions_by_document.setdefault(revision.document.document_id, []).append(revision)
        if set(revisions_by_document) != set(active_by_id):
            raise ValueError("knowledge export revision history has an unknown or missing source")
        for document_id, active in active_by_id.items():
            revisions = revisions_by_document[document_id]
            if [item.revision_number for item in revisions] != list(range(1, len(revisions) + 1)):
                raise ValueError("knowledge export revision chain is not contiguous")
            for index, revision in enumerate(revisions):
                expected_predecessor = None if index == 0 else revisions[index - 1].revision_id
                if revision.predecessor_revision_id != expected_predecessor:
                    raise ValueError("knowledge export revision predecessor does not match")
            current = revisions[-1]
            if (
                active.current_revision_id != current.revision_id
                or active.revision_number != current.revision_number
                or active.relative_path != current.document.relative_path
                or active.content_digest != current.document.content_digest
            ):
                raise ValueError("knowledge export active source does not match current revision")
