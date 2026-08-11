"""Content-free Mnemo telemetry contracts and local personal adapters."""

from .automatic_routes import (
    AutomaticRouteDiagnosticsMode,
    AutomaticRouteDiagnosticsSettings,
    AutomaticRouteEvent,
    AutomaticRouteFeedback,
    AutomaticRouteOutcome,
    AutomaticRouteScope,
    AutomaticRouteSummary,
    AutomaticRouteTelemetryError,
    AutomaticRouteToolCategory,
    LocalAutomaticRouteDiagnosticsSettingsStore,
    LocalAutomaticRouteTelemetryStore,
)
from .checkpoint_saves import (
    CheckpointSaveDiagnosticEvent,
    CheckpointSaveOutcome,
    CheckpointSaveTelemetryError,
    LocalCheckpointSaveTelemetryStore,
)

__all__ = [
    "AutomaticRouteDiagnosticsMode",
    "AutomaticRouteDiagnosticsSettings",
    "AutomaticRouteEvent",
    "AutomaticRouteFeedback",
    "AutomaticRouteOutcome",
    "AutomaticRouteScope",
    "AutomaticRouteSummary",
    "AutomaticRouteTelemetryError",
    "AutomaticRouteToolCategory",
    "CheckpointSaveDiagnosticEvent",
    "CheckpointSaveOutcome",
    "CheckpointSaveTelemetryError",
    "LocalAutomaticRouteDiagnosticsSettingsStore",
    "LocalAutomaticRouteTelemetryStore",
    "LocalCheckpointSaveTelemetryStore",
]
