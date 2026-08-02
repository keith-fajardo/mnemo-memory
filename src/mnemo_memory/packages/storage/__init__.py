"""Storage contracts and the local SQLite personal-profile adapter."""

from .contracts import (
    ActiveSnapshotConflict,
    CheckpointRepository,
    ManifestNodeNotFound,
    ManifestSnapshotNotFound,
    ProjectIndexRepository,
)
from .reference import ReferenceCheckpointRepository, ReferenceProjectIndexRepository
from .sqlite import SQLiteCheckpointRepository, SQLiteMigrationError, SQLiteSchemaTooNewError

__all__ = [
    "ActiveSnapshotConflict",
    "CheckpointRepository",
    "ManifestNodeNotFound",
    "ManifestSnapshotNotFound",
    "ProjectIndexRepository",
    "ReferenceCheckpointRepository",
    "ReferenceProjectIndexRepository",
    "SQLiteCheckpointRepository",
    "SQLiteMigrationError",
    "SQLiteSchemaTooNewError",
]
