"""Phase-1 typed-decision evaluation over synthetic fixtures (spec §8).

This module never opens a network connection itself. Callers pass a guarded classifier whose
source is ``synthetic_fixture``, and only fixtures that declare synthetic provenance are read.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from mnemo_memory.packages.domain import TypedDecisionUnavailableReason
from mnemo_memory.packages.model_gateway.cascade_router import (
    AxisRoutedClassifier,
    ClassifierResult,
)
from mnemo_memory.packages.model_gateway.decision_axes import (
    COMPLEXITY,
    EPISODIC_KIND,
    FRONT_DOOR_AXES,
    TOOL_NEED,
    WORTH_REMEMBERING,
    NeedAnswer,
    accepted_choice,
    hint_eligible,
    need_from_result,
    relevance_axis,
    should_drop_candidate,
    tier_committee,
    worth_extracting,
)
from mnemo_memory.packages.model_gateway.episodic_extraction import parse_episodic_output
from mnemo_memory.packages.model_gateway.rule_axes import RISK_AXIS, RiskTermClassifier
from mnemo_memory.packages.model_gateway.typed_decisions import (
    GuardedTypedDecisionClassifier,
    decide_tier,
)

REPOSITORY_ROOT = Path(__file__).parents[1]
_FIXTURES = REPOSITORY_ROOT / "tests/fixtures/evals"
ROUTING_FIXTURE = _FIXTURES / "automatic-context-routing-v1.json"
VIABILITY_FIXTURE = _FIXTURES / "viability-corpus-v1.json"
TYPED_DECISION_FIXTURE = _FIXTURES / "typed-decision-v1.json"
TELEHEALTH_FIXTURE = _FIXTURES / "telehealth-long-horizon-phase2-qwen25coder7b.json"

LATENCY_DEADLINE_MS = 600
LATENCY_MINIMUM_SAMPLES = 50
LATENCY_MAXIMUM_SHARE_OVER = 0.05
RELEVANCE_BATCH_SIZE = 8
_SYNTHETIC_PROVENANCE: tuple[object, ...] = (
    {
        "origin": "Mnemo-owned original synthetic prompts",
        "competing_product_artifacts_used": False,
    },
    "Original synthetic Mnemo evaluation data; no production, personal, secret, or competitor "
    "content.",
)
_EXPECTED_NEED = {
    "prior_memory": "long_term",
    "knowledge": "long_term",
    "structure": "structure",
    "none": "none",
}
_EPISODIC_KIND_BY_PREFIX = {"decision": "decision", "failure": "failure", "result": "outcome"}
_NOISE_SUMMARY = (
    "Background conversation {index:04d} for synthetic workflow {template}; it is unrelated "
    "and must not displace active task state."
)
_MEASURED_OUTCOMES = (None, TypedDecisionUnavailableReason.TIMEOUT.value)
_SECTIONS = ("front_door", "latency", "relevance", "extraction", "tier")


class FixtureProvenanceError(ValueError):
    """A fixture does not declare synthetic provenance, so it may not be sent to Jev."""


class EpisodicProvider(Protocol):
    def generate(self, request: object) -> object: ...


@dataclass(frozen=True, slots=True)
class FrontDoorRow:
    case_id: str
    expected_route: str
    structure: NeedAnswer
    long_term: NeedAnswer
    duration_ms: int
    unavailable_reason: str | None


@dataclass(frozen=True, slots=True)
class RelevanceRow:
    template_id: str
    candidate_id: str
    category: str  # "relevant", "superseded" or "noise"
    dropped: bool


@dataclass(frozen=True, slots=True)
class ExtractionRow:
    arm: str  # "jev" or "ollama"
    event_id: str
    expected_worth: bool
    expected_kind: str | None
    predicted_worth: bool
    predicted_kind: str | None


@dataclass(frozen=True, slots=True)
class TierRow:
    case_id: str
    expected_tier: str
    expected_tool_need: str
    route: str
    reason: str
    tool_need: str | None
    hint: bool


@dataclass(frozen=True, slots=True)
class _OllamaRequest:
    summary: str
    max_candidates: int = 4


def load_synthetic_fixture(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("provenance") not in _SYNTHETIC_PROVENANCE:
        raise FixtureProvenanceError(path.name)
    return value


def derived_route(structure: NeedAnswer, long_term: NeedAnswer) -> str:
    if structure is NeedAnswer.YES:
        return "structure"
    if long_term is NeedAnswer.YES:
        return "long_term"
    if structure is NeedAnswer.NO and long_term is NeedAnswer.NO:
        return "none"
    return "unknown"


def score_front_door(rows: Sequence[FrontDoorRow]) -> dict[str, Any]:
    prior = [row for row in rows if row.expected_route == "prior_memory"]
    structure = [row for row in rows if row.expected_route == "structure"]
    derived = {row.case_id: derived_route(row.structure, row.long_term) for row in rows}
    predicted_none = [row for row in rows if derived[row.case_id] == "none"]
    missed = sorted(
        row.case_id for row in rows if derived[row.case_id] != _EXPECTED_NEED[row.expected_route]
    )
    accuracy = _share(len(rows) - len(missed), len(rows))
    prior_recall = _share(sum(row.long_term is NeedAnswer.YES for row in prior), len(prior))
    structure_recall = _share(
        sum(row.structure is NeedAnswer.YES for row in structure), len(structure)
    )
    none_precision = _share(
        sum(row.expected_route == "none" for row in predicted_none), len(predicted_none), empty=1.0
    )
    return {
        "cases": len(rows),
        "unavailable": sum(row.unavailable_reason is not None for row in rows),
        "accuracy": accuracy,
        "prior_memory_recall": prior_recall,
        "structure_recall": structure_recall,
        "none_predicted": len(predicted_none),
        "none_precision": none_precision,
        "missed_case_ids": missed,
        "gates": {
            "accuracy": accuracy >= 0.80,
            "prior_memory_recall": prior_recall >= 0.90,
            "structure_recall": structure_recall >= 0.80,
            "none_precision": none_precision == 1.0,
        },
    }


def score_latency(durations_ms: Sequence[int]) -> dict[str, Any]:
    ordered = sorted(durations_ms)
    share_over = _share(
        sum(value > LATENCY_DEADLINE_MS for value in ordered), len(ordered), empty=1.0
    )
    return {
        "samples": len(ordered),
        "p50_ms": _nearest_rank(ordered, 0.50),
        "p95_ms": _nearest_rank(ordered, 0.95),
        "max_ms": ordered[-1] if ordered else None,
        "deadline_ms": LATENCY_DEADLINE_MS,
        "share_over_deadline": share_over,
        "gates": {
            "samples": len(ordered) >= LATENCY_MINIMUM_SAMPLES,
            "share_over_deadline": share_over <= LATENCY_MAXIMUM_SHARE_OVER,
        },
    }


def score_relevance(rows: Sequence[RelevanceRow]) -> dict[str, Any]:
    relevant = [row for row in rows if row.category == "relevant"]
    dropped = sum(row.dropped for row in relevant)
    return {
        "candidates": len(rows),
        "relevant_dropped": dropped,
        "noise_drop_rate": _drop_rate(rows, "noise"),
        "superseded_drop_rate": _drop_rate(rows, "superseded"),
        "gates": {"relevant_dropped": bool(relevant) and dropped == 0},
    }


def score_extraction(rows: Sequence[ExtractionRow]) -> dict[str, Any]:
    arms: dict[str, dict[str, Any]] = {}
    for arm in ("jev", "ollama"):
        arm_rows = [row for row in rows if row.arm == arm]
        if not arm_rows:
            continue
        kinded = [row for row in arm_rows if row.expected_kind is not None]
        arms[arm] = {
            "events": len(arm_rows),
            "worth_accuracy": _share(
                sum(row.predicted_worth == row.expected_worth for row in arm_rows), len(arm_rows)
            ),
            "kind_accuracy": _share(
                sum(row.predicted_kind == row.expected_kind for row in kinded), len(kinded)
            ),
        }
    if "jev" not in arms or "ollama" not in arms:
        return {"arms": arms, "gates": {"baseline": "not_evaluated"}}
    return {
        "arms": arms,
        "gates": {
            "worth_accuracy": arms["jev"]["worth_accuracy"] >= arms["ollama"]["worth_accuracy"],
            "kind_accuracy": arms["jev"]["kind_accuracy"] >= arms["ollama"]["kind_accuracy"],
        },
    }


def score_tier(rows: Sequence[TierRow]) -> dict[str, Any]:
    heavy = [row for row in rows if row.expected_tier == "heavy"]
    light = [row for row in rows if row.expected_tier == "light"]
    heavy_recall = _share(sum(row.route == "heavy" for row in heavy), len(heavy))
    return {
        "cases": len(rows),
        "heavy_recall": heavy_recall,
        "light_recall": _share(sum(row.route == "light" for row in light), len(light)),
        "tool_need_accuracy": _share(
            sum(row.tool_need == row.expected_tool_need for row in rows), len(rows)
        ),
        "hint_eligible": sum(row.hint for row in rows),
        "unavailable": sum(row.reason.startswith("unavailable:") for row in rows),
        "missed_heavy_case_ids": sorted(row.case_id for row in heavy if row.route != "heavy"),
        "gates": {"heavy_recall": heavy_recall >= 0.95},
    }


async def evaluate_front_door(guard: GuardedTypedDecisionClassifier) -> list[FrontDoorRow]:
    rows: list[FrontDoorRow] = []
    for case in load_synthetic_fixture(ROUTING_FIXTURE)["cases"]:
        outcome = await guard.ask(FRONT_DOOR_AXES, case["prompt"])
        results = {result.axis_name: result for result in outcome.results}
        reason = outcome.unavailable_reason
        rows.append(
            FrontDoorRow(
                case["id"],
                case["expected_route"],
                need_from_result(results.get("needs_structure")),
                need_from_result(results.get("needs_long_term")),
                outcome.duration_ms,
                None if reason is None else reason.value,
            )
        )
    return rows


async def evaluate_relevance(guard: GuardedTypedDecisionClassifier) -> list[RelevanceRow]:
    rows: list[RelevanceRow] = []
    for template in load_synthetic_fixture(VIABILITY_FIXTURE)["templates"]:
        relevant = set(template["ground_truth"]["relevant_evidence"])
        candidates = [
            (
                event["event_key"],
                event["summary"],
                "relevant" if event["event_key"] in relevant else "superseded",
            )
            for event in template["events"]
        ]
        candidates += [
            (
                f"noise-{index}",
                _NOISE_SUMMARY.format(index=index, template=template["template_id"]),
                "noise",
            )
            for index in (1, 2)
        ]
        for start in range(0, len(candidates), RELEVANCE_BATCH_SIZE):
            batch = candidates[start : start + RELEVANCE_BATCH_SIZE]
            axes = tuple(relevance_axis(index, item[1]) for index, item in enumerate(batch))
            outcome = await guard.ask(axes, template["task_prompt"])
            results = {result.axis_name: result for result in outcome.results}
            for index, (candidate_id, _, category) in enumerate(batch):
                rows.append(
                    RelevanceRow(
                        template["template_id"],
                        candidate_id,
                        category,
                        should_drop_candidate(results.get(f"helps_{index}")),
                    )
                )
    return rows


def extraction_events() -> list[tuple[str, str, bool, str | None]]:
    events: list[tuple[str, str, bool, str | None]] = []
    for template in load_synthetic_fixture(VIABILITY_FIXTURE)["templates"]:
        for event in template["events"]:
            prefix, _, body = event["summary"].partition(": ")
            events.append(
                (
                    event["event_key"],
                    body or event["summary"],
                    True,
                    _EPISODIC_KIND_BY_PREFIX.get(prefix),
                )
            )
    for negative in load_synthetic_fixture(TYPED_DECISION_FIXTURE)["worth_negatives"]:
        events.append((negative["id"], negative["summary"], False, None))
    return events


async def evaluate_extraction_jev(guard: GuardedTypedDecisionClassifier) -> list[ExtractionRow]:
    rows: list[ExtractionRow] = []
    for event_id, summary, worth, kind in extraction_events():
        outcome = await guard.ask((WORTH_REMEMBERING, EPISODIC_KIND), summary)
        results = {result.axis_name: result for result in outcome.results}
        rows.append(
            ExtractionRow(
                "jev",
                event_id,
                worth,
                kind,
                worth_extracting(results.get(WORTH_REMEMBERING.name)),
                accepted_choice(results.get(EPISODIC_KIND.name)),
            )
        )
    return rows


def evaluate_extraction_ollama(provider: EpisodicProvider) -> list[ExtractionRow]:
    rows: list[ExtractionRow] = []
    for event_id, summary, worth, kind in extraction_events():
        try:
            proposals = parse_episodic_output(provider.generate(_OllamaRequest(summary)), 4)
        except Exception:
            proposals = ()
        rows.append(
            ExtractionRow(
                "ollama",
                event_id,
                worth,
                kind,
                bool(proposals),
                proposals[0].kind.value if proposals else None,
            )
        )
    return rows


async def evaluate_tier(guard: GuardedTypedDecisionClassifier) -> list[TierRow]:
    classifier = AxisRoutedClassifier(
        {COMPLEXITY.name: guard, TOOL_NEED.name: guard, RISK_AXIS.name: RiskTermClassifier()}
    )
    committee = tier_committee()
    rows: list[TierRow] = []
    for case in load_synthetic_fixture(TYPED_DECISION_FIXTURE)["tier_cases"]:
        tier = await decide_tier(committee, classifier, case["prompt"])
        results: dict[str, ClassifierResult] = (
            {} if tier.decision is None else {r.axis_name: r for r in tier.decision.results}
        )
        tool_need = results.get(TOOL_NEED.name)
        rows.append(
            TierRow(
                case["id"],
                case["expected_tier"],
                case["expected_tool_need"],
                tier.route,
                tier.reason,
                accepted_choice(tool_need),
                hint_eligible(tier.route, tool_need),
            )
        )
    return rows


def phase_one_complete(report: dict[str, Any]) -> bool:
    return all(
        value is True for section in _SECTIONS for value in report[section]["gates"].values()
    )


async def run_phase_one(
    guard: GuardedTypedDecisionClassifier, ollama: EpisodicProvider | None
) -> dict[str, Any]:
    front = await evaluate_front_door(guard)
    extraction = await evaluate_extraction_jev(guard)
    if ollama is not None:
        extraction += evaluate_extraction_ollama(ollama)
    report: dict[str, Any] = {
        "front_door": score_front_door(front),
        "latency": score_latency(
            [row.duration_ms for row in front if row.unavailable_reason in _MEASURED_OUTCOMES]
        ),
        "relevance": score_relevance(await evaluate_relevance(guard)),
        "extraction": score_extraction(extraction),
        "tier": score_tier(await evaluate_tier(guard)),
        "excluded_fixtures": _excluded_fixtures(),
    }
    report["phase_1_complete"] = phase_one_complete(report)
    return report


def _excluded_fixtures() -> dict[str, str]:
    try:
        load_synthetic_fixture(TELEHEALTH_FIXTURE)
    except FixtureProvenanceError:
        return {TELEHEALTH_FIXTURE.name: "no synthetic provenance declared"}
    return {}


def _share(numerator: int, denominator: int, *, empty: float = 0.0) -> float:
    return numerator / denominator if denominator else empty


def _drop_rate(rows: Sequence[RelevanceRow], category: str) -> float:
    selected = [row for row in rows if row.category == category]
    return _share(sum(row.dropped for row in selected), len(selected))


def _nearest_rank(ordered: Sequence[int], quantile: float) -> int | None:
    if not ordered:
        return None
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]
