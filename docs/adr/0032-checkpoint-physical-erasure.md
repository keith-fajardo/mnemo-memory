# ADR 0032: Checkpoint deletion erases payload behind a tombstone

## Status

Accepted on 2026-08-06.

## Context

Checkpoint expiry is an evidence-preserving terminal revision. It removes a handoff from current
selection but deliberately retains its content, evidence, revisions, lifecycle events, source
observation, and delivery jobs. A user deletion request therefore needs a separate operation that
physically removes Mnemo-controlled checkpoint payload without permitting later resurrection.

Deletion must be exact-scope, explicit, retry-safe, and equivalent in personal and team storage.
It must not turn an expired state transition into deletion or silently broaden into retention,
backup cleanup, or external-copy recall.

## Decision

One user-authored action identifies one checkpoint and one bounded source action key in its exact
task scope. The repository first inserts a deterministic payload-free tombstone, then atomically
deletes the aggregate, every immutable revision and embedded evidence payload, lifecycle event,
checkpoint source observation, and checkpoint-lifecycle outbox job. Newly orphaned normalized
evidence and legacy SQLite checkpoint rows are also removed where they exist.

The tombstone retains only deletion identity, checkpoint identity, exact scope, user actor, action
key, and deletion time. An exact retry returns that tombstone idempotently; a competing action,
reused action key, missing or cross-scope target, partial write, or resurrection attempt fails
closed. SQLite triggers and PostgreSQL fixed-search-path guards require the tombstone before direct
payload deletion. PostgreSQL stores tombstones behind forced row-level security and grants only the
minimum controlled delete privileges needed by the repository transaction.

Checkpoint export enumerates live checkpoint history only, so a deleted checkpoint is absent from
new exports. The tombstone itself is not added to the existing history bundle in this issue;
portable deletion propagation remains a separate bounded requirement.

ADR 0033 subsequently adds that tombstone to checkpoint export version 2 and verified
personal-to-team import without changing this canonical erasure decision.

## Consequences

- A deleted checkpoint can no longer be selected, inspected, exported as live history, or
  resurrected in the same canonical store.
- Deletion is physically destructive for the live store and keeps only minimal anti-resurrection
  metadata.
- Expiry remains available when evidence-bearing audit history must be retained.
- User-controlled prior exports and backups cannot be recalled. Backup propagation, scheduled
  retention, and authenticated remote deletion remain separate controls.

## Verification

Reference and SQLite tests cover exact-scope deletion, payload and outbox removal, source-
observation removal, orphaned evidence cleanup, anti-resurrection, conflicting actions,
idempotent retry, direct-delete guards, and migration rollback/retry. Real PostgreSQL tests cover
the same canonical and observation payloads behind forced RLS, private-project viewer denial,
direct-delete rejection, controlled privileges, restart visibility, and atomic migration retry.
