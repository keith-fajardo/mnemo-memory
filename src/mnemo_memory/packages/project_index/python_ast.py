"""Backward-compatible Python-only facade for the general source parser."""

from __future__ import annotations

from .source_structure import (
    SourceStructureError,
    SourceStructureLimits,
    SourceStructureParser,
    SourceStructureParseRequest,
)

PythonSourceStructureError = SourceStructureError
PythonSourceLimits = SourceStructureLimits
PythonSourceParseRequest = SourceStructureParseRequest


class PythonSourceParser(SourceStructureParser):
    """Parse only Python files for callers that intentionally need that subset."""

    def __init__(self) -> None:
        super().__init__(languages=frozenset({"python"}))
