# ADR 0029: Approved-event transfer preserves erasure and rebases scoped identities

- **Status:** accepted
- **Date:** 2026-08-06
- **Deciders:** Mnemo maintainers
- **Issue:** 21U
- **Supersedes:** none
- **Superseded by:** none

## Context

Personal SQLite retains explicit approved facts, immutable correction or retraction actions, and
ordered pin actions. Scope participates in each deterministic identity, so copying source IDs into
a team project is invalid. Retraction physically removes the event summary and direct evidence;
an importer therefore cannot replay an erased target through the ordinary retract operation
without inventing or restoring prohibited payload.

## Decision

`mnemo.approved-event-export.v1` contains every retained event payload, every governance action,
and the complete pin-action stream for one exact task scope. Stable event and governance order,
contiguous portable pin sequence, deterministic identity validation, relationship validation,
erasure validation, and one canonical SHA-256 digest make incomplete or modified history fail
closed.

Import rebuilds retained event, governance, and pin identities from their source keys and the
explicit target scope. An erased retraction target receives a deterministic payload-free identity
derived from target scope and retained source identity. PostgreSQL stores retained events and all
actions in the native forced-RLS tables. Import provenance columns retain source identity, source
bundle digest, and import time. A narrowly bounded trigger branch accepts a retraction or pin whose
target payload was already erased; it cannot accept a live payload under that branch. No summary,
source key, or direct event evidence is reconstructed for the erased target.

The target scope must be empty or exactly identical. PostgreSQL validates the complete source to
target projection, writes retained events, retractions, corrections, pin history, and normal
outbox jobs in one transaction, reconstructs the target bundle before commit, and makes exact
retry idempotent. The application independently exports before and after import and verifies typed
state, counts, and source and target digests.

## Alternatives considered

- **Replay ordinary mutations.** Rejected because an already-erased target payload is unavailable
  and must not be fabricated.
- **Restore a placeholder event before retracting it.** Rejected because it would manufacture
  canonical content and evidence for deleted data.
- **Store an opaque export blob.** Rejected because ordinary governance inspection,
  anti-resurrection, RLS, and deletion propagation need canonical rows.
- **Discard pin history.** Rejected because pin actions are immutable user governance and their
  order determines current priority.

## Consequences

Approved facts can move from a personal task to a team task with verified live, governance, and
pin counts and hashes. Retained payload and evidence remain queryable through ordinary team
repositories; erased payload remains absent. Source and target digests differ because scope and
scope-derived identities change.

## Security and privacy implications

Export selects one exact task scope before reconstruction. Import uses the bound principal,
workspace, operation, and forced RLS before any query or insert. Source/target mapping and bundle
digest provenance are insert-only. A retracted event identity remains an anti-resurrection target,
but its summary, source key, and direct event evidence are neither exported nor recreated. Private
viewers receive no rows and cannot import.

## Token and cost implications

No model, embedding, network, or provider call is added. Durable migration avoids transcript replay
and preserves compact approved facts for later bounded retrieval.

## Dependency and licensing implications

No dependency is added. The format, migration, validation, services, adapter code, and fixtures are
original Mnemo work.

## Reversal or migration strategy

PostgreSQL schema version 16 is forward-only. Migration failure rolls back to version 15. Recovery
after a committed migration uses a database backup or a reviewed forward migration; imported rows
remain ordinary canonical state and must use the future deletion-propagation workflow.

## Verification

- Domain and Reference tests cover canonical JSON, tampering, ordered pins, erasure, rebasing,
  conflicting targets, and replay.
- SQLite tests prove full governance export and restart stability.
- Real PostgreSQL tests prove v15-to-v16 rollback/retry, SQLite transfer, counts and hashes after
  restart, provenance, outbox creation, anti-resurrection, replay, and private-viewer denial.

## References

- `docs/implementation-plan.md`, Milestone 9
- `docs/adr/0028-checkpoint-history-transfer.md`
- `docs/product-memory-contract.md`
- `docs/threat-model.md`
