-- An immutable, scoped co-observation: a source snapshot parsed immediately after one exact
-- checkpoint revision persisted. It is not an inferred change rationale.
CREATE TABLE checkpoint_source_observations (
    checkpoint_revision_id TEXT PRIMARY KEY
        REFERENCES checkpoint_revision_records(checkpoint_revision_id) ON DELETE RESTRICT,
    checkpoint_id TEXT NOT NULL REFERENCES checkpoint_aggregates(checkpoint_id) ON DELETE RESTRICT,
    source_snapshot_id TEXT NOT NULL
        REFERENCES source_structure_snapshots(snapshot_id) ON DELETE RESTRICT,
    owner_id TEXT NOT NULL REFERENCES principals(owner_id) ON DELETE RESTRICT,
    visibility TEXT NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    workspace_id TEXT NULL REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE RESTRICT,
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    observed_at TEXT NOT NULL,
    UNIQUE (checkpoint_revision_id, source_snapshot_id)
);

CREATE INDEX checkpoint_source_observation_scope_idx
    ON checkpoint_source_observations(
        owner_id, workspace_id, project_id, session_id, task_id, checkpoint_id
    );

CREATE TRIGGER checkpoint_source_observation_checkpoint_scope_match
BEFORE INSERT ON checkpoint_source_observations
FOR EACH ROW
WHEN NOT EXISTS (
    SELECT 1
    FROM checkpoint_revision_records AS revision
    JOIN checkpoint_aggregates AS aggregate ON aggregate.checkpoint_id = revision.checkpoint_id
    WHERE revision.checkpoint_revision_id = NEW.checkpoint_revision_id
      AND revision.checkpoint_id = NEW.checkpoint_id
      AND aggregate.owner_id = NEW.owner_id
      AND aggregate.visibility = NEW.visibility
      AND aggregate.workspace_id IS NEW.workspace_id
      AND aggregate.project_id = NEW.project_id
      AND aggregate.session_id = NEW.session_id
      AND aggregate.task_id = NEW.task_id
)
BEGIN
    SELECT RAISE(ABORT, 'checkpoint source observation checkpoint scope mismatch');
END;

CREATE TRIGGER checkpoint_source_observation_snapshot_scope_match
BEFORE INSERT ON checkpoint_source_observations
FOR EACH ROW
WHEN NOT EXISTS (
    SELECT 1
    FROM source_structure_snapshots
    WHERE snapshot_id = NEW.source_snapshot_id
      AND owner_id = NEW.owner_id
      AND workspace_id IS NEW.workspace_id
      AND project_id = NEW.project_id
)
BEGIN
    SELECT RAISE(ABORT, 'checkpoint source observation snapshot scope mismatch');
END;
