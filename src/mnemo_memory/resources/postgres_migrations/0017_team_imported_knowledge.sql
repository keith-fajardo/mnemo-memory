ALTER TABLE mnemo_team.knowledge_document_sources
    ADD COLUMN import_source_document_id uuid,
    ADD COLUMN import_source_content_digest text,
    ADD COLUMN imported_at timestamptz,
    ADD CONSTRAINT knowledge_source_import_provenance CHECK (
        (import_source_document_id IS NULL AND import_source_content_digest IS NULL
            AND imported_at IS NULL)
        OR (import_source_document_id IS NOT NULL
            AND import_source_content_digest ~ '^sha256:[0-9a-f]{64}$'
            AND imported_at IS NOT NULL)
    );

CREATE UNIQUE INDEX knowledge_source_import_identity
    ON mnemo_team.knowledge_document_sources(workspace_id, import_source_document_id)
    WHERE import_source_document_id IS NOT NULL;

CREATE TABLE mnemo_team.imported_knowledge_deletions (
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    document_id uuid NOT NULL,
    source_document_id uuid NOT NULL,
    relative_path text NOT NULL CHECK (relative_path <> '' AND left(relative_path, 1) <> '/'),
    content_digest text NOT NULL CHECK (content_digest ~ '^sha256:[0-9a-f]{64}$'),
    deleted_at timestamptz NOT NULL,
    source_content_digest text NOT NULL CHECK (
        source_content_digest ~ '^sha256:[0-9a-f]{64}$'
    ),
    imported_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, document_id),
    UNIQUE (workspace_id, source_document_id),
    FOREIGN KEY (workspace_id, project_id)
        REFERENCES mnemo_team.projects(workspace_id, project_id) ON DELETE RESTRICT
);

CREATE INDEX imported_knowledge_deletion_scope
    ON mnemo_team.imported_knowledge_deletions(
        workspace_id, project_id, owner_id, visibility, document_id
    );

ALTER TABLE mnemo_team.imported_knowledge_deletions ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.imported_knowledge_deletions FORCE ROW LEVEL SECURITY;

CREATE POLICY imported_knowledge_deletion_access
ON mnemo_team.imported_knowledge_deletions
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));

CREATE FUNCTION mnemo_team.prevent_imported_knowledge_resurrection()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM mnemo_team.imported_knowledge_deletions AS deletion
         WHERE deletion.workspace_id = NEW.workspace_id
           AND deletion.project_id = NEW.project_id
           AND deletion.owner_id = NEW.owner_id
           AND deletion.visibility = NEW.visibility
           AND deletion.document_id = NEW.document_id
    ) THEN
        RAISE EXCEPTION 'imported knowledge deletion prevents resurrection'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER imported_knowledge_resurrection_guard
BEFORE INSERT ON mnemo_team.knowledge_document_sources
FOR EACH ROW EXECUTE FUNCTION mnemo_team.prevent_imported_knowledge_resurrection();

REVOKE ALL ON ALL TABLES IN SCHEMA mnemo_team FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mnemo_team FROM PUBLIC;

INSERT INTO mnemo_team.schema_migrations(version, applied_at)
VALUES (17, CURRENT_TIMESTAMP);
