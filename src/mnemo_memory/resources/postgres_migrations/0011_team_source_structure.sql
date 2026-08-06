CREATE TABLE mnemo_team.source_structure_snapshots (
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    snapshot_id uuid NOT NULL,
    source_digest text NOT NULL CHECK (source_digest ~ '^sha256:[0-9a-f]{64}$'),
    file_count integer NOT NULL CHECK (file_count >= 0),
    symbol_count integer NOT NULL CHECK (symbol_count >= 0),
    edge_count integer NOT NULL CHECK (edge_count >= 0),
    is_active boolean NOT NULL DEFAULT false,
    PRIMARY KEY (workspace_id, snapshot_id),
    UNIQUE (workspace_id, project_id, owner_id, visibility, snapshot_id),
    UNIQUE (workspace_id, project_id, owner_id, visibility, source_digest),
    FOREIGN KEY (workspace_id, project_id)
        REFERENCES mnemo_team.projects(workspace_id, project_id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX source_structure_one_active
ON mnemo_team.source_structure_snapshots(workspace_id, project_id, owner_id, visibility)
WHERE is_active;

CREATE TABLE mnemo_team.source_structure_files (
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    snapshot_id uuid NOT NULL,
    relative_path text NOT NULL CHECK (
        relative_path <> '' AND left(relative_path, 1) <> '/'
        AND relative_path !~ '(^|/)\.\.(/|$)'
    ),
    content_digest text NOT NULL CHECK (content_digest ~ '^sha256:[0-9a-f]{64}$'),
    PRIMARY KEY (workspace_id, snapshot_id, relative_path),
    FOREIGN KEY (workspace_id, project_id, owner_id, visibility, snapshot_id)
        REFERENCES mnemo_team.source_structure_snapshots(
            workspace_id, project_id, owner_id, visibility, snapshot_id
        ) ON DELETE CASCADE
);

CREATE TABLE mnemo_team.source_structure_symbols (
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    snapshot_id uuid NOT NULL,
    symbol_id uuid NOT NULL,
    relative_path text NOT NULL CHECK (
        relative_path <> '' AND left(relative_path, 1) <> '/'
        AND relative_path !~ '(^|/)\.\.(/|$)'
    ),
    qualified_name text NOT NULL CHECK (qualified_name <> ''),
    symbol_kind text NOT NULL CHECK (symbol_kind IN (
        'module', 'package', 'class', 'interface', 'struct', 'enum', 'trait',
        'function', 'async_function'
    )),
    line_number integer NOT NULL CHECK (line_number >= 1),
    PRIMARY KEY (workspace_id, snapshot_id, symbol_id),
    UNIQUE (workspace_id, project_id, owner_id, visibility, snapshot_id, symbol_id),
    FOREIGN KEY (workspace_id, project_id, owner_id, visibility, snapshot_id)
        REFERENCES mnemo_team.source_structure_snapshots(
            workspace_id, project_id, owner_id, visibility, snapshot_id
        ) ON DELETE CASCADE
);

CREATE TABLE mnemo_team.source_structure_edges (
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    snapshot_id uuid NOT NULL,
    edge_sequence integer NOT NULL CHECK (edge_sequence >= 0),
    source_symbol_id uuid NOT NULL,
    target text NOT NULL CHECK (target <> '' AND length(target) <= 512),
    edge_kind text NOT NULL CHECK (edge_kind IN (
        'imports', 'calls', 'package_dependency', 'defines'
    )),
    target_symbol_id uuid,
    PRIMARY KEY (workspace_id, snapshot_id, edge_sequence),
    FOREIGN KEY (workspace_id, project_id, owner_id, visibility, snapshot_id)
        REFERENCES mnemo_team.source_structure_snapshots(
            workspace_id, project_id, owner_id, visibility, snapshot_id
        ) ON DELETE CASCADE,
    FOREIGN KEY (
        workspace_id, project_id, owner_id, visibility, snapshot_id, source_symbol_id
    ) REFERENCES mnemo_team.source_structure_symbols(
        workspace_id, project_id, owner_id, visibility, snapshot_id, symbol_id
    ) ON DELETE CASCADE,
    FOREIGN KEY (
        workspace_id, project_id, owner_id, visibility, snapshot_id, target_symbol_id
    ) REFERENCES mnemo_team.source_structure_symbols(
        workspace_id, project_id, owner_id, visibility, snapshot_id, symbol_id
    ) ON DELETE CASCADE
);

CREATE TABLE mnemo_team.source_snapshot_activations (
    activation_sequence bigserial NOT NULL,
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    snapshot_id uuid NOT NULL,
    activated_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, activation_sequence),
    FOREIGN KEY (workspace_id, project_id, owner_id, visibility, snapshot_id)
        REFERENCES mnemo_team.source_structure_snapshots(
            workspace_id, project_id, owner_id, visibility, snapshot_id
        ) ON DELETE RESTRICT
);

CREATE TABLE mnemo_team.source_structure_sync_status (
    workspace_id uuid NOT NULL,
    project_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    last_synced_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, project_id, owner_id, visibility),
    FOREIGN KEY (workspace_id, project_id)
        REFERENCES mnemo_team.projects(workspace_id, project_id) ON DELETE CASCADE
);

