CREATE EXTENSION IF NOT EXISTS vector;

DO $$
BEGIN
    IF (SELECT extversion FROM pg_catalog.pg_extension WHERE extname = 'vector') <> '0.8.5' THEN
        RAISE EXCEPTION 'Mnemo requires pgvector 0.8.5' USING ERRCODE = '0A000';
    END IF;
END;
$$;

CREATE TABLE mnemo_team.knowledge_sync_status (
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    last_synced_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, project_id, owner_id, visibility),
    FOREIGN KEY (workspace_id, project_id)
        REFERENCES mnemo_team.projects(workspace_id, project_id) ON DELETE RESTRICT
);

CREATE TABLE mnemo_team.knowledge_document_sources (
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    document_id uuid NOT NULL,
    relative_path text NOT NULL CHECK (relative_path <> '' AND left(relative_path, 1) <> '/'),
    content_digest text NOT NULL CHECK (content_digest ~ '^sha256:[0-9a-f]{64}$'),
    current_revision_id uuid,
    is_deleted boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL,
    deleted_at timestamptz,
    PRIMARY KEY (workspace_id, document_id),
    FOREIGN KEY (workspace_id, project_id)
        REFERENCES mnemo_team.projects(workspace_id, project_id) ON DELETE RESTRICT,
    CHECK ((NOT is_deleted AND deleted_at IS NULL)
        OR (is_deleted AND current_revision_id IS NULL AND deleted_at IS NOT NULL))
);

CREATE UNIQUE INDEX knowledge_document_active_path_unique
    ON mnemo_team.knowledge_document_sources(
        workspace_id, project_id, owner_id, visibility, relative_path
    ) WHERE NOT is_deleted;
CREATE INDEX knowledge_document_scope_order
    ON mnemo_team.knowledge_document_sources(
        workspace_id, project_id, owner_id, visibility, is_deleted, relative_path, document_id
    );

CREATE TABLE mnemo_team.knowledge_document_revisions (
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    revision_id uuid NOT NULL,
    document_id uuid NOT NULL,
    revision_number integer NOT NULL CHECK (revision_number >= 1),
    predecessor_revision_id uuid,
    source_kind text NOT NULL CHECK (source_kind IN ('markdown', 'obsidian')),
    relative_path text NOT NULL CHECK (relative_path <> '' AND left(relative_path, 1) <> '/'),
    content_digest text NOT NULL CHECK (content_digest ~ '^sha256:[0-9a-f]{64}$'),
    title text NOT NULL CHECK (title <> ''),
    frontmatter_json jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, revision_id),
    UNIQUE (workspace_id, document_id, revision_number),
    FOREIGN KEY (workspace_id, document_id)
        REFERENCES mnemo_team.knowledge_document_sources(workspace_id, document_id)
        ON DELETE CASCADE,
    FOREIGN KEY (workspace_id, predecessor_revision_id)
        REFERENCES mnemo_team.knowledge_document_revisions(workspace_id, revision_id)
        ON DELETE RESTRICT
);

ALTER TABLE mnemo_team.knowledge_document_sources
    ADD CONSTRAINT knowledge_source_current_revision
    FOREIGN KEY (workspace_id, current_revision_id)
    REFERENCES mnemo_team.knowledge_document_revisions(workspace_id, revision_id)
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE mnemo_team.knowledge_document_sections (
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    revision_id uuid NOT NULL,
    section_index integer NOT NULL CHECK (section_index >= 0),
    heading text NOT NULL CHECK (heading <> ''),
    heading_level integer NOT NULL CHECK (heading_level BETWEEN 0 AND 6),
    content text NOT NULL,
    PRIMARY KEY (workspace_id, revision_id, section_index),
    FOREIGN KEY (workspace_id, revision_id)
        REFERENCES mnemo_team.knowledge_document_revisions(workspace_id, revision_id)
        ON DELETE CASCADE
);

CREATE TABLE mnemo_team.knowledge_document_links (
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    revision_id uuid NOT NULL,
    link_target text NOT NULL CHECK (link_target <> ''),
    link_kind text NOT NULL CHECK (link_kind IN ('markdown', 'wiki')),
    PRIMARY KEY (workspace_id, revision_id, link_kind, link_target),
    FOREIGN KEY (workspace_id, revision_id)
        REFERENCES mnemo_team.knowledge_document_revisions(workspace_id, revision_id)
        ON DELETE CASCADE
);

