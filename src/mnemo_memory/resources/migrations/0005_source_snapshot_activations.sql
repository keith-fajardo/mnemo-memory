-- Source snapshot identifiers are UUIDs, so they cannot establish chronology.
-- This append-only ledger records only scoped activation metadata, never source text.
CREATE TABLE source_snapshot_activations (
    activation_id INTEGER PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES source_structure_snapshots(snapshot_id) ON DELETE RESTRICT,
    owner_id TEXT NOT NULL REFERENCES principals(owner_id) ON DELETE RESTRICT,
    workspace_id TEXT NULL REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    activated_at TEXT NOT NULL
);

CREATE INDEX source_snapshot_activation_scope_idx
    ON source_snapshot_activations(owner_id, workspace_id, project_id, activation_id DESC);

CREATE TRIGGER source_snapshot_activation_scope_match
BEFORE INSERT ON source_snapshot_activations
FOR EACH ROW
WHEN NOT EXISTS (
    SELECT 1 FROM source_structure_snapshots
    WHERE snapshot_id = NEW.snapshot_id
      AND owner_id = NEW.owner_id
      AND workspace_id IS NEW.workspace_id
      AND project_id = NEW.project_id
)
BEGIN
    SELECT RAISE(ABORT, 'source snapshot activation scope mismatch');
END;

-- Existing databases have one known active point but no trustworthy predecessor.
INSERT INTO source_snapshot_activations(
    snapshot_id, owner_id, workspace_id, project_id, activated_at
)
SELECT snapshot_id, owner_id, workspace_id, project_id, CURRENT_TIMESTAMP
FROM source_structure_snapshots
WHERE is_active = 1;
