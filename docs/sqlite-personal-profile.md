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

Scope/principal records are retained with `RESTRICT`. Checkpoints, revisions, evidence, and their
links also use `RESTRICT`, so evidence supporting a durable checkpoint cannot disappear silently.
The repository contract suite exercises the same public contract intended for a later PostgreSQL
adapter. Context packets, FTS, embeddings, raw transcripts, model logs, and deletion/tombstone
work remain deferred.