CREATE TABLE mnemo_team.knowledge_document_tombstones (
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    document_id uuid NOT NULL,
    relative_path text NOT NULL CHECK (relative_path <> '' AND left(relative_path, 1) <> '/'),
    content_digest text NOT NULL CHECK (content_digest ~ '^sha256:[0-9a-f]{64}$'),
    deleted_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, document_id),
    FOREIGN KEY (workspace_id, document_id)
        REFERENCES mnemo_team.knowledge_document_sources(workspace_id, document_id)
        ON DELETE RESTRICT
);

CREATE INDEX knowledge_tombstone_scope_order
    ON mnemo_team.knowledge_document_tombstones(
        workspace_id, project_id, owner_id, visibility, deleted_at, document_id
    );

CREATE TABLE mnemo_team.knowledge_section_embeddings (
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    revision_id uuid NOT NULL,
    section_index integer NOT NULL CHECK (section_index >= 0),
    model_id text NOT NULL CHECK (model_id <> ''),
    section_digest text NOT NULL CHECK (section_digest ~ '^sha256:[0-9a-f]{64}$'),
    embedding vector NOT NULL CHECK (
        vector_dims(embedding) BETWEEN 8 AND 4096
        AND embedding::text !~ 'NaN|Infinity|-Infinity'
    ),
    PRIMARY KEY (workspace_id, revision_id, section_index, model_id),
    FOREIGN KEY (workspace_id, revision_id, section_index)
        REFERENCES mnemo_team.knowledge_document_sections(
            workspace_id, revision_id, section_index
        ) ON DELETE CASCADE
);

CREATE INDEX knowledge_embedding_scope_order
    ON mnemo_team.knowledge_section_embeddings(
        workspace_id, project_id, owner_id, visibility, model_id, revision_id, section_index
    );

CREATE FUNCTION mnemo_team.ensure_knowledge_revision_chain()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    IF NEW.revision_number = 1 THEN
        IF NEW.predecessor_revision_id IS NOT NULL THEN
            RAISE EXCEPTION 'first knowledge revision cannot have a predecessor'
                USING ERRCODE = '23514';
        END IF;
    ELSIF NOT EXISTS (
        SELECT 1
          FROM mnemo_team.knowledge_document_revisions AS prior
         WHERE prior.workspace_id = NEW.workspace_id
           AND prior.revision_id = NEW.predecessor_revision_id
           AND prior.document_id = NEW.document_id
           AND prior.revision_number = NEW.revision_number - 1
    ) THEN
        RAISE EXCEPTION 'knowledge revision predecessor mismatch' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER knowledge_revision_chain_guard
BEFORE INSERT ON mnemo_team.knowledge_document_revisions
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_knowledge_revision_chain();

CREATE FUNCTION mnemo_team.ensure_knowledge_source_state()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    source_state record;
BEGIN
    SELECT source.is_deleted, source.current_revision_id, source.deleted_at
      INTO source_state
      FROM mnemo_team.knowledge_document_sources AS source
     WHERE source.workspace_id = NEW.workspace_id
       AND source.document_id = NEW.document_id;
    IF FOUND AND (
        (NOT source_state.is_deleted AND source_state.current_revision_id IS NULL)
        OR (source_state.is_deleted AND (
            source_state.current_revision_id IS NOT NULL OR source_state.deleted_at IS NULL
        ))
    ) THEN
        RAISE EXCEPTION 'knowledge source lifecycle state is invalid' USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER knowledge_source_state_guard
AFTER INSERT OR UPDATE ON mnemo_team.knowledge_document_sources
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_knowledge_source_state();

CREATE FUNCTION mnemo_team.ensure_knowledge_row_scope()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    source_scope record;
BEGIN
    SELECT source.project_id, source.owner_id, source.visibility
      INTO source_scope
      FROM mnemo_team.knowledge_document_sources AS source
     WHERE source.workspace_id = NEW.workspace_id
       AND source.document_id = NEW.document_id;
    IF NOT FOUND
       OR source_scope.project_id IS DISTINCT FROM NEW.project_id
       OR source_scope.owner_id IS DISTINCT FROM NEW.owner_id
       OR source_scope.visibility IS DISTINCT FROM NEW.visibility
    THEN
        RAISE EXCEPTION 'knowledge revision scope mismatch' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER knowledge_revision_scope_guard
