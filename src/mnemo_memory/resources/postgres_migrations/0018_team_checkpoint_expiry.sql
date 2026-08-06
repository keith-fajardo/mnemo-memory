ALTER TABLE mnemo_team.checkpoint_aggregates
    DROP CONSTRAINT checkpoint_aggregates_lifecycle_status_check,
    ADD CONSTRAINT checkpoint_aggregates_lifecycle_status_check
        CHECK (lifecycle_status IN ('active', 'completed', 'abandoned', 'expired'));

ALTER TABLE mnemo_team.checkpoint_revisions
    DROP CONSTRAINT checkpoint_revisions_status_check,
    ADD CONSTRAINT checkpoint_revisions_status_check
        CHECK (status IN ('active', 'completed', 'abandoned', 'expired'));

ALTER TABLE mnemo_team.checkpoint_lifecycle_events
    DROP CONSTRAINT checkpoint_lifecycle_events_event_kind_check,
    ADD CONSTRAINT checkpoint_lifecycle_events_event_kind_check CHECK (event_kind IN (
        'checkpoint_created', 'checkpoint_revised', 'checkpoint_completed',
        'checkpoint_abandoned', 'checkpoint_expired', 'checkpoint_lesson_recorded'
    ));

CREATE OR REPLACE FUNCTION mnemo_team.ensure_checkpoint_event_state()
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
       OR (NEW.event_kind = 'checkpoint_expired' AND revision_state.status <> 'expired')
    THEN
        RAISE EXCEPTION 'checkpoint lifecycle event kind mismatch' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION mnemo_team.ensure_checkpoint_event_state() FROM PUBLIC;

INSERT INTO mnemo_team.schema_migrations(version, applied_at)
VALUES (18, CURRENT_TIMESTAMP);
