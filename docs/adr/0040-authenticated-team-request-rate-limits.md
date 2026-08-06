# ADR 0040: Bound authenticated team requests before repository composition

## Status

Accepted on 2026-08-06.

## Context

OAuth and row-level security prevent unauthorized reads but do not stop a valid principal from
flooding database-backed MCP tools. The current supported team runtime is one process behind an
operator TLS proxy, so a small process-local control can protect the application boundary without
introducing a distributed coordination dependency.

## Decision

After a verified OAuth subject and canonical explicit workspace are resolved, one fixed-window
bucket is charged for that exact principal/workspace pair. Denial occurs before repository
composition. A monotonic clock, lock, positive bounded configuration, tracked-identity cap, and
expired-state reclamation make behavior deterministic and memory-bounded. The stable denial is
`MNEMO_RATE_LIMITED` and contains no request or tenant data.

The supported guarantee is explicitly single-process. Mnemo does not delegate canonical rate
policy to the reverse proxy, though operators still bound connections and body sizes there.

## Consequences

- One authenticated tenant cannot consume another tenant's application bucket.
- Invalid authentication and malformed scope do not consume rate capacity.
- Restart clears counters, and multiple processes would multiply the effective limit.
- Distributed counters, storage quotas, model budgets, dashboards, and alerts remain separate
  issues.

## Security and privacy

Limiter keys contain typed principal/workspace IDs only in volatile memory. No token, payload,
request body, or identifier is logged or persisted. Capacity exhaustion fails closed for new keys.

## Token and cost

The limiter makes no model call and adds no context token. It provides constant-time accounting and
a bounded in-memory map.

## Dependencies and originality

The implementation is original Mnemo standard-library code and adds no dependency.

## Reversal and recovery

Removing the limiter restores prior behavior without changing durable data. Operational recovery
from an accidental restrictive limit is a reviewed configuration change and service restart.

## Verification

Focused tests cover exact isolation, reset, capacity, invalid values, concurrent calls, ordering
after authentication, and denial before repository composition.
