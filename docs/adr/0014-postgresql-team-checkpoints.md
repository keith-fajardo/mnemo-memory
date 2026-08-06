# ADR 0014: Team checkpoints retain one immutable task-scoped revision chain

- **Status:** accepted
- **Date:** 2026-08-06
- **Deciders:** Mnemo maintainers
- **Issue:** 21E
- **Supersedes:** none
- **Superseded by:** none

## Context

The PostgreSQL control plane and knowledge repository cannot yet resume team work because the
canonical checkpoint remains personal-only. The existing storage-neutral checkpoint and lifecycle
event contracts already define the required creation, revision, terminal transition, evidence,
pagination, and retry behavior. Team parity should implement those contracts without creating a
second checkpoint format or prematurely adding a remote service.

## Decision

PostgreSQL migration 0003 adds exact task-scoped checkpoint aggregates, immutable checkpoint
revisions, and append-only checkpoint lifecycle events inside `mnemo_team`. Every row repeats its
workspace, project, owner, visibility, session, and task identity. All three tables enable and
force row-level security through the existing principal/workspace/operation authorization
function. Runtime writes use `contribute`; reads use `read`.

The aggregate stores only the current revision pointer and lifecycle state. Revisions retain the
existing canonical `CheckpointContent` and evidence-reference serialization as JSONB; scope,
identity, revision, predecessor, status, and time remain relational and constrained. This keeps
the storage-neutral domain format exact without inventing team-only evidence tables. A deferred
constraint verifies that the aggregate pointer names a revision with the same complete scope,
identity, number, and status. A fixed-search-path trigger verifies each predecessor and prevents a
child row from substituting another task scope.

`PostgreSQLCheckpointRepository` implements both the existing checkpoint aggregate and lifecycle
event repository contracts. Creation and every revision transition append their deterministic
lifecycle event in the same transaction. Revision updates lock the aggregate and compare the exact
expected current revision before inserting a successor. Completion and abandonment are terminal;
an identical terminal retry returns the committed revision while a stale or competing write fails.
Lifecycle events derive evidence from their immutable revision and are independently appendable
only when their scope, revision number, time, and evidence match it exactly.

## Alternatives considered

- **Store one mutable checkpoint JSON document.** Rejected because it loses revision provenance,
  stale-writer detection, and independently constrained lifecycle state.
- **Copy the personal SQLite evidence tables.** Rejected because canonical evidence is already
  represented by the storage-neutral revision serialization; duplicating it would add joins and a
  second deletion boundary without a demonstrated team workflow.
- **Add session and task authority tables now.** Rejected because the current authorization model
  is workspace/project based and exact session/task identifiers already isolate checkpoint rows.
  New authority concepts require their own approved contract rather than speculative schema.
- **Add checkpoint outbox and episodic parity in this migration.** Rejected because those are
  separate repository contracts and would expand the bounded checkpoint issue.

## Consequences

Canonical team handoffs can now survive PostgreSQL restarts and preserve their complete immutable
revision/event provenance under RLS. Team mode is still unavailable: no authenticated service
composes this adapter, and source-observation, episodic, structural, import, remote transport,
backup, quota, and operational requirements remain.

The runtime role can select/insert aggregates, revisions, and events and can update only aggregate
current pointers. It cannot update or delete immutable revisions or lifecycle events. Migration
owners retain schema-recovery responsibility and are never used by the adapter.

## Security and privacy implications

Authorization executes before exact checkpoint, revision, or event selection. Owner-only
visibility retains no administrator bypass, and private-project reads and writes use the same role
matrix as the control plane. Trigger functions use a fixed `pg_catalog` search path and reveal no
payload. Database failures become bounded repository errors; no PostgreSQL message or content is
returned.

The checkpoint content and evidence remain untrusted data. This migration does not authenticate a
principal, add retention/deletion propagation, or make a database credential suitable for an
agent. Those remain release blockers for team exposure.

## Token and cost implications

Active checkpoint selection remains one exact task scope and existing pagination defaults. The
adapter performs no model or embedding call and creates no additional context category.

## Dependency and licensing implications

No dependency is added. The adapter uses the already approved optional PostgreSQL driver and the
existing team runtime extra.

## Reversal or migration strategy

Migration 0003 is atomic and forward-only. A failure while upgrading a valid v2 database leaves
the schema and ledger at v2. Before team release, recovery is restore of the verified pre-upgrade
backup or correction followed by idempotent migration. A future removal must first migrate or
export canonical revision/event state; dropping these tables is not a projection rebuild.

## Verification

- A real v2-to-v3 injected failure retains ledger `(1, 2)` and no checkpoint table; retry reaches
  `(1, 2, 3)`.
- Creation, historical/current reads, active selection, append, completion, abandonment, identical
  terminal retry, lifecycle event retrieval, and event idempotency run against real PostgreSQL.
- A stale writer creates neither a revision nor an event.
- A private-project viewer cannot read or create the owner's checkpoint, and a different task or
  workspace scope cannot discover it.
- Migration inspection requires forced RLS on all three tables, a deferred current-pointer
  constraint, public privilege revocation, and least-privilege runtime grants.

## References

- `docs/implementation-plan.md`, Milestone 9
- `docs/adr/0010-team-authorization-kernel.md`
- `docs/adr/0012-postgresql-team-control-plane.md`
- `docs/adr/0013-postgresql-team-knowledge-pgvector.md`
