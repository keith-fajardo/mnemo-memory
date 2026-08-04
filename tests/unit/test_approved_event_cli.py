from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from mnemo_memory.apps.cli.main import app
from mnemo_memory.packages.application import (
    GetCheckpointContext,
    LocalConfig,
    RecordApprovedEpisodicEvent,
    build_checkpoint_runtime,
)
from mnemo_memory.packages.application.automatic_memory import LocalMemoryProjectBindingStore
from mnemo_memory.packages.domain import (
    ApprovedEventKind,
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    SourceId,
    SourceTrustClass,
    VerificationStatus,
)

NOW = datetime(2026, 8, 5, 5, 0, tzinfo=UTC)


def _evidence() -> EvidenceReference:
    return EvidenceReference(
        EvidenceId.new(),
        SourceId.new(),
        EvidenceSourceType.TOOL_RESULT,
        SourceTrustClass.VERIFIED_TOOL_RESULT,
        "synthetic://approved-event-cli",
        "sha256:" + "a" * 64,
        EvidenceLocation("fixture://approved-event-cli"),
        NOW,
        VerificationStatus.VERIFIED,
    )


def _command(data_dir: Path, project: Path, *arguments: str) -> list[str]:
    return [
        "memory",
        *arguments,
        "--project-dir",
        str(project),
        "--data-dir",
        str(data_dir),
    ]


def test_cli_reviews_corrects_and_payload_erases_one_approved_fact(tmp_path: Path) -> None:
    project = tmp_path / "enabled project"
    project.mkdir()
    data_dir = tmp_path / "memory data"
    config = LocalConfig.defaults(data_dir)
    binding = LocalMemoryProjectBindingStore(config.data_directory).enable(project)
    original_summary = "Use the account grain for the reconciliation."
    corrected_summary = "Use the verified transaction grain for the reconciliation."
    with build_checkpoint_runtime(config) as runtime:
        original = runtime.checkpoint_service.record_approved_event(
            RecordApprovedEpisodicEvent(
                binding.checkpoint_scope,
                ApprovedEventKind.DECISION,
                original_summary,
                "cli-review:original",
                (_evidence(),),
            )
        ).event

    runner = CliRunner()
    listed = runner.invoke(app, _command(data_dir, project, "events"))
    inspected = runner.invoke(
        app, _command(data_dir, project, "event", "inspect", str(original.event_id))
    )
    assert listed.exit_code == inspected.exit_code == 0
    assert json.loads(listed.output)["events"][0]["status"] == "active"
    inspected_value = json.loads(inspected.output)
    assert inspected_value["event"]["summary"] == original_summary
    assert len(inspected_value["event"]["evidence_references"]) == 1

    correction = _command(
        data_dir,
        project,
        "event",
        "correct",
        str(original.event_id),
        "--summary",
        corrected_summary,
        "--reason",
        "The verified fixture disproved the account-grain statement.",
        "--yes",
    )
    corrected = runner.invoke(app, correction)
    retried_correction = runner.invoke(app, correction)
    assert corrected.exit_code == retried_correction.exit_code == 0
    corrected_value = json.loads(corrected.output)
    assert corrected_value["idempotent"] is False
    assert json.loads(retried_correction.output)["idempotent"] is True
    replacement_id = corrected_value["replacement"]["event_id"]
    assert corrected_value["corrected"]["status"] == "corrected"
    assert corrected_value["replacement"]["status"] == "active"
    with build_checkpoint_runtime(config) as runtime:
        packet = runtime.checkpoint_service.get_context(
            GetCheckpointContext(binding.checkpoint_scope, include_approved_events=True)
        )
    assert len(packet.episodic_memories) == 1
    assert corrected_summary in packet.episodic_memories[0].content
    assert original_summary not in packet.episodic_memories[0].content

    retraction = _command(
        data_dir,
        project,
        "event",
        "retract",
        replacement_id,
        "--reason",
        "The user withdrew this fact from durable episodic memory.",
        "--yes",
    )
    retracted = runner.invoke(app, retraction)
    retried_retraction = runner.invoke(app, retraction)
    assert retracted.exit_code == retried_retraction.exit_code == 0
    assert json.loads(retracted.output)["retracted"]["event"] is None
    assert json.loads(retried_retraction.output)["idempotent"] is True
    tombstone = runner.invoke(app, _command(data_dir, project, "event", "inspect", replacement_id))
    assert tombstone.exit_code == 0
    tombstone_value = json.loads(tombstone.output)
    assert tombstone_value["status"] == "retracted"
    assert tombstone_value["event"] is None
    assert tombstone_value["governance"]["kind"] == "retracted"
    with build_checkpoint_runtime(config) as runtime:
        assert (
            runtime.checkpoint_service.get_context(
                GetCheckpointContext(binding.checkpoint_scope, include_approved_events=True)
            ).episodic_memories
            == ()
        )
    with sqlite3.connect(config.database_path) as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM approved_episodic_events WHERE event_id = ?", (replacement_id,)
            ).fetchone()
            is None
        )
        assert (
            connection.execute(
                "SELECT 1 FROM approved_episodic_event_evidence WHERE event_id = ?",
                (replacement_id,),
            ).fetchone()
            is None
        )
        assert corrected_summary not in "".join(
            row[0]
            for row in connection.execute("SELECT summary FROM approved_episodic_events").fetchall()
        )


