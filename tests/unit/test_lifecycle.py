import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from typer.testing import CliRunner

import mnemo_memory.apps.cli.main as cli_main
from mnemo_memory.apps.api.app import create_app
from mnemo_memory.apps.cli.main import app
from mnemo_memory.connectors.dbt.project_binding import (
    DbtProjectBinding,
    LocalDbtProjectBindingStore,
)
from mnemo_memory.packages.application import (
    CreateCheckpoint,
    LocalConfig,
    build_checkpoint_runtime,
    build_lifecycle_service,
)
from mnemo_memory.packages.application.automatic_memory import LocalMemoryProjectBindingStore
from mnemo_memory.packages.application.command_wrapper import (
    CommandContext,
    CommandResult,
    HookDiscoveryResult,
    HookOutcome,
    HookRegistration,
    HookStatus,
)
from mnemo_memory.packages.application.services import LifecycleService
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
from mnemo_memory.packages.project_index import SourceStructureParser, SourceStructureParseRequest
from mnemo_memory.packages.storage import SQLiteSourceStructureRepository


def service(tmp_path: Path) -> LifecycleService:
    config = LocalConfig.defaults(tmp_path / "mnemo-memory")
    return build_lifecycle_service(config)


def test_init_is_idempotent_and_creates_restrictive_local_state(tmp_path: Path) -> None:
    value = service(tmp_path)
    first = value.initialize()
    second = value.initialize()

    assert first["initialized"] is True
    assert second["initialized"] is False
    assert first["schema_version"] == 26
    assert value.config.config_path.exists()
    assert value.config.database_path.exists()


def test_cli_help_explains_the_user_facing_workflow() -> None:
    runner = CliRunner()
    root = runner.invoke(app, ["--help"])
    memory = runner.invoke(app, ["memory", "--help"])
    dbt = runner.invoke(app, ["dbt", "--help"])

    assert root.exit_code == memory.exit_code == dbt.exit_code == 0
    assert "Local-first durable task checkpoints and dbt lineage context." in root.output
    assert "Run a deterministic interactive Mnemo setup guide." in root.output
    assert "Register Mnemo with an AI coding client." in root.output
    assert "Enable automatic task handoffs for this project and client." in memory.output
    assert "Print this enabled project's bounded active handoff" in memory.output
    assert "Show bounded saved structural changes" in memory.output
    assert "Enable Mnemo for this dbt project; no UUIDs are needed normally." in dbt.output
    assert "Run exact dbt arguments with safe Mnemo pre/post manifest hooks." in dbt.output


def test_configuration_is_strict_loopback_only_and_path_safe(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="loopback"):
        LocalConfig(tmp_path, tmp_path / "db.sqlite", host="0.0.0.0")
    with pytest.raises(ValueError, match="escapes"):
        LocalConfig(tmp_path, Path("../db.sqlite"))
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"unknown": True}))
    with pytest.raises(ValueError, match="unknown"):
        LocalConfig.load(config_path)


def test_lifecycle_api_exposes_only_local_readiness_endpoints(tmp_path: Path) -> None:
    value = service(tmp_path)
    value.initialize()
    app = create_app(value)
    routes = {route.path: route.endpoint for route in app.routes if isinstance(route, APIRoute)}

    assert routes["/live"]() == {"status": "live"}
    assert routes["/ready"]()["status"] == "ready"
    assert routes["/version"]()["profile"] == "personal"
    assert app.docs_url is None
    assert app.redoc_url is None


