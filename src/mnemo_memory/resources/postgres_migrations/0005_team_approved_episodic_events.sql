CREATE TABLE mnemo_team.approved_episodic_events (
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
    event_kind text NOT NULL CHECK (event_kind IN ('decision', 'failure', 'tool_outcome')),
    summary text NOT NULL CHECK (summary <> '' AND length(summary) <= 1200),
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

CREATE INDEX approved_episodic_event_scope_order
    ON mnemo_team.approved_episodic_events(
        workspace_id, project_id, owner_id, visibility, session_id, task_id,
        event_sequence DESC
    );

CREATE TABLE mnemo_team.approved_episodic_event_governance (
    governance_sequence bigserial NOT NULL,
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    session_id uuid NOT NULL,
    task_id uuid NOT NULL,
    action_id uuid NOT NULL,
    source_action_key text NOT NULL CHECK (
        source_action_key <> '' AND length(source_action_key) <= 256
    ),
    action_kind text NOT NULL CHECK (action_kind IN ('corrected', 'retracted')),
    target_event_id uuid NOT NULL,
    target_event_sequence bigint NOT NULL,
    replacement_event_id uuid,
    reason text NOT NULL CHECK (reason <> '' AND length(reason) <= 1200),
    occurred_at timestamptz NOT NULL,
    evidence_json jsonb NOT NULL CHECK (
        jsonb_typeof(evidence_json) = 'array'
        AND jsonb_array_length(evidence_json) BETWEEN 1 AND 64
    ),
    PRIMARY KEY (workspace_id, action_id),
    UNIQUE (workspace_id, target_event_id),
    UNIQUE (workspace_id, replacement_event_id),
    UNIQUE (
        workspace_id, project_id, owner_id, visibility, session_id, task_id, source_action_key
    ),
    UNIQUE (workspace_id, governance_sequence),
    FOREIGN KEY (workspace_id, project_id)
        REFERENCES mnemo_team.projects(workspace_id, project_id) ON DELETE RESTRICT,
    CHECK (
        (action_kind = 'corrected' AND replacement_event_id IS NOT NULL
            AND replacement_event_id <> target_event_id)
        OR (action_kind = 'retracted' AND replacement_event_id IS NULL)
    )
);

CREATE INDEX approved_episodic_governance_scope_order
    ON mnemo_team.approved_episodic_event_governance(
        workspace_id, project_id, owner_id, visibility, session_id, task_id,
        target_event_sequence DESC
    );

CREATE TABLE mnemo_team.approved_episodic_event_pin_actions (
    action_sequence bigserial NOT NULL,
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    session_id uuid NOT NULL,
    task_id uuid NOT NULL,
    action_id uuid NOT NULL,
    event_id uuid NOT NULL,
    pinned boolean NOT NULL,
    source_action_key text NOT NULL CHECK (
        source_action_key <> '' AND length(source_action_key) <= 256
    ),
    occurred_at timestamptz NOT NULL,
    evidence_json jsonb NOT NULL CHECK (
        jsonb_typeof(evidence_json) = 'array'
        AND jsonb_array_length(evidence_json) BETWEEN 1 AND 64
    ),
    PRIMARY KEY (workspace_id, action_id),
    UNIQUE (
        workspace_id, project_id, owner_id, visibility, session_id, task_id, source_action_key
    ),
    UNIQUE (workspace_id, action_sequence),
    FOREIGN KEY (workspace_id, project_id)
        REFERENCES mnemo_team.projects(workspace_id, project_id) ON DELETE RESTRICT
);

CREATE INDEX approved_episodic_pin_current
    ON mnemo_team.approved_episodic_event_pin_actions(
        workspace_id, project_id, owner_id, visibility, session_id, task_id,
        event_id, action_sequence DESC
    );

CREATE FUNCTION mnemo_team.ensure_approved_governance_scope()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM mnemo_team.approved_episodic_events AS target
         WHERE target.workspace_id = NEW.workspace_id
           AND target.project_id = NEW.project_id
           AND target.owner_id = NEW.owner_id
           AND target.visibility = NEW.visibility
           AND target.session_id = NEW.session_id
           AND target.task_id = NEW.task_id
           AND target.event_id = NEW.target_event_id
           AND target.event_sequence = NEW.target_event_sequence
    ) THEN
        RAISE EXCEPTION 'approved event governance target scope mismatch'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.action_kind = 'corrected' AND NOT EXISTS (
        SELECT 1
          FROM mnemo_team.approved_episodic_events AS replacement
         WHERE replacement.workspace_id = NEW.workspace_id
           AND replacement.project_id = NEW.project_id
           AND replacement.owner_id = NEW.owner_id
           AND replacement.visibility = NEW.visibility
           AND replacement.session_id = NEW.session_id
           AND replacement.task_id = NEW.task_id
           AND replacement.event_id = NEW.replacement_event_id
    ) THEN
        RAISE EXCEPTION 'approved event governance replacement scope mismatch'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER approved_governance_scope_guard
