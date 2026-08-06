CREATE TABLE mnemo_team.imported_episodic_lifecycle (
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    session_id uuid NOT NULL,
    task_id uuid NOT NULL,
    lifecycle_kind text NOT NULL CHECK (
        lifecycle_kind IN (
            'memory_expiration', 'memory_purge', 'task_expiration', 'task_purge',
            'memory_deletion', 'task_deletion'
        )
    ),
    target_id uuid NOT NULL,
    source_id uuid NOT NULL,
    source_content_digest text NOT NULL CHECK (
        source_content_digest ~ '^sha256:[0-9a-f]{64}$'
    ),
    payload_json jsonb NOT NULL CHECK (jsonb_typeof(payload_json) = 'object'),
    imported_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, lifecycle_kind, target_id),
    UNIQUE (
        workspace_id, project_id, owner_id, visibility, session_id, task_id,
        lifecycle_kind, source_id
    ),
    FOREIGN KEY (workspace_id, project_id)
        REFERENCES mnemo_team.projects(workspace_id, project_id) ON DELETE RESTRICT
);

CREATE INDEX imported_episodic_lifecycle_scope
    ON mnemo_team.imported_episodic_lifecycle(
        workspace_id, project_id, owner_id, visibility, session_id, task_id,
        lifecycle_kind, target_id
    );

ALTER TABLE mnemo_team.imported_episodic_lifecycle ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.imported_episodic_lifecycle FORCE ROW LEVEL SECURITY;

CREATE POLICY imported_episodic_lifecycle_access
ON mnemo_team.imported_episodic_lifecycle
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));

REVOKE ALL ON ALL TABLES IN SCHEMA mnemo_team FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mnemo_team FROM PUBLIC;

INSERT INTO mnemo_team.schema_migrations(version, applied_at)
VALUES (15, CURRENT_TIMESTAMP);
