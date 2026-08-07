# SQLite personal storage profile

Issue 5 supplies a local-only SQLite adapter for the personal profile. It uses a fresh connection
per operation, `PRAGMA foreign_keys=ON`, WAL where SQLite supports it, and a 5,000 ms busy timeout.
Every multi-record write uses `BEGIN IMMEDIATE` and rolls back on any exception. Database paths
must remain inside their supplied base directory; parent directories are created mode `0700` and
database files are changed to mode `0600` where the platform supports it.

All timestamps are stored as UTC ISO-8601 strings with an explicit offset. Structured domain
objects are stored as deterministically ordered JSON and reloaded through their strict domain
parsers, preserving nominal IDs and rejecting unknown states.

## Verified backups

`mnemo backup` opens the configured live database read-only and uses SQLite's backup API so
committed WAL state is represented coherently without copying a possibly changing file directly.
The candidate copy is created with mode 0600 under a mode-0700 `backups` directory, then checked
with `PRAGMA integrity_check`, `PRAGMA foreign_key_check`, and the `schema_migrations` maximum. Only
after validation is it fsynced and atomically renamed to a filename containing the schema version,
UTC creation timestamp, and full SHA-256 digest. Existing destinations and symlinked backup
directories fail closed; partial candidates are removed without touching the live database.

This is a complete sensitive database copy, not a redacted export. It is retained until the user
removes it. The bounded command does not perform restore or upgrade; the upgrade workflow must call
this service successfully before changing an installed version.

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

Migration `0027_approved_episodic_event_pins.sql` additively stores immutable, evidence-linked pin
and unpin actions for active approved facts. The current state is the latest action for one exact
scoped event; the event row is deliberately not a foreign-key target because a later retraction
must erase that payload while retaining the bounded action audit. An insertion trigger still
requires an exact live scoped target at action time. The migration is forward-only and
transactional: an injected failure leaves schema 26 and its ledger unchanged. Restore the
pre-upgrade schema-26 backup to reverse a committed migration 0027.

Migration `0028_project_index_sync_status.sql` additively stores only the last successful sync
timestamp for each knowledge, source-structure, and dbt index in one exact project scope. Existing
indexes are not backfilled because an old activation or document revision cannot prove the time of
the last complete sync. Runtime updates occur in the same transaction as the successful index
write, including empty and idempotent syncs, so failed writes cannot advance status. The migration
is forward-only and transactional; restore the verified pre-upgrade schema-27 backup to reverse a
committed migration 0028.

Migration `0029_checkpoint_expiry.sql` rebuilds only the checkpoint lifecycle-event table so its
closed event-kind constraint admits `checkpoint_expired`. It preserves every existing event
identity and sequence, then recreates the scoped indexes and revision/scope guards. The migration
is forward-only and transactional; an injected failure leaves the prior schema unchanged. Restore
the verified pre-upgrade schema-28 backup to reverse a committed migration 0029.

Migration `0030_checkpoint_deletions.sql` additively stores one deterministic payload-free
anti-resurrection tombstone for an explicitly deleted checkpoint. Scoped triggers require the
tombstone before aggregate, revision, lifecycle-event, or source-observation payload can be
deleted and reject later aggregate recreation. Runtime deletion removes all canonical checkpoint
payload, related lifecycle jobs, legacy checkpoint rows, and newly orphaned evidence in one
transaction. The migration is forward-only and transactional; an injected failure leaves the
prior schema unchanged. Restore the verified pre-upgrade schema-29 backup to reverse a committed
migration 0030, recognizing that payload erased after migration cannot be reconstructed from the
live database.

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

Migration `0017_dbt_manifest_activations.sql` additively records an append-only scoped ledger of
manifest snapshot activations so changed-state retrieval never infers chronology from UUIDs or
timestamps. It seeds only the currently active snapshot on upgrade because no trustworthy earlier
activation order exists. The migration is forward-only and transactional; restore a pre-upgrade
schema-16 backup to reverse a committed migration. An interrupted or injected failure leaves the
schema and migration ledger at version 16.

Scope/principal records are retained with `RESTRICT`. Checkpoints, revisions, evidence, and their
links also use `RESTRICT`, so evidence supporting a durable checkpoint cannot disappear silently.
The repository contract suite exercises the same public contract intended for a later PostgreSQL
adapter. Context packets, FTS, embeddings, raw transcripts, model logs, and deletion/tombstone
work remain deferred.
