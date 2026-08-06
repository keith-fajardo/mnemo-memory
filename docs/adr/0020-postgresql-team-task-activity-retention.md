# ADR 0020: Team task-event purge waits for dependent memory purge

- **Status:** accepted
- **Date:** 2026-08-06
- **Deciders:** Mnemo maintainers
- **Issue:** 21K
- **Supersedes:** none
- **Superseded by:** none

## Context

Team extracted-memory retention can now purge candidate-owned payloads, but the minimized source
event and its outbox job remain indefinitely. The existing task-activity retention contract
requires immediate event-payload exclusion when due and later physical purge only after every
dependent episodic candidate payload is gone. Source purge must retain both source and candidate
anti-resurrection state.

## Decision

Migration 0009 adds immutable forced-RLS `task_activity_event_expirations` and
`task_activity_event_purges` tables with complete task scope. Expiration is bound by a fixed-search-
path trigger to the event's exact non-permanent policy and canonical ISO schedule text. Event reads
exclude expired identities immediately, and append rejects retained expiration tombstones.

Purge names one exact expiration and cannot precede it. Its trigger rejects insertion while any
candidate payload still references the event. Once admitted, trigger-gated deletes atomically
remove the task-activity outbox job and minimized event. The source expiration and purge remain,
as do any candidate expiration/purge tombstones. Migration 0009 removes the earlier candidate-
expiration foreign key to the live source row because that tombstone must outlive source purge;
expiration insertion still requires the live exact source through its database trigger.

Canonical timestamp text is preserved for deterministic identity and cast only for chronological
comparison. Complete batches validate before mutation. Exact replay is idempotent; changed,
non-due, missing, dependent, cross-scope, or concurrent operations roll back atomically.

## Alternatives considered

- **Cascade-delete candidate payloads with the source.** Rejected because candidate expiration or
  explicit deletion must establish its own durable identity and cleanup ordering.
- **Leave the outbox job after source purge.** Rejected because it could repeatedly request work
  for a source that no longer exists.
- **Keep a foreign key from candidate tombstones to live events.** Rejected because it prevents the
  required source cleanup and makes anti-resurrection metadata depend on retained payload.
- **Add a scheduler now.** Rejected because storage parity and scheduling have separate operational
  and failure boundaries.

## Consequences

PostgreSQL now implements both layers of the existing episodic retention lifecycle: dependent
memory first, minimized source second. It does not schedule sweeps, implement user deletion/export,
or propagate cleanup to backups and external consumers.

## Security and privacy implications

Complete task scope and authorization precede due scans, reads, and mutations. Both tombstone
tables force RLS and are read/insert-only. Runtime DELETE on the event and outbox is constrained by
fixed-search-path triggers requiring a matching exact-scope purge; non-task outbox deletion is
denied. Dependency checks occur in both adapter and database trigger.

## Token and cost implications

No model, embedding, or context-budget change is introduced. Expiration prevents stale source
selection and purge removes retained minimized content and delivery metadata.

## Dependency and licensing implications

No dependency is added. The implementation uses the existing PostgreSQL driver and Mnemo-owned
domain, application, and storage contracts.

## Reversal or migration strategy

Migration 0009 is atomic and forward-only. Failure from valid v8 leaves ledger
`(1, 2, 3, 4, 5, 6, 7, 8)`, retains the candidate tombstone foreign key, and creates neither source
retention table. Recovery before team release is restore of the verified pre-upgrade backup or
correction followed by idempotent migration.

## Verification

- An injected v8-to-v9 failure retains v8 and a clean retry reaches v9.
- Real PostgreSQL tests cover not-due selection, conflicting-batch rollback, exact expiration
  replay, immediate event exclusion, restart durability, and cross-task/private-project denial.
- Direct deletion and source purge with a live candidate fail; candidate purge then permits atomic
  event/outbox purge while retaining all tombstones.
- Exact purge replay is idempotent, source append cannot resurrect, and tombstones have no
  update/delete privilege.

## References

- `docs/implementation-plan.md`, Milestones 4 and 9
- `docs/adr/0015-postgresql-team-task-events-outbox.md`
- `docs/adr/0019-postgresql-team-episodic-retention.md`