BEFORE INSERT ON mnemo_team.knowledge_document_revisions
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_knowledge_row_scope();

CREATE FUNCTION mnemo_team.ensure_knowledge_child_scope()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    revision_scope record;
BEGIN
    SELECT revision.project_id, revision.owner_id, revision.visibility
      INTO revision_scope
      FROM mnemo_team.knowledge_document_revisions AS revision
     WHERE revision.workspace_id = NEW.workspace_id
       AND revision.revision_id = NEW.revision_id;
    IF NOT FOUND
       OR revision_scope.project_id IS DISTINCT FROM NEW.project_id
       OR revision_scope.owner_id IS DISTINCT FROM NEW.owner_id
       OR revision_scope.visibility IS DISTINCT FROM NEW.visibility
    THEN
        RAISE EXCEPTION 'knowledge child scope mismatch' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER knowledge_section_scope_guard
BEFORE INSERT ON mnemo_team.knowledge_document_sections
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_knowledge_child_scope();

CREATE TRIGGER knowledge_link_scope_guard
BEFORE INSERT ON mnemo_team.knowledge_document_links
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_knowledge_child_scope();

CREATE FUNCTION mnemo_team.ensure_knowledge_tombstone_scope()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM mnemo_team.knowledge_document_sources AS source
         WHERE source.workspace_id = NEW.workspace_id
           AND source.project_id = NEW.project_id
           AND source.owner_id = NEW.owner_id
           AND source.visibility = NEW.visibility
           AND source.document_id = NEW.document_id
           AND source.is_deleted
           AND source.current_revision_id IS NULL
    ) THEN
        RAISE EXCEPTION 'knowledge tombstone scope mismatch' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER knowledge_tombstone_scope_guard
BEFORE INSERT OR UPDATE ON mnemo_team.knowledge_document_tombstones
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_knowledge_tombstone_scope();

CREATE FUNCTION mnemo_team.ensure_knowledge_embedding_current()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM mnemo_team.knowledge_document_sources AS source
          JOIN mnemo_team.knowledge_document_sections AS section
            ON section.workspace_id = source.workspace_id
           AND section.revision_id = source.current_revision_id
         WHERE source.workspace_id = NEW.workspace_id
           AND source.project_id = NEW.project_id
           AND source.owner_id = NEW.owner_id
           AND source.visibility = NEW.visibility
           AND source.current_revision_id = NEW.revision_id
           AND section.section_index = NEW.section_index
           AND NOT source.is_deleted
    ) THEN
        RAISE EXCEPTION 'knowledge embedding is not current' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER knowledge_embedding_current_guard
BEFORE INSERT OR UPDATE ON mnemo_team.knowledge_section_embeddings
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_knowledge_embedding_current();

ALTER TABLE mnemo_team.knowledge_sync_status ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.knowledge_sync_status FORCE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.knowledge_document_sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.knowledge_document_sources FORCE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.knowledge_document_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.knowledge_document_revisions FORCE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.knowledge_document_sections ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.knowledge_document_sections FORCE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.knowledge_document_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.knowledge_document_links FORCE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.knowledge_document_tombstones ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.knowledge_document_tombstones FORCE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.knowledge_section_embeddings ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.knowledge_section_embeddings FORCE ROW LEVEL SECURITY;

CREATE POLICY knowledge_sync_status_access ON mnemo_team.knowledge_sync_status
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));

CREATE POLICY knowledge_document_sources_access ON mnemo_team.knowledge_document_sources
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));

CREATE POLICY knowledge_document_revisions_access ON mnemo_team.knowledge_document_revisions
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));

CREATE POLICY knowledge_document_sections_access ON mnemo_team.knowledge_document_sections
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));

CREATE POLICY knowledge_document_links_access ON mnemo_team.knowledge_document_links
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));

CREATE POLICY knowledge_document_tombstones_access ON mnemo_team.knowledge_document_tombstones
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));

CREATE POLICY knowledge_section_embeddings_access ON mnemo_team.knowledge_section_embeddings
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));

REVOKE ALL ON ALL TABLES IN SCHEMA mnemo_team FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mnemo_team FROM PUBLIC;

INSERT INTO mnemo_team.schema_migrations(version, applied_at)
VALUES (2, CURRENT_TIMESTAMP);
