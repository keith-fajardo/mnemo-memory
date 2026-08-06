"""Bounded team PostgreSQL connection reuse stays deterministic and content-free."""

from __future__ import annotations

import pytest

from mnemo_memory.apps.mcp.team_runtime import (
    TeamServiceConfigurationError,
    _PostgreSQLConnectionPool,
)
from mnemo_memory.packages.storage import PostgreSQLCursor


class _Connection:
    def __init__(self, *, fail_rollback: bool = False) -> None:
        self.autocommit = False
        self.fail_rollback = fail_rollback
        self.closed = False

    def cursor(self) -> PostgreSQLCursor:
        raise AssertionError("cursor is not used by this pool contract test")

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        if self.fail_rollback:
            raise RuntimeError("private driver failure")

    def close(self) -> None:
        self.closed = True


def test_pool_reuses_one_connection_and_closes_idle_capacity() -> None:
    created: list[_Connection] = []

    def connect() -> _Connection:
        connection = _Connection()
        created.append(connection)
        return connection

    pool = _PostgreSQLConnectionPool(connect, maximum_connections=1)
    pool().close()
    pool().close()

    assert len(created) == 1
    assert not created[0].closed
    pool.close()
    assert created[0].closed


def test_pool_fails_content_free_when_capacity_does_not_return() -> None:
    pool = _PostgreSQLConnectionPool(
        _Connection, maximum_connections=1, acquire_timeout_seconds=0.001
    )
    borrowed = pool()

    with pytest.raises(TeamServiceConfigurationError, match=r"^MNEMO_TEAM_POSTGRES_UNAVAILABLE$"):
        pool()

    borrowed.close()
    pool.close()


def test_pool_discards_a_connection_that_cannot_rollback() -> None:
    created: list[_Connection] = []

    def connect() -> _Connection:
        connection = _Connection(fail_rollback=not created)
        created.append(connection)
        return connection

    pool = _PostgreSQLConnectionPool(connect, maximum_connections=1)
    pool().close()
    assert created[0].closed

    pool().close()
    assert len(created) == 2
    pool.close()
