"""Strict, secret-free personal settings stored beside the local profile."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, ClassVar, Self

from mnemo_memory.packages.application.automatic_memory import (
    AutomaticMemoryBindingError,
    exclusive_local_file_lock,
)
from mnemo_memory.packages.domain import (
    ContextBudget,
    TypedDecisionDataRoute,
    TypedDecisionKind,
    TypedDecisionMode,
)

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
    "experimental_typed_decisions_enabled",
    "typed_decision_data_route",
    "typed_decision_daily_input_tokens",
    "typed_decision_model_id",
    "typed_decision_modes",
}
DEFAULT_TYPED_DECISION_DAILY_INPUT_TOKENS = 10_000_000
_MAXIMUM_TYPED_DECISION_DAILY_INPUT_TOKENS = 1_000_000_000


class TypedDecisionLock(StrEnum):
    """Why a typed-decision mode is refused (spec 2026-10-02 §6); checked in this order."""

    MASTER_SWITCH = "master_switch"
    SEMANTIC_MEMORY_GATE = "semantic_memory_gate"
    DATA_ROUTE = "data_route"


TYPED_DECISION_LOCK_MESSAGES: dict[TypedDecisionLock, str] = {
    TypedDecisionLock.MASTER_SWITCH: (
        "typed decision modes other than off need experimental_typed_decisions_enabled"
    ),
    TypedDecisionLock.SEMANTIC_MEMORY_GATE: (
        "front_door live needs experimental_semantic_memory_enabled"
    ),
    TypedDecisionLock.DATA_ROUTE: (
        "live typed decisions are locked while the data route is synthetic_only; use off or shadow"
    ),
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
    experimental_typed_decisions_enabled: bool = False
    typed_decision_data_route: str = TypedDecisionDataRoute.SYNTHETIC_ONLY.value
    typed_decision_model_id: str = "jev-1.13.0"
    typed_decision_modes: tuple[tuple[str, str], ...] = ()
    typed_decision_daily_input_tokens: int = DEFAULT_TYPED_DECISION_DAILY_INPUT_TOKENS

    def __post_init__(self) -> None:
        for name in (
            "repository_knowledge_sync_enabled",
            "approved_event_capture_enabled",
            "experimental_semantic_memory_enabled",
            "optional_model_enabled",
            "experimental_local_first_takeover_enabled",
            "local_first_takeover_live_calls_authorized",
            "experimental_typed_decisions_enabled",
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
            TypedDecisionDataRoute(self.typed_decision_data_route)
        except ValueError as error:
            raise PersonalSettingsError("typed decision data route is not supported") from error
        if _optional_metadata(self.typed_decision_model_id, "typed decision model id") is None:
            raise PersonalSettingsError("typed decision model id is required")
        object.__setattr__(
            self, "typed_decision_modes", _typed_decision_modes(self.typed_decision_modes)
        )
        modes = dict(self.typed_decision_modes)
        if not self.experimental_typed_decisions_enabled and any(
            mode != TypedDecisionMode.OFF.value for mode in modes.values()
        ):
            raise PersonalSettingsError(
                TYPED_DECISION_LOCK_MESSAGES[TypedDecisionLock.MASTER_SWITCH]
            )
        if (
            modes.get(TypedDecisionKind.FRONT_DOOR.value) == TypedDecisionMode.LIVE.value
            and not self.experimental_semantic_memory_enabled
        ):
            raise PersonalSettingsError(
                TYPED_DECISION_LOCK_MESSAGES[TypedDecisionLock.SEMANTIC_MEMORY_GATE]
            )
        if (
            TypedDecisionDataRoute(self.typed_decision_data_route)
            is TypedDecisionDataRoute.SYNTHETIC_ONLY
            and TypedDecisionMode.LIVE.value in modes.values()
        ):
            raise PersonalSettingsError(TYPED_DECISION_LOCK_MESSAGES[TypedDecisionLock.DATA_ROUTE])
        daily = self.typed_decision_daily_input_tokens
        if (
            isinstance(daily, bool)
            or not isinstance(daily, int)
            or not 1 <= daily <= _MAXIMUM_TYPED_DECISION_DAILY_INPUT_TOKENS
        ):
            raise PersonalSettingsError(
                "typed decision daily input tokens must be between 1 and 1000000000"
            )
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

    def typed_decision_mode(self, kind: TypedDecisionKind) -> TypedDecisionMode:
        """Return one decision's mode; decisions not listed are off."""

        return TypedDecisionMode(
            dict(self.typed_decision_modes).get(kind.value, TypedDecisionMode.OFF.value)
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
            "experimental_typed_decisions_enabled": self.experimental_typed_decisions_enabled,
            "local_first_takeover_live_calls_authorized": (
                self.local_first_takeover_live_calls_authorized
            ),
            "model_id": self.model_id,
            "model_provider": self.model_provider,
            "optional_model_enabled": self.optional_model_enabled,
            "repository_knowledge_sync_enabled": self.repository_knowledge_sync_enabled,
            "typed_decision_data_route": self.typed_decision_data_route,
            "typed_decision_daily_input_tokens": self.typed_decision_daily_input_tokens,
            "typed_decision_model_id": self.typed_decision_model_id,
            "typed_decision_modes": dict(self.typed_decision_modes),
        }

    _MIGRATED_DEFAULTS: ClassVar[dict[str, object]] = {
        "experimental_semantic_memory_enabled": False,
        "experimental_local_first_takeover_enabled": False,
        "local_first_takeover_live_calls_authorized": False,
        "context_save_growth_bytes": 200_000,
        "experimental_typed_decisions_enabled": False,
        "typed_decision_data_route": TypedDecisionDataRoute.SYNTHETIC_ONLY.value,
        "typed_decision_daily_input_tokens": DEFAULT_TYPED_DECISION_DAILY_INPUT_TOKENS,
        "typed_decision_model_id": "jev-1.13.0",
        "typed_decision_modes": {},
    }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        if not isinstance(value, dict):
            raise PersonalSettingsError("personal settings fields are invalid")
        fields: dict[str, Any] = dict(value)
        missing = _FIELDS - set(fields)
        if missing and missing <= set(cls._MIGRATED_DEFAULTS):
            fields = {**{k: cls._MIGRATED_DEFAULTS[k] for k in missing}, **fields}
        if set(fields) != _FIELDS:
            raise PersonalSettingsError("personal settings fields are invalid")
        modes = fields["typed_decision_modes"]
        if not isinstance(modes, dict):
            raise PersonalSettingsError("personal settings values are invalid")
        fields["typed_decision_modes"] = tuple(modes.items())
        try:
            return cls(**fields)
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


