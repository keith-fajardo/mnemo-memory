"""Content-free Mnemo telemetry contracts and local personal adapters."""

from .automatic_routes import (
    AutomaticRouteEvent,
    AutomaticRouteOutcome,
    AutomaticRouteScope,
    AutomaticRouteSummary,
    AutomaticRouteTelemetryError,
    AutomaticRouteToolCategory,
    LocalAutomaticRouteTelemetryStore,
)

__all__ = [
    "AutomaticRouteEvent",
    "AutomaticRouteOutcome",
    "AutomaticRouteScope",
    "AutomaticRouteSummary",
    "AutomaticRouteTelemetryError",
    "AutomaticRouteToolCategory",
    "LocalAutomaticRouteTelemetryStore",
]
