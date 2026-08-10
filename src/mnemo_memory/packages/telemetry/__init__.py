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
    "LocalAutomaticRouteDiagnosticsSettingsStore",
    "LocalAutomaticRouteTelemetryStore",
]
