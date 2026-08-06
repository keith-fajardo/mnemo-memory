# ADR 0044: Reuse bounded PostgreSQL connections across team context requests

## Status

Accepted on 2026-08-06.

## Context

The first real load run of the authenticated team `get_context` composition opened a new physical
PostgreSQL connection for every repository transaction. At eight concurrent clients it delivered
3.711 requests per second with 2,458.889 ms p95 latency. Reusing one connection only within each
tool call improved this to 15.455 requests per second and 590.646 ms p95, but still missed the
declared 30 requests-per-second and 250 ms p95 objectives. Connection establishment, rather than
authorization or ranking, was the demonstrated bottleneck.

## Decision

The installed team process owns one lazy, bounded PostgreSQL connection pool behind the existing
storage connection-factory port. The default size is 16 and the configured range is 1–64. A caller
waits at most five seconds for capacity; exhaustion fails content-free as
`MNEMO_TEAM_POSTGRES_UNAVAILABLE`. No request queue or retry is added.

Within one authenticated tool call, all storage repositories sequentially borrow the same checked-
out physical connection. Repository commits and rollbacks retain their existing transaction
boundaries. Repository `close` operations release only the request-local lease; the tool boundary
rolls back and returns the physical connection to the process pool. Pool return performs another
defensive rollback. A connection that cannot roll back is closed and discarded. Process shutdown
closes remaining sockets through normal process teardown; the pool also provides deterministic
close behavior for tests and embedding hosts.

## Consequences

- The production composition establishes at most the configured number of database connections
  instead of one connection per repository transaction.
- Each request still has independent transactions and deterministic authorization-first behavior.
- Pool size must fit the deployment's PostgreSQL connection budget after reserving administrator,
  migration, backup, and monitoring capacity.
- The pool is process-local. The supported profile still runs exactly one Mnemo service process;
  a future multi-process deployment must allocate the database connection budget across processes.
- No cache, new dependency, background worker, request retry, or cross-request data object is added.

## Security and privacy

Every transaction continues to set principal, workspace, and operation with transaction-local
PostgreSQL settings before any query. Commit or rollback clears those settings before another
request can use the connection. Pool return never inspects or logs a row, request, token, scope, or
exception. The real PostgreSQL suite reuses one physical connection across an owner, viewer, and
foreign-workspace request and proves that forced RLS returns only each request's authorized result.

## Token and cost

Connection reuse makes no model call and changes no context-packet budget. It reduces database
handshake work and caps physical connection consumption.

## Dependencies and originality

The bounded pool and request lease are original Mnemo standard-library code. No dependency is
added.

## Reversal and recovery

Set `MNEMO_TEAM_DB_POOL_SIZE=1` for a single-connection diagnostic profile. Reverting the pool
requires restoring a connection factory with equivalent bounded capacity and rerunning the load
and cross-tenant RLS gates; returning to unbounded per-transaction connections is not an accepted
production profile.

## Verification

Unit tests cover reuse, capacity timeout, broken-connection discard, and deterministic close.
Configuration tests cover the default and upper bound. The real RLS test covers cross-principal and
cross-workspace reuse of one physical connection. The opt-in load gate runs 160 authenticated
context requests at concurrency eight and fails unless errors are zero, nearest-rank p95 is at most
250 ms, and throughput is at least 30 requests per second.
