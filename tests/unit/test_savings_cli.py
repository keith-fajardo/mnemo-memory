from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from mnemo_memory.apps.cli.main import _format_savings_table, app
from mnemo_memory.packages.telemetry import LocalTakeoverRouteTelemetryStore


def test_format_savings_table_shows_estimate_and_all_time_row() -> None:
    stats: dict[str, object] = {
        "today": {"local": 12, "frontier": 1},
        "last_7_days": {"local": 34, "frontier": 2},
        "last_30_days": {"local": 91, "frontier": 5},
        "all_time": {"local": 140, "frontier": 9},
        "recent": [("2026-08-24", 12, 1), ("2026-08-23", 8, 0)],
    }
    table = _format_savings_table(stats, 297)
    assert "TOKENS SAVED" in table
    assert "estimate" in table
    assert "2026-08-24" in table
    assert "All-time" in table
    # 140 local x 297 = 41,580 saved tokens, comma-grouped.
    assert "41,580" in table


def test_format_savings_table_handles_no_dated_activity() -> None:
    stats: dict[str, object] = {
        "today": {"local": 0, "frontier": 0},
        "last_7_days": {"local": 0, "frontier": 0},
        "last_30_days": {"local": 0, "frontier": 0},
        "all_time": {"local": 8, "frontier": 0},
        "recent": [],
    }
    table = _format_savings_table(stats, 297)
    assert "(no dated activity yet)" in table
    assert "2,376" in table  # 8 untimed x 297 still surfaces in the all-time row


def test_savings_command_reports_recorded_local_extractions(tmp_path: Path) -> None:
    data = tmp_path / "data"
    store = LocalTakeoverRouteTelemetryStore(data)
    now = datetime(2026, 8, 24, tzinfo=UTC)
    store.record("extracted", now=now)
    store.record("extracted", now=now)
    store.record("handoff", now=now)

    result = CliRunner().invoke(app, ["savings", "--data-dir", str(data)])
    assert result.exit_code == 0
    assert "TOKENS SAVED" in result.output
    assert "All-time" in result.output


def test_savings_command_json_flag_emits_raw_stats(tmp_path: Path) -> None:
    data = tmp_path / "data"
    now = datetime(2026, 8, 24, tzinfo=UTC)
    LocalTakeoverRouteTelemetryStore(data).record("extracted", now=now)
    result = CliRunner().invoke(app, ["savings", "--data-dir", str(data), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["all_time"]["local"] == 1
