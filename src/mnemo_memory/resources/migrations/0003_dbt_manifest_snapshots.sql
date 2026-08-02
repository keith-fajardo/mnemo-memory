CREATE TABLE dbt_manifest_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES principals(owner_id) ON DELETE RESTRICT,
    visibility TEXT NOT NULL CHECK (visibility IN ('owner','workspace','project')),
    workspace_id TEXT NULL REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    scope_level TEXT NOT NULL CHECK (scope_level = 'project'),
    manifest_schema_version TEXT NOT NULL,
    dbt_version TEXT NULL,
    project_name TEXT NULL,
    generated_at TEXT NULL,
    ingested_at TEXT NOT NULL,
    invocation_id TEXT NULL,
    content_digest TEXT NOT NULL CHECK (length(content_digest) = 64),
    normalized_graph_digest TEXT NOT NULL CHECK (length(normalized_graph_digest) = 64),
    source_state_json TEXT NULL,
    currentness TEXT NOT NULL CHECK (currentness IN ('unknown','current','stale')),
    source_identity TEXT NOT NULL CHECK (substr(source_identity, 1, 1) != '/'),
    node_count INTEGER NOT NULL CHECK (node_count >= 0),
    edge_count INTEGER NOT NULL CHECK (edge_count >= 0),
    is_active INTEGER NOT NULL CHECK (is_active IN (0, 1)),
    UNIQUE (owner_id, workspace_id, project_id, content_digest)
);

CREATE TABLE dbt_manifest_nodes (
    snapshot_id TEXT NOT NULL REFERENCES dbt_manifest_snapshots(snapshot_id) ON DELETE RESTRICT,
    unique_id TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    package_name TEXT NOT NULL,
    name TEXT NOT NULL,
    alias TEXT NULL,
    database_name TEXT NULL,
    schema_name TEXT NULL,
    relation_name TEXT NULL,
    original_file_path TEXT NULL,
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    checksum TEXT NULL,
    tags_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, unique_id)
);

CREATE TABLE dbt_manifest_edges (
    snapshot_id TEXT NOT NULL REFERENCES dbt_manifest_snapshots(snapshot_id) ON DELETE RESTRICT,
    parent_unique_id TEXT NOT NULL,
    child_unique_id TEXT NOT NULL,
    edge_type TEXT NOT NULL CHECK (edge_type = 'dbt_dependency'),
    artifact_digest TEXT NOT NULL CHECK (length(artifact_digest) = 64),
    evidence_json TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, parent_unique_id, child_unique_id, edge_type),
    FOREIGN KEY (snapshot_id, parent_unique_id)
        REFERENCES dbt_manifest_nodes(snapshot_id, unique_id) ON DELETE RESTRICT,
    FOREIGN KEY (snapshot_id, child_unique_id)
        REFERENCES dbt_manifest_nodes(snapshot_id, unique_id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX dbt_manifest_one_active_project_idx
    ON dbt_manifest_snapshots(owner_id, project_id) WHERE is_active = 1;
CREATE INDEX dbt_manifest_active_scope_idx
    ON dbt_manifest_snapshots(owner_id, workspace_id, project_id, is_active, ingested_at DESC);
CREATE INDEX dbt_manifest_snapshot_order_idx
    ON dbt_manifest_snapshots(owner_id, project_id, ingested_at DESC, snapshot_id ASC);
CREATE INDEX dbt_manifest_edge_upstream_idx
    ON dbt_manifest_edges(snapshot_id, child_unique_id, parent_unique_id);
CREATE INDEX dbt_manifest_edge_downstream_idx
    ON dbt_manifest_edges(snapshot_id, parent_unique_id, child_unique_id);
