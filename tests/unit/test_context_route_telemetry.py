"""Private, content-free automatic route telemetry."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest
from typer.testing import CliRunner

from mnemo_memory.apps.cli.main import app
from mnemo_memory.packages.application.automatic_memory import LocalMemoryProjectBindingStore
from mnemo_memory.packages.telemetry import (
    AutomaticRouteDiagnosticsMode,
    AutomaticRouteDiagnosticsSettings,
    AutomaticRouteEvent,
    AutomaticRouteFeedback,
    AutomaticRouteOutcome,
    AutomaticRouteScope,
    AutomaticRouteTelemetryError,
    AutomaticRouteToolCategory,
    LocalAutomaticRouteDiagnosticsSettingsStore,
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


def test_trace_fields_are_content_free_backward_compatible_and_labelable(tmp_path: Path) -> None:
    store = LocalAutomaticRouteTelemetryStore(tmp_path)
    traced = replace(
        _event(1),
        shadow_structural_need="yes",
        shadow_long_term_need="yes",
        shadow_reason="potion_proposal",
        shadow_structural_tokens=600,
        shadow_long_term_tokens=700,
        shadow_shared_maximum_tokens=1_300,
        shadow_action="push_both",
        shadow_estimated_tokens=1_300,
        shadow_duration_ms=5,
        semantic_invoked=True,
        semantic_route="structure",
        semantic_latency_ms=14,
    )
    store.record(traced)

    assert store.events(SCOPE) == (traced,)
    assert store.record_feedback(SCOPE, traced.event_id, AutomaticRouteFeedback.HELPFUL) is True
    labeled = store.events(SCOPE)[0]
    assert labeled.feedback is AutomaticRouteFeedback.HELPFUL
    encoded = store.path.read_text(encoding="utf-8")
    assert "prompt" not in encoded and "embedding" not in encoded and "score" not in encoded

    legacy = _event(2).to_dict()
    assert "shadow_structural_need" not in legacy
    store.path.write_text(json.dumps({"version": 1, "events": [legacy]}), encoding="utf-8")
    assert store.summary(SCOPE).event_count == 1

    old_shadow = traced.to_dict()
    old_shadow.pop("shadow_action")
    old_shadow.pop("shadow_estimated_tokens")
    old_shadow.pop("shadow_duration_ms")
    store.path.write_text(json.dumps({"version": 1, "events": [old_shadow]}), encoding="utf-8")
    replayed = store.events(SCOPE)[0]
    assert replayed.shadow_action is None
    assert replayed.semantic_latency_ms == 14


def test_diagnostic_settings_are_private_strict_and_default_to_summary(tmp_path: Path) -> None:
    store = LocalAutomaticRouteDiagnosticsSettingsStore(tmp_path)

    assert store.load() == AutomaticRouteDiagnosticsSettings()
    saved = store.save(AutomaticRouteDiagnosticsSettings(AutomaticRouteDiagnosticsMode.TRACE, 14))

    assert store.load() == saved
    assert store.path.stat().st_mode & 0o777 == 0o600
    store.path.write_text('{"mode":"trace","prompt":"private"}', encoding="utf-8")
    with pytest.raises(AutomaticRouteTelemetryError, match="settings"):
        store.load()


def test_exact_scope_purge_does_not_remove_another_project(tmp_path: Path) -> None:
    other_scope = replace(
        SCOPE,
        project_id="66666666-6666-4666-8666-666666666666",
        task_id="77777777-7777-4777-8777-777777777777",
    )
    store = LocalAutomaticRouteTelemetryStore(tmp_path)
    store.record(_event(1))
    store.record(replace(_event(2), scope=other_scope))

    assert store.purge(SCOPE) == 1
    assert store.events(SCOPE) == ()
    assert len(store.events(other_scope)) == 1


def test_diagnostic_cli_turns_trace_on_labels_and_purges_exact_scope(tmp_path: Path) -> None:
    data = tmp_path / "data"
    project = tmp_path / "project"
    project.mkdir()
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    scope = AutomaticRouteScope(
        str(binding.checkpoint_scope.owner_id),
        str(binding.checkpoint_scope.workspace_id),
        str(binding.checkpoint_scope.project_id),
        str(binding.checkpoint_scope.session_id),
        str(binding.checkpoint_scope.task_id),
        binding.checkpoint_scope.visibility.value,
    )
    event = replace(_event(9), scope=scope, observed_at=datetime.now(UTC))
    traced = replace(
        _event(10),
        scope=scope,
        observed_at=event.observed_at + timedelta(seconds=1),
        shadow_structural_need="yes",
        shadow_long_term_need="no",
        shadow_reason="potion_proposal",
        shadow_structural_tokens=1_000,
        shadow_shared_maximum_tokens=1_300,
        shadow_action="push_structure",
        shadow_estimated_tokens=1_000,
        shadow_duration_ms=5,
        semantic_invoked=True,
        semantic_route="structure",
        semantic_latency_ms=14,
    )
    telemetry = LocalAutomaticRouteTelemetryStore(data)
    telemetry.record(event)
    telemetry.record(traced)
    common = ["--project-dir", str(project), "--data-dir", str(data)]
    runner = CliRunner()

    enabled = runner.invoke(
        app, ["memory", "diagnostics", "on", "--retention-days", "14", "--data-dir", str(data)]
    )
    shown = runner.invoke(app, ["memory", "diagnostics", "show", *common])
    shown_json = runner.invoke(app, ["memory", "diagnostics", "show", "--format", "json", *common])
    shown_table = runner.invoke(
        app, ["memory", "diagnostics", "show", "--format", "table", *common]
    )
    invalid_format = runner.invoke(
        app, ["memory", "diagnostics", "show", "--format", "csv", *common]
    )
    marked = runner.invoke(
        app,
        ["memory", "diagnostics", "mark", str(event.event_id), "helpful", *common],
    )
    disabled = runner.invoke(app, ["memory", "diagnostics", "off", "--data-dir", str(data)])
    purged = runner.invoke(app, ["memory", "diagnostics", "purge", "--yes", *common])
    shown_empty = runner.invoke(
        app, ["memory", "diagnostics", "show", "--format", "table", *common]
    )

    assert enabled.exit_code == shown.exit_code == marked.exit_code == disabled.exit_code == 0
    assert (
        shown_json.exit_code
        == shown_table.exit_code
        == purged.exit_code
        == shown_empty.exit_code
        == 0
    )
    assert invalid_format.exit_code == 2
    assert json.loads(enabled.output)["mode"] == "trace"
    assert json.loads(shown.output)["notice"].endswith("does not prove causation.")
    assert json.loads(shown.output) == json.loads(shown_json.output)
    traced_json = json.loads(shown.output)["events"][0]
    assert traced_json["shadow_action"] == "push_structure"
    assert traced_json["shadow_budget"]["estimated_attachment_tokens"] == 1_000
    assert traced_json["shadow_duration_ms"] == 14
    assert traced_json["total_routing_duration_ms"] == 26
    table_lines = shown_table.output.splitlines()
    assert table_lines[0].split() == [
        "TIME",
        "LIVE",
        "OUTCOME",
        "REASON",
        "SHADOW",
        "STRUCT",
        "LONG",
        "TOKENS",
        "PLAN_TOK",
        "ROUTE_MS",
        "SHADOW_MS",
        "POTION",
        "POTION_MS",
        "TOTAL_MS",
        "FEEDBACK",
        "EVENT_ID",
    ]
    assert "structure  hit" in shown_table.output
    assert "push_structure  yes     no" in shown_table.output
    assert "structure" in shown_table.output
    assert str(traced.event_id) in shown_table.output
    assert str(event.event_id) in shown_table.output
    assert shown_table.output.rstrip().endswith("does not prove causation.")
    assert "json" in invalid_format.output and "table" in invalid_format.output
    assert json.loads(marked.output)["changes_routing"] is False
    assert json.loads(disabled.output)["existing_events_retained"] is True
    assert json.loads(purged.output)["removed_events"] == 2
    assert shown_empty.output.splitlines()[0].split()[-1] == "EVENT_ID"
    assert str(event.event_id) not in shown_empty.output
    assert shown_empty.output.rstrip().endswith("does not prove causation.")
