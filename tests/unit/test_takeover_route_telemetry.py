from pathlib import Path

import pytest

from mnemo_memory.packages.telemetry.takeover_routes import (
    LocalTakeoverRouteTelemetryStore,
    TakeoverRouteTelemetry,
)


def test_records_route_counts_content_free() -> None:
    t = TakeoverRouteTelemetry()
    t.record("local", escalated=False, duration_ms=12)
    t.record("frontier", escalated=True, duration_ms=1800)
    assert t.counts() == {"local": 1, "frontier": 1}


def test_rejects_unknown_route() -> None:
    t = TakeoverRouteTelemetry()
    with pytest.raises(ValueError):
        t.record("secretstuff", escalated=False, duration_ms=1)


def test_persisted_store_counts_survive_new_instance(tmp_path: Path) -> None:
    LocalTakeoverRouteTelemetryStore(tmp_path).record("extracted")
    reopened = LocalTakeoverRouteTelemetryStore(tmp_path)
    reopened.record("handoff")
    assert reopened.counts()["extracted"] == 1
    assert reopened.counts()["handoff"] == 1


def test_persisted_store_rejects_unknown_status(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        LocalTakeoverRouteTelemetryStore(tmp_path).record("secretstuff")


def test_savings_ratio_counts_local_versus_frontier(tmp_path: Path) -> None:
    store = LocalTakeoverRouteTelemetryStore(tmp_path)
    for _ in range(3):
        store.record("extracted")
    store.record("handoff")
    store.record("local_failed")
    savings = store.savings()
    assert savings["local"] == 3
    assert savings["frontier"] == 2
    assert savings["ratio"] == pytest.approx(0.6)


def test_savings_excludes_not_applicable_statuses(tmp_path: Path) -> None:
    store = LocalTakeoverRouteTelemetryStore(tmp_path)
    store.record("extraction_disabled")
    store.record("error")
    savings = store.savings()
    assert savings["local"] == 0
    assert savings["frontier"] == 0
    assert savings["ratio"] is None


def test_persisted_store_is_content_free(tmp_path: Path) -> None:
    store = LocalTakeoverRouteTelemetryStore(tmp_path)
    store.record("extracted")
    stored = store.path.read_text(encoding="utf-8")
    assert set(store.counts()) <= {
        "extracted",
        "handoff",
        "local_failed",
        "extraction_disabled",
        "error",
    }
    assert not {"claim", "summary", "prompt", "payload", "text"} & set(stored.lower().split())
