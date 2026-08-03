-- Append-only lifecycle facts; checkpoint revisions remain the single content/evidence authority.
CREATE TABLE checkpoint_lifecycle_events (
    event_sequence INTEGER PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE,
    event_kind TEXT NOT NULL CHECK (event_kind IN (
        'checkpoint_created', 'checkpoint_revised', 'checkpoint_completed',
        'checkpoint_abandoned', 'checkpoint_lesson_recorded'
    )),
    checkpoint_id TEXT NOT NULL REFERENCES checkpoint_aggregates(checkpoint_id) ON DELETE RESTRICT,
    checkpoint_revision_id TEXT NOT NULL REFERENCES checkpoint_revision_records(checkpoint_revision_id) ON DELETE RESTRICT,
    revision_number INTEGER NOT NULL CHECK (revision_number >= 1),
    owner_id TEXT NOT NULL REFERENCES principals(owner_id) ON DELETE RESTRICT,
    visibility TEXT NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    workspace_id TEXT NULL REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE RESTRICT,
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    occurred_at TEXT NOT NULL,
    UNIQUE (checkpoint_revision_id, event_kind)
);

CREATE INDEX checkpoint_lifecycle_events_scope_order_idx
    ON checkpoint_lifecycle_events(
        owner_id, visibility, workspace_id, project_id, session_id, task_id, event_sequence DESC
    );
CREATE INDEX checkpoint_lifecycle_events_scope_checkpoint_idx
    ON checkpoint_lifecycle_events(
        owner_id, visibility, workspace_id, project_id, session_id, task_id, checkpoint_id,
        event_sequence DESC
    );

CREATE TRIGGER checkpoint_lifecycle_event_revision_matches
BEFORE INSERT ON checkpoint_lifecycle_events
FOR EACH ROW
WHEN NOT EXISTS (
    SELECT 1 FROM checkpoint_revision_records
    WHERE checkpoint_revision_id = NEW.checkpoint_revision_id
      AND checkpoint_id = NEW.checkpoint_id
      AND revision_number = NEW.revision_number
      AND created_at = NEW.occurred_at
)
BEGIN
    SELECT RAISE(ABORT, 'checkpoint lifecycle event revision mismatch');
END;

CREATE TRIGGER checkpoint_lifecycle_event_scope_matches
BEFORE INSERT ON checkpoint_lifecycle_events
FOR EACH ROW
WHEN NOT EXISTS (
    SELECT 1 FROM checkpoint_aggregates
    WHERE checkpoint_id = NEW.checkpoint_id
      AND owner_id = NEW.owner_id
      AND visibility = NEW.visibility
      AND workspace_id IS NEW.workspace_id
      AND project_id = NEW.project_id
      AND session_id = NEW.session_id
      AND task_id = NEW.task_id
)
BEGIN
    SELECT RAISE(ABORT, 'checkpoint lifecycle event scope mismatch');
END;
