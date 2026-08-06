# ADR 0034: Schedule checkpoint expiry from the last canonical write

## Status

Accepted on 2026-08-06.

## Context

Mnemo already supports explicit, evidence-preserving checkpoint expiry in personal SQLite and team
PostgreSQL profiles. Without due discovery and composition, however, the configured personal
retention period does not make old active handoffs leave current retrieval automatically.

Retention must not broaden scope, turn access into an implicit renewal, race with an agent saving a
new handoff, or make a client hook unavailable when local storage fails.

## Decision

The checkpoint repository exposes a bounded query for active aggregates in one exact task scope
whose `updated_at` is at or before a caller-supplied cutoff. Adapters authorize and filter scope
before ordering the result oldest-first, use checkpoint identity as the deterministic tie-breaker,
and cap each pass at 100.

The application retention service derives the cutoff from a timezone-aware `as_of` value and the
configured 1–3650 day period. Before expiry it rereads the aggregate and current revision, verifies
that the selected revision and update time are unchanged and still due, then uses the existing
expected-revision expiry transition. A concurrent revision or terminal transition is skipped for a
later pass. The terminal revision preserves the current content and evidence and remains the
durable audit record; no payload is purged.

For an automatic-memory-enabled personal project, `SessionStart` runs one pass using
`episodic_retention_days` before selecting the current checkpoint. The callback is fail-open: an
invalid setting, unavailable repository, or retention failure cannot block the coding client.
Checkpoint reads and context retrieval never update `updated_at` and therefore never extend the
schedule. Team execution can use the same application service only after its authenticated remote
composition owns scheduling and principal identity.

## Consequences

- Old active personal handoffs leave current retrieval automatically without transcript capture.
- A new canonical write renews the retention clock; merely reading a handoff does not.
- Each pass is bounded and exact-task; additional due checkpoints are handled by later starts.
- Logical expiry remains distinct from explicit physical erasure, backup cleanup, and portable
  deletion tombstones.
- PostgreSQL has scope-first due-discovery parity, but no unauthenticated team scheduler or listener
  is introduced.

## Verification

Reference and SQLite tests cover cutoff boundaries, exact-scope isolation, bounded selection,
evidence preservation, restart idempotence, and a concurrent revision after discovery. Hook tests
cover session-start invocation and fail-open behavior. Real PostgreSQL tests cover scope-first due
discovery under forced RLS. The threat model treats stale selection and cross-task enumeration as
security failures.
