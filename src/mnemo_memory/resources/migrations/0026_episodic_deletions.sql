-- Payload-free user deletion tombstones survive removal of their episodic targets.
CREATE TABLE IF NOT EXISTS task_activity_event_deletions (
    deletion_sequence INTEGER PRIMARY KEY,
    deletion_id TEXT NOT NULL UNIQUE,
    event_id TEXT NOT NULL UNIQUE,
    actor TEXT NOT NULL CHECK (actor = 'user'),
    source_action_key TEXT NOT NULL CHECK (
        length(source_action_key) BETWEEN 1 AND 256
    ),
    deleted_at TEXT NOT NULL CHECK (julianday(deleted_at) IS NOT NULL),
    owner_id TEXT NOT NULL REFERENCES principals(owner_id) ON DELETE RESTRICT,
    visibility TEXT NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    workspace_id TEXT NULL REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE RESTRICT,
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS task_activity_deletion_action_key_unique
ON task_activity_event_deletions(
    owner_id, visibility, IFNULL(workspace_id, ''), project_id, session_id, task_id,
    source_action_key
);

CREATE INDEX IF NOT EXISTS task_activity_deletion_scope_idx
ON task_activity_event_deletions(
    owner_id, visibility, workspace_id, project_id, session_id, task_id,
    deletion_sequence DESC
);

CREATE TRIGGER IF NOT EXISTS task_activity_deletion_target_guard
BEFORE INSERT ON task_activity_event_deletions
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM task_activity_events AS event
        WHERE event.event_id = NEW.event_id
          AND event.owner_id = NEW.owner_id
          AND event.visibility = NEW.visibility
          AND event.workspace_id IS NEW.workspace_id
          AND event.project_id = NEW.project_id
          AND event.session_id = NEW.session_id
          AND event.task_id = NEW.task_id
        UNION ALL
        SELECT 1 FROM task_activity_event_expirations AS expiration
        WHERE expiration.event_id = NEW.event_id
          AND expiration.owner_id = NEW.owner_id
          AND expiration.visibility = NEW.visibility
          AND expiration.workspace_id IS NEW.workspace_id
          AND expiration.project_id = NEW.project_id
          AND expiration.session_id = NEW.session_id
          AND expiration.task_id = NEW.task_id
    ) THEN RAISE(ABORT, 'task activity deletion target mismatch') END;
END;

