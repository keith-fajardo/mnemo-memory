"""Authenticated team request limiting is isolated, bounded, and deterministic."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from mnemo_memory.packages.application import TeamRequestRateLimit, TeamRequestRateLimiter
from mnemo_memory.packages.domain import OwnerId, WorkspaceId


def test_rate_limit_isolates_scope_and_resets_after_window() -> None:
    now = [10.0]
    limit = TeamRequestRateLimiter(TeamRequestRateLimit(2, 5, 10), timer=lambda: now[0])
    principal, other = OwnerId.new(), OwnerId.new()
    workspace, another = WorkspaceId.new(), WorkspaceId.new()

    limit.require(principal, workspace)
    limit.require(principal, workspace)
    with pytest.raises(ValueError, match="MNEMO_RATE_LIMITED"):
        limit.require(principal, workspace)
    limit.require(other, workspace)
    limit.require(principal, another)

    now[0] = 15.0
    limit.require(principal, workspace)


def test_rate_limit_caps_tracked_identities_and_reclaims_expired_state() -> None:
    now = [1.0]
    limit = TeamRequestRateLimiter(TeamRequestRateLimit(1, 10, 1), timer=lambda: now[0])
    workspace = WorkspaceId.new()
    limit.require(OwnerId.new(), workspace)

    with pytest.raises(ValueError, match="MNEMO_RATE_LIMITED"):
        limit.require(OwnerId.new(), workspace)

    now[0] = 11.0
    limit.require(OwnerId.new(), workspace)


def test_rate_limit_is_atomic_under_concurrent_calls() -> None:
    limit = TeamRequestRateLimiter(TeamRequestRateLimit(10, 60, 100), timer=lambda: 1.0)
    principal, workspace = OwnerId.new(), WorkspaceId.new()

    def invoke(_: int) -> bool:
        try:
            limit.require(principal, workspace)
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=20) as executor:
        allowed = tuple(executor.map(invoke, range(40)))

    assert sum(allowed) == 10


def test_rate_limit_configuration_rejects_invalid_values() -> None:
    with pytest.raises((TypeError, ValueError)):
        TeamRequestRateLimit(0, 60, 100)
    with pytest.raises((TypeError, ValueError)):
        TeamRequestRateLimit(1, 0, 100)
    with pytest.raises((TypeError, ValueError)):
        TeamRequestRateLimit(1, 60, 0)
