"""Strict, secret-free personal settings stored beside the local profile."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import ClassVar, Self

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
    "context_save_growth_bytes",
    "context_skills_tokens",
    "context_structural_tokens",
    "context_total_tokens",
    "episodic_retention_days",
    "experimental_local_first_takeover_enabled",
    "experimental_semantic_memory_enabled",
    "local_first_takeover_live_calls_authorized",
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
    experimental_semantic_memory_enabled: bool = False
    optional_model_enabled: bool = False
    experimental_local_first_takeover_enabled: bool = False
    local_first_takeover_live_calls_authorized: bool = False
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
    context_save_growth_bytes: int = 200_000

    def __post_init__(self) -> None:
        for name in (
            "repository_knowledge_sync_enabled",
            "approved_event_capture_enabled",
            "experimental_semantic_memory_enabled",
            "optional_model_enabled",
            "experimental_local_first_takeover_enabled",
            "local_first_takeover_live_calls_authorized",
        ):
            if not isinstance(getattr(self, name), bool):
                raise PersonalSettingsError(f"{name} must be a boolean")
        if not isinstance(self.episodic_retention_days, int) or not (
            1 <= self.episodic_retention_days <= 3_650
        ):
            raise PersonalSettingsError("episodic retention must be between 1 and 3650 days")
        if (
            isinstance(self.context_save_growth_bytes, bool)
            or not isinstance(self.context_save_growth_bytes, int)
            or not 0 <= self.context_save_growth_bytes <= 100_000_000
        ):
            raise PersonalSettingsError("context save growth must be between 0 and 100000000 bytes")
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
    def episodic_extraction_enabled(self) -> bool:
        """Whether local Ollama episodic extraction should be wired at construction sites."""
        return (
            self.optional_model_enabled
            and self.model_provider == "ollama"
            and self.model_id is not None
        )

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
            "context_save_growth_bytes": self.context_save_growth_bytes,
            "context_skills_tokens": self.context_skills_tokens,
            "context_structural_tokens": self.context_structural_tokens,
            "context_total_tokens": self.context_total_tokens,
            "episodic_retention_days": self.episodic_retention_days,
            "experimental_local_first_takeover_enabled": (
                self.experimental_local_first_takeover_enabled
            ),
            "experimental_semantic_memory_enabled": self.experimental_semantic_memory_enabled,
            "local_first_takeover_live_calls_authorized": (
                self.local_first_takeover_live_calls_authorized
            ),
            "model_id": self.model_id,
            "model_provider": self.model_provider,
            "optional_model_enabled": self.optional_model_enabled,
            "repository_knowledge_sync_enabled": self.repository_knowledge_sync_enabled,
        }

    _MIGRATED_DEFAULTS: ClassVar[dict[str, bool | int]] = {
        "experimental_semantic_memory_enabled": False,
        "experimental_local_first_takeover_enabled": False,
        "local_first_takeover_live_calls_authorized": False,
        "context_save_growth_bytes": 200_000,
    }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        if not isinstance(value, dict):
            raise PersonalSettingsError("personal settings fields are invalid")
        missing = _FIELDS - set(value)
        if missing and missing <= set(cls._MIGRATED_DEFAULTS):
            value = {**{k: cls._MIGRATED_DEFAULTS[k] for k in missing}, **value}
        if set(value) != _FIELDS:
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
