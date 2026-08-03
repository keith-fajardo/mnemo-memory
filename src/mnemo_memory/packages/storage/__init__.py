"""Storage contracts and the local SQLite personal-profile adapter."""

from .contracts import (
    ActiveSnapshotConflict,
    CheckpointRepository,
    ManifestNodeNotFound,
    ManifestSnapshotNotFound,
    ProjectIndexRepository,
    SourceStructureRepository,
)
from .reference import (
    ReferenceCheckpointRepository,
    ReferenceProjectIndexRepository,
    ReferenceSourceStructureRepository,
)
from .sqlite import (
    SQLiteCheckpointRepository,
    SQLiteMigrationError,
    SQLiteSchemaTooNewError,
    SQLiteSourceStructureRepository,
)

__all__ = [
    "ActiveSnapshotConflict",
    "CheckpointRepository",
    "ManifestNodeNotFound",
    "ManifestSnapshotNotFound",
    "ProjectIndexRepository",
    "ReferenceCheckpointRepository",
    "ReferenceProjectIndexRepository",
    "ReferenceSourceStructureRepository",
    "SQLiteCheckpointRepository",
    "SQLiteMigrationError",
    "SQLiteSchemaTooNewError",
    "SQLiteSourceStructureRepository",
    "SourceStructureRepository",
]
