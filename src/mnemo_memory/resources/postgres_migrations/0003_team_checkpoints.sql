CREATE TABLE mnemo_team.checkpoint_aggregates (
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    session_id uuid NOT NULL,
    task_id uuid NOT NULL,
    checkpoint_id uuid NOT NULL,
    current_revision_id uuid NOT NULL,
    current_revision_number integer NOT NULL CHECK (current_revision_number >= 1),
    lifecycle_status text NOT NULL CHECK (
        lifecycle_status IN ('active', 'completed', 'abandoned')
    ),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL CHECK (updated_at >= created_at),
    PRIMARY KEY (workspace_id, checkpoint_id),
    FOREIGN KEY (workspace_id, project_id)
        REFERENCES mnemo_team.projects(workspace_id, project_id) ON DELETE RESTRICT
);

CREATE INDEX checkpoint_aggregate_scope_order
    ON mnemo_team.checkpoint_aggregates(
        workspace_id, project_id, owner_id, visibility, session_id, task_id,
        lifecycle_status, updated_at DESC, checkpoint_id
    );

CREATE TABLE mnemo_team.checkpoint_revisions (
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    session_id uuid NOT NULL,
    task_id uuid NOT NULL,
    checkpoint_id uuid NOT NULL,
    revision_id uuid NOT NULL,
    revision_number integer NOT NULL CHECK (revision_number >= 1),
    predecessor_revision_id uuid,
    status text NOT NULL CHECK (status IN ('active', 'completed', 'abandoned')),
    content_json jsonb NOT NULL CHECK (jsonb_typeof(content_json) = 'object'),
    evidence_json jsonb NOT NULL CHECK (
        jsonb_typeof(evidence_json) = 'array' AND jsonb_array_length(evidence_json) > 0
    ),
    created_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, revision_id),
    UNIQUE (workspace_id, checkpoint_id, revision_number),
    FOREIGN KEY (workspace_id, checkpoint_id)
        REFERENCES mnemo_team.checkpoint_aggregates(workspace_id, checkpoint_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (workspace_id, predecessor_revision_id)
        REFERENCES mnemo_team.checkpoint_revisions(workspace_id, revision_id)
        ON DELETE RESTRICT
);

ALTER TABLE mnemo_team.checkpoint_aggregates
    ADD CONSTRAINT checkpoint_aggregate_current_revision
    FOREIGN KEY (workspace_id, current_revision_id)
    REFERENCES mnemo_team.checkpoint_revisions(workspace_id, revision_id)
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE mnemo_team.checkpoint_lifecycle_events (
    event_sequence bigserial NOT NULL,
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    session_id uuid NOT NULL,
    task_id uuid NOT NULL,
    event_id uuid NOT NULL,
    idempotency_key text NOT NULL CHECK (
        idempotency_key <> '' AND length(idempotency_key) <= 256
    ),
    event_kind text NOT NULL CHECK (event_kind IN (
        'checkpoint_created', 'checkpoint_revised', 'checkpoint_completed',
        'checkpoint_abandoned', 'checkpoint_lesson_recorded'
    )),
    checkpoint_id uuid NOT NULL,
    revision_id uuid NOT NULL,
    revision_number integer NOT NULL CHECK (revision_number >= 1),
    occurred_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, event_id),
    UNIQUE (workspace_id, idempotency_key),
    UNIQUE (workspace_id, event_sequence),
    FOREIGN KEY (workspace_id, checkpoint_id)
        REFERENCES mnemo_team.checkpoint_aggregates(workspace_id, checkpoint_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (workspace_id, revision_id)
        REFERENCES mnemo_team.checkpoint_revisions(workspace_id, revision_id)
        ON DELETE RESTRICT
);

CREATE INDEX checkpoint_event_scope_order
    ON mnemo_team.checkpoint_lifecycle_events(
        workspace_id, project_id, owner_id, visibility, session_id, task_id,
        event_sequence DESC
    );
CREATE INDEX checkpoint_event_checkpoint_order
    ON mnemo_team.checkpoint_lifecycle_events(
        workspace_id, checkpoint_id, event_sequence DESC
    );

