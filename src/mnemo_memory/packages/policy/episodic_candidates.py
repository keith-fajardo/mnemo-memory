"""Mandatory safety policy for inactive episodic-memory candidates."""

from __future__ import annotations

from mnemo_memory.packages.domain import (
    EpisodicCandidateReviewAction,
    EpisodicMemoryCandidate,
    Sensitivity,
)

from .content_safety import ContentSafetyClassifier, ContentSafetyDecision, ContentSafetyPolicy

_SENSITIVITY_ORDER = {
    Sensitivity.NORMAL: 0,
    Sensitivity.PERSONAL: 1,
    Sensitivity.CONFIDENTIAL: 2,
    Sensitivity.RESTRICTED: 3,
}


class EpisodicMemoryCandidateSafetyPolicy:
    def __init__(self, additional_classifiers: tuple[ContentSafetyClassifier, ...] = ()) -> None:
        self._content_safety = ContentSafetyPolicy(additional_classifiers)

    def assess(self, candidate: EpisodicMemoryCandidate) -> ContentSafetyDecision:
        if not isinstance(candidate, EpisodicMemoryCandidate):
            raise TypeError("episodic candidate safety requires a canonical candidate")
        decision = self._content_safety.assess(
            candidate.memory.claim,
            *(
                value
                for evidence in candidate.evidence_references
                for value in (evidence.immutable_source_ref, evidence.location.uri)
            ),
        )
        if not decision.accepted:
            return ContentSafetyDecision(
                False, Sensitivity.PROHIBITED, "MNEMO_EPISODIC_CANDIDATE_REJECTED"
            )
        if (
            _SENSITIVITY_ORDER[decision.sensitivity]
            > _SENSITIVITY_ORDER[candidate.memory.classification.sensitivity]
        ):
            return ContentSafetyDecision(
                False,
                Sensitivity.PROHIBITED,
                "MNEMO_EPISODIC_CANDIDATE_SENSITIVITY_UNDERSPECIFIED",
            )
        return ContentSafetyDecision(True, candidate.memory.classification.sensitivity)


class EpisodicCandidateReviewSafetyPolicy:
    def __init__(self, additional_classifiers: tuple[ContentSafetyClassifier, ...] = ()) -> None:
        self._candidate_safety = EpisodicMemoryCandidateSafetyPolicy(additional_classifiers)
        self._content_safety = ContentSafetyPolicy(additional_classifiers)

    def assess(
        self,
        candidate: EpisodicMemoryCandidate,
        action: EpisodicCandidateReviewAction,
    ) -> ContentSafetyDecision:
        if not isinstance(action, EpisodicCandidateReviewAction):
            raise TypeError("episodic candidate review safety requires a canonical action")
        candidate_decision = self._candidate_safety.assess(candidate)
        if not candidate_decision.accepted:
            return candidate_decision
        if action.scope != candidate.scope or action.candidate_id != candidate.memory_id:
            return ContentSafetyDecision(
                False, Sensitivity.PROHIBITED, "MNEMO_EPISODIC_REVIEW_TARGET_MISMATCH"
            )
        decision = self._content_safety.assess(
            action.source_action_key,
            action.reason,
            *(
                value
                for evidence in action.evidence_references
                for value in (evidence.immutable_source_ref, evidence.location.uri)
            ),
        )
        if not decision.accepted:
            return ContentSafetyDecision(
                False, Sensitivity.PROHIBITED, "MNEMO_EPISODIC_REVIEW_REJECTED"
            )
        return ContentSafetyDecision(True, decision.sensitivity)
