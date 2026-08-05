-- Preserve payload-free expiration tombstones after candidate-dependent content is purged.
DROP TRIGGER IF EXISTS episodic_memory_expiration_target_guard;
DROP INDEX IF EXISTS episodic_memory_expirations_scope_idx;

CREATE TABLE episodic_memory_expirations_v24 (
    expiration_sequence INTEGER PRIMARY KEY,
    expiration_id TEXT NOT NULL UNIQUE,
    memory_id TEXT NOT NULL UNIQUE,
    source_event_id TEXT NOT NULL REFERENCES task_activity_events(event_id) ON DELETE RESTRICT,
    retention_policy_id TEXT NOT NULL,
    scheduled_expires_at TEXT NOT NULL,
    expired_at TEXT NOT NULL,
    purge_id TEXT NULL UNIQUE,
    purged_at TEXT NULL,
    owner_id TEXT NOT NULL REFERENCES principals(owner_id) ON DELETE RESTRICT,
    visibility TEXT NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    workspace_id TEXT NULL REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE RESTRICT,
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    CHECK (
        julianday(scheduled_expires_at) IS NOT NULL
        AND julianday(expired_at) IS NOT NULL
        AND julianday(expired_at) >= julianday(scheduled_expires_at)
    ),
    CHECK (
        (purge_id IS NULL AND purged_at IS NULL)
        OR (
            purge_id IS NOT NULL
            AND julianday(purged_at) IS NOT NULL
            AND julianday(purged_at) >= julianday(expired_at)
        )
    )
);

INSERT INTO episodic_memory_expirations_v24(
    expiration_sequence, expiration_id, memory_id, source_event_id, retention_policy_id,
    scheduled_expires_at, expired_at, purge_id, purged_at, owner_id, visibility, workspace_id,
    project_id, session_id, task_id
)
SELECT
    expiration_sequence, expiration_id, memory_id, source_event_id, retention_policy_id,
    scheduled_expires_at, expired_at, NULL, NULL, owner_id, visibility, workspace_id,
    project_id, session_id, task_id
FROM episodic_memory_expirations;

DROP TABLE episodic_memory_expirations;
ALTER TABLE episodic_memory_expirations_v24 RENAME TO episodic_memory_expirations;

CREATE INDEX episodic_memory_expirations_scope_idx
ON episodic_memory_expirations(
    owner_id, visibility, workspace_id, project_id, session_id, task_id,
    expiration_sequence DESC
);

CREATE TRIGGER episodic_memory_expiration_target_guard
BEFORE INSERT ON episodic_memory_expirations
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM episodic_memory_candidates AS candidate
        WHERE candidate.memory_id = NEW.memory_id
          AND candidate.source_event_id = NEW.source_event_id
          AND candidate.retention_policy_id = NEW.retention_policy_id
          AND candidate.retention_permanent = 0
          AND candidate.retention_expires_at = NEW.scheduled_expires_at
          AND candidate.owner_id = NEW.owner_id
          AND candidate.visibility = NEW.visibility
          AND candidate.workspace_id IS NEW.workspace_id
          AND candidate.project_id = NEW.project_id
          AND candidate.session_id = NEW.session_id
          AND candidate.task_id = NEW.task_id
    ) THEN RAISE(ABORT, 'episodic expiration target mismatch') END;
END;

CREATE TRIGGER episodic_memory_purge_guard
BEFORE UPDATE OF purge_id, purged_at ON episodic_memory_expirations
BEGIN
    SELECT CASE WHEN OLD.purge_id IS NOT NULL AND (
        NEW.purge_id IS NOT OLD.purge_id OR NEW.purged_at IS NOT OLD.purged_at
    ) THEN RAISE(ABORT, 'episodic purge is immutable') END;
    SELECT CASE WHEN NEW.purge_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM episodic_memory_candidates AS candidate
        WHERE candidate.memory_id = NEW.memory_id
          AND candidate.source_event_id = NEW.source_event_id
          AND candidate.retention_policy_id = NEW.retention_policy_id
          AND candidate.retention_expires_at = NEW.scheduled_expires_at
          AND candidate.owner_id = NEW.owner_id
          AND candidate.visibility = NEW.visibility
          AND candidate.workspace_id IS NEW.workspace_id
          AND candidate.project_id = NEW.project_id
          AND candidate.session_id = NEW.session_id
          AND candidate.task_id = NEW.task_id
    ) THEN RAISE(ABORT, 'episodic purge target mismatch') END;
END;
