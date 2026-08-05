# ADR 0010: Team authorization kernel before team persistence

- **Status:** accepted
- **Date:** 2026-08-06
- **Deciders:** Mnemo maintainers
- **Issue:** 21A
- **Supersedes:** none
- **Superseded by:** none

## Context

Milestone 9 requires PostgreSQL row-level security, remote MCP, workspace membership, roles, and
project visibility. Implementing those adapters before fixing one storage-independent decision
contract would risk inconsistent application and database rules. Personal SQLite intentionally has
no team-tenancy claim, and its nullable workspace fields cannot become a wildcard team rule.

This decision covers authorization inputs and deterministic decisions only. It does not create a
database schema, authenticated network principal, membership mutation service, audit log, import
format, OAuth flow, or remote listener.

## Decision

Mnemo defines immutable strict domain values for active or suspended workspace membership, active
or suspended project membership, workspace and project roles, and private or workspace-visible
projects. The existing `OwnerId` is the canonical principal identity after an authentication
adapter has mapped an external subject; memory is never evidence for that mapping.

The policy package owns one pure deny-by-default decision over a closed operation enum: read,
contribute, manage project, manage membership, manage workspace, and approve source. Every request
must provide one non-null workspace scope and one exact active workspace membership matching both
the principal and workspace before project authorization is considered.

Workspace role permissions are:

| Role | Permissions |
| --- | --- |
| owner | all closed operations |
| admin | all except manage workspace |
| editor | read and contribute |
| viewer | read |

A workspace-visible project uses that matrix. A private project additionally requires the project
owner, a workspace owner/admin, or one exact active project membership. Project maintainers may
read, contribute, manage project, and approve sources; contributors may read and contribute;
viewers may read. Workspace-membership and workspace-ownership operations never derive from a
project role. Owner-visible data remains restricted to its exact owner before role evaluation, so
administrator status cannot bypass item visibility.

Every denial uses a closed payload-free reason code. No policy call retrieves membership, infers a
role, treats null as broad access, or changes storage. Later application composition must retrieve
only the exact membership keys required for this decision; later PostgreSQL RLS must enforce an
equivalent restrictive rule below it.

## Alternatives considered

- **Put authorization only in PostgreSQL RLS.** Rejected because domain/application tests and
  non-database operations still need one canonical rule, while adapter-only logic is harder to
  compare and reuse safely.
- **Put authorization only in application services.** Rejected because one missed query could
  become a cross-tenant disclosure; RLS is required as defense in depth.
- **Use an external authorization service now.** Rejected because it adds a dependency and remote
  failure mode before Mnemo has a concrete team workflow, and canonical source/retention/deletion
  decisions must remain Mnemo-owned.
- **Use a numeric role hierarchy.** Rejected because project and workspace permissions are not one
  hierarchy; explicit operation sets are easier to audit and do not silently grant new operations.
- **Defer the policy until remote MCP.** Rejected because storage and API design would then precede
  the security contract they must enforce.

## Consequences

PostgreSQL schema and RLS work now have an executable role matrix and exact denial vocabulary.
Application services can remain storage-neutral and tests can compare application decisions with
database decisions. Adding a role or operation is deliberately a contract change requiring review.

This issue alone offers no usable team mode. Membership lifecycle persistence, invitation and
ownership-transfer constraints, audit history, authentication, RLS, and remote service behavior
remain required bounded issues.

## Security and privacy implications

Authorization precedes every future team storage read and every ranking operation. Cross-workspace
or cross-project membership objects are rejected rather than normalized. Suspended membership is a
denial. Workspace administrators cannot read another principal's owner-only item. A private project
does not become discoverable through a missing project membership.

The policy trusts an authenticated principal mapping supplied by a future composition boundary;
it does not authenticate. That mapping, revocation latency, membership mutation invariants, RLS,
connection pooling, and audit records require separate threat review before network exposure.

## Token and cost implications

The decision invokes no model, embedding, or external service and adds no retrieved context.
Authorization performs constant-size comparisons over exact records. Later adapters must retrieve
only the exact membership/project rows and must not load broad candidate sets before policy.

## Dependency and licensing implications

No dependency is added. Domain and policy code use only the Python standard library and existing
Mnemo identifiers.

## Reversal or migration strategy

No persistent team data exists, so this issue has no data migration. Before team persistence is
released, an incompatible role change may replace these contracts and their fixtures. After
persistence exists, role or operation changes require a new ADR, database migration, RLS update,
and compatibility plan.

## Verification

- Strict immutable membership and project serialization round trips.
- Complete closed workspace-role and project-role operation matrices.
- Wrong-principal, cross-workspace, cross-project, missing, and suspended membership denials.
- Owner-only item and private-project administrator/owner cases.
- Domain-only dependency enforcement and full repository architecture gate.
- Future parity tests executing the same matrix against PostgreSQL RLS before team exposure.

## References

- `docs/implementation-plan.md`, Milestone 9
- `docs/product-memory-contract.md`, Scope model
- `docs/threat-model.md`, Cross-tenant team authorization
- `AGENTS.md`, security and architecture requirements
