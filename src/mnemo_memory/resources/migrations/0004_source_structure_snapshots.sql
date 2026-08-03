CREATE TABLE source_structure_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES principals(owner_id) ON DELETE RESTRICT,
    visibility TEXT NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    workspace_id TEXT NULL REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    scope_level TEXT NOT NULL CHECK (scope_level = 'project'),
    source_digest TEXT NOT NULL CHECK (length(source_digest) = 71 AND substr(source_digest, 1, 7) = 'sha256:'),
    file_count INTEGER NOT NULL CHECK (file_count >= 0),
    symbol_count INTEGER NOT NULL CHECK (symbol_count >= 0),
    edge_count INTEGER NOT NULL CHECK (edge_count >= 0),
    is_active INTEGER NOT NULL CHECK (is_active IN (0, 1)),
    UNIQUE (owner_id, workspace_id, project_id, source_digest)
);

CREATE TABLE source_structure_symbols (
    snapshot_id TEXT NOT NULL REFERENCES source_structure_snapshots(snapshot_id) ON DELETE RESTRICT,
    symbol_id TEXT NOT NULL,
    relative_path TEXT NOT NULL CHECK (substr(relative_path, 1, 1) != '/'),
    qualified_name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN (
        'module', 'class', 'interface', 'struct', 'enum', 'trait', 'function', 'async_function'
    )),
    line_number INTEGER NOT NULL CHECK (line_number >= 1),
    PRIMARY KEY (snapshot_id, symbol_id),
    UNIQUE (snapshot_id, relative_path, qualified_name, kind, line_number)
);

CREATE TABLE source_structure_edges (
    snapshot_id TEXT NOT NULL REFERENCES source_structure_snapshots(snapshot_id) ON DELETE RESTRICT,
    source_symbol_id TEXT NOT NULL,
    target TEXT NOT NULL,
    edge_type TEXT NOT NULL CHECK (edge_type IN ('imports', 'calls', 'defines')),
    target_symbol_id TEXT NULL,
    PRIMARY KEY (snapshot_id, source_symbol_id, target, edge_type),
    FOREIGN KEY (snapshot_id, source_symbol_id)
        REFERENCES source_structure_symbols(snapshot_id, symbol_id) ON DELETE RESTRICT,
    FOREIGN KEY (snapshot_id, target_symbol_id)
        REFERENCES source_structure_symbols(snapshot_id, symbol_id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX source_structure_one_active_project_idx
    ON source_structure_snapshots(owner_id, project_id) WHERE is_active = 1;
CREATE INDEX source_structure_active_scope_idx
    ON source_structure_snapshots(owner_id, workspace_id, project_id, is_active);
CREATE INDEX source_structure_symbol_lookup_idx
    ON source_structure_symbols(snapshot_id, qualified_name, relative_path);
CREATE INDEX source_structure_edge_source_idx
    ON source_structure_edges(snapshot_id, source_symbol_id, target);
CREATE INDEX source_structure_edge_target_idx
    ON source_structure_edges(snapshot_id, target_symbol_id)
    WHERE target_symbol_id IS NOT NULL;
