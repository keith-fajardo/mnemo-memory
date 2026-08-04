-- Rebuildable local semantic-retrieval projection.  It deliberately stores only finite
-- float vectors plus an integrity digest, never a second source-text payload.  All read paths
-- join through the current scoped knowledge source before considering a row.
CREATE TABLE knowledge_section_embeddings (
    revision_id TEXT NOT NULL,
    section_index INTEGER NOT NULL CHECK (section_index >= 0),
    owner_id TEXT NOT NULL REFERENCES principals(owner_id) ON DELETE RESTRICT,
    workspace_id TEXT NULL REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    model_id TEXT NOT NULL,
    section_digest TEXT NOT NULL,
    dimensions INTEGER NOT NULL CHECK (dimensions BETWEEN 8 AND 4096),
    vector_blob BLOB NOT NULL,
    PRIMARY KEY (revision_id, section_index, model_id),
    FOREIGN KEY (revision_id, section_index)
        REFERENCES knowledge_document_sections(revision_id, section_index) ON DELETE CASCADE
);

CREATE INDEX knowledge_section_embeddings_scope_current_idx
    ON knowledge_section_embeddings(owner_id, workspace_id, project_id, model_id, revision_id);

CREATE TRIGGER knowledge_embedding_scope_matches_revision
BEFORE INSERT ON knowledge_section_embeddings
FOR EACH ROW
WHEN NOT EXISTS (
    SELECT 1 FROM knowledge_document_revisions AS revision
    JOIN knowledge_document_sources AS source ON source.document_id = revision.document_id
    WHERE revision.revision_id = NEW.revision_id
      AND source.owner_id = NEW.owner_id
      AND source.workspace_id IS NEW.workspace_id
      AND source.project_id = NEW.project_id
)
BEGIN
    SELECT RAISE(ABORT, 'knowledge embedding scope mismatch');
END;
