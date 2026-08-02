from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from packages.storage import (
    CheckpointRepository,
    ReferenceCheckpointRepository,
    SQLiteCheckpointRepository,
)


@pytest.fixture(params=("reference", "sqlite"))
def repository_factory(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Callable[[], CheckpointRepository]:
    """Run the same behavior contract against every supported repository adapter."""
    if request.param == "reference":
        return ReferenceCheckpointRepository

    def sqlite_factory() -> CheckpointRepository:
        repository = SQLiteCheckpointRepository(
            tmp_path / "contract.sqlite3", base_directory=tmp_path
        )
        repository.migrate()
        return repository

    return sqlite_factory
