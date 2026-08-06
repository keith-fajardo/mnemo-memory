"""Validate one independent, revision-pinned team security review artifact."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Final

_REVISION: Final = re.compile(r"^[0-9a-f]{40}$")
_SEVERITIES: Final = ("critical", "high", "medium", "low", "info")
_STATUSES: Final = ("open", "resolved", "accepted_risk")
_TOP_LEVEL_KEYS: Final = {
    "schema_version",
    "review_scope",
    "reviewed_revision",
    "reviewed_at",
    "reviewer_name",
    "reviewer_organization",
    "independence_attested",
    "methodology",
    "findings",
}
_FINDING_KEYS: Final = {"finding_id", "severity", "status", "summary"}


class SecurityReviewInvalid(ValueError):
    """The review artifact is absent, malformed, or not pinned to this candidate."""


class SecurityReviewBlocked(RuntimeError):
    """The review has at least one unresolved critical or high finding."""


def validate_review(document: dict[str, object], *, expected_revision: str) -> dict[str, object]:
    if set(document) != _TOP_LEVEL_KEYS:
        raise SecurityReviewInvalid("review fields are invalid")
    if document["schema_version"] != "1.0" or document["review_scope"] != "mnemo-team-v1":
        raise SecurityReviewInvalid("review contract is unsupported")
    if not _REVISION.fullmatch(expected_revision):
        raise SecurityReviewInvalid("expected revision is invalid")
    if document["reviewed_revision"] != expected_revision:
        raise SecurityReviewInvalid("review does not match the candidate revision")
    try:
        reviewed_at = datetime.fromisoformat(str(document["reviewed_at"]).replace("Z", "+00:00"))
    except ValueError as error:
        raise SecurityReviewInvalid("reviewed_at is invalid") from error
    if reviewed_at.tzinfo is None:
        raise SecurityReviewInvalid("reviewed_at must include an offset")
    for key in ("reviewer_name", "reviewer_organization"):
        value = document[key]
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            raise SecurityReviewInvalid(f"{key} is invalid")
    if document["independence_attested"] is not True:
        raise SecurityReviewInvalid("review independence is not attested")
    methodology = document["methodology"]
    if (
        not isinstance(methodology, list)
        or not methodology
        or any(not isinstance(item, str) or not item.strip() for item in methodology)
    ):
        raise SecurityReviewInvalid("review methodology is invalid")
    findings = document["findings"]
    if not isinstance(findings, list):
        raise SecurityReviewInvalid("findings must be an array")
    identities: set[str] = set()
    counts: Counter[str] = Counter()
    blocking = 0
    for finding in findings:
        if not isinstance(finding, dict) or set(finding) != _FINDING_KEYS:
            raise SecurityReviewInvalid("finding fields are invalid")
        finding_id = finding["finding_id"]
        severity = finding["severity"]
        status = finding["status"]
        summary = finding["summary"]
        if not isinstance(finding_id, str) or not finding_id.strip() or finding_id in identities:
            raise SecurityReviewInvalid("finding identity is invalid")
        if severity not in _SEVERITIES or status not in _STATUSES:
            raise SecurityReviewInvalid("finding severity or status is invalid")
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 500:
            raise SecurityReviewInvalid("finding summary is invalid")
        identities.add(finding_id)
        counts[f"{severity}:{status}"] += 1
        if severity in {"critical", "high"} and status != "resolved":
            blocking += 1
    if blocking:
        raise SecurityReviewBlocked("critical or high findings remain unresolved")
    return {
        "finding_counts": dict(sorted(counts.items())),
        "review_scope": "mnemo-team-v1",
        "reviewed_revision": expected_revision,
        "schema_version": "1.0",
        "unresolved_critical_or_high": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review-file", type=Path, required=True)
    parser.add_argument("--expected-revision", required=True)
    arguments = parser.parse_args()
    try:
        with arguments.review_file.open("rb") as handle:
            document = tomllib.load(handle)
        result = validate_review(document, expected_revision=arguments.expected_revision)
    except SecurityReviewBlocked:
        print("MNEMO_TEAM_SECURITY_REVIEW_BLOCKED", file=sys.stderr)
        raise SystemExit(1) from None
    except (OSError, tomllib.TOMLDecodeError, SecurityReviewInvalid):
        print("MNEMO_TEAM_SECURITY_REVIEW_INVALID", file=sys.stderr)
        raise SystemExit(2) from None
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
