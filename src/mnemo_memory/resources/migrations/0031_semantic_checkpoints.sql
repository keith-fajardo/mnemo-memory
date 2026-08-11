-- Additive personal semantic ledger and checkpoint materializations.
-- Recovery is forward-only: restore the pre-migration database backup if rollback is required.

CREATE TABLE IF NOT EXISTS semantic_memory_atoms (
    atom_id TEXT PRIMARY KEY,
    scope_key TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    visibility TEXT NOT NULL,
    workspace_id TEXT NULL,
    project_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    atom_kind TEXT NOT NULL CHECK (
        atom_kind IN (
            'goal', 'fact', 'state', 'decision', 'constraint', 'preference',
            'open_question', 'next_action', 'result', 'failure', 'inference'
        )
    ),
    atom_status TEXT NOT NULL CHECK (
        atom_status IN ('active', 'resolved', 'superseded', 'expired')
    ),
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object_value TEXT NOT NULL,
    priority INTEGER NOT NULL CHECK (priority BETWEEN 0 AND 100),
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS semantic_memory_atoms_scope_idx ON semantic_memory_atoms(
    scope_key, atom_status, atom_kind, priority DESC, atom_id
);

CREATE TABLE IF NOT EXISTS semantic_atom_source_events (
    atom_id TEXT NOT NULL REFERENCES semantic_memory_atoms(atom_id) ON DELETE CASCADE,
    event_id TEXT NOT NULL REFERENCES task_activity_events(event_id) ON DELETE CASCADE,
    PRIMARY KEY (atom_id, event_id)
);

CREATE INDEX IF NOT EXISTS semantic_atom_source_events_event_idx
ON semantic_atom_source_events(event_id, atom_id);

CREATE TRIGGER IF NOT EXISTS semantic_atom_delete_when_evidence_removed
AFTER DELETE ON semantic_atom_source_events
WHEN NOT EXISTS (
    SELECT 1 FROM semantic_atom_source_events AS remaining
    WHERE remaining.atom_id = OLD.atom_id
)
BEGIN
    DELETE FROM semantic_memory_atoms WHERE atom_id = OLD.atom_id;
END;

CREATE TABLE IF NOT EXISTS semantic_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    scope_key TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    visibility TEXT NOT NULL,
    workspace_id TEXT NULL,
    project_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    parent_checkpoint_id TEXT NULL REFERENCES semantic_checkpoints(checkpoint_id) ON DELETE RESTRICT,
    generation INTEGER NOT NULL CHECK (generation >= 1),
    schema_version TEXT NOT NULL,
    checkpoint_type TEXT NOT NULL CHECK (checkpoint_type IN ('delta', 'snapshot')),
    head_event_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    renderer_profile TEXT NOT NULL CHECK (renderer_profile IN ('compact', 'portable', 'audit')),
    target_tokenizer TEXT NOT NULL,
    measured_tokens INTEGER NOT NULL CHECK (measured_tokens >= 0),
    compression_ratio REAL NOT NULL CHECK (compression_ratio >= 0),
    patch_digest TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE (scope_key, generation),
    UNIQUE (scope_key, patch_digest)
);

CREATE INDEX IF NOT EXISTS semantic_checkpoints_scope_current_idx ON semantic_checkpoints(
    scope_key, generation DESC, checkpoint_id
);

-- Payload-free compilation markers survive authorized evidence erasure so a deleted head event
-- cannot cause older evidence to be compiled again.
CREATE TABLE IF NOT EXISTS semantic_compiled_events (
    scope_key TEXT NOT NULL,
    event_id TEXT NOT NULL,
    checkpoint_id TEXT NOT NULL REFERENCES semantic_checkpoints(checkpoint_id) ON DELETE RESTRICT,
    PRIMARY KEY (scope_key, event_id)
);

CREATE INDEX IF NOT EXISTS semantic_compiled_events_checkpoint_idx ON semantic_compiled_events(
    checkpoint_id, event_id
);

CREATE TABLE IF NOT EXISTS semantic_checkpoint_patch_operations (
    checkpoint_id TEXT NOT NULL REFERENCES semantic_checkpoints(checkpoint_id) ON DELETE CASCADE,
    operation_index INTEGER NOT NULL CHECK (operation_index >= 0),
    operation_kind TEXT NOT NULL CHECK (
        operation_kind IN (
            'add', 'update_metadata', 'supersede', 'resolve', 'expire',
            'activate_in_checkpoint', 'remove_from_active_checkpoint'
        )
    ),
    atom_id TEXT NULL,
    target_atom_id TEXT NULL,
    PRIMARY KEY (checkpoint_id, operation_index)
);

CREATE TABLE IF NOT EXISTS semantic_checkpoint_atoms (
    checkpoint_id TEXT NOT NULL REFERENCES semantic_checkpoints(checkpoint_id) ON DELETE CASCADE,
    atom_id TEXT NOT NULL REFERENCES semantic_memory_atoms(atom_id) ON DELETE CASCADE,
    inclusion_reason TEXT NOT NULL,
    checkpoint_priority INTEGER NOT NULL CHECK (checkpoint_priority BETWEEN 0 AND 100),
    atom_payload_json TEXT NOT NULL,
    PRIMARY KEY (checkpoint_id, atom_id)
);

CREATE INDEX IF NOT EXISTS semantic_checkpoint_atoms_priority_idx ON semantic_checkpoint_atoms(
    checkpoint_id, checkpoint_priority DESC, atom_id
);
