ALTER TABLE mnemo_team.approved_episodic_events
    ADD COLUMN import_source_event_id uuid,
    ADD COLUMN import_source_content_digest text,
    ADD COLUMN imported_at timestamptz,
    ADD CONSTRAINT approved_event_import_provenance CHECK (
        (import_source_event_id IS NULL AND import_source_content_digest IS NULL
            AND imported_at IS NULL)
        OR (import_source_event_id IS NOT NULL
            AND import_source_content_digest ~ '^sha256:[0-9a-f]{64}$'
            AND imported_at IS NOT NULL)
    );

CREATE UNIQUE INDEX approved_event_import_source_identity
    ON mnemo_team.approved_episodic_events(workspace_id, import_source_event_id)
    WHERE import_source_event_id IS NOT NULL;

ALTER TABLE mnemo_team.approved_episodic_event_governance
    ADD COLUMN import_source_action_id uuid,
    ADD COLUMN import_source_target_event_id uuid,
    ADD COLUMN import_source_replacement_event_id uuid,
    ADD COLUMN import_source_content_digest text,
    ADD COLUMN imported_at timestamptz,
    ADD COLUMN imported_without_target_payload boolean NOT NULL DEFAULT false,
    ADD CONSTRAINT approved_governance_import_provenance CHECK (
        (
            import_source_action_id IS NULL
            AND import_source_target_event_id IS NULL
            AND import_source_replacement_event_id IS NULL
            AND import_source_content_digest IS NULL
            AND imported_at IS NULL
            AND NOT imported_without_target_payload
        ) OR (
            import_source_action_id IS NOT NULL
            AND import_source_target_event_id IS NOT NULL
            AND import_source_content_digest ~ '^sha256:[0-9a-f]{64}$'
            AND imported_at IS NOT NULL
            AND (
                (action_kind = 'corrected' AND import_source_replacement_event_id IS NOT NULL)
                OR (action_kind = 'retracted' AND import_source_replacement_event_id IS NULL)
            )
            AND (
                NOT imported_without_target_payload
                OR (action_kind = 'retracted' AND target_event_sequence = 0)
            )
        )
    );

CREATE UNIQUE INDEX approved_governance_import_source_identity
    ON mnemo_team.approved_episodic_event_governance(
        workspace_id, import_source_action_id
    ) WHERE import_source_action_id IS NOT NULL;

ALTER TABLE mnemo_team.approved_episodic_event_pin_actions
    ADD COLUMN import_source_action_id uuid,
    ADD COLUMN import_source_event_id uuid,
    ADD COLUMN import_source_content_digest text,
    ADD COLUMN imported_at timestamptz,
    ADD COLUMN imported_without_event_payload boolean NOT NULL DEFAULT false,
    ADD CONSTRAINT approved_pin_import_provenance CHECK (
        (
            import_source_action_id IS NULL
            AND import_source_event_id IS NULL
            AND import_source_content_digest IS NULL
            AND imported_at IS NULL
            AND NOT imported_without_event_payload
        ) OR (
            import_source_action_id IS NOT NULL
            AND import_source_event_id IS NOT NULL
            AND import_source_content_digest ~ '^sha256:[0-9a-f]{64}$'
            AND imported_at IS NOT NULL
        )
    );

CREATE UNIQUE INDEX approved_pin_import_source_identity
    ON mnemo_team.approved_episodic_event_pin_actions(
        workspace_id, import_source_action_id
    ) WHERE import_source_action_id IS NOT NULL;

CREATE OR REPLACE FUNCTION mnemo_team.ensure_approved_governance_scope()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    IF NEW.imported_without_target_payload THEN
        IF EXISTS (
            SELECT 1 FROM mnemo_team.approved_episodic_events AS target
             WHERE target.workspace_id = NEW.workspace_id
               AND target.event_id = NEW.target_event_id
        ) THEN
            RAISE EXCEPTION 'imported retraction unexpectedly retains target payload'
                USING ERRCODE = '23514';
        END IF;
    ELSIF NOT EXISTS (
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
        UNION ALL
        SELECT 1
          FROM mnemo_team.approved_episodic_event_governance AS retraction
         WHERE retraction.workspace_id = NEW.workspace_id
           AND retraction.project_id = NEW.project_id
           AND retraction.owner_id = NEW.owner_id
           AND retraction.visibility = NEW.visibility
           AND retraction.session_id = NEW.session_id
           AND retraction.task_id = NEW.task_id
           AND retraction.target_event_id = NEW.replacement_event_id
           AND retraction.action_kind = 'retracted'
           AND retraction.imported_without_target_payload
    ) THEN
        RAISE EXCEPTION 'approved event governance replacement scope mismatch'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION mnemo_team.ensure_approved_pin_scope()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    IF NEW.imported_without_event_payload THEN
        IF NOT EXISTS (
            SELECT 1 FROM mnemo_team.approved_episodic_event_governance AS action
             WHERE action.workspace_id = NEW.workspace_id
               AND action.project_id = NEW.project_id
               AND action.owner_id = NEW.owner_id
               AND action.visibility = NEW.visibility
               AND action.session_id = NEW.session_id
               AND action.task_id = NEW.task_id
               AND action.target_event_id = NEW.event_id
               AND action.action_kind = 'retracted'
               AND action.imported_without_target_payload
        ) THEN
            RAISE EXCEPTION 'imported pin has no erased retraction target'
                USING ERRCODE = '23514';
        END IF;
    ELSIF NOT EXISTS (
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

REVOKE ALL ON ALL TABLES IN SCHEMA mnemo_team FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mnemo_team FROM PUBLIC;

INSERT INTO mnemo_team.schema_migrations(version, applied_at)
VALUES (16, CURRENT_TIMESTAMP);
