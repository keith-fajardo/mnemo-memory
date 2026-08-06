ALTER TABLE mnemo_team.knowledge_document_sources
    ADD COLUMN source_owner_id uuid,
    ADD COLUMN source_owner_authenticated boolean NOT NULL DEFAULT false;

UPDATE mnemo_team.knowledge_document_sources
   SET source_owner_id = owner_id;

ALTER TABLE mnemo_team.knowledge_document_sources
    ALTER COLUMN source_owner_id SET NOT NULL;

ALTER TABLE mnemo_team.knowledge_document_revisions
    ADD COLUMN authored_by_id uuid,
    ADD COLUMN author_authenticated boolean NOT NULL DEFAULT false;

UPDATE mnemo_team.knowledge_document_revisions
   SET authored_by_id = owner_id;

ALTER TABLE mnemo_team.knowledge_document_revisions
    ALTER COLUMN authored_by_id SET NOT NULL;

CREATE TABLE mnemo_team.knowledge_source_approvals (
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    document_id uuid NOT NULL,
    approval_id uuid NOT NULL,
    expected_revision_id uuid NOT NULL,
    approved_by_id uuid NOT NULL,
    source_action_key text NOT NULL CHECK (
        source_action_key <> '' AND length(source_action_key) <= 256
    ),
    approved_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, document_id),
    UNIQUE (workspace_id, approval_id),
    UNIQUE (workspace_id, source_action_key),
    FOREIGN KEY (workspace_id, document_id)
        REFERENCES mnemo_team.knowledge_document_sources(workspace_id, document_id)
        ON DELETE RESTRICT
);

CREATE INDEX knowledge_source_approval_scope_order
    ON mnemo_team.knowledge_source_approvals(
        workspace_id, project_id, owner_id, visibility, approved_at, document_id
    );

CREATE FUNCTION mnemo_team.can_approve_knowledge_source(
    target_workspace uuid,
    target_project uuid
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
    SELECT mnemo_team.current_operation() = 'approve_source'
       AND mnemo_team.current_workspace() = target_workspace
       AND (
            EXISTS (
                SELECT 1
                  FROM mnemo_team.workspace_memberships AS membership
                 WHERE membership.workspace_id = target_workspace
                   AND membership.principal_id = mnemo_team.current_principal()
                   AND membership.status = 'active'
                   AND membership.role IN ('owner', 'admin')
            )
            OR EXISTS (
                SELECT 1
                  FROM mnemo_team.projects AS project
                 WHERE project.workspace_id = target_workspace
                   AND project.project_id = target_project
                   AND project.owner_id = mnemo_team.current_principal()
            )
            OR EXISTS (
                SELECT 1
                  FROM mnemo_team.project_memberships AS membership
                 WHERE membership.workspace_id = target_workspace
                   AND membership.project_id = target_project
                   AND membership.principal_id = mnemo_team.current_principal()
                   AND membership.status = 'active'
                   AND membership.role = 'maintainer'
            )
       )
$$;

CREATE FUNCTION mnemo_team.ensure_knowledge_source_owner()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF mnemo_team.current_operation() <> 'contribute'
           OR NEW.source_owner_id IS DISTINCT FROM mnemo_team.current_principal()
           OR NOT NEW.source_owner_authenticated
        THEN
            RAISE EXCEPTION 'knowledge source owner must match its creator'
                USING ERRCODE = '42501';
        END IF;
    ELSIF NEW.source_owner_id IS DISTINCT FROM OLD.source_owner_id
       OR NEW.source_owner_authenticated IS DISTINCT FROM OLD.source_owner_authenticated
    THEN
        RAISE EXCEPTION 'knowledge source owner is immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER knowledge_source_owner_guard
BEFORE INSERT OR UPDATE ON mnemo_team.knowledge_document_sources
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_knowledge_source_owner();

CREATE FUNCTION mnemo_team.ensure_knowledge_revision_author()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    IF mnemo_team.current_operation() <> 'contribute'
       OR NEW.authored_by_id IS DISTINCT FROM mnemo_team.current_principal()
       OR NOT NEW.author_authenticated
    THEN
        RAISE EXCEPTION 'knowledge revision author must match its contributor'
            USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER knowledge_revision_author_guard
BEFORE INSERT ON mnemo_team.knowledge_document_revisions
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_knowledge_revision_author();

CREATE FUNCTION mnemo_team.ensure_knowledge_source_approval()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    IF mnemo_team.current_operation() <> 'approve_source'
       OR NEW.approved_by_id IS DISTINCT FROM mnemo_team.current_principal()
       OR NOT EXISTS (
            SELECT 1
              FROM mnemo_team.knowledge_document_sources AS source
             WHERE source.workspace_id = NEW.workspace_id
               AND source.project_id = NEW.project_id
               AND source.owner_id = NEW.owner_id
               AND source.visibility = NEW.visibility
               AND source.document_id = NEW.document_id
               AND source.current_revision_id = NEW.expected_revision_id
               AND NOT source.is_deleted
       )
    THEN
        RAISE EXCEPTION 'knowledge source approval target is invalid'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER knowledge_source_approval_guard
BEFORE INSERT ON mnemo_team.knowledge_source_approvals
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_knowledge_source_approval();

ALTER TABLE mnemo_team.knowledge_source_approvals ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.knowledge_source_approvals FORCE ROW LEVEL SECURITY;

DROP POLICY knowledge_document_sources_access
ON mnemo_team.knowledge_document_sources;

CREATE POLICY knowledge_document_sources_access
ON mnemo_team.knowledge_document_sources
USING (
    mnemo_team.authorized(workspace_id, project_id, owner_id, visibility)
    OR mnemo_team.can_approve_knowledge_source(workspace_id, project_id)
)
WITH CHECK (
    mnemo_team.authorized(workspace_id, project_id, owner_id, visibility)
);

CREATE POLICY knowledge_source_approvals_select
ON mnemo_team.knowledge_source_approvals
FOR SELECT USING (
    mnemo_team.authorized(workspace_id, project_id, owner_id, visibility)
    OR mnemo_team.can_approve_knowledge_source(workspace_id, project_id)
);

CREATE POLICY knowledge_source_approvals_insert
ON mnemo_team.knowledge_source_approvals
FOR INSERT WITH CHECK (
    mnemo_team.current_operation() = 'approve_source'
    AND approved_by_id = mnemo_team.current_principal()
    AND mnemo_team.can_approve_knowledge_source(workspace_id, project_id)
);

REVOKE ALL ON mnemo_team.knowledge_source_approvals FROM PUBLIC;
REVOKE ALL ON FUNCTION mnemo_team.can_approve_knowledge_source(uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION mnemo_team.ensure_knowledge_source_owner() FROM PUBLIC;
REVOKE ALL ON FUNCTION mnemo_team.ensure_knowledge_revision_author() FROM PUBLIC;
REVOKE ALL ON FUNCTION mnemo_team.ensure_knowledge_source_approval() FROM PUBLIC;

INSERT INTO mnemo_team.schema_migrations(version, applied_at)
VALUES (21, CURRENT_TIMESTAMP);
