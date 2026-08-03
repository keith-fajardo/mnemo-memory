"""Storage contracts and the local SQLite personal-profile adapter."""

from .contracts import (
    ActiveSnapshotConflict,
    CheckpointLifecycleEventRepository,
    CheckpointRepository,
    EpisodicEventNotFound,
    ManifestNodeNotFound,
    ManifestSnapshotNotFound,
    ProjectIndexRepository,
    SourceStructureRepository,
)
from .reference import (
    ReferenceCheckpointLifecycleEventRepository,
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
    "CheckpointLifecycleEventRepository",
    "CheckpointRepository",
    "EpisodicEventNotFound",
    "ManifestNodeNotFound",
    "ManifestSnapshotNotFound",
    "ProjectIndexRepository",
    "ReferenceCheckpointLifecycleEventRepository",
    "ReferenceCheckpointRepository",
    "ReferenceProjectIndexRepository",
    "ReferenceSourceStructureRepository",
    "SQLiteCheckpointRepository",
    "SQLiteMigrationError",
    "SQLiteSchemaTooNewError",
    "SQLiteSourceStructureRepository",
    "SourceStructureRepository",
]
