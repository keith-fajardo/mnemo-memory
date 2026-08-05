"""Authorization-first export of one exact production episodic scope."""

from __future__ import annotations

from datetime import datetime

from mnemo_memory.packages.domain import EpisodicExportBundle, MemoryScope
from mnemo_memory.packages.storage.contracts import EpisodicExportRepository


class EpisodicExportService:
    def __init__(self, repository: EpisodicExportRepository) -> None:
        self._repository = repository

    def export(self, scope: MemoryScope, *, exported_at: datetime) -> EpisodicExportBundle:
        return self._repository.export_episodic_state(scope, exported_at=exported_at)
