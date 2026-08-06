"""Authorization-first conversion of one minimized event into inactive candidates."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from mnemo_memory.packages.domain import (
    EpisodicExtractionPort,
    EpisodicExtractionRequest,
    EpisodicMemoryCandidate,
    EventId,
    MemoryScope,
    Sensitivity,
    SourceTrustClass,
    VerificationStatus,
)
from mnemo_memory.packages.policy import ContentSafetyPolicy
from mnemo_memory.packages.storage.contracts import (
    EpisodicMemoryCandidateRepository,
    EpisodicMemoryCandidateStoreResult,
    TaskActivityEventRepository,
)

_SENSITIVITY_ORDER = {
    Sensitivity.NORMAL: 0,
    Sensitivity.PERSONAL: 1,
    Sensitivity.CONFIDENTIAL: 2,
    Sensitivity.RESTRICTED: 3,
}


class EpisodicCandidateExtractionError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class EpisodicCandidateExtractionResult:
    candidates: tuple[EpisodicMemoryCandidate, ...]
    idempotent: bool


class EpisodicCandidateExtractionService:
    def __init__(
        self,
        events: TaskActivityEventRepository,
        candidates: EpisodicMemoryCandidateRepository,
        extractor: EpisodicExtractionPort,
        *,
        clock: Callable[[], datetime],
        content_safety: ContentSafetyPolicy | None = None,
    ) -> None:
        self._events = events
        self._candidates = candidates
        self._extractor = extractor
        self._clock = clock
        self._content_safety = content_safety or ContentSafetyPolicy()

    def extract(
        self, scope: MemoryScope, source_event_id: EventId
    ) -> EpisodicCandidateExtractionResult:
        event = self._events.get_task_activity_event(scope, source_event_id)
        if not any(
            evidence.verification_status is VerificationStatus.VERIFIED
            and evidence.trust_class is not SourceTrustClass.ASSISTANT_INFERENCE
            for evidence in event.evidence_references
        ):
            raise EpisodicCandidateExtractionError("MNEMO_EPISODIC_EVIDENCE_INSUFFICIENT")
        proposals = self._extractor.extract(scope, EpisodicExtractionRequest.from_event(event))
        if len(proposals) > 4:
            raise EpisodicCandidateExtractionError("MNEMO_EPISODIC_INVALID_OUTPUT")
        created_at = self._clock()
        values: list[EpisodicMemoryCandidate] = []
        for index, proposal in enumerate(proposals):
            decision = self._content_safety.assess(
                proposal.claim,
                *(evidence.immutable_source_ref for evidence in event.evidence_references),
            )
            if not decision.accepted:
                raise EpisodicCandidateExtractionError("MNEMO_EPISODIC_CONTENT_REJECTED")
            sensitivity = max(
                (event.sensitivity, proposal.sensitivity, decision.sensitivity),
                key=_SENSITIVITY_ORDER.__getitem__,
            )
            values.append(
                EpisodicMemoryCandidate.create(
                    source_event=event,
                    proposal=proposal,
                    proposal_index=index,
                    sensitivity=sensitivity,
                    extractor_version=self._extractor.extractor_version,
                    provider_id=self._extractor.provider_id,
                    model_id=self._extractor.model_id,
                    prompt_version=self._extractor.prompt_version,
                    created_at=created_at,
                )
            )
        if not values:
            return EpisodicCandidateExtractionResult((), False)
        stored: EpisodicMemoryCandidateStoreResult = (
            self._candidates.store_episodic_memory_candidates(tuple(values))
        )
        return EpisodicCandidateExtractionResult(stored.candidates, stored.idempotent)
