# Immutable dbt manifest snapshots (Issue 12B.1)

Each accepted manifest graph is stored as an immutable, project-scoped snapshot. Its metadata
includes schema and dbt versions, generated and ingestion times, invocation identity when present,
raw-manifest and normalized-graph digests, optional source-state fingerprint, currentness, and
counts. A unique digest within the same project scope is idempotent; a changed digest creates a new
snapshot and atomically selects it as active. An optional expected active snapshot prevents stale
ingestion from replacing a newer snapshot.

The projection stores selected node identity/relation/file metadata, enabled state, checksum, tags,
and evidence plus dependency edges and edge evidence. It deliberately excludes raw manifests,
compiled or raw SQL, macro bodies, unbounded meta dictionaries, environment variables, warehouse
credentials, absolute source paths, and node descriptions. Prior snapshots remain for provenance.

All reads require an explicit project scope. Cross-scope snapshot and node requests use the same
not-found outcomes as unknown identities. SQLite constraints enforce snapshot/node/edge uniqueness,
same-snapshot edge endpoints, foreign keys, digest idempotency, and one active snapshot per project.
The migration is forward-only: back up the local profile before upgrading; failed migration or
ingestion transactions roll back without replacing the prior active snapshot.

Private manifests may be used read-only with a temporary isolated database for local compatibility
validation. They must not be copied, committed, logged, documented, or used to derive fixtures.

The transitive lineage query service is deferred to 12B.2. Context-packet/MCP integration is
deferred to 12C.
