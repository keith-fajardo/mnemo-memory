-- Immutable minimized sources.json projections attached to exact manifest snapshots.
CREATE TABLE dbt_source_freshness_artifacts (
    manifest_snapshot_id TEXT NOT NULL
        REFERENCES dbt_manifest_snapshots(snapshot_id) ON DELETE RESTRICT,
    content_digest TEXT NOT NULL CHECK (length(content_digest) = 64),
    schema_version TEXT NOT NULL,
    dbt_version TEXT NULL,
    generated_at TEXT NULL,
    invocation_id TEXT NULL,
    normalized_digest TEXT NOT NULL CHECK (length(normalized_digest) = 64),
    source_identity TEXT NOT NULL CHECK (substr(source_identity, 1, 1) != '/'),
    ingested_at TEXT NOT NULL,
    elapsed_time_seconds REAL NOT NULL CHECK (elapsed_time_seconds >= 0),
    is_active INTEGER NOT NULL CHECK (is_active IN (0, 1)),
    PRIMARY KEY (manifest_snapshot_id, content_digest)
);

CREATE UNIQUE INDEX dbt_source_freshness_one_active_idx
ON dbt_source_freshness_artifacts(manifest_snapshot_id)
WHERE is_active = 1;

CREATE TABLE dbt_source_freshness_results (
    manifest_snapshot_id TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    unique_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pass', 'warn', 'error', 'runtime_error')),
    max_loaded_at TEXT NULL,
    snapshotted_at TEXT NULL,
    age_seconds REAL NULL CHECK (age_seconds IS NULL OR age_seconds >= 0),
    warn_count INTEGER NULL CHECK (warn_count IS NULL OR warn_count >= 0),
    warn_period TEXT NULL CHECK (warn_period IS NULL OR warn_period IN ('minute', 'hour', 'day')),
    error_count INTEGER NULL CHECK (error_count IS NULL OR error_count >= 0),
    error_period TEXT NULL CHECK (
        error_period IS NULL OR error_period IN ('minute', 'hour', 'day')
    ),
    execution_time_seconds REAL NULL CHECK (
        execution_time_seconds IS NULL OR execution_time_seconds >= 0
    ),
    evidence_json TEXT NOT NULL,
    PRIMARY KEY (manifest_snapshot_id, content_digest, unique_id),
    FOREIGN KEY (manifest_snapshot_id, content_digest)
        REFERENCES dbt_source_freshness_artifacts(
            manifest_snapshot_id, content_digest
        ) ON DELETE RESTRICT,
    FOREIGN KEY (manifest_snapshot_id, unique_id)
        REFERENCES dbt_manifest_nodes(snapshot_id, unique_id) ON DELETE RESTRICT,
    CHECK ((warn_count IS NULL) = (warn_period IS NULL)),
    CHECK ((error_count IS NULL) = (error_period IS NULL)),
    CHECK (
        (status = 'runtime_error' AND max_loaded_at IS NULL AND snapshotted_at IS NULL
            AND age_seconds IS NULL AND warn_count IS NULL AND error_count IS NULL
            AND execution_time_seconds IS NULL)
        OR
        (status != 'runtime_error' AND max_loaded_at IS NOT NULL AND snapshotted_at IS NOT NULL
            AND age_seconds IS NOT NULL AND execution_time_seconds IS NOT NULL)
    )
);
