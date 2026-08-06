# ADR 0033: Checkpoint export version 2 carries deletion tombstones

## Status

Accepted on 2026-08-06.

## Context

The version-1 checkpoint bundle preserves complete live aggregate, revision, and lifecycle-event
history but predates physical checkpoint erasure. Exporting a scope after deletion as an empty
history would lose the anti-resurrection fact, so a personal-to-team import could later recreate a
checkpoint that the user explicitly deleted.

The portable contract must retain minimal deletion state without changing the meaning or digest of
an existing version-1 bundle and without reconstructing erased content.

## Decision

`mnemo.checkpoint-export.v2` adds a canonical `deletions` collection to the existing exact-task
bundle. Every deletion remains the strict payload-free domain action: deletion and checkpoint
identity, exact scope, user actor, bounded action key, and deletion time only. Deleted checkpoint
identities must be disjoint from live aggregates, and deletion identities, checkpoint identities,
and action keys must be unique and canonically ordered. The version-2 content digest covers both
live history and tombstones.

The parser continues to validate the exact original version-1 field set and digest, representing
its absent deletion state as an empty tuple. New exports always use version 2. Import preserves the
checkpoint identity, actor, action key, and deletion time while deriving a new deterministic
deletion identity from the explicit target scope. It never creates an aggregate, revision,
evidence payload, event, observation, or outbox job for a tombstone.

PostgreSQL records the retained source deletion identity, source bundle digest, and import time on
the native forced-RLS tombstone. A fixed-search-path target guard permits this payload-free import
without requiring the erased aggregate to exist. The complete live history and tombstone set are
inserted and re-exported in one transaction; only an empty or already-identical target is accepted.

## Consequences

- Personal-to-team transfer preserves checkpoint anti-resurrection state with verified typed
  counts and source/target hashes.
- Existing version-1 exports remain readable and cannot claim deletion history they never carried.
- Source and target deletion identities intentionally differ because scope is part of identity;
  the source identity and digest remain explicit PostgreSQL provenance.
- User-controlled exports, backups, scheduled checkpoint retention, and authenticated remote
  delivery remain separate lifecycle boundaries.

## Verification

Domain tests cover version-1 compatibility, version-2 canonical round trips, tampering, duplicate
deletions, live/deleted overlap, and scope rebasing. Reference and SQLite tests cover restart-
stable export, exact import, conflicts, and replay. Real PostgreSQL tests cover migration
rollback/retry, SQLite-to-team transfer, provenance, absence of deleted payload, forced-RLS viewer
denial, anti-resurrection, verified counts and hashes, and exact retry.
