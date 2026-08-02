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
and predecessor links use `CheckpointRevisionId`. Legacy `Checkpoint` remains an explicit
compatibility and migration-input DTO until 10A.3c removes old consumers. Conversion is through
`CheckpointContent.from_legacy`, never implicit aliasing.
