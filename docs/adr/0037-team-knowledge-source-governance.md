# ADR 0037: Approve stable team knowledge sources before retrieval

## Status

Accepted on 2026-08-06.

## Context

The team PostgreSQL profile already stores immutable knowledge revisions behind forced RLS, but
scope authorization alone does not establish who created a source or whether a maintainer accepts
it as shared context. Treating every contribution as immediately retrievable lets an authorized
contributor inject unreviewed text into another agent's context. Requiring approval for every edit
would instead turn a stable reviewed source into a manual per-revision queue.

## Decision

Each team knowledge source records the authenticated creating principal as its immutable source
owner. Each revision records the authenticated contributing principal as author. Corrections keep
the existing immutable predecessor chain and current-revision compare-and-swap, so two revisions
based on one predecessor cannot both commit and no automatic merge is attempted.

A new source is pending and its content, links, procedures, skills, and embeddings are excluded from
team retrieval. A project maintainer, workspace administrator, or workspace owner may approve the
stable source identity while naming its exact current revision. That revision is the review
evidence; approval remains valid for later conflict-checked revisions of the same source. Approval
is one immutable action with a deterministic caller key. Exact retry is idempotent; another action,
reused key, stale expected revision, deleted source, foreign scope, missing approval OAuth scope, or insufficient role fails
closed. Listing approval status is content-free and bounded.

The two management operations exist only on the OAuth-authenticated team MCP surface. Personal
SQLite knowledge remains unchanged.

Rows created before migration 0021 have no recoverable actor evidence. They are conservatively
attributed to their existing scope owner with both authentication flags false; Mnemo does not
retroactively claim that this principal created or authored them.

## Consequences

- A contributor can add or correct a source without silently placing unreviewed text in retrieval.
- Source ownership and each revision's author remain independently inspectable.
- Reviewed sources do not require repetitive approval after every ordinary correction.
- Approval is source trust, not endorsement of every sentence; deterministic conflict and secret
  controls still apply to every revision.
- Rejection workflow, semantic contradiction inference, automatic merge, and a general knowledge
  editor remain outside this decision.

## Security and privacy

PostgreSQL migration 0021 adds forced-RLS approval rows and fixed-search-path guards that bind the
creator, author, approver, exact scope, live source, and expected revision to the authenticated
transaction principal. Retrieval joins approval before loading content. Status output contains
identities, relative path, revision number, ownership, and approval metadata, never note content.
Tombstoning still erases revision, section, link, and vector payloads; the payload-free source and
approval record may remain for lifecycle provenance.

## Token and cost

The feature makes no model or embedding call. Pending sources contribute zero context tokens.
Approval status is bounded to 100 content-free records.

## Dependencies and originality

The implementation uses existing Mnemo domain, PostgreSQL, OAuth, and MCP boundaries and adds no
dependency. Its schema, policy, tests, and documentation are original Mnemo work.

## Reversal and recovery

Migration 0021 is additive and forward-only. A failure rolls back the migration and ledger entry in
one transaction. Recovery from a committed defective migration is restore of the verified
pre-upgrade backup or a corrective forward migration; dropping attribution or approval rows would
discard security provenance and therefore is not an acceptable automatic rollback.

## Verification

Unit tests cover deterministic approval identity, strict serialization, content-free status, exact
revision transport, and retry. Real PostgreSQL tests cover migration rollback, pending-source
exclusion, maintainer/administrator authority through forced RLS, contributor denial, stale
approval, exact retry, stable ownership, revision authorship, competing corrections, scope
isolation, approved retrieval, vector handling, and payload deletion.
