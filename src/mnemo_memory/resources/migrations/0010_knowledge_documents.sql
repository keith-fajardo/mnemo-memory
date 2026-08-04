-- Scoped local Markdown/Obsidian knowledge sources. Revisions retain accepted document payloads
-- until an explicit deletion removes them; tombstones keep only minimal path/digest metadata.
CREATE TABLE knowledge_document_sources (
    document_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES principals(owner_id) ON DELETE RESTRICT,
    visibility TEXT NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    workspace_id TEXT NULL REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    scope_level TEXT NOT NULL CHECK (scope_level = 'project'),
    relative_path TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    current_revision_id TEXT NULL UNIQUE,
    is_deleted INTEGER NOT NULL CHECK (is_deleted IN (0, 1)),
    created_at TEXT NOT NULL,
    deleted_at TEXT NULL
);

CREATE UNIQUE INDEX knowledge_document_active_path_unique
    ON knowledge_document_sources(owner_id, workspace_id, project_id, relative_path)
    WHERE is_deleted = 0;
CREATE INDEX knowledge_document_scope_idx
    ON knowledge_document_sources(owner_id, workspace_id, project_id, is_deleted, relative_path);

CREATE TABLE knowledge_document_revisions (
    revision_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES knowledge_document_sources(document_id) ON DELETE CASCADE,
    revision_number INTEGER NOT NULL CHECK (revision_number >= 1),
    predecessor_revision_id TEXT NULL REFERENCES knowledge_document_revisions(revision_id) ON DELETE RESTRICT,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('markdown', 'obsidian')),
    relative_path TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    title TEXT NOT NULL,
    frontmatter_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(document_id, revision_number)
);

CREATE TABLE knowledge_document_sections (
    revision_id TEXT NOT NULL REFERENCES knowledge_document_revisions(revision_id) ON DELETE CASCADE,
    section_index INTEGER NOT NULL CHECK (section_index >= 0),
    heading TEXT NOT NULL,
    heading_level INTEGER NOT NULL CHECK (heading_level BETWEEN 0 AND 6),
    content TEXT NOT NULL,
    PRIMARY KEY(revision_id, section_index)
);

CREATE TABLE knowledge_document_links (
    revision_id TEXT NOT NULL REFERENCES knowledge_document_revisions(revision_id) ON DELETE CASCADE,
    link_target TEXT NOT NULL,
    link_kind TEXT NOT NULL CHECK (link_kind IN ('markdown', 'wiki')),
    PRIMARY KEY(revision_id, link_kind, link_target)
);

CREATE TABLE knowledge_document_tombstones (
    document_id TEXT PRIMARY KEY REFERENCES knowledge_document_sources(document_id) ON DELETE RESTRICT,
    owner_id TEXT NOT NULL REFERENCES principals(owner_id) ON DELETE RESTRICT,
    visibility TEXT NOT NULL CHECK (visibility IN ('owner', 'workspace', 'project')),
    workspace_id TEXT NULL REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    relative_path TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    deleted_at TEXT NOT NULL
);

CREATE INDEX knowledge_tombstone_scope_idx
    ON knowledge_document_tombstones(owner_id, workspace_id, project_id, deleted_at);

CREATE TRIGGER knowledge_revision_predecessor_matches_document
BEFORE INSERT ON knowledge_document_revisions
FOR EACH ROW
WHEN (NEW.revision_number = 1 AND NEW.predecessor_revision_id IS NOT NULL)
   OR (NEW.revision_number > 1 AND NOT EXISTS (
        SELECT 1 FROM knowledge_document_revisions AS prior
        WHERE prior.revision_id = NEW.predecessor_revision_id
          AND prior.document_id = NEW.document_id
          AND prior.revision_number = NEW.revision_number - 1
   ))
BEGIN
    SELECT RAISE(ABORT, 'knowledge revision predecessor mismatch');
END;

CREATE TRIGGER knowledge_source_current_revision_matches_document
BEFORE UPDATE OF current_revision_id ON knowledge_document_sources
FOR EACH ROW
WHEN NEW.current_revision_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM knowledge_document_revisions
    WHERE revision_id = NEW.current_revision_id AND document_id = NEW.document_id
)
BEGIN
    SELECT RAISE(ABORT, 'knowledge current revision mismatch');
END;

CREATE TRIGGER knowledge_tombstone_scope_matches_source
BEFORE INSERT ON knowledge_document_tombstones
FOR EACH ROW
WHEN NOT EXISTS (
    SELECT 1 FROM knowledge_document_sources AS source
    WHERE source.document_id = NEW.document_id
      AND source.owner_id = NEW.owner_id
      AND source.visibility = NEW.visibility
      AND source.workspace_id IS NEW.workspace_id
      AND source.project_id = NEW.project_id
      AND source.is_deleted = 1
)
BEGIN
    SELECT RAISE(ABORT, 'knowledge tombstone scope mismatch');
END;
