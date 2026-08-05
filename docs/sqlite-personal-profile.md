# SQLite personal storage profile

Issue 5 supplies a local-only SQLite adapter for the personal profile. It uses a fresh connection
per operation, `PRAGMA foreign_keys=ON`, WAL where SQLite supports it, and a 5,000 ms busy timeout.
Every multi-record write uses `BEGIN IMMEDIATE` and rolls back on any exception. Database paths
must remain inside their supplied base directory; parent directories are created mode `0700` and
database files are changed to mode `0600` where the platform supports it.

All timestamps are stored as UTC ISO-8601 strings with an explicit offset. Structured domain
objects are stored as deterministically ordered JSON and reloaded through their strict domain
parsers, preserving nominal IDs and rejecting unknown states.

Migration `0001_initial.sql` is forward-only and transactional. Run `SQLiteCheckpointRepository.migrate()`
before repository use; it is idempotent and rejects databases newer than the application. No
destructive migration exists in Issue 5. A future destructive change requires a backup/restore
strategy and a new migration policy ADR before implementation.

Migration `0013_approved_episodic_event_governance.sql` adds append-only correction/retraction
metadata. The migration itself is additive, forward-only, and transactional: a failed step rolls
back both schema objects and its ledger entry and can be retried. Retraction is a runtime
transaction, not a schema migration; it removes one approved event payload and its direct evidence
links only after inserting the scoped tombstone. Restore the pre-upgrade database backup to return
to schema 12; no down-migration is provided because schema-12 code cannot enforce governance state.

Migration `0014_dbt_supplemental_artifacts.sql` adds immutable minimized catalog and run-results
projections beneath exact manifest snapshots. Composite foreign keys bind every relation, column,
result, and timing row to both its digest-addressed supplemental artifact and an existing manifest
node. Only one projection per manifest/artifact kind is current, while older projections remain
rebuildable structural history. The migration is additive, forward-only, and transactional;
restore the pre-upgrade schema-13 backup for rollback. Runtime writes insert a complete inactive
projection before atomically switching its current pointer, and a failed child insert rolls back
the header and all rows.

Migration `0015_dbt_macro_dependency_edges.sql` transactionally rebuilds only the rebuildable dbt
edge table to admit the distinct `dbt_macro_dependency` type while preserving all existing
`dbt_dependency` rows and endpoint foreign keys. It is forward-only; restore the pre-upgrade
schema-14 backup for rollback, or rebuild the structural projection from its retained source
artifact. A failed migration leaves the schema-14 table and ledger unchanged.

Migration `0016_dbt_source_freshness.sql` additively stores minimized immutable `sources.json` v3
headers and source observations linked by foreign keys to exact manifest snapshots and source
nodes. It is forward-only and transactional; back up the personal-profile database before upgrade
and restore that backup to reverse a committed migration. An interrupted or injected failure
leaves schema 15 and its ledger unchanged.

Scope/principal records are retained with `RESTRICT`. Checkpoints, revisions, evidence, and their
links also use `RESTRICT`, so evidence supporting a durable checkpoint cannot disappear silently.
The repository contract suite exercises the same public contract intended for a later PostgreSQL
adapter. Context packets, FTS, embeddings, raw transcripts, model logs, and deletion/tombstone
work remain deferred.
