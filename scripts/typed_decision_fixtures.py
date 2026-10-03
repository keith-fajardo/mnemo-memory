"""Synthetic typed-decision fixtures: paths, the provenance-checked loader and the note sets.

Light by design: it imports nothing of the typed step or the model gateway, so a replay child can
read its case from a fixture without loading ``asyncio`` before the timed hook call.
``scripts.typed_decision_evaluation`` re-exports every name here.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).parents[1]
_FIXTURES = REPOSITORY_ROOT / "tests/fixtures/evals"
ROUTING_FIXTURE = _FIXTURES / "automatic-context-routing-v1.json"
VIABILITY_FIXTURE = _FIXTURES / "viability-corpus-v1.json"
TYPED_DECISION_FIXTURE = _FIXTURES / "typed-decision-v1.json"
HOLDOUT_FIXTURE = _FIXTURES / "typed-decision-holdout-v1.json"
TELEHEALTH_FIXTURE = _FIXTURES / "telehealth-long-horizon-phase2-qwen25coder7b.json"

_SYNTHETIC_PROVENANCE: tuple[object, ...] = (
    {
        "origin": "Mnemo-owned original synthetic prompts",
        "competing_product_artifacts_used": False,
    },
    "Original synthetic Mnemo evaluation data; no production, personal, secret, or competitor "
    "content.",
)

NOISE_SUMMARY = (
    "Background conversation {index:04d} for synthetic workflow {template}; it is unrelated "
    "and must not displace active task state."
)


class FixtureProvenanceError(ValueError):
    """A fixture does not declare synthetic provenance, so it may not be sent to Jev."""


def load_synthetic_fixture(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("provenance") not in _SYNTHETIC_PROVENANCE:
        raise FixtureProvenanceError(path.name)
    return value


def relevance_notes() -> dict[str, list[tuple[str, str, str]]]:
    """The dev filler-check notes per viability template, as ``(note ID, summary, category)``.

    Each template's events are ``relevant`` or ``superseded``; two generated ``noise`` notes
    follow. Note IDs are unique across templates.
    """

    notes: dict[str, list[tuple[str, str, str]]] = {}
    for template in load_synthetic_fixture(VIABILITY_FIXTURE)["templates"]:
        template_id = template["template_id"]
        relevant = set(template["ground_truth"]["relevant_evidence"])
        notes[template_id] = [
            (
                event["event_key"],
                event["summary"],
                "relevant" if event["event_key"] in relevant else "superseded",
            )
            for event in template["events"]
        ] + [
            (
                f"noise-{template_id}-{index}",
                NOISE_SUMMARY.format(index=index, template=template_id),
                "noise",
            )
            for index in (1, 2)
        ]
    return notes


def share(numerator: int, denominator: int, *, empty: float = 0.0) -> float:
    """``numerator / denominator``, or ``empty`` when there is nothing to divide by."""

    return numerator / denominator if denominator else empty


def nearest_rank(ordered: Sequence[int], quantile: float) -> int | None:
    """The nearest-rank percentile of already sorted values, or ``None`` for none."""

    if not ordered:
        return None
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]
