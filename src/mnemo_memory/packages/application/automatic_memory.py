"""Local, explicit project bindings for opt-in automatic task memory.

The binding is deliberately machine-local.  It maps a trusted project directory to
opaque stable identifiers; paths are never used as identities and nothing from a
project's files is persisted here.
"""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import cast

from mnemo_memory.packages.domain import (
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    SessionId,
    TaskId,
    Visibility,
    WorkspaceId,
)


class AutomaticMemoryBindingError(ValueError):
    """Safe local-binding failure; callers expose only the stable code."""


@contextmanager
def exclusive_local_file_lock(
    directory: Path, name: str, *, create_directory: bool = True
) -> Iterator[None]:
    """Serialize a small local configuration update without following a symlink.

    Mnemo currently supports macOS and Linux, where ``flock`` provides the process-level
    serialization needed around read-modify-replace JSON updates.  The lock contains no user
    data and is removed only by normal operating-system cleanup when the directory is deleted.
    """
    if not name or "/" in name or "\\" in name:
        raise AutomaticMemoryBindingError("MNEMO_MEMORY_LOCK_INVALID")
    try:
        if create_directory:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        elif not directory.is_dir():
            raise AutomaticMemoryBindingError("MNEMO_MEMORY_LOCK_UNAVAILABLE")
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(directory / name, flags, 0o600)
    except OSError as error:
        raise AutomaticMemoryBindingError("MNEMO_MEMORY_LOCK_UNAVAILABLE") from error
    try:
        os.chmod(directory / name, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    except OSError as error:
        raise AutomaticMemoryBindingError("MNEMO_MEMORY_LOCK_UNAVAILABLE") from error
    finally:
        with suppress(OSError):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


@dataclass(frozen=True, slots=True)
class PersonalMemoryProfile:
    """Stable private owner/workspace identifiers for one personal profile."""

    owner_id: OwnerId
    workspace_id: WorkspaceId

    @classmethod
    def new(cls) -> PersonalMemoryProfile:
        return cls(OwnerId.new(), WorkspaceId.new())

    def scope(self) -> MemoryScope:
        return MemoryScope(
            self.owner_id,
            ScopeLevel.PROJECT,
            Visibility.PROJECT,
            self.workspace_id,
            ProjectId.new(),
        )

    @staticmethod
    def task_scope(project_scope: MemoryScope) -> MemoryScope:
        return MemoryScope(
            project_scope.owner_id,
            ScopeLevel.TASK,
            project_scope.visibility,
            project_scope.workspace_id,
            project_scope.project_id,
            SessionId.new(),
            TaskId.new(),
        )

    def to_dict(self) -> dict[str, str]:
        return {"owner_id": str(self.owner_id), "workspace_id": str(self.workspace_id)}

    @classmethod
    def from_dict(cls, value: object) -> PersonalMemoryProfile:
        if not isinstance(value, dict) or set(value) != {"owner_id", "workspace_id"}:
            raise AutomaticMemoryBindingError("MNEMO_MEMORY_PROFILE_INVALID")
        try:
            owner = value["owner_id"]
            workspace = value["workspace_id"]
            if not isinstance(owner, str) or not isinstance(workspace, str):
                raise TypeError
            return cls(OwnerId.from_string(owner), WorkspaceId.from_string(workspace))
        except (TypeError, ValueError) as error:
            raise AutomaticMemoryBindingError("MNEMO_MEMORY_PROFILE_INVALID") from error


@dataclass(frozen=True, slots=True)
class MemoryProjectBinding:
    project_root: Path
    scope: MemoryScope
    checkpoint_scope: MemoryScope

    def __post_init__(self) -> None:
        if not self.project_root.is_absolute() or not self.project_root.is_dir():
            raise AutomaticMemoryBindingError("MNEMO_MEMORY_PROJECT_ROOT_INVALID")
        if self.scope.level is not ScopeLevel.PROJECT:
            raise AutomaticMemoryBindingError("MNEMO_MEMORY_SCOPE_INVALID")
        if (
            self.checkpoint_scope.level is not ScopeLevel.TASK
            or self.checkpoint_scope.owner_id != self.scope.owner_id
            or self.checkpoint_scope.workspace_id != self.scope.workspace_id
            or self.checkpoint_scope.project_id != self.scope.project_id
        ):
            raise AutomaticMemoryBindingError("MNEMO_MEMORY_TASK_SCOPE_INVALID")

    def to_dict(self) -> dict[str, object]:
        return {
            "project_scope": self.scope.to_dict(),
            "checkpoint_scope": self.checkpoint_scope.to_dict(),
        }

    @classmethod
    def from_dict(cls, root: Path, value: object) -> MemoryProjectBinding:
        if not isinstance(value, dict):
            raise AutomaticMemoryBindingError("MNEMO_MEMORY_BINDING_INVALID")
        try:
            return cls(
                root,
                MemoryScope.from_dict(value["project_scope"]),
                MemoryScope.from_dict(value["checkpoint_scope"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise AutomaticMemoryBindingError("MNEMO_MEMORY_BINDING_INVALID") from error


def find_memory_project_root(path: Path) -> Path:
    """Find the nearest repository root, or use the explicit existing directory."""
    candidate = path.expanduser().resolve()
    if candidate.is_file():
        candidate = candidate.parent
    if not candidate.is_dir():
        raise AutomaticMemoryBindingError("MNEMO_MEMORY_PROJECT_ROOT_INVALID")
    for parent in (candidate, *candidate.parents):
        if (parent / ".git").exists():
            return parent
    return candidate


class LocalMemoryProjectBindingStore:
    """Symlink-safe private configuration for normal personal-mode onboarding."""

    _profile_name = "automatic-memory-profile.json"
    _bindings_name = "automatic-memory-project-bindings.json"
    _lock_name = ".automatic-memory.lock"

    def __init__(self, data_directory: Path) -> None:
        self._directory = data_directory.expanduser().resolve()
        self._profile_path = self._directory / self._profile_name
        self._bindings_path = self._directory / self._bindings_name

    def enable(
        self, project_dir: Path, *, project_scope: MemoryScope | None = None
    ) -> MemoryProjectBinding:
        """Enable one local repository, optionally reusing an established project scope.

        A dbt binding may supply the scope so checkpoint and manifest facts can share one
        explicit project boundary.  Paths still only locate the local binding; they never
        produce an identity.
        """
        root = find_memory_project_root(project_dir)
        with exclusive_local_file_lock(self._directory, self._lock_name):
            existing = self._get(root)
            if existing is not None:
                if project_scope is not None and project_scope != existing.scope:
                    raise AutomaticMemoryBindingError("MNEMO_MEMORY_PROJECT_SCOPE_CONFLICT")
                return existing
            scope = project_scope or self._personal_profile().scope()
            if scope.level is not ScopeLevel.PROJECT:
                raise AutomaticMemoryBindingError("MNEMO_MEMORY_SCOPE_INVALID")
            binding = MemoryProjectBinding(root, scope, PersonalMemoryProfile.task_scope(scope))
            values = self._read_bindings()
            values[str(root)] = binding.to_dict()
            self._write(self._bindings_path, values)
            return binding

    def get(self, project_dir: Path) -> MemoryProjectBinding | None:
        root = find_memory_project_root(project_dir)
        if not self._directory.exists():
            return None
        with exclusive_local_file_lock(self._directory, self._lock_name, create_directory=False):
            return self._get(root)

    def _get(self, root: Path) -> MemoryProjectBinding | None:
        value = self._read_bindings().get(str(root))
        if value is None:
            return None
        return MemoryProjectBinding.from_dict(root, value)

    def disable(self, project_dir: Path) -> bool:
        root = find_memory_project_root(project_dir)
        if not self._directory.exists():
            return False
        with exclusive_local_file_lock(self._directory, self._lock_name, create_directory=False):
            values = self._read_bindings()
            removed = values.pop(str(root), None) is not None
            if removed:
                self._write(self._bindings_path, values)
            return removed

    def personal_profile(self) -> PersonalMemoryProfile:
        with exclusive_local_file_lock(self._directory, self._lock_name):
            return self._personal_profile()

    def _personal_profile(self) -> PersonalMemoryProfile:
        value = self._read_json(self._profile_path)
        if value is not None:
            return PersonalMemoryProfile.from_dict(value)
        created = PersonalMemoryProfile.new()
        self._write(self._profile_path, created.to_dict())
        value = self._read_json(self._profile_path)
        if value is None:
            raise AutomaticMemoryBindingError("MNEMO_MEMORY_PROFILE_INVALID")
        return PersonalMemoryProfile.from_dict(value)

    def _read_bindings(self) -> dict[str, object]:
        value = self._read_json(self._bindings_path)
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise AutomaticMemoryBindingError("MNEMO_MEMORY_BINDING_INVALID")
        return value

    def _read_json(self, path: Path) -> object | None:
        if not path.exists():
            return None
        if path.is_symlink():
            raise AutomaticMemoryBindingError("MNEMO_MEMORY_BINDING_UNSAFE")
        try:
            return cast(object, json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as error:
            raise AutomaticMemoryBindingError("MNEMO_MEMORY_BINDING_INVALID") from error

    def _write(self, destination: Path, value: object) -> None:
        self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if destination.exists() and destination.is_symlink():
            raise AutomaticMemoryBindingError("MNEMO_MEMORY_BINDING_UNSAFE")
        temporary: Path | None = None
        try:
            with NamedTemporaryFile(
                "w", encoding="utf-8", dir=self._directory, delete=False
            ) as handle:
                temporary = Path(handle.name)
                os.chmod(temporary, 0o600)
                json.dump(value, handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
            os.replace(temporary, destination)
            os.chmod(destination, 0o600)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
