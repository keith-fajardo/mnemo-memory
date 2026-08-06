"""PostgreSQL/pgvector implementation of the scoped knowledge repository."""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import cast

from mnemo_memory.packages.domain import (
    CurrentKnowledgeDocumentSection,
    KnowledgeDocument,
    KnowledgeDocumentId,
    KnowledgeDocumentLink,
    KnowledgeDocumentRevision,
    KnowledgeDocumentRevisionId,
    KnowledgeDocumentSection,
    KnowledgeDocumentSectionMatch,
    KnowledgeDocumentSourceKind,
    KnowledgeDocumentTombstone,
    KnowledgeSectionEmbedding,
    KnownKnowledgeDocument,
    MemoryScope,
    OwnerId,
    ScopeLevel,
    WorkspaceId,
)
from mnemo_memory.packages.policy import KnowledgeDocumentSafetyPolicy, TeamOperation

from .contracts import (
    InvalidKnowledgeDocumentScope,
    KnowledgeDocumentConflict,
    KnowledgeDocumentNotFound,
    KnowledgeDocumentSecretRejected,
    KnowledgeDocumentStorageFailure,
    KnowledgeDocumentSyncStoreResult,
    rank_knowledge_sections,
    validate_knowledge_search,
)
from .postgres import PostgreSQLConnectionFactory, PostgreSQLCursor

_REVISION_COLUMNS = (
    "revision.revision_id::text, revision.document_id::text, "
    "revision.revision_number, revision.predecessor_revision_id::text, "
    "revision.source_kind, revision.relative_path, revision.content_digest, "
    "revision.title, revision.frontmatter_json::text, revision.created_at"
)


def _sqlstate(error: Exception) -> str | None:
    for value in error.args:
        if isinstance(value, dict):
            state = value.get("C")
            if isinstance(state, str):
                return state
    return None


def _translated_error(
    error: Exception,
) -> KnowledgeDocumentStorageFailure | KnowledgeDocumentConflict:
    state = _sqlstate(error)
    if state == "42501" or (state is not None and state.startswith("23")):
        return KnowledgeDocumentConflict("knowledge database rejected conflicting state")
    return KnowledgeDocumentStorageFailure("knowledge database operation failed")


def _vector_text(vector: tuple[float, ...]) -> str:
    return "[" + ",".join(repr(value) for value in vector) + "]"


def _parse_vector(value: object) -> tuple[float, ...]:
    text = str(value)
    if len(text) < 2 or text[0] != "[" or text[-1] != "]":
        raise ValueError("knowledge vector storage is invalid")
    return tuple(float(item) for item in text[1:-1].split(","))


