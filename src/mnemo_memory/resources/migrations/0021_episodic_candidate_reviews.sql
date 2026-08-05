-- Explicit user review is append-only; only approved candidates receive an active marker.
CREATE TABLE IF NOT EXISTS episodic_candidate_reviews (
    action_sequence INTEGER PRIMARY KEY,
    action_id TEXT NOT NULL UNIQUE,
    candidate_id TEXT NOT NULL UNIQUE
        REFERENCES episodic_memory_candidates(memory_id) ON DELETE RESTRICT,
    decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
    actor TEXT NOT NULL CHECK (actor = 'user'),
    source_action_key TEXT NOT NULL CHECK (length(source_action_key) BETWEEN 1 AND 256),
    reason TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 1200),
    owner_id TEXT NOT NULL REFERENCES principals(owner_id) ON DELETE RESTRICT,
    visibility TEXT NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    workspace_id TEXT NULL REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE RESTRICT,
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    reviewed_at TEXT NOT NULL,
    UNIQUE (
        owner_id, visibility, workspace_id, project_id, session_id, task_id, source_action_key
    )
);

CREATE TABLE IF NOT EXISTS episodic_candidate_review_evidence (
    action_id TEXT NOT NULL REFERENCES episodic_candidate_reviews(action_id) ON DELETE RESTRICT,
    evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id) ON DELETE RESTRICT,
    PRIMARY KEY (action_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS active_episodic_memories (
    memory_id TEXT PRIMARY KEY
        REFERENCES episodic_memory_candidates(memory_id) ON DELETE RESTRICT,
    approval_action_id TEXT NOT NULL UNIQUE
        REFERENCES episodic_candidate_reviews(action_id) ON DELETE RESTRICT,
    activated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS episodic_candidate_reviews_scope_order_idx
ON episodic_candidate_reviews(
    owner_id, visibility, workspace_id, project_id, session_id, task_id,
    action_sequence DESC
);

CREATE TRIGGER IF NOT EXISTS episodic_review_candidate_scope_guard
BEFORE INSERT ON episodic_candidate_reviews
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM episodic_memory_candidates AS candidate
        WHERE candidate.memory_id = NEW.candidate_id
          AND candidate.owner_id = NEW.owner_id
          AND candidate.visibility = NEW.visibility
          AND candidate.workspace_id IS NEW.workspace_id
          AND candidate.project_id = NEW.project_id
          AND candidate.session_id = NEW.session_id
          AND candidate.task_id = NEW.task_id
    ) THEN RAISE(ABORT, 'episodic review candidate scope mismatch') END;
END;

CREATE TRIGGER IF NOT EXISTS active_episodic_memory_approval_guard
BEFORE INSERT ON active_episodic_memories
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM episodic_candidate_reviews AS review
        WHERE review.action_id = NEW.approval_action_id
          AND review.candidate_id = NEW.memory_id
          AND review.decision = 'approved'
          AND review.reviewed_at = NEW.activated_at
    ) THEN RAISE(ABORT, 'active episodic memory requires matching approval') END;
END;
