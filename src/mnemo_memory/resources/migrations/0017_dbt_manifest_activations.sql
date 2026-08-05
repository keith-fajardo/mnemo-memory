-- Manifest snapshot identifiers and timestamps are not activation order.
-- This append-only ledger records only scoped activation metadata, never source content.
CREATE TABLE dbt_manifest_activations (
    activation_id INTEGER PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES dbt_manifest_snapshots(snapshot_id) ON DELETE RESTRICT,
    owner_id TEXT NOT NULL REFERENCES principals(owner_id) ON DELETE RESTRICT,
    workspace_id TEXT NULL REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    activated_at TEXT NOT NULL
);

CREATE INDEX dbt_manifest_activation_scope_idx
    ON dbt_manifest_activations(owner_id, workspace_id, project_id, activation_id DESC);

CREATE TRIGGER dbt_manifest_activation_scope_match
BEFORE INSERT ON dbt_manifest_activations
FOR EACH ROW
WHEN NOT EXISTS (
    SELECT 1 FROM dbt_manifest_snapshots
    WHERE snapshot_id = NEW.snapshot_id
      AND owner_id = NEW.owner_id
      AND workspace_id IS NEW.workspace_id
      AND project_id = NEW.project_id
)
BEGIN
    SELECT RAISE(ABORT, 'dbt manifest activation scope mismatch');
END;

-- Existing databases have one known active point but no trustworthy predecessor.
INSERT INTO dbt_manifest_activations(
    snapshot_id, owner_id, workspace_id, project_id, activated_at
)
SELECT snapshot_id, owner_id, workspace_id, project_id, CURRENT_TIMESTAMP
FROM dbt_manifest_snapshots
WHERE is_active = 1;
