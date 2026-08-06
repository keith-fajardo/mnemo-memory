# ADR 0025: Team dbt supplemental artifacts are minimized immutable projections

- **Status:** accepted
- **Date:** 2026-08-06
- **Deciders:** Mnemo maintainers
- **Issue:** 21Q
- **Supersedes:** none
- **Superseded by:** none

## Context

Team PostgreSQL now stores authoritative manifest graphs but cannot preserve the existing bounded
catalog, run-results, and source-freshness evidence attached to those graphs. Parity requires exact
typed round trips and manifest-resource binding without copying raw dbt artifacts or expanding the
schema into tables for fields that are never independently queried.

## Decision

Migration 0014 adds one immutable supplemental-artifact table and one manifest-resource link table.
Both repeat exact project scope, force RLS, and bind to the exact immutable manifest snapshot.
Artifact kind and content digest identify a retained version; one partial unique index selects the
active version of each kind for each snapshot. A project/snapshot/kind advisory transaction lock
serializes insertion and activation. Exact digest replay is idempotent, while conflicting schema,
normalized digest, or source identity fails closed.

The projection JSON is a Mnemo-owned serialization of already minimized domain objects, not the raw
artifact. Catalog projections contain relation identities/types, ordered column names/types, error
count, and evidence. Run-results projections contain resource status, bounded timings, durations,
failure count, safe command name, and evidence. Freshness projections contain dbt-reported status,
timestamps, age, thresholds, duration, and evidence. Metadata remains in typed columns. Every
resource identity is also stored as a relational link with a composite foreign key to a node in the
same scoped manifest snapshot.

A fixed-search-path trigger makes every field except `is_active` immutable. Runtime updates are
column-limited to that flag. Retrieval authorizes and verifies the manifest before selecting the
active projection, then reconstructs the strict domain contract; malformed stored data fails as a
payload-free storage outcome.

## Alternatives considered

- **Store raw catalog/run-results/sources JSON.** Rejected because it would retain unreviewed
  comments, statistics, messages, adapter/database payloads, filters, and arbitrary arguments.
- **Create a table for every nested field.** Rejected because no current workflow queries those
  fields independently; the resource-link table supplies the relational integrity requirement.
- **Merge supplemental fields into manifest rows.** Rejected because these artifacts have separate
  lifecycles, content digests, evidence, and availability.

## Consequences

`PostgreSQLProjectIndexRepository` now implements the complete existing manifest and supplemental
project-index protocol. It still does not execute dbt, contact a warehouse, schedule ingestion,
provide remote authentication, or import personal state.

## Security and privacy implications

Authorization and exact manifest verification precede resource validation, mutation, and
reconstruction. Composite foreign keys prevent cross-snapshot resource substitution. Raw artifact
bytes and prohibited fixture payloads are absent from stored projections. Private viewers receive
the same not-found result as an absent snapshot, and the runtime role cannot change a projection or
delete retained versions.

## Token and cost implications

No model, embedding, network, dbt, or warehouse call is added. Bounded relation columns, test
status, and freshness evidence can answer targeted questions without replaying artifacts or SQL.

## Dependency and licensing implications

No dependency is added.

## Reversal or migration strategy

Migration 0014 is atomic and forward-only. Failure from valid v13 leaves versions 1 through 13 and
no supplemental table. Before team release, recovery is restore of the verified pre-upgrade backup
or correction followed by idempotent migration and rebuild from authoritative dbt artifacts.

## Verification

- Injected v13-to-v14 failure leaves v13 intact and a clean retry reaches v14.
- Real PostgreSQL tests cover all three artifact kinds, exact replay, version switching,
  manifest-resource rejection, restart round trips, RLS isolation, immutable privileges, and
  prohibited-payload absence.
- Existing parser and SQLite/reference contract suites remain unchanged and green.

## References

- `docs/implementation-plan.md`, Milestones 3 and 9
- `docs/adr/0003-dbt-manifest-lineage.md`
- `docs/adr/0024-postgresql-team-dbt-manifests.md`
