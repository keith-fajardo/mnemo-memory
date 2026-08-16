"""Deterministic candidate-versus-memory consistency checks."""

from __future__ import annotations

from datetime import UTC, datetime

from mnemo_memory.packages.application.semantic_verification import (
    verify_candidate_against_memory,
)
from mnemo_memory.packages.domain import (
    EventId,
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    SemanticAtomKind,
    SemanticMemoryAtom,
    SessionId,
    TaskId,
    Visibility,
    WorkspaceId,
)

NOW = datetime(2026, 8, 16, 9, 0, tzinfo=UTC)


def _scope() -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string("10000000-0000-4000-8000-000000000001"),
        ScopeLevel.TASK,
        Visibility.PROJECT,
        WorkspaceId.from_string("20000000-0000-4000-8000-000000000001"),
        ProjectId.from_string("30000000-0000-4000-8000-000000000001"),
        SessionId.from_string("40000000-0000-4000-8000-000000000001"),
        TaskId.from_string("50000000-0000-4000-8000-000000000001"),
    )


def _atom(
    seed: int,
    object_value: str,
    *,
    kind: SemanticAtomKind = SemanticAtomKind.CONSTRAINT,
    qualifiers: tuple[tuple[str, str], ...] = (),
    confidence: float = 0.9,
) -> SemanticMemoryAtom:
    return SemanticMemoryAtom.create(
        scope=_scope(),
        kind=kind,
        subject="user",
        predicate="requires",
        object_value=object_value,
        source_event_ids=(EventId.from_string(f"60000000-0000-4000-8000-{seed:012d}"),),
        created_at=NOW,
        qualifiers=qualifiers,
        confidence=confidence,
        priority=100,
    )


def test_exact_structured_memory_match_is_consistent() -> None:
    report = verify_candidate_against_memory(
        (_atom(1, "timezone_mode=iana"),),
        {"timezone_mode": "iana"},
    )

    assert report.to_dict() == {
        "content_representation": "untrusted_evidence",
        "status": "consistent",
        "violations": [],
        "violation_count": 0,
        "truncated": False,
        "unverifiable_fields": [],
        "note": "Consistency check only; not approval",
    }


def test_mismatches_are_deterministically_sorted_and_capped() -> None:
    report = verify_candidate_against_memory(
        (
            _atom(2, "timezone_mode=iana"),
            _atom(1, "conflict_status=409", kind=SemanticAtomKind.DECISION),
        ),
        {"timezone_mode": "offset", "conflict_status": 500},
        maximum_mismatches=1,
    )

    value = report.to_dict()
    assert value["status"] == "mismatch"
    assert value["violation_count"] == 2
    assert value["truncated"] is True
    assert value["violations"] == [
        {
            "field": "conflict_status",
            "candidate_value": "500",
            "remembered_value": "409",
            "memory_kind": "decision",
            "memory_atom_id": str(
                _atom(1, "conflict_status=409", kind=SemanticAtomKind.DECISION).atom_id
            ),
            "memory_confidence": 0.9,
        }
    ]


def test_prior_guess_and_unstructured_prose_are_unverifiable() -> None:
    report = verify_candidate_against_memory(
        (
            _atom(1, "timezone_mode=offset", kind=SemanticAtomKind.INFERENCE),
            _atom(2, "Use an IANA timezone.", kind=SemanticAtomKind.CONSTRAINT),
        ),
        {"timezone_mode": "iana"},
    )

    assert report.status == "unverifiable"
    assert report.violations == ()
    assert report.unverifiable_fields == ("timezone_mode",)


def test_conflicting_authoritative_literals_never_choose_a_memory() -> None:
    report = verify_candidate_against_memory(
        (
            _atom(1, "timezone_mode=iana"),
            _atom(2, "timezone_mode=offset", kind=SemanticAtomKind.DECISION),
        ),
        {"timezone_mode": "offset"},
    )

    assert report.status == "unverifiable"
    assert report.violations == ()
    assert report.unverifiable_fields == ("timezone_mode",)


def test_checkpoint_grouped_structured_constraints_are_compared_individually() -> None:
    report = verify_candidate_against_memory(
        (
            _atom(
                1,
                "authorization_role=scheduler ; authorize_before_lookup=true",
            ),
        ),
        {"authorization_role": "viewer", "authorize_before_lookup": True},
    )

    assert report.status == "mismatch"
    assert [violation.field for violation in report.violations] == ["authorization_role"]


def test_reconcile_copies_only_agent_named_high_confidence_remembered_literals() -> None:
    report = verify_candidate_against_memory(
        (
            _atom(1, "timezone_mode=iana"),
            _atom(2, "conflict_status=409"),
        ),
        {"timezone_mode": "offset", "unchanged_note": "agent-owned"},
        reconcile=True,
    )

    reconciled = report.to_dict()["reconciled_candidate"]
    assert isinstance(reconciled, dict)
    assert reconciled == {
        "timezone_mode": "iana",
        "unchanged_note": "agent-owned",
    }
    assert report.to_dict()["reconciled_fields"] == ["timezone_mode"]
    assert "conflict_status" not in reconciled


def test_reconcile_abstains_below_the_high_confidence_floor() -> None:
    report = verify_candidate_against_memory(
        (_atom(1, "timezone_mode=iana", confidence=0.89),),
        {"timezone_mode": "offset"},
        reconcile=True,
    )

    assert report.status == "mismatch"
    assert report.to_dict()["reconciled_candidate"] == {"timezone_mode": "offset"}
    assert report.to_dict()["reconciled_fields"] == []
