CREATE TABLE checkpoint_aggregates (
    checkpoint_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES principals(owner_id) ON DELETE RESTRICT,
    workspace_id TEXT NULL REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE RESTRICT,
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    current_revision_id TEXT NOT NULL UNIQUE,
    current_revision_number INTEGER NOT NULL CHECK (current_revision_number >= 1),
    lifecycle_status TEXT NOT NULL CHECK (lifecycle_status IN ('draft','active','completed','abandoned','superseded','expired')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (checkpoint_id, current_revision_number),
    FOREIGN KEY (current_revision_id) REFERENCES checkpoint_revision_records(checkpoint_revision_id)
        DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE checkpoint_revision_records (
    checkpoint_revision_id TEXT PRIMARY KEY,
    checkpoint_id TEXT NOT NULL REFERENCES checkpoint_aggregates(checkpoint_id) ON DELETE RESTRICT,
    revision_number INTEGER NOT NULL CHECK (revision_number >= 1),
    predecessor_revision_id TEXT NULL REFERENCES checkpoint_revision_records(checkpoint_revision_id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (status IN ('draft','active','completed','abandoned','superseded','expired')),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (checkpoint_id, revision_number)
);
CREATE TABLE checkpoint_revision_evidence (
    checkpoint_revision_id TEXT NOT NULL REFERENCES checkpoint_revision_records(checkpoint_revision_id) ON DELETE RESTRICT,
    evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id) ON DELETE RESTRICT,
    PRIMARY KEY (checkpoint_revision_id, evidence_id)
);
CREATE INDEX checkpoint_aggregate_scope_current_idx ON checkpoint_aggregates(owner_id, project_id, session_id, task_id, updated_at DESC);
CREATE INDEX checkpoint_revision_current_idx ON checkpoint_revision_records(checkpoint_id, revision_number DESC);
