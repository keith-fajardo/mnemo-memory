"""The typed-decisions CLI shows switches, locks and budget, and refuses locked modes."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner, Result

from mnemo_memory.apps.cli.main import app
from mnemo_memory.packages.application import PersonalSettings, PersonalSettingsStore
from mnemo_memory.packages.application.settings import (
    TYPED_DECISION_LOCK_MESSAGES,
    TypedDecisionLock,
)

FAKE_KEY = "test-key-not-real-0000"
runner = CliRunner()


def _invoke(data: Path, *args: str, key: str | None = None) -> Result:
    return runner.invoke(
        app,
        ["typed-decisions", *args, "--data-dir", str(data)],
        env={"TYPESAFE_API_KEY": key},
    )


def test_status_reports_switches_locks_budget_and_key_presence_only(tmp_path: Path) -> None:
    result = _invoke(tmp_path, "status")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "status": "available",
        "master_switch": False,
        "data_route": "synthetic_only",
        "model_id": "jev-1.13.0",
        "modes": {"front_door": "off", "relevance": "off", "tier_hint": "off", "skill": "off"},
        "locks": ["master_switch", "semantic_memory_gate", "data_route"],
        "credential_present": False,
        "daily_input_tokens": {
            "counter": "available",
            "limit": 10_000_000,
            "reserved_today": 0,
        },
        "sends_real_prompts": False,
    }
    with_key = _invoke(tmp_path, "status", key=FAKE_KEY)
    assert json.loads(with_key.output)["credential_present"] is True
    assert FAKE_KEY not in with_key.output


def test_set_changes_one_mode_and_applies_the_locks(tmp_path: Path) -> None:
    refused = _invoke(tmp_path, "set", "skill", "shadow")
    assert refused.exit_code == 1
    assert (
        json.loads(refused.output)["reason"]
        == TYPED_DECISION_LOCK_MESSAGES[TypedDecisionLock.MASTER_SWITCH]
    )
    PersonalSettingsStore(tmp_path).save(
        PersonalSettings(experimental_typed_decisions_enabled=True)
    )

    updated = _invoke(tmp_path, "set", "skill", "shadow")
    assert updated.exit_code == 0, updated.output
    assert json.loads(updated.output) == {"status": "updated", "kind": "skill", "mode": "shadow"}
    assert json.loads(_invoke(tmp_path, "status").output)["modes"]["skill"] == "shadow"

    live = _invoke(tmp_path, "set", "relevance", "live")
    assert live.exit_code == 1
    assert (
        json.loads(live.output)["reason"]
        == TYPED_DECISION_LOCK_MESSAGES[TypedDecisionLock.DATA_ROUTE]
    )
    front = _invoke(tmp_path, "set", "front_door", "live")
    assert (
        json.loads(front.output)["reason"]
        == TYPED_DECISION_LOCK_MESSAGES[TypedDecisionLock.SEMANTIC_MEMORY_GATE]
    )
    assert PersonalSettingsStore(tmp_path).load().typed_decision_modes == (("skill", "shadow"),)

    assert _invoke(tmp_path, "set", "verify", "shadow").exit_code == 2
    assert _invoke(tmp_path, "set", "skill", "on").exit_code == 2


def test_status_reports_a_locked_settings_file_in_plain_words(tmp_path: Path) -> None:
    PersonalSettingsStore(tmp_path).save(
        PersonalSettings(experimental_typed_decisions_enabled=True)
    )
    path = tmp_path / "settings.json"
    stored = json.loads(path.read_text("utf-8"))
    stored["typed_decision_modes"] = {"skill": "live"}
    path.write_text(json.dumps(stored), encoding="utf-8")
    result = _invoke(tmp_path, "status")
    assert result.exit_code == 1
    assert json.loads(result.output) == {
        "status": "settings_invalid",
        "reason": TYPED_DECISION_LOCK_MESSAGES[TypedDecisionLock.DATA_ROUTE],
    }


def test_status_reports_an_unreadable_counter(tmp_path: Path) -> None:
    (tmp_path / "typed_decision-budget.json").write_text("{not json", encoding="utf-8")
    budget = json.loads(_invoke(tmp_path, "status").output)["daily_input_tokens"]
    assert budget == {"counter": "unavailable", "limit": 10_000_000, "reserved_today": None}


def test_help_lists_both_commands() -> None:
    result = runner.invoke(app, ["typed-decisions", "--help"])
    assert result.exit_code == 0
    assert "status" in result.output and "set" in result.output


def test_set_reports_a_locked_settings_file_like_status(tmp_path: Path) -> None:
    PersonalSettingsStore(tmp_path).save(
        PersonalSettings(experimental_typed_decisions_enabled=True)
    )
    path = tmp_path / "settings.json"
    stored = json.loads(path.read_text("utf-8"))
    stored["typed_decision_modes"] = {"skill": "live"}
    path.write_text(json.dumps(stored), encoding="utf-8")
    result = _invoke(tmp_path, "set", "skill", "off")
    assert result.exit_code == 1
    assert json.loads(result.output) == {
        "status": "settings_invalid",
        "reason": TYPED_DECISION_LOCK_MESSAGES[TypedDecisionLock.DATA_ROUTE],
    }
