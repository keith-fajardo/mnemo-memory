CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE principals (
    owner_id TEXT PRIMARY KEY
);
CREATE TABLE workspaces (
    workspace_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES principals(owner_id) ON DELETE RESTRICT
);
CREATE TABLE projects (
    project_id TEXT PRIMARY KEY,
    workspace_id TEXT NULL REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    owner_id TEXT NOT NULL REFERENCES principals(owner_id) ON DELETE RESTRICT
);
CREATE TABLE agents (
    agent_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT
);
CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT
);
CREATE TABLE tasks (
    task_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE RESTRICT
);
CREATE TABLE evidence (
    evidence_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES principals(owner_id) ON DELETE RESTRICT,
    workspace_id TEXT NULL REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE RESTRICT,
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    current_revision INTEGER NOT NULL CHECK (current_revision >= 1)
);
CREATE TABLE checkpoint_revisions (
    checkpoint_id TEXT NOT NULL REFERENCES checkpoints(checkpoint_id) ON DELETE RESTRICT,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    status TEXT NOT NULL CHECK (status IN ('draft', 'active', 'completed', 'abandoned', 'superseded', 'expired')),
    supersedes_checkpoint_id TEXT NULL REFERENCES checkpoints(checkpoint_id) ON DELETE RESTRICT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (checkpoint_id, revision),
    UNIQUE (checkpoint_id, revision)
);
CREATE TABLE checkpoint_evidence (
    checkpoint_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id) ON DELETE RESTRICT,
    PRIMARY KEY (checkpoint_id, revision, evidence_id),
    FOREIGN KEY (checkpoint_id, revision) REFERENCES checkpoint_revisions(checkpoint_id, revision) ON DELETE RESTRICT
);
