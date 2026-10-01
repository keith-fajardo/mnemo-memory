"""Runtime composition for typed decisions (phase 1: never sends runtime text).

Phase 1 composes the guard only so the data-route rule is enforced and tested at a real
composition root. Nothing in the hook or the MCP server calls it yet (spec §9).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from uuid import UUID

from mnemo_memory.connectors.typesafe import JevClassifier, JevTransport
from mnemo_memory.packages.application import PersonalSettings
from mnemo_memory.packages.domain import (
    ModelBudgetDenied,
    ModelBudgetReservation,
    ModelTaskType,
    TypedDecisionDataRoute,
    TypedDecisionSource,
    WorkspaceId,
)
from mnemo_memory.packages.model_gateway.typed_decisions import GuardedTypedDecisionClassifier

RUNTIME_DEADLINE_SECONDS = 0.6
_RUNTIME_RESERVATION = ModelBudgetReservation(input_tokens=1_000, output_tokens=1, cost_microusd=0)
_LOCAL_WORKSPACE = WorkspaceId(UUID(int=0))


class DenyAllModelBudget:
    """Phase 1 has no personal typed-decision budget, so every reservation is denied."""

    def reserve(
        self,
        workspace_id: WorkspaceId,
        task_type: ModelTaskType,
        reservation: ModelBudgetReservation,
    ) -> None:
        raise ModelBudgetDenied("MNEMO_TYPED_DECISION_BUDGET_UNAVAILABLE")


def build_runtime_typed_decision_classifier(
    settings: PersonalSettings,
    *,
    environ: Mapping[str, str] | None = None,
    jev_transport: JevTransport | None = None,
) -> GuardedTypedDecisionClassifier | None:
    """Return ``None`` when disabled; otherwise a guard bound to the runtime source."""

    if not settings.experimental_typed_decisions_enabled:
        return None
    variables = os.environ if environ is None else environ
    return GuardedTypedDecisionClassifier(
        _adapter_from_environment(settings, variables, jev_transport),
        data_route=TypedDecisionDataRoute(settings.typed_decision_data_route),
        source=TypedDecisionSource.RUNTIME,
        budget=DenyAllModelBudget(),
        workspace_id=_LOCAL_WORKSPACE,
        reservation=_RUNTIME_RESERVATION,
        deadline_seconds=RUNTIME_DEADLINE_SECONDS,
    )


def _adapter_from_environment(
    settings: PersonalSettings,
    environ: Mapping[str, str],
    transport: JevTransport | None,
) -> JevClassifier | None:
    """Return a Jev adapter, or ``None`` when the key is missing or malformed.

    A malformed key disables the feature quietly (the guard reports ``no_credential``) instead
    of failing composition; the key is never echoed.
    """

    api_key = environ.get("TYPESAFE_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        return JevClassifier(
            api_key, model_id=settings.typed_decision_model_id, transport=transport
        )
    except ValueError:
        return None
