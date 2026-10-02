from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import mnemo_memory.apps.cli.main as cli_main
from mnemo_memory.apps.api.app import create_app
from mnemo_memory.packages.application import (
    LocalConfig,
    PersonalSettings,
    PersonalSettingsError,
    PersonalSettingsStore,
    build_lifecycle_service,
)
from mnemo_memory.packages.application.automatic_memory import LocalMemoryProjectBindingStore
from mnemo_memory.packages.application.settings import (
    TYPED_DECISION_LOCK_MESSAGES,
    TypedDecisionLock,
    active_typed_decision_locks,
    with_typed_decision_mode,
)
from mnemo_memory.packages.domain import TypedDecisionKind, TypedDecisionMode
from mnemo_memory.packages.storage import SQLiteKnowledgeDocumentRepository


def test_settings_defaults_are_strict_bounded_and_secret_free() -> None:
    settings = PersonalSettings()

    assert settings.context_budget.total_limit == 5_700
    assert settings.context_budget.active_task_checkpoint == 600
    assert settings.episodic_retention_days == 180
    assert settings.optional_model_enabled is False
    assert settings.experimental_semantic_memory_enabled is False
    assert settings.model_provider is settings.model_id is None
    assert set(settings.to_dict()) == {
        "approved_event_capture_enabled",
        "context_active_task_checkpoint_tokens",
        "context_episodic_tokens",
        "context_knowledge_tokens",
        "context_provenance_tokens",
        "context_skills_tokens",
        "context_structural_tokens",
        "context_total_tokens",
        "context_save_growth_bytes",
        "episodic_retention_days",
        "experimental_local_first_takeover_enabled",
        "experimental_semantic_memory_enabled",
        "experimental_typed_decisions_enabled",
        "local_first_takeover_live_calls_authorized",
        "model_id",
        "model_provider",
        "optional_model_enabled",
        "repository_knowledge_sync_enabled",
        "typed_decision_data_route",
        "typed_decision_daily_input_tokens",
        "typed_decision_model_id",
        "typed_decision_modes",
    }
    assert not any(
        "key" in name or "secret" in name or "token_value" in name for name in settings.to_dict()
    )


def test_settings_store_atomically_round_trips_mode_0600(tmp_path: Path) -> None:
    store = PersonalSettingsStore(tmp_path / "profile")
    expected = PersonalSettings(
        repository_knowledge_sync_enabled=False,
        approved_event_capture_enabled=False,
        experimental_semantic_memory_enabled=True,
        optional_model_enabled=True,
        model_provider="local-provider",
        model_id="local/model-1",
        episodic_retention_days=90,
        context_total_tokens=4_000,
    )

    assert store.load() == PersonalSettings()
    assert store.save(expected) == expected
    assert store.load() == expected
    path = tmp_path / "profile" / "settings.json"
    assert path.stat().st_mode & 0o777 == 0o600
    assert json.loads(path.read_text("utf-8")) == expected.to_dict()


def test_settings_load_legacy_document_with_semantic_memory_disabled(tmp_path: Path) -> None:
    store = PersonalSettingsStore(tmp_path / "profile")
    store.save(PersonalSettings())
    path = tmp_path / "profile" / "settings.json"
    legacy = json.loads(path.read_text("utf-8"))
    del legacy["experimental_semantic_memory_enabled"]
    path.write_text(json.dumps(legacy), encoding="utf-8")

    assert store.load().experimental_semantic_memory_enabled is False


@pytest.mark.parametrize(
    "value",
    [
        {**PersonalSettings().to_dict(), "unknown": True},
        {**PersonalSettings().to_dict(), "episodic_retention_days": 0},
        {
            **PersonalSettings().to_dict(),
            "optional_model_enabled": True,
            "model_provider": None,
            "model_id": None,
        },
        {**PersonalSettings().to_dict(), "context_total_tokens": 8_001},
    ],
)
def test_settings_reject_unknown_or_unbounded_values(value: dict[str, object]) -> None:
    with pytest.raises(PersonalSettingsError):
        PersonalSettings.from_dict(value)


