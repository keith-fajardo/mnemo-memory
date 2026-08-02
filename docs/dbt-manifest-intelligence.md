# dbt manifest intelligence (Issue 12A)

Mnemo parses a supported dbt `manifest.json` as authoritative structural evidence. The initial
adapter accepts only v12, identified by the exact `metadata.dbt_schema_version` URL
`https://schemas.getdbt.com/dbt/manifest/v12.json`; it never infers schema from `dbt_version`.
The official registry is [schemas.getdbt.com](https://schemas.getdbt.com/) and the reviewed v12
schema is [manifest v12](https://schemas.getdbt.com/dbt/manifest/v12.json). This policy was
reviewed on 2026-08-02.

The parser consumes artifact metadata; `nodes` and `sources`; resource identity, relation/file
metadata, enabled state, checksum, tags, and description; and `depends_on.nodes`. It builds edges
only from that explicit dependency list, then validates `parent_map` and `child_map` when supplied.
Descriptions, tags, and meta content remain untrusted descriptive data. Other collections, such
as exposures and metrics, are recorded as deferred counts, not interpreted as lineage.

Parsing is local and offline: no dbt executable, Jinja, macros, SQL parsing, warehouse connection,
network call, or LLM is used. Personal-mode byte, node, edge, dependency, string, and traversal
limits reject oversized input. Unsupported versions and inconsistent or cyclic artifacts fail with
typed errors.

Each parsed graph has a raw-manifest SHA-256 and a deterministic normalized-graph SHA-256. Optional
Git commit, working-tree fingerprint, dirty state, and dbt target may be supplied by an outer
integration later. Without such matching state, structural currentness is `unknown`.

The graph supports deterministic, bounded direct and transitive upstream/downstream traversal.
Results sort by depth and dbt unique ID, include node and edge evidence, and report structured
truncation rather than recursing indefinitely. Disabled nodes are included by default so the
artifact remains faithful; callers can explicitly exclude them.

SQLite storage, incremental ingestion, MCP/context-packet integration, catalog/run-results,
general code graphs, and all model behavior remain out of scope for 12A.
