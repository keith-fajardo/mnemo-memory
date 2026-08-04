"""Immutable, scoped metadata for checked-in project procedures.

Procedures are literal Markdown evidence selected by explicit tags.  They are never executable
code, model instructions, or authority to bypass Mnemo policy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .knowledge import KnowledgeDocumentRevision, KnowledgeDocumentSourceKind

_TAG = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def normalize_procedure_tags(tags: tuple[str, ...]) -> tuple[str, ...]:
    """Return bounded, canonical explicit applicability tags.

    Tags are intentionally simple literals.  Mnemo does not infer a procedure from prose, a
    prompt, an agent name, or a source file.
    """
    if not isinstance(tags, tuple) or not 1 <= len(tags) <= 8:
        raise ValueError("procedure tags must contain between 1 and 8 values")
    normalized: list[str] = []
    for tag in tags:
        if not isinstance(tag, str):
            raise TypeError("procedure tag must be a string")
        value = tag.strip().casefold()
        if not _TAG.fullmatch(value):
            raise ValueError("procedure tag is invalid")
        normalized.append(value)
    if len(set(normalized)) != len(normalized):
        raise ValueError("procedure tags must be unique")
    return tuple(sorted(normalized))


@dataclass(frozen=True, slots=True)
class ProjectProcedure:
    """One current checked-in Markdown revision eligible for explicit procedure retrieval."""

    revision: KnowledgeDocumentRevision
    tags: tuple[str, ...]
    mandatory: bool

    def __post_init__(self) -> None:
        if self.revision.document.source_kind is not KnowledgeDocumentSourceKind.MARKDOWN:
            raise ValueError("procedures require checked-in Markdown")
        object.__setattr__(self, "tags", normalize_procedure_tags(self.tags))
        if not isinstance(self.mandatory, bool):
            raise TypeError("procedure mandatory flag must be a boolean")
