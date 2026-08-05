"""Deterministic secret gate for explicit approved episodic fact payloads."""

from __future__ import annotations

from dataclasses import dataclass

from mnemo_memory.packages.domain import (
    ApprovedEpisodicEvent,
    ApprovedEpisodicEventGovernance,
    ApprovedEpisodicEventPinAction,
    EvidenceReference,
    Sensitivity,
)

from .content_safety import ContentSafetyClassifier, ContentSafetyPolicy


@dataclass(frozen=True, slots=True)
class ApprovedEpisodicEventSafetyDecision:
    accepted: bool
    sensitivity: Sensitivity
    code: str | None = None

    def __post_init__(self) -> None:
        if self.accepted != (self.code is None) or (
            self.accepted == (self.sensitivity is Sensitivity.PROHIBITED)
        ):
            raise ValueError("approved event safety decision is inconsistent")


class ApprovedEpisodicEventSafetyPolicy:
    """Reject clear credential-shaped values before canonical episodic persistence."""

    def __init__(self, additional_classifiers: tuple[ContentSafetyClassifier, ...] = ()) -> None:
        self._content_safety = ContentSafetyPolicy(additional_classifiers)

    def assess_event(self, event: ApprovedEpisodicEvent) -> ApprovedEpisodicEventSafetyDecision:
        if not isinstance(event, ApprovedEpisodicEvent):
            raise TypeError("approved event safety policy requires a canonical event")
        return self._decision(
            event.summary,
            event.source_event_key,
            *self._evidence_values(event.evidence_references),
        )

    def assess_governance(
        self, governance: ApprovedEpisodicEventGovernance
    ) -> ApprovedEpisodicEventSafetyDecision:
        if not isinstance(governance, ApprovedEpisodicEventGovernance):
            raise TypeError("approved event safety policy requires canonical governance")
        return self._decision(
            governance.reason,
            governance.source_action_key,
            *self._evidence_values(governance.evidence_references),
        )

    def assess_pin(
        self, action: ApprovedEpisodicEventPinAction
    ) -> ApprovedEpisodicEventSafetyDecision:
        if not isinstance(action, ApprovedEpisodicEventPinAction):
            raise TypeError("approved event safety policy requires a canonical pin action")
        return self._decision(
            action.source_action_key,
            *self._evidence_values(action.evidence_references),
        )

    def _decision(self, *values: str) -> ApprovedEpisodicEventSafetyDecision:
        decision = self._content_safety.assess(*values)
        code = decision.code
        if code == "MNEMO_CONTENT_SECRET_REJECTED":
            code = "MNEMO_APPROVED_EVENT_SECRET_REJECTED"
        return ApprovedEpisodicEventSafetyDecision(decision.accepted, decision.sensitivity, code)

    @staticmethod
    def _evidence_values(
        evidence_references: tuple[EvidenceReference, ...],
    ) -> tuple[str, ...]:
        return tuple(
            value
            for evidence in evidence_references
            for value in (evidence.immutable_source_ref, evidence.location.uri)
        )