def test_settings_store_rejects_symlink_and_malformed_state(tmp_path: Path) -> None:
    directory = tmp_path / "profile"
    directory.mkdir()
    target = tmp_path / "outside.json"
    target.write_text(json.dumps(PersonalSettings().to_dict()))
    (directory / "settings.json").symlink_to(target)
    with pytest.raises(PersonalSettingsError, match="MNEMO_SETTINGS_INVALID"):
        PersonalSettingsStore(directory).load()
    (directory / "settings.json").unlink()
    (directory / "settings.json").write_text("not-json")
    with pytest.raises(PersonalSettingsError, match="MNEMO_SETTINGS_INVALID"):
        PersonalSettingsStore(directory).load()


def test_settings_api_requires_same_origin_explicit_intent_and_exact_fields(tmp_path: Path) -> None:
    config = LocalConfig.defaults(tmp_path / "profile")
    service = build_lifecycle_service(config)
    service.initialize()
    app = create_app(service, settings_store=PersonalSettingsStore(config.data_directory))
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    value = PersonalSettings(context_total_tokens=4_200).to_dict()

    assert client.get("/api/settings").json() == PersonalSettings().to_dict()
    assert client.put("/api/settings", json=value).status_code == 403
    assert (
        client.put(
            "/api/settings",
            json=value,
            headers={
                "Origin": "https://attacker.example",
                "X-Mnemo-Intent": "update-settings",
            },
        ).status_code
        == 403
    )
    saved = client.put(
        "/api/settings",
        json=value,
        headers={
            "Origin": "http://127.0.0.1:8765",
            "X-Mnemo-Intent": "update-settings",
        },
    )
    assert saved.status_code == 200
    assert saved.json()["context_total_tokens"] == 4_200
    invalid = client.put(
        "/api/settings",
        json={**value, "api_key": "prohibited"},
        headers={
            "Origin": "http://127.0.0.1:8765",
            "X-Mnemo-Intent": "update-settings",
        },
    )
    assert invalid.status_code == 422
    assert "prohibited" not in invalid.text


def test_settings_write_failure_preserves_previous_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = PersonalSettingsStore(tmp_path / "profile")
    original = PersonalSettings(context_total_tokens=4_100)
    store.save(original)
    monkeypatch.setattr(os, "replace", lambda *_: (_ for _ in ()).throw(OSError("synthetic")))

    with pytest.raises(PersonalSettingsError, match="MNEMO_SETTINGS_WRITE_FAILED"):
        store.save(PersonalSettings(context_total_tokens=4_200))

    assert store.load() == original


def test_automatic_repository_knowledge_sync_honors_personal_consent(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "decision.md").write_text("# Decision\nKeep this local.")
    data = tmp_path / "profile"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    store = PersonalSettingsStore(data)
    store.save(PersonalSettings(repository_knowledge_sync_enabled=False))

    cli_main._refresh_project_knowledge(data, binding)
    repository = SQLiteKnowledgeDocumentRepository(data / "mnemo.sqlite3", base_directory=data)
    repository.migrate()
    assert repository.list_active_documents(binding.scope) == ()

    store.save(PersonalSettings(repository_knowledge_sync_enabled=True))
    cli_main._refresh_project_knowledge(data, binding)
    assert len(repository.list_active_documents(binding.scope)) == 1


def test_takeover_flags_default_false() -> None:
    s = PersonalSettings()
    assert s.experimental_local_first_takeover_enabled is False
    assert s.local_first_takeover_live_calls_authorized is False


def test_from_dict_migrates_missing_takeover_flags() -> None:
    d = PersonalSettings().to_dict()
    d.pop("experimental_local_first_takeover_enabled")
    d.pop("local_first_takeover_live_calls_authorized")
    migrated = PersonalSettings.from_dict(d)
    assert migrated.experimental_local_first_takeover_enabled is False
    assert migrated.local_first_takeover_live_calls_authorized is False


