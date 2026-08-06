CREATE TABLE mnemo_team.dbt_supplemental_artifacts (
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    snapshot_id uuid NOT NULL,
    artifact_kind text NOT NULL CHECK (
        artifact_kind IN ('catalog', 'run_results', 'source_freshness')
    ),
    content_digest text NOT NULL CHECK (content_digest ~ '^[0-9a-f]{64}$'),
    schema_version text NOT NULL CHECK (schema_version LIKE 'https://schemas.getdbt.com/dbt/%'),
    dbt_version text,
    generated_at timestamptz,
    invocation_id text,
    normalized_digest text NOT NULL CHECK (normalized_digest ~ '^[0-9a-f]{64}$'),
    source_identity text NOT NULL CHECK (
        source_identity <> '' AND left(source_identity, 1) <> '/'
    ),
    ingested_at timestamptz NOT NULL,
    projection jsonb NOT NULL CHECK (jsonb_typeof(projection) = 'object'),
    is_active boolean NOT NULL DEFAULT false,
    PRIMARY KEY (workspace_id, snapshot_id, artifact_kind, content_digest),
    UNIQUE (
        workspace_id, project_id, owner_id, visibility, snapshot_id,
        artifact_kind, content_digest
    ),
    FOREIGN KEY (workspace_id, project_id, owner_id, visibility, snapshot_id)
        REFERENCES mnemo_team.dbt_manifest_snapshots(
            workspace_id, project_id, owner_id, visibility, snapshot_id
        ) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX dbt_supplemental_one_active
ON mnemo_team.dbt_supplemental_artifacts(workspace_id, snapshot_id, artifact_kind)
WHERE is_active;

CREATE TABLE mnemo_team.dbt_supplemental_resources (
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    snapshot_id uuid NOT NULL,
    artifact_kind text NOT NULL CHECK (
        artifact_kind IN ('catalog', 'run_results', 'source_freshness')
    ),
    content_digest text NOT NULL CHECK (content_digest ~ '^[0-9a-f]{64}$'),
    unique_id text NOT NULL CHECK (unique_id <> ''),
    PRIMARY KEY (workspace_id, snapshot_id, artifact_kind, content_digest, unique_id),
    FOREIGN KEY (
        workspace_id, project_id, owner_id, visibility, snapshot_id,
        artifact_kind, content_digest
    ) REFERENCES mnemo_team.dbt_supplemental_artifacts(
        workspace_id, project_id, owner_id, visibility, snapshot_id,
        artifact_kind, content_digest
    ) ON DELETE RESTRICT,
    FOREIGN KEY (
        workspace_id, project_id, owner_id, visibility, snapshot_id, unique_id
    ) REFERENCES mnemo_team.dbt_manifest_nodes(
        workspace_id, project_id, owner_id, visibility, snapshot_id, unique_id
    ) ON DELETE RESTRICT
);

CREATE FUNCTION mnemo_team.ensure_dbt_supplemental_update()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF ROW(
        NEW.workspace_id, NEW.project_id, NEW.owner_id, NEW.visibility, NEW.snapshot_id,
        NEW.artifact_kind, NEW.content_digest, NEW.schema_version, NEW.dbt_version,
        NEW.generated_at, NEW.invocation_id, NEW.normalized_digest, NEW.source_identity,
        NEW.ingested_at, NEW.projection
    ) IS DISTINCT FROM ROW(
        OLD.workspace_id, OLD.project_id, OLD.owner_id, OLD.visibility, OLD.snapshot_id,
        OLD.artifact_kind, OLD.content_digest, OLD.schema_version, OLD.dbt_version,
        OLD.generated_at, OLD.invocation_id, OLD.normalized_digest, OLD.source_identity,
        OLD.ingested_at, OLD.projection
    ) THEN
        RAISE EXCEPTION 'dbt supplemental immutable fields cannot change' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER dbt_supplemental_update_guard
BEFORE UPDATE ON mnemo_team.dbt_supplemental_artifacts
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_dbt_supplemental_update();

ALTER TABLE mnemo_team.dbt_supplemental_artifacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.dbt_supplemental_artifacts FORCE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.dbt_supplemental_resources ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.dbt_supplemental_resources FORCE ROW LEVEL SECURITY;

CREATE POLICY dbt_supplemental_artifact_access ON mnemo_team.dbt_supplemental_artifacts
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));
CREATE POLICY dbt_supplemental_resource_access ON mnemo_team.dbt_supplemental_resources
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));

REVOKE ALL ON ALL TABLES IN SCHEMA mnemo_team FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mnemo_team FROM PUBLIC;

INSERT INTO mnemo_team.schema_migrations(version, applied_at)
VALUES (14, CURRENT_TIMESTAMP);
