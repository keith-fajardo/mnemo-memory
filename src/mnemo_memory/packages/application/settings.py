"""Strict, secret-free personal settings stored beside the local profile."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Self

from mnemo_memory.packages.application.automatic_memory import (
    AutomaticMemoryBindingError,
    exclusive_local_file_lock,
)
from mnemo_memory.packages.domain import ContextBudget

_METADATA = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_FIELDS = {
    "approved_event_capture_enabled",
    "context_active_task_checkpoint_tokens",
    "context_episodic_tokens",
    "context_knowledge_tokens",
    "context_provenance_tokens",
    "context_skills_tokens",
    "context_structural_tokens",
    "context_total_tokens",
    "episodic_retention_days",
    "model_id",
    "model_provider",
    "optional_model_enabled",
    "repository_knowledge_sync_enabled",
}


class PersonalSettingsError(ValueError):
    """Stable failure reading or replacing local settings."""


@dataclass(frozen=True, slots=True)
class PersonalSettings:
    repository_knowledge_sync_enabled: bool = True
    approved_event_capture_enabled: bool = True
    optional_model_enabled: bool = False
    model_provider: str | None = None
    model_id: str | None = None
    episodic_retention_days: int = 180
    context_active_task_checkpoint_tokens: int = 600
    context_episodic_tokens: int = 800
    context_knowledge_tokens: int = 1_200
    context_structural_tokens: int = 1_500
    context_skills_tokens: int = 1_200
    context_provenance_tokens: int = 400
    context_total_tokens: int = 5_700

    def __post_init__(self) -> None:
        for name in (
            "repository_knowledge_sync_enabled",
            "approved_event_capture_enabled",
            "optional_model_enabled",
        ):
            if not isinstance(getattr(self, name), bool):
                raise PersonalSettingsError(f"{name} must be a boolean")
        if not isinstance(self.episodic_retention_days, int) or not (
            1 <= self.episodic_retention_days <= 3_650
        ):
            raise PersonalSettingsError("episodic retention must be between 1 and 3650 days")
        provider = _optional_metadata(self.model_provider, "model provider")
        model = _optional_metadata(self.model_id, "model id")
        object.__setattr__(self, "model_provider", provider)
        object.__setattr__(self, "model_id", model)
        if self.optional_model_enabled and (provider is None or model is None):
            raise PersonalSettingsError("enabled optional model requires provider and model id")
        if not self.optional_model_enabled and (provider is not None or model is not None):
            raise PersonalSettingsError("disabled optional model cannot retain routing metadata")
        try:
            _ = self.context_budget
        except (TypeError, ValueError) as error:
            raise PersonalSettingsError("context budget is invalid") from error

    @property
    def context_budget(self) -> ContextBudget:
        return ContextBudget(
            active_task_checkpoint=self.context_active_task_checkpoint_tokens,
            episodic_memories=self.context_episodic_tokens,
            knowledge=self.context_knowledge_tokens,
            structural=self.context_structural_tokens,
            skills_and_procedures=self.context_skills_tokens,
            provenance_and_conflicts=self.context_provenance_tokens,
            total_limit=self.context_total_tokens,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "approved_event_capture_enabled": self.approved_event_capture_enabled,
            "context_active_task_checkpoint_tokens": self.context_active_task_checkpoint_tokens,
            "context_episodic_tokens": self.context_episodic_tokens,
            "context_knowledge_tokens": self.context_knowledge_tokens,
            "context_provenance_tokens": self.context_provenance_tokens,
            "context_skills_tokens": self.context_skills_tokens,
            "context_structural_tokens": self.context_structural_tokens,
            "context_total_tokens": self.context_total_tokens,
            "episodic_retention_days": self.episodic_retention_days,
            "model_id": self.model_id,
            "model_provider": self.model_provider,
            "optional_model_enabled": self.optional_model_enabled,
            "repository_knowledge_sync_enabled": self.repository_knowledge_sync_enabled,
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        if not isinstance(value, dict) or set(value) != _FIELDS:
            raise PersonalSettingsError("personal settings fields are invalid")
        try:
            return cls(**value)
        except TypeError as error:
            raise PersonalSettingsError("personal settings values are invalid") from error


class PersonalSettingsStore:
    _name = "settings.json"
    _lock_name = ".settings.lock"

    def __init__(self, data_directory: Path) -> None:
        self._directory = data_directory.expanduser().resolve()
        self._path = self._directory / self._name

    def load(self) -> PersonalSettings:
        if not self._path.exists():
            return PersonalSettings()
        if self._path.is_symlink() or not self._path.is_file():
            raise PersonalSettingsError("MNEMO_SETTINGS_INVALID")
        try:
            if self._path.stat().st_size > 16_384:
                raise PersonalSettingsError("MNEMO_SETTINGS_INVALID")
            return PersonalSettings.from_dict(json.loads(self._path.read_text("utf-8")))
        except (OSError, UnicodeError, json.JSONDecodeError, PersonalSettingsError) as error:
            raise PersonalSettingsError("MNEMO_SETTINGS_INVALID") from error

    def save(self, settings: PersonalSettings) -> PersonalSettings:
        if not isinstance(settings, PersonalSettings):
            raise PersonalSettingsError("MNEMO_SETTINGS_INVALID")
        self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = json.dumps(settings.to_dict(), sort_keys=True, separators=(",", ":"))
        try:
            with exclusive_local_file_lock(self._directory, self._lock_name):
                with NamedTemporaryFile(
                    "w", encoding="utf-8", dir=self._directory, delete=False
                ) as temporary:
                    temporary.write(payload)
                    temporary.flush()
                    os.fsync(temporary.fileno())
                    temporary_path = Path(temporary.name)
                os.chmod(temporary_path, 0o600)
                os.replace(temporary_path, self._path)
        except (AutomaticMemoryBindingError, OSError) as error:
            raise PersonalSettingsError("MNEMO_SETTINGS_WRITE_FAILED") from error
        return settings


def _optional_metadata(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _METADATA.fullmatch(value.strip()):
        raise PersonalSettingsError(f"{name} is invalid")
    return value.strip()
