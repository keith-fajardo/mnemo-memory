# ADR 0022: Team source structure is a rebuildable forced-RLS projection

- **Status:** accepted
- **Date:** 2026-08-06
- **Deciders:** Mnemo maintainers
- **Issue:** 21N
- **Supersedes:** none
- **Superseded by:** none

## Context

Team storage has durable memory parity but no repository-structure projection. Checkpoint source
observations and team structural context require an authorized immutable snapshot identity, while
the product boundary forbids treating repository structure as durable user memory or retaining
source bodies merely to answer structural questions.

## Decision

Migration 0011 adds project-scoped source snapshots, file fingerprints, symbols, edges, activation
events, and last-sync status in PostgreSQL. Every table repeats workspace, project, owner, and
visibility, forces row-level security, and contains only rebuildable structural metadata. Files
retain safe relative paths and SHA-256 digests; symbols retain identity, kind, qualified name, path,
and line; edges retain static source/target identities. No source text, comments, docstrings,
absolute paths, environment values, embeddings, or model output are stored.

One store transaction takes a project-specific advisory transaction lock, writes the complete
immutable projection, validates its relational graph through composite foreign keys, appends an
activation only when the active identity changes, updates the bounded sync status, and commits
atomically. Exact scoped digest replay reuses and may reactivate the prior snapshot. Activation
events, rather than UUID ordering, define transition history. A fixed-search-path trigger prevents
runtime mutation of immutable snapshot fields or active-state changes that do not match the latest
activation event.

Authorized reads select exact snapshots and bounded graph frontiers. Symbol lookup performs a
bounded authorized database selection over retained identities and then reuses Mnemo's deterministic
literal rank. Authorization therefore precedes ranking, and no model or embedding participates.

## Alternatives considered

- **Store source bodies for richer search.** Rejected because this projection needs identities and
  relationships only and should minimize sensitive repository content.
- **Derive history from snapshot UUIDs.** Rejected because UUIDs are identities, not clocks.
- **Replace prior snapshots on activation.** Rejected because immutable transitions support stale
  detection, checkpoint co-observation, and deterministic rebuild comparison.
- **Add a graph database.** Rejected because bounded PostgreSQL adjacency rows satisfy the current
  measured contract without another operational dependency.

## Consequences

Team PostgreSQL can now preserve and query the existing multi-language source-structure projection
contract. It does not scan files, schedule refresh, add dbt projections, or link snapshots to
checkpoint revisions; those remain separate composition and parity issues.

## Security and privacy implications

Forced RLS and complete project scope apply before every read, write, or lexical rank. Runtime
access is select/insert plus column-limited updates for active state and sync time. Foreign keys
bind all child projections to the exact scoped snapshot and resolved edges to same-snapshot symbols.
Private-project viewers receive no rows, and failures expose no path, symbol, count, or database
detail.

## Token and cost implications

The projection invokes no model and stores no embeddings. Bounded symbol selection can reduce the
amount of repository context an agent must rediscover or place in prompts.

## Dependency and licensing implications

No dependency is added. The implementation uses PostgreSQL, the existing driver, and Mnemo-owned
domain, ranking, authorization, and storage contracts.

## Reversal or migration strategy

Migration 0011 is atomic and forward-only. Failure from valid v10 leaves ledger
`(1, 2, 3, 4, 5, 6, 7, 8, 9, 10)` and creates no source-structure table. Because all added data is a
rebuildable projection, recovery before team release is restore of the verified pre-upgrade backup
or correction followed by idempotent migration and projection rebuild.

## Verification

- An injected v10-to-v11 failure retains v10 and a clean retry reaches v11.
- Real PostgreSQL tests cover exact digest replay, two activations and reactivation, transitions,
  history, sync state, files, symbols, edges, bounded search, and graph frontiers.
- Tests cover conflicting identity rollback, restart durability, foreign-project and private-viewer
  denial, immutable-column privileges, and trigger denial of an unrecorded active-state change.

## References

- `docs/implementation-plan.md`, Milestones 3 and 9
- `docs/adr/0005-python-source-structure-memory.md`
- `docs/adr/0012-postgresql-team-control-plane.md`
