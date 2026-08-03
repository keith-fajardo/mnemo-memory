"""Storage contracts and the local SQLite personal-profile adapter."""

from .contracts import (
    ActiveSnapshotConflict,
    ApprovedEpisodicEventRepository,
    ApprovedEpisodicEventStorageFailure,
    CheckpointLifecycleEventRepository,
    CheckpointRepository,
    EpisodicEventNotFound,
    ManifestNodeNotFound,
    ManifestSnapshotNotFound,
    ProjectIndexRepository,
    SourceStructureRepository,
)
from .reference import (
    ReferenceApprovedEpisodicEventRepository,
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
    "ApprovedEpisodicEventRepository",
    "ApprovedEpisodicEventStorageFailure",
    "CheckpointLifecycleEventRepository",
    "CheckpointRepository",
    "EpisodicEventNotFound",
    "ManifestNodeNotFound",
    "ManifestSnapshotNotFound",
    "ProjectIndexRepository",
    "ReferenceApprovedEpisodicEventRepository",
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
