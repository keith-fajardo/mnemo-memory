# ADR 0001: Standard-library SQLite migration harness

- **Status:** accepted
- **Date:** 2026-08-02
- **Deciders:** Mnemo maintainers
- **Issue:** Issue 5
- **Supersedes:** none
- **Superseded by:** none

## Context

The personal profile needs versioned schema creation before service work, without coupling the
domain or repository contract to an ORM or migration framework.

## Decision

Use version-controlled SQL files and a small `sqlite3` migration harness. Migrations are
forward-only, applied inside explicit transactions, recorded in `schema_migrations`, idempotent,
and reject databases newer than the running application.

## Alternatives considered

SQLAlchemy/Alembic would add dependencies before API or multi-backend mapping needs them. Ad-hoc
schema creation has no durable version history. Both are deferred.

## Consequences

The first adapter is small and deterministic. Future destructive migrations require a separate
backup/restore decision and tested recovery path.

## Security and privacy implications

Foreign keys, restrictive deletion, local file permissions, explicit transactions, and scope
predicates protect stored checkpoint evidence. The adapter makes no network call.

## Token and cost implications

No model calls, embeddings, or token processing are introduced.

## Dependency and licensing implications

No dependency is added; `sqlite3` is part of CPython already recorded in the dependency register.

## Reversal or migration strategy

The current migration is non-destructive and forward-only. Before any destructive change, export
or filesystem backup/restore verification must be implemented and documented.

## Verification

Repository contract tests cover empty upgrades, idempotency, newer-schema rejection, injected
failure rollback, foreign keys, scope isolation, and transaction rollback.
