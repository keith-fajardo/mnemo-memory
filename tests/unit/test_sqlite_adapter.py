import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mnemo_memory.packages.storage import SQLiteCheckpointRepository, SQLiteSchemaTooNewError


def test_newer_schema_is_rejected_and_foreign_keys_are_enforced(tmp_path: Path) -> None:
    path = tmp_path / "mnemo.sqlite3"
    repository = SQLiteCheckpointRepository(path, base_directory=tmp_path)
    repository.migrate()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (21, ?)",
            (datetime(2026, 8, 2, tzinfo=UTC).isoformat(),),
        )
    with pytest.raises(SQLiteSchemaTooNewError):
        repository.migrate()
    fresh = SQLiteCheckpointRepository(tmp_path / "foreign.sqlite3", base_directory=tmp_path)
    fresh.migrate()
    with sqlite3.connect(fresh.path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO checkpoint_evidence(checkpoint_id, revision, evidence_id) VALUES ('missing', 1, 'missing')"  # noqa: E501
            )
