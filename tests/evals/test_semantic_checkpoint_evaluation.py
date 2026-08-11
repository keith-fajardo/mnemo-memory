"""Held-out deterministic evaluation for semantic checkpoint fidelity and token cost."""

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

NOW = datetime(2026, 8, 12, 13, 0, tzinfo=UTC)
FIXTURE = Path(__file__).parents[1] / "fixtures" / "evals" / "semantic-checkpoints-v1.json"


def _scope(seed: int) -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string(f"11000000-0000-4000-8000-{seed:012d}"),
        ScopeLevel.TASK,
        Visibility.PROJECT,
        WorkspaceId.from_string(f"22000000-0000-4000-8000-{seed:012d}"),
        ProjectId.from_string(f"33000000-0000-4000-8000-{seed:012d}"),
        SessionId.from_string(f"44000000-0000-4000-8000-{seed:012d}"),
        TaskId.from_string(f"55000000-0000-4000-8000-{seed:012d}"),
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
    return TaskActivityEvent.create(
        scope=scope,
        kind=TaskActivityEventKind.TASK_ACTIVITY,
        actor=TaskActivityActor(actor),
        summary=summary,
        source_event_key=f"held-out:{case_index}:{event_index}",
        sensitivity=Sensitivity.NORMAL,
        retention=RetentionSchedule(
            RetentionPolicyId.from_string("66000000-0000-4000-8000-000000000001"),
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
                EvidenceId.from_string(f"77000000-0000-4000-8000-{seed:012d}"),
                SourceId.from_string(f"88000000-0000-4000-8000-{seed:012d}"),
                EvidenceSourceType.AGENT_EVENT,
                SourceTrustClass.APPROVED_CHECKPOINT,
                f"fixture://semantic-held-out/{case_index}/{event_index}",
                "sha256:" + f"{seed:064x}",
                EvidenceLocation(f"fixture://semantic-held-out/{case_index}/{event_index}"),
                at,
                VerificationStatus.VERIFIED,
            ),
        ),
    )


def test_held_out_semantic_checkpoint_corpus_preserves_required_meaning() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    cases = cast(list[dict[str, object]], fixture["cases"])
    assert len(cases) == 12
    tokenizers = (
        ConservativeTokenCounter(),
        CallableTokenCounter(
            "fixture/openai-tokenizer",
            lambda text: math.ceil(len(text.encode("utf-8")) / 4),
        ),
        CallableTokenCounter(
            "fixture/anthropic-tokenizer",
            lambda text: math.ceil(len(text.encode("utf-8")) / 3.5),
        ),
    )
    total_history = {item.tokenizer_id: 0 for item in tokenizers}
    total_compact = {item.tokenizer_id: 0 for item in tokenizers}
    saw_dense = False

    for case_index, case in enumerate(cases, start=1):
        scope = _scope(case_index)
        events = ReferenceTaskActivityEventRepository()
        service = SemanticMemoryService(
            events,
            ReferenceSemanticCheckpointRepository(events),
            clock=lambda: NOW + timedelta(hours=1),
        )
        source_events = cast(list[dict[str, str]], case["events"])
        domain_events = tuple(
            _event(scope, case_index, event_index, item["actor"], item["summary"])
            for event_index, item in enumerate(source_events, start=1)
        )
        service.save_checkpoint(scope, events=domain_events)
        preferred = int(cast(int, case["preferred_tokens"]))
        maximum = int(cast(int, case["maximum_tokens"]))
        compact = service.recall_memory(
            scope,
            preferred_token_target=preferred,
            maximum_token_ceiling=maximum,
            mode=SemanticRendererProfile.COMPACT,
        )
        portable = service.recall_memory(
            scope,
            preferred_token_target=preferred,
            maximum_token_ceiling=maximum,
            mode=SemanticRendererProfile.PORTABLE,
        )
        history = "\n".join(
            json.dumps(item.to_dict(), sort_keys=True, separators=(",", ":"))
            for item in domain_events
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
        evaluation = evaluate_semantic_checkpoint(
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

        assert evaluation.continuation_fidelity == 1.0, case["name"]
        assert evaluation.portable_continuation_fidelity == 1.0, case["name"]
        assert evaluation.protected_span_fidelity == 1.0, case["name"]
        assert evaluation.meaning_inversion_count == 0, case["name"]
        assert evaluation.critical_omission_count == 0, case["name"]
        assert evaluation.false_memory_count == 0, case["name"]
        assert evaluation.constraint_retention == 1.0, case["name"]
        assert evaluation.decision_rationale_retention == 1.0, case["name"]
        assert evaluation.supersession_accuracy == 1.0, case["name"]
        assert evaluation.temporal_accuracy == 1.0, case["name"]
        assert evaluation.provenance_coverage == 1.0, case["name"]
        assert evaluation.determinism is True, case["name"]
        assert evaluation.drift_cycle_count == 0, case["name"]
        assert evaluation.fresh_session_task_success is True, case["name"]
        assert evaluation.production_ready is False
        if case["name"] == "conflicting-speakers":
            assert "subject=user" in portable.text
            assert "subject=agent" in portable.text
        saw_dense = saw_dense or compact.measured_tokens > 200
        for tokenizer_id, count in evaluation.full_history_tokens:
            total_history[tokenizer_id] += count
        for tokenizer_id, count in evaluation.compact_tokens:
            total_compact[tokenizer_id] += count

    assert saw_dense is True
    assert all(total_history[name] > 0 for name in total_history)
    assert all(total_compact[name] > 0 for name in total_compact)
    assert all(total_history[name] / total_compact[name] > 1.0 for name in total_history)
