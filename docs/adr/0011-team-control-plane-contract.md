# ADR 0011: Atomic team authority state before PostgreSQL

- **Status:** accepted
- **Date:** 2026-08-06
- **Deciders:** Mnemo maintainers
- **Issue:** 21B
- **Supersedes:** none
- **Superseded by:** none

## Context

ADR 0010 fixes the canonical team authorization decision, but PostgreSQL row-level security cannot
be designed safely until workspace ownership, memberships, projects, and audit history have one
storage-neutral mutation contract. Authority changes are especially vulnerable to stale writes,
duplicate requests, partial owner transfers, orphaned project grants, and an audit append that
commits separately from the state it describes.

This issue defines repository semantics and an in-memory executable specification only. It does not
authenticate actors, expose a service, persist team data, or claim database isolation.

## Decision

One team workspace has one immutable identity, a creation time, and exactly one active owner. The
same owner is represented by one active workspace membership with the owner role. Ordinary
membership mutation cannot create, suspend, demote, or replace an owner. Ownership transfer is one
atomic operation: it compares the expected owner, requires the successor to be an active member,
sets the successor to owner, demotes the former owner to admin, updates the workspace, and appends
one audit event.

Workspace and project membership writes use compare-and-set against the exact previously read
record. A new membership must start active. An active project membership requires an active
workspace membership. Projects require an existing workspace and active workspace-member owner;
project visibility updates cannot change project identity or ownership. Wrong-workspace,
wrong-project, orphan, stale, second-owner, implicit-owner, and no-op mutations fail closed.

Every successful mutation appends one immutable `TeamAuditEvent` in the same repository operation.
The event contains only typed identities, action, actor, target workspace/project/principal,
request identity, and timestamp—never note content, prompts, credentials, email, arbitrary labels,
or mutation payload. An exact workspace/request pair identifies one mutation. The reference adapter
retains a bounded canonical mutation fingerprint outside the audit event so an identical retry is
idempotent while the same request with different state conflicts. A persistent adapter must provide
equivalent request-ledger semantics without retaining arbitrary user content.

Reads use exact composite keys. Audit history is ordered by commit and paginated with a hard
maximum of 100 records. An event requested through another workspace is reported as not found.

## Alternatives considered

- **Let each API handler update tables directly.** Rejected because ownership, audit atomicity, and
  stale-write behavior would vary by caller.
- **Use last-write-wins membership updates.** Rejected because a delayed administrator request
  could silently undo a suspension or ownership change.
- **Record the full before/after object in audit.** Rejected because authorization evidence needs
  identities and actions, not duplicated user or knowledge payloads.
- **Store only audit events and derive current membership by replay.** Rejected for the first team
  profile because authorization needs small exact current-state reads and future RLS predicates.
- **Add PostgreSQL immediately.** Rejected because an adapter and schema should implement an
  executable repository contract rather than define security behavior accidentally.

## Consequences

The next PostgreSQL issue has concrete transaction, uniqueness, foreign-key, idempotency, and audit
requirements. Application services can authorize an actor with ADR 0010 and then call one atomic
mutation. The reference adapter is not durable and team mode remains unavailable.

Membership removal is represented by suspension in this bounded contract. Permanent identity/data
deletion and backup propagation are later Milestone 9 issues and must not erase audit metadata
before its retention policy permits.

## Security and privacy implications

Exact keys prevent accidental broad tenant reads, but this repository does not authenticate or
authorize its actor. The later application service must load exact membership and apply ADR 0010
before invoking a mutation. PostgreSQL RLS and transaction-local authenticated scope remain
required defense in depth. Audit identities are security metadata and must receive authorization,
retention, encryption, and deletion-policy treatment; they are not user content and must never be
used as authentication evidence.

## Token and cost implications

The contract performs no retrieval ranking, embedding, or model call and adds no context tokens.
All state reads are exact-key; audit reads are bounded before materialization.

## Dependency and licensing implications

No dependency is added. The implementation uses the Python standard library and existing original
Mnemo domain contracts.

## Reversal or migration strategy

No durable team data exists in this issue, so the reference adapter can be replaced without data
migration. Once PostgreSQL persistence exists, incompatible ownership or audit changes require a
new ADR, a migration with rollback or documented forward recovery, and parity tests against this
contract.

## Verification

- Strict workspace and payload-free audit serialization.
- Atomic workspace creation and exact idempotent replay.
- Rejection of changed payload under a reused request identity.
- Compare-and-set membership and project visibility conflicts.
- Exactly one owner and atomic transfer of workspace plus both memberships.
- Project and project-membership referential and active-membership constraints.
- Exact cross-workspace/project not-found behavior and bounded audit pagination.
- A concurrent competing-write test with exactly one winner and one audit append.

## References

- `docs/implementation-plan.md`, Milestone 9
- `docs/adr/0010-team-authorization-kernel.md`
- `docs/product-memory-contract.md`, Scope model
- `docs/threat-model.md`, Cross-tenant team authorization
- `AGENTS.md`, security, privacy, and migration requirements
