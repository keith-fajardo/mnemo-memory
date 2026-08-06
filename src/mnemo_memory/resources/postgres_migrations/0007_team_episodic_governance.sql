CREATE TABLE mnemo_team.episodic_memory_governance (
    action_sequence bigserial NOT NULL,
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    session_id uuid NOT NULL,
    task_id uuid NOT NULL,
    action_id uuid NOT NULL,
    memory_id uuid NOT NULL,
    action_kind text NOT NULL CHECK (action_kind IN ('corrected', 'retracted')),
    actor text NOT NULL CHECK (actor = 'user'),
    expected_revision_id uuid NOT NULL,
    source_action_key text NOT NULL CHECK (
        source_action_key <> '' AND length(source_action_key) <= 256
    ),
    reason text NOT NULL CHECK (reason <> '' AND length(reason) <= 1200),
    corrected_claim text CHECK (
        corrected_claim IS NULL OR (corrected_claim <> '' AND length(corrected_claim) <= 1200)
    ),
    corrected_sensitivity text CHECK (
        corrected_sensitivity IS NULL
        OR corrected_sensitivity IN ('normal', 'personal', 'confidential', 'restricted')
    ),
    occurred_at timestamptz NOT NULL,
    evidence_json jsonb NOT NULL CHECK (
        jsonb_typeof(evidence_json) = 'array'
        AND jsonb_array_length(evidence_json) BETWEEN 1 AND 16
    ),
    PRIMARY KEY (workspace_id, action_id),
    UNIQUE (workspace_id, memory_id, expected_revision_id),
    UNIQUE (
        workspace_id, project_id, owner_id, visibility, session_id, task_id, source_action_key
    ),
    UNIQUE (workspace_id, action_sequence),
    FOREIGN KEY (workspace_id, project_id)
        REFERENCES mnemo_team.projects(workspace_id, project_id) ON DELETE RESTRICT,
    FOREIGN KEY (workspace_id, memory_id)
        REFERENCES mnemo_team.active_episodic_memories(workspace_id, memory_id)
        ON DELETE RESTRICT,
    CHECK (
        (action_kind = 'corrected' AND corrected_claim IS NOT NULL
            AND corrected_sensitivity IS NOT NULL)
        OR (action_kind = 'retracted' AND corrected_claim IS NULL
            AND corrected_sensitivity IS NULL)
    )
);

CREATE INDEX episodic_governance_scope_order
    ON mnemo_team.episodic_memory_governance(
        workspace_id, project_id, owner_id, visibility, session_id, task_id,
        memory_id, action_sequence ASC
    );

CREATE FUNCTION mnemo_team.ensure_episodic_governance_scope()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM mnemo_team.active_episodic_memories AS active
         WHERE active.workspace_id = NEW.workspace_id
           AND active.project_id = NEW.project_id
           AND active.owner_id = NEW.owner_id
           AND active.visibility = NEW.visibility
           AND active.session_id = NEW.session_id
           AND active.task_id = NEW.task_id
           AND active.memory_id = NEW.memory_id
    ) THEN
        RAISE EXCEPTION 'episodic governance memory scope mismatch'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER episodic_governance_scope_guard
BEFORE INSERT ON mnemo_team.episodic_memory_governance
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_episodic_governance_scope();

ALTER TABLE mnemo_team.episodic_memory_governance ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.episodic_memory_governance FORCE ROW LEVEL SECURITY;

CREATE POLICY episodic_governance_access ON mnemo_team.episodic_memory_governance
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));

REVOKE ALL ON ALL TABLES IN SCHEMA mnemo_team FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mnemo_team FROM PUBLIC;

INSERT INTO mnemo_team.schema_migrations(version, applied_at)
VALUES (7, CURRENT_TIMESTAMP);
