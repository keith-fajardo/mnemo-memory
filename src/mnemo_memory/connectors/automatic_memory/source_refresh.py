"""The background code-map refresh worker and its single-instance lock (spec 2026-10-03 §3).

Lifecycle hooks never parse source (§2). When the stat-only fingerprint says the saved map is
stale, the hook starts ``mnemo_memory.apps.cli.source_refresh_entry`` detached, and that entry
calls ``run_source_refresh`` here. One worker runs per data directory: it holds a non-blocking
``flock`` on ``<data_dir>/.source-refresh.lock``, and any other worker leaves at once. The OS
releases the lock when its holder exits, however it exits. Nothing here prints or stores source
text.
"""

from __future__ import annotations

import fcntl
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from mnemo_memory.connectors.automatic_memory.git_observation import (
    GitSourceObserver,
    observe_and_store_git,
)
from mnemo_memory.connectors.automatic_memory.scan_fingerprint import working_tree_fingerprint
from mnemo_memory.connectors.automatic_memory.source_observation import (
    refresh_registered_project_source,
    source_snapshot_is_fresh,
)
from mnemo_memory.packages.application.automatic_memory import (
    AutomaticMemoryBindingError,
    LocalMemoryProjectBindingStore,
    MemoryProjectBinding,
)
from mnemo_memory.packages.storage import SQLiteSourceStructureRepository

SOURCE_REFRESH_LOCK_FILE = ".source-refresh.lock"
MAXIMUM_PARSES_PER_RUN = 3
PRUNE_SNAPSHOTS_PER_RUN = 4


class SourceRefreshLock:
    """The single-worker lock for one data directory."""

    def __init__(self, data_directory: Path) -> None:
        self._directory = data_directory.expanduser().resolve()
        self.path = self._directory / SOURCE_REFRESH_LOCK_FILE

    @contextmanager
    def hold(self) -> Iterator[bool]:
        """Hold the lock for one run; yield ``False`` at once when another holder has it."""
        self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(self.path, _flags(create=True), 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                with suppress(OSError):
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def running(self) -> bool:
        """Whether a holder has the lock now: a status probe that never creates the file."""
        try:
            descriptor = os.open(self.path, _flags(create=False))
        except OSError:
            return False
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        except OSError:
            return False
        else:
            with suppress(OSError):
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            return False
        finally:
            os.close(descriptor)


@dataclass(frozen=True, slots=True)
class SourceRefreshOutcome:
    """What one run did. ``ran`` is ``False`` when another worker held the lock."""

    ran: bool
    parses: int = 0
    pruned: int = 0


def run_source_refresh(
    data_directory: Path,
    project_root: Path,
    *,
    git_observer: GitSourceObserver | None = None,
    fingerprint: Callable[[Path], str] = working_tree_fingerprint,
) -> SourceRefreshOutcome:
    """Refresh one enabled project's code map. Failures may escape; the entry swallows them.

    In order: take the lock or leave; resolve the binding exactly as the hook does; while the
    tree is stale, parse, store and activate, then write the scan cache (at most three parses,
    so edits made during a parse are picked up); record Git state for each new digest; prune
    one bounded step.
    """
    with SourceRefreshLock(data_directory).hold() as held:
        if not held:
            return SourceRefreshOutcome(ran=False)
        binding = _binding(data_directory, project_root)
        if binding is None:
            return SourceRefreshOutcome(ran=True)
        repository = SQLiteSourceStructureRepository(data_directory / "mnemo.sqlite3")
        repository.migrate()
        cache_dir = data_directory / "scan-cache"
        parses = 0
        while parses < MAXIMUM_PARSES_PER_RUN:
            active = repository.get_active_snapshot(binding.scope)
            if source_snapshot_is_fresh(
                binding,
                None if active is None else active.snapshot_id,
                cache_dir=cache_dir,
                fingerprint=fingerprint,
            ):
                break
            parses += 1
            snapshot = refresh_registered_project_source(
                binding, repository, cache_dir=cache_dir, fingerprint=fingerprint
            )
            if snapshot is None:
                break
            observe_and_store_git(
                data_directory,
                binding.project_root,
                binding.scope,
                snapshot.source_digest,
                git_observer,
            )
        pruned = repository.prune_source_snapshots(
            binding.scope, max_snapshots=PRUNE_SNAPSHOTS_PER_RUN
        )
        return SourceRefreshOutcome(ran=True, parses=parses, pruned=pruned)


def _binding(data_directory: Path, project_root: Path) -> MemoryProjectBinding | None:
    """Resolve the enabled project as the hook does; a disabled or vanished one is no work."""
    try:
        return LocalMemoryProjectBindingStore(data_directory).get(project_root)
    except (AutomaticMemoryBindingError, OSError):
        return None


def _flags(*, create: bool) -> int:
    flags = os.O_RDWR | (os.O_CREAT if create else 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return flags
