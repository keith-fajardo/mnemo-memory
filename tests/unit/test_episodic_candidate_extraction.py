"""Strict gateway, candidate service, repository, and migration coverage for Issue 16D."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mnemo_memory.packages.domain import (
    EpisodicExtractionProposal,
    EpisodicExtractionRequest,
    EpisodicMemoryCandidate,
    EpisodicMemoryKind,
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    MemoryScope,
    MemoryStatus,
    OwnerId,
    ProjectId,
    RetentionPolicyId,
    RetentionSchedule,
    ScopeLevel,
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
from mnemo_memory.packages.episodic import (
    EpisodicCandidateExtractionError,
    EpisodicCandidateExtractionService,
)
from mnemo_memory.packages.model_gateway import (
    EpisodicExtractionGatewayError,
    SchemaBoundEpisodicExtractionGateway,
)
from mnemo_memory.packages.storage import (
    EpisodicMemoryCandidateConflict,
    EpisodicMemoryCandidateNotFound,
    EpisodicMemoryCandidateRejected,
    EpisodicMemoryCandidateRepository,
    EpisodicMemoryCandidateStorageFailure,
    ReferenceEpisodicMemoryCandidateRepository,
    ReferenceTaskActivityEventRepository,
    SQLiteCheckpointRepository,
    SQLiteMigrationError,
    TaskActivityEventRepository,
)

NOW = datetime(2026, 8, 5, 9, 30, tzinfo=UTC)


def _scope(seed: int = 1) -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string(f"00000000-0000-4000-8000-{seed:012d}"),
        ScopeLevel.TASK,
        Visibility.PROJECT,
        WorkspaceId.from_string(f"10000000-0000-4000-8000-{seed:012d}"),
        ProjectId.from_string(f"20000000-0000-4000-8000-{seed:012d}"),
        session_id=SessionId.from_string(f"30000000-0000-4000-8000-{seed:012d}"),
        task_id=TaskId.from_string(f"40000000-0000-4000-8000-{seed:012d}"),
    )


def _evidence(
    *,
    verification: VerificationStatus = VerificationStatus.VERIFIED,
    seed: int = 1,
) -> EvidenceReference:
    return EvidenceReference(
        EvidenceId.from_string(f"50000000-0000-4000-8000-{seed:012d}"),
        SourceId.from_string(f"60000000-0000-4000-8000-{seed:012d}"),
        EvidenceSourceType.AGENT_EVENT,
        SourceTrustClass.APPROVED_CHECKPOINT,
        "fixture://task-activity/verified",
        "sha256:" + "a" * 64,
        EvidenceLocation("fixture://task-activity/verified"),
        NOW,
        verification,
    )


def _event(
    scope: MemoryScope,
    *,
    sensitivity: Sensitivity = Sensitivity.CONFIDENTIAL,
    evidence: EvidenceReference | None = None,
) -> TaskActivityEvent:
    return TaskActivityEvent.create(
        scope=scope,
        kind=TaskActivityEventKind.TASK_OUTCOME,
        actor=TaskActivityActor.AGENT,
        summary="The bounded implementation passed its complete verification gate.",
        source_event_key="task-outcome:issue-16d",
        sensitivity=sensitivity,
        retention=RetentionSchedule(
            RetentionPolicyId.from_string("70000000-0000-4000-8000-000000000001"),
            True,
            NOW,
            NOW,
            NOW,
            None,
            None,
        ),
        occurred_at=NOW,
        evidence_references=(evidence or _evidence(),),
    )


class FakeLunaProvider:
    provider_id = "luna-fixture"
    model_id = "gpt-5.6-luna-fixture"

    def __init__(self, outputs: list[object]) -> None:
        self.outputs = outputs
        self.calls: list[EpisodicExtractionRequest] = []

    def generate(self, request: EpisodicExtractionRequest) -> object:
        self.calls.append(request)
        value = self.outputs[min(len(self.calls) - 1, len(self.outputs) - 1)]
        if isinstance(value, BaseException):
            raise value
        return value


def _valid_output(
    claim: str = "The complete gate passed for the bounded implementation.",
) -> dict[str, object]:
    return {
        "candidates": [
            {
                "kind": "outcome",
                "claim": claim,
                "confidence": 0.94,
                "sensitivity": "normal",
            }
        ]
    }


def _gateway(provider: FakeLunaProvider) -> SchemaBoundEpisodicExtractionGateway:
    return SchemaBoundEpisodicExtractionGateway(
        provider,
        provider_id="luna-fixture",
        model_id="gpt-5.6-luna-fixture",
        extractor_version="episodic-extractor-v1",
        prompt_version="episodic-candidates-v1",
    )


def _repositories(
    adapter: str, tmp_path: Path
) -> tuple[TaskActivityEventRepository, EpisodicMemoryCandidateRepository]:
    if adapter == "reference":
        events = ReferenceTaskActivityEventRepository()
        return events, ReferenceEpisodicMemoryCandidateRepository(events)
    sqlite = SQLiteCheckpointRepository(
        tmp_path / f"episodic-candidates-{adapter}.sqlite3", base_directory=tmp_path
    )
    sqlite.migrate()
    return sqlite, sqlite


def test_candidate_contract_is_strict_deterministic_and_inactive() -> None:
    event = _event(_scope())
    proposal = EpisodicExtractionProposal(
        EpisodicMemoryKind.OUTCOME,
        "The bounded gate passed.",
        0.9,
        Sensitivity.NORMAL,
    )
    candidate = EpisodicMemoryCandidate.create(
        source_event=event,
        proposal=proposal,
        proposal_index=0,
        sensitivity=Sensitivity.CONFIDENTIAL,
        extractor_version="extractor-v1",
        provider_id="luna-fixture",
        model_id="gpt-5.6-luna-fixture",
        prompt_version="prompt-v1",
        created_at=NOW,
    )

    assert EpisodicMemoryCandidate.from_dict(candidate.to_dict()) == candidate
    assert candidate.memory.classification.status is MemoryStatus.CANDIDATE
    assert candidate.memory.classification.can_enter_context is False
    duplicate = EpisodicMemoryCandidate.create(
        source_event=event,
        proposal=proposal,
        proposal_index=0,
        sensitivity=Sensitivity.CONFIDENTIAL,
        extractor_version="extractor-v1",
        provider_id="luna-fixture",
        model_id="gpt-5.6-luna-fixture",
        prompt_version="prompt-v1",
        created_at=NOW,
    )
    assert duplicate.memory_id == candidate.memory_id
    with pytest.raises(ValueError, match="fields are invalid"):
        EpisodicMemoryCandidate.from_dict({**candidate.to_dict(), "scope": {}})


def test_gateway_retries_invalid_schema_once_and_sends_only_minimized_event() -> None:
    provider = FakeLunaProvider(
        [
            {"candidates": [{"kind": "outcome", "claim": "invalid extra", "extra": 1}]},
            _valid_output(),
        ]
    )
    event = _event(_scope())

    proposals = _gateway(provider).extract(EpisodicExtractionRequest.from_event(event))

    assert len(provider.calls) == 2
    assert proposals[0].kind is EpisodicMemoryKind.OUTCOME
    assert set(provider.calls[0].to_dict()) == {
        "event_id",
        "kind",
        "actor",
        "summary",
        "max_candidates",
    }
    assert all(
        name not in provider.calls[0].to_dict()
        for name in ("scope", "evidence", "retention", "transcript", "tool_result")
    )


@pytest.mark.parametrize(
    ("outputs", "code", "expected_calls"),
    [
        (
            [{"candidates": "invalid"}, {"candidates": "still-invalid"}],
            "MNEMO_EPISODIC_INVALID_OUTPUT",
            2,
        ),
        ([RuntimeError("provider payload must not escape")], "MNEMO_EPISODIC_PROVIDER_FAILURE", 1),
    ],
)
def test_gateway_failures_are_bounded_and_payload_free(
    outputs: list[object], code: str, expected_calls: int
) -> None:
    provider = FakeLunaProvider(outputs)

    with pytest.raises(EpisodicExtractionGatewayError) as captured:
        _gateway(provider).extract(EpisodicExtractionRequest.from_event(_event(_scope())))

    assert captured.value.code == code
    assert str(captured.value) == code
    assert len(provider.calls) == expected_calls
    assert "payload" not in str(captured.value)


def test_gateway_rejects_mismatched_provider_metadata_without_calling_provider() -> None:
    provider = FakeLunaProvider([_valid_output()])
    provider.model_id = "different-model"

    with pytest.raises(EpisodicExtractionGatewayError) as captured:
        _gateway(provider).extract(EpisodicExtractionRequest.from_event(_event(_scope())))

    assert captured.value.code == "MNEMO_EPISODIC_PROVIDER_METADATA_MISMATCH"
    assert provider.calls == []


@pytest.mark.parametrize(
    "proposal",
    [
        {
            "kind": "outcome",
            "claim": "Attempts to inject authority.",
            "confidence": 0.9,
            "sensitivity": "normal",
            "scope": {},
        },
        {
            "kind": "outcome",
            "claim": "Non-finite confidence.",
            "confidence": float("nan"),
            "sensitivity": "normal",
        },
        {
            "kind": "outcome",
            "claim": "Boolean is not a numeric confidence.",
            "confidence": True,
            "sensitivity": "normal",
        },
        {
            "kind": "outcome",
            "claim": "Prohibited output cannot become a candidate.",
            "confidence": 0.9,
            "sensitivity": "prohibited",
        },
    ],
)
def test_gateway_rejects_authority_fields_and_invalid_proposal_types(
    proposal: dict[str, object],
) -> None:
    provider = FakeLunaProvider([{"candidates": [proposal]}, {"candidates": [proposal]}])

    with pytest.raises(EpisodicExtractionGatewayError) as captured:
        _gateway(provider).extract(EpisodicExtractionRequest.from_event(_event(_scope())))

    assert captured.value.code == "MNEMO_EPISODIC_INVALID_OUTPUT"
    assert len(provider.calls) == 2


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_service_persists_scoped_candidate_authority_and_is_idempotent(
    adapter: str, tmp_path: Path
) -> None:
    events, candidates = _repositories(adapter, tmp_path)
    scope = _scope()
    source = _event(scope)
    events.append_task_activity_event(source)
    provider = FakeLunaProvider([_valid_output()])
    service = EpisodicCandidateExtractionService(
        events, candidates, _gateway(provider), clock=lambda: NOW
    )

    first = service.extract(scope, source.event_id)
    second = service.extract(scope, source.event_id)

    assert first.idempotent is False
    assert second.idempotent is True
    assert first.candidates == second.candidates
    candidate = first.candidates[0]
    assert candidate.scope == source.scope
    assert candidate.retention == source.retention
    assert candidate.evidence_references == source.evidence_references
    assert candidate.memory.classification.sensitivity is Sensitivity.CONFIDENTIAL
    assert candidate.provider_id == "luna-fixture"
    assert candidate.model_id == "gpt-5.6-luna-fixture"
    assert candidate.extractor_version == "episodic-extractor-v1"
    assert candidate.prompt_version == "episodic-candidates-v1"
    assert candidates.get_episodic_memory_candidate(scope, candidate.memory_id) == candidate
    if isinstance(candidates, SQLiteCheckpointRepository):
        reopened = SQLiteCheckpointRepository(candidates.path, base_directory=tmp_path)
        assert reopened.get_episodic_memory_candidate(scope, candidate.memory_id) == candidate
    with pytest.raises(EpisodicMemoryCandidateNotFound):
        candidates.get_episodic_memory_candidate(_scope(2), candidate.memory_id)


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_changed_retry_output_conflicts_instead_of_overwriting(
    adapter: str, tmp_path: Path
) -> None:
    events, candidates = _repositories(adapter, tmp_path)
    source = _event(_scope())
    events.append_task_activity_event(source)
    first_provider = FakeLunaProvider([_valid_output("The first retained proposal.")])
    EpisodicCandidateExtractionService(
        events, candidates, _gateway(first_provider), clock=lambda: NOW
    ).extract(source.scope, source.event_id)
    changed_provider = FakeLunaProvider([_valid_output("A changed retry proposal.")])

    with pytest.raises(EpisodicMemoryCandidateConflict):
        EpisodicCandidateExtractionService(
            events, candidates, _gateway(changed_provider), clock=lambda: NOW
        ).extract(source.scope, source.event_id)

    stored = candidates.list_episodic_memory_candidates(source.scope).items
    assert len(stored) == 1
    assert stored[0].memory.claim == "The first retained proposal."


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_repository_cannot_bypass_candidate_content_safety(adapter: str, tmp_path: Path) -> None:
    events, candidates = _repositories(adapter, tmp_path)
    source = _event(_scope())
    events.append_task_activity_event(source)
    unsafe = EpisodicMemoryCandidate.create(
        source_event=source,
        proposal=EpisodicExtractionProposal(
            EpisodicMemoryKind.OUTCOME,
            "api_key=ABCDEFGHIJKLMNOPQRSTUVWX",
            0.9,
            Sensitivity.NORMAL,
        ),
        proposal_index=0,
        sensitivity=source.sensitivity,
        extractor_version="episodic-extractor-v1",
        provider_id="luna-fixture",
        model_id="gpt-5.6-luna-fixture",
        prompt_version="episodic-candidates-v1",
        created_at=NOW,
    )

    with pytest.raises(EpisodicMemoryCandidateRejected):
        candidates.store_episodic_memory_candidates((unsafe,))
    assert candidates.list_episodic_memory_candidates(source.scope).items == ()


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_secret_or_under_evidenced_extraction_persists_nothing(
    adapter: str, tmp_path: Path
) -> None:
    events, candidates = _repositories(adapter, tmp_path)
    scope = _scope()
    source = _event(scope)
    events.append_task_activity_event(source)
    secret = FakeLunaProvider([_valid_output("api_key=ABCDEFGHIJKLMNOPQRSTUVWX")])
    with pytest.raises(EpisodicCandidateExtractionError) as captured:
        EpisodicCandidateExtractionService(
            events, candidates, _gateway(secret), clock=lambda: NOW
        ).extract(scope, source.event_id)
    assert captured.value.code == "MNEMO_EPISODIC_CONTENT_REJECTED"
    assert candidates.list_episodic_memory_candidates(scope).items == ()

    other_scope = _scope(2)
    weak = _event(
        other_scope,
        evidence=_evidence(verification=VerificationStatus.UNVERIFIED, seed=2),
    )
    events.append_task_activity_event(weak)
    provider = FakeLunaProvider([_valid_output()])
    with pytest.raises(EpisodicCandidateExtractionError) as captured:
        EpisodicCandidateExtractionService(
            events, candidates, _gateway(provider), clock=lambda: NOW
        ).extract(other_scope, weak.event_id)
    assert captured.value.code == "MNEMO_EPISODIC_EVIDENCE_INSUFFICIENT"
    assert provider.calls == []
    assert candidates.list_episodic_memory_candidates(other_scope).items == ()


def test_sqlite_candidate_batch_failure_is_atomic(tmp_path: Path) -> None:
    sqlite = SQLiteCheckpointRepository(
        tmp_path / "candidate-atomic.sqlite3", base_directory=tmp_path
    )
    sqlite.migrate()
    source = _event(_scope())
    sqlite.append_task_activity_event(source)
    with sqlite3.connect(sqlite.path) as connection:
        connection.execute(
            "CREATE TRIGGER reject_second_candidate BEFORE INSERT ON "
            "episodic_memory_candidates WHEN NEW.proposal_index = 1 "
            "BEGIN SELECT RAISE(ABORT, 'injected candidate failure'); END"
        )
    provider = FakeLunaProvider(
        [
            {
                "candidates": [
                    {
                        "kind": "outcome",
                        "claim": "The complete gate passed for the bounded implementation.",
                        "confidence": 0.94,
                        "sensitivity": "normal",
                    },
                    {
                        "kind": "lesson",
                        "claim": "Keep the bounded verification step.",
                        "confidence": 0.88,
                        "sensitivity": "normal",
                    },
                ]
            }
        ]
    )

    with pytest.raises(EpisodicMemoryCandidateStorageFailure):
        EpisodicCandidateExtractionService(
            sqlite, sqlite, _gateway(provider), clock=lambda: NOW
        ).extract(source.scope, source.event_id)
    assert sqlite.list_episodic_memory_candidates(source.scope).items == ()


def test_candidate_migration_is_forward_only_atomic_and_preserves_source_event(
    tmp_path: Path,
) -> None:
    sqlite = SQLiteCheckpointRepository(
        tmp_path / "candidate-migration.sqlite3", base_directory=tmp_path
    )
    sqlite.migrate()
    source = _event(_scope())
    sqlite.append_task_activity_event(source)
    with sqlite3.connect(sqlite.path) as connection:
        connection.execute("DROP TABLE episodic_memory_candidate_evidence")
        connection.execute("DROP TABLE episodic_memory_candidates")
        connection.execute("DELETE FROM schema_migrations WHERE version = 20")

    with pytest.raises(SQLiteMigrationError, match="injected migration failure"):
        sqlite.migrate(fail_after_version=20)
    assert sqlite.schema_version() == 19
    assert sqlite.get_task_activity_event(source.scope, source.event_id) == source
    with sqlite3.connect(sqlite.path) as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='episodic_memory_candidates'"
            ).fetchone()
            is None
        )

    sqlite.migrate()
    assert sqlite.schema_version() == 20
    with sqlite3.connect(sqlite.path) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(episodic_memory_candidates)"
            ).fetchall()
        }
    assert {
        "memory_id",
        "source_event_id",
        "proposal_index",
        "memory_kind",
        "claim",
        "confidence",
        "sensitivity",
        "status",
        "extractor_version",
        "provider_id",
        "model_id",
        "prompt_version",
        "retention_policy_id",
        "owner_id",
        "project_id",
        "session_id",
        "task_id",
    } <= columns
