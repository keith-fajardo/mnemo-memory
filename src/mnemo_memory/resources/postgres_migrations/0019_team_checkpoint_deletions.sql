CREATE TABLE mnemo_team.checkpoint_deletions (
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    session_id uuid NOT NULL,
    task_id uuid NOT NULL,
    deletion_id uuid NOT NULL,
    checkpoint_id uuid NOT NULL,
    actor text NOT NULL CHECK (actor = 'user'),
    source_action_key text NOT NULL CHECK (
        source_action_key <> '' AND length(source_action_key) <= 256
    ),
    deleted_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, deletion_id),
    UNIQUE (workspace_id, checkpoint_id),
    UNIQUE (
        workspace_id, project_id, owner_id, visibility, session_id, task_id, source_action_key
    )
);

CREATE INDEX checkpoint_deletion_scope_idx ON mnemo_team.checkpoint_deletions(
    workspace_id, project_id, owner_id, visibility, session_id, task_id, deleted_at
);

CREATE FUNCTION mnemo_team.ensure_checkpoint_deletion_target()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM mnemo_team.checkpoint_aggregates AS aggregate
         WHERE aggregate.workspace_id = NEW.workspace_id
           AND aggregate.project_id = NEW.project_id
           AND aggregate.owner_id = NEW.owner_id
           AND aggregate.visibility = NEW.visibility
           AND aggregate.session_id = NEW.session_id
           AND aggregate.task_id = NEW.task_id
           AND aggregate.checkpoint_id = NEW.checkpoint_id
    ) THEN
        RAISE EXCEPTION 'checkpoint deletion target mismatch' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER checkpoint_deletion_target_guard
BEFORE INSERT ON mnemo_team.checkpoint_deletions
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_checkpoint_deletion_target();

CREATE FUNCTION mnemo_team.prevent_checkpoint_resurrection()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM mnemo_team.checkpoint_deletions AS deletion
         WHERE deletion.workspace_id = NEW.workspace_id
           AND deletion.project_id = NEW.project_id
           AND deletion.owner_id = NEW.owner_id
           AND deletion.visibility = NEW.visibility
           AND deletion.session_id = NEW.session_id
           AND deletion.task_id = NEW.task_id
           AND deletion.checkpoint_id = NEW.checkpoint_id
    ) THEN
        RAISE EXCEPTION 'checkpoint deletion prevents resurrection' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER checkpoint_resurrection_guard
BEFORE INSERT ON mnemo_team.checkpoint_aggregates
FOR EACH ROW EXECUTE FUNCTION mnemo_team.prevent_checkpoint_resurrection();

CREATE FUNCTION mnemo_team.require_checkpoint_deletion_tombstone()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM mnemo_team.checkpoint_deletions AS deletion
         WHERE deletion.workspace_id = OLD.workspace_id
           AND deletion.project_id = OLD.project_id
           AND deletion.owner_id = OLD.owner_id
           AND deletion.visibility = OLD.visibility
           AND deletion.session_id = OLD.session_id
           AND deletion.task_id = OLD.task_id
           AND deletion.checkpoint_id = OLD.checkpoint_id
    ) THEN
        RAISE EXCEPTION 'checkpoint payload deletion requires tombstone' USING ERRCODE = '23514';
    END IF;
    RETURN OLD;
END;
$$;

CREATE TRIGGER checkpoint_aggregate_delete_guard
BEFORE DELETE ON mnemo_team.checkpoint_aggregates
FOR EACH ROW EXECUTE FUNCTION mnemo_team.require_checkpoint_deletion_tombstone();
CREATE TRIGGER checkpoint_revision_delete_guard
BEFORE DELETE ON mnemo_team.checkpoint_revisions
FOR EACH ROW EXECUTE FUNCTION mnemo_team.require_checkpoint_deletion_tombstone();
CREATE TRIGGER checkpoint_event_delete_guard
BEFORE DELETE ON mnemo_team.checkpoint_lifecycle_events
FOR EACH ROW EXECUTE FUNCTION mnemo_team.require_checkpoint_deletion_tombstone();
CREATE TRIGGER checkpoint_observation_delete_guard
BEFORE DELETE ON mnemo_team.checkpoint_source_observations
FOR EACH ROW EXECUTE FUNCTION mnemo_team.require_checkpoint_deletion_tombstone();

CREATE OR REPLACE FUNCTION mnemo_team.ensure_task_activity_payload_purge()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE target_event_id uuid;
BEGIN
    IF TG_TABLE_NAME = 'event_outbox' THEN
        IF OLD.topic = 'checkpoint_lifecycle' THEN
            IF NOT EXISTS (
                SELECT 1
                  FROM mnemo_team.checkpoint_lifecycle_events AS event
                  JOIN mnemo_team.checkpoint_deletions AS deletion
                    ON deletion.workspace_id = event.workspace_id
                   AND deletion.project_id = event.project_id
                   AND deletion.owner_id = event.owner_id
                   AND deletion.visibility = event.visibility
                   AND deletion.session_id = event.session_id
                   AND deletion.task_id = event.task_id
                   AND deletion.checkpoint_id = event.checkpoint_id
                 WHERE event.workspace_id = OLD.workspace_id
                   AND event.event_id = OLD.source_event_id
            ) THEN
                RAISE EXCEPTION 'checkpoint outbox deletion requires tombstone'
                    USING ERRCODE = '23514';
            END IF;
            RETURN OLD;
        ELSIF OLD.topic <> 'task_activity' THEN
            RAISE EXCEPTION 'unsupported outbox deletion is not permitted'
                USING ERRCODE = '23514';
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

ALTER TABLE mnemo_team.checkpoint_deletions ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.checkpoint_deletions FORCE ROW LEVEL SECURITY;
CREATE POLICY checkpoint_deletion_access ON mnemo_team.checkpoint_deletions
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));

REVOKE ALL ON ALL TABLES IN SCHEMA mnemo_team FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mnemo_team FROM PUBLIC;

INSERT INTO mnemo_team.schema_migrations(version, applied_at)
VALUES (19, CURRENT_TIMESTAMP);
