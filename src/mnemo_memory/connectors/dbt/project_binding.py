"""Machine-local, explicit dbt-project to Mnemo-scope bindings."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

from mnemo_memory.packages.domain import (
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    Visibility,
    WorkspaceId,
)


class DbtProjectBindingError(ValueError):
    """Safe configuration error; callers expose only its stable code."""


@dataclass(frozen=True, slots=True)
class PersonalDbtProfile:
    """Stable local owner/workspace identities for personal-mode dbt bindings."""

    owner_id: OwnerId
    workspace_id: WorkspaceId

    def __post_init__(self) -> None:
        if not isinstance(self.owner_id, OwnerId) or not isinstance(self.workspace_id, WorkspaceId):
            raise TypeError("personal dbt profile identifiers are invalid")

    @classmethod
    def new(cls) -> PersonalDbtProfile:
        return cls(OwnerId.new(), WorkspaceId.new())

    @classmethod
    def from_dict(cls, value: object) -> PersonalDbtProfile:
        if not isinstance(value, dict) or set(value) != {"owner_id", "workspace_id"}:
            raise DbtProjectBindingError("MNEMO_DBT_PERSONAL_PROFILE_INVALID")
        try:
            return cls(
                OwnerId.from_string(value["owner_id"]),
                WorkspaceId.from_string(value["workspace_id"]),
            )
        except (TypeError, ValueError) as error:
            raise DbtProjectBindingError("MNEMO_DBT_PERSONAL_PROFILE_INVALID") from error

    def to_dict(self) -> dict[str, str]:
        return {"owner_id": str(self.owner_id), "workspace_id": str(self.workspace_id)}

    def project_scope(self, project_id: ProjectId | None = None) -> MemoryScope:
        return MemoryScope(
            self.owner_id,
            ScopeLevel.PROJECT,
            Visibility.PROJECT,
            self.workspace_id,
            project_id or ProjectId.new(),
        )


@dataclass(frozen=True, slots=True)
class DbtProjectBinding:
    project_root: Path
    scope: MemoryScope

    def __post_init__(self) -> None:
        if (
            not self.project_root.is_absolute()
            or not (self.project_root / "dbt_project.yml").is_file()
        ):
            raise DbtProjectBindingError("MNEMO_DBT_PROJECT_ROOT_INVALID")


def find_dbt_project_root(path: Path) -> Path:
    candidate = path.resolve()
    if candidate.is_file():
        candidate = candidate.parent
    for parent in (candidate, *candidate.parents):
        if (parent / "dbt_project.yml").is_file():
            return parent
    raise DbtProjectBindingError("MNEMO_DBT_PROJECT_ROOT_NOT_FOUND")


class LocalDbtProjectBindingStore:
    """Private local configuration; stable scopes are never inferred from paths."""

    _filename = "dbt-project-bindings.json"
    _profile_filename = "dbt-personal-profile.json"

    def __init__(self, data_directory: Path) -> None:
        self._directory = data_directory.resolve()
        self._path = self._directory / self._filename
        self._profile_path = self._directory / self._profile_filename

    def personal_profile(self) -> PersonalDbtProfile:
        """Return the persisted personal identity pair, creating it once when absent."""
        profile = self._read_profile()
        if profile is not None:
            return profile
        created = PersonalDbtProfile.new()
        self._write_file(self._profile_path, created.to_dict())
        # Read back the committed profile so every later binding uses the persisted identities.
        profile = self._read_profile()
        if profile is None:
            raise DbtProjectBindingError("MNEMO_DBT_PERSONAL_PROFILE_INVALID")
        return profile

    def get(self, project_root: Path) -> DbtProjectBinding | None:
        root = find_dbt_project_root(project_root)
        raw = self._read()
        value = raw.get(str(root))
        if value is None:
            return None
        if not isinstance(value, dict):
            raise DbtProjectBindingError("MNEMO_DBT_BINDING_INVALID")
        try:
            return DbtProjectBinding(root, MemoryScope.from_dict(value))
        except (TypeError, ValueError) as error:
            raise DbtProjectBindingError("MNEMO_DBT_BINDING_INVALID") from error

    def set(self, binding: DbtProjectBinding) -> None:
        data = self._read()
        data[str(binding.project_root.resolve())] = binding.scope.to_dict()
        self._write(data)

    def remove(self, project_root: Path) -> bool:
        root = find_dbt_project_root(project_root)
        data = self._read()
        removed = data.pop(str(root), None) is not None
        if removed:
            self._write(data)
        return removed

    def _read(self) -> dict[str, object]:
        if not self._path.exists():
            return {}
        if self._path.is_symlink():
            raise DbtProjectBindingError("MNEMO_DBT_BINDING_UNSAFE")
        try:
            value = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise DbtProjectBindingError("MNEMO_DBT_BINDING_INVALID") from error
        if not isinstance(value, dict):
            raise DbtProjectBindingError("MNEMO_DBT_BINDING_INVALID")
        return value

    def _read_profile(self) -> PersonalDbtProfile | None:
        if not self._profile_path.exists():
            return None
        if self._profile_path.is_symlink():
            raise DbtProjectBindingError("MNEMO_DBT_BINDING_UNSAFE")
        try:
            return PersonalDbtProfile.from_dict(json.loads(self._profile_path.read_text()))
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise DbtProjectBindingError("MNEMO_DBT_PERSONAL_PROFILE_INVALID") from error

    def _write(self, data: dict[str, object]) -> None:
        self._write_file(self._path, data)

    def _write_file(self, destination: Path, data: dict[str, object] | dict[str, str]) -> None:
        self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if destination.exists() and destination.is_symlink():
            raise DbtProjectBindingError("MNEMO_DBT_BINDING_UNSAFE")
        with NamedTemporaryFile("w", encoding="utf-8", dir=self._directory, delete=False) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            json.dump(data, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
        try:
            os.replace(temporary, destination)
            os.chmod(destination, 0o600)
        finally:
            temporary.unlink(missing_ok=True)
