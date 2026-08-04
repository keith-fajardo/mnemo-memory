-- Append-only correction/retraction actions for explicit approved episodic facts.
-- A retraction deletes the target event payload; this table retains only bounded audit metadata.
CREATE TABLE approved_episodic_event_governance (
    governance_sequence INTEGER PRIMARY KEY,
    action_id TEXT NOT NULL UNIQUE,
    source_action_key TEXT NOT NULL,
    action_kind TEXT NOT NULL CHECK (action_kind IN ('corrected', 'retracted')),
    target_event_id TEXT NOT NULL UNIQUE,
    target_event_sequence INTEGER NOT NULL,
    replacement_event_id TEXT NULL UNIQUE,
    reason TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 1200),
    owner_id TEXT NOT NULL REFERENCES principals(owner_id) ON DELETE RESTRICT,
    visibility TEXT NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    workspace_id TEXT NULL REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE RESTRICT,
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    occurred_at TEXT NOT NULL,
    CHECK (
        (action_kind = 'corrected' AND replacement_event_id IS NOT NULL
            AND replacement_event_id <> target_event_id)
        OR (action_kind = 'retracted' AND replacement_event_id IS NULL)
    ),
    UNIQUE (
        owner_id, visibility, workspace_id, project_id, session_id, task_id, source_action_key
    )
);
CREATE TABLE approved_episodic_event_governance_evidence (
    action_id TEXT NOT NULL
        REFERENCES approved_episodic_event_governance(action_id) ON DELETE RESTRICT,
    evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id) ON DELETE RESTRICT,
    PRIMARY KEY (action_id, evidence_id)
);
CREATE INDEX approved_episodic_event_governance_scope_order_idx
ON approved_episodic_event_governance(
    owner_id, visibility, workspace_id, project_id, session_id, task_id,
    target_event_sequence DESC
);
CREATE TRIGGER approved_episodic_event_governance_target_scope_match
BEFORE INSERT ON approved_episodic_event_governance
FOR EACH ROW
WHEN NOT EXISTS (
    SELECT 1 FROM approved_episodic_events AS target
    WHERE target.event_id = NEW.target_event_id
      AND target.event_sequence = NEW.target_event_sequence
      AND target.owner_id = NEW.owner_id
      AND target.visibility = NEW.visibility
      AND target.workspace_id IS NEW.workspace_id
      AND target.project_id = NEW.project_id
      AND target.session_id = NEW.session_id
      AND target.task_id = NEW.task_id
)
BEGIN
    SELECT RAISE(ABORT, 'approved event governance target scope mismatch');
END;
CREATE TRIGGER approved_episodic_event_governance_replacement_scope_match
BEFORE INSERT ON approved_episodic_event_governance
FOR EACH ROW
WHEN NEW.action_kind = 'corrected' AND NOT EXISTS (
    SELECT 1 FROM approved_episodic_events AS replacement
    WHERE replacement.event_id = NEW.replacement_event_id
      AND replacement.owner_id = NEW.owner_id
      AND replacement.visibility = NEW.visibility
      AND replacement.workspace_id IS NEW.workspace_id
      AND replacement.project_id = NEW.project_id
      AND replacement.session_id = NEW.session_id
      AND replacement.task_id = NEW.task_id
)
BEGIN
    SELECT RAISE(ABORT, 'approved event governance replacement scope mismatch');
END;
