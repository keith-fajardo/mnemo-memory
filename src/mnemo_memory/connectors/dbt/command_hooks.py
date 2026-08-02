"""dbt-specific functions used by the generic command-wrapper kernel."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from mnemo_memory.connectors.dbt.project_binding import (
    LocalDbtProjectBindingStore,
    find_dbt_project_root,
)
from mnemo_memory.packages.application.command_wrapper import (
    CommandContext,
    CommandResult,
    HookOutcome,
    HookStatus,
)
from mnemo_memory.packages.application.dbt import (
    DbtApplicationConflict,
    DbtApplicationInvalidManifest,
    DbtApplicationStorageFailure,
    DbtManifestApplicationService,
    GetActiveManifestStatus,
    IngestManifest,
)
from mnemo_memory.packages.domain import DbtSnapshotId, MemoryScope


@dataclass(frozen=True, slots=True)
class DbtBeforeState:
    scope: MemoryScope
    project_root: Path
    manifest_path: Path
    previous_digest: str | None
    expected_active_snapshot_id: DbtSnapshotId | None


def _option_path(arguments: tuple[str, ...], option: str, cwd: Path) -> Path | None:
    for index, argument in enumerate(arguments):
        if argument.startswith(f"{option}="):
            value = argument.removeprefix(f"{option}=")
        elif argument == option and index + 1 < len(arguments):
            value = arguments[index + 1]
        else:
            continue
        path = Path(value)
        return (cwd / path).resolve() if not path.is_absolute() else path.resolve()
    return None


class DbtManifestHooks:
    def __init__(
        self,
        bindings: LocalDbtProjectBindingStore,
        service: DbtManifestApplicationService,
        clock: Callable[[], datetime],
    ) -> None:
        self._bindings = bindings
        self._service = service
        self._clock = clock

    def before_dbt(self, context: CommandContext) -> DbtBeforeState:
        root = _option_path(context.arguments, "--project-dir", context.working_directory)
        project_root = find_dbt_project_root(root or context.working_directory)
        binding = self._bindings.get(project_root)
        if binding is None:
            raise ValueError("MNEMO_DBT_PROJECT_UNCONFIGURED")
        target = _option_path(context.arguments, "--target-path", context.working_directory)
        manifest_path = (target or project_root / "target") / "manifest.json"
        previous = (
            sha256(manifest_path.read_bytes()).hexdigest() if manifest_path.is_file() else None
        )
        active = self._service.get_active_status(GetActiveManifestStatus(binding.scope)).snapshot
        return DbtBeforeState(
            binding.scope,
            project_root,
            manifest_path,
            previous,
            active.snapshot_id if active else None,
        )

    def after_dbt(self, _: CommandContext, state: object, result: CommandResult) -> HookOutcome:
        if not isinstance(state, DbtBeforeState):
            return HookOutcome(HookStatus.FAILED, "MNEMO_DBT_HOOK_STATE_INVALID")
        if not result.started or result.interrupted or result.exit_code != 0:
            return HookOutcome(HookStatus.SKIPPED, "MNEMO_DBT_COMMAND_NOT_SUCCESSFUL")
        if not state.manifest_path.is_file():
            return HookOutcome(HookStatus.UNAVAILABLE, "MNEMO_DBT_MANIFEST_UNAVAILABLE")
        try:
            raw = state.manifest_path.read_bytes()
            digest = sha256(raw).hexdigest()
            if digest == state.previous_digest:
                return HookOutcome(HookStatus.UNCHANGED, "MNEMO_DBT_MANIFEST_UNCHANGED")
            stored = self._service.ingest(
                IngestManifest(
                    state.scope,
                    raw,
                    "manifest.json",
                    self._clock(),
                    expected_active_snapshot_id=state.expected_active_snapshot_id,
                )
            )
        except DbtApplicationConflict:
            return HookOutcome(HookStatus.FAILED, "MNEMO_DBT_ACTIVE_SNAPSHOT_CONFLICT")
        except (DbtApplicationInvalidManifest, DbtApplicationStorageFailure, OSError, ValueError):
            return HookOutcome(HookStatus.FAILED, "MNEMO_DBT_MANIFEST_ACTIVATION_FAILED")
        return HookOutcome(
            HookStatus.UNCHANGED if stored.idempotent else HookStatus.ACTIVATED,
            "MNEMO_DBT_MANIFEST_UNCHANGED" if stored.idempotent else "MNEMO_DBT_MANIFEST_ACTIVATED",
            metadata=(("snapshot", str(stored.snapshot.snapshot_id)),),
        )
