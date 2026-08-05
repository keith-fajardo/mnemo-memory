-- Content-free operational status for exact project-scoped rebuildable indexes.
-- Existing rows are deliberately not backfilled: an activation or document revision does not
-- prove when the last complete sync attempt succeeded.
CREATE TABLE project_index_sync_status (
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    index_kind TEXT NOT NULL CHECK (index_kind IN ('knowledge', 'source', 'dbt')),
    owner_id TEXT NOT NULL REFERENCES principals(owner_id) ON DELETE RESTRICT,
    workspace_id TEXT NULL REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    last_sync_at TEXT NOT NULL CHECK (julianday(last_sync_at) IS NOT NULL),
    PRIMARY KEY (project_id, index_kind)
);

CREATE INDEX project_index_sync_scope_idx
ON project_index_sync_status(owner_id, workspace_id, project_id, index_kind);

CREATE TRIGGER project_index_sync_scope_match_insert
BEFORE INSERT ON project_index_sync_status
FOR EACH ROW
WHEN NOT EXISTS (
    SELECT 1 FROM projects
    WHERE project_id = NEW.project_id
      AND owner_id = NEW.owner_id
      AND workspace_id IS NEW.workspace_id
)
BEGIN
    SELECT RAISE(ABORT, 'project index sync scope mismatch');
END;

CREATE TRIGGER project_index_sync_scope_match_update
BEFORE UPDATE ON project_index_sync_status
FOR EACH ROW
WHEN NOT EXISTS (
    SELECT 1 FROM projects
    WHERE project_id = NEW.project_id
      AND owner_id = NEW.owner_id
      AND workspace_id IS NEW.workspace_id
)
BEGIN
    SELECT RAISE(ABORT, 'project index sync scope mismatch');
END;
