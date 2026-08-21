import pytest
from mnemo_memory.packages.telemetry.takeover_routes import TakeoverRouteTelemetry

def test_records_route_counts_content_free():
    t = TakeoverRouteTelemetry()
    t.record("local", escalated=False, duration_ms=12)
    t.record("frontier", escalated=True, duration_ms=1800)
    assert t.counts() == {"local": 1, "frontier": 1}

def test_rejects_unknown_route():
    t = TakeoverRouteTelemetry()
    with pytest.raises(ValueError):
        t.record("secretstuff", escalated=False, duration_ms=1)
