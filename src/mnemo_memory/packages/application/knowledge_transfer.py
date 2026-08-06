"""Portable knowledge export and verified personal-to-team import."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from mnemo_memory.packages.domain import (
    KnowledgeDeletionRecord,
    KnowledgeDocument,
    KnowledgeDocumentRevision,
    KnowledgeExportBundle,
    KnownKnowledgeDocument,
    MemoryScope,
    ScopeLevel,
    knowledge_import_document_identity,
)
from mnemo_memory.packages.storage.contracts import (
    KnowledgeDocumentRepositoryError,
    KnowledgeExportRepository,
    KnowledgeImportConflict,
    KnowledgeImportRepository,
)


class KnowledgeTransferError(Exception):
    """Safe application outcome for knowledge transfer."""


class KnowledgeTransferConflict(KnowledgeTransferError):
    pass


class KnowledgeTransferStorageFailure(KnowledgeTransferError):
    pass


@dataclass(frozen=True, slots=True)
class KnowledgeTransferResult:
    source_content_digest: str
    target_content_digest: str
    active_document_count: int
    revision_count: int
    deletion_count: int
    idempotent: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "source_content_digest": self.source_content_digest,
            "target_content_digest": self.target_content_digest,
            "active_document_count": self.active_document_count,
            "revision_count": self.revision_count,
            "deletion_count": self.deletion_count,
            "idempotent": self.idempotent,
        }


class KnowledgeExportService:
    def __init__(self, repository: KnowledgeExportRepository) -> None:
        self._repository = repository

    def export(self, scope: MemoryScope, *, exported_at: datetime) -> KnowledgeExportBundle:
        if not isinstance(scope, MemoryScope) or scope.level is not ScopeLevel.PROJECT:
            raise ValueError("knowledge export requires exact project scope")
        try:
            return self._repository.export_knowledge_history(scope, exported_at=exported_at)
        except KnowledgeDocumentRepositoryError as error:
            raise KnowledgeTransferStorageFailure(
                "knowledge export storage operation failed"
            ) from error


class KnowledgeImportService:
    def __init__(
        self, exports: KnowledgeExportRepository, imports: KnowledgeImportRepository
    ) -> None:
        self._exports = exports
        self._imports = imports

    def import_bundle(
        self, bundle: KnowledgeExportBundle, *, target_scope: MemoryScope
    ) -> KnowledgeTransferResult:
        if not isinstance(bundle, KnowledgeExportBundle):
            raise TypeError("knowledge import requires a validated export bundle")
        if (
            not isinstance(target_scope, MemoryScope)
            or target_scope.level is not ScopeLevel.PROJECT
        ):
            raise ValueError("knowledge import requires exact target project scope")
        expected = rebase_knowledge_bundle(bundle, target_scope)
        try:
            before = self._exports.export_knowledge_history(
                target_scope, exported_at=bundle.exported_at
            )
            if _same_bundle(before, expected):
                return _result(bundle, before, idempotent=True)
            if (
                before.last_synced_at is not None
                or before.active_documents
                or before.revisions
                or before.deletions
            ):
                raise KnowledgeTransferConflict(
                    "knowledge import target contains conflicting state"
                )
            stored = self._imports.import_knowledge_history(bundle, expected)
            after = self._exports.export_knowledge_history(
                target_scope, exported_at=bundle.exported_at
            )
        except KnowledgeTransferError:
            raise
        except KnowledgeImportConflict as error:
            raise KnowledgeTransferConflict(str(error)) from error
        except KnowledgeDocumentRepositoryError as error:
            raise KnowledgeTransferStorageFailure(
                "knowledge import storage operation failed"
            ) from error
        if not _same_bundle(after, expected):
            raise KnowledgeTransferConflict(
                "knowledge import target counts or canonical state do not match"
            )
        return _result(bundle, after, idempotent=stored.idempotent)


def rebase_knowledge_bundle(
    bundle: KnowledgeExportBundle, target_scope: MemoryScope
) -> KnowledgeExportBundle:
    mapped = {
        document_id: knowledge_import_document_identity(target_scope, document_id)
        for document_id in (
            *(item.document_id for item in bundle.active_documents),
            *(item.document_id for item in bundle.deletions),
        )
    }
    active = tuple(
        KnownKnowledgeDocument(
            mapped[item.document_id],
            target_scope,
            item.relative_path,
            item.content_digest,
            item.current_revision_id,
            item.revision_number,
        )
        for item in bundle.active_documents
    )
    revisions = tuple(
        KnowledgeDocumentRevision(
            item.revision_id,
            KnowledgeDocument(
                mapped[item.document.document_id],
                target_scope,
                item.document.relative_path,
                item.document.source_kind,
                item.document.content_digest,
                item.document.title,
                item.document.frontmatter,
                item.document.sections,
                item.document.links,
            ),
            item.revision_number,
            item.predecessor_revision_id,
            item.created_at,
        )
        for item in bundle.revisions
    )
    deletions = tuple(
        KnowledgeDeletionRecord(
            mapped[item.document_id],
            target_scope,
            item.relative_path,
            item.content_digest,
            item.deleted_at,
        )
        for item in bundle.deletions
    )
    return KnowledgeExportBundle.create(
        scope=target_scope,
        exported_at=bundle.exported_at,
        last_synced_at=bundle.last_synced_at,
        active_documents=active,
        revisions=revisions,
        deletions=deletions,
    )


def _same_bundle(left: KnowledgeExportBundle, right: KnowledgeExportBundle) -> bool:
    return (
        left.format_version == right.format_version
        and left.scope == right.scope
        and left.exported_at == right.exported_at
        and left.last_synced_at == right.last_synced_at
        and left.active_documents == right.active_documents
        and left.revisions == right.revisions
        and left.deletions == right.deletions
    )


def _result(
    source: KnowledgeExportBundle,
    target: KnowledgeExportBundle,
    *,
    idempotent: bool,
) -> KnowledgeTransferResult:
    return KnowledgeTransferResult(
        source.content_digest,
        target.content_digest,
        len(target.active_documents),
        len(target.revisions),
        len(target.deletions),
        idempotent,
    )
