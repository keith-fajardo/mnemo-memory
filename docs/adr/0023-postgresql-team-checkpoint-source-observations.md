# ADR 0023: Team checkpoint source observations are immutable and non-causal

- **Status:** accepted
- **Date:** 2026-08-06
- **Deciders:** Mnemo maintainers
- **Issue:** 21O
- **Supersedes:** none
- **Superseded by:** none

## Context

Team checkpoints and source snapshots are now durable, but no scoped relation records which source
projection was observed immediately after one checkpoint revision. The existing contract treats
this as co-observation metadata, never proof that a source change caused or explains checkpoint
content.

## Decision

Migration 0012 adds one immutable forced-RLS observation per exact checkpoint revision. It repeats
complete task scope and stores checkpoint, revision, source-snapshot identities and observation
time only. A composite foreign key binds the source to the same workspace/project/owner/visibility,
and a fixed-search-path trigger binds the revision to the complete task scope and checkpoint.

The adapter verifies both authorized sides before insertion. Exact replay is idempotent; a second
snapshot, missing side, scope mismatch, competing insert, or unauthorized access fails closed.
Runtime privileges are read/insert only.

## Consequences

Team context can now cite a truthful source snapshot alongside a checkpoint without inferring
causality. Automatic filesystem parsing, checkpoint deletion/export/retention, and dbt observation
remain separate issues.

## Security and privacy implications

Authorization precedes both target checks and reads. The row contains no checkpoint content, source
path, source body, reason, or evidence payload. Cross-task and private-project access returns the
same not-found outcome.

## Token and cost implications

No model or embedding is invoked. A precise snapshot reference can reduce repository rediscovery
and prevent stale structural context from entering prompts.

## Dependency and licensing implications

No dependency is added.

## Reversal or migration strategy

Migration 0012 is atomic and forward-only. Failure from valid v11 leaves ledger versions 1 through
11 and creates no observation table. Recovery before team release is restore or correction followed
by idempotent migration.

## Verification

Real PostgreSQL tests cover exact replay, competing and missing snapshots, restart durability,
cross-task/private-project denial, immutable runtime privileges, and v11-to-v12 rollback/retry.

## References

- `docs/implementation-plan.md`, Milestones 3 and 9
- `docs/adr/0014-postgresql-team-checkpoints.md`
- `docs/adr/0022-postgresql-team-source-structure.md`
