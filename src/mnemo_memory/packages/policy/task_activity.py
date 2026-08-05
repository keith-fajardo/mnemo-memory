"""Pre-persistence safety policy for explicit minimized task activity events."""

from __future__ import annotations

from mnemo_memory.packages.domain import Sensitivity, TaskActivityEvent

from .content_safety import ContentSafetyClassifier, ContentSafetyDecision, ContentSafetyPolicy

_SENSITIVITY_ORDER = {
    Sensitivity.NORMAL: 0,
    Sensitivity.PERSONAL: 1,
    Sensitivity.CONFIDENTIAL: 2,
    Sensitivity.RESTRICTED: 3,
}


class TaskActivityEventSafetyPolicy:
    """Reject secrets and labels weaker than the composed classification result."""

    def __init__(self, additional_classifiers: tuple[ContentSafetyClassifier, ...] = ()) -> None:
        self._content_safety = ContentSafetyPolicy(additional_classifiers)

    def assess(self, event: TaskActivityEvent) -> ContentSafetyDecision:
        if not isinstance(event, TaskActivityEvent):
            raise TypeError("task activity safety policy requires a canonical event")
        decision = self._content_safety.assess(
            event.summary,
            event.source_event_key,
            *(
                value
                for evidence in event.evidence_references
                for value in (evidence.immutable_source_ref, evidence.location.uri)
            ),
        )
        if not decision.accepted:
            code = decision.code
            if code == "MNEMO_CONTENT_SECRET_REJECTED":
                code = "MNEMO_TASK_ACTIVITY_SECRET_REJECTED"
            assert code is not None
            return ContentSafetyDecision(False, Sensitivity.PROHIBITED, code)
        if _SENSITIVITY_ORDER[decision.sensitivity] > _SENSITIVITY_ORDER[event.sensitivity]:
            return ContentSafetyDecision(
                False,
                Sensitivity.PROHIBITED,
                "MNEMO_TASK_ACTIVITY_SENSITIVITY_UNDERSPECIFIED",
            )
        return ContentSafetyDecision(True, event.sensitivity)