def test_cli_governance_fails_closed_for_another_or_unregistered_project(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    other = tmp_path / "other project"
    unregistered = tmp_path / "unregistered"
    project.mkdir()
    other.mkdir()
    unregistered.mkdir()
    data_dir = tmp_path / "memory data"
    config = LocalConfig.defaults(data_dir)
    bindings = LocalMemoryProjectBindingStore(config.data_directory)
    binding = bindings.enable(project)
    bindings.enable(other)
    with build_checkpoint_runtime(config) as runtime:
        event = runtime.checkpoint_service.record_approved_event(
            RecordApprovedEpisodicEvent(
                binding.checkpoint_scope,
                ApprovedEventKind.FAILURE,
                "This project-scoped fact must not appear elsewhere.",
                "cli-review:private",
                (_evidence(),),
            )
        ).event

    runner = CliRunner()
    other_list = runner.invoke(app, _command(data_dir, other, "events"))
    other_inspect = runner.invoke(
        app, _command(data_dir, other, "event", "inspect", str(event.event_id))
    )
    other_retract = runner.invoke(
        app,
        _command(
            data_dir,
            other,
            "event",
            "retract",
            str(event.event_id),
            "--reason",
            "A different project must not retract this fact.",
            "--yes",
        ),
    )
    unregistered_list = runner.invoke(app, _command(data_dir, unregistered, "events"))

    assert other_list.exit_code == 0
    assert json.loads(other_list.output)["events"] == []
    assert str(event.event_id) not in other_list.output
    assert other_inspect.exit_code != 0
    assert "MNEMO_APPROVED_EVENT_NOT_FOUND" in other_inspect.output
    assert other_retract.exit_code != 0
    assert "MNEMO_APPROVED_EVENT_NOT_FOUND" in other_retract.output
    assert unregistered_list.exit_code != 0
    assert "MNEMO_MEMORY_PROJECT_NOT_ENABLED" in unregistered_list.output
    with build_checkpoint_runtime(config) as runtime:
        assert runtime.checkpoint_service.get_context(
            GetCheckpointContext(binding.checkpoint_scope, include_approved_events=True)
        ).episodic_memories


def test_cli_governance_decline_does_not_mutate(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    data_dir = tmp_path / "memory data"
    config = LocalConfig.defaults(data_dir)
    binding = LocalMemoryProjectBindingStore(config.data_directory).enable(project)
    with build_checkpoint_runtime(config) as runtime:
        event = runtime.checkpoint_service.record_approved_event(
            RecordApprovedEpisodicEvent(
                binding.checkpoint_scope,
                ApprovedEventKind.TOOL_OUTCOME,
                "The bounded validation passed.",
                "cli-review:decline",
                (_evidence(),),
            )
        ).event

    declined = CliRunner().invoke(
        app,
        _command(
            data_dir,
            project,
            "event",
            "retract",
            str(event.event_id),
            "--reason",
            "This should not be applied without confirmation.",
        ),
        input="n\n",
    )

    assert declined.exit_code != 0
    with build_checkpoint_runtime(config) as runtime:
        assert runtime.checkpoint_service.get_context(
            GetCheckpointContext(binding.checkpoint_scope, include_approved_events=True)
        ).episodic_memories
