CREATE TABLE checkpoint_deletions (
    deletion_id TEXT PRIMARY KEY,
    checkpoint_id TEXT NOT NULL UNIQUE,
    actor TEXT NOT NULL CHECK (actor = 'user'),
    source_action_key TEXT NOT NULL CHECK (
        length(source_action_key) BETWEEN 1 AND 256
    ),
    deleted_at TEXT NOT NULL,
    owner_id TEXT NOT NULL REFERENCES principals(owner_id) ON DELETE RESTRICT,
    visibility TEXT NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    workspace_id TEXT NULL REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE RESTRICT,
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    UNIQUE (
        owner_id, visibility, workspace_id, project_id, session_id, task_id, source_action_key
    )
);

CREATE INDEX checkpoint_deletion_scope_idx ON checkpoint_deletions(
    owner_id, visibility, workspace_id, project_id, session_id, task_id, deleted_at
);

CREATE TRIGGER checkpoint_deletion_prevents_resurrection
BEFORE INSERT ON checkpoint_aggregates
FOR EACH ROW
WHEN EXISTS (
    SELECT 1 FROM checkpoint_deletions
    WHERE checkpoint_id = NEW.checkpoint_id
      AND owner_id = NEW.owner_id
      AND visibility = NEW.visibility
      AND workspace_id IS NEW.workspace_id
      AND project_id = NEW.project_id
      AND session_id = NEW.session_id
      AND task_id = NEW.task_id
)
BEGIN
    SELECT RAISE(ABORT, 'checkpoint deletion prevents resurrection');
END;

CREATE TRIGGER checkpoint_aggregate_delete_requires_tombstone
BEFORE DELETE ON checkpoint_aggregates
FOR EACH ROW
WHEN NOT EXISTS (
    SELECT 1 FROM checkpoint_deletions
    WHERE checkpoint_id = OLD.checkpoint_id
      AND owner_id = OLD.owner_id
      AND visibility = OLD.visibility
      AND workspace_id IS OLD.workspace_id
      AND project_id = OLD.project_id
      AND session_id = OLD.session_id
      AND task_id = OLD.task_id
)
BEGIN
    SELECT RAISE(ABORT, 'checkpoint payload deletion requires tombstone');
END;

CREATE TRIGGER checkpoint_revision_delete_requires_tombstone
BEFORE DELETE ON checkpoint_revision_records
FOR EACH ROW
WHEN NOT EXISTS (
    SELECT 1 FROM checkpoint_deletions AS deletion
    JOIN checkpoint_aggregates AS aggregate
      ON aggregate.checkpoint_id = OLD.checkpoint_id
    WHERE deletion.checkpoint_id = OLD.checkpoint_id
      AND deletion.owner_id = aggregate.owner_id
      AND deletion.visibility = aggregate.visibility
      AND deletion.workspace_id IS aggregate.workspace_id
      AND deletion.project_id = aggregate.project_id
      AND deletion.session_id = aggregate.session_id
      AND deletion.task_id = aggregate.task_id
)
BEGIN
    SELECT RAISE(ABORT, 'checkpoint payload deletion requires tombstone');
END;

CREATE TRIGGER checkpoint_event_delete_requires_tombstone
BEFORE DELETE ON checkpoint_lifecycle_events
FOR EACH ROW
WHEN NOT EXISTS (
    SELECT 1 FROM checkpoint_deletions
    WHERE checkpoint_id = OLD.checkpoint_id
      AND owner_id = OLD.owner_id
      AND visibility = OLD.visibility
      AND workspace_id IS OLD.workspace_id
      AND project_id = OLD.project_id
      AND session_id = OLD.session_id
      AND task_id = OLD.task_id
)
BEGIN
    SELECT RAISE(ABORT, 'checkpoint payload deletion requires tombstone');
END;

CREATE TRIGGER checkpoint_observation_delete_requires_tombstone
BEFORE DELETE ON checkpoint_source_observations
FOR EACH ROW
WHEN NOT EXISTS (
    SELECT 1 FROM checkpoint_deletions
    WHERE checkpoint_id = OLD.checkpoint_id
      AND owner_id = OLD.owner_id
      AND visibility = OLD.visibility
      AND workspace_id IS OLD.workspace_id
      AND project_id = OLD.project_id
      AND session_id = OLD.session_id
      AND task_id = OLD.task_id
)
BEGIN
    SELECT RAISE(ABORT, 'checkpoint payload deletion requires tombstone');
END;