def test_cli_init_and_status_use_isolated_data_directory(tmp_path: Path) -> None:
    data_dir = tmp_path / "cli-state"
    init = subprocess.run(
        [sys.executable, "-m", "mnemo_memory.cli", "init", "--data-dir", str(data_dir)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert init.returncode == 0, init.stderr
    assert json.loads(init.stdout)["initialized"] is True
    status = subprocess.run(
        [sys.executable, "-m", "mnemo_memory.cli", "status", "--data-dir", str(data_dir)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["initialized"] is True


def test_cli_help_explains_top_level_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Initialize Mnemo's local data directory and SQLite database." in result.output
    assert "Register Mnemo with an AI coding client." in result.output
    assert "Enable personal dbt lineage memory and wrap dbt." in result.output


def test_dbt_help_leads_with_personal_enablement_not_advanced_scope_configuration() -> None:
    result = CliRunner().invoke(app, ["dbt", "--help"])

    assert result.exit_code == 0
    assert "enable" in result.output
    assert "disable" in result.output
    assert "status" in result.output
    assert "configure" not in result.output
    assert "unconfigure" not in result.output
    assert "ingest" not in result.output


def test_dbt_wrapper_docs_forward_only_the_arguments_after_dbt() -> None:
    """The explicit wrapper already chooses dbt; users must not pass it twice."""
    guide = Path("docs/command-wrapper.md").read_text(encoding="utf-8")

    assert "mnemo-memory dbt exec -- dbt " not in guide
    assert "mnemo-memory dbt exec -- run --select orders+" in guide


def test_interactive_guide_explains_explicit_memory_and_requires_confirmation(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "guide store"
    result = CliRunner().invoke(app, ["guide", "--data-dir", str(data_dir)], input="n\nboth\n")

    assert result.exit_code == 0
    assert "explicit task checkpoints, not an automatic chat or directory history" in result.output
    assert "Store status: not initialized" in result.output
    assert "mnemo-memory connect codex" in result.output
    assert "mnemo-memory connect claude-code" in result.output
    assert not data_dir.exists()


def test_non_interactive_guide_never_initializes_or_registers_clients(tmp_path: Path) -> None:
    data_dir = tmp_path / "guide store"
    result = CliRunner().invoke(app, ["guide", "--data-dir", str(data_dir), "--non-interactive"])

    assert result.exit_code == 0
    assert "mnemo-memory connect codex" in result.output
    assert "mnemo-memory connect claude-code" in result.output
    assert not data_dir.exists()


def test_agent_is_an_interactive_guide_alias(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app, ["agent", "--data-dir", str(tmp_path / "agent store"), "--non-interactive"]
    )

    assert result.exit_code == 0
    assert "Mnemo Memory setup guide" in result.output


def test_memory_inspect_returns_only_the_enabled_projects_active_checkpoint(
    tmp_path: Path,
) -> None:
    project = tmp_path / "enabled project"
    other_project = tmp_path / "other enabled project"
    project.mkdir()
    other_project.mkdir()
    data_dir = tmp_path / "memory data"
    config = LocalConfig.defaults(data_dir)
    bindings = LocalMemoryProjectBindingStore(config.data_directory)
    binding = bindings.enable(project)
    other_binding = bindings.enable(other_project)
    observed_at = datetime(2026, 8, 5, 4, 30, tzinfo=UTC)
    evidence = EvidenceReference(
        EvidenceId.new(),
        SourceId.new(),
        EvidenceSourceType.CHECKPOINT,
        SourceTrustClass.USER_AUTHORED,
        "synthetic://cli-inspection",
        "sha256:" + "a" * 64,
        EvidenceLocation("fixture://cli-inspection"),
        observed_at,
        VerificationStatus.VERIFIED,
    )
    content = CheckpointContent(
        "Inspect the exact active handoff",
        ("implemented bounded inspection",),
        "ready for verification",
        ("run the focused test",),
        ("use only the enabled project scope",),
        (),
        (),
        ("src/mnemo_memory/apps/cli/main.py",),
        (),
        ("focused CLI test",),
        48,
    )
    with build_checkpoint_runtime(config) as runtime:
        created = runtime.checkpoint_service.create(
            CreateCheckpoint(binding.checkpoint_scope, content, (evidence,))
        )
        other = runtime.checkpoint_service.create(
            CreateCheckpoint(other_binding.checkpoint_scope, content, (evidence,))
        )

    result = CliRunner().invoke(
        app,
        [
            "memory",
            "inspect",
            "--project-dir",
            str(project),
            "--data-dir",
            str(data_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    packet = json.loads(result.output)
    checkpoint = packet["active_task_checkpoint"]
    assert packet["owner_scope"] == binding.checkpoint_scope.to_dict()
    assert checkpoint["item_id"] == (
        f"checkpoint:{created.aggregate.checkpoint_id}:revision:{created.revision.revision_id}"
    )
    assert json.loads(checkpoint["content"])["task_objective"] == (
        "Inspect the exact active handoff"
    )
    assert packet["provenance"] == [
        {
            "evidence_references": [evidence.to_dict()],
            "item_id": checkpoint["item_id"],
            "provenance_id": f"provenance:{checkpoint['item_id']}",
            "source_digest": packet["provenance"][0]["source_digest"],
            "source_reference": (
                f"mnemo:checkpoint/{created.aggregate.checkpoint_id}/"
                f"revision/{created.revision.revision_id}"
            ),
            "token_estimate": 0,
        }
    ]
    assert str(other.aggregate.checkpoint_id) not in result.output
    assert str(other.revision.revision_id) not in result.output


def test_memory_inspect_reports_no_active_checkpoint_and_rejects_unregistered_project(
    tmp_path: Path,
) -> None:
    enabled = tmp_path / "enabled"
    unregistered = tmp_path / "unregistered"
    enabled.mkdir()
    unregistered.mkdir()
    data_dir = tmp_path / "memory data"
    config = LocalConfig.defaults(data_dir)
    LocalMemoryProjectBindingStore(config.data_directory).enable(enabled)

    empty = CliRunner().invoke(
        app,
        [
            "memory",
            "inspect",
            "--project-dir",
            str(enabled),
            "--data-dir",
            str(data_dir),
        ],
    )
    rejected = CliRunner().invoke(
        app,
        [
            "memory",
            "inspect",
            "--project-dir",
            str(unregistered),
            "--data-dir",
            str(data_dir),
        ],
    )

    assert empty.exit_code == 0, empty.output
    packet = json.loads(empty.output)
    assert packet["active_task_checkpoint"] is None
    assert packet["provenance"] == []
    assert rejected.exit_code != 0
    assert "MNEMO_MEMORY_PROJECT_NOT_ENABLED" in rejected.output


def test_memory_impact_queries_the_enabled_projects_static_snapshot(tmp_path: Path) -> None:
    project = tmp_path / "project with spaces Δ"
    project.mkdir()
    (project / ".git").mkdir()
    (project / "core.py").write_text("def calculate():\n    return 1\n")
    (project / "service.py").write_text(
        "import core\n\ndef serve():\n    return core.calculate()\n"
    )
    data_dir = tmp_path / "memory data"
    config = LocalConfig.defaults(data_dir)
    binding = LocalMemoryProjectBindingStore(config.data_directory).enable(project)
    repository = SQLiteSourceStructureRepository(config.database_path, base_directory=data_dir)
    repository.migrate()
    repository.store_and_activate(
        SourceStructureParser().parse(SourceStructureParseRequest(binding.scope, project))
    )

    result = CliRunner().invoke(
        app,
        [
            "memory",
            "impact",
            "core",
            "--project-dir",
            str(project),
            "--data-dir",
            str(data_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    value = json.loads(result.output)
    assert value["direction"] == "dependents"
    assert value["start_symbols"] == ["core"]
    assert value["symbols"] == [
        {
            "depth": 1,
            "kind": "module",
            "line": 1,
            "path": "service.py",
            "symbol": "service",
        }
    ]


def test_memory_changes_compares_scoped_immutable_source_snapshots(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    (project / "core.py").write_text("def calculate():\n    return 1\n")
    data_dir = tmp_path / "memory"
    config = LocalConfig.defaults(data_dir)
    binding = LocalMemoryProjectBindingStore(config.data_directory).enable(project)
    repository = SQLiteSourceStructureRepository(config.database_path, base_directory=data_dir)
    repository.migrate()
    before = repository.store_and_activate(
        SourceStructureParser().parse(SourceStructureParseRequest(binding.scope, project))
    ).snapshot
    (project / "worker.py").write_text("import core\n\ndef work():\n    return core.calculate()\n")
    after = repository.store_and_activate(
        SourceStructureParser().parse(SourceStructureParseRequest(binding.scope, project))
    ).snapshot

    result = CliRunner().invoke(
        app,
        [
            "memory",
            "changes",
            "--from",
            str(before.snapshot_id),
            "--to",
            str(after.snapshot_id),
            "--project-dir",
            str(project),
            "--data-dir",
            str(data_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    value = json.loads(result.output)
    assert value["before_snapshot_id"] == str(before.snapshot_id)
    assert value["after_snapshot_id"] == str(after.snapshot_id)
    assert [item["symbol"] for item in value["added_symbols"]] == ["worker", "worker.work"]
    assert value["removed_symbols"] == []
    assert value["file_fingerprints_available"] is True
    assert value["added_files"] == ["worker.py"]
    assert value["removed_files"] == []
    assert value["renamed_files"] == []
    assert value["modified_files"] == []


def test_memory_changes_reports_a_digest_proven_file_rename_without_source_text(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    old_path = project / "legacy.py"
    old_path.write_text("def calculate():\n    return 1\n")
    data_dir = tmp_path / "memory"
    config = LocalConfig.defaults(data_dir)
    binding = LocalMemoryProjectBindingStore(config.data_directory).enable(project)
    repository = SQLiteSourceStructureRepository(config.database_path, base_directory=data_dir)
    repository.migrate()
    before = repository.store_and_activate(
        SourceStructureParser().parse(SourceStructureParseRequest(binding.scope, project))
    ).snapshot
    old_path.rename(project / "current.py")
    after = repository.store_and_activate(
        SourceStructureParser().parse(SourceStructureParseRequest(binding.scope, project))
    ).snapshot

    result = CliRunner().invoke(
        app,
        [
            "memory",
            "changes",
            "--from",
            str(before.snapshot_id),
            "--to",
            str(after.snapshot_id),
            "--project-dir",
            str(project),
            "--data-dir",
            str(data_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    value = json.loads(result.output)
    assert value["added_files"] == []
    assert value["removed_files"] == []
    assert value["renamed_files"] == [{"from": "legacy.py", "to": "current.py"}]
    assert str(project) not in result.output

    latest = CliRunner().invoke(
        app,
        [
            "memory",
            "changes",
            "--latest",
            "--project-dir",
            str(project),
            "--data-dir",
            str(data_dir),
        ],
    )
    assert latest.exit_code == 0, latest.output
    assert json.loads(latest.output) == value

    history = CliRunner().invoke(
        app,
        [
            "memory",
            "history",
            "--limit",
            "2",
            "--project-dir",
            str(project),
            "--data-dir",
            str(data_dir),
        ],
    )
    assert history.exit_code == 0, history.output
    history_value = json.loads(history.output)
    assert history_value["active_snapshot_id"] == str(after.snapshot_id)
    assert [item["snapshot_id"] for item in history_value["snapshots"]] == [
        str(after.snapshot_id),
        str(before.snapshot_id),
    ]
    assert all("path" not in item for item in history_value["snapshots"])


def test_memory_changes_reports_a_body_only_file_change_without_source_text(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    source = project / "core.py"
    source.write_text("def calculate():\n    return 'private first value'\n")
    data_dir = tmp_path / "memory"
    config = LocalConfig.defaults(data_dir)
    binding = LocalMemoryProjectBindingStore(config.data_directory).enable(project)
    repository = SQLiteSourceStructureRepository(config.database_path, base_directory=data_dir)
    repository.migrate()
    repository.store_and_activate(
        SourceStructureParser().parse(SourceStructureParseRequest(binding.scope, project))
    )
    source.write_text("def calculate():\n    return 'private corrected value'\n")
    repository.store_and_activate(
        SourceStructureParser().parse(SourceStructureParseRequest(binding.scope, project))
    )

    result = CliRunner().invoke(
        app,
        ["memory", "changes", "--project-dir", str(project), "--data-dir", str(data_dir)],
    )

    assert result.exit_code == 0, result.output
    value = json.loads(result.output)
    assert value["file_fingerprints_available"] is True
    assert value["added_files"] == []
    assert value["removed_files"] == []
    assert value["modified_files"] == ["core.py"]
    assert value["added_symbols"] == []
    assert value["removed_symbols"] == []
    assert "private first value" not in result.output
    assert "private corrected value" not in result.output
    assert str(project) not in result.output


def test_memory_changes_can_show_bounded_newest_first_history_for_one_path(tmp_path: Path) -> None:
    project = tmp_path / "project history Ω"
    project.mkdir()
    (project / ".git").mkdir()
    tracked = project / "core.py"
    unrelated = project / "private.py"
    tracked.write_text("def core():\n    return 1\n")
    unrelated.write_text("def private():\n    return 1\n")
    data_dir = tmp_path / "memory"
    config = LocalConfig.defaults(data_dir)
    binding = LocalMemoryProjectBindingStore(config.data_directory).enable(project)
    repository = SQLiteSourceStructureRepository(config.database_path, base_directory=data_dir)
    repository.migrate()
    parser = SourceStructureParser()
    repository.store_and_activate(parser.parse(SourceStructureParseRequest(binding.scope, project)))
    tracked.write_text("def core():\n    return 2\n")
    second = repository.store_and_activate(
        parser.parse(SourceStructureParseRequest(binding.scope, project))
    ).snapshot
    unrelated.write_text("def private():\n    return 2\n")
    repository.store_and_activate(parser.parse(SourceStructureParseRequest(binding.scope, project)))
    tracked.write_text("def core():\n    return 3\n")
    fourth = repository.store_and_activate(
        parser.parse(SourceStructureParseRequest(binding.scope, project))
    ).snapshot

    result = CliRunner().invoke(
        app,
        [
            "memory",
            "changes",
            "--path",
            "core.py",
            "--history-limit",
            "3",
            "--project-dir",
            str(project),
            "--data-dir",
            str(data_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    value = json.loads(result.output)
    assert value["requested_relative_path"] == "core.py"
    assert len(value["transitions"]) == 2
    assert value["transitions"][0]["after_snapshot_id"] == str(fourth.snapshot_id)
    assert value["transitions"][1]["after_snapshot_id"] == str(second.snapshot_id)
    assert all("private.py" not in json.dumps(item) for item in value["transitions"])


def test_memory_changes_defaults_to_latest_and_requires_a_real_transition(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    (project / "core.py").write_text("def calculate():\n    return 1\n")
    data_dir = tmp_path / "memory"
    config = LocalConfig.defaults(data_dir)
    binding = LocalMemoryProjectBindingStore(config.data_directory).enable(project)
    repository = SQLiteSourceStructureRepository(config.database_path, base_directory=data_dir)
    repository.migrate()
    repository.store_and_activate(
        SourceStructureParser().parse(SourceStructureParseRequest(binding.scope, project))
    )

    missing = CliRunner().invoke(
        app,
        ["memory", "changes", "--project-dir", str(project), "--data-dir", str(data_dir)],
    )
    conflicting = CliRunner().invoke(
        app,
        [
            "memory",
            "changes",
            "--latest",
            "--from",
            "00000000-0000-4000-8000-000000000000",
            "--to",
            "00000000-0000-4000-8000-000000000000",
            "--project-dir",
            str(project),
            "--data-dir",
            str(data_dir),
        ],
    )

    assert missing.exit_code != 0
    assert "MNEMO_SOURCE_DIFF_NO_PRIOR_TRANSITION" in missing.output
    assert conflicting.exit_code != 0
    assert "MNEMO_SOURCE_DIFF_ARGUMENTS_INVALID" in conflicting.output


def test_memory_refresh_creates_new_snapshot_then_is_idempotent(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    (project / "core.py").write_text("def calculate():\n    return 1\n")
    data_dir = tmp_path / "memory"
    config = LocalConfig.defaults(data_dir)
    LocalMemoryProjectBindingStore(config.data_directory).enable(project)
    runner = CliRunner()

    first = runner.invoke(
        app,
        ["memory", "refresh", "--project-dir", str(project), "--data-dir", str(data_dir)],
    )
    second = runner.invoke(
        app,
        ["memory", "refresh", "--project-dir", str(project), "--data-dir", str(data_dir)],
    )

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    first_value = json.loads(first.output)
    second_value = json.loads(second.output)
    assert first_value["idempotent"] is False
    assert second_value["idempotent"] is True
    assert second_value["previous_snapshot_id"] == first_value["snapshot_id"]
    assert first_value["currentness"] == "unknown_after_refresh"


def test_dbt_configure_shell_hook_and_exec_activate_manifest(tmp_path: Path) -> None:
    project = tmp_path / "dbt project Δ"
    target = project / "target"
    target.mkdir(parents=True)
    (project / "dbt_project.yml").write_text("name: synthetic\n")
    data_dir = tmp_path / "mnemo data"
    runner = CliRunner()
    identifiers = [
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
        "33333333-3333-4333-8333-333333333333",
    ]
    configured = runner.invoke(
        app,
        [
            "dbt",
            "configure",
            "--project-dir",
            str(project),
            "--owner-id",
            identifiers[0],
            "--workspace-id",
            identifiers[1],
            "--project-id",
            identifiers[2],
            "--data-dir",
            str(data_dir),
        ],
    )
    assert configured.exit_code == 0, configured.output
    assert (
        "command mnemo-memory dbt exec" in runner.invoke(app, ["dbt", "shell-hook", "zsh"]).output
    )

    fixture = Path("tests/fixtures/dbt/manifest-v12.json").resolve()
    fake = tmp_path / "fake dbt.py"
    fake.write_text(
        "from pathlib import Path\nimport shutil, sys\n"
        f"source = {str(fixture)!r}\n"
        "target = Path(sys.argv[sys.argv.index('--target-path') + 1]) / 'manifest.json'\n"
        "shutil.copyfile(source, target)\n"
    )
    executed = runner.invoke(
        app,
        [
            "dbt",
            "exec",
            "--data-dir",
            str(data_dir),
            "--dbt-executable",
            str(Path(sys.executable).resolve()),
            "--json-summary",
            "--",
            str(fake),
            "run",
            "--project-dir",
            str(project),
            "--target-path",
            str(target),
        ],
    )
    assert executed.exit_code == 0, executed.output
    assert "MNEMO_DBT_MANIFEST_ACTIVATED" in executed.output
    status = runner.invoke(
        app,
        [
            "dbt",
            "status",
            "--owner-id",
            identifiers[0],
            "--workspace-id",
            identifiers[1],
            "--project-id",
            identifiers[2],
            "--data-dir",
            str(data_dir),
        ],
    )
    assert json.loads(status.output)["active"] is True


def test_dbt_exec_does_not_activate_a_manifest_written_by_a_failing_child(tmp_path: Path) -> None:
    """The actual CLI wrapper must retain the prior active state when dbt itself fails."""
    project = tmp_path / "failing dbt project Δ"
    target = project / "target"
    target.mkdir(parents=True)
    (project / "dbt_project.yml").write_text("name: synthetic\n")
    data_dir = tmp_path / "Mnemo data"
    runner = CliRunner()

    enabled = runner.invoke(
        app, ["dbt", "enable", "--project-dir", str(project), "--data-dir", str(data_dir)]
    )
    assert enabled.exit_code == 0, enabled.output
    assert json.loads(enabled.output)["existing_manifest"] == "unavailable"

    fixture = Path("tests/fixtures/dbt/manifest-v12.json").resolve()
    fake = tmp_path / "failing fake dbt.py"
    fake.write_text(
        "from pathlib import Path\nimport shutil, sys\n"
        f"source = {str(fixture)!r}\n"
        "target = Path(sys.argv[sys.argv.index('--target-path') + 1]) / 'manifest.json'\n"
        "shutil.copyfile(source, target)\n"
        "raise SystemExit(23)\n"
    )
    executed = runner.invoke(
        app,
        [
            "dbt",
            "exec",
            "--data-dir",
            str(data_dir),
            "--dbt-executable",
            str(Path(sys.executable).resolve()),
            "--",
            str(fake),
            "run",
            "--project-dir",
            str(project),
            "--target-path",
            str(target),
        ],
    )

    assert executed.exit_code == 23, executed.output
    assert "MNEMO_DBT_COMMAND_NOT_SUCCESSFUL" in executed.output
    status = runner.invoke(
        app, ["dbt", "status", "--project-dir", str(project), "--data-dir", str(data_dir)]
    )
    assert status.exit_code == 0, status.output
    assert json.loads(status.output)["active"] is False


def test_dbt_exec_runs_validated_installed_hook_registrations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real CLI combines the trusted dbt hook with installed extension registrations."""
    calls: list[str] = []

    def before(_: CommandContext) -> str:
        calls.append("before")
        return "private-state"

    def after(_: CommandContext, state: object, __: CommandResult) -> HookOutcome:
        assert state == "private-state"
        calls.append("after")
        return HookOutcome(HookStatus.UNCHANGED, "MNEMO_PLUGIN_UNCHANGED")

    plugin = HookRegistration("installed-plugin", "dbt", before, after)
    monkeypatch.setattr(
        cli_main,
        "discover_command_hooks",
        lambda integration: (
            HookDiscoveryResult((plugin,), ())
            if integration == "dbt"
            else pytest.fail("wrong integration")
        ),
    )

    result = CliRunner().invoke(
        app,
        [
            "dbt",
            "exec",
            "--data-dir",
            str(tmp_path / "isolated memory"),
            "--dbt-executable",
            str(Path(sys.executable).resolve()),
            "--json-summary",
            "--",
            "-c",
            "raise SystemExit(0)",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == ["before", "after"]
    summary = json.loads(result.output)
    assert [item["hook"] for item in summary["outcomes"]] == [
        "installed-plugin",
        "dbt-manifest",
    ]
    assert "MNEMO_PLUGIN_UNCHANGED" in [item["code"] for item in summary["outcomes"]]


def test_dbt_enable_uses_private_stable_personal_ids_and_optional_existing_manifest(
    tmp_path: Path,
) -> None:
    project = tmp_path / "dbt project Δ"
    target = project / "target"
    target.mkdir(parents=True)
    (project / "dbt_project.yml").write_text("name: synthetic\n")
    fixture = Path("tests/fixtures/dbt/manifest-v12.json")
    target.joinpath("manifest.json").write_bytes(fixture.read_bytes())
    target.joinpath("catalog.json").write_bytes(
        Path("tests/fixtures/dbt/catalog-v1.json").read_bytes()
    )
    target.joinpath("run_results.json").write_bytes(
        Path("tests/fixtures/dbt/run-results-v6.json").read_bytes()
    )
    second = tmp_path / "another dbt project"
    second.mkdir()
    (second / "dbt_project.yml").write_text("name: synthetic\n")
    data_dir = tmp_path / "Mnemo data Δ"
    runner = CliRunner()

    first = runner.invoke(
        app, ["dbt", "enable", "--project-dir", str(project), "--data-dir", str(data_dir)]
    )
    assert first.exit_code == 0, first.output
    result = json.loads(first.output)
    assert result["enabled"] is True
    assert result["existing_manifest"] == "activated"
    assert result["catalog"] == "activated"
    assert result["run_results"] == "activated"
    assert "scope" not in result
    assert (data_dir / "config.json").exists()

    first_binding = _binding(data_dir, project)
    repeated = runner.invoke(
        app, ["dbt", "enable", "--project-dir", str(project), "--data-dir", str(data_dir)]
    )
    assert repeated.exit_code == 0, repeated.output
    assert _binding(data_dir, project).scope == first_binding.scope

    second_enable = runner.invoke(
        app, ["dbt", "enable", "--project-dir", str(second), "--data-dir", str(data_dir)]
    )
    assert second_enable.exit_code == 0, second_enable.output
    second_binding = _binding(data_dir, second)
    assert second_binding.scope.owner_id == first_binding.scope.owner_id
    assert second_binding.scope.workspace_id == first_binding.scope.workspace_id
    assert second_binding.scope.project_id != first_binding.scope.project_id

    status = runner.invoke(
        app, ["dbt", "status", "--project-dir", str(project), "--data-dir", str(data_dir)]
    )
    assert status.exit_code == 0, status.output
    status_value = json.loads(status.output)
    assert status_value["active"] is True
    assert status_value["catalog"] == "available"
    assert status_value["run_results"] == "available"

    disabled = runner.invoke(
        app, ["dbt", "disable", "--project-dir", str(project), "--data-dir", str(data_dir)]
    )
    assert disabled.exit_code == 0, disabled.output
    assert json.loads(disabled.output) == {"enabled": False, "removed": True}
    disabled_status = runner.invoke(
        app, ["dbt", "status", "--project-dir", str(project), "--data-dir", str(data_dir)]
    )
    assert json.loads(disabled_status.output)["instruction"] == "mnemo-memory dbt enable"


def test_dbt_enable_reuses_automatic_memory_project_scope_for_unified_context(
    tmp_path: Path,
) -> None:
    project = tmp_path / "dbt reconciliation project"
    project.mkdir()
    (project / "dbt_project.yml").write_text("name: synthetic\n")
    data_dir = tmp_path / "Mnemo data"
    automatic = LocalMemoryProjectBindingStore(data_dir).enable(project)

    result = CliRunner().invoke(
        app, ["dbt", "enable", "--project-dir", str(project), "--data-dir", str(data_dir)]
    )

    assert result.exit_code == 0, result.output
    assert _binding(data_dir, project).scope == automatic.scope


def _binding(data_dir: Path, project: Path) -> DbtProjectBinding:
    binding = LocalDbtProjectBindingStore(data_dir).get(project)
    assert binding is not None
    return binding


def test_unenabled_dbt_project_runs_normally_and_reports_one_setup_instruction(
    tmp_path: Path,
) -> None:
    project = tmp_path / "unenabled dbt project"
    project.mkdir()
    (project / "dbt_project.yml").write_text("name: synthetic\n")
    data_dir = tmp_path / "Mnemo data"
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "dbt",
            "exec",
            "--data-dir",
            str(data_dir),
            "--dbt-executable",
            str(Path(sys.executable).resolve()),
            "--",
            "-c",
            "raise SystemExit(0)",
            "--project-dir",
            str(project),
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.output.count("mnemo-memory dbt enable") == 1
    assert "MNEMO_DBT_MANIFEST_ACTIVATED" not in result.output
    assert not data_dir.exists()


def test_guide_initializes_only_when_explicitly_requested(tmp_path: Path) -> None:
    data_dir = tmp_path / "guide store"
    result = CliRunner().invoke(
        app, ["guide", "--data-dir", str(data_dir), "--initialize", "--non-interactive"]
    )

    assert result.exit_code == 0
    initialization = next(line for line in result.output.splitlines() if line.startswith("{"))
    assert json.loads(initialization)["initialized"] is True
    assert (data_dir / "config.json").exists()
