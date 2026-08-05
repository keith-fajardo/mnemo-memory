CREATE SCHEMA mnemo_team;

CREATE TABLE mnemo_team.schema_migrations (
    version integer PRIMARY KEY,
    applied_at timestamptz NOT NULL
);

CREATE TABLE mnemo_team.workspaces (
    workspace_id uuid PRIMARY KEY,
    owner_id uuid NOT NULL,
    created_at timestamptz NOT NULL
);

CREATE TABLE mnemo_team.workspace_memberships (
    workspace_id uuid NOT NULL REFERENCES mnemo_team.workspaces(workspace_id) ON DELETE RESTRICT,
    principal_id uuid NOT NULL,
    role text NOT NULL CHECK (role IN ('owner', 'admin', 'editor', 'viewer')),
    status text NOT NULL CHECK (status IN ('active', 'suspended')),
    owner_marker boolean GENERATED ALWAYS AS (
        CASE WHEN role = 'owner' THEN true ELSE NULL END
    ) STORED,
    PRIMARY KEY (workspace_id, principal_id),
    UNIQUE (workspace_id, owner_marker) DEFERRABLE INITIALLY DEFERRED,
    CHECK (role <> 'owner' OR status = 'active')
);

ALTER TABLE mnemo_team.workspaces
    ADD CONSTRAINT workspace_owner_membership
    FOREIGN KEY (workspace_id, owner_id)
    REFERENCES mnemo_team.workspace_memberships(workspace_id, principal_id)
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE mnemo_team.projects (
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('private', 'workspace')),
    PRIMARY KEY (workspace_id, project_id),
    FOREIGN KEY (workspace_id) REFERENCES mnemo_team.workspaces(workspace_id) ON DELETE RESTRICT,
    FOREIGN KEY (workspace_id, owner_id)
        REFERENCES mnemo_team.workspace_memberships(workspace_id, principal_id)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE mnemo_team.project_memberships (
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    principal_id uuid NOT NULL,
    role text NOT NULL CHECK (role IN ('maintainer', 'contributor', 'viewer')),
    status text NOT NULL CHECK (status IN ('active', 'suspended')),
    PRIMARY KEY (workspace_id, project_id, principal_id),
    FOREIGN KEY (workspace_id, project_id)
        REFERENCES mnemo_team.projects(workspace_id, project_id) ON DELETE RESTRICT,
    FOREIGN KEY (workspace_id, principal_id)
        REFERENCES mnemo_team.workspace_memberships(workspace_id, principal_id)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE mnemo_team.audit_events (
    audit_sequence bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    event_id uuid PRIMARY KEY,
    request_id uuid NOT NULL,
    workspace_id uuid NOT NULL REFERENCES mnemo_team.workspaces(workspace_id) ON DELETE RESTRICT,
    actor_id uuid NOT NULL,
    action text NOT NULL CHECK (
        action IN (
            'workspace_created',
            'workspace_membership_changed',
            'workspace_ownership_transferred',
            'project_created',
            'project_visibility_changed',
            'project_membership_changed'
        )
    ),
    occurred_at timestamptz NOT NULL,
    project_id uuid,
    subject_principal_id uuid,
    mutation_fingerprint text NOT NULL CHECK (mutation_fingerprint ~ '^[0-9a-f]{64}$'),
    UNIQUE (workspace_id, request_id),
    FOREIGN KEY (workspace_id, project_id)
        REFERENCES mnemo_team.projects(workspace_id, project_id)
        DEFERRABLE INITIALLY DEFERRED,
    CHECK (
        (action IN ('project_created', 'project_visibility_changed', 'project_membership_changed'))
        = (project_id IS NOT NULL)
    ),
    CHECK (
        (action IN (
            'workspace_membership_changed',
            'workspace_ownership_transferred',
            'project_membership_changed'
        )) = (subject_principal_id IS NOT NULL)
    )
);

CREATE INDEX audit_events_workspace_order
    ON mnemo_team.audit_events(workspace_id, occurred_at, event_id);

CREATE FUNCTION mnemo_team.current_uuid(setting_name text)
RETURNS uuid
LANGUAGE plpgsql
STABLE
SET search_path = pg_catalog
AS $$
DECLARE
    raw_value text;
BEGIN
    raw_value := pg_catalog.current_setting(setting_name, true);
    IF raw_value IS NULL OR raw_value = '' THEN
        RETURN NULL;
    END IF;
    RETURN raw_value::uuid;
EXCEPTION WHEN invalid_text_representation THEN
    RETURN NULL;
END;
$$;

CREATE FUNCTION mnemo_team.current_principal()
RETURNS uuid
LANGUAGE sql
STABLE
SET search_path = pg_catalog
AS $$
    SELECT mnemo_team.current_uuid('mnemo.principal_id')
$$;

CREATE FUNCTION mnemo_team.current_workspace()
RETURNS uuid
LANGUAGE sql
STABLE
SET search_path = pg_catalog
AS $$
    SELECT mnemo_team.current_uuid('mnemo.workspace_id')
$$;

CREATE FUNCTION mnemo_team.current_operation()
RETURNS text
LANGUAGE sql
STABLE
SET search_path = pg_catalog
AS $$
    SELECT CASE
        WHEN pg_catalog.current_setting('mnemo.operation', true) IN (
            'read',
            'contribute',
            'manage_project',
            'manage_membership',
            'manage_workspace',
            'approve_source'
        ) THEN pg_catalog.current_setting('mnemo.operation', true)
        ELSE NULL
    END
$$;

CREATE FUNCTION mnemo_team.workspace_role_allowed(role_name text, operation_name text)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
SET search_path = pg_catalog
AS $$
    SELECT CASE role_name
        WHEN 'owner' THEN operation_name IN (
            'read', 'contribute', 'manage_project', 'manage_membership',
            'manage_workspace', 'approve_source'
        )
        WHEN 'admin' THEN operation_name IN (
            'read', 'contribute', 'manage_project', 'manage_membership', 'approve_source'
        )
        WHEN 'editor' THEN operation_name IN ('read', 'contribute')
        WHEN 'viewer' THEN operation_name = 'read'
        ELSE false
    END
$$;

CREATE FUNCTION mnemo_team.project_role_allowed(role_name text, operation_name text)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
SET search_path = pg_catalog
AS $$
    SELECT CASE role_name
        WHEN 'maintainer' THEN operation_name IN (
            'read', 'contribute', 'manage_project', 'approve_source'
        )
        WHEN 'contributor' THEN operation_name IN ('read', 'contribute')
        WHEN 'viewer' THEN operation_name = 'read'
        ELSE false
    END
$$;

CREATE FUNCTION mnemo_team.is_active_workspace_member(
    target_workspace uuid,
    target_principal uuid
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM mnemo_team.workspace_memberships AS membership
        WHERE membership.workspace_id = target_workspace
          AND membership.principal_id = target_principal
          AND membership.status = 'active'
    )
$$;

CREATE FUNCTION mnemo_team.authorized(
    target_workspace uuid,
    target_project uuid,
    item_owner uuid,
    item_visibility text
)
RETURNS boolean
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    principal uuid := mnemo_team.current_principal();
    selected_workspace uuid := mnemo_team.current_workspace();
    operation_name text := mnemo_team.current_operation();
    workspace_role text;
    project_owner uuid;
    project_visibility text;
    project_role text;
BEGIN
    IF principal IS NULL
        OR selected_workspace IS NULL
        OR operation_name IS NULL
        OR target_workspace IS DISTINCT FROM selected_workspace
        OR item_visibility NOT IN ('owner', 'workspace', 'project')
    THEN
        RETURN false;
    END IF;

    SELECT membership.role
      INTO workspace_role
      FROM mnemo_team.workspace_memberships AS membership
     WHERE membership.workspace_id = target_workspace
       AND membership.principal_id = principal
       AND membership.status = 'active';
    IF NOT FOUND THEN
        RETURN false;
    END IF;

    IF item_visibility = 'owner' AND item_owner IS DISTINCT FROM principal THEN
        RETURN false;
    END IF;

    IF operation_name IN ('manage_membership', 'manage_workspace') THEN
        RETURN mnemo_team.workspace_role_allowed(workspace_role, operation_name);
    END IF;

    IF target_project IS NULL THEN
        RETURN mnemo_team.workspace_role_allowed(workspace_role, operation_name);
    END IF;

    SELECT project.owner_id, project.visibility
      INTO project_owner, project_visibility
      FROM mnemo_team.projects AS project
     WHERE project.workspace_id = target_workspace
       AND project.project_id = target_project;
    IF NOT FOUND THEN
        RETURN false;
    END IF;

    IF workspace_role IN ('owner', 'admin')
        OR project_owner = principal
        OR project_visibility = 'workspace'
    THEN
        RETURN mnemo_team.workspace_role_allowed(workspace_role, operation_name);
    END IF;

    SELECT membership.role
      INTO project_role
      FROM mnemo_team.project_memberships AS membership
     WHERE membership.workspace_id = target_workspace
       AND membership.project_id = target_project
       AND membership.principal_id = principal
       AND membership.status = 'active';
    IF NOT FOUND THEN
        RETURN false;
    END IF;
    RETURN mnemo_team.project_role_allowed(project_role, operation_name);
END;
$$;

CREATE FUNCTION mnemo_team.ensure_workspace_owner()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
DECLARE
    affected_workspace uuid := CASE WHEN TG_OP = 'DELETE' THEN OLD.workspace_id ELSE NEW.workspace_id END;
BEGIN
    IF EXISTS (SELECT 1 FROM mnemo_team.workspaces WHERE workspace_id = affected_workspace)
       AND NOT EXISTS (
           SELECT 1
             FROM mnemo_team.workspaces AS workspace
             JOIN mnemo_team.workspace_memberships AS membership
               ON membership.workspace_id = workspace.workspace_id
              AND membership.principal_id = workspace.owner_id
              AND membership.role = 'owner'
              AND membership.status = 'active'
            WHERE workspace.workspace_id = affected_workspace
       )
    THEN
        RAISE EXCEPTION 'workspace must have exactly one active owner' USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER workspaces_require_owner
AFTER INSERT OR UPDATE ON mnemo_team.workspaces
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_workspace_owner();

CREATE CONSTRAINT TRIGGER workspace_memberships_preserve_owner
AFTER INSERT OR UPDATE OR DELETE ON mnemo_team.workspace_memberships
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_workspace_owner();

CREATE FUNCTION mnemo_team.ensure_active_project_authority()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
DECLARE
    affected_workspace uuid := CASE WHEN TG_OP = 'DELETE' THEN OLD.workspace_id ELSE NEW.workspace_id END;
    affected_principal uuid := CASE WHEN TG_OP = 'DELETE' THEN OLD.principal_id ELSE NEW.principal_id END;
BEGIN
    IF EXISTS (
        SELECT 1
          FROM mnemo_team.project_memberships AS project_membership
          LEFT JOIN mnemo_team.workspace_memberships AS workspace_membership
            ON workspace_membership.workspace_id = project_membership.workspace_id
           AND workspace_membership.principal_id = project_membership.principal_id
           AND workspace_membership.status = 'active'
         WHERE project_membership.workspace_id = affected_workspace
           AND project_membership.principal_id = affected_principal
           AND project_membership.status = 'active'
           AND workspace_membership.principal_id IS NULL
    ) OR EXISTS (
        SELECT 1
          FROM mnemo_team.projects AS project
          LEFT JOIN mnemo_team.workspace_memberships AS workspace_membership
            ON workspace_membership.workspace_id = project.workspace_id
           AND workspace_membership.principal_id = project.owner_id
           AND workspace_membership.status = 'active'
         WHERE project.workspace_id = affected_workspace
           AND project.owner_id = affected_principal
           AND workspace_membership.principal_id IS NULL
    )
    THEN
        RAISE EXCEPTION 'project authority requires active workspace membership'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER workspace_memberships_preserve_project_authority
AFTER INSERT OR UPDATE OR DELETE ON mnemo_team.workspace_memberships
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_active_project_authority();

CREATE CONSTRAINT TRIGGER project_memberships_require_active_workspace_member
AFTER INSERT OR UPDATE ON mnemo_team.project_memberships
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_active_project_authority();

CREATE FUNCTION mnemo_team.ensure_project_owner_active()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
    IF NOT mnemo_team.is_active_workspace_member(NEW.workspace_id, NEW.owner_id) THEN
        RAISE EXCEPTION 'project owner must be an active workspace member'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER projects_require_active_owner
AFTER INSERT OR UPDATE ON mnemo_team.projects
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_project_owner_active();

ALTER TABLE mnemo_team.workspaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.workspaces FORCE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.workspace_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.workspace_memberships FORCE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.projects FORCE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.project_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.project_memberships FORCE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.audit_events FORCE ROW LEVEL SECURITY;

CREATE POLICY workspaces_select ON mnemo_team.workspaces
FOR SELECT USING (
    mnemo_team.authorized(workspace_id, NULL, NULL, 'workspace')
);

CREATE POLICY workspaces_bootstrap_insert ON mnemo_team.workspaces
FOR INSERT WITH CHECK (
    mnemo_team.current_operation() = 'manage_workspace'
    AND mnemo_team.current_workspace() = workspace_id
    AND mnemo_team.current_principal() = owner_id
);

CREATE POLICY workspaces_update ON mnemo_team.workspaces
FOR UPDATE USING (
    mnemo_team.current_operation() IN ('manage_membership', 'manage_workspace')
    AND mnemo_team.authorized(workspace_id, NULL, NULL, 'workspace')
) WITH CHECK (
    mnemo_team.current_operation() = 'manage_workspace'
    AND workspace_id = mnemo_team.current_workspace()
);

CREATE POLICY workspace_memberships_select ON mnemo_team.workspace_memberships
FOR SELECT USING (
    mnemo_team.authorized(workspace_id, NULL, NULL, 'workspace')
);

CREATE POLICY workspace_memberships_insert ON mnemo_team.workspace_memberships
FOR INSERT WITH CHECK (
    (
        mnemo_team.current_operation() = 'manage_workspace'
        AND mnemo_team.current_workspace() = workspace_id
        AND mnemo_team.current_principal() = principal_id
        AND role = 'owner'
        AND status = 'active'
    ) OR (
        mnemo_team.current_operation() = 'manage_membership'
        AND mnemo_team.authorized(workspace_id, NULL, NULL, 'workspace')
        AND role <> 'owner'
    )
);

CREATE POLICY workspace_memberships_update ON mnemo_team.workspace_memberships
FOR UPDATE USING (
    mnemo_team.current_operation() IN ('manage_membership', 'manage_workspace')
    AND mnemo_team.authorized(workspace_id, NULL, NULL, 'workspace')
) WITH CHECK (
    workspace_id = mnemo_team.current_workspace()
    AND (
        (mnemo_team.current_operation() = 'manage_membership' AND role <> 'owner')
        OR (mnemo_team.current_operation() = 'manage_workspace' AND status = 'active')
    )
);

CREATE POLICY projects_select ON mnemo_team.projects
FOR SELECT USING (
    mnemo_team.authorized(workspace_id, project_id, NULL, 'project')
);

CREATE POLICY projects_insert ON mnemo_team.projects
FOR INSERT WITH CHECK (
    mnemo_team.current_operation() = 'manage_project'
    AND mnemo_team.authorized(workspace_id, NULL, NULL, 'workspace')
);

CREATE POLICY projects_update ON mnemo_team.projects
FOR UPDATE USING (
    mnemo_team.current_operation() = 'manage_project'
    AND mnemo_team.authorized(workspace_id, project_id, NULL, 'project')
) WITH CHECK (
    mnemo_team.current_operation() = 'manage_project'
    AND mnemo_team.authorized(workspace_id, project_id, NULL, 'project')
);

CREATE POLICY project_memberships_select ON mnemo_team.project_memberships
FOR SELECT USING (
    mnemo_team.authorized(workspace_id, project_id, NULL, 'project')
);

CREATE POLICY project_memberships_insert ON mnemo_team.project_memberships
FOR INSERT WITH CHECK (
    mnemo_team.current_operation() = 'manage_project'
    AND mnemo_team.authorized(workspace_id, project_id, NULL, 'project')
);

CREATE POLICY project_memberships_update ON mnemo_team.project_memberships
FOR UPDATE USING (
    mnemo_team.current_operation() = 'manage_project'
    AND mnemo_team.authorized(workspace_id, project_id, NULL, 'project')
) WITH CHECK (
    mnemo_team.current_operation() = 'manage_project'
    AND mnemo_team.authorized(workspace_id, project_id, NULL, 'project')
);

CREATE POLICY audit_events_select ON mnemo_team.audit_events
FOR SELECT USING (
    (
        workspace_id = mnemo_team.current_workspace()
        AND actor_id = mnemo_team.current_principal()
        AND mnemo_team.is_active_workspace_member(workspace_id, actor_id)
    ) OR mnemo_team.authorized(workspace_id, project_id, NULL, 'workspace')
);

CREATE POLICY audit_events_insert ON mnemo_team.audit_events
FOR INSERT WITH CHECK (
    workspace_id = mnemo_team.current_workspace()
    AND actor_id = mnemo_team.current_principal()
    AND (
        mnemo_team.authorized(workspace_id, project_id, NULL, 'workspace')
        OR (
            action = 'workspace_created'
            AND mnemo_team.current_operation() = 'manage_workspace'
            AND EXISTS (
                SELECT 1 FROM mnemo_team.workspaces AS workspace
                 WHERE workspace.workspace_id = audit_events.workspace_id
                   AND workspace.owner_id = audit_events.actor_id
            )
        )
    )
);

REVOKE ALL ON SCHEMA mnemo_team FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA mnemo_team FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mnemo_team FROM PUBLIC;

INSERT INTO mnemo_team.schema_migrations(version, applied_at)
VALUES (1, CURRENT_TIMESTAMP);
