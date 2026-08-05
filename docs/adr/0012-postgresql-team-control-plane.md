# ADR 0012: PostgreSQL team authority uses forced RLS and transaction-local identity

- **Status:** accepted
- **Date:** 2026-08-06
- **Deciders:** Mnemo maintainers
- **Issue:** 21C
- **Supersedes:** none
- **Superseded by:** none

## Context

ADRs 0010 and 0011 define team authorization and atomic authority mutations without durable team
storage. Personal SQLite cannot provide tenant isolation. The first PostgreSQL adapter must enforce
the same contract below the application boundary without exposing migration-owner privileges to
runtime requests or making the optional database driver part of a personal installation.

## Decision

Team authority state is stored in the dedicated `mnemo_team` PostgreSQL schema. Migration 0001 is
one atomic, forward-only transaction containing workspace, membership, project, project-membership,
payload-free audit, and migration-ledger tables. Deferrable foreign keys and constraint triggers
enforce one active workspace owner, active workspace authority for project owners and active
project grants, and an atomic ownership swap. The workspace/request uniqueness constraint and a
canonical SHA-256 mutation fingerprint implement Issue 21B idempotency without storing a mutation
payload.

Every runtime repository is bound to one authenticated principal and workspace. Each transaction
sets that principal, workspace, closed operation, and statement timeout with transaction-local
PostgreSQL settings before accessing a team table. Missing, malformed, or unknown values produce a
denial. All authority and audit tables enable and force row-level security. Policy helpers reproduce
ADR 0010's workspace/private-project/owner-item matrix before rows become visible or writable.

The migration connection owns schema maintenance. Runtime uses a separately provisioned role that
must be a non-owner, non-superuser, non-`BYPASSRLS` role. It receives only schema usage, exact table
operations, sequence access, and execution of fixed policy functions. Audit rows are append-only to
runtime. Public schema/table/function privileges are revoked. Constraint and database failures are
translated to bounded storage-neutral errors rather than returning SQL or row details.

`pg8000==1.31.5` is the optional `team` transport. It is not imported by domain, policy, personal
storage, or package initialization, and a normal personal install does not acquire it. Its complete
locked dependency graph and permissive licenses are recorded in the dependency register.

## Alternatives considered

- **Reuse personal SQLite.** Rejected because SQLite supplies no safe team tenancy boundary.
- **Authorize only in application code.** Rejected because a query-scoping defect would become a
  direct cross-tenant disclosure; forced RLS is required defense in depth.
- **Connect runtime as the schema owner.** Rejected because table owners normally bypass RLS and
  migration privileges exceed the request path's needs.
- **Use one database role per end user.** Rejected for this bounded storage issue because identity
  lifecycle and authenticated service composition are later issues; exact transaction-local
  identity keeps the adapter replaceable while RLS still denies absent context.
- **Add pgvector now.** Rejected because vector projections and personal-data parity belong to the
  next approved backend issue.

## Consequences

The control plane is durable, transactional, and independently tenant-filtered, but it does not yet
make team mode usable. There is no authentication boundary, remote MCP/API, personal-data import,
shared knowledge, deletion workflow, backup service, or operational deployment in this issue.

The runtime database credential is service infrastructure and must never be exposed to an end user.
The later authenticated service must derive the principal/workspace binding from verified identity,
not arbitrary request fields. Connection pools must begin a transaction before setting local values
and must roll back before reuse; transaction-local settings prevent identity leakage after commit or
rollback.

## Security and privacy implications

Security-definer policy functions have a fixed `pg_catalog` search path and return only authorization
booleans. They do not return membership rows or content. Foreign-key and uniqueness failures can be
tenant existence side channels if exposed verbatim, so the adapter emits bounded conflict/denial
outcomes. The schema owner and superusers can still bypass database policy and therefore remain
restricted to migration and recovery operations outside runtime composition.

## Token and cost implications

The adapter performs exact authority reads and no model, embedding, or context operation. It adds no
agent tokens. PostgreSQL is optional; personal users retain the SQLite-only install and runtime.

## Dependency and licensing implications

The direct BSD-3-Clause `pg8000` dependency and its `python-dateutil`, `six`, `scramp`, and
`asn1crypto` transitive graph are exactly pinned, registered, and isolated behind the Mnemo-owned
DB-API connection-factory boundary.

## Reversal or migration strategy

Migration 0001 is applied atomically: an error rolls back the schema and ledger together. There is
no destructive automatic down migration. Before team mode is released with user data, recovery is
to restore the pre-migration database backup or create a clean database and rerun the migration.
Every later migration must include a rollback or document its forward recovery and backup restore
point before acceptance. The storage-neutral Issue 21B contract permits replacement of pg8000 or
PostgreSQL without changing domain or policy types.

## Verification

- Migration succeeds idempotently, is packaged, and rolls back completely under injected failure.
- The runtime role is verified as non-owner, non-superuser, and non-`BYPASSRLS`.
- Real PostgreSQL tests cover durable atomic mutations, exact retries, ownership transfer, audit
  pagination, private-project access, cross-tenant denial, and absent/malformed setting denial.
- The complete workspace and private-project role matrices are compared with ADR 0010's Python
  policy against a real database.
- `npm run check` starts an isolated PostgreSQL server and makes this suite mandatory.

## References

- `docs/implementation-plan.md`, Milestone 9
- `docs/adr/0010-team-authorization-kernel.md`
- `docs/adr/0011-team-control-plane-contract.md`
- `docs/product-memory-contract.md`, Scope model
- `docs/threat-model.md`, Cross-tenant team authorization
- PostgreSQL row-security documentation: <https://www.postgresql.org/docs/current/ddl-rowsecurity.html>
- PostgreSQL configuration-setting functions: <https://www.postgresql.org/docs/current/functions-admin.html#FUNCTIONS-ADMIN-SET>
