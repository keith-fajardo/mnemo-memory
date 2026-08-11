from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from mnemo_memory.packages.telemetry import (
    AutomaticRouteScope,
    CheckpointSaveDiagnosticEvent,
    CheckpointSaveOutcome,
    CheckpointSaveTelemetryError,
    LocalCheckpointSaveTelemetryStore,
)

NOW = datetime(2026, 8, 11, 3, 0, tzinfo=UTC)


def scope(task: str = "55555555-5555-4555-8555-555555555555") -> AutomaticRouteScope:
    return AutomaticRouteScope(
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
        "33333333-3333-4333-8333-333333333333",
        "44444444-4444-4444-8444-444444444444",
        task,
        "project",
    )


def event(
    outcome: CheckpointSaveOutcome = CheckpointSaveOutcome.FAILURE,
    *,
    observed_at: datetime = NOW,
) -> CheckpointSaveDiagnosticEvent:
    return CheckpointSaveDiagnosticEvent(
        uuid4(),
        scope(),
        observed_at,
        "record_lesson",
        outcome,
        7,
        "MNEMO_INVALID_INPUT" if outcome is CheckpointSaveOutcome.FAILURE else None,
        None if outcome is CheckpointSaveOutcome.FAILURE else 188,
        None if outcome is CheckpointSaveOutcome.FAILURE else True,
    )


def test_checkpoint_save_event_is_sparse_and_contains_no_payload_fields() -> None:
    failed = event().to_dict()

    assert "token_estimate" not in failed
    assert "compacted" not in failed
    assert None not in failed.values()
    assert not {
        "checkpoint_id",
        "checkpoint_text",
        "path",
        "hash",
        "prompt",
        "payload",
    }.intersection(failed)
    assert CheckpointSaveDiagnosticEvent.from_dict(failed).to_dict() == failed


def test_checkpoint_save_store_is_private_bounded_and_exact_scope(tmp_path: Path) -> None:
    store = LocalCheckpointSaveTelemetryStore(tmp_path, maximum_events=2, retention_days=7)
    old = event(observed_at=NOW - timedelta(days=8))
    first = event(observed_at=NOW)
    second = event(CheckpointSaveOutcome.SUCCESS, observed_at=NOW + timedelta(seconds=1))

    store.record(old)
    store.record(first)
    store.record(second)

    assert store.events(scope()) == (second, first)
    assert store.events(scope("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")) == ()
    assert store.path.stat().st_mode & 0o777 == 0o600
    encoded = json.loads(store.path.read_text(encoding="utf-8"))
    assert len(encoded["events"]) == 2
    assert store.purge(scope()) == 2
    assert store.events(scope()) == ()


def test_checkpoint_save_store_rejects_corrupt_and_symlink_state(tmp_path: Path) -> None:
    store = LocalCheckpointSaveTelemetryStore(tmp_path)
    store.path.write_text("not json", encoding="utf-8")
    with pytest.raises(CheckpointSaveTelemetryError):
        store.events(scope())

    store.path.unlink()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    store.path.symlink_to(outside)
    with pytest.raises(CheckpointSaveTelemetryError):
        store.events(scope())
