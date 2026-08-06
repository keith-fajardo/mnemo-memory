CREATE TABLE mnemo_team.episodic_memory_candidates (
    candidate_sequence bigserial NOT NULL,
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    session_id uuid NOT NULL,
    task_id uuid NOT NULL,
    memory_id uuid NOT NULL,
    source_event_id uuid NOT NULL,
    proposal_index integer NOT NULL CHECK (proposal_index BETWEEN 0 AND 3),
    memory_kind text NOT NULL CHECK (
        memory_kind IN ('decision', 'failure', 'outcome', 'lesson', 'preference')
    ),
    claim text NOT NULL CHECK (claim <> '' AND length(claim) <= 1200),
    confidence double precision NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    sensitivity text NOT NULL CHECK (
        sensitivity IN ('normal', 'personal', 'confidential', 'restricted')
    ),
    status text NOT NULL CHECK (status = 'candidate'),
    extractor_version text NOT NULL CHECK (
        extractor_version <> '' AND length(extractor_version) <= 128
    ),
    provider_id text NOT NULL CHECK (provider_id <> '' AND length(provider_id) <= 128),
    model_id text NOT NULL CHECK (model_id <> '' AND length(model_id) <= 128),
    prompt_version text NOT NULL CHECK (
        prompt_version <> '' AND length(prompt_version) <= 128
    ),
    retention_json jsonb NOT NULL CHECK (jsonb_typeof(retention_json) = 'object'),
    created_at timestamptz NOT NULL,
    evidence_json jsonb NOT NULL CHECK (
        jsonb_typeof(evidence_json) = 'array'
        AND jsonb_array_length(evidence_json) BETWEEN 1 AND 64
    ),
    PRIMARY KEY (workspace_id, memory_id),
    UNIQUE (workspace_id, source_event_id, extractor_version, proposal_index),
    UNIQUE (workspace_id, candidate_sequence),
    FOREIGN KEY (workspace_id, project_id)
        REFERENCES mnemo_team.projects(workspace_id, project_id) ON DELETE RESTRICT,
    FOREIGN KEY (workspace_id, source_event_id)
        REFERENCES mnemo_team.task_activity_events(workspace_id, event_id) ON DELETE RESTRICT
);

CREATE INDEX episodic_candidate_scope_order
    ON mnemo_team.episodic_memory_candidates(
        workspace_id, project_id, owner_id, visibility, session_id, task_id,
        candidate_sequence DESC
    );

CREATE TABLE mnemo_team.episodic_candidate_reviews (
    action_sequence bigserial NOT NULL,
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    session_id uuid NOT NULL,
    task_id uuid NOT NULL,
    action_id uuid NOT NULL,
    candidate_id uuid NOT NULL,
    decision text NOT NULL CHECK (decision IN ('approved', 'rejected')),
    actor text NOT NULL CHECK (actor = 'user'),
    source_action_key text NOT NULL CHECK (
        source_action_key <> '' AND length(source_action_key) <= 256
    ),
    reason text NOT NULL CHECK (reason <> '' AND length(reason) <= 1200),
    reviewed_at timestamptz NOT NULL,
    evidence_json jsonb NOT NULL CHECK (
        jsonb_typeof(evidence_json) = 'array'
        AND jsonb_array_length(evidence_json) BETWEEN 1 AND 16
    ),
    PRIMARY KEY (workspace_id, action_id),
    UNIQUE (workspace_id, candidate_id),
    UNIQUE (
        workspace_id, project_id, owner_id, visibility, session_id, task_id, source_action_key
    ),
    UNIQUE (workspace_id, action_sequence),
    FOREIGN KEY (workspace_id, project_id)
        REFERENCES mnemo_team.projects(workspace_id, project_id) ON DELETE RESTRICT,
    FOREIGN KEY (workspace_id, candidate_id)
        REFERENCES mnemo_team.episodic_memory_candidates(workspace_id, memory_id)
        ON DELETE RESTRICT
);

