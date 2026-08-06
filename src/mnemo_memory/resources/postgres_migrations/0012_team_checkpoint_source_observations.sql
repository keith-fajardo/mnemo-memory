CREATE TABLE mnemo_team.checkpoint_source_observations (
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    session_id uuid NOT NULL,
    task_id uuid NOT NULL,
    checkpoint_id uuid NOT NULL,
    revision_id uuid NOT NULL,
    source_snapshot_id uuid NOT NULL,
    observed_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, revision_id),
    FOREIGN KEY (workspace_id, revision_id)
        REFERENCES mnemo_team.checkpoint_revisions(workspace_id, revision_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (workspace_id, project_id, owner_id, visibility, source_snapshot_id)
        REFERENCES mnemo_team.source_structure_snapshots(
            workspace_id, project_id, owner_id, visibility, snapshot_id
        ) ON DELETE RESTRICT
);

CREATE FUNCTION mnemo_team.ensure_checkpoint_source_observation_scope()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM mnemo_team.checkpoint_revisions AS revision
         WHERE revision.workspace_id = NEW.workspace_id
           AND revision.project_id = NEW.project_id
           AND revision.owner_id = NEW.owner_id
           AND revision.visibility = NEW.visibility
           AND revision.session_id = NEW.session_id
           AND revision.task_id = NEW.task_id
           AND revision.checkpoint_id = NEW.checkpoint_id
           AND revision.revision_id = NEW.revision_id
    ) THEN
        RAISE EXCEPTION 'checkpoint source observation revision mismatch'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER checkpoint_source_observation_scope_guard
BEFORE INSERT ON mnemo_team.checkpoint_source_observations
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_checkpoint_source_observation_scope();

ALTER TABLE mnemo_team.checkpoint_source_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.checkpoint_source_observations FORCE ROW LEVEL SECURITY;

CREATE POLICY checkpoint_source_observation_access
ON mnemo_team.checkpoint_source_observations
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));

REVOKE ALL ON ALL TABLES IN SCHEMA mnemo_team FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mnemo_team FROM PUBLIC;

INSERT INTO mnemo_team.schema_migrations(version, applied_at)
VALUES (12, CURRENT_TIMESTAMP);
