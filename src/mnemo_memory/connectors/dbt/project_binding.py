"""Machine-local, explicit dbt-project to Mnemo-scope bindings."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

from mnemo_memory.packages.domain import MemoryScope


class DbtProjectBindingError(ValueError):
    """Safe configuration error; callers expose only its stable code."""


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

    def __init__(self, data_directory: Path) -> None:
        self._directory = data_directory.resolve()
        self._path = self._directory / self._filename

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

    def _write(self, data: dict[str, object]) -> None:
        self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self._path.exists() and self._path.is_symlink():
            raise DbtProjectBindingError("MNEMO_DBT_BINDING_UNSAFE")
        with NamedTemporaryFile("w", encoding="utf-8", dir=self._directory, delete=False) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            json.dump(data, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
        try:
            os.replace(temporary, self._path)
            os.chmod(self._path, 0o600)
        finally:
            temporary.unlink(missing_ok=True)
