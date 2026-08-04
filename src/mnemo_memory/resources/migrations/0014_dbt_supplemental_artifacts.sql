-- Immutable, minimized catalog/run-results projections attached to exact manifest snapshots.
CREATE TABLE dbt_supplemental_artifacts (
    manifest_snapshot_id TEXT NOT NULL
        REFERENCES dbt_manifest_snapshots(snapshot_id) ON DELETE RESTRICT,
    artifact_kind TEXT NOT NULL CHECK (artifact_kind IN ('catalog', 'run_results')),
    content_digest TEXT NOT NULL CHECK (length(content_digest) = 64),
    schema_version TEXT NOT NULL,
    dbt_version TEXT NULL,
    generated_at TEXT NULL,
    invocation_id TEXT NULL,
    normalized_digest TEXT NOT NULL CHECK (length(normalized_digest) = 64),
    source_identity TEXT NOT NULL CHECK (substr(source_identity, 1, 1) != '/'),
    ingested_at TEXT NOT NULL,
    catalog_error_count INTEGER NULL CHECK (
        catalog_error_count IS NULL OR catalog_error_count >= 0
    ),
    elapsed_time_seconds REAL NULL CHECK (
        elapsed_time_seconds IS NULL OR elapsed_time_seconds >= 0
    ),
    command_name TEXT NULL,
    is_active INTEGER NOT NULL CHECK (is_active IN (0, 1)),
    PRIMARY KEY (manifest_snapshot_id, artifact_kind, content_digest),
    CHECK (
        (artifact_kind = 'catalog' AND catalog_error_count IS NOT NULL
            AND elapsed_time_seconds IS NULL AND command_name IS NULL)
        OR
        (artifact_kind = 'run_results' AND catalog_error_count IS NULL
            AND elapsed_time_seconds IS NOT NULL)
    )
);

CREATE UNIQUE INDEX dbt_supplemental_one_active_kind_idx
ON dbt_supplemental_artifacts(manifest_snapshot_id, artifact_kind)
WHERE is_active = 1;

CREATE TABLE dbt_catalog_relations (
    manifest_snapshot_id TEXT NOT NULL,
    artifact_kind TEXT NOT NULL CHECK (artifact_kind = 'catalog'),
    content_digest TEXT NOT NULL,
    unique_id TEXT NOT NULL,
    collection_kind TEXT NOT NULL CHECK (collection_kind IN ('node', 'source')),
    relation_type TEXT NOT NULL,
    database_name TEXT NULL,
    schema_name TEXT NOT NULL,
    relation_name TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    PRIMARY KEY (manifest_snapshot_id, content_digest, unique_id),
    FOREIGN KEY (manifest_snapshot_id, artifact_kind, content_digest)
        REFERENCES dbt_supplemental_artifacts(
            manifest_snapshot_id, artifact_kind, content_digest
        ) ON DELETE RESTRICT,
    FOREIGN KEY (manifest_snapshot_id, unique_id)
        REFERENCES dbt_manifest_nodes(snapshot_id, unique_id) ON DELETE RESTRICT
);

CREATE TABLE dbt_catalog_columns (
    manifest_snapshot_id TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    unique_id TEXT NOT NULL,
    column_index INTEGER NOT NULL CHECK (column_index >= 0),
    column_name TEXT NOT NULL,
    data_type TEXT NOT NULL,
    PRIMARY KEY (manifest_snapshot_id, content_digest, unique_id, column_index),
    UNIQUE (manifest_snapshot_id, content_digest, unique_id, column_name),
    FOREIGN KEY (manifest_snapshot_id, content_digest, unique_id)
        REFERENCES dbt_catalog_relations(
            manifest_snapshot_id, content_digest, unique_id
        ) ON DELETE RESTRICT
);

CREATE TABLE dbt_run_results (
    manifest_snapshot_id TEXT NOT NULL,
    artifact_kind TEXT NOT NULL CHECK (artifact_kind = 'run_results'),
    content_digest TEXT NOT NULL,
    unique_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'success', 'error', 'skipped', 'partial_success', 'no_op',
            'pass', 'fail', 'warn', 'runtime_error'
        )
    ),
    execution_time_seconds REAL NOT NULL CHECK (execution_time_seconds >= 0),
    failures INTEGER NULL CHECK (failures IS NULL OR failures >= 0),
    evidence_json TEXT NOT NULL,
    PRIMARY KEY (manifest_snapshot_id, content_digest, unique_id),
    FOREIGN KEY (manifest_snapshot_id, artifact_kind, content_digest)
        REFERENCES dbt_supplemental_artifacts(
            manifest_snapshot_id, artifact_kind, content_digest
        ) ON DELETE RESTRICT,
    FOREIGN KEY (manifest_snapshot_id, unique_id)
        REFERENCES dbt_manifest_nodes(snapshot_id, unique_id) ON DELETE RESTRICT
);

CREATE TABLE dbt_run_result_timings (
    manifest_snapshot_id TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    unique_id TEXT NOT NULL,
    timing_name TEXT NOT NULL,
    started_at TEXT NULL,
    completed_at TEXT NULL,
    PRIMARY KEY (manifest_snapshot_id, content_digest, unique_id, timing_name),
    FOREIGN KEY (manifest_snapshot_id, content_digest, unique_id)
        REFERENCES dbt_run_results(
            manifest_snapshot_id, content_digest, unique_id
        ) ON DELETE RESTRICT
);