def test_context_save_growth_bytes_default_and_migration() -> None:
    assert PersonalSettings().context_save_growth_bytes == 200_000
    legacy = PersonalSettings().to_dict()
    legacy.pop("context_save_growth_bytes")
    migrated = PersonalSettings.from_dict(legacy)
    assert migrated.context_save_growth_bytes == 200_000


def test_context_save_growth_bytes_rejects_negative() -> None:
    with pytest.raises(PersonalSettingsError):
        PersonalSettings(context_save_growth_bytes=-1)


def test_typed_decision_settings_default_off_and_synthetic_only() -> None:
    settings = PersonalSettings()
    assert settings.experimental_typed_decisions_enabled is False
    assert settings.typed_decision_data_route == "synthetic_only"
    assert settings.typed_decision_model_id == "jev-1.13.0"
    assert settings.typed_decision_mode(TypedDecisionKind.FRONT_DOOR) is TypedDecisionMode.OFF
    assert settings.to_dict()["typed_decision_modes"] == {}


@pytest.mark.parametrize(
    "overrides",
    [
        {"typed_decision_data_route": "vercel_zdr"},
        {"typed_decision_data_route": "standard_terms"},
        {"typed_decision_model_id": "bad model id"},
        {"typed_decision_modes": {"front_door": "shadow"}},  # switch still off
        {"experimental_typed_decisions_enabled": True, "typed_decision_modes": {"nope": "live"}},
        {
            "experimental_typed_decisions_enabled": True,
            "typed_decision_modes": {"front_door": "on"},
        },
        {"experimental_typed_decisions_enabled": "yes"},
        {"typed_decision_modes": ["front_door"]},
    ],
)
def test_typed_decision_settings_reject_unsupported_values(overrides: dict[str, object]) -> None:
    with pytest.raises(PersonalSettingsError):
        PersonalSettings.from_dict({**PersonalSettings().to_dict(), **overrides})


def test_typed_decision_modes_round_trip_through_the_store(tmp_path: Path) -> None:
    store = PersonalSettingsStore(tmp_path / "profile")
    expected = PersonalSettings.from_dict(
        {
            **PersonalSettings().to_dict(),
            "experimental_typed_decisions_enabled": True,
            "typed_decision_modes": {"relevance": "shadow", "front_door": "shadow"},
        }
    )
    store.save(expected)
    loaded = store.load()
    assert loaded == expected
    assert loaded.typed_decision_mode(TypedDecisionKind.RELEVANCE) is TypedDecisionMode.SHADOW
    assert loaded.to_dict()["typed_decision_modes"] == {
        "front_door": "shadow",
        "relevance": "shadow",
    }


def test_legacy_settings_without_typed_decision_fields_load(tmp_path: Path) -> None:
    store = PersonalSettingsStore(tmp_path / "profile")
    store.save(PersonalSettings())
    path = tmp_path / "profile" / "settings.json"
    legacy = json.loads(path.read_text("utf-8"))
    for name in (
        "experimental_typed_decisions_enabled",
        "typed_decision_data_route",
        "typed_decision_daily_input_tokens",
        "typed_decision_model_id",
        "typed_decision_modes",
    ):
        del legacy[name]
    path.write_text(json.dumps(legacy), encoding="utf-8")
    assert store.load() == PersonalSettings()


_HOOK_KINDS = ("front_door", "relevance", "tier_hint", "skill")


def _typed(**overrides: object) -> dict[str, object]:
    return {
        **PersonalSettings().to_dict(),
        "experimental_typed_decisions_enabled": True,
        **overrides,
    }


def test_typed_decision_daily_input_tokens_default_bounds_and_migration() -> None:
    assert PersonalSettings().typed_decision_daily_input_tokens == 10_000_000
    legacy = PersonalSettings().to_dict()
    legacy.pop("typed_decision_daily_input_tokens")
    assert PersonalSettings.from_dict(legacy).typed_decision_daily_input_tokens == 10_000_000
    assert (
        PersonalSettings(typed_decision_daily_input_tokens=1).typed_decision_daily_input_tokens == 1
    )
    for value in (0, 1_000_000_001, True, "10"):
        with pytest.raises(PersonalSettingsError, match="daily input tokens"):
            PersonalSettings.from_dict(
                {**PersonalSettings().to_dict(), "typed_decision_daily_input_tokens": value}
            )


