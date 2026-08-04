-- Rebuildable, current-revision lexical projection for explicitly scoped local knowledge.
-- SQLite operations rebuild one scope atomically after each successful sync and remove this
-- projection before a deleted document could be returned.
CREATE VIRTUAL TABLE knowledge_document_section_fts USING fts5(
    document_id UNINDEXED,
    revision_id UNINDEXED,
    section_index UNINDEXED,
    owner_id UNINDEXED,
    workspace_id UNINDEXED,
    project_id UNINDEXED,
    relative_path UNINDEXED,
    heading,
    content,
    tokenize = 'unicode61 remove_diacritics 2'
);

INSERT INTO knowledge_document_section_fts(
    document_id, revision_id, section_index, owner_id, workspace_id, project_id,
    relative_path, heading, content
)
SELECT
    source.document_id,
    revision.revision_id,
    section.section_index,
    source.owner_id,
    source.workspace_id,
    source.project_id,
    source.relative_path,
    section.heading,
    section.content
FROM knowledge_document_sources AS source
JOIN knowledge_document_revisions AS revision
  ON revision.revision_id = source.current_revision_id
JOIN knowledge_document_sections AS section
  ON section.revision_id = revision.revision_id
WHERE source.is_deleted = 0;
