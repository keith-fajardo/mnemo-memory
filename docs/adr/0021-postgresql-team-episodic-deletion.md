# ADR 0021: Team explicit deletion erases payload and retains tombstones

- **Status:** accepted
- **Date:** 2026-08-06
- **Deciders:** Mnemo maintainers
- **Issue:** 21L
- **Supersedes:** none
- **Superseded by:** none

## Context

PostgreSQL team storage can expire and purge episodic memories and their minimized source events,
but a verified user has no explicit deletion path. The existing storage-neutral deletion contract
requires immediate physical erasure, a minimal deterministic anti-resurrection record, and atomic
source/dependent ordering. Deletion must also remain distinct from scheduled retention and preserve
any retention tombstones that already exist.

## Decision

Migration 0010 adds immutable forced-RLS `episodic_memory_deletions` and
`task_activity_event_deletions` tables. Each record repeats complete task scope and stores only
identity, verified-user actor, action key, cause/dependency identity, and exact deletion time. It
stores no claim, event summary, reason, evidence, review, or governance payload.

Deleting one memory inserts its tombstone before trigger-gated removal of its governance actions,
active marker, review, and candidate. Deleting a source first inserts the source tombstone, then
creates missing dependent memory tombstones and removes every dependent payload before atomically
removing the source event and its task-activity outbox job. An existing individual memory tombstone
is retained and returned as part of a later source deletion. Retention expiration and purge
tombstones are never removed.

Fixed-search-path triggers require an exact live target or matching retention tombstone and bind
source-caused memory deletion to the exact source deletion. Payload-table delete guards accept only
a matching purge or deletion lifecycle record. Candidate and event insertion reject retained
deletion tombstones, preventing resurrection. Exact action replay is idempotent; changed target,
action key, scope, or deterministic identity fails closed and rolls back the transaction.

## Alternatives considered

- **Reuse retention purge as user deletion.** Rejected because schedule-driven expiry and explicit
  user intent have different authority, identity, and audit semantics.
- **Cascade-delete from the source without dependent tombstones.** Rejected because it loses
  per-memory anti-resurrection state and makes retry results incomplete.
- **Retain deleted payload for audit.** Rejected because the deletion contract requires physical
  erasure; the bounded tombstone is sufficient lifecycle evidence.
- **Remove prior retention tombstones.** Rejected because deletion must not weaken existing
  anti-resurrection or retention provenance.

## Consequences

PostgreSQL now implements the existing explicit episodic deletion contract for individual
extracted memories and minimized source events. It does not implement export, backup propagation,
external-consumer cleanup, scheduling, or deletion parity for checkpoints, knowledge, dbt, or
source-structure data.

## Security and privacy implications

Authorization and exact task scope precede target discovery and mutation. Both tombstone tables
force RLS and grant the runtime only select/insert access. Database triggers constrain the narrow
runtime DELETE grants to an already inserted exact-scope lifecycle record. Source deletion and all
dependent cleanup commit in one transaction, so neither orphan payload nor a partial tombstone set
is externally visible. Errors disclose no payload or cross-scope existence.

## Token and cost implications

No model, embedding, or context-budget change is introduced. Deletion invokes no provider and
prevents erased content from consuming later retrieval context.

## Dependency and licensing implications

No dependency is added. The implementation uses the existing PostgreSQL driver and Mnemo-owned
domain, application, policy, and storage contracts.

## Reversal or migration strategy

Migration 0010 is atomic and forward-only. Failure from valid v9 leaves ledger
`(1, 2, 3, 4, 5, 6, 7, 8, 9)` and creates neither deletion table. Recovery before team release is
restore of the verified pre-upgrade backup or correction followed by idempotent migration. Future
replacement must preserve deterministic tombstone identities, exact scope, dependency links, and
anti-resurrection behavior.

## Verification

- An injected v9-to-v10 failure retains v9 and a clean retry reaches v10.
- Real PostgreSQL tests cover individual deletion, exact replay, conflicting action-key reuse,
  source/dependent ordering, restart durability, and cross-task/private-project denial.
- Source deletion removes every candidate-owned payload plus its event and task-activity outbox
  job, retains prior individual tombstones, and prevents candidate/event resurrection.
- Deletion after completed retention purge succeeds from retained target tombstones without
  removing them.
- Runtime privilege inspection denies update/delete on both deletion tombstone tables.

## References

- `docs/implementation-plan.md`, Milestones 4 and 9
- `docs/adr/0019-postgresql-team-episodic-retention.md`
- `docs/adr/0020-postgresql-team-task-activity-retention.md`
