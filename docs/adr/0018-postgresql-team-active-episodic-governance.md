# ADR 0018: Team active episodic memories use immutable optimistic governance

- **Status:** accepted
- **Date:** 2026-08-06
- **Deciders:** Mnemo maintainers
- **Issue:** 21I
- **Supersedes:** none
- **Superseded by:** none

## Context

PostgreSQL can stage team episodic candidates and activate one only after explicit verified-user
approval, but an approved claim cannot yet be corrected or retracted. The existing storage-neutral
governance contract defines an immutable revision chain, expected-revision compare-and-set, and
payload-free terminal retraction. PostgreSQL needs parity with that contract before active memories
can be safely composed by a future authenticated team service.

## Decision

Migration 0007 adds one exact-task `episodic_memory_governance` action table. Every row repeats
workspace, project, owner, visibility, session, and task identity and uses forced row-level
security. A fixed-search-path trigger binds each action to the matching active memory. Runtime
access is select/insert only; actions cannot be updated or deleted.

The approval action is revision one. Each correction or retraction names the expected current
revision, and a unique memory/expected-revision constraint prevents two actions from extending the
same revision. Corrections store bounded replacement claim, sensitivity, reason, and verified-user
evidence. Retractions store no claim or sensitivity and terminate the chain. The repository replays
the approval plus ordered actions to derive revisions and the current active value; it does not
store a second mutable current-claim projection. Active reads exclude retracted chains.

Deterministic safety and exact task scope are validated before insertion. An identical action or
source-key retry returns the committed result; changed reuse, a stale expected revision, unsafe
content, or any action after retraction fails closed.

## Alternatives considered

- **Update the candidate claim in place.** Rejected because it would erase approval provenance and
  make stale-writer conflicts or retraction replay ambiguous.
- **Store a mutable current revision beside the action stream.** Rejected because the bounded
  revision chain can be replayed directly, avoiding duplicate canonical state.
- **Permit reactivation after retraction.** Rejected because retraction is terminal in the existing
  contract and must not silently restore withdrawn content.
- **Add retention and deletion in this migration.** Rejected because those have distinct expiry,
  erasure, tombstone, and recovery requirements.

## Consequences

PostgreSQL can now replay approved episodic-memory corrections and hide a terminally retracted
memory after restart. This does not authenticate actors, schedule extraction, enforce retention,
delete/export data, import personal state, or expose a usable team service.

## Security and privacy implications

Authorization and complete task scope precede every read and mutation. Safety checks reject
prohibited secrets before persistence. Forced RLS, immutable privileges, the scope trigger, and
the optimistic uniqueness constraint prevent cross-task linkage, direct mutation, and revision
forks. A retraction action contains governance evidence and reason but no withdrawn claim payload;
the original approved candidate remains until the separate deletion/retention boundary is added.

## Token and cost implications

No provider, embedding, or retrieval-budget change is introduced. Corrections retain only the
bounded existing claim and evidence formats; retraction adds no replacement claim payload.

## Dependency and licensing implications

No dependency is added. The adapter uses the existing PostgreSQL driver and Mnemo-owned domain,
policy, and storage contracts.

## Reversal or migration strategy

Migration 0007 is atomic and forward-only. Failure from valid v6 leaves ledger
`(1, 2, 3, 4, 5, 6)` and no governance table. Recovery before team release is restore of the
verified pre-upgrade backup or correction followed by idempotent migration. Future replacement
must preserve action identity, exact scope, expected-revision links, evidence, ordering, and
terminal status.

## Verification

- An injected v6-to-v7 failure retains v6 and a clean retry reaches v7.
- Real PostgreSQL tests cover correction, exact retry, stale writers, unsafe content, competing
  identity reuse, terminal payload-free retraction, post-retraction denial, and restart replay.
- Different tasks and unauthorized private-project viewers cannot read the revision chain.
- Runtime privilege inspection denies update/delete on the governance table.

## References

- `docs/implementation-plan.md`, Milestones 4 and 9
- `docs/adr/0015-postgresql-team-task-events-outbox.md`
- `docs/adr/0017-postgresql-team-episodic-candidates-review.md`
