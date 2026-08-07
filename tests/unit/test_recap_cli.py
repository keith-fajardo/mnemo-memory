from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from mnemo_memory.apps.cli.main import app
from mnemo_memory.packages.application import (
    CompleteCheckpoint,
    CreateCheckpoint,
    LocalConfig,
    build_checkpoint_runtime,
)
from mnemo_memory.packages.application.automatic_memory import LocalMemoryProjectBindingStore
from mnemo_memory.packages.domain import (
    CheckpointContent,
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    SourceId,
    SourceTrustClass,
    VerificationStatus,
)


def _evidence() -> EvidenceReference:
    return EvidenceReference(
        EvidenceId.new(),
        SourceId.new(),
        EvidenceSourceType.CHECKPOINT,
        SourceTrustClass.USER_AUTHORED,
        "synthetic://recap-cli",
        "sha256:" + "a" * 64,
        EvidenceLocation("fixture://recap-cli"),
        datetime.now(UTC),
        VerificationStatus.VERIFIED,
    )


def _content(*, complete: bool = False) -> CheckpointContent:
    return CheckpointContent(
        task_objective="Improve the dbt inventory workflow",
        completed_work=("Returned one exact aggregate from the saved manifest.",),
        current_state="The bounded retrieval is complete.",
        remaining_work=() if complete else ("Start the recap feature.",),
        decisions=("Do not replay model-node lists for a count question.",),
        failures=(),
        blockers=(),
        relevant_files=("src/mnemo_memory/packages/application/unified_context.py",),
        relevant_artifacts=(),
        verification_performed=("The full gate passed.",),
        token_estimate=140,
    )


def test_recap_cli_supports_previous_session_and_three_day_shorthand(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    data_dir = tmp_path / "data"
    config = LocalConfig.defaults(data_dir)
    binding = LocalMemoryProjectBindingStore(config.data_directory).enable(project)
    with build_checkpoint_runtime(config) as runtime:
        created = runtime.checkpoint_service.create(
            CreateCheckpoint(binding.checkpoint_scope, _content(), (_evidence(),))
        )
        runtime.checkpoint_service.complete(
            CompleteCheckpoint(
                binding.checkpoint_scope,
                created.aggregate.checkpoint_id,
                created.revision.revision_id,
                _content(complete=True),
                (_evidence(),),
            )
        )

    common = ["--project-dir", str(project), "--data-dir", str(data_dir)]
    previous = CliRunner().invoke(app, ["recap", *common])
    three_days = CliRunner().invoke(app, ["recap", "--3days", *common])

    assert previous.exit_code == three_days.exit_code == 0
    assert "Mnemo recap — previous saved session" in previous.output
    assert "Mnemo recap — past 3 days" in three_days.output
    assert "Improve the dbt inventory workflow" in previous.output
    assert "Returned one exact aggregate" in three_days.output
    assert "Source: checkpoint" in previous.output


def test_recap_cli_requires_an_enabled_project(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    result = CliRunner().invoke(
        app,
        ["recap", "--project-dir", str(project), "--data-dir", str(tmp_path / "data")],
    )
    assert result.exit_code == 2
    assert "MNEMO_RECAP_" in result.output
