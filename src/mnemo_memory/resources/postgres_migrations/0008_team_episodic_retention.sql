CREATE TABLE mnemo_team.episodic_memory_expirations (
    expiration_sequence bigserial NOT NULL,
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    session_id uuid NOT NULL,
    task_id uuid NOT NULL,
    expiration_id uuid NOT NULL,
    memory_id uuid NOT NULL,
    source_event_id uuid NOT NULL,
    retention_policy_id uuid NOT NULL,
    scheduled_expires_at text NOT NULL,
    expired_at text NOT NULL CHECK (
        expired_at::timestamptz >= scheduled_expires_at::timestamptz
    ),
    PRIMARY KEY (workspace_id, memory_id),
    UNIQUE (workspace_id, expiration_id),
    UNIQUE (workspace_id, expiration_sequence),
    FOREIGN KEY (workspace_id, project_id)
        REFERENCES mnemo_team.projects(workspace_id, project_id) ON DELETE RESTRICT,
    FOREIGN KEY (workspace_id, source_event_id)
        REFERENCES mnemo_team.task_activity_events(workspace_id, event_id) ON DELETE RESTRICT
);

CREATE INDEX episodic_expiration_scope_order
    ON mnemo_team.episodic_memory_expirations(
        workspace_id, project_id, owner_id, visibility, session_id, task_id,
        expired_at ASC, memory_id ASC
    );

CREATE TABLE mnemo_team.episodic_memory_purges (
    purge_sequence bigserial NOT NULL,
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    session_id uuid NOT NULL,
    task_id uuid NOT NULL,
    purge_id uuid NOT NULL,
    expiration_id uuid NOT NULL,
    memory_id uuid NOT NULL,
    purged_at text NOT NULL,
    PRIMARY KEY (workspace_id, memory_id),
    UNIQUE (workspace_id, purge_id),
    UNIQUE (workspace_id, expiration_id),
    UNIQUE (workspace_id, purge_sequence),
    FOREIGN KEY (workspace_id, project_id)
        REFERENCES mnemo_team.projects(workspace_id, project_id) ON DELETE RESTRICT,
    FOREIGN KEY (workspace_id, expiration_id)
        REFERENCES mnemo_team.episodic_memory_expirations(workspace_id, expiration_id)
        ON DELETE RESTRICT
);

CREATE INDEX episodic_purge_scope_order
    ON mnemo_team.episodic_memory_purges(
        workspace_id, project_id, owner_id, visibility, session_id, task_id,
        purged_at ASC, memory_id ASC
    );

CREATE FUNCTION mnemo_team.ensure_episodic_expiration_target()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM mnemo_team.episodic_memory_candidates AS candidate
         WHERE candidate.workspace_id = NEW.workspace_id
           AND candidate.project_id = NEW.project_id
           AND candidate.owner_id = NEW.owner_id
           AND candidate.visibility = NEW.visibility
           AND candidate.session_id = NEW.session_id
           AND candidate.task_id = NEW.task_id
           AND candidate.memory_id = NEW.memory_id
           AND candidate.source_event_id = NEW.source_event_id
           AND (candidate.retention_json ->> 'policy_id')::uuid = NEW.retention_policy_id
           AND (candidate.retention_json ->> 'permanent')::boolean = false
           AND candidate.retention_json ->> 'expires_at' = NEW.scheduled_expires_at
    ) THEN
        RAISE EXCEPTION 'episodic expiration target mismatch' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER episodic_expiration_target_guard
BEFORE INSERT ON mnemo_team.episodic_memory_expirations
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_episodic_expiration_target();

CREATE FUNCTION mnemo_team.ensure_episodic_purge_target()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM mnemo_team.episodic_memory_expirations AS expiration
         WHERE expiration.workspace_id = NEW.workspace_id
           AND expiration.project_id = NEW.project_id
           AND expiration.owner_id = NEW.owner_id
           AND expiration.visibility = NEW.visibility
           AND expiration.session_id = NEW.session_id
           AND expiration.task_id = NEW.task_id
           AND expiration.expiration_id = NEW.expiration_id
           AND expiration.memory_id = NEW.memory_id
           AND NEW.purged_at::timestamptz >= expiration.expired_at::timestamptz
    ) THEN
        RAISE EXCEPTION 'episodic purge target mismatch' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER episodic_purge_target_guard
BEFORE INSERT ON mnemo_team.episodic_memory_purges
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_episodic_purge_target();

CREATE FUNCTION mnemo_team.ensure_episodic_payload_purge()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    target_memory_id uuid;
BEGIN
    IF TG_TABLE_NAME = 'episodic_candidate_reviews' THEN
        target_memory_id := OLD.candidate_id;
    ELSE
        target_memory_id := OLD.memory_id;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM mnemo_team.episodic_memory_purges AS purge
         WHERE purge.workspace_id = OLD.workspace_id
           AND purge.project_id = OLD.project_id
           AND purge.owner_id = OLD.owner_id
           AND purge.visibility = OLD.visibility
           AND purge.session_id = OLD.session_id
           AND purge.task_id = OLD.task_id
           AND purge.memory_id = target_memory_id
    ) THEN
        RAISE EXCEPTION 'episodic payload deletion requires purge' USING ERRCODE = '23514';
    END IF;
    RETURN OLD;
END;
$$;

CREATE TRIGGER episodic_governance_purge_guard
BEFORE DELETE ON mnemo_team.episodic_memory_governance
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_episodic_payload_purge();

CREATE TRIGGER active_episodic_purge_guard
BEFORE DELETE ON mnemo_team.active_episodic_memories
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_episodic_payload_purge();

CREATE TRIGGER episodic_review_purge_guard
BEFORE DELETE ON mnemo_team.episodic_candidate_reviews
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_episodic_payload_purge();

CREATE TRIGGER episodic_candidate_purge_guard
BEFORE DELETE ON mnemo_team.episodic_memory_candidates
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_episodic_payload_purge();

ALTER TABLE mnemo_team.episodic_memory_expirations ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.episodic_memory_expirations FORCE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.episodic_memory_purges ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.episodic_memory_purges FORCE ROW LEVEL SECURITY;

CREATE POLICY episodic_expiration_access ON mnemo_team.episodic_memory_expirations
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));

CREATE POLICY episodic_purge_access ON mnemo_team.episodic_memory_purges
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));

REVOKE ALL ON ALL TABLES IN SCHEMA mnemo_team FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mnemo_team FROM PUBLIC;

INSERT INTO mnemo_team.schema_migrations(version, applied_at)
VALUES (8, CURRENT_TIMESTAMP);
