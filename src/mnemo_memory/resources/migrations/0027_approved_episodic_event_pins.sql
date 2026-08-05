-- Immutable scoped user actions controlling approved-fact retrieval priority.
CREATE TABLE approved_episodic_event_pin_actions (
    action_sequence INTEGER PRIMARY KEY,
    action_id TEXT NOT NULL UNIQUE,
    event_id TEXT NOT NULL,
    pinned INTEGER NOT NULL CHECK (pinned IN (0, 1)),
    source_action_key TEXT NOT NULL CHECK (length(source_action_key) BETWEEN 1 AND 256),
    occurred_at TEXT NOT NULL CHECK (julianday(occurred_at) IS NOT NULL),
    owner_id TEXT NOT NULL REFERENCES principals(owner_id) ON DELETE RESTRICT,
    visibility TEXT NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    workspace_id TEXT NULL REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE RESTRICT,
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    UNIQUE (
        owner_id, visibility, workspace_id, project_id, session_id, task_id,
        source_action_key
    )
);

CREATE TABLE approved_episodic_event_pin_evidence (
    action_id TEXT NOT NULL
        REFERENCES approved_episodic_event_pin_actions(action_id) ON DELETE RESTRICT,
    evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id) ON DELETE RESTRICT,
    PRIMARY KEY (action_id, evidence_id)
);

CREATE INDEX approved_episodic_event_pin_current_idx
ON approved_episodic_event_pin_actions(
    owner_id, visibility, workspace_id, project_id, session_id, task_id,
    event_id, action_sequence DESC
);

CREATE TRIGGER approved_episodic_event_pin_target_scope_match
BEFORE INSERT ON approved_episodic_event_pin_actions
FOR EACH ROW
WHEN NOT EXISTS (
    SELECT 1 FROM approved_episodic_events AS target
    WHERE target.event_id = NEW.event_id
      AND target.owner_id = NEW.owner_id
      AND target.visibility = NEW.visibility
      AND target.workspace_id IS NEW.workspace_id
      AND target.project_id = NEW.project_id
      AND target.session_id = NEW.session_id
      AND target.task_id = NEW.task_id
)
BEGIN
    SELECT RAISE(ABORT, 'approved event pin target scope mismatch');
END;
