"""Private, content-free automatic route telemetry."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID

from mnemo_memory.packages.telemetry import (
    AutomaticRouteEvent,
    AutomaticRouteOutcome,
    AutomaticRouteScope,
    AutomaticRouteToolCategory,
    LocalAutomaticRouteTelemetryStore,
)

NOW = datetime(2026, 8, 8, 2, 0, tzinfo=UTC)
SCOPE = AutomaticRouteScope(
    owner_id="11111111-1111-4111-8111-111111111111",
    workspace_id="22222222-2222-4222-8222-222222222222",
    project_id="33333333-3333-4333-8333-333333333333",
    session_id="44444444-4444-4444-8444-444444444444",
    task_id="55555555-5555-4555-8555-555555555555",
    visibility="project",
)


def _event(seed: int, *, route: str = "structure", outcome: str = "hit") -> AutomaticRouteEvent:
    return AutomaticRouteEvent(
        event_id=UUID(f"00000000-0000-4000-8000-{seed:012d}"),
        scope=SCOPE,
        observed_at=NOW + timedelta(seconds=seed),
        client="codex",
        route=route,
        reason="architecture",
        outcome=AutomaticRouteOutcome(outcome),
        fallback_route=None,
        maximum_attachment_tokens=1_000,
        canonical_tokens=120,
        rendered_characters=800,
        rendered_bytes=800,
        rendered_estimated_tokens=200,
        duration_ms=12,
        skill_candidate_count=0,
        duplicate_render=False,
    )


def test_route_store_is_bounded_private_and_aggregates_downstream_tool_cost(tmp_path: Path) -> None:
    store = LocalAutomaticRouteTelemetryStore(tmp_path, maximum_events=2)
    store.record(_event(1))
    store.record(_event(2))
    store.record(_event(3))
    store.record_tool_observation(
        _event(3).event_id,
        AutomaticRouteToolCategory.DIRECT_INSPECTION,
        result_characters=401,
    )

    summary = store.summary(SCOPE).to_dict()
    routes = cast(dict[str, dict[str, int]], summary["routes"])

    assert summary["event_count"] == 2
    assert summary["totals"] == {
        "maximum_attachment_tokens": 2_000,
        "canonical_tokens": 240,
        "rendered_bytes": 1_600,
        "rendered_estimated_tokens": 400,
        "tool_result_estimated_tokens": 101,
        "estimated_total_tokens": 501,
        "duration_ms": 24,
        "duplicate_renders": 0,
        "tool_calls": 1,
        "measured_tool_result_calls": 1,
        "unmeasured_tool_calls": 0,
    }
    assert routes["structure"]["events"] == 2
    assert routes["structure"]["hits"] == 2
    assert routes["structure"]["tool_calls"] == 1
    assert routes["structure"]["fallbacks"] == 0
    assert routes["structure"]["duplicate_renders"] == 0
    assert routes["structure"]["maximum_attachment_tokens"] == 2_000
    assert store.path.stat().st_mode & 0o777 == 0o600
    encoded = store.path.read_text(encoding="utf-8")
    assert "prompt" not in encoded
    assert "tool_output" not in encoded


def test_route_store_replaces_preliminary_render_with_final_delivery_counts(
    tmp_path: Path,
) -> None:
    store = LocalAutomaticRouteTelemetryStore(tmp_path)
    event = _event(1)
    store.record(event)

    store.record_delivery(
        event.event_id,
        rendered_characters=1_001,
        rendered_bytes=1_100,
        duplicate_render=True,
    )

    summary = store.summary(SCOPE).to_dict()
    totals = cast(dict[str, int], summary["totals"])
    routes = cast(dict[str, dict[str, int]], summary["routes"])
    assert totals["rendered_bytes"] == 1_100
    assert totals["rendered_estimated_tokens"] == 251
    assert totals["duplicate_renders"] == 1
    assert routes["structure"]["estimated_total_tokens"] == 251
    assert routes["structure"]["duplicate_renders"] == 1


def test_corrupt_route_state_is_reported_without_payload_and_recovers_on_record(
    tmp_path: Path,
) -> None:
    private_marker = "private-corrupt-routing-payload"
    path = tmp_path / "automatic-route-telemetry.json"
    tmp_path.mkdir(exist_ok=True)
    path.write_text(private_marker, encoding="utf-8")
    store = LocalAutomaticRouteTelemetryStore(tmp_path)

    unavailable = store.summary(SCOPE).to_dict()
    assert unavailable["status"] == "corrupt"
    assert private_marker not in str(unavailable)

    store.record(_event(1))
    recovered = store.summary(SCOPE).to_dict()
    assert recovered["status"] == "available"
    assert recovered["event_count"] == 1
    assert private_marker not in store.path.read_text(encoding="utf-8")
