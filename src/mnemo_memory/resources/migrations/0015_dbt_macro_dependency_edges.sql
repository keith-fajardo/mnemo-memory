ALTER TABLE dbt_manifest_edges RENAME TO dbt_manifest_edges_v14;

CREATE TABLE dbt_manifest_edges (
    snapshot_id TEXT NOT NULL REFERENCES dbt_manifest_snapshots(snapshot_id) ON DELETE RESTRICT,
    parent_unique_id TEXT NOT NULL,
    child_unique_id TEXT NOT NULL,
    edge_type TEXT NOT NULL CHECK (
        edge_type IN ('dbt_dependency', 'dbt_macro_dependency')
    ),
    artifact_digest TEXT NOT NULL CHECK (length(artifact_digest) = 64),
    evidence_json TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, parent_unique_id, child_unique_id, edge_type),
    FOREIGN KEY (snapshot_id, parent_unique_id)
        REFERENCES dbt_manifest_nodes(snapshot_id, unique_id) ON DELETE RESTRICT,
    FOREIGN KEY (snapshot_id, child_unique_id)
        REFERENCES dbt_manifest_nodes(snapshot_id, unique_id) ON DELETE RESTRICT
);

INSERT INTO dbt_manifest_edges
SELECT snapshot_id, parent_unique_id, child_unique_id, edge_type, artifact_digest, evidence_json
FROM dbt_manifest_edges_v14;

DROP TABLE dbt_manifest_edges_v14;

CREATE INDEX dbt_manifest_edge_upstream_idx
    ON dbt_manifest_edges(snapshot_id, child_unique_id, parent_unique_id);
CREATE INDEX dbt_manifest_edge_downstream_idx
    ON dbt_manifest_edges(snapshot_id, parent_unique_id, child_unique_id);
