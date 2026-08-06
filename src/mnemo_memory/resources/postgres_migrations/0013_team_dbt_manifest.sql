CREATE TABLE mnemo_team.dbt_manifest_snapshots (
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    snapshot_id uuid NOT NULL,
    schema_version text NOT NULL CHECK (
        schema_version LIKE 'https://schemas.getdbt.com/dbt/manifest/%'
    ),
    dbt_version text,
    generated_at timestamptz,
    invocation_id text,
    project_name text,
    content_digest text NOT NULL CHECK (content_digest ~ '^[0-9a-f]{64}$'),
    normalized_graph_digest text NOT NULL CHECK (normalized_graph_digest ~ '^[0-9a-f]{64}$'),
    source_identity text NOT NULL CHECK (
        source_identity <> '' AND left(source_identity, 1) <> '/'
    ),
    ingested_at timestamptz NOT NULL,
    source_state jsonb CHECK (source_state IS NULL OR jsonb_typeof(source_state) = 'object'),
    currentness text NOT NULL CHECK (currentness IN ('unknown', 'current', 'stale')),
    deferred_resource_counts jsonb NOT NULL CHECK (
        jsonb_typeof(deferred_resource_counts) = 'array'
    ),
    node_count integer NOT NULL CHECK (node_count >= 0),
    edge_count integer NOT NULL CHECK (edge_count >= 0),
    is_active boolean NOT NULL DEFAULT false,
    PRIMARY KEY (workspace_id, snapshot_id),
    UNIQUE (workspace_id, project_id, owner_id, visibility, snapshot_id),
    UNIQUE (workspace_id, project_id, owner_id, visibility, content_digest),
    FOREIGN KEY (workspace_id, project_id)
        REFERENCES mnemo_team.projects(workspace_id, project_id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX dbt_manifest_one_active
ON mnemo_team.dbt_manifest_snapshots(workspace_id, project_id, owner_id, visibility)
WHERE is_active;

CREATE TABLE mnemo_team.dbt_manifest_nodes (
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    snapshot_id uuid NOT NULL,
    unique_id text NOT NULL CHECK (unique_id <> ''),
    resource_type text NOT NULL CHECK (resource_type IN (
        'model', 'source', 'test', 'seed', 'snapshot', 'analysis', 'exposure', 'metric',
        'semantic_model', 'macro', 'other'
    )),
    raw_resource_type text NOT NULL CHECK (raw_resource_type <> ''),
    package_name text NOT NULL CHECK (package_name <> ''),
    name text NOT NULL CHECK (name <> ''),
    alias text,
    database_name text,
    schema_name text,
    relation_name text,
    original_file_path text,
    patch_path text,
    enabled boolean NOT NULL,
    checksum text,
    tags jsonb NOT NULL CHECK (jsonb_typeof(tags) = 'array'),
    description text NOT NULL,
    dependency_ids jsonb NOT NULL CHECK (jsonb_typeof(dependency_ids) = 'array'),
    macro_dependency_ids jsonb NOT NULL CHECK (jsonb_typeof(macro_dependency_ids) = 'array'),
    evidence jsonb NOT NULL CHECK (jsonb_typeof(evidence) = 'object'),
    PRIMARY KEY (workspace_id, snapshot_id, unique_id),
    UNIQUE (workspace_id, project_id, owner_id, visibility, snapshot_id, unique_id),
    FOREIGN KEY (workspace_id, project_id, owner_id, visibility, snapshot_id)
        REFERENCES mnemo_team.dbt_manifest_snapshots(
            workspace_id, project_id, owner_id, visibility, snapshot_id
        ) ON DELETE CASCADE
);

CREATE INDEX dbt_manifest_nodes_file
ON mnemo_team.dbt_manifest_nodes(
    workspace_id, project_id, owner_id, visibility, snapshot_id, original_file_path
);

CREATE TABLE mnemo_team.dbt_lineage_edges (
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    snapshot_id uuid NOT NULL,
    parent_id text NOT NULL,
    child_id text NOT NULL,
    edge_type text NOT NULL CHECK (edge_type IN ('dbt_dependency', 'dbt_macro_dependency')),
    evidence jsonb NOT NULL CHECK (jsonb_typeof(evidence) = 'object'),
    artifact_digest text NOT NULL CHECK (artifact_digest ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY (workspace_id, snapshot_id, parent_id, child_id, edge_type),
    FOREIGN KEY (
        workspace_id, project_id, owner_id, visibility, snapshot_id, parent_id
    ) REFERENCES mnemo_team.dbt_manifest_nodes(
        workspace_id, project_id, owner_id, visibility, snapshot_id, unique_id
    ) ON DELETE CASCADE,
    FOREIGN KEY (
        workspace_id, project_id, owner_id, visibility, snapshot_id, child_id
    ) REFERENCES mnemo_team.dbt_manifest_nodes(
        workspace_id, project_id, owner_id, visibility, snapshot_id, unique_id
    ) ON DELETE CASCADE
);

CREATE TABLE mnemo_team.dbt_manifest_activations (
    activation_sequence bigserial NOT NULL,
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    snapshot_id uuid NOT NULL,
    activated_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, activation_sequence),
    FOREIGN KEY (workspace_id, project_id, owner_id, visibility, snapshot_id)
        REFERENCES mnemo_team.dbt_manifest_snapshots(
            workspace_id, project_id, owner_id, visibility, snapshot_id
        ) ON DELETE RESTRICT
);

CREATE TABLE mnemo_team.dbt_manifest_sync_status (
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    last_synced_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, project_id, owner_id, visibility),
    FOREIGN KEY (workspace_id, project_id)
        REFERENCES mnemo_team.projects(workspace_id, project_id) ON DELETE CASCADE
);

CREATE FUNCTION mnemo_team.ensure_dbt_manifest_snapshot_update()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE latest_snapshot_id uuid;
BEGIN
    IF ROW(
        NEW.workspace_id, NEW.project_id, NEW.owner_id, NEW.visibility, NEW.snapshot_id,
        NEW.schema_version, NEW.dbt_version, NEW.generated_at, NEW.invocation_id,
        NEW.project_name, NEW.content_digest, NEW.normalized_graph_digest, NEW.source_identity,
        NEW.ingested_at, NEW.source_state, NEW.currentness, NEW.deferred_resource_counts,
        NEW.node_count, NEW.edge_count
    ) IS DISTINCT FROM ROW(
        OLD.workspace_id, OLD.project_id, OLD.owner_id, OLD.visibility, OLD.snapshot_id,
        OLD.schema_version, OLD.dbt_version, OLD.generated_at, OLD.invocation_id,
        OLD.project_name, OLD.content_digest, OLD.normalized_graph_digest, OLD.source_identity,
        OLD.ingested_at, OLD.source_state, OLD.currentness, OLD.deferred_resource_counts,
        OLD.node_count, OLD.edge_count
    ) THEN
        RAISE EXCEPTION 'dbt manifest snapshot immutable fields cannot change'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.is_active IS DISTINCT FROM OLD.is_active THEN
        SELECT activation.snapshot_id INTO latest_snapshot_id
          FROM mnemo_team.dbt_manifest_activations AS activation
         WHERE activation.workspace_id = NEW.workspace_id
           AND activation.project_id = NEW.project_id
           AND activation.owner_id = NEW.owner_id
           AND activation.visibility = NEW.visibility
         ORDER BY activation.activation_sequence DESC LIMIT 1;
        IF latest_snapshot_id IS NULL
           OR (NEW.is_active AND latest_snapshot_id <> NEW.snapshot_id)
           OR (NOT NEW.is_active AND latest_snapshot_id = NEW.snapshot_id) THEN
            RAISE EXCEPTION 'dbt manifest activation order mismatch' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER dbt_manifest_snapshot_update_guard
BEFORE UPDATE ON mnemo_team.dbt_manifest_snapshots
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_dbt_manifest_snapshot_update();

ALTER TABLE mnemo_team.dbt_manifest_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.dbt_manifest_snapshots FORCE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.dbt_manifest_nodes ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.dbt_manifest_nodes FORCE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.dbt_lineage_edges ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.dbt_lineage_edges FORCE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.dbt_manifest_activations ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.dbt_manifest_activations FORCE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.dbt_manifest_sync_status ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.dbt_manifest_sync_status FORCE ROW LEVEL SECURITY;

CREATE POLICY dbt_manifest_snapshot_access ON mnemo_team.dbt_manifest_snapshots
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));
CREATE POLICY dbt_manifest_node_access ON mnemo_team.dbt_manifest_nodes
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));
CREATE POLICY dbt_lineage_edge_access ON mnemo_team.dbt_lineage_edges
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));
CREATE POLICY dbt_manifest_activation_access ON mnemo_team.dbt_manifest_activations
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));
CREATE POLICY dbt_manifest_sync_access ON mnemo_team.dbt_manifest_sync_status
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));

REVOKE ALL ON ALL TABLES IN SCHEMA mnemo_team FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mnemo_team FROM PUBLIC;

INSERT INTO mnemo_team.schema_migrations(version, applied_at)
VALUES (13, CURRENT_TIMESTAMP);
