-- Expand only the closed outbox topic constraint while preserving every existing scoped job.
ALTER TABLE event_outbox RENAME TO event_outbox_v18;

CREATE TABLE event_outbox (
    job_id TEXT PRIMARY KEY,
    topic TEXT NOT NULL CHECK (
        topic IN (
            'checkpoint_lifecycle', 'approved_episodic', 'approved_governance', 'task_activity'
        )
    ),
    source_event_id TEXT NOT NULL,
    event_kind TEXT NOT NULL CHECK (length(event_kind) BETWEEN 1 AND 64),
    owner_id TEXT NOT NULL REFERENCES principals(owner_id) ON DELETE RESTRICT,
    visibility TEXT NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    workspace_id TEXT NULL REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE RESTRICT,
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    lease_owner TEXT NULL CHECK (lease_owner IS NULL OR length(lease_owner) BETWEEN 1 AND 128),
    lease_expires_at TEXT NULL,
    completed_at TEXT NULL,
    last_failure_code TEXT NULL CHECK (
        last_failure_code IS NULL OR length(last_failure_code) BETWEEN 1 AND 64
    ),
    UNIQUE (topic, source_event_id),
    CHECK ((lease_owner IS NULL) = (lease_expires_at IS NULL)),
    CHECK (completed_at IS NULL OR lease_owner IS NULL)
);

INSERT INTO event_outbox SELECT * FROM event_outbox_v18;
DROP TABLE event_outbox_v18;

CREATE INDEX event_outbox_scope_claim_idx ON event_outbox(
    owner_id, visibility, workspace_id, project_id, session_id, task_id,
    completed_at, available_at, lease_expires_at, created_at, job_id
);

-- Explicit minimized summaries only. Raw transcripts, prompts, commands, and tool bodies are absent.
CREATE TABLE IF NOT EXISTS task_activity_events (
    event_sequence INTEGER PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    source_event_key TEXT NOT NULL,
    event_kind TEXT NOT NULL CHECK (
        event_kind IN ('conversation_handoff', 'task_activity', 'tool_invocation', 'task_outcome')
    ),
    actor_kind TEXT NOT NULL CHECK (actor_kind IN ('user', 'assistant', 'agent', 'tool')),
    summary TEXT NOT NULL CHECK (length(summary) BETWEEN 1 AND 1200),
    sensitivity TEXT NOT NULL CHECK (
        sensitivity IN ('normal', 'personal', 'confidential', 'restricted')
    ),
    retention_policy_id TEXT NOT NULL,
    retention_permanent INTEGER NOT NULL CHECK (retention_permanent IN (0, 1)),
    retention_created_at TEXT NOT NULL,
    retention_observed_at TEXT NOT NULL,
    retention_valid_from TEXT NOT NULL,
    retention_valid_to TEXT NULL,
    retention_expires_at TEXT NULL,
    retention_expired_at TEXT NULL,
    owner_id TEXT NOT NULL REFERENCES principals(owner_id) ON DELETE RESTRICT,
    visibility TEXT NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    workspace_id TEXT NULL REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE RESTRICT,
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    occurred_at TEXT NOT NULL,
    UNIQUE (owner_id, visibility, workspace_id, project_id, session_id, task_id, source_event_key),
    CHECK (
        (retention_permanent = 1 AND retention_expires_at IS NULL AND retention_expired_at IS NULL)
        OR (retention_permanent = 0 AND retention_expires_at IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS task_activity_event_evidence (
    event_id TEXT NOT NULL REFERENCES task_activity_events(event_id) ON DELETE RESTRICT,
    evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id) ON DELETE RESTRICT,
    PRIMARY KEY (event_id, evidence_id)
);

CREATE INDEX IF NOT EXISTS task_activity_events_scope_order_idx ON task_activity_events(
    owner_id, visibility, workspace_id, project_id, session_id, task_id, event_sequence DESC
);
