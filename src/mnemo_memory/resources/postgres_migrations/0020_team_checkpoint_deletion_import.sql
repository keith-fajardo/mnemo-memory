ALTER TABLE mnemo_team.checkpoint_deletions
    ADD COLUMN import_source_deletion_id uuid NULL,
    ADD COLUMN import_source_content_digest text NULL,
    ADD COLUMN imported_at timestamptz NULL,
    ADD CONSTRAINT checkpoint_deletion_import_provenance_complete CHECK (
        (
            import_source_deletion_id IS NULL
            AND import_source_content_digest IS NULL
            AND imported_at IS NULL
        )
        OR
        (
            import_source_deletion_id IS NOT NULL
            AND import_source_content_digest ~ '^sha256:[0-9a-f]{64}$'
            AND imported_at IS NOT NULL
        )
    );

CREATE UNIQUE INDEX checkpoint_deletion_import_source_unique
ON mnemo_team.checkpoint_deletions(
    workspace_id,
    project_id,
    owner_id,
    visibility,
    session_id,
    task_id,
    import_source_deletion_id,
    import_source_content_digest
)
WHERE import_source_deletion_id IS NOT NULL;

CREATE OR REPLACE FUNCTION mnemo_team.ensure_checkpoint_deletion_target()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.import_source_deletion_id IS NOT NULL THEN
        RETURN NEW;
    END IF;
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

REVOKE ALL ON ALL TABLES IN SCHEMA mnemo_team FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mnemo_team FROM PUBLIC;

INSERT INTO mnemo_team.schema_migrations(version, applied_at)
VALUES (20, CURRENT_TIMESTAMP);