CREATE INDEX episodic_review_scope_order
    ON mnemo_team.episodic_candidate_reviews(
        workspace_id, project_id, owner_id, visibility, session_id, task_id,
        action_sequence DESC
    );

CREATE TABLE mnemo_team.active_episodic_memories (
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    session_id uuid NOT NULL,
    task_id uuid NOT NULL,
    memory_id uuid NOT NULL,
    approval_action_id uuid NOT NULL,
    activated_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, memory_id),
    UNIQUE (workspace_id, approval_action_id),
    FOREIGN KEY (workspace_id, project_id)
        REFERENCES mnemo_team.projects(workspace_id, project_id) ON DELETE RESTRICT,
    FOREIGN KEY (workspace_id, memory_id)
        REFERENCES mnemo_team.episodic_memory_candidates(workspace_id, memory_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (workspace_id, approval_action_id)
        REFERENCES mnemo_team.episodic_candidate_reviews(workspace_id, action_id)
        ON DELETE RESTRICT
);

CREATE FUNCTION mnemo_team.ensure_episodic_candidate_source()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM mnemo_team.task_activity_events AS source
         WHERE source.workspace_id = NEW.workspace_id
           AND source.project_id = NEW.project_id
           AND source.owner_id = NEW.owner_id
           AND source.visibility = NEW.visibility
           AND source.session_id = NEW.session_id
           AND source.task_id = NEW.task_id
           AND source.event_id = NEW.source_event_id
           AND source.retention_json = NEW.retention_json
           AND source.evidence_json = NEW.evidence_json
    ) THEN
        RAISE EXCEPTION 'episodic candidate source authority mismatch'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER episodic_candidate_source_guard
BEFORE INSERT ON mnemo_team.episodic_memory_candidates
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_episodic_candidate_source();

CREATE FUNCTION mnemo_team.ensure_episodic_review_candidate()
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
           AND candidate.memory_id = NEW.candidate_id
    ) THEN
        RAISE EXCEPTION 'episodic review candidate scope mismatch' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER episodic_review_candidate_guard
BEFORE INSERT ON mnemo_team.episodic_candidate_reviews
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_episodic_review_candidate();

CREATE FUNCTION mnemo_team.ensure_active_episodic_approval()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM mnemo_team.episodic_candidate_reviews AS review
         WHERE review.workspace_id = NEW.workspace_id
           AND review.project_id = NEW.project_id
           AND review.owner_id = NEW.owner_id
           AND review.visibility = NEW.visibility
           AND review.session_id = NEW.session_id
           AND review.task_id = NEW.task_id
           AND review.candidate_id = NEW.memory_id
           AND review.action_id = NEW.approval_action_id
           AND review.decision = 'approved'
           AND review.reviewed_at = NEW.activated_at
    ) THEN
        RAISE EXCEPTION 'active episodic memory requires matching approval'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER active_episodic_approval_guard
BEFORE INSERT ON mnemo_team.active_episodic_memories
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_active_episodic_approval();

ALTER TABLE mnemo_team.episodic_memory_candidates ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.episodic_memory_candidates FORCE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.episodic_candidate_reviews ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.episodic_candidate_reviews FORCE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.active_episodic_memories ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.active_episodic_memories FORCE ROW LEVEL SECURITY;

CREATE POLICY episodic_candidates_access ON mnemo_team.episodic_memory_candidates
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));

CREATE POLICY episodic_reviews_access ON mnemo_team.episodic_candidate_reviews
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));

CREATE POLICY active_episodic_memories_access ON mnemo_team.active_episodic_memories
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));

REVOKE ALL ON ALL TABLES IN SCHEMA mnemo_team FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mnemo_team FROM PUBLIC;

INSERT INTO mnemo_team.schema_migrations(version, applied_at)
VALUES (6, CURRENT_TIMESTAMP);
