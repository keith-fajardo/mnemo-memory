# ADR 0019: Team episodic expiration precedes payload purge

- **Status:** accepted
- **Date:** 2026-08-06
- **Deciders:** Mnemo maintainers
- **Issue:** 21J
- **Supersedes:** none
- **Superseded by:** none

## Context

PostgreSQL now stores and governs team episodic candidates, but their canonical source retention
schedule is not enforced. The existing storage-neutral retention contract requires deterministic
expiration to hide an identity immediately and a separate physical purge to erase dependent
payloads while preserving anti-resurrection state. Team storage needs parity without adding a
scheduler, conflating retention with user deletion, or deleting the minimized source event.

## Decision

Migration 0008 adds immutable exact-task `episodic_memory_expirations` and
`episodic_memory_purges` tables under forced row-level security. Expiration binds the deterministic
identity, memory, source event, canonical retention policy and scheduled time, actual sweep time,
and complete scope. A fixed-search-path trigger requires an exact candidate whose source-bound
retention is non-permanent and matches the record. Candidate, review, active-memory, governance,
and revision reads exclude any expired identity before returning payload.

Purge is a second immutable record bound to one exact expiration and cannot precede it. Once that
record exists, fixed-search-path delete triggers permit removal of the matching governance,
active, review, and candidate rows only. Expiration and purge tombstones remain read/insert-only,
and the source task event remains intact. Candidate insertion checks expiration tombstones so a
repeated extraction cannot resurrect purged content.

Canonical retention timestamps are stored as their exact ISO text because the domain's
deterministic expiration identity includes that representation. PostgreSQL casts the text to
`timestamptz` only for chronological constraints, preventing the server time zone from rewriting
identity provenance.

The adapter validates complete batches before writing. Exact expiration and purge replay is
idempotent; changed records, non-due schedules, missing targets, and concurrent conflicts roll back
the whole batch.

## Alternatives considered

- **Delete as soon as the schedule is due.** Rejected because immediate exclusion and later
  physical cleanup have different retry and recovery semantics.
- **Update the candidate to an expired status.** Rejected because the payload would remain and a
  mutable row would not provide a durable anti-resurrection record after purge.
- **Delete the source event with each candidate.** Rejected because multiple candidates may depend
  on one permitted event and source retention has its own lifecycle.
- **Store timestamps only as `timestamptz`.** Rejected because PostgreSQL normalizes offsets and
  would change the exact text used by the existing deterministic identity contract.

## Consequences

Team candidate and approved-memory retention now matches the existing personal lifecycle.
Expiration is durable and immediately effective after restart; purge erases all candidate-owned
claim and governance JSON while retaining minimal scoped tombstones. Task-event retention,
explicit deletion/export, backup propagation, and scheduling remain separate issues.

## Security and privacy implications

Complete task scope and authorization precede every due scan, tombstone read, and mutation. Forced
RLS protects both tombstone tables. The runtime can delete candidate-owned payload tables only
after a matching exact-scope purge exists; database triggers deny direct deletion beforehand.
Tombstones have no update/delete grant. Errors expose no payload or database detail.

## Token and cost implications

No provider, embedding, or retrieval-budget change is introduced. Expiration prevents stale
payload selection, and purge reduces retained content. The operation invokes no model.

## Dependency and licensing implications

No dependency is added. The implementation uses the existing PostgreSQL driver and Mnemo-owned
domain, application, and storage contracts.

## Reversal or migration strategy

Migration 0008 is atomic and forward-only. Failure from valid v7 leaves ledger
`(1, 2, 3, 4, 5, 6, 7)` and neither retention table. Recovery before team release is restore of the
verified pre-upgrade backup or correction followed by idempotent migration. Future replacement
must preserve tombstone identities, exact timestamp text, source/policy/scope bindings, and purge
state.

## Verification

- An injected v7-to-v8 failure retains v7 and a clean retry reaches v8.
- Real PostgreSQL tests cover not-due selection, conflicting-batch rollback, exact expiration
  replay, immediate exclusion of every dependent read, restart durability, and source survival.
- Purge removes candidate, review, active, and governance payloads; exact retry is idempotent and
  both tombstone types remain.
- Direct pre-purge deletion, cross-task reads, and private-project viewer reads fail closed.
- Runtime privilege inspection denies update/delete on expiration and purge tombstones.

## References

- `docs/implementation-plan.md`, Milestones 4 and 9
- `docs/adr/0017-postgresql-team-episodic-candidates-review.md`
- `docs/adr/0018-postgresql-team-active-episodic-governance.md`
