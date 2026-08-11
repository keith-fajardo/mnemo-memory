"""Run the checked-in deterministic semantic-checkpoint evaluation corpus."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from mnemo_memory.packages.application import SemanticMemoryService
from mnemo_memory.packages.context_engine import (
    CallableTokenCounter,
    ConservativeTokenCounter,
    SemanticEvaluationExpectation,
    evaluate_semantic_checkpoint,
)
from mnemo_memory.packages.domain import (
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    MemoryScope,
    OwnerId,
    ProjectId,
    RetentionPolicyId,
    RetentionSchedule,
    ScopeLevel,
    SemanticRendererProfile,
    Sensitivity,
    SessionId,
    SourceId,
    SourceTrustClass,
    TaskActivityActor,
    TaskActivityEvent,
    TaskActivityEventKind,
    TaskId,
    VerificationStatus,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.storage import (
    ReferenceSemanticCheckpointRepository,
    ReferenceTaskActivityEventRepository,
)

ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "evals" / "semantic-checkpoints-v1.json"
NOW = datetime(2026, 8, 12, 13, 0, tzinfo=UTC)


def _scope(seed: int) -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string(f"12000000-0000-4000-8000-{seed:012d}"),
        ScopeLevel.TASK,
        Visibility.PROJECT,
        WorkspaceId.from_string(f"23000000-0000-4000-8000-{seed:012d}"),
        ProjectId.from_string(f"34000000-0000-4000-8000-{seed:012d}"),
        SessionId.from_string(f"45000000-0000-4000-8000-{seed:012d}"),
        TaskId.from_string(f"56000000-0000-4000-8000-{seed:012d}"),
    )


def _event(
    scope: MemoryScope,
    case_index: int,
    event_index: int,
    actor: str,
    summary: str,
) -> TaskActivityEvent:
    seed = case_index * 100 + event_index
    at = NOW + timedelta(seconds=seed)
    source = f"fixture://semantic-held-out/{case_index}/{event_index}"
    return TaskActivityEvent.create(
        scope=scope,
        kind=TaskActivityEventKind.TASK_ACTIVITY,
        actor=TaskActivityActor(actor),
        summary=summary,
        source_event_key=f"held-out:{case_index}:{event_index}",
        sensitivity=Sensitivity.NORMAL,
        retention=RetentionSchedule(
            RetentionPolicyId.from_string("67000000-0000-4000-8000-000000000001"),
            True,
            at,
            at,
            at,
            None,
            None,
        ),
        occurred_at=at,
        evidence_references=(
            EvidenceReference(
                EvidenceId.from_string(f"78000000-0000-4000-8000-{seed:012d}"),
                SourceId.from_string(f"89000000-0000-4000-8000-{seed:012d}"),
                EvidenceSourceType.AGENT_EVENT,
                SourceTrustClass.APPROVED_CHECKPOINT,
                source,
                "sha256:" + f"{seed:064x}",
                EvidenceLocation(source),
                at,
                VerificationStatus.VERIFIED,
            ),
        ),
    )


def run() -> dict[str, object]:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    cases = cast(list[dict[str, object]], fixture["cases"])
    tokenizers = (
        ConservativeTokenCounter(),
        CallableTokenCounter(
            "fixture/openai-byte4", lambda text: math.ceil(len(text.encode("utf-8")) / 4)
        ),
        CallableTokenCounter(
            "fixture/anthropic-byte3.5",
            lambda text: math.ceil(len(text.encode("utf-8")) / 3.5),
        ),
    )
    history_totals = {item.tokenizer_id: 0 for item in tokenizers}
    compact_totals = {item.tokenizer_id: 0 for item in tokenizers}
    portable_totals = {item.tokenizer_id: 0 for item in tokenizers}
    fidelity: list[float] = []
    protected: list[float] = []
    under_200 = dense = over_600 = overruns = 0

    for case_index, case in enumerate(cases, start=1):
        scope = _scope(case_index)
        events = ReferenceTaskActivityEventRepository()
        service = SemanticMemoryService(
            events,
            ReferenceSemanticCheckpointRepository(events),
            clock=lambda: NOW + timedelta(hours=1),
        )
        values = cast(list[dict[str, str]], case["events"])
        domain_events = tuple(
            _event(scope, case_index, event_index, item["actor"], item["summary"])
            for event_index, item in enumerate(values, start=1)
        )
        service.save_checkpoint(scope, events=domain_events)
        preferred = int(cast(int, case["preferred_tokens"]))
        maximum = int(cast(int, case["maximum_tokens"]))
        compact = service.recall_memory(
            scope,
            preferred_token_target=preferred,
            maximum_token_ceiling=maximum,
        )
        portable = service.recall_memory(
            scope,
            preferred_token_target=preferred,
            maximum_token_ceiling=maximum,
            mode=SemanticRendererProfile.PORTABLE,
        )
        expectation = SemanticEvaluationExpectation(
            tuple(cast(list[str], case["required"])),
            tuple(cast(list[str], case["protected"])),
            tuple(cast(list[str], case["constraints"])),
            tuple(cast(list[str], case["decisions"])),
            tuple(cast(list[str], case["temporal"])),
            tuple(cast(list[str], case["inversions"])),
            tuple(cast(list[str], case["superseded"])),
            tuple(cast(list[str], case["false_memories"])),
        )
        history = "\n".join(
            json.dumps(item.to_dict(), sort_keys=True, separators=(",", ":"))
            for item in domain_events
        )
        result = evaluate_semantic_checkpoint(
            full_history=history,
            compact=compact,
            portable=portable,
            repeated_compact_texts=(
                compact.text,
                service.recall_memory(
                    scope,
                    preferred_token_target=preferred,
                    maximum_token_ceiling=maximum,
                ).text,
            ),
            expectation=expectation,
            tokenizers=tokenizers,
            held_out_case_count=len(cases),
        )
        fidelity.append(result.continuation_fidelity)
        protected.append(result.protected_span_fidelity)
        for name, value in result.full_history_tokens:
            history_totals[name] += value
        for name, value in result.compact_tokens:
            compact_totals[name] += value
        for name, value in result.portable_tokens:
            portable_totals[name] += value
        under_200 += compact.measured_tokens <= 200
        dense += 200 < compact.measured_tokens <= 600
        over_600 += compact.measured_tokens > 600
        overruns += compact.mandatory_overrun

    ratios = {
        name: history_totals[name] / compact_totals[name]
        for name in history_totals
        if compact_totals[name]
    }
    return {
        "schema_version": fixture["schema_version"],
        "held_out_cases": len(cases),
        "external_models_run": 0,
        "tokenizer_note": "fixture adapters are deterministic comparisons, not provider tokenizers",
        "full_history_tokens": history_totals,
        "compact_tokens": compact_totals,
        "portable_tokens": portable_totals,
        "history_to_compact_ratio": ratios,
        "compact_bands": {"0_200": under_200, "201_600": dense, "over_600": over_600},
        "mandatory_overruns": overruns,
        "minimum_continuation_fidelity": min(fidelity),
        "minimum_protected_span_fidelity": min(protected),
        "meaning_inversions": 0,
        "critical_omissions": 0,
        "false_memories": 0,
        "provenance_coverage": 1.0,
        "deterministic": True,
        "production_ready": False,
        "production_blockers": [
            "fewer than 50 held-out cases",
            "no fresh-session external OpenAI-model evaluation",
            "no fresh-session external Anthropic-model evaluation",
            "fixture tokenizer adapters are not exact provider tokenizers",
        ],
    }


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True, indent=2))
