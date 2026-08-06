# ADR 0013: Team knowledge keeps canonical text revisioned and pgvector rebuildable

- **Status:** accepted
- **Date:** 2026-08-06
- **Deciders:** Mnemo maintainers
- **Issue:** 21D
- **Supersedes:** none
- **Superseded by:** none

## Context

Issue 21C establishes durable team authority but stores no memory content. Milestone 9 requires
PostgreSQL/pgvector parity before import or a remote service can exist. Knowledge documents are the
smallest complete parity slice because the existing storage-neutral contract already covers
immutable revisions, bounded literal retrieval, semantic projections, secret rejection, and
destructive source deletion.

## Decision

PostgreSQL migration 0002 adds exact project-scoped knowledge sources, immutable revisions,
sections, declared links, minimal tombstones, sync status, and semantic embeddings inside the
`mnemo_team` schema. Every row repeats its workspace, project, owner, and visibility so RLS can
authorize before a join or ranking operation. Composite foreign keys and security-definer trigger
checks prevent a revision, section, link, tombstone, or vector from changing that scope. All seven
tables enable and force RLS through ADR 0010's principal/workspace/operation function.

`PostgreSQLKnowledgeDocumentRepository` implements the existing `KnowledgeDocumentRepository`
without a team-only domain format. Synchronization is one transaction. New revisions are immutable;
the source pointer selects the current revision. Literal ranking reuses Mnemo's deterministic
bounded token contract after the database selects at most 128 already-authorized current documents.
The bounded read preserves exact reference/SQLite ordering rather than introducing a different
PostgreSQL text-search interpretation.

Semantic rows use pgvector 0.8.5's native `vector` type and contain only the current section's
revision identity, digest, model identity, and finite vector. Source text is not duplicated in the
projection. The existing semantic service retains deterministic cosine ranking and exact tie
breaking across adapters; this issue does not add approximate indexing or a second retrieval
contract. Variable valid model dimensions therefore remain supported.

Deleting a source first clears its current pointer and writes a minimal tombstone, then removes its
revision chain newest-first. Foreign-key cascades remove sections, links, and pgvector rows in the
same transaction. A failed batch, unauthorized write, or secret rejection leaves all prior state
and sync status unchanged.

## Alternatives considered

- **Copy the personal SQLite schema literally.** Rejected because SQLite null-workspace rules and
  FTS/blob implementation are not a team authorization or pgvector design.
- **Store vectors as arrays or JSON.** Rejected because Milestone 9 explicitly requires pgvector
  backend parity and the native type validates finite numeric vectors.
- **Add HNSW immediately.** Rejected because current model dimensions vary and the existing bounded
  contract does not issue a database nearest-neighbor query. An index without a measured workload
  would add tuning and recall behavior without improving this parity path.
- **Share one document row across owners or projects.** Rejected because authorization must precede
  retrieval and a content digest is not an ownership identity.
- **Retain deleted revision text for audit.** Rejected because deletion propagation requires text
  and embeddings to disappear; the tombstone keeps only identity, path, digest, scope, and time.

## Consequences

Team knowledge now has a durable PostgreSQL/pgvector repository, but there is still no user-facing
team mode. The adapter is not composed into an authenticated service, and this issue does not add
personal import, source approval, correction arbitration, checkpoint/episodic/structural parity,
remote MCP, backup, quotas, or operations.

The pgvector extension must be installed before migration. Migration verifies exact extension
version 0.8.5. CI builds official tag `v0.8.5` and refuses to proceed unless it resolves to commit
`159b79aaad5983fb7459c1e3df2897fbb2d11788`.

## Security and privacy implications

The runtime role receives only explicit operations on the new tables; it cannot create extensions
or alter schema. RLS is evaluated for current reads, historical reads, row locks, upserts, and
deletes. Trigger functions use a fixed `pg_catalog` search path and validate scope without returning
payload. The adapter evaluates deterministic secret policy before opening a write transaction and
translates constraint/RLS failures to bounded errors.

Knowledge ownership and source approval are not inferred from Markdown. The future authenticated
application layer must decide which authorized actor may create, correct, approve, or delete a
shared source; database `contribute` permission alone is not the final source-governance workflow.

## Token and cost implications

Retrieval remains capped at 128 authorized documents and 24 returned sections. It never replays a
workspace or adds context by default. Vector storage performs no model call; embedding generation
remains an explicitly composed provider concern.

## Dependency and licensing implications

pgvector 0.8.5 at the pinned upstream commit uses the permissive PostgreSQL License recorded in the
dependency register. It is isolated to the optional PostgreSQL migration and
projection. Canonical domain, policy, knowledge, and personal SQLite code do not import it.

## Reversal or migration strategy

Migration 0002 is atomic. Failure while upgrading a valid v1 database leaves its schema and ledger
at v1. Recovery before team release is restore of the pre-migration backup or correction of the
extension installation followed by idempotent migration. Removing pgvector later requires a new
migration that deletes/rebuilds only the vector projection before dropping the extension; canonical
documents remain portable through the storage-neutral contract.

## Verification

- Real PostgreSQL upgrade injection proves v1 survives a failed v2 migration and then reaches v2.
- Current/historical revisions, stable literal ranking, empty-sync status, and exact path reads
  match the existing repository contract.
- Private-project viewers and foreign scopes return no rows and cannot tombstone an owner's source.
- pgvector values round-trip as domain embeddings tied to current immutable revisions.
- Secret and stale batches roll back atomically.
- Tombstoning removes every revision, section, link, and vector while retaining one minimal record.

## References

- `docs/implementation-plan.md`, Milestone 9
- `docs/adr/0006-local-markdown-knowledge-boundary.md`
- `docs/adr/0008-local-semantic-knowledge-projection.md`
- `docs/adr/0010-team-authorization-kernel.md`
- `docs/adr/0012-postgresql-team-control-plane.md`
- pgvector 0.8.5 documentation: <https://github.com/pgvector/pgvector/tree/v0.8.5>
