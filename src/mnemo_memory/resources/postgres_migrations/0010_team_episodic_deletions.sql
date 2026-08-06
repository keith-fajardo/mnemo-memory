CREATE TABLE mnemo_team.task_activity_event_deletions (
    deletion_sequence bigserial NOT NULL,
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    session_id uuid NOT NULL,
    task_id uuid NOT NULL,
    deletion_id uuid NOT NULL,
    event_id uuid NOT NULL,
    actor text NOT NULL CHECK (actor = 'user'),
    source_action_key text NOT NULL CHECK (
        source_action_key <> '' AND length(source_action_key) <= 256
    ),
    deleted_at text NOT NULL,
    PRIMARY KEY (workspace_id, event_id),
    UNIQUE (workspace_id, deletion_id),
    UNIQUE (
        workspace_id, project_id, owner_id, visibility, session_id, task_id, source_action_key
    ),
    UNIQUE (workspace_id, deletion_sequence),
    FOREIGN KEY (workspace_id, project_id)
        REFERENCES mnemo_team.projects(workspace_id, project_id) ON DELETE RESTRICT
);

CREATE TABLE mnemo_team.episodic_memory_deletions (
    deletion_sequence bigserial NOT NULL,
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    session_id uuid NOT NULL,
    task_id uuid NOT NULL,
    deletion_id uuid NOT NULL,
    memory_id uuid NOT NULL,
    source_event_id uuid NOT NULL,
    cause text NOT NULL CHECK (cause IN ('user', 'source_deleted')),
    source_deletion_id uuid,
    actor text NOT NULL CHECK (actor = 'user'),
    source_action_key text NOT NULL CHECK (
        source_action_key <> '' AND length(source_action_key) <= 256
    ),
    deleted_at text NOT NULL,
    PRIMARY KEY (workspace_id, memory_id),
    UNIQUE (workspace_id, deletion_id),
    UNIQUE (
        workspace_id, project_id, owner_id, visibility, session_id, task_id, source_action_key
    ),
    UNIQUE (workspace_id, deletion_sequence),
    FOREIGN KEY (workspace_id, project_id)
        REFERENCES mnemo_team.projects(workspace_id, project_id) ON DELETE RESTRICT,
    FOREIGN KEY (workspace_id, source_deletion_id)
        REFERENCES mnemo_team.task_activity_event_deletions(workspace_id, deletion_id)
        ON DELETE RESTRICT,
    CHECK (
        (cause = 'user' AND source_deletion_id IS NULL)
        OR (cause = 'source_deleted' AND source_deletion_id IS NOT NULL)
    )
);

CREATE FUNCTION mnemo_team.ensure_task_activity_deletion_target()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM mnemo_team.task_activity_events AS event
         WHERE event.workspace_id = NEW.workspace_id AND event.project_id = NEW.project_id
           AND event.owner_id = NEW.owner_id AND event.visibility = NEW.visibility
           AND event.session_id = NEW.session_id AND event.task_id = NEW.task_id
           AND event.event_id = NEW.event_id
        UNION ALL
        SELECT 1 FROM mnemo_team.task_activity_event_expirations AS expiration
         WHERE expiration.workspace_id = NEW.workspace_id
           AND expiration.project_id = NEW.project_id AND expiration.owner_id = NEW.owner_id
           AND expiration.visibility = NEW.visibility AND expiration.session_id = NEW.session_id
           AND expiration.task_id = NEW.task_id AND expiration.event_id = NEW.event_id
    ) THEN
        RAISE EXCEPTION 'task activity deletion target mismatch' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER task_activity_deletion_target_guard
BEFORE INSERT ON mnemo_team.task_activity_event_deletions
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_task_activity_deletion_target();

