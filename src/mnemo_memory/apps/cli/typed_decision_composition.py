"""Runtime composition for typed decisions (spec 2026-10-02 §3, §6).

The prompt hook asks Jev only through a guard built here and bound to the runtime source, so the
``synthetic_only`` data route blocks every real prompt before credential, budget or network
work. The replay's synthetic-source builder lives here too, so this module stays the only
importer of the Jev connector.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from uuid import UUID

from mnemo_memory.connectors.typesafe import JevClassifier, JevTransport
from mnemo_memory.packages.application import PersonalSettings
from mnemo_memory.packages.domain import (
    ModelBudgetReservation,
    ModelTaskType,
    TypedDecisionDataRoute,
    TypedDecisionSource,
    WorkspaceId,
)
from mnemo_memory.packages.model_gateway.decision_axes import FILLER_CHECK_BUDGET_SECONDS
from mnemo_memory.packages.model_gateway.typed_decisions import (
    GuardedTypedDecisionClassifier,
    TypedDecisionRecorder,
)
from mnemo_memory.packages.storage import LocalDailyModelBudget

RUNTIME_DEADLINE_SECONDS = FILLER_CHECK_BUDGET_SECONDS
_RUNTIME_RESERVATION = ModelBudgetReservation(input_tokens=1_000, output_tokens=1, cost_microusd=0)
_LOCAL_WORKSPACE = WorkspaceId(UUID(int=0))


def build_runtime_typed_decision_classifier(
    settings: PersonalSettings,
    *,
    data_directory: Path,
    environ: Mapping[str, str] | None = None,
    jev_transport: JevTransport | None = None,
    recorder: TypedDecisionRecorder | None = None,
) -> GuardedTypedDecisionClassifier | None:
    """Return ``None`` when disabled; otherwise a guard bound to the runtime source."""

    if not settings.experimental_typed_decisions_enabled:
        return None
    return _guard(
        settings, TypedDecisionSource.RUNTIME, data_directory, environ, jev_transport, recorder
    )


def build_synthetic_typed_decision_classifier(
    settings: PersonalSettings,
    *,
    data_directory: Path,
    environ: Mapping[str, str] | None = None,
    jev_transport: JevTransport | None = None,
    recorder: TypedDecisionRecorder | None = None,
) -> GuardedTypedDecisionClassifier:
    """Return a synthetic-fixture guard for the replay (spec §8.2); the hook never calls it.

    Its source lets fixture text through the ``synthetic_only`` route, so callers must pass only
    text read from fixtures that declare synthetic provenance.
    """

    return _guard(
        settings,
        TypedDecisionSource.SYNTHETIC_FIXTURE,
        data_directory,
        environ,
        jev_transport,
        recorder,
    )


def _guard(
    settings: PersonalSettings,
    source: TypedDecisionSource,
    data_directory: Path,
    environ: Mapping[str, str] | None,
    transport: JevTransport | None,
    recorder: TypedDecisionRecorder | None,
) -> GuardedTypedDecisionClassifier:
    variables = os.environ if environ is None else environ
    return GuardedTypedDecisionClassifier(
        _adapter_from_environment(settings, variables, transport),
        data_route=TypedDecisionDataRoute(settings.typed_decision_data_route),
        source=source,
        budget=LocalDailyModelBudget(
            data_directory,
            task_type=ModelTaskType.TYPED_DECISION,
            daily_input_tokens=settings.typed_decision_daily_input_tokens,
        ),
        workspace_id=_LOCAL_WORKSPACE,
        reservation=_RUNTIME_RESERVATION,
        deadline_seconds=RUNTIME_DEADLINE_SECONDS,
        recorder=recorder,
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