CREATE TABLE IF NOT EXISTS episodic_memory_deletions (
    deletion_sequence INTEGER PRIMARY KEY,
    deletion_id TEXT NOT NULL UNIQUE,
    memory_id TEXT NOT NULL UNIQUE,
    source_event_id TEXT NOT NULL,
    cause TEXT NOT NULL CHECK (cause IN ('user', 'source_deleted')),
    source_deletion_id TEXT NULL REFERENCES task_activity_event_deletions(deletion_id)
        ON DELETE RESTRICT,
    actor TEXT NOT NULL CHECK (actor = 'user'),
    source_action_key TEXT NOT NULL CHECK (
        length(source_action_key) BETWEEN 1 AND 256
    ),
    deleted_at TEXT NOT NULL CHECK (julianday(deleted_at) IS NOT NULL),
    owner_id TEXT NOT NULL REFERENCES principals(owner_id) ON DELETE RESTRICT,
    visibility TEXT NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    workspace_id TEXT NULL REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE RESTRICT,
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    CHECK (
        (cause = 'user' AND source_deletion_id IS NULL)
        OR (cause = 'source_deleted' AND source_deletion_id IS NOT NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS episodic_memory_deletion_action_key_unique
ON episodic_memory_deletions(
    owner_id, visibility, IFNULL(workspace_id, ''), project_id, session_id, task_id,
    source_action_key
);

CREATE INDEX IF NOT EXISTS episodic_memory_deletion_scope_idx
ON episodic_memory_deletions(
    owner_id, visibility, workspace_id, project_id, session_id, task_id,
    deletion_sequence DESC
);

CREATE INDEX IF NOT EXISTS episodic_memory_deletion_source_idx
ON episodic_memory_deletions(source_event_id, memory_id);

CREATE TRIGGER IF NOT EXISTS episodic_memory_deletion_target_guard
BEFORE INSERT ON episodic_memory_deletions
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM episodic_memory_candidates AS candidate
        WHERE candidate.memory_id = NEW.memory_id
          AND candidate.source_event_id = NEW.source_event_id
          AND candidate.owner_id = NEW.owner_id
          AND candidate.visibility = NEW.visibility
          AND candidate.workspace_id IS NEW.workspace_id
          AND candidate.project_id = NEW.project_id
          AND candidate.session_id = NEW.session_id
          AND candidate.task_id = NEW.task_id
        UNION ALL
        SELECT 1 FROM episodic_memory_expirations AS expiration
        WHERE expiration.memory_id = NEW.memory_id
          AND expiration.source_event_id = NEW.source_event_id
          AND expiration.owner_id = NEW.owner_id
          AND expiration.visibility = NEW.visibility
          AND expiration.workspace_id IS NEW.workspace_id
          AND expiration.project_id = NEW.project_id
          AND expiration.session_id = NEW.session_id
          AND expiration.task_id = NEW.task_id
    ) THEN RAISE(ABORT, 'episodic memory deletion target mismatch') END;
    SELECT CASE WHEN NEW.cause = 'source_deleted' AND NOT EXISTS (
        SELECT 1 FROM task_activity_event_deletions AS source
        WHERE source.deletion_id = NEW.source_deletion_id
          AND source.event_id = NEW.source_event_id
          AND source.owner_id = NEW.owner_id
          AND source.visibility = NEW.visibility
          AND source.workspace_id IS NEW.workspace_id
          AND source.project_id = NEW.project_id
          AND source.session_id = NEW.session_id
          AND source.task_id = NEW.task_id
    ) THEN RAISE(ABORT, 'episodic memory source deletion mismatch') END;
END;

-- Retention completion remains valid when explicit deletion already removed the payload.
DROP TRIGGER IF EXISTS episodic_memory_purge_guard;
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
        UNION ALL
        SELECT 1 FROM episodic_memory_deletions AS deletion
        WHERE deletion.memory_id = NEW.memory_id
          AND deletion.source_event_id = NEW.source_event_id
          AND deletion.owner_id = NEW.owner_id
          AND deletion.visibility = NEW.visibility
          AND deletion.workspace_id IS NEW.workspace_id
          AND deletion.project_id = NEW.project_id
          AND deletion.session_id = NEW.session_id
          AND deletion.task_id = NEW.task_id
    ) THEN RAISE(ABORT, 'episodic purge target mismatch') END;
END;

DROP TRIGGER IF EXISTS task_activity_purge_guard;
CREATE TRIGGER task_activity_purge_guard
BEFORE UPDATE OF purge_id, purged_at ON task_activity_event_expirations
BEGIN
    SELECT CASE WHEN OLD.purge_id IS NOT NULL AND (
        NEW.purge_id IS NOT OLD.purge_id OR NEW.purged_at IS NOT OLD.purged_at
    ) THEN RAISE(ABORT, 'task activity purge is immutable') END;
    SELECT CASE WHEN NEW.purge_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM task_activity_events AS event
        WHERE event.event_id = NEW.event_id
          AND event.retention_policy_id = NEW.retention_policy_id
          AND event.retention_expires_at = NEW.scheduled_expires_at
          AND event.owner_id = NEW.owner_id
          AND event.visibility = NEW.visibility
          AND event.workspace_id IS NEW.workspace_id
          AND event.project_id = NEW.project_id
          AND event.session_id = NEW.session_id
          AND event.task_id = NEW.task_id
        UNION ALL
        SELECT 1 FROM task_activity_event_deletions AS deletion
        WHERE deletion.event_id = NEW.event_id
          AND deletion.owner_id = NEW.owner_id
          AND deletion.visibility = NEW.visibility
          AND deletion.workspace_id IS NEW.workspace_id
          AND deletion.project_id = NEW.project_id
          AND deletion.session_id = NEW.session_id
          AND deletion.task_id = NEW.task_id
    ) THEN RAISE(ABORT, 'task activity purge target mismatch') END;
END;
