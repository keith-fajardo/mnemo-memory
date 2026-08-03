-- Explicit user/agent-approved episodic facts. No transcript, prompt, command output, or source body.
CREATE TABLE approved_episodic_events (
    event_sequence INTEGER PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    source_event_key TEXT NOT NULL,
    event_kind TEXT NOT NULL CHECK (event_kind IN ('decision', 'failure', 'tool_outcome')),
    summary TEXT NOT NULL CHECK (length(summary) BETWEEN 1 AND 1200),
    owner_id TEXT NOT NULL REFERENCES principals(owner_id) ON DELETE RESTRICT,
    visibility TEXT NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    workspace_id TEXT NULL REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE RESTRICT,
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    occurred_at TEXT NOT NULL,
    UNIQUE (owner_id, visibility, workspace_id, project_id, session_id, task_id, source_event_key)
);
CREATE TABLE approved_episodic_event_evidence (
    event_id TEXT NOT NULL REFERENCES approved_episodic_events(event_id) ON DELETE RESTRICT,
    evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id) ON DELETE RESTRICT,
    PRIMARY KEY (event_id, evidence_id)
);
CREATE INDEX approved_episodic_events_scope_order_idx ON approved_episodic_events(
    owner_id, visibility, workspace_id, project_id, session_id, task_id, event_sequence DESC
);
