-- Inactive, model-proposed candidates retain canonical source authority and provenance.
CREATE TABLE IF NOT EXISTS episodic_memory_candidates (
    candidate_sequence INTEGER PRIMARY KEY,
    memory_id TEXT NOT NULL UNIQUE,
    source_event_id TEXT NOT NULL REFERENCES task_activity_events(event_id) ON DELETE RESTRICT,
    proposal_index INTEGER NOT NULL CHECK (proposal_index BETWEEN 0 AND 3),
    memory_kind TEXT NOT NULL CHECK (
        memory_kind IN ('decision', 'failure', 'outcome', 'lesson', 'preference')
    ),
    claim TEXT NOT NULL CHECK (length(claim) BETWEEN 1 AND 1200),
    confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    sensitivity TEXT NOT NULL CHECK (
        sensitivity IN ('normal', 'personal', 'confidential', 'restricted')
    ),
    status TEXT NOT NULL CHECK (status = 'candidate'),
    extractor_version TEXT NOT NULL CHECK (length(extractor_version) BETWEEN 1 AND 128),
    provider_id TEXT NOT NULL CHECK (length(provider_id) BETWEEN 1 AND 128),
    model_id TEXT NOT NULL CHECK (length(model_id) BETWEEN 1 AND 128),
    prompt_version TEXT NOT NULL CHECK (length(prompt_version) BETWEEN 1 AND 128),
    retention_policy_id TEXT NOT NULL,
    retention_permanent INTEGER NOT NULL CHECK (retention_permanent IN (0, 1)),
    retention_created_at TEXT NOT NULL,
    retention_observed_at TEXT NOT NULL,
    retention_valid_from TEXT NOT NULL,
    retention_valid_to TEXT NULL,
    retention_expires_at TEXT NULL,
    retention_expired_at TEXT NULL,
    owner_id TEXT NOT NULL REFERENCES principals(owner_id) ON DELETE RESTRICT,
    visibility TEXT NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    workspace_id TEXT NULL REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE RESTRICT,
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    UNIQUE (source_event_id, extractor_version, proposal_index),
    CHECK (
        (retention_permanent = 1 AND retention_expires_at IS NULL AND retention_expired_at IS NULL)
        OR (retention_permanent = 0 AND retention_expires_at IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS episodic_memory_candidate_evidence (
    memory_id TEXT NOT NULL REFERENCES episodic_memory_candidates(memory_id) ON DELETE RESTRICT,
    evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id) ON DELETE RESTRICT,
    PRIMARY KEY (memory_id, evidence_id)
);

CREATE INDEX IF NOT EXISTS episodic_memory_candidates_scope_order_idx
ON episodic_memory_candidates(
    owner_id, visibility, workspace_id, project_id, session_id, task_id,
    candidate_sequence DESC
);

CREATE TRIGGER IF NOT EXISTS episodic_candidate_source_scope_guard
BEFORE INSERT ON episodic_memory_candidates
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM task_activity_events AS source
        WHERE source.event_id = NEW.source_event_id
          AND source.owner_id = NEW.owner_id
          AND source.visibility = NEW.visibility
          AND source.workspace_id IS NEW.workspace_id
          AND source.project_id = NEW.project_id
          AND source.session_id = NEW.session_id
          AND source.task_id = NEW.task_id
    ) THEN RAISE(ABORT, 'episodic candidate source scope mismatch') END;
END;
