"""Independent team security review acceptance stays strict and revision-pinned."""

from __future__ import annotations

from copy import deepcopy

import pytest

from scripts.check_team_security_review import (
    SecurityReviewBlocked,
    SecurityReviewInvalid,
    validate_review,
)

REVISION = "a" * 40


def _review() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "review_scope": "mnemo-team-v1",
        "reviewed_revision": REVISION,
        "reviewed_at": "2026-08-06T12:00:00+08:00",
        "reviewer_name": "Independent Reviewer",
        "reviewer_organization": "External Security Practice",
        "independence_attested": True,
        "methodology": ["source review", "adversarial test review"],
        "findings": [],
    }


def test_review_accepts_exact_revision_with_no_unresolved_high_finding() -> None:
    review = _review()
    review["findings"] = [
        {
            "finding_id": "MNEMO-SEC-001",
            "severity": "high",
            "status": "resolved",
            "summary": "Resolution was verified against the candidate revision.",
        },
        {
            "finding_id": "MNEMO-SEC-002",
            "severity": "medium",
            "status": "open",
            "summary": "A non-release-blocking hardening item remains documented.",
        },
    ]

    result = validate_review(review, expected_revision=REVISION)

    assert result["unresolved_critical_or_high"] == 0
    assert result["finding_counts"] == {"high:resolved": 1, "medium:open": 1}


@pytest.mark.parametrize("status", ["open", "accepted_risk"])
def test_review_rejects_unresolved_high_or_critical(status: str) -> None:
    review = _review()
    review["findings"] = [
        {
            "finding_id": "MNEMO-SEC-003",
            "severity": "critical",
            "status": status,
            "summary": "The release-blocking condition remains.",
        }
    ]

    with pytest.raises(SecurityReviewBlocked):
        validate_review(review, expected_revision=REVISION)


def test_review_rejects_revision_mismatch_or_false_independence() -> None:
    with pytest.raises(SecurityReviewInvalid, match="candidate revision"):
        validate_review(_review(), expected_revision="b" * 40)

    review = deepcopy(_review())
    review["independence_attested"] = False
    with pytest.raises(SecurityReviewInvalid, match="independence"):
        validate_review(review, expected_revision=REVISION)
