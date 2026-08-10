"""Explicit learned route phrases remain bounded, secret-safe, and project-scoped."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mnemo_memory.apps.cli.main import app
from mnemo_memory.connectors.automatic_memory.learned_routes import (
    LearnedRouteStoreError,
    LocalLearnedRouteStore,
)
from mnemo_memory.packages.application.automatic_memory import LocalMemoryProjectBindingStore
from mnemo_memory.packages.application.context_routing import CompactMemoryRoute


def test_learned_phrases_are_private_scoped_idempotent_and_exactly_forgotten(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    first_project = tmp_path / "first"
    second_project = tmp_path / "second"
    first_project.mkdir()
    second_project.mkdir()
    bindings = LocalMemoryProjectBindingStore(data)
    first = bindings.enable(first_project)
    second = bindings.enable(second_project)
    store = LocalLearnedRouteStore(data)

    created = store.learn(first.scope, "  Check   blast-radius! ", CompactMemoryRoute.STRUCTURE)
    repeated = store.learn(first.scope, "check blast radius", CompactMemoryRoute.STRUCTURE)

    assert created.changed is True
    assert repeated.changed is False
    assert [record.normalized_phrase for record in store.records(first.scope)] == [
        "check blast radius"
    ]
    assert store.records(second.scope) == ()
    assert store.path.stat().st_mode & 0o777 == 0o600
    encoded = store.path.read_text(encoding="utf-8")
    assert str(first_project) not in encoded
    assert str(second_project) not in encoded

    assert store.forget(second.scope, "check blast radius").changed is False
    assert store.records(first.scope)
    assert store.forget(first.scope, "CHECK blast-radius").changed is True
    assert store.forget(first.scope, "check blast radius").changed is False
    assert store.records(first.scope) == ()


def test_learned_phrase_rejects_secrets_and_corrupt_state_without_echoing_payload(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    project = tmp_path / "project"
    project.mkdir()
    scope = LocalMemoryProjectBindingStore(data).enable(project).scope
    store = LocalLearnedRouteStore(data)

    with pytest.raises(LearnedRouteStoreError, match="SECRET_REJECTED"):
        store.learn(scope, "api_key=abcdefghijklmnop1234", CompactMemoryRoute.KNOWLEDGE)

    private_marker = "private-corrupt-learned-route"
    store.path.write_text(private_marker, encoding="utf-8")
    with pytest.raises(LearnedRouteStoreError) as raised:
        store.records(scope)
    assert private_marker not in str(raised.value)


def test_learn_and_forget_cli_are_documented_and_report_shadow_only_behavior(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    project = tmp_path / "project"
    project.mkdir()
    LocalMemoryProjectBindingStore(data).enable(project)
    runner = CliRunner()

    root_help = runner.invoke(app, ["--help"])
    learned = runner.invoke(
        app,
        [
            "learn",
            "--phrase",
            "check the blast radius",
            "--as",
            "structure",
            "--project-dir",
            str(project),
            "--data-dir",
            str(data),
        ],
    )
    forgotten = runner.invoke(
        app,
        [
            "forget",
            "--phrase",
            "check the blast radius",
            "--project-dir",
            str(project),
            "--data-dir",
            str(data),
        ],
    )

    assert root_help.exit_code == learned.exit_code == forgotten.exit_code == 0
    assert "learn" in root_help.output and "forget" in root_help.output
    learned_payload = json.loads(learned.output)
    forgotten_payload = json.loads(forgotten.output)
    assert learned_payload["active_mode"] == "shadow"
    assert forgotten_payload == {"active_mode": "shadow", "status": "forgotten"}
    assert "check the blast radius" not in learned.output
