"""Deterministic checked-in-procedure selection over immutable knowledge revisions.

This component never reads the working tree, executes a document, or infers applicability from
free text. A composition root supplies a scoped knowledge repository after explicit source sync.
"""

from __future__ import annotations

from mnemo_memory.packages.domain import (
    KnowledgeDocumentSourceKind,
    MemoryScope,
    ProjectProcedure,
    ScopeLevel,
    normalize_procedure_tags,
)
from mnemo_memory.packages.storage import KnowledgeDocumentRepository

_KIND = "mnemo_kind"
_TAGS = "mnemo_tags"
_MANDATORY = "mnemo_mandatory"


class KnowledgeDocumentProcedureRegistry:
    """Expose only explicitly tagged, checked-in procedure document revisions.

    Frontmatter is intentionally a narrow scalar contract::

        mnemo_kind: procedure
        mnemo_tags: reconciliation, dbt
        mnemo_mandatory: true

    Missing or malformed procedure frontmatter makes a document ineligible; it never becomes a
    best-effort instruction. The underlying document remains untrusted evidence in a packet.
    """

    def __init__(self, documents: KnowledgeDocumentRepository) -> None:
        self._documents = documents

    def find_current_procedures(
        self, scope: MemoryScope, tags: tuple[str, ...], maximum_procedures: int
    ) -> tuple[ProjectProcedure, ...]:
        if scope.level is not ScopeLevel.PROJECT:
            raise ValueError("procedures require an explicit project scope")
        expected_tags = normalize_procedure_tags(tags)
        if not 1 <= maximum_procedures <= 8:
            raise ValueError("procedure selection limit must be between 1 and 8")
        candidates: list[ProjectProcedure] = []
        for known in self._documents.list_active_documents(scope):
            revision = self._documents.get_current_revision(scope, known.document_id)
            document = revision.document
            if document.source_kind is not KnowledgeDocumentSourceKind.MARKDOWN:
                continue
            values = dict(document.frontmatter)
            if values.get(_KIND) != "procedure":
                continue
            try:
                procedure_tags = _parse_tags(values.get(_TAGS))
                mandatory = _parse_mandatory(values.get(_MANDATORY))
            except ValueError:
                continue
            if not set(expected_tags).intersection(procedure_tags):
                continue
            candidates.append(ProjectProcedure(revision, procedure_tags, mandatory))
        return tuple(
            sorted(
                candidates,
                key=lambda procedure: (
                    0 if procedure.mandatory else 1,
                    procedure.revision.document.relative_path,
                    str(procedure.revision.revision_id),
                ),
            )[:maximum_procedures]
        )


def _parse_tags(value: str | None) -> tuple[str, ...]:
    if value is None:
        raise ValueError("procedure tags are required")
    parts = tuple(item.strip() for item in value.split(","))
    return normalize_procedure_tags(parts)


def _parse_mandatory(value: str | None) -> bool:
    if value is None:
        return False
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError("procedure mandatory flag is invalid")
