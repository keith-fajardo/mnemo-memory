"""Pure deterministic comparison of transient candidates with semantic memory."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass

from mnemo_memory.packages.domain import (
    SemanticAtomKind,
    SemanticAtomStatus,
    SemanticMemoryAtom,
)

_FIELD_VALUE = re.compile(r"^([a-z][a-z0-9_]{0,63})=(\S(?:.*\S)?)$", re.DOTALL)
_FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_VERIFY_KINDS = frozenset({SemanticAtomKind.CONSTRAINT, SemanticAtomKind.DECISION})
_CONSISTENCY_NOTE = "Consistency check only; not approval"


@dataclass(frozen=True, slots=True)
class SemanticVerificationViolation:
    """One exact candidate literal that conflicts with one authoritative memory literal."""

    field: str
    candidate_value: str
    remembered_value: str
    memory_kind: SemanticAtomKind
    memory_atom_id: str
    memory_confidence: float

    def to_dict(self) -> dict[str, object]:
        return {
            "field": self.field,
            "candidate_value": self.candidate_value,
            "remembered_value": self.remembered_value,
            "memory_kind": self.memory_kind.value,
            "memory_atom_id": self.memory_atom_id,
            "memory_confidence": self.memory_confidence,
        }


@dataclass(frozen=True, slots=True)
class SemanticVerificationReport:
    """Bounded untrusted consistency evidence; never authorization or approval."""

    status: str
    violations: tuple[SemanticVerificationViolation, ...]
    violation_count: int
    truncated: bool
    unverifiable_fields: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "content_representation": "untrusted_evidence",
            "status": self.status,
            "violations": [violation.to_dict() for violation in self.violations],
            "violation_count": self.violation_count,
            "truncated": self.truncated,
            "unverifiable_fields": list(self.unverifiable_fields),
            "note": _CONSISTENCY_NOTE,
        }


def verify_candidate_against_memory(
    atoms: tuple[SemanticMemoryAtom, ...],
    candidate: Mapping[str, object],
    *,
    maximum_mismatches: int = 16,
) -> SemanticVerificationReport:
    """Compare agent-named scalar fields without inferring from prose or prior guesses."""

    if (
        isinstance(maximum_mismatches, bool)
        or not isinstance(maximum_mismatches, int)
        or not 1 <= maximum_mismatches <= 32
    ):
        raise ValueError("maximum_mismatches must be between 1 and 32")
    if not isinstance(candidate, Mapping):
        raise TypeError("candidate must be an object")
    if len(candidate) > 64:
        raise ValueError("candidate must contain at most 64 fields")
    fields = tuple(candidate)
    if any(not isinstance(field, str) or _FIELD_NAME.fullmatch(field) is None for field in fields):
        raise ValueError("candidate field names must use lowercase snake_case")

    predicates: dict[str, list[tuple[str, SemanticMemoryAtom]]] = {}
    for atom in atoms:
        if atom.status is not SemanticAtomStatus.ACTIVE or not _is_authoritative(atom):
            continue
        for field, remembered_value in _structured_predicates(atom.object_value):
            predicates.setdefault(field, []).append((remembered_value, atom))

    violations: list[SemanticVerificationViolation] = []
    unverifiable: list[str] = []
    for field in sorted(fields):
        candidate_value = _scalar_literal(candidate[field])
        remembered = predicates.get(field, [])
        remembered_values = {value for value, _ in remembered}
        if candidate_value is None or len(remembered_values) != 1:
            unverifiable.append(field)
            continue
        remembered_value = next(iter(remembered_values))
        if candidate_value == remembered_value:
            continue
        source = min(
            (atom for value, atom in remembered if value == remembered_value),
            key=lambda atom: str(atom.atom_id),
        )
        violations.append(
            SemanticVerificationViolation(
                field,
                candidate_value,
                remembered_value,
                source.kind,
                str(source.atom_id),
                source.confidence,
            )
        )

    violation_count = len(violations)
    bounded = tuple(violations[:maximum_mismatches])
    status = "mismatch" if violations else "unverifiable" if unverifiable else "consistent"
    return SemanticVerificationReport(
        status,
        bounded,
        violation_count,
        violation_count > len(bounded),
        tuple(unverifiable),
    )


def _is_authoritative(atom: SemanticMemoryAtom) -> bool:
    return atom.kind in _VERIFY_KINDS or dict(atom.qualifiers).get("authority_boundary") == "true"


def _scalar_literal(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and math.isfinite(value):
        return json.dumps(value, allow_nan=False, separators=(",", ":"))
    return None


def _structured_predicates(value: str) -> tuple[tuple[str, str], ...]:
    parts = value.split(" ; ")
    predicates: list[tuple[str, str]] = []
    for part in parts:
        match = _FIELD_VALUE.fullmatch(part)
        if match is None:
            return ()
        predicates.append((match.group(1), match.group(2)))
    return tuple(predicates)
