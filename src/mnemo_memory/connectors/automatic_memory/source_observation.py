"""Fail-open source observation immediately after a durable checkpoint revision.

The adapter knows local project paths and parsers.  It deliberately records an observed snapshot
reference, never an inferred reason for a source change.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from mnemo_memory.connectors.automatic_memory.scan_fingerprint import working_tree_fingerprint
from mnemo_memory.packages.application.automatic_memory import (
    LocalMemoryProjectBindingStore,
    MemoryProjectBinding,
)
from mnemo_memory.packages.application.checkpoints import CheckpointView
from mnemo_memory.packages.domain import (
    CheckpointSourceObservation,
    CodeSnapshot,
    CodeSnapshotId,
)
from mnemo_memory.packages.project_index import SourceStructureParser, SourceStructureParseRequest
from mnemo_memory.packages.storage.contracts import (
    CheckpointSourceObservationRepository,
    SourceStructureRepository,
)


def _cache_path(cache_dir: Path, binding: MemoryProjectBinding) -> Path:
    return cache_dir / f"{binding.scope.project_id}.txt"


def _read_cache(path: Path) -> tuple[str, str] | None:
    try:
        fingerprint, snapshot_id = path.read_text(encoding="utf-8").split("\n", 1)
    except (OSError, ValueError):
        return None
    return fingerprint.strip(), snapshot_id.strip()


def _write_cache(path: Path, fingerprint: str, snapshot_id: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{fingerprint}\n{snapshot_id}", encoding="utf-8")
    except OSError:
        pass  # cache is an optimization; never fail the refresh


def _cache_matches(cached: tuple[str, str] | None, fingerprint: str, snapshot_id: str) -> bool:
    return cached is not None and cached == (fingerprint, snapshot_id)


def source_snapshot_is_fresh(
    binding: MemoryProjectBinding,
    active_snapshot_id: CodeSnapshotId | None,
    *,
    cache_dir: Path,
    fingerprint: Callable[[Path], str] = working_tree_fingerprint,
) -> bool:
    """Whether the active snapshot still matches the working tree, by file metadata only.

    True only when the scan-cache entry exists, its fingerprint equals a fresh stat-only
    fingerprint of the tree, and it names the active snapshot. It never parses. A missing cache,
    no active snapshot, or an unreadable tree counts as stale (spec 2026-10-03 §2).
    """
    if active_snapshot_id is None:
        return False
    cached = _read_cache(_cache_path(cache_dir, binding))
    if cached is None:
        return False
    try:
        current = fingerprint(binding.project_root)
    except OSError:
        return False
    return _cache_matches(cached, current, str(active_snapshot_id))


def refresh_registered_project_source(
    binding: MemoryProjectBinding,
    source_repository: SourceStructureRepository,
    *,
    cache_dir: Path | None = None,
    fingerprint: Callable[[Path], str] = working_tree_fingerprint,
) -> CodeSnapshot | None:
    """Refresh one exact registered project without making the caller depend on indexing.

    When ``cache_dir`` is given, a cheap stat-only fingerprint of the working tree is compared
    against a small sidecar cache file. If the fingerprint matches the last capture and the
    active snapshot is still the one that fingerprint was recorded against, the full parse is
    skipped and the already-active snapshot is returned. Otherwise (or when ``cache_dir`` is
    omitted) the tree is parsed and stored as before, and the cache entry is rewritten.
    """
    try:
        cache_file = _cache_path(cache_dir, binding) if cache_dir is not None else None
        current_fp = fingerprint(binding.project_root) if cache_file is not None else None
        if cache_file is not None:
            active = source_repository.get_active_snapshot(binding.scope)
            cached = _read_cache(cache_file)
            if (
                active is not None
                and current_fp is not None
                and _cache_matches(cached, current_fp, str(active.snapshot_id))
            ):
                return active  # nothing changed — skip parse + store
        snapshot = source_repository.store_and_activate(
            SourceStructureParser().parse(
                SourceStructureParseRequest(binding.scope, binding.project_root)
            )
        ).snapshot
        if cache_file is not None and current_fp is not None:
            _write_cache(cache_file, current_fp, str(snapshot.snapshot_id))
        return snapshot
    except Exception:
        return None


@dataclass(frozen=True, slots=True)
class CheckpointSourceObserver:
    """Best-effort local observer; checkpoint success never depends on source indexing."""

    bindings: LocalMemoryProjectBindingStore
    source_repository: SourceStructureRepository
    association_repository: CheckpointSourceObservationRepository
    clock: Callable[[], datetime]
    cache_dir: Path | None = None

    def observe(self, view: CheckpointView) -> bool:
        try:
            binding = self.bindings.get_for_scope(view.aggregate.scope)
            if binding is None:
                return False
            snapshot = refresh_registered_project_source(
                binding, self.source_repository, cache_dir=self.cache_dir
            )
            if snapshot is None:
                return False
            self.association_repository.append_checkpoint_source_observation(
                CheckpointSourceObservation(
                    scope=view.aggregate.scope,
                    checkpoint_id=view.aggregate.checkpoint_id,
                    revision_id=view.revision.revision_id,
                    source_snapshot_id=snapshot.snapshot_id,
                    observed_at=self.clock(),
                )
            )
            return True
        except Exception:
            # This is an optional, local projection refresh after the checkpoint transaction has
            # completed. It must never disclose parser/storage details or turn a successful save
            # into a client-visible failure.
            return False
