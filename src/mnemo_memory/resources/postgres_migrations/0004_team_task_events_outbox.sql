CREATE TABLE mnemo_team.task_activity_events (
    event_sequence bigserial NOT NULL,
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    session_id uuid NOT NULL,
    task_id uuid NOT NULL,
    event_id uuid NOT NULL,
    source_event_key text NOT NULL CHECK (
        source_event_key <> '' AND length(source_event_key) <= 256
    ),
    event_kind text NOT NULL CHECK (event_kind IN (
        'conversation_handoff', 'task_activity', 'tool_invocation', 'task_outcome'
    )),
    actor_kind text NOT NULL CHECK (actor_kind IN ('user', 'assistant', 'agent', 'tool')),
    summary text NOT NULL CHECK (summary <> '' AND length(summary) <= 1200),
    sensitivity text NOT NULL CHECK (
        sensitivity IN ('normal', 'personal', 'confidential', 'restricted')
    ),
    retention_json jsonb NOT NULL CHECK (jsonb_typeof(retention_json) = 'object'),
    occurred_at timestamptz NOT NULL,
    evidence_json jsonb NOT NULL CHECK (
        jsonb_typeof(evidence_json) = 'array'
        AND jsonb_array_length(evidence_json) BETWEEN 1 AND 64
    ),
    PRIMARY KEY (workspace_id, event_id),
    UNIQUE (
        workspace_id, project_id, owner_id, visibility, session_id, task_id, source_event_key
    ),
    UNIQUE (workspace_id, event_sequence),
    FOREIGN KEY (workspace_id, project_id)
        REFERENCES mnemo_team.projects(workspace_id, project_id) ON DELETE RESTRICT
);

CREATE INDEX task_activity_scope_order
    ON mnemo_team.task_activity_events(
        workspace_id, project_id, owner_id, visibility, session_id, task_id,
        event_sequence DESC
    );

CREATE TABLE mnemo_team.event_outbox (
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    session_id uuid NOT NULL,
    task_id uuid NOT NULL,
    job_id uuid NOT NULL,
    topic text NOT NULL CHECK (topic IN (
        'checkpoint_lifecycle', 'approved_episodic', 'approved_governance', 'task_activity'
    )),
    source_event_id uuid NOT NULL,
    event_kind text NOT NULL CHECK (event_kind <> '' AND length(event_kind) <= 64),
    occurred_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL,
    available_at timestamptz NOT NULL,
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    lease_owner text CHECK (lease_owner ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
    lease_expires_at timestamptz,
    completed_at timestamptz,
    last_failure_code text CHECK (last_failure_code ~ '^[A-Z][A-Z0-9_]{0,63}$'),
    PRIMARY KEY (workspace_id, job_id),
    UNIQUE (workspace_id, topic, source_event_id),
    FOREIGN KEY (workspace_id, project_id)
        REFERENCES mnemo_team.projects(workspace_id, project_id) ON DELETE RESTRICT,
    CHECK ((lease_owner IS NULL) = (lease_expires_at IS NULL)),
    CHECK (completed_at IS NULL OR lease_owner IS NULL)
);

CREATE INDEX event_outbox_task_claim_order
    ON mnemo_team.event_outbox(
        workspace_id, project_id, owner_id, visibility, session_id, task_id,
        completed_at, available_at, lease_expires_at, created_at, job_id
    );
CREATE INDEX event_outbox_project_status
    ON mnemo_team.event_outbox(
        workspace_id, project_id, owner_id, visibility, completed_at, last_failure_code
    );

CREATE FUNCTION mnemo_team.ensure_event_outbox_source()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    IF NEW.topic = 'task_activity' THEN
        IF NOT EXISTS (
            SELECT 1
              FROM mnemo_team.task_activity_events AS event
             WHERE event.workspace_id = NEW.workspace_id
               AND event.project_id = NEW.project_id
               AND event.owner_id = NEW.owner_id
               AND event.visibility = NEW.visibility
               AND event.session_id = NEW.session_id
               AND event.task_id = NEW.task_id
               AND event.event_id = NEW.source_event_id
               AND event.event_kind = NEW.event_kind
               AND event.occurred_at = NEW.occurred_at
        ) THEN
            RAISE EXCEPTION 'task activity outbox source mismatch' USING ERRCODE = '23514';
        END IF;
    ELSIF NEW.topic = 'checkpoint_lifecycle' THEN
        IF NOT EXISTS (
            SELECT 1
              FROM mnemo_team.checkpoint_lifecycle_events AS event
             WHERE event.workspace_id = NEW.workspace_id
               AND event.project_id = NEW.project_id
               AND event.owner_id = NEW.owner_id
               AND event.visibility = NEW.visibility
               AND event.session_id = NEW.session_id
               AND event.task_id = NEW.task_id
               AND event.event_id = NEW.source_event_id
               AND event.event_kind = NEW.event_kind
               AND event.occurred_at = NEW.occurred_at
        ) THEN
            RAISE EXCEPTION 'checkpoint outbox source mismatch' USING ERRCODE = '23514';
        END IF;
    ELSE
        RAISE EXCEPTION 'outbox topic source is not implemented' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER event_outbox_source_guard
BEFORE INSERT ON mnemo_team.event_outbox
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_event_outbox_source();

ALTER TABLE mnemo_team.task_activity_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.task_activity_events FORCE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.event_outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.event_outbox FORCE ROW LEVEL SECURITY;

CREATE POLICY task_activity_events_access ON mnemo_team.task_activity_events
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));

CREATE POLICY event_outbox_access ON mnemo_team.event_outbox
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));

REVOKE ALL ON ALL TABLES IN SCHEMA mnemo_team FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mnemo_team FROM PUBLIC;

INSERT INTO mnemo_team.schema_migrations(version, applied_at)
VALUES (4, CURRENT_TIMESTAMP);
