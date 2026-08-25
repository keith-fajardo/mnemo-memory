-- Widen source-structure CHECK constraints so multi-language snapshots
-- (Go package symbols, Rust/Cargo package-dependency edges) persist.
-- SQLite cannot ALTER a CHECK, so rebuild both tables. FK-safe under
-- foreign_keys=ON: the child (edges) is dropped before the parent (symbols),
-- and edges are repopulated last from a constraint-free backup.

-- 1. Back up edges into a constraint-free scratch table (survives the drop).
CREATE TABLE _source_structure_edges_backup AS
    SELECT * FROM source_structure_edges;

-- 2. Rebuild symbols with the widened kind set.
CREATE TABLE source_structure_symbols_v2 (
    snapshot_id TEXT NOT NULL REFERENCES source_structure_snapshots(snapshot_id) ON DELETE RESTRICT,
    symbol_id TEXT NOT NULL,
    relative_path TEXT NOT NULL CHECK (substr(relative_path, 1, 1) != '/'),
    qualified_name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN (
        'module', 'package', 'class', 'interface', 'struct',
        'enum', 'trait', 'function', 'async_function'
    )),
    line_number INTEGER NOT NULL CHECK (line_number >= 1),
    PRIMARY KEY (snapshot_id, symbol_id),
    UNIQUE (snapshot_id, relative_path, qualified_name, kind, line_number)
);
INSERT INTO source_structure_symbols_v2 SELECT * FROM source_structure_symbols;

-- 3. Drop the child (edges) then the parent (symbols); safe with FK on.
DROP TABLE source_structure_edges;
DROP TABLE source_structure_symbols;

-- 4. Promote the rebuilt symbols table.
ALTER TABLE source_structure_symbols_v2 RENAME TO source_structure_symbols;

-- 5. Recreate edges with the widened edge_type set.
CREATE TABLE source_structure_edges (
    snapshot_id TEXT NOT NULL REFERENCES source_structure_snapshots(snapshot_id) ON DELETE RESTRICT,
    source_symbol_id TEXT NOT NULL,
    target TEXT NOT NULL,
    edge_type TEXT NOT NULL CHECK (edge_type IN (
        'imports', 'calls', 'defines', 'package_dependency'
    )),
    target_symbol_id TEXT NULL,
    PRIMARY KEY (snapshot_id, source_symbol_id, target, edge_type),
    FOREIGN KEY (snapshot_id, source_symbol_id)
        REFERENCES source_structure_symbols(snapshot_id, symbol_id) ON DELETE RESTRICT,
    FOREIGN KEY (snapshot_id, target_symbol_id)
        REFERENCES source_structure_symbols(snapshot_id, symbol_id) ON DELETE RESTRICT
);
INSERT INTO source_structure_edges SELECT * FROM _source_structure_edges_backup;
DROP TABLE _source_structure_edges_backup;

-- 6. Recreate the indexes that lived on the rebuilt tables.
CREATE INDEX source_structure_symbol_lookup_idx
    ON source_structure_symbols(snapshot_id, qualified_name, relative_path);
CREATE INDEX source_structure_edge_source_idx
    ON source_structure_edges(snapshot_id, source_symbol_id, target);
CREATE INDEX source_structure_edge_target_idx
    ON source_structure_edges(snapshot_id, target_symbol_id)
    WHERE target_symbol_id IS NOT NULL;