CREATE FUNCTION mnemo_team.ensure_source_snapshot_update()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE latest_snapshot_id uuid;
BEGIN
    IF ROW(
        NEW.workspace_id, NEW.project_id, NEW.owner_id, NEW.visibility, NEW.snapshot_id,
        NEW.source_digest, NEW.file_count, NEW.symbol_count, NEW.edge_count
    ) IS DISTINCT FROM ROW(
        OLD.workspace_id, OLD.project_id, OLD.owner_id, OLD.visibility, OLD.snapshot_id,
        OLD.source_digest, OLD.file_count, OLD.symbol_count, OLD.edge_count
    ) THEN
        RAISE EXCEPTION 'source snapshot immutable fields cannot change' USING ERRCODE = '23514';
    END IF;
    IF NEW.is_active IS DISTINCT FROM OLD.is_active THEN
        SELECT activation.snapshot_id INTO latest_snapshot_id
          FROM mnemo_team.source_snapshot_activations AS activation
         WHERE activation.workspace_id = NEW.workspace_id
           AND activation.project_id = NEW.project_id
           AND activation.owner_id = NEW.owner_id
           AND activation.visibility = NEW.visibility
         ORDER BY activation.activation_sequence DESC LIMIT 1;
        IF latest_snapshot_id IS NULL
           OR (NEW.is_active AND latest_snapshot_id <> NEW.snapshot_id)
           OR (NOT NEW.is_active AND latest_snapshot_id = NEW.snapshot_id) THEN
            RAISE EXCEPTION 'source snapshot activation order mismatch' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER source_snapshot_update_guard
BEFORE UPDATE ON mnemo_team.source_structure_snapshots
FOR EACH ROW EXECUTE FUNCTION mnemo_team.ensure_source_snapshot_update();

ALTER TABLE mnemo_team.source_structure_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.source_structure_snapshots FORCE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.source_structure_files ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.source_structure_files FORCE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.source_structure_symbols ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.source_structure_symbols FORCE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.source_structure_edges ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.source_structure_edges FORCE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.source_snapshot_activations ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.source_snapshot_activations FORCE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.source_structure_sync_status ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemo_team.source_structure_sync_status FORCE ROW LEVEL SECURITY;

CREATE POLICY source_snapshot_access ON mnemo_team.source_structure_snapshots
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));
CREATE POLICY source_file_access ON mnemo_team.source_structure_files
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));
CREATE POLICY source_symbol_access ON mnemo_team.source_structure_symbols
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));
CREATE POLICY source_edge_access ON mnemo_team.source_structure_edges
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));
CREATE POLICY source_activation_access ON mnemo_team.source_snapshot_activations
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));
CREATE POLICY source_sync_access ON mnemo_team.source_structure_sync_status
USING (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility))
WITH CHECK (mnemo_team.authorized(workspace_id, project_id, owner_id, visibility));

REVOKE ALL ON ALL TABLES IN SCHEMA mnemo_team FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mnemo_team FROM PUBLIC;

INSERT INTO mnemo_team.schema_migrations(version, applied_at)
VALUES (11, CURRENT_TIMESTAMP);
