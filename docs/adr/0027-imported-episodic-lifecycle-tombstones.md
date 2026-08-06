# ADR 0027: Imported episodic tombstones use a dedicated forced-RLS canonical table

- **Status:** accepted
- **Date:** 2026-08-06
- **Deciders:** Mnemo maintainers
- **Issue:** 21S
- **Supersedes:** ADR 0026's lifecycle-import deferral
- **Superseded by:** none

## Context

ADR 0026 imports live episodic state but deliberately rejects expiration, purge, and deletion
tombstones. Those records no longer have the event or candidate payload required by native
PostgreSQL lifecycle insertion guards. Recreating placeholder payload would violate deletion;
disabling or bypassing the guards would weaken normal retention and anti-resurrection controls.

Some tombstone target identities also cannot be rebuilt through the live factories because the
source key, extractor version, and proposal index were intentionally erased with their payload.
The import therefore needs a deterministic mapping from the retained source identity and exact
target scope.

## Decision

Migration 0015 adds `mnemo_team.imported_episodic_lifecycle`, one canonical payload-free table for
the six imported kinds: memory/task expiration, purge, and deletion. Every row repeats exact task
scope, forces RLS, stores a target identity, retained source identity, validated source-bundle
SHA-256 digest, strict tombstone projection JSON, and import time. It cannot store a summary, claim,
review, evidence, or deleted source payload. The runtime role receives `SELECT` and `INSERT` only.

Live source identities continue to use their canonical target factories. An orphan source event or
memory identity is mapped with a fixed Mnemo UUIDv5 namespace over canonical target scope and the
retained source identity. Expiration and purge identities are then derived through their normal
domain factories. Deletion actions are rebuilt through their scope-aware factories, including
source-dependent memory deletions. The complete target bundle revalidates all relationships.

The PostgreSQL lifecycle-import repository checks source/target group counts, exact target scope,
existing native tombstones, and both target and source mapping uniqueness. It inserts every
tombstone in one transaction. Exact source digest, source identity, and payload replay is
idempotent; any competing mapping rolls back. Ordinary export reads native and imported tombstones
behind the same repeatable-read RLS transaction and reconstructs the strict bundle. Event and
candidate creation check both stores before writing, so an imported expiration or deletion blocks
the mapped identity from resurrection.

The application import remains resumable: live idempotent writes may precede the one atomic
tombstone transaction, but success is returned only after a final complete export matches every
typed object, count, and target digest.

## Alternatives considered

- **Insert into native tables with synthetic payloads.** Rejected because deleted content must not
  be recreated and placeholder authority would be false.
- **Add a privileged trigger-bypass mode.** Rejected because a runtime-set flag or broad owner
  credential could weaken native lifecycle validation.
- **Store only an import receipt.** Rejected because ordinary export and anti-resurrection need each
  exact tombstone identity and relationship.
- **Create six new tables.** Rejected because imported records share one bounded access pattern and
  are never independently ranked or joined to retained payload.

## Consequences

The existing episodic export can now transfer complete live and lifecycle state from SQLite into a
team task with verified counts and source/target hashes. Imported tombstones remain distinguishable
for provenance while participating in the canonical export and resurrection barrier. Other export
categories remain separate requirements.

## Security and privacy implications

Forced RLS filters before imported rows are selected or inserted. A viewer cannot import into or
enumerate a private project. Source/target uniqueness prevents remapping one retained deletion to a
different target. The projection contains only strict payload-free tombstone fields; real database
tests assert that no `summary` or `claim` key is retained. Storage errors expose no row, identifier,
SQL, or adapter detail.

## Token and cost implications

No model, embedding, network, or provider call is added. Retaining compact tombstones prevents
costly and unsafe re-extraction of deleted state.

## Dependency and licensing implications

No dependency is added. The schema, mapping, repository code, and fixtures are original Mnemo work.

## Reversal or migration strategy

Migration 0015 is atomic and forward-only. A failure from valid v14 leaves no imported-lifecycle
table and a clean retry reaches v15. Before team release, recovery is restore of the verified
pre-upgrade backup or correction followed by idempotent migration. Removing imported rows is a
future deletion-propagation operation and must not be used as rollback because it would remove
anti-resurrection state.

## Verification

- Unit tests import a complete live/expired/purged/deleted bundle, verify deterministic rebasing,
  exact counts/hash, and idempotent replay.
- Real PostgreSQL tests prove atomic v14-to-v15 rollback/retry, complete SQLite transfer, restart
  export parity, exact replay, private-viewer denial, payload minimization, immutable runtime
  privileges, and imported event anti-resurrection.

## References

- `docs/implementation-plan.md`, Milestone 9
- `docs/adr/0021-postgresql-team-episodic-deletion.md`
- `docs/adr/0026-verified-live-episodic-import.md`
- `docs/product-memory-contract.md`