BEFORE INSERT ON mnemo_team.approved_episodic_event_governance
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_approved_governance_scope();

CREATE FUNCTION mnemo_team.ensure_approved_pin_scope()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM mnemo_team.approved_episodic_events AS event
         WHERE event.workspace_id = NEW.workspace_id
           AND event.project_id = NEW.project_id
           AND event.owner_id = NEW.owner_id
           AND event.visibility = NEW.visibility
           AND event.session_id = NEW.session_id
           AND event.task_id = NEW.task_id
           AND event.event_id = NEW.event_id
    ) THEN
        RAISE EXCEPTION 'approved event pin target scope mismatch' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER approved_pin_scope_guard
BEFORE INSERT ON mnemo_team.approved_episodic_event_pin_actions
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_approved_pin_scope();

CREATE FUNCTION mnemo_team.ensure_approved_event_erasure()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM mnemo_team.approved_episodic_event_governance AS action
         WHERE action.workspace_id = OLD.workspace_id
           AND action.project_id = OLD.project_id
           AND action.owner_id = OLD.owner_id
           AND action.visibility = OLD.visibility
           AND action.session_id = OLD.session_id
           AND action.task_id = OLD.task_id
           AND action.target_event_id = OLD.event_id
           AND action.action_kind = 'retracted'
    ) THEN
        RAISE EXCEPTION 'approved event erasure requires a retraction' USING ERRCODE = '23514';
    END IF;
    RETURN OLD;
END;
$$;

CREATE TRIGGER approved_event_erasure_guard
BEFORE DELETE ON mnemo_team.approved_episodic_events
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_approved_event_erasure();

CREATE OR REPLACE FUNCTION mnemo_team.ensure_event_outbox_source()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    IF NEW.topic = 'task_activity' THEN
        IF NOT EXISTS (
            SELECT 1 FROM mnemo_team.task_activity_events AS event
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
            SELECT 1 FROM mnemo_team.checkpoint_lifecycle_events AS event
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
    ELSIF NEW.topic = 'approved_episodic' THEN
        IF NOT EXISTS (
            SELECT 1 FROM mnemo_team.approved_episodic_events AS event
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
            RAISE EXCEPTION 'approved event outbox source mismatch' USING ERRCODE = '23514';
        END IF;
    ELSIF NEW.topic = 'approved_governance' THEN
        IF NEW.event_kind IN ('corrected', 'retracted') THEN
            IF NOT EXISTS (
                SELECT 1 FROM mnemo_team.approved_episodic_event_governance AS action
                 WHERE action.workspace_id = NEW.workspace_id
                   AND action.project_id = NEW.project_id
                   AND action.owner_id = NEW.owner_id
                   AND action.visibility = NEW.visibility
                   AND action.session_id = NEW.session_id
                   AND action.task_id = NEW.task_id
                   AND action.action_id = NEW.source_event_id
                   AND action.action_kind = NEW.event_kind
                   AND action.occurred_at = NEW.occurred_at
            ) THEN
                RAISE EXCEPTION 'approved governance outbox source mismatch'
                    USING ERRCODE = '23514';
            END IF;
        ELSIF NEW.event_kind IN ('pinned', 'unpinned') THEN
            IF NOT EXISTS (
                SELECT 1 FROM mnemo_team.approved_episodic_event_pin_actions AS action
                 WHERE action.workspace_id = NEW.workspace_id
                   AND action.project_id = NEW.project_id
                   AND action.owner_id = NEW.owner_id
                   AND action.visibility = NEW.visibility
                   AND action.session_id = NEW.session_id
                   AND action.task_id = NEW.task_id
                   AND action.action_id = NEW.source_event_id
                   AND action.pinned = (NEW.event_kind = 'pinned')
                   AND action.occurred_at = NEW.occurred_at
            ) THEN
                RAISE EXCEPTION 'approved pin outbox source mismatch' USING ERRCODE = '23514';
            END IF;
        ELSE
            RAISE EXCEPTION 'approved governance outbox kind is invalid' USING ERRCODE = '23514';
        END IF;
    ELSE
        RAISE EXCEPTION 'outbox topic source is not implemented' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

ALTER TABLE mnemo_team.approved_episodic_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.approved_episodic_events FORCE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.approved_episodic_event_governance ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.approved_episodic_event_governance FORCE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.approved_episodic_event_pin_actions ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.approved_episodic_event_pin_actions FORCE ROW LEVEL SECURITY;

CREATE POLICY approved_episodic_events_access ON mnemo_team.approved_episodic_events
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));

CREATE POLICY approved_episodic_governance_access
ON mnemo_team.approved_episodic_event_governance
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));

CREATE POLICY approved_episodic_pin_access
ON mnemo_team.approved_episodic_event_pin_actions
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));

REVOKE ALL ON ALL TABLES IN SCHEMA mnemo_team FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mnemo_team FROM PUBLIC;

INSERT INTO mnemo_team.schema_migrations(version, applied_at)
VALUES (5, CURRENT_TIMESTAMP);
