-- Immutable, privacy-safe file fingerprints for source-structure snapshots.
-- Mnemo stores a relative path and SHA-256 digest only: never source bytes, comments,
-- docstrings, credentials, or an absolute workstation path.
CREATE TABLE source_structure_files (
    snapshot_id TEXT NOT NULL REFERENCES source_structure_snapshots(snapshot_id) ON DELETE RESTRICT,
    relative_path TEXT NOT NULL CHECK (
        length(relative_path) BETWEEN 1 AND 1024
        AND substr(relative_path, 1, 1) != '/'
        AND instr('/' || relative_path || '/', '/../') = 0
    ),
    content_digest TEXT NOT NULL CHECK (
        length(content_digest) = 71 AND substr(content_digest, 1, 7) = 'sha256:'
    ),
    PRIMARY KEY (snapshot_id, relative_path)
);

CREATE INDEX source_structure_file_snapshot_idx
    ON source_structure_files(snapshot_id, relative_path);
