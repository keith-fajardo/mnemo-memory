-- Candidate retention tombstones must survive later source-event purge.
ALTER TABLE mnemo_team.episodic_memory_expirations
DROP CONSTRAINT episodic_memory_expirations_workspace_id_source_event_id_fkey;

CREATE TABLE mnemo_team.task_activity_event_expirations (
    expiration_sequence bigserial NOT NULL,
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    session_id uuid NOT NULL,
    task_id uuid NOT NULL,
    expiration_id uuid NOT NULL,
    event_id uuid NOT NULL,
    retention_policy_id uuid NOT NULL,
    scheduled_expires_at text NOT NULL,
    expired_at text NOT NULL CHECK (
        expired_at::timestamptz >= scheduled_expires_at::timestamptz
    ),
    PRIMARY KEY (workspace_id, event_id),
    UNIQUE (workspace_id, expiration_id),
    UNIQUE (workspace_id, expiration_sequence),
    FOREIGN KEY (workspace_id, project_id)
        REFERENCES mnemo_team.projects(workspace_id, project_id) ON DELETE RESTRICT
);

CREATE INDEX task_activity_expiration_scope_order
    ON mnemo_team.task_activity_event_expirations(
        workspace_id, project_id, owner_id, visibility, session_id, task_id,
        expired_at ASC, event_id ASC
    );

CREATE TABLE mnemo_team.task_activity_event_purges (
    purge_sequence bigserial NOT NULL,
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    session_id uuid NOT NULL,
    task_id uuid NOT NULL,
    purge_id uuid NOT NULL,
    expiration_id uuid NOT NULL,
    event_id uuid NOT NULL,
    purged_at text NOT NULL,
    PRIMARY KEY (workspace_id, event_id),
    UNIQUE (workspace_id, purge_id),
    UNIQUE (workspace_id, expiration_id),
    UNIQUE (workspace_id, purge_sequence),
    FOREIGN KEY (workspace_id, project_id)
        REFERENCES mnemo_team.projects(workspace_id, project_id) ON DELETE RESTRICT,
    FOREIGN KEY (workspace_id, expiration_id)
        REFERENCES mnemo_team.task_activity_event_expirations(workspace_id, expiration_id)
        ON DELETE RESTRICT
);

CREATE FUNCTION mnemo_team.ensure_task_activity_expiration_target()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM mnemo_team.task_activity_events AS event
         WHERE event.workspace_id = NEW.workspace_id
           AND event.project_id = NEW.project_id
           AND event.owner_id = NEW.owner_id
           AND event.visibility = NEW.visibility
           AND event.session_id = NEW.session_id
           AND event.task_id = NEW.task_id
           AND event.event_id = NEW.event_id
           AND (event.retention_json ->> 'policy_id')::uuid = NEW.retention_policy_id
           AND (event.retention_json ->> 'permanent')::boolean = false
           AND event.retention_json ->> 'expires_at' = NEW.scheduled_expires_at
    ) THEN
        RAISE EXCEPTION 'task activity expiration target mismatch' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER task_activity_expiration_target_guard
BEFORE INSERT ON mnemo_team.task_activity_event_expirations
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_task_activity_expiration_target();

CREATE FUNCTION mnemo_team.ensure_task_activity_purge_target()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM mnemo_team.task_activity_event_expirations AS expiration
         WHERE expiration.workspace_id = NEW.workspace_id
           AND expiration.project_id = NEW.project_id
           AND expiration.owner_id = NEW.owner_id
           AND expiration.visibility = NEW.visibility
           AND expiration.session_id = NEW.session_id
           AND expiration.task_id = NEW.task_id
           AND expiration.expiration_id = NEW.expiration_id
           AND expiration.event_id = NEW.event_id
           AND NEW.purged_at::timestamptz >= expiration.expired_at::timestamptz
    ) OR EXISTS (
        SELECT 1 FROM mnemo_team.episodic_memory_candidates AS candidate
         WHERE candidate.workspace_id = NEW.workspace_id
           AND candidate.source_event_id = NEW.event_id
    ) THEN
        RAISE EXCEPTION 'task activity purge target mismatch' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER task_activity_purge_target_guard
BEFORE INSERT ON mnemo_team.task_activity_event_purges
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_task_activity_purge_target();

CREATE FUNCTION mnemo_team.ensure_task_activity_payload_purge()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    target_event_id uuid;
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
         WHERE purge.workspace_id = OLD.workspace_id
           AND purge.project_id = OLD.project_id
           AND purge.owner_id = OLD.owner_id
           AND purge.visibility = OLD.visibility
           AND purge.session_id = OLD.session_id
           AND purge.task_id = OLD.task_id
           AND purge.event_id = target_event_id
    ) THEN
        RAISE EXCEPTION 'task activity payload deletion requires purge' USING ERRCODE = '23514';
    END IF;
    RETURN OLD;
END;
$$;

CREATE TRIGGER task_activity_outbox_purge_guard
BEFORE DELETE ON mnemo_team.event_outbox
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_task_activity_payload_purge();

CREATE TRIGGER task_activity_event_purge_guard
BEFORE DELETE ON mnemo_team.task_activity_events
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_task_activity_payload_purge();

ALTER TABLE mnemo_team.task_activity_event_expirations ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.task_activity_event_expirations FORCE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.task_activity_event_purges ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.task_activity_event_purges FORCE ROW LEVEL SECURITY;

CREATE POLICY task_activity_expiration_access ON mnemo_team.task_activity_event_expirations
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));

CREATE POLICY task_activity_purge_access ON mnemo_team.task_activity_event_purges
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));

REVOKE ALL ON ALL TABLES IN SCHEMA mnemo_team FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mnemo_team FROM PUBLIC;

INSERT INTO mnemo_team.schema_migrations(version, applied_at)
VALUES (9, CURRENT_TIMESTAMP);