def _typed_decision_modes(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, tuple):
        raise PersonalSettingsError("typed decision modes are invalid")
    modes: dict[str, str] = {}
    for item in value:
        if not isinstance(item, tuple) or len(item) != 2:
            raise PersonalSettingsError("typed decision modes are invalid")
        try:
            kind = TypedDecisionKind(item[0]).value
            mode = TypedDecisionMode(item[1]).value
        except ValueError as error:
            raise PersonalSettingsError("typed decision modes are invalid") from error
        if kind in modes:
            raise PersonalSettingsError("typed decision modes are invalid")
        modes[kind] = mode
    return tuple(sorted(modes.items()))


def active_typed_decision_locks(settings: PersonalSettings) -> tuple[TypedDecisionLock, ...]:
    """Return the locks that would refuse some mode today, in check order."""

    locks: list[TypedDecisionLock] = []
    if not settings.experimental_typed_decisions_enabled:
        locks.append(TypedDecisionLock.MASTER_SWITCH)
    if not settings.experimental_semantic_memory_enabled:
        locks.append(TypedDecisionLock.SEMANTIC_MEMORY_GATE)
    if (
        TypedDecisionDataRoute(settings.typed_decision_data_route)
        is TypedDecisionDataRoute.SYNTHETIC_ONLY
    ):
        locks.append(TypedDecisionLock.DATA_ROUTE)
    return tuple(locks)


def with_typed_decision_mode(
    settings: PersonalSettings, kind: TypedDecisionKind, mode: TypedDecisionMode
) -> PersonalSettings:
    """Return ``settings`` with one mode changed; a lock refuses it with its plain message."""

    modes = dict(settings.typed_decision_modes)
    modes[TypedDecisionKind(kind).value] = TypedDecisionMode(mode).value
    return replace(settings, typed_decision_modes=tuple(sorted(modes.items())))