CREATE FUNCTION mnemo_team.ensure_episodic_memory_deletion_target()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM mnemo_team.episodic_memory_candidates AS candidate
         WHERE candidate.workspace_id = NEW.workspace_id AND candidate.project_id = NEW.project_id
           AND candidate.owner_id = NEW.owner_id AND candidate.visibility = NEW.visibility
           AND candidate.session_id = NEW.session_id AND candidate.task_id = NEW.task_id
           AND candidate.memory_id = NEW.memory_id
           AND candidate.source_event_id = NEW.source_event_id
        UNION ALL
        SELECT 1 FROM mnemo_team.episodic_memory_expirations AS expiration
         WHERE expiration.workspace_id = NEW.workspace_id
           AND expiration.project_id = NEW.project_id AND expiration.owner_id = NEW.owner_id
           AND expiration.visibility = NEW.visibility AND expiration.session_id = NEW.session_id
           AND expiration.task_id = NEW.task_id AND expiration.memory_id = NEW.memory_id
           AND expiration.source_event_id = NEW.source_event_id
    ) THEN
        RAISE EXCEPTION 'episodic memory deletion target mismatch' USING ERRCODE = '23514';
    END IF;
    IF NEW.cause = 'source_deleted' AND NOT EXISTS (
        SELECT 1 FROM mnemo_team.task_activity_event_deletions AS source
         WHERE source.workspace_id = NEW.workspace_id AND source.project_id = NEW.project_id
           AND source.owner_id = NEW.owner_id AND source.visibility = NEW.visibility
           AND source.session_id = NEW.session_id AND source.task_id = NEW.task_id
           AND source.deletion_id = NEW.source_deletion_id
           AND source.event_id = NEW.source_event_id
    ) THEN
        RAISE EXCEPTION 'episodic source deletion mismatch' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER episodic_memory_deletion_target_guard
BEFORE INSERT ON mnemo_team.episodic_memory_deletions
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_episodic_memory_deletion_target();

CREATE OR REPLACE FUNCTION mnemo_team.ensure_episodic_payload_purge()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE target_memory_id uuid;
BEGIN
    IF TG_TABLE_NAME = 'episodic_candidate_reviews' THEN
        target_memory_id := OLD.candidate_id;
    ELSE
        target_memory_id := OLD.memory_id;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM mnemo_team.episodic_memory_purges AS purge
         WHERE purge.workspace_id = OLD.workspace_id AND purge.memory_id = target_memory_id
        UNION ALL
        SELECT 1 FROM mnemo_team.episodic_memory_deletions AS deletion
         WHERE deletion.workspace_id = OLD.workspace_id
           AND deletion.project_id = OLD.project_id AND deletion.owner_id = OLD.owner_id
           AND deletion.visibility = OLD.visibility AND deletion.session_id = OLD.session_id
           AND deletion.task_id = OLD.task_id AND deletion.memory_id = target_memory_id
    ) THEN
        RAISE EXCEPTION 'episodic payload deletion requires lifecycle action'
            USING ERRCODE = '23514';
    END IF;
    RETURN OLD;
END;
$$;

CREATE OR REPLACE FUNCTION mnemo_team.ensure_task_activity_payload_purge()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE target_event_id uuid;
BEGIN
    IF TG_TABLE_NAME = 'event_outbox' THEN
        IF OLD.topic <> 'task_activity' THEN
            RAISE EXCEPTION 'non-task outbox deletion is not permitted' USING ERRCODE = '23514';
        END IF;
        target_event_id := OLD.source_event_id;
    ELSE
        target_event_id := OLD.event_id;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM mnemo_team.task_activity_event_purges AS purge
         WHERE purge.workspace_id = OLD.workspace_id AND purge.event_id = target_event_id
        UNION ALL
        SELECT 1 FROM mnemo_team.task_activity_event_deletions AS deletion
         WHERE deletion.workspace_id = OLD.workspace_id
           AND deletion.project_id = OLD.project_id AND deletion.owner_id = OLD.owner_id
           AND deletion.visibility = OLD.visibility AND deletion.session_id = OLD.session_id
           AND deletion.task_id = OLD.task_id AND deletion.event_id = target_event_id
    ) THEN
        RAISE EXCEPTION 'task activity payload deletion requires lifecycle action'
            USING ERRCODE = '23514';
    END IF;
    RETURN OLD;
END;
$$;

ALTER TABLE mnemo_team.task_activity_event_deletions ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.task_activity_event_deletions FORCE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.episodic_memory_deletions ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.episodic_memory_deletions FORCE ROW LEVEL SECURITY;

CREATE POLICY task_activity_deletion_access ON mnemo_team.task_activity_event_deletions
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));
CREATE POLICY episodic_memory_deletion_access ON mnemo_team.episodic_memory_deletions
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));

REVOKE ALL ON ALL TABLES IN SCHEMA mnemo_team FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mnemo_team FROM PUBLIC;

INSERT INTO mnemo_team.schema_migrations(version, applied_at)
VALUES (10, CURRENT_TIMESTAMP);
