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

## Ingestion and lineage queries (Issue 12B.2)

The application service parses a caller-supplied manifest offline, validates it using the supported
schema adapter, and stores/activates it through the immutable snapshot repository. Identical
content digests are idempotent; changed artifacts create new snapshots, and an expected active
snapshot prevents a stale ingestion from replacing newer structural evidence.

Queries require explicit scope and a dbt `unique_id`. They use the active snapshot by default or
an explicitly scoped historical snapshot when requested. Direct queries return depth-one adjacency;
transitive queries use iterative, batched frontiers, shortest depth, deterministic depth/unique-ID
ordering, and bounded node/edge/depth limits. Every returned node and edge carries manifest
evidence. A limit produces a structured truncation result rather than an unbounded traversal.

An active snapshot is not automatically current. The service labels a result `current` only when an
exact manifest digest or safely comparable source fingerprint matches; it labels a comparable
mismatch `stale`, and otherwise returns `unknown`. Timestamps and dbt versions alone are never
currentness evidence. Cross-scope requests use the same not-found outcome as unknown identities.

The local runtime composition includes the offline parser, snapshot repository, and application
service over the same canonical Mnemo SQLite profile. MCP/context-packet integration remains
deferred to 12C.
