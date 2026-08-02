# ADR 0002: Stable checkpoint identities and immutable revisions

## Context

Issue 5 stores each `Checkpoint` row under `checkpoint_id`; `revise()` creates a replacement
checkpoint ID and links it through `supersedes_checkpoint_id`. Consequently, history is a chain of
logical IDs, and the current query selects the newest chain member. This preserves content and
evidence, but does not provide a stable aggregate identity or atomic compare-and-swap revision.

## Decision

Adopt one logical `checkpoint_id` and separate immutable revision records. Each revision has a
unique revision identity, a monotonic number scoped to the checkpoint, an optional predecessor
revision identity, resulting lifecycle status, immutable payload, and its evidence links. The
checkpoint header owns scope, current revision pointer, lifecycle status, and timestamps.

## Alternatives considered

Keep the replacement-checkpoint chain; use mutable revision rows; or create an event stream. The
chain cannot give a stable identity, mutable rows lose history, and event sourcing is out of scope.

## Consequences

Reads use the header current pointer; historical reads use `(checkpoint_id, revision_number)`.
Mutations compare the expected current revision while atomically inserting a new revision and
updating the header pointer. Existing IDs become aggregate IDs and existing payloads become
revision one.

## Security and privacy implications

Scope is immutable on the header and every revision retains its exact evidence links. Scope is
always supplied to repository reads.

## Token and cost implications

No model or tokenizer is introduced. Revisions retain caller-supplied token estimates.

## Dependency and licensing implications

No dependency is added.

## Reversal or migration strategy

Migration is forward-only. SQLite backup before upgrade is the recovery path; failed migration is
transactionally rolled back. Legacy rows that cannot map without fabricating scope or evidence fail.

## Corrective amendment: canonical revision content

Implementation found that the legacy `Checkpoint` DTO encodes replacement-checkpoint identity:
revision numbers above one require a different `checkpoint_id` and `supersedes_checkpoint_id`.
Embedding it in a canonical `CheckpointRevision` therefore contradicts this ADR. Canonical revisions
now contain identity-free `CheckpointContent`; aggregate and revision IDs stay in their own fields,
and predecessor links use `CheckpointRevisionId`. Conversion is through
`CheckpointContent.from_legacy`, never implicit aliasing.

## Final 10A repository boundary review

The production `CheckpointRepository` exposes only aggregate/revision lifecycle operations:
scoped aggregate, current, and historical reads; aggregate creation; expected-revision append;
completion; abandonment; and active selection. Each mutation inserts a new immutable revision and
uses a scope-first compare-and-swap update of the aggregate current pointer. Identical terminal
retries return the existing revision; conflicting retries fail without another write.

Replacement-chain behavior is classified as legacy migration input only. The legacy `Checkpoint`
DTO and the explicitly named `SQLiteCheckpointRepository.create_legacy_checkpoint` fixture helper
remain solely to seed real v1 databases for forward-migration tests. The v2 migration mapper is the
only durable path that interprets `supersedes_checkpoint_id`. Legacy repository reads, chain
selection, and replacement writes were removed because no product caller uses them. The remaining
fixture helper will be removed during 10B when durable checkpoint consumers replace the fixture
boundary; it is not part of the canonical repository port and immediately serializes only the
legacy v1 representation for migration input.

V2 persists scope visibility alongside owner/workspace/project/session/task identifiers so exact
scope round trips are possible. SQLite triggers additionally reject a current pointer that does not
belong to its aggregate. Migration remains forward-only: back up the local SQLite profile before an
upgrade; failed upgrades roll back transactionally. MCP remains fixture-backed until 10B.
