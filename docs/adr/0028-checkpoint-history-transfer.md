# ADR 0028: Checkpoint transfer preserves canonical identities and rebases only scope

- **Status:** accepted
- **Date:** 2026-08-06
- **Deciders:** Mnemo maintainers
- **Issue:** 21T
- **Supersedes:** none
- **Superseded by:** none

## Context

Team PostgreSQL already implements the canonical checkpoint aggregate, immutable revision, and
lifecycle-event contracts, but a personal SQLite task cannot transfer that history. Replaying
through ordinary revision methods would allocate new revision identities and would not atomically
preserve terminal history. Copying rows without a strict portable contract would make incomplete,
tampered, cross-scope, or conflicting state difficult to detect.

Checkpoint source observations refer to rebuildable source-structure snapshots. Those projections
can be regenerated in the team project and are not durable checkpoint payload.

## Decision

`mnemo.checkpoint-export.v1` contains every checkpoint aggregate, immutable revision, and matching
lifecycle event in one exact task scope. It uses stable checkpoint/revision ordering, validates a
contiguous predecessor chain and exactly one deterministic lifecycle event per revision, and binds
the aggregate's current pointer, lifecycle status, creation time, and update time to that chain.
Canonical UTF-8 JSON and one SHA-256 digest cover the complete bundle.

Import preserves checkpoint, revision, lifecycle-event, content, evidence, status, and timestamp
identity. Only the enclosing owner/workspace/project/session/task scope is replaced with the exact
authorized team target. PostgreSQL validates that the target bundle is an exact scope-only rebase,
then requires the target scope to be empty or already identical. It inserts all aggregates,
revisions, lifecycle events, and their deterministic outbox jobs in one forced-RLS transaction and
reconstructs the bundle before commit. Exact replay is idempotent; any different target state or
identity collision rolls back.

The application service independently exports before and after import and reports success only
when typed target state, category counts, and the target canonical digest match the expected
projection. PostgreSQL timestamp offset normalization is compared as typed instants; each backend's
own canonical digest remains strict.

## Alternatives considered

- **Replay ordinary checkpoint mutations.** Rejected because later revision identities would
  change and a terminal chain could not be recreated as one atomic operation.
- **Store an opaque personal bundle.** Rejected because canonical team retrieval, RLS, lifecycle
  queries, and future deletion need normal checkpoint rows rather than a second payload store.
- **Import source-structure observations.** Rejected because their referenced snapshots are
  rebuildable project projections and may not exist in the target.
- **Allow resumable partial checkpoint chains.** Rejected because PostgreSQL can import this
  bounded category atomically and an empty-or-identical rule is simpler and safer.

## Consequences

Personal checkpoint history can move to one team task without losing checkpoint, revision, event,
content, evidence, status, or timestamp identity. Source and target canonical hashes differ because
the explicit scope changes, and both are returned for verification. Imported lifecycle events
create normal team outbox jobs; handlers remain idempotent.

## Security and privacy implications

SQLite export selects only one complete task scope before reconstructing payload. PostgreSQL export
and import set an exact principal, workspace, and closed operation before forced-RLS queries or
writes. A private-project viewer cannot export payload or import history. Bundle validation rejects
cross-scope objects, missing or duplicate identities, non-contiguous chains, mismatched lifecycle
facts, non-canonical order, and digest tampering. Storage failures expose no content, identifier,
SQL, or adapter detail.

## Token and cost implications

No model, embedding, network, or provider call is added. Preserving compact checkpoint history
avoids transcript replay after migration.

## Dependency and licensing implications

No dependency is added. The format, validation, services, adapter code, and fixtures are original
Mnemo work.

## Reversal or migration strategy

No schema migration is added. The v1 export format remains readable once released. Before team
release, a failed import rolls back completely; an already committed import is ordinary canonical
checkpoint state and must later use the checkpoint deletion-propagation workflow rather than an
unscoped rollback delete.

## Verification

- Domain and reference tests cover canonical JSON, tamper/duplicate rejection, identity
  preservation, scope rebasing, conflict rejection, and exact replay.
- SQLite tests cover full history, scope isolation, and restart-stable export.
- Real PostgreSQL tests transfer SQLite history, verify counts and source/target hashes after
  restart, preserve every identity, retry idempotently, and deny a private-project viewer.

## References

- `docs/implementation-plan.md`, Milestone 9
- `docs/adr/0014-postgresql-team-checkpoints.md`
- `docs/adr/0027-imported-episodic-lifecycle-tombstones.md`
- `docs/product-memory-contract.md`
