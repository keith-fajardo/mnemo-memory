-- Minimal delivery metadata for canonical episodic events. Event payloads remain in source tables.
CREATE TABLE event_outbox (
    job_id TEXT PRIMARY KEY,
    topic TEXT NOT NULL CHECK (
        topic IN ('checkpoint_lifecycle', 'approved_episodic', 'approved_governance')
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

CREATE INDEX event_outbox_scope_claim_idx ON event_outbox(
    owner_id, visibility, workspace_id, project_id, session_id, task_id,
    completed_at, available_at, lease_expires_at, created_at, job_id
);