CREATE FUNCTION mnemo_team.ensure_checkpoint_revision_chain()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    aggregate_scope record;
BEGIN
    SELECT aggregate.project_id, aggregate.owner_id, aggregate.visibility,
           aggregate.session_id, aggregate.task_id
      INTO aggregate_scope
      FROM mnemo_team.checkpoint_aggregates AS aggregate
     WHERE aggregate.workspace_id = NEW.workspace_id
       AND aggregate.checkpoint_id = NEW.checkpoint_id;
    IF NOT FOUND
       OR aggregate_scope.project_id IS DISTINCT FROM NEW.project_id
       OR aggregate_scope.owner_id IS DISTINCT FROM NEW.owner_id
       OR aggregate_scope.visibility IS DISTINCT FROM NEW.visibility
       OR aggregate_scope.session_id IS DISTINCT FROM NEW.session_id
       OR aggregate_scope.task_id IS DISTINCT FROM NEW.task_id
    THEN
        RAISE EXCEPTION 'checkpoint revision scope mismatch' USING ERRCODE = '23514';
    END IF;

    IF NEW.revision_number = 1 THEN
        IF NEW.predecessor_revision_id IS NOT NULL THEN
            RAISE EXCEPTION 'first checkpoint revision cannot have a predecessor'
                USING ERRCODE = '23514';
        END IF;
    ELSIF NOT EXISTS (
        SELECT 1
          FROM mnemo_team.checkpoint_revisions AS prior
         WHERE prior.workspace_id = NEW.workspace_id
           AND prior.revision_id = NEW.predecessor_revision_id
           AND prior.checkpoint_id = NEW.checkpoint_id
           AND prior.revision_number = NEW.revision_number - 1
           AND prior.project_id = NEW.project_id
           AND prior.owner_id = NEW.owner_id
           AND prior.visibility = NEW.visibility
           AND prior.session_id = NEW.session_id
           AND prior.task_id = NEW.task_id
    ) THEN
        RAISE EXCEPTION 'checkpoint revision predecessor mismatch' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER checkpoint_revision_chain_guard
BEFORE INSERT ON mnemo_team.checkpoint_revisions
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_checkpoint_revision_chain();

CREATE FUNCTION mnemo_team.ensure_checkpoint_aggregate_state()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM mnemo_team.checkpoint_revisions AS revision
         WHERE revision.workspace_id = NEW.workspace_id
           AND revision.revision_id = NEW.current_revision_id
           AND revision.checkpoint_id = NEW.checkpoint_id
           AND revision.revision_number = NEW.current_revision_number
           AND revision.status = NEW.lifecycle_status
           AND revision.project_id = NEW.project_id
           AND revision.owner_id = NEW.owner_id
           AND revision.visibility = NEW.visibility
           AND revision.session_id = NEW.session_id
           AND revision.task_id = NEW.task_id
    ) THEN
        RAISE EXCEPTION 'checkpoint aggregate current revision mismatch'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER checkpoint_aggregate_state_guard
AFTER INSERT OR UPDATE ON mnemo_team.checkpoint_aggregates
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_checkpoint_aggregate_state();

CREATE FUNCTION mnemo_team.ensure_checkpoint_event_state()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    revision_state record;
BEGIN
    SELECT revision.project_id, revision.owner_id, revision.visibility,
           revision.session_id, revision.task_id, revision.checkpoint_id,
           revision.revision_number, revision.status, revision.created_at
      INTO revision_state
      FROM mnemo_team.checkpoint_revisions AS revision
     WHERE revision.workspace_id = NEW.workspace_id
       AND revision.revision_id = NEW.revision_id;
    IF NOT FOUND
       OR revision_state.project_id IS DISTINCT FROM NEW.project_id
       OR revision_state.owner_id IS DISTINCT FROM NEW.owner_id
       OR revision_state.visibility IS DISTINCT FROM NEW.visibility
       OR revision_state.session_id IS DISTINCT FROM NEW.session_id
       OR revision_state.task_id IS DISTINCT FROM NEW.task_id
       OR revision_state.checkpoint_id IS DISTINCT FROM NEW.checkpoint_id
       OR revision_state.revision_number IS DISTINCT FROM NEW.revision_number
       OR revision_state.created_at IS DISTINCT FROM NEW.occurred_at
    THEN
        RAISE EXCEPTION 'checkpoint lifecycle event revision mismatch'
            USING ERRCODE = '23514';
    END IF;
    IF (NEW.event_kind = 'checkpoint_created'
            AND (NEW.revision_number <> 1 OR revision_state.status <> 'active'))
       OR (NEW.event_kind IN ('checkpoint_revised', 'checkpoint_lesson_recorded')
            AND (NEW.revision_number = 1 OR revision_state.status <> 'active'))
       OR (NEW.event_kind = 'checkpoint_completed' AND revision_state.status <> 'completed')
       OR (NEW.event_kind = 'checkpoint_abandoned' AND revision_state.status <> 'abandoned')
    THEN
        RAISE EXCEPTION 'checkpoint lifecycle event kind mismatch' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER checkpoint_event_state_guard
BEFORE INSERT ON mnemo_team.checkpoint_lifecycle_events
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_checkpoint_event_state();

ALTER TABLE mnemo_team.checkpoint_aggregates ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.checkpoint_aggregates FORCE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.checkpoint_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.checkpoint_revisions FORCE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.checkpoint_lifecycle_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.checkpoint_lifecycle_events FORCE ROW LEVEL SECURITY;

CREATE POLICY checkpoint_aggregates_access ON mnemo_team.checkpoint_aggregates
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));

CREATE POLICY checkpoint_revisions_access ON mnemo_team.checkpoint_revisions
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));

CREATE POLICY checkpoint_lifecycle_events_access ON mnemo_team.checkpoint_lifecycle_events
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));

REVOKE ALL ON ALL TABLES IN SCHEMA mnemo_team FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mnemo_team FROM PUBLIC;

INSERT INTO mnemo_team.schema_migrations(version, applied_at)
VALUES (3, CURRENT_TIMESTAMP);
