"""Run the deterministic, model-free fresh-session resumption fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from mnemo_memory.packages.domain import (
    CheckpointContent,
    ConflictState,
    ContentRepresentation,
    ContextBudget,
    ContextItem,
    ContextItemType,
    ContextPacket,
    EvidenceReference,
    MemoryScope,
    PacketSchemaVersion,
    ProvenanceNotice,
    RequestId,
    Sensitivity,
    SourceTrustClass,
    ValidityState,
)

ROOT = Path(__file__).parents[1]
DEFAULT_FIXTURE = ROOT / "tests/fixtures/evals/fresh-session-resumption.json"
ESTIMATOR_VERSION = "mnemo-character-heuristic-v1"
FIXED_NOW = datetime(2026, 8, 2, 16, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class CharacterHeuristicEstimator:
    """A deterministic cold-input estimate; it is not a provider billing tokenizer."""

    version: str = ESTIMATOR_VERSION

    def estimate(self, content: str) -> int:
        return (len(content) + 2) // 3


def load_fixture(path: Path = DEFAULT_FIXTURE) -> tuple[dict[str, Any], str]:
    fixture = cast(dict[str, Any], json.loads(path.read_text()))
    transcript_path = path.parent / cast(str, fixture["transcript_file"])
    return fixture, transcript_path.read_text()


def fixture_digest(fixture: Mapping[str, object], transcript: str) -> str:
    canonical = json.dumps(fixture, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{canonical}\n{transcript}".encode()).hexdigest()


def build_checkpoint_packet(
    fixture: Mapping[str, object],
) -> tuple[CheckpointContent, ContextPacket]:
    estimator = CharacterHeuristicEstimator()
    checkpoint = cast(Mapping[str, object], fixture["checkpoint"])
    without_estimate = {**checkpoint, "token_estimate": 0}
    content_tokens = estimator.estimate(
        json.dumps(without_estimate, sort_keys=True, separators=(",", ":"))
    )
    content = CheckpointContent.from_dict({**checkpoint, "token_estimate": content_tokens})
    scope = MemoryScope.from_dict(cast(Mapping[str, object], fixture["scope"]))
    evidence = tuple(
        EvidenceReference.from_dict(cast(Mapping[str, object], item))
        for item in cast(list[object], fixture["evidence"])
    )
    checkpoint_id = "88888888-8888-4888-8888-888888888888"
    revision_id = "99999999-9999-4999-8999-999999999999"
    item_id = f"checkpoint:{checkpoint_id}:revision:{revision_id}"
    item_content = json.dumps(content.to_dict(), sort_keys=True, separators=(",", ":"))
    item = ContextItem(
        item_id=item_id,
        item_type=ContextItemType.ACTIVE_TASK_CHECKPOINT,
        source_scope=scope,
        content=item_content,
        content_representation=ContentRepresentation.UNTRUSTED_EVIDENCE,
        token_estimate=content.token_estimate,
        evidence_references=evidence,
        source_trust=SourceTrustClass.APPROVED_CHECKPOINT,
        sensitivity=Sensitivity.NORMAL,
        validity=ValidityState.CURRENT,
        ranking=None,
        conflict_state=ConflictState.NONE,
        observed_at=FIXED_NOW,
    )
    provenance_tokens = estimator.estimate(f"{item_id}|{evidence[0].immutable_source_ref}")
    provenance = ProvenanceNotice(
        provenance_id=f"provenance:{item_id}",
        item_id=item_id,
        source_reference=f"mnemo:checkpoint/{checkpoint_id}/revision/{revision_id}",
        source_digest=hashlib.sha256(item_content.encode()).hexdigest(),
        evidence_references=evidence,
        token_estimate=provenance_tokens,
    )
    packet = ContextPacket(
        schema_version=PacketSchemaVersion.V1,
        request_id=RequestId.from_string("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        owner_scope=scope,
        query_id=None,
        task_id=scope.task_id,
        created_at=FIXED_NOW,
        expires_at=None,
        declared_total_tokens=content.token_estimate + provenance_tokens,
        budget=ContextBudget(),
        producer_version="mnemo-eval/1.0",
        active_task_checkpoint=item,
        provenance=(provenance,),
    )
    return content, packet


def _facts(fixture: Mapping[str, object], kinds: set[str]) -> list[Mapping[str, object]]:
    return [
        cast(Mapping[str, object], fact)
        for fact in cast(list[object], fixture["facts"])
        if cast(Mapping[str, object], fact)["kind"] in kinds
    ]


def _quality(
    fixture: Mapping[str, object],
    context: str,
    current_claims: str,
    provenance: bool,
    *,
    use_markers: bool,
) -> dict[str, object]:
    required = _facts(fixture, {"required", "current_decision", "verified"})
    optional = _facts(fixture, {"optional", "unverified_inference"})
    forbidden = _facts(fixture, {"forbidden_stale"})

    def normalize(value: str) -> str:
        return " ".join(value.lower().split())

    def present(facts: list[Mapping[str, object]], text: str) -> list[str]:
        normalized_text = normalize(text)
        return [
            cast(str, fact["id"])
            for fact in facts
            if (
                normalize(cast(str, fact.get("mnemo_marker", fact["text"]))) in normalized_text
                if use_markers
                else normalize(cast(str, fact["text"])) in normalized_text
            )
        ]

    required_present = present(required, context)
    optional_present = present(optional, context)
    stale_present = present(forbidden, context)
    stale_as_current = present(forbidden, current_claims)
    current = _facts(fixture, {"current_decision"})
    next_action = [fact for fact in required if fact["id"] == "next-action"]
    verification = [fact for fact in required if fact["id"] == "verification"]
    lesson = [fact for fact in required if fact["id"] == "reasoning-lesson"]
    return {
        "required_fact_recall": len(required_present) / len(required),
        "optional_fact_recall": len(optional_present) / len(optional),
        "forbidden_stale_fact_ids_present": stale_present,
        "forbidden_stale_fact_ids_as_current": stale_as_current,
        "provenance_coverage": 1.0 if provenance else 0.0,
        "expected_next_action_available": bool(present(next_action, context)),
        "current_decision_available": bool(present(current, current_claims)),
        "verification_state_available": bool(present(verification, context)),
        "reasoning_lesson_available": bool(present(lesson, context)),
    }


def evaluate(
    fixture: Mapping[str, object], transcript: str, *, minimum_savings_percent: float = 50.0
) -> dict[str, object]:
    estimator = CharacterHeuristicEstimator()
    content, packet = build_checkpoint_packet(fixture)
    prompt = cast(str, fixture["task_prompt"])
    packet_json = packet.to_json()
    full_tokens = estimator.estimate(transcript)
    packet_tokens = packet.declared_total_tokens
    common_tokens = estimator.estimate(prompt)
    mnemo_context = packet_json
    full_context = transcript
    checkpoint_current_claims = "\n".join(content.decisions)
    conditions = {
        "no_memory": {
            "context_tokens": 0,
            "total_input_tokens": common_tokens,
            "quality": _quality(fixture, prompt, "", False, use_markers=False),
        },
        "full_transcript": {
            "context_tokens": full_tokens,
            "total_input_tokens": common_tokens + full_tokens,
            "quality": _quality(
                fixture,
                f"{prompt}\n{full_context}",
                cast(str, _facts(fixture, {"current_decision"})[0]["text"]),
                True,
                use_markers=False,
            ),
        },
        "mnemo_context": {
            "context_tokens": packet_tokens,
            "total_input_tokens": common_tokens + packet_tokens,
            "quality": _quality(
                fixture,
                f"{prompt}\n{mnemo_context}",
                checkpoint_current_claims,
                True,
                use_markers=True,
            ),
        },
    }
    context_savings = (full_tokens - packet_tokens) / full_tokens * 100
    full_total = cast(int, conditions["full_transcript"]["total_input_tokens"])
    mnemo_total = cast(int, conditions["mnemo_context"]["total_input_tokens"])
    total_savings = (full_total - mnemo_total) / full_total * 100
    mnemo_quality = cast(dict[str, object], conditions["mnemo_context"]["quality"])
    gates = {
        "checkpoint_within_600_tokens": content.token_estimate <= 600,
        "packet_within_hard_budget": packet.declared_total_tokens <= packet.budget.total_limit,
        "required_fact_recall": mnemo_quality["required_fact_recall"] == 1.0,
        "provenance_coverage": mnemo_quality["provenance_coverage"] == 1.0,
        "next_action_present": mnemo_quality["expected_next_action_available"] is True,
        "current_decision_present": mnemo_quality["current_decision_available"] is True,
        "no_stale_decision_as_current": not mnemo_quality["forbidden_stale_fact_ids_as_current"],
        "reasoning_lesson_present": mnemo_quality["reasoning_lesson_available"] is True,
        "context_savings_threshold": context_savings >= minimum_savings_percent,
    }
    return {
        "fixture_version": fixture["fixture_version"],
        "fixture_digest": fixture_digest(fixture, transcript),
        "estimator_version": estimator.version,
        "token_accounting": {
            "common_task_prompt_tokens": common_tokens,
            "full_transcript_tokens": full_tokens,
            "checkpoint_content_tokens": content.token_estimate,
            "provenance_tokens": packet.provenance[0].token_estimate,
            "context_packet_tokens": packet_tokens,
            "raw_transcript_to_packet_reduction_percent": context_savings,
            "full_condition_to_mnemo_total_input_reduction_percent": total_savings,
            "cached_tokens": "not counted: cold fresh-session deterministic estimate",
        },
        "conditions": conditions,
        "gates": gates,
        "passed": all(gates.values()),
        "context_packet": packet.to_dict(),
    }


def score_context_packet(
    fixture: Mapping[str, object], transcript: str, packet: ContextPacket
) -> dict[str, object]:
    """Score a returned packet with the same exact-match gates as the Mnemo baseline."""
    if packet.active_task_checkpoint is None:
        raise ValueError("returned packet does not contain an active checkpoint")
    content = CheckpointContent.from_dict(json.loads(packet.active_task_checkpoint.content))
    quality = _quality(
        fixture,
        packet.to_json(),
        "\n".join(content.decisions),
        bool(packet.provenance),
        use_markers=True,
    )
    baseline = evaluate(fixture, transcript)
    tokens = cast(Mapping[str, object], baseline["token_accounting"])
    gates = {
        "checkpoint_within_600_tokens": content.token_estimate <= 600,
        "packet_within_hard_budget": packet.declared_total_tokens <= packet.budget.total_limit,
        "required_fact_recall": quality["required_fact_recall"] == 1.0,
        "provenance_coverage": quality["provenance_coverage"] == 1.0,
        "next_action_present": quality["expected_next_action_available"] is True,
        "current_decision_present": quality["current_decision_available"] is True,
        "verification_state_present": quality["verification_state_available"] is True,
        "no_stale_decision_as_current": not quality["forbidden_stale_fact_ids_as_current"],
        "reasoning_lesson_present": quality["reasoning_lesson_available"] is True,
    }
    return {
        "quality": quality,
        "context_packet_tokens": packet.declared_total_tokens,
        "full_transcript_tokens": tokens["full_transcript_tokens"],
        "context_savings_percent": (
            (cast(int, tokens["full_transcript_tokens"]) - packet.declared_total_tokens)
            / cast(int, tokens["full_transcript_tokens"])
            * 100
        ),
        "total_input_savings_percent": tokens[
            "full_condition_to_mnemo_total_input_reduction_percent"
        ],
        "passed": all(gates.values()),
        "gates": gates,
    }


def render_table(result: Mapping[str, object]) -> str:
    conditions = cast(Mapping[str, Mapping[str, object]], result["conditions"])
    rows = ["condition        context  total input  required recall  provenance"]
    for name in ("no_memory", "full_transcript", "mnemo_context"):
        values = conditions[name]
        quality = cast(Mapping[str, object], values["quality"])
        rows.append(
            f"{name:16} {values['context_tokens']:>7} {values['total_input_tokens']:>12}"
            f" {cast(float, quality['required_fact_recall']):>14.0%}"
            f" {cast(float, quality['provenance_coverage']):>11.0%}"
        )
    tokens = cast(Mapping[str, object], result["token_accounting"])
    rows.append(
        f"context savings: {cast(float, tokens['raw_transcript_to_packet_reduction_percent']):.1f}%"
    )
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--minimum-savings-percent", type=float, default=50.0)
    parser.add_argument("--json", action="store_true", help="emit only stable JSON")
    args = parser.parse_args(argv)
    fixture, transcript = load_fixture(args.fixture)
    result = evaluate(fixture, transcript, minimum_savings_percent=args.minimum_savings_percent)
    if not args.json:
        print(render_table(result))
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