@pytest.mark.parametrize("kind", _HOOK_KINDS)
def test_live_is_refused_while_the_route_is_synthetic_only(kind: str) -> None:
    with pytest.raises(PersonalSettingsError) as raised:
        PersonalSettings.from_dict(
            _typed(experimental_semantic_memory_enabled=True, typed_decision_modes={kind: "live"})
        )
    assert str(raised.value) == TYPED_DECISION_LOCK_MESSAGES[TypedDecisionLock.DATA_ROUTE]


def test_front_door_live_is_refused_without_the_semantic_memory_gate() -> None:
    with pytest.raises(PersonalSettingsError) as raised:
        PersonalSettings.from_dict(_typed(typed_decision_modes={"front_door": "live"}))
    assert str(raised.value) == TYPED_DECISION_LOCK_MESSAGES[TypedDecisionLock.SEMANTIC_MEMORY_GATE]


def test_any_mode_other_than_off_needs_the_master_switch() -> None:
    with pytest.raises(PersonalSettingsError) as raised:
        PersonalSettings.from_dict(
            {**PersonalSettings().to_dict(), "typed_decision_modes": {"skill": "shadow"}}
        )
    assert str(raised.value) == TYPED_DECISION_LOCK_MESSAGES[TypedDecisionLock.MASTER_SWITCH]


def test_shadow_is_allowed_on_synthetic_only_for_every_hook_kind() -> None:
    settings = PersonalSettings.from_dict(
        _typed(typed_decision_modes={kind: "shadow" for kind in _HOOK_KINDS})
    )
    for kind in _HOOK_KINDS:
        assert settings.typed_decision_mode(TypedDecisionKind(kind)) is TypedDecisionMode.SHADOW


def test_with_typed_decision_mode_applies_the_locks() -> None:
    base = PersonalSettings(experimental_typed_decisions_enabled=True)
    shadow = with_typed_decision_mode(base, TypedDecisionKind.SKILL, TypedDecisionMode.SHADOW)
    assert shadow.typed_decision_mode(TypedDecisionKind.SKILL) is TypedDecisionMode.SHADOW
    with pytest.raises(PersonalSettingsError, match="synthetic_only"):
        with_typed_decision_mode(shadow, TypedDecisionKind.RELEVANCE, TypedDecisionMode.LIVE)
    off = with_typed_decision_mode(shadow, TypedDecisionKind.SKILL, TypedDecisionMode.OFF)
    assert off.typed_decision_mode(TypedDecisionKind.SKILL) is TypedDecisionMode.OFF


def test_active_typed_decision_locks_name_each_closed_lock() -> None:
    assert active_typed_decision_locks(PersonalSettings()) == (
        TypedDecisionLock.MASTER_SWITCH,
        TypedDecisionLock.SEMANTIC_MEMORY_GATE,
        TypedDecisionLock.DATA_ROUTE,
    )
    opened = PersonalSettings(
        experimental_typed_decisions_enabled=True, experimental_semantic_memory_enabled=True
    )
    assert active_typed_decision_locks(opened) == (TypedDecisionLock.DATA_ROUTE,)
    assert all(message.strip() for message in TYPED_DECISION_LOCK_MESSAGES.values())


def test_a_stored_live_mode_is_refused_on_load(tmp_path: Path) -> None:
    store = PersonalSettingsStore(tmp_path / "profile")
    store.save(PersonalSettings(experimental_typed_decisions_enabled=True))
    path = tmp_path / "profile" / "settings.json"
    stored = json.loads(path.read_text("utf-8"))
    stored["typed_decision_modes"] = {"skill": "live"}
    path.write_text(json.dumps(stored), encoding="utf-8")
    with pytest.raises(PersonalSettingsError, match="MNEMO_SETTINGS_INVALID") as raised:
        store.load()
    assert str(raised.value.__cause__) == TYPED_DECISION_LOCK_MESSAGES[TypedDecisionLock.DATA_ROUTE]
