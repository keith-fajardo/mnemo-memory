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
from mnemo_memory.packages.storage import SQLiteKnowledgeDocumentRepository


def test_settings_defaults_are_strict_bounded_and_secret_free() -> None:
    settings = PersonalSettings()

    assert settings.context_budget.total_limit == 5_700
    assert settings.context_budget.active_task_checkpoint == 600
    assert settings.episodic_retention_days == 180
    assert settings.optional_model_enabled is False
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
        "episodic_retention_days",
        "model_id",
        "model_provider",
        "optional_model_enabled",
        "repository_knowledge_sync_enabled",
    }
    assert not any(
        "key" in name or "secret" in name or "token_value" in name for name in settings.to_dict()
    )


def test_settings_store_atomically_round_trips_mode_0600(tmp_path: Path) -> None:
    store = PersonalSettingsStore(tmp_path / "profile")
    expected = PersonalSettings(
        repository_knowledge_sync_enabled=False,
        approved_event_capture_enabled=False,
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
