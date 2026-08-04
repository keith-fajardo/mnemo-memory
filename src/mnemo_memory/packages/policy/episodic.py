"""Deterministic secret gate for explicit approved episodic fact payloads."""

from __future__ import annotations

from dataclasses import dataclass

from mnemo_memory.packages.domain import ApprovedEpisodicEvent, ApprovedEpisodicEventGovernance

from .knowledge import contains_high_confidence_secret


@dataclass(frozen=True, slots=True)
class ApprovedEpisodicEventSafetyDecision:
    accepted: bool
    code: str | None = None

    def __post_init__(self) -> None:
        if self.accepted != (self.code is None):
            raise ValueError("approved event safety decision is inconsistent")


class ApprovedEpisodicEventSafetyPolicy:
    """Reject clear credential-shaped values before canonical episodic persistence."""

    def assess_event(self, event: ApprovedEpisodicEvent) -> ApprovedEpisodicEventSafetyDecision:
        if not isinstance(event, ApprovedEpisodicEvent):
            raise TypeError("approved event safety policy requires a canonical event")
        return self._decision(event.summary)

    def assess_governance(
        self, governance: ApprovedEpisodicEventGovernance
    ) -> ApprovedEpisodicEventSafetyDecision:
        if not isinstance(governance, ApprovedEpisodicEventGovernance):
            raise TypeError("approved event safety policy requires canonical governance")
        return self._decision(governance.reason)

    @staticmethod
    def _decision(value: str) -> ApprovedEpisodicEventSafetyDecision:
        if contains_high_confidence_secret(value):
            return ApprovedEpisodicEventSafetyDecision(
                False, "MNEMO_APPROVED_EVENT_SECRET_REJECTED"
            )
        return ApprovedEpisodicEventSafetyDecision(True)
