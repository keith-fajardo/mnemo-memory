import pytest
from mnemo_memory.packages.model_gateway.local_first_takeover import (
    run_local_first_takeover,
)

def _ok(x): return None
def _bad(x): raise ValueError("invalid")

def test_valid_local_returns_local_no_frontier():
    routes = []
    calls = {"frontier": 0}
    def frontier():
        calls["frontier"] += 1
        return "F"
    out = run_local_first_takeover(
        local=lambda: "L", frontier=frontier, validate=_ok,
        authorized=lambda: True, reserve_frontier=lambda: None,
        on_route=routes.append,
    )
    assert out == "L" and calls["frontier"] == 0 and routes == ["local"]

def test_invalid_local_escalates_once_to_frontier():
    routes = []
    validate = lambda x: None if x == "F" else (_ for _ in ()).throw(ValueError())
    out = run_local_first_takeover(
        local=lambda: "L", frontier=lambda: "F", validate=validate,
        authorized=lambda: True, reserve_frontier=lambda: None,
        on_route=routes.append,
    )
    assert out == "F" and routes == ["frontier"]

def test_no_frontier_provider_fails_closed():
    with pytest.raises(ValueError):
        run_local_first_takeover(
            local=lambda: "L", frontier=None, validate=_bad,
            authorized=lambda: True, reserve_frontier=lambda: None,
        )

def test_unauthorized_does_not_call_frontier():
    calls = {"frontier": 0}
    def frontier():
        calls["frontier"] += 1
        return "F"
    with pytest.raises(ValueError):
        run_local_first_takeover(
            local=lambda: "L", frontier=frontier, validate=_bad,
            authorized=lambda: False, reserve_frontier=lambda: None,
        )
    assert calls["frontier"] == 0

def test_budget_denied_fails_closed_without_frontier_call():
    calls = {"frontier": 0}
    def frontier():
        calls["frontier"] += 1
        return "F"
    def reserve():
        raise RuntimeError("denied")
    with pytest.raises(ValueError):
        run_local_first_takeover(
            local=lambda: "L", frontier=frontier, validate=_bad,
            authorized=lambda: True, reserve_frontier=reserve,
        )
    assert calls["frontier"] == 0

def test_frontier_invalid_fails_closed():
    with pytest.raises(ValueError):
        run_local_first_takeover(
            local=lambda: "L", frontier=lambda: "F", validate=_bad,
            authorized=lambda: True, reserve_frontier=lambda: None,
        )

def test_local_raising_typed_error_triggers_escalation():
    def local(): raise ValueError("local blew up")
    out = run_local_first_takeover(
        local=local, frontier=lambda: "F", validate=lambda x: None,
        authorized=lambda: True, reserve_frontier=lambda: None,
    )
    assert out == "F"
