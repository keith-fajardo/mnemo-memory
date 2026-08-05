-- User-authored corrections and retractions form one append-only optimistic revision chain.
CREATE TABLE IF NOT EXISTS episodic_memory_governance (
    action_sequence INTEGER PRIMARY KEY,
    action_id TEXT NOT NULL UNIQUE,
    memory_id TEXT NOT NULL REFERENCES active_episodic_memories(memory_id) ON DELETE RESTRICT,
    action_kind TEXT NOT NULL CHECK (action_kind IN ('corrected', 'retracted')),
    actor TEXT NOT NULL CHECK (actor = 'user'),
    expected_revision_id TEXT NOT NULL,
    source_action_key TEXT NOT NULL CHECK (length(source_action_key) BETWEEN 1 AND 256),
    reason TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 1200),
    corrected_claim TEXT NULL CHECK (
        corrected_claim IS NULL OR length(corrected_claim) BETWEEN 1 AND 1200
    ),
    corrected_sensitivity TEXT NULL CHECK (
        corrected_sensitivity IS NULL
        OR corrected_sensitivity IN ('normal', 'personal', 'confidential', 'restricted')
    ),
    owner_id TEXT NOT NULL REFERENCES principals(owner_id) ON DELETE RESTRICT,
    visibility TEXT NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    workspace_id TEXT NULL REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE RESTRICT,
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    occurred_at TEXT NOT NULL,
    UNIQUE (memory_id, expected_revision_id),
    UNIQUE (
        owner_id, visibility, workspace_id, project_id, session_id, task_id, source_action_key
    ),
    CHECK (
        (action_kind = 'corrected' AND corrected_claim IS NOT NULL
            AND corrected_sensitivity IS NOT NULL)
        OR (action_kind = 'retracted' AND corrected_claim IS NULL
            AND corrected_sensitivity IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS episodic_memory_governance_evidence (
    action_id TEXT NOT NULL REFERENCES episodic_memory_governance(action_id) ON DELETE RESTRICT,
    evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id) ON DELETE RESTRICT,
    PRIMARY KEY (action_id, evidence_id)
);

CREATE INDEX IF NOT EXISTS episodic_memory_governance_scope_order_idx
ON episodic_memory_governance(
    owner_id, visibility, workspace_id, project_id, session_id, task_id,
    memory_id, action_sequence ASC
);

CREATE TRIGGER IF NOT EXISTS episodic_memory_governance_scope_guard
BEFORE INSERT ON episodic_memory_governance
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM active_episodic_memories AS active
        JOIN episodic_memory_candidates AS candidate
          ON candidate.memory_id = active.memory_id
        WHERE active.memory_id = NEW.memory_id
          AND candidate.owner_id = NEW.owner_id
          AND candidate.visibility = NEW.visibility
          AND candidate.workspace_id IS NEW.workspace_id
          AND candidate.project_id = NEW.project_id
          AND candidate.session_id = NEW.session_id
          AND candidate.task_id = NEW.task_id
    ) THEN RAISE(ABORT, 'episodic governance memory scope mismatch') END;
END;
