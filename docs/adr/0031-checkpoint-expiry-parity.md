# ADR 0031: Checkpoint expiry is an immutable terminal revision

## Status

Accepted on 2026-08-06.

## Context

Mnemo's canonical checkpoint status already included `expired`, but active repositories and the
portable history contract implemented only completion and abandonment. A handoff therefore could
not be retired through the production lifecycle consistently across personal and team storage.

Expiry must remove stale handoffs from current retrieval without destroying their evidence-bearing
audit history. It must also remain scope-first, compare-and-swap safe, portable, and idempotent.

## Decision

Expiry appends one immutable revision with status `expired`, the current bounded content, and the
current evidence references. The aggregate pointer and deterministic `checkpoint_expired` event
advance in the same transaction. Only an active aggregate at the expected revision may transition;
an identical retry naming that predecessor returns the committed expired revision.

Reference, SQLite, and PostgreSQL repositories implement the same operation. SQLite migration 0029
and PostgreSQL migration 0018 admit the new event and terminal status. PostgreSQL retains forced
RLS, fixed-search-path event validation, immutable revision/event privileges, and atomic migration
rollback. The strict checkpoint export bundle validates and transfers expired histories exactly.

Expiry is logical retirement, not erasure. It retains content and provenance and therefore does
not satisfy a user deletion request or a physical retention purge. Those require a payload-free
tombstone and anti-resurrection path in a later bounded issue.

## Consequences

- Expired handoffs no longer appear in current checkpoint selection.
- Audit and personal-to-team transfer retain the exact terminal revision and event.
- Stale or competing terminal writes fail without partial state.
- Scheduled retention, physical erasure, backup propagation, and remote authentication remain
  explicit separate controls.

## Verification

Backend-neutral contract tests cover expiry, immutable history, idempotent retry, terminal write
rejection, and current-selection removal. Application tests prove content/evidence preservation.
Transfer tests prove strict canonical round trips. Real PostgreSQL tests cover the runtime path and
an injected migration-18 rollback before retry.
