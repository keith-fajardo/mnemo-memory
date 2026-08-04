"""Bounded, local Git evidence for already-enabled source snapshots.

This connector never reads a diff, commit message, configured remotes, environment values, or
source contents.  It observes only Git object IDs and whether Git reports a dirty working tree.
The small private cache links that evidence to Mnemo's existing source digest; it is not a second
source index or an explanation of why a change occurred.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile

from mnemo_memory.packages.application.automatic_memory import (
    AutomaticMemoryBindingError,
    exclusive_local_file_lock,
)
from mnemo_memory.packages.domain import MemoryScope

_GIT_ID = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_MAX_SNAPSHOTS_PER_SCOPE = 64
_Runner = Callable[[tuple[str, ...], Path], str | None]


@dataclass(frozen=True, slots=True)
class GitSourceObservation:
    """Safe Git state attached to one immutable Mnemo source digest."""

    source_digest: str
    commit_id: str | None
    parent_commit_id: str | None
    dirty: bool

    def __post_init__(self) -> None:
        if not self.source_digest.startswith("sha256:") or len(self.source_digest) != 71:
            raise ValueError("Git observation requires a source digest")
        for value in (self.commit_id, self.parent_commit_id):
            if value is not None and _GIT_ID.fullmatch(value) is None:
                raise ValueError("Git observation requires a full lowercase object ID")

    def to_dict(self) -> dict[str, object]:
        return {
            "commit_id": self.commit_id,
            "dirty": self.dirty,
            "parent_commit_id": self.parent_commit_id,
            "source_digest": self.source_digest,
        }

    @classmethod
    def from_dict(cls, value: object) -> GitSourceObservation:
        if not isinstance(value, dict) or set(value) != {
            "commit_id",
            "dirty",
            "parent_commit_id",
            "source_digest",
        }:
            raise ValueError("Git observation fields are invalid")
        commit_id = value["commit_id"]
        parent_commit_id = value["parent_commit_id"]
        source_digest = value["source_digest"]
        dirty = value["dirty"]
        if (
            (commit_id is not None and not isinstance(commit_id, str))
            or (parent_commit_id is not None and not isinstance(parent_commit_id, str))
            or not isinstance(source_digest, str)
            or not isinstance(dirty, bool)
        ):
            raise ValueError("Git observation values are invalid")
        return cls(source_digest, commit_id, parent_commit_id, dirty)


class GitSourceObserver:
    """Probe only safe local Git state with bounded, shell-free subprocess calls."""

    def __init__(self, runner: _Runner | None = None) -> None:
        self._runner = runner or _run_git

    def observe(self, root: Path, source_digest: str) -> GitSourceObservation | None:
        if not root.is_absolute() or not root.is_dir() or not source_digest.startswith("sha256:"):
            return None
        if self._run(("rev-parse", "--is-inside-work-tree"), root) != "true":
            return None
        commit_id = self._run(("rev-parse", "--verify", "HEAD"), root)
        if commit_id is None or _GIT_ID.fullmatch(commit_id) is None:
            return None
        parent = self._run(("rev-parse", "--verify", "HEAD^"), root)
        parent_commit_id = parent if parent is not None and _GIT_ID.fullmatch(parent) else None
        status = self._run(("status", "--porcelain=v1", "-z"), root)
        if status is None:
            return None
        return GitSourceObservation(source_digest, commit_id, parent_commit_id, bool(status))

    def _run(self, arguments: tuple[str, ...], root: Path) -> str | None:
        value = self._runner(arguments, root)
        return value.strip() if isinstance(value, str) else None


def _run_git(arguments: tuple[str, ...], root: Path) -> str | None:
    executable = shutil.which("git")
    if executable is None:
        return None
    try:
        completed = subprocess.run(
            (executable, "-C", str(root), *arguments),
            check=False,
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout if completed.returncode == 0 else None


class GitObservationStore:
    """Atomic bounded local cache keyed by hashed scope and immutable source digest."""

    _name = "automatic-memory-git-observations.json"

    def __init__(self, data_directory: Path) -> None:
        self._directory = data_directory.expanduser().resolve()
        self._path = self._directory / self._name

    def get(self, scope: MemoryScope, source_digest: str) -> GitSourceObservation | None:
        raw = self._read().get(_scope_key(scope), {}).get(source_digest)
        try:
            return GitSourceObservation.from_dict(raw)
        except ValueError:
            return None

    def put(self, scope: MemoryScope, observation: GitSourceObservation) -> None:
        try:
            with exclusive_local_file_lock(self._directory, ".automatic-memory-git.lock"):
                values = self._read()
                key = _scope_key(scope)
                scoped = values.setdefault(key, {})
                scoped[observation.source_digest] = observation.to_dict()
                if len(scoped) > _MAX_SNAPSHOTS_PER_SCOPE:
                    for digest in sorted(scoped)[:-_MAX_SNAPSHOTS_PER_SCOPE]:
                        scoped.pop(digest, None)
                self._write(values)
        except AutomaticMemoryBindingError:
            return

    def _read(self) -> dict[str, dict[str, object]]:
        if not self._path.exists() or self._path.is_symlink():
            return {}
        try:
            value = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(value, dict):
            return {}
        return {
            key: child
            for key, child in value.items()
            if isinstance(key, str) and isinstance(child, dict)
        }

    def _write(self, values: dict[str, dict[str, object]]) -> None:
        temporary: Path | None = None
        try:
            self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self._path.exists() and self._path.is_symlink():
                return
            with NamedTemporaryFile(
                "w", encoding="utf-8", dir=self._directory, delete=False
            ) as handle:
                temporary = Path(handle.name)
                os.chmod(temporary, 0o600)
                json.dump(values, handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
            os.replace(temporary, self._path)
            os.chmod(self._path, 0o600)
        except OSError:
            return
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def _scope_key(scope: MemoryScope) -> str:
    return sha256(
        json.dumps(scope.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
