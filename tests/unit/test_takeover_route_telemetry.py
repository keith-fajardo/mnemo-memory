import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

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


def test_stats_buckets_today_week_month_and_all_time(tmp_path: Path) -> None:
    store = LocalTakeoverRouteTelemetryStore(tmp_path)
    now = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
    store.record("extracted", now=now)  # today
    store.record("extracted", now=now - timedelta(days=3))  # within 7 and 30 days
    store.record("handoff", now=now - timedelta(days=10))  # within 30 days only
    store.record("extracted", now=now - timedelta(days=40))  # all-time only
    stats = store.stats(now=now)
    assert stats["today"] == {"local": 1, "frontier": 0}
    assert stats["last_7_days"] == {"local": 2, "frontier": 0}
    assert stats["last_30_days"] == {"local": 2, "frontier": 1}
    assert stats["all_time"] == {"local": 3, "frontier": 1}


def test_stats_migrates_v1_counts_into_untimed_all_time_only(tmp_path: Path) -> None:
    store = LocalTakeoverRouteTelemetryStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps({"version": 1, "counts": {"extracted": 8}}), encoding="utf-8")
    now = datetime(2026, 8, 24, tzinfo=UTC)
    stats = store.stats(now=now)
    assert stats["today"] == {"local": 0, "frontier": 0}
    assert stats["last_30_days"] == {"local": 0, "frontier": 0}
    assert stats["all_time"] == {"local": 8, "frontier": 0}
    # New dated records accrue alongside the migrated untimed total.
    store.record("extracted", now=now)
    assert store.stats(now=now)["today"] == {"local": 1, "frontier": 0}
    assert store.stats(now=now)["all_time"] == {"local": 9, "frontier": 0}


def test_stats_recent_lists_per_day_rows_newest_first(tmp_path: Path) -> None:
    store = LocalTakeoverRouteTelemetryStore(tmp_path)
    now = datetime(2026, 8, 24, tzinfo=UTC)
    store.record("extracted", now=now)
    store.record("handoff", now=now - timedelta(days=1))
    recent = cast(list[tuple[str, int, int]], store.stats(now=now)["recent"])
    assert recent[0] == ("2026-08-24", 1, 0)
    assert recent[1] == ("2026-08-23", 0, 1)
