"""Deterministic phase accounting for the dbt command wrapper.

This is an offline synthetic fixture, not a hardware-performance claim.  Its injected clock
separates the work controlled by Mnemo's pre-hook, the wrapped dbt process, and post-hook
manifest parse/activation while proving the resulting snapshot is durable in the repository port.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from mnemo_memory.connectors.dbt.command_hooks import DbtManifestHooks
from mnemo_memory.connectors.dbt.manifest import DbtManifestParser
from mnemo_memory.connectors.dbt.project_binding import (
    DbtProjectBinding,
    LocalDbtProjectBindingStore,
)
from mnemo_memory.packages.application.command_wrapper import (
    CommandContext,
    CommandInvocation,
    CommandResult,
    CommandWrapper,
    HookRegistration,
)
from mnemo_memory.packages.application.dbt import (
    DbtManifestApplicationService,
    GetActiveManifestStatus,
)
from mnemo_memory.packages.domain import (
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.storage import ReferenceProjectIndexRepository

ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "tests/fixtures/dbt/manifest-v12.json"
NOW = datetime(2026, 8, 3, tzinfo=UTC)


class _FixtureResolver:
    def resolve(self, executable: str | Path, wrapper_executable: Path | None = None) -> Path:
        del executable, wrapper_executable
        return Path(__file__).resolve()


class _FixtureExecutor:
    def __init__(self, manifest_path: Path) -> None:
        self._manifest_path = manifest_path

    def execute(self, context: CommandContext) -> CommandResult:
        self._manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self._manifest_path.write_bytes(FIXTURE.read_bytes())
        return CommandResult(
            True,
            0,
            False,
            NOW + timedelta(milliseconds=3),
            NOW + timedelta(milliseconds=23),
        )


def _scope() -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string("11111111-1111-4111-8111-111111111111"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string("22222222-2222-4222-8222-222222222222"),
        ProjectId.from_string("33333333-3333-4333-8333-333333333333"),
    )


def _milliseconds(start: datetime, finish: datetime) -> int:
    return int((finish - start).total_seconds() * 1_000)


def evaluate() -> dict[str, object]:
    """Run one deterministic fake-dbt invocation and return stable phase accounting."""
    with TemporaryDirectory(prefix="mnemo-wrapper-benchmark-") as temporary:
        project = Path(temporary) / "dbt project"
        project.mkdir()
        (project / "dbt_project.yml").write_text("name: synthetic\n", encoding="utf-8")
        manifest_path = project / "target" / "manifest.json"
        bindings = LocalDbtProjectBindingStore(Path(temporary) / "memory")
        bindings.set(DbtProjectBinding(project.resolve(), _scope()))
        service = DbtManifestApplicationService(
            ReferenceProjectIndexRepository(), DbtManifestParser()
        )
        hooks = DbtManifestHooks(bindings, lambda: service, lambda: NOW)
        ticks = iter(NOW + timedelta(milliseconds=value) for value in (0, 1, 2, 24, 32))
        # The built-in dbt hook is registered through the same function-level kernel as a plugin.
        wrapped = CommandWrapper(
            _FixtureResolver(),
            _FixtureExecutor(manifest_path),
            lambda: next(ticks),
            lambda: "wrapper-benchmark",
            (HookRegistration("dbt-manifest", "dbt", hooks.before_dbt, hooks.after_dbt),),
        )
        result = wrapped.run(CommandInvocation("dbt", ("run",), project.resolve(), "dbt"))
        timing = result.hook_timings[0]
        phase_ms = {
            "mnemo_pre_hook": _milliseconds(timing.before_started_at, timing.before_finished_at),
            "dbt_execution": _milliseconds(result.result.started_at, result.result.finished_at),
            "mnemo_post_hook_parse_and_ingestion": _milliseconds(
                timing.after_started_at or NOW, timing.after_finished_at or NOW
            ),
        }
        phase_ms["total"] = sum(phase_ms.values())
        snapshot = service.get_active_status(GetActiveManifestStatus(_scope())).snapshot
        gates = {
            "child_exit_preserved": result.result.exit_code == 0,
            "manifest_activated": result.outcomes[0].outcome.status.value == "activated",
            "active_snapshot": snapshot is not None,
            "phases_non_negative": all(value >= 0 for value in phase_ms.values()),
        }
        return {
            "fixture": "synthetic-dbt-manifest-v12",
            "deterministic": True,
            "hardware_performance_claim": False,
            "phase_milliseconds": phase_ms,
            "gates": gates,
            "passed": all(gates.values()),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = evaluate()
    print(json.dumps(result, sort_keys=True) if args.json else result)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
