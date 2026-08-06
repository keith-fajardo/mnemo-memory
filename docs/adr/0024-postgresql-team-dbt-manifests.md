# ADR 0024: Team dbt manifests are immutable forced-RLS projections

- **Status:** accepted
- **Date:** 2026-08-06
- **Deciders:** Mnemo maintainers
- **Issue:** 21P
- **Supersedes:** none
- **Superseded by:** none

## Context

Personal mode already retains minimized authoritative dbt manifest projections, but team
PostgreSQL cannot answer the same lineage questions. Team parity requires exact scoped graph
storage and truthful activation history without retaining raw artifacts, compiled SQL, or source
SQL and without introducing another database.

## Decision

Migration 0013 adds immutable project-scoped manifest snapshots, minimized typed nodes, typed
lineage edges, explicit activations, and last-sync state. Every table repeats workspace, project,
owner, and visibility and forces row-level security. Composite foreign keys bind every node to its
snapshot and both edge endpoints to nodes in that exact scoped snapshot.

One project-keyed advisory transaction lock serializes compare-and-swap activation. A store checks
the caller's expected active snapshot, reuses an exact content-digest match, inserts a new complete
graph atomically when needed, appends an activation only when identity changes, and updates sync
state. Activation events—not UUIDs or ingestion timestamps—define transitions. A fixed-search-path
trigger permits only active-state changes matching the newest activation and rejects changes to
immutable fields. Runtime updates are column-limited to active state and last-sync time.

Metadata retains schema and dbt versions, observed timestamps, invocation/project labels, content
and normalized-graph digests, safe artifact identity, bounded source-state fingerprints, and
currentness. Nodes retain only the existing minimized domain projection and evidence. Edges retain
typed endpoints and evidence. Raw manifests, SQL, compiled content, macro bodies, adapter responses,
warehouse results, credentials, and environment values are not stored.

## Alternatives considered

- **Store the raw manifest JSON.** Rejected because graph queries need only the reviewed minimized
  projection and raw artifacts increase sensitive retention.
- **Use a graph database.** Rejected because bounded PostgreSQL adjacency queries satisfy the
  existing contract without another dependency or authorization plane.
- **Replace the active snapshot in place.** Rejected because immutable snapshots and explicit
  transitions are required for provenance, stale-state comparison, and retry safety.

## Consequences

Team PostgreSQL now provides the core manifest snapshot, graph, transition, and sync portion of
the existing project-index contract. Catalog, run-results, and source-freshness projections remain
the next bounded parity issue. No filesystem scanner, dbt execution, warehouse access, worker,
remote service, or team import is introduced.

## Security and privacy implications

Authorization occurs in PostgreSQL before reconstruction or graph selection. Private-project
viewers receive no snapshot, count, path, node, or edge disclosure. The runtime role cannot update
manifest metadata or delete projections. Invalid endpoints fail through both domain validation and
same-snapshot foreign keys.

## Token and cost implications

No model, embedding, or network call is added. Exact structural retrieval can replace broad source
replay with bounded authoritative node and edge evidence.

## Dependency and licensing implications

No dependency is added. The implementation uses the existing PostgreSQL driver and Mnemo-owned
domain, authorization, and storage contracts.

## Reversal or migration strategy

Migration 0013 is atomic and forward-only. Failure from valid v12 leaves ledger versions 1 through
12 and creates no dbt manifest table. Before team release, recovery is restore of the verified
pre-upgrade backup or correction followed by idempotent migration and rebuild from authoritative
dbt artifacts.

## Verification

- Injected v12-to-v13 failure leaves the v12 schema intact and clean retry reaches v13.
- Real PostgreSQL coverage exercises exact digest replay, CAS conflict, two activations,
  reactivation, deterministic listing, node/file/adjacency/batch queries, and restart durability.
- Tests cover invalid endpoints, conflicting identity rollback, foreign-project/private-viewer
  denial, immutable-column privileges, and trigger rejection of unrecorded active-state changes.

## References

- `docs/implementation-plan.md`, Milestones 3 and 9
- `docs/adr/0003-dbt-manifest-lineage.md`
- `docs/adr/0012-postgresql-team-control-plane.md`