class PostgreSQLKnowledgeDocumentRepository:
    """One principal/workspace-bound durable team knowledge repository."""

    def __init__(
        self,
        connection_factory: PostgreSQLConnectionFactory,
        *,
        principal_id: OwnerId,
        workspace_id: WorkspaceId,
        policy: KnowledgeDocumentSafetyPolicy | None = None,
        statement_timeout_ms: int = 5000,
    ) -> None:
        if not isinstance(principal_id, OwnerId):
            raise TypeError("principal_id must be an OwnerId")
        if not isinstance(workspace_id, WorkspaceId):
            raise TypeError("workspace_id must be a WorkspaceId")
        if (
            not isinstance(statement_timeout_ms, int)
            or isinstance(statement_timeout_ms, bool)
            or not 1 <= statement_timeout_ms <= 60_000
        ):
            raise ValueError("statement_timeout_ms must be between 1 and 60000")
        self._connection_factory = connection_factory
        self._principal_id = principal_id
        self._workspace_id = workspace_id
        self._policy = policy or KnowledgeDocumentSafetyPolicy()
        self._statement_timeout_ms = statement_timeout_ms

    def list_active_documents(self, scope: MemoryScope) -> tuple[KnownKnowledgeDocument, ...]:
        self._require_scope(scope)
        with self._transaction(TeamOperation.READ) as cursor:
            rows = self._active_document_rows(cursor, scope)
            return tuple(self._known_document(row, scope) for row in rows)

    def last_sync_at(self, scope: MemoryScope) -> datetime | None:
        self._require_scope(scope)
        with self._transaction(TeamOperation.READ) as cursor:
            cursor.execute(
                "SELECT last_synced_at FROM mnemo_team.knowledge_sync_status "
                "WHERE workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s",
                self._scope_values(scope),
            )
            row = cursor.fetchone()
            return None if row is None else cast(datetime, row[0])

    def get_current_revision(
        self, scope: MemoryScope, document_id: KnowledgeDocumentId
    ) -> KnowledgeDocumentRevision:
        self._require_scope(scope)
        if not isinstance(document_id, KnowledgeDocumentId):
            raise TypeError("document_id must be a KnowledgeDocumentId")
        with self._transaction(TeamOperation.READ) as cursor:
            cursor.execute(
                "SELECT "
                + _REVISION_COLUMNS
                + " FROM mnemo_team.knowledge_document_sources AS source "
                "JOIN mnemo_team.knowledge_document_revisions AS revision "
                "ON revision.workspace_id = source.workspace_id "
                "AND revision.revision_id = source.current_revision_id "
                "WHERE source.workspace_id = CAST(%s AS uuid) "
                "AND source.project_id = CAST(%s AS uuid) "
                "AND source.owner_id = CAST(%s AS uuid) AND source.visibility = %s "
                "AND source.document_id = CAST(%s AS uuid) AND NOT source.is_deleted",
                (*self._scope_values(scope), str(document_id)),
            )
            row = cursor.fetchone()
            if row is None:
                raise KnowledgeDocumentNotFound("knowledge document was not found")
            return self._revision_from_row(cursor, row, scope)

    def get_current_revision_by_path(
        self, scope: MemoryScope, relative_path: str
    ) -> KnowledgeDocumentRevision:
        self._require_scope(scope)
        if (
            not isinstance(relative_path, str)
            or not relative_path
            or relative_path.startswith("/")
            or ".." in relative_path.split("/")
        ):
            raise KnowledgeDocumentNotFound("knowledge document was not found")
        with self._transaction(TeamOperation.READ) as cursor:
            cursor.execute(
                "SELECT "
                + _REVISION_COLUMNS
                + " FROM mnemo_team.knowledge_document_sources AS source "
                "JOIN mnemo_team.knowledge_document_revisions AS revision "
                "ON revision.workspace_id = source.workspace_id "
                "AND revision.revision_id = source.current_revision_id "
                "WHERE source.workspace_id = CAST(%s AS uuid) "
                "AND source.project_id = CAST(%s AS uuid) "
                "AND source.owner_id = CAST(%s AS uuid) AND source.visibility = %s "
                "AND source.relative_path = %s AND NOT source.is_deleted",
                (*self._scope_values(scope), relative_path),
            )
            row = cursor.fetchone()
            if row is None:
                raise KnowledgeDocumentNotFound("knowledge document was not found")
            return self._revision_from_row(cursor, row, scope)

    def get_revision(
        self,
        scope: MemoryScope,
        document_id: KnowledgeDocumentId,
        revision_id: KnowledgeDocumentRevisionId,
    ) -> KnowledgeDocumentRevision:
        self._require_scope(scope)
        if not isinstance(document_id, KnowledgeDocumentId) or not isinstance(
            revision_id, KnowledgeDocumentRevisionId
        ):
            raise TypeError("knowledge revision identities are invalid")
        with self._transaction(TeamOperation.READ) as cursor:
            cursor.execute(
                "SELECT "
                + _REVISION_COLUMNS
                + " FROM mnemo_team.knowledge_document_sources AS source "
                "JOIN mnemo_team.knowledge_document_revisions AS revision "
                "ON revision.workspace_id = source.workspace_id "
                "AND revision.document_id = source.document_id "
                "WHERE source.workspace_id = CAST(%s AS uuid) "
                "AND source.project_id = CAST(%s AS uuid) "
                "AND source.owner_id = CAST(%s AS uuid) AND source.visibility = %s "
                "AND source.document_id = CAST(%s AS uuid) "
                "AND revision.revision_id = CAST(%s AS uuid) AND NOT source.is_deleted",
                (*self._scope_values(scope), str(document_id), str(revision_id)),
            )
            row = cursor.fetchone()
            if row is None:
                raise KnowledgeDocumentNotFound("knowledge document was not found")
            return self._revision_from_row(cursor, row, scope)

    def search_current_sections(
        self,
        scope: MemoryScope,
        terms: tuple[str, ...],
        limit: int,
        maximum_documents: int,
    ) -> tuple[KnowledgeDocumentSectionMatch, ...]:
        self._require_scope(scope)
        validate_knowledge_search(terms, limit, maximum_documents)
        with self._transaction(TeamOperation.READ) as cursor:
            revisions = self._current_revisions(cursor, scope, maximum_documents)
            return rank_knowledge_sections(revisions, terms, limit)

    def iter_current_sections(
        self, scope: MemoryScope, maximum_documents: int
    ) -> tuple[CurrentKnowledgeDocumentSection, ...]:
        self._require_scope(scope)
        if not 1 <= maximum_documents <= 128:
            raise KnowledgeDocumentConflict("knowledge document limit is invalid")
        with self._transaction(TeamOperation.READ) as cursor:
            revisions = self._current_revisions(cursor, scope, maximum_documents)
            return tuple(
                CurrentKnowledgeDocumentSection(revision, index, section)
                for revision in revisions
                for index, section in enumerate(revision.document.sections)
            )

    def list_current_section_embeddings(
        self, scope: MemoryScope, model_id: str, maximum_documents: int
    ) -> tuple[KnowledgeSectionEmbedding, ...]:
        self._require_scope(scope)
        if (
            not isinstance(model_id, str)
            or not model_id
            or len(model_id) > 256
            or any(character.isspace() for character in model_id)
            or not 1 <= maximum_documents <= 128
        ):
            raise KnowledgeDocumentConflict("knowledge embedding query is invalid")
        with self._transaction(TeamOperation.READ) as cursor:
            cursor.execute(
                "WITH selected_sources AS ("
                "SELECT current_revision_id FROM mnemo_team.knowledge_document_sources "
                "WHERE workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
                "AND owner_id = CAST(%s AS uuid) AND visibility = %s AND NOT is_deleted "
                "ORDER BY relative_path, document_id LIMIT %s"
                ") SELECT embedding.revision_id::text, embedding.section_index, "
                "embedding.model_id, embedding.section_digest, embedding.embedding::text "
                "FROM selected_sources AS selected "
                "JOIN mnemo_team.knowledge_section_embeddings AS embedding "
                "ON embedding.revision_id = selected.current_revision_id "
                "WHERE embedding.workspace_id = CAST(%s AS uuid) "
                "AND embedding.project_id = CAST(%s AS uuid) "
                "AND embedding.owner_id = CAST(%s AS uuid) AND embedding.visibility = %s "
                "AND embedding.model_id = %s "
                "ORDER BY embedding.revision_id, embedding.section_index",
                (
                    *self._scope_values(scope),
                    maximum_documents,
                    *self._scope_values(scope),
                    model_id,
                ),
            )
            return tuple(
                KnowledgeSectionEmbedding(
                    scope,
                    KnowledgeDocumentRevisionId.from_string(str(row[0])),
                    int(str(row[1])),
                    str(row[2]),
                    str(row[3]),
                    _parse_vector(row[4]),
                )
                for row in cursor.fetchall()
            )

    def store_section_embeddings(
        self, scope: MemoryScope, embeddings: tuple[KnowledgeSectionEmbedding, ...]
    ) -> None:
        self._require_scope(scope)
        if not embeddings:
            return
        keys = [(item.revision_id, item.section_index, item.model_id) for item in embeddings]
        if len(set(keys)) != len(keys) or any(item.scope != scope for item in embeddings):
            raise KnowledgeDocumentConflict("knowledge embeddings are invalid")
        with self._transaction(TeamOperation.CONTRIBUTE) as cursor:
            for item in embeddings:
                cursor.execute(
                    "SELECT 1 FROM mnemo_team.knowledge_document_sources AS source "
                    "JOIN mnemo_team.knowledge_document_sections AS section "
                    "ON section.workspace_id = source.workspace_id "
                    "AND section.revision_id = source.current_revision_id "
                    "WHERE source.workspace_id = CAST(%s AS uuid) "
                    "AND source.project_id = CAST(%s AS uuid) "
                    "AND source.owner_id = CAST(%s AS uuid) AND source.visibility = %s "
                    "AND source.current_revision_id = CAST(%s AS uuid) "
                    "AND section.section_index = %s AND NOT source.is_deleted",
                    (*self._scope_values(scope), str(item.revision_id), item.section_index),
                )
                if cursor.fetchone() is None:
                    raise KnowledgeDocumentConflict("knowledge embedding is not current")
                cursor.execute(
                    "INSERT INTO mnemo_team.knowledge_section_embeddings("
                    "workspace_id, project_id, owner_id, visibility, revision_id, "
                    "section_index, model_id, section_digest, embedding) "
                    "VALUES (CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, "
                    "CAST(%s AS uuid), %s, %s, %s, CAST(%s AS vector)) "
                    "ON CONFLICT(workspace_id, revision_id, section_index, model_id) "
                    "DO UPDATE SET section_digest = EXCLUDED.section_digest, "
                    "embedding = EXCLUDED.embedding",
                    (
                        *self._scope_values(scope),
                        str(item.revision_id),
                        item.section_index,
                        item.model_id,
                        item.section_digest,
                        _vector_text(item.vector),
                    ),
                )

    def apply_sync(
        self,
        scope: MemoryScope,
        revisions: tuple[KnowledgeDocumentRevision, ...],
        tombstones: tuple[KnowledgeDocumentTombstone, ...],
    ) -> KnowledgeDocumentSyncStoreResult:
        self._require_scope(scope)
        self._validate_sync(scope, revisions, tombstones)
        for revision in revisions:
            if not self._policy.assess(revision.document).accepted:
                raise KnowledgeDocumentSecretRejected(
                    "knowledge document was rejected by safety policy"
                )
        with self._transaction(TeamOperation.CONTRIBUTE) as cursor:
            for tombstone in tombstones:
                self._store_tombstone(cursor, scope, tombstone)
            for revision in revisions:
                self._store_revision(cursor, scope, revision)
            cursor.execute(
                "INSERT INTO mnemo_team.knowledge_sync_status("
                "workspace_id, project_id, owner_id, visibility, last_synced_at) "
                "VALUES (CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, %s) "
                "ON CONFLICT(workspace_id, project_id, owner_id, visibility) "
                "DO UPDATE SET last_synced_at = EXCLUDED.last_synced_at",
                (*self._scope_values(scope), datetime.now(UTC)),
            )
            active = tuple(
                self._known_document(row, scope)
                for row in self._active_document_rows(cursor, scope)
            )
            return KnowledgeDocumentSyncStoreResult(active, len(revisions), len(tombstones))

    @contextmanager
    def _transaction(self, operation: TeamOperation) -> Iterator[PostgreSQLCursor]:
        try:
            connection = self._connection_factory()
            connection.autocommit = False
        except Exception as error:
            raise KnowledgeDocumentStorageFailure("knowledge database connection failed") from error
        cursor = connection.cursor()
        try:
            cursor.execute(
                "SELECT set_config('mnemo.principal_id', %s, true), "
                "set_config('mnemo.workspace_id', %s, true), "
                "set_config('mnemo.operation', %s, true), "
                "set_config('statement_timeout', %s, true)",
                (
                    str(self._principal_id),
                    str(self._workspace_id),
                    operation.value,
                    str(self._statement_timeout_ms),
                ),
            )
            yield cursor
            connection.commit()
        except (
            InvalidKnowledgeDocumentScope,
            KnowledgeDocumentConflict,
            KnowledgeDocumentNotFound,
            KnowledgeDocumentSecretRejected,
            KnowledgeDocumentStorageFailure,
        ):
            connection.rollback()
            raise
        except Exception as error:
            connection.rollback()
            raise _translated_error(error) from error
        finally:
            cursor.close()
            connection.close()

    def _require_scope(self, scope: MemoryScope) -> None:
        if (
            not isinstance(scope, MemoryScope)
            or scope.level is not ScopeLevel.PROJECT
            or scope.workspace_id != self._workspace_id
            or scope.project_id is None
        ):
            raise InvalidKnowledgeDocumentScope("team knowledge requires an exact project scope")

    @staticmethod
    def _scope_values(scope: MemoryScope) -> tuple[str, str, str, str]:
        if scope.workspace_id is None or scope.project_id is None:
            raise InvalidKnowledgeDocumentScope("team knowledge requires an exact project scope")
        return (
            str(scope.workspace_id),
            str(scope.project_id),
            str(scope.owner_id),
            scope.visibility.value,
        )

    def _active_document_rows(
        self, cursor: PostgreSQLCursor, scope: MemoryScope
    ) -> Sequence[Sequence[object]]:
        cursor.execute(
            "SELECT source.document_id::text, source.relative_path, source.content_digest, "
            "source.current_revision_id::text, revision.revision_number "
            "FROM mnemo_team.knowledge_document_sources AS source "
            "JOIN mnemo_team.knowledge_document_revisions AS revision "
            "ON revision.workspace_id = source.workspace_id "
            "AND revision.revision_id = source.current_revision_id "
            "WHERE source.workspace_id = CAST(%s AS uuid) "
            "AND source.project_id = CAST(%s AS uuid) "
            "AND source.owner_id = CAST(%s AS uuid) AND source.visibility = %s "
            "AND NOT source.is_deleted ORDER BY source.relative_path, source.document_id",
            self._scope_values(scope),
        )
        return cursor.fetchall()

    def _current_revisions(
        self, cursor: PostgreSQLCursor, scope: MemoryScope, maximum_documents: int
    ) -> tuple[KnowledgeDocumentRevision, ...]:
        cursor.execute(
            "WITH selected_sources AS ("
            "SELECT workspace_id, current_revision_id "
            "FROM mnemo_team.knowledge_document_sources "
            "WHERE workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s AND NOT is_deleted "
            "ORDER BY relative_path, document_id LIMIT %s"
            ") SELECT " + _REVISION_COLUMNS + " FROM selected_sources AS selected "
            "JOIN mnemo_team.knowledge_document_revisions AS revision "
            "ON revision.workspace_id = selected.workspace_id "
            "AND revision.revision_id = selected.current_revision_id "
            "ORDER BY revision.relative_path, revision.document_id",
            (*self._scope_values(scope), maximum_documents),
        )
        return tuple(self._revision_from_row(cursor, row, scope) for row in cursor.fetchall())

    def _store_revision(
        self, cursor: PostgreSQLCursor, scope: MemoryScope, revision: KnowledgeDocumentRevision
    ) -> None:
        document = revision.document
        cursor.execute(
            "SELECT current_revision_id::text, is_deleted, relative_path, content_digest "
            "FROM mnemo_team.knowledge_document_sources "
            "WHERE workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND document_id = CAST(%s AS uuid) FOR UPDATE",
            (*self._scope_values(scope), str(document.document_id)),
        )
        existing = cursor.fetchone()
        if existing is None:
            if revision.revision_number != 1 or revision.predecessor_revision_id is not None:
                raise KnowledgeDocumentConflict("knowledge document creation revision conflicts")
            cursor.execute(
                "INSERT INTO mnemo_team.knowledge_document_sources("
                "workspace_id, project_id, owner_id, visibility, document_id, relative_path, "
                "content_digest, current_revision_id, is_deleted, created_at, deleted_at) "
                "VALUES (CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, "
                "CAST(%s AS uuid), %s, %s, NULL, false, %s, NULL)",
                (
                    *self._scope_values(scope),
                    str(document.document_id),
                    document.relative_path,
                    document.content_digest,
                    revision.created_at,
                ),
            )
        else:
            if bool(existing[1]) or str(existing[0]) != str(revision.predecessor_revision_id):
                raise KnowledgeDocumentConflict("knowledge document current revision conflicts")
            cursor.execute(
                "SELECT revision_number FROM mnemo_team.knowledge_document_revisions "
                "WHERE workspace_id = CAST(%s AS uuid) AND revision_id = CAST(%s AS uuid)",
                (str(self._workspace_id), str(revision.predecessor_revision_id)),
            )
            predecessor = cursor.fetchone()
            if predecessor is None or revision.revision_number != int(str(predecessor[0])) + 1:
                raise KnowledgeDocumentConflict("knowledge document predecessor conflicts")
        cursor.execute(
            "INSERT INTO mnemo_team.knowledge_document_revisions("
            "workspace_id, project_id, owner_id, visibility, revision_id, document_id, "
            "revision_number, predecessor_revision_id, source_kind, relative_path, "
            "content_digest, title, frontmatter_json, created_at) "
            "VALUES (CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, "
            "CAST(%s AS uuid), CAST(%s AS uuid), %s, CAST(%s AS uuid), %s, %s, %s, %s, "
            "CAST(%s AS jsonb), %s)",
            (
                *self._scope_values(scope),
                str(revision.revision_id),
                str(document.document_id),
                revision.revision_number,
                None
                if revision.predecessor_revision_id is None
                else str(revision.predecessor_revision_id),
                document.source_kind.value,
                document.relative_path,
                document.content_digest,
                document.title,
                json.dumps(document.frontmatter, separators=(",", ":")),
                revision.created_at,
            ),
        )
        for index, section in enumerate(document.sections):
            cursor.execute(
                "INSERT INTO mnemo_team.knowledge_document_sections("
                "workspace_id, project_id, owner_id, visibility, revision_id, section_index, "
                "heading, heading_level, content) "
                "VALUES (CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, "
                "CAST(%s AS uuid), %s, %s, %s, %s)",
                (
                    *self._scope_values(scope),
                    str(revision.revision_id),
                    index,
                    section.heading,
                    section.level,
                    section.content,
                ),
            )
        for link in document.links:
            cursor.execute(
                "INSERT INTO mnemo_team.knowledge_document_links("
                "workspace_id, project_id, owner_id, visibility, revision_id, "
                "link_target, link_kind) "
                "VALUES (CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, "
                "CAST(%s AS uuid), %s, %s)",
                (
                    *self._scope_values(scope),
                    str(revision.revision_id),
                    link.target,
                    link.kind,
                ),
            )
        cursor.execute(
            "UPDATE mnemo_team.knowledge_document_sources SET relative_path = %s, "
            "content_digest = %s, current_revision_id = CAST(%s AS uuid), "
            "is_deleted = false, deleted_at = NULL "
            "WHERE workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND document_id = CAST(%s AS uuid)",
            (
                document.relative_path,
                document.content_digest,
                str(revision.revision_id),
                *self._scope_values(scope),
                str(document.document_id),
            ),
        )
        if cursor.rowcount != 1:
            raise KnowledgeDocumentConflict("knowledge document activation conflicts")

    def _store_tombstone(
        self, cursor: PostgreSQLCursor, scope: MemoryScope, tombstone: KnowledgeDocumentTombstone
    ) -> None:
        cursor.execute(
            "SELECT current_revision_id::text, is_deleted, relative_path, content_digest "
            "FROM mnemo_team.knowledge_document_sources "
            "WHERE workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND document_id = CAST(%s AS uuid) FOR UPDATE",
            (*self._scope_values(scope), str(tombstone.document_id)),
        )
        current = cursor.fetchone()
        if (
            current is None
            or bool(current[1])
            or str(current[0]) != str(tombstone.expected_revision_id)
            or str(current[2]) != tombstone.relative_path
            or str(current[3]) != tombstone.content_digest
        ):
            raise KnowledgeDocumentConflict("knowledge document deletion conflicts")
        cursor.execute(
            "UPDATE mnemo_team.knowledge_document_sources SET current_revision_id = NULL, "
            "is_deleted = true, deleted_at = %s "
            "WHERE workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND document_id = CAST(%s AS uuid)",
            (
                tombstone.deleted_at,
                *self._scope_values(scope),
                str(tombstone.document_id),
            ),
        )
        if cursor.rowcount != 1:
            raise KnowledgeDocumentConflict("knowledge document deletion conflicts")
        cursor.execute(
            "INSERT INTO mnemo_team.knowledge_document_tombstones("
            "workspace_id, project_id, owner_id, visibility, document_id, relative_path, "
            "content_digest, deleted_at) "
            "VALUES (CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, "
            "CAST(%s AS uuid), %s, %s, %s) "
            "ON CONFLICT(workspace_id, document_id) DO UPDATE SET "
            "relative_path = EXCLUDED.relative_path, content_digest = EXCLUDED.content_digest, "
            "deleted_at = EXCLUDED.deleted_at",
            (
                *self._scope_values(scope),
                str(tombstone.document_id),
                tombstone.relative_path,
                tombstone.content_digest,
                tombstone.deleted_at,
            ),
        )
        cursor.execute(
            "SELECT revision_id::text FROM mnemo_team.knowledge_document_revisions "
            "WHERE workspace_id = CAST(%s AS uuid) AND document_id = CAST(%s AS uuid) "
            "ORDER BY revision_number DESC",
            (str(self._workspace_id), str(tombstone.document_id)),
        )
        for row in cursor.fetchall():
            cursor.execute(
                "DELETE FROM mnemo_team.knowledge_document_revisions "
                "WHERE workspace_id = CAST(%s AS uuid) AND revision_id = CAST(%s AS uuid)",
                (str(self._workspace_id), str(row[0])),
            )

    def _revision_from_row(
        self, cursor: PostgreSQLCursor, row: Sequence[object], scope: MemoryScope
    ) -> KnowledgeDocumentRevision:
        revision_id = KnowledgeDocumentRevisionId.from_string(str(row[0]))
        cursor.execute(
            "SELECT heading, heading_level, content "
            "FROM mnemo_team.knowledge_document_sections "
            "WHERE workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND revision_id = CAST(%s AS uuid) ORDER BY section_index",
            (*self._scope_values(scope), str(revision_id)),
        )
        sections = tuple(
            KnowledgeDocumentSection(str(item[0]), int(str(item[1])), str(item[2]))
            for item in cursor.fetchall()
        )
        cursor.execute(
            "SELECT link_target, link_kind FROM mnemo_team.knowledge_document_links "
            "WHERE workspace_id = CAST(%s AS uuid) AND project_id = CAST(%s AS uuid) "
            "AND owner_id = CAST(%s AS uuid) AND visibility = %s "
            "AND revision_id = CAST(%s AS uuid) ORDER BY link_kind, link_target",
            (*self._scope_values(scope), str(revision_id)),
        )
        links = tuple(
            KnowledgeDocumentLink(str(item[0]), str(item[1])) for item in cursor.fetchall()
        )
        frontmatter_value = json.loads(str(row[8]))
        if not isinstance(frontmatter_value, list) or any(
            not isinstance(item, list)
            or len(item) != 2
            or not all(isinstance(value, str) for value in item)
            for item in frontmatter_value
        ):
            raise KnowledgeDocumentStorageFailure("knowledge document stored payload is invalid")
        return KnowledgeDocumentRevision(
            revision_id,
            KnowledgeDocument(
                KnowledgeDocumentId.from_string(str(row[1])),
                scope,
                str(row[5]),
                KnowledgeDocumentSourceKind(str(row[4])),
                str(row[6]),
                str(row[7]),
                tuple((item[0], item[1]) for item in frontmatter_value),
                sections,
                links,
            ),
            int(str(row[2])),
            None if row[3] is None else KnowledgeDocumentRevisionId.from_string(str(row[3])),
            cast(datetime, row[9]),
        )

    @staticmethod
    def _known_document(row: Sequence[object], scope: MemoryScope) -> KnownKnowledgeDocument:
        return KnownKnowledgeDocument(
            KnowledgeDocumentId.from_string(str(row[0])),
            scope,
            str(row[1]),
            str(row[2]),
            KnowledgeDocumentRevisionId.from_string(str(row[3])),
            int(str(row[4])),
        )

    @staticmethod
    def _validate_sync(
        scope: MemoryScope,
        revisions: tuple[KnowledgeDocumentRevision, ...],
        tombstones: tuple[KnowledgeDocumentTombstone, ...],
    ) -> None:
        if any(item.document.scope != scope for item in revisions) or any(
            item.scope != scope for item in tombstones
        ):
            raise InvalidKnowledgeDocumentScope("knowledge document scope is invalid")
        revision_ids = [item.document.document_id for item in revisions]
        tombstone_ids = [item.document_id for item in tombstones]
        if (
            len(set(revision_ids)) != len(revision_ids)
            or len(set(tombstone_ids)) != len(tombstone_ids)
            or set(revision_ids) & set(tombstone_ids)
        ):
            raise KnowledgeDocumentConflict("knowledge sync contains conflicting document actions")
