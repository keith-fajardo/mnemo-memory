# ADR 0017: Team extracted candidates stay inactive until explicit review

- **Status:** accepted
- **Date:** 2026-08-06
- **Deciders:** Mnemo maintainers
- **Issue:** 21H
- **Supersedes:** none
- **Superseded by:** none

## Context

Team task events and delivery state exist in PostgreSQL, but an extraction worker has no durable
team repository for bounded proposals. The existing storage-neutral candidate and review contracts
already separate untrusted proposals from canonical authority and require an explicit verified user
approval before a candidate can become active. PostgreSQL needs parity with those contracts before
any worker or authenticated service is composed.

## Decision

Migration 0006 adds exact-task `episodic_memory_candidates`, `episodic_candidate_reviews`, and
`active_episodic_memories` tables. Every row repeats workspace, project, owner, visibility,
session, and task identity, uses forced RLS, and is immutable to the runtime role. Composite foreign
keys and fixed-search-path triggers bind candidates to canonical task events, reviews to candidates,
and active markers to matching approvals. Candidate retention and evidence JSON must equal the
source event's canonical values.

`PostgreSQLEpisodicMemoryRepository` accepts one contiguous batch of at most four proposals from
one source event and extractor version. Scope, retention, evidence, extractor/provider/model/prompt
versions, proposal indexes, deterministic identities, sensitivity, and safety are validated before
insertion. An exact repeated batch is idempotent; changed extraction output or identity reuse fails
atomically. Candidates remain `candidate` regardless of model confidence.

Review loads only one authorized candidate, reruns candidate and review safety, and accepts one
immutable verified-user action. Rejection stores the review but no active marker. Approval stores
the review and matching active marker atomically, preserving candidate/source/extraction provenance
and merging verified review evidence through the existing domain contract. Exact retry is
idempotent; competing review or action-key reuse fails.

## Alternatives considered

- **Activate high-confidence proposals automatically.** Rejected because model confidence is not
  user consent, evidence authority, or deterministic mutation approval.
- **Copy candidate scope or retention from provider output.** Rejected because those fields must
  come from the canonical source event and policy.
- **Combine candidates and active memories in one mutable table.** Rejected because inactive
  proposals and user-approved state have different authority and audit requirements.
- **Add the extraction worker now.** Rejected because worker scheduling, provider consent, budgets,
  delivery completion, and authenticated composition are separate bounded issues.

## Consequences

PostgreSQL can now durably stage source-bound episodic proposals and explicitly approve or reject
them. No candidate becomes active without one matching user review. The implementation does not
provide a model call, worker, scheduler, correction/retraction, retention, deletion, export,
personal import, or team service.

The immutable schema deliberately has no ordinary update/delete grants. Later retention and
deletion work must add constrained, tested erasure operations instead of broad mutable access.

## Security and privacy implications

Authorization and exact task scope precede candidate or active-memory selection. Safety policy
checks claims, review reasons/action keys, and evidence references before persistence. Database
triggers and foreign keys prevent cross-scope source, review, and activation links. Rejected
content produces bounded errors and no row.

## Token and cost implications

Each source/extractor attempt stores at most four 1,200-character candidates. This issue invokes no
provider, computes no embedding, and changes no retrieval budget or prompt.

## Dependency and licensing implications

No dependency is added. The adapter uses the existing PostgreSQL driver and Mnemo-owned domain,
policy, and storage contracts.

## Reversal or migration strategy

Migration 0006 is atomic and forward-only. Failure from valid v5 leaves ledger
`(1, 2, 3, 4, 5)` and no candidate table. Recovery before team release is restore of the verified
pre-upgrade backup or correction followed by idempotent migration. Future schema replacement must
preserve candidate identity, source/extraction provenance, review evidence, decisions, and active
state.

## Verification

- An injected v5-to-v6 failure retains v5 and a clean retry reaches v6.
- Real PostgreSQL tests cover exact batch retry, changed output, secret and source-authority
  rejection, ordering, filtering, approval, rejection, active reads, and restart durability.
- Competing review, action-key reuse, unsafe review, different-task access, and private-project
  viewer access fail closed.
- Runtime privilege inspection denies update/delete on all three tables, and a rejected review
  cannot be forged into an active marker.

## References

- `docs/implementation-plan.md`, Milestones 4 and 9
- `docs/adr/0015-postgresql-team-task-events-outbox.md`
- `docs/adr/0016-postgresql-team-approved-episodic-governance.md`
