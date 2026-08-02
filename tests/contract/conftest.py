from __future__ import annotations

from collections.abc import Callable
from typing import cast

import pytest

from packages.storage import CheckpointRepository, ReferenceCheckpointRepository


@pytest.fixture
def repository_factory() -> Callable[[], CheckpointRepository]:
    """The behavioral contract uses this factory; 10A.3b adds SQLite parity."""
    return cast(Callable[[], CheckpointRepository], ReferenceCheckpointRepository)
