"""dbt-specific functions used by the generic command-wrapper kernel."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from mnemo_memory.connectors.dbt.git_state import DbtGitStateObserver
from mnemo_memory.connectors.dbt.project_binding import (
    DbtProjectBindingError,
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
    DbtApplicationNotFound,
    DbtApplicationStorageFailure,
    DbtManifestApplicationService,
    GetActiveManifestStatus,
    IngestCatalog,
    IngestManifest,
    IngestRunResults,
    IngestSourceFreshness,
)
from mnemo_memory.packages.domain import DbtSnapshotId, MemoryScope


@dataclass(frozen=True, slots=True)
class DbtBeforeState:
    scope: MemoryScope | None
    project_root: Path | None
    manifest_path: Path | None
    previous_digest: str | None = None
    expected_active_snapshot_id: DbtSnapshotId | None = None
    expected_active_content_digest: str | None = None
    skip_code: str | None = None


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


def _option_value(arguments: tuple[str, ...], option: str) -> str | None:
    for index, argument in enumerate(arguments):
        if argument.startswith(f"{option}="):
            return argument.removeprefix(f"{option}=")
        if argument == option and index + 1 < len(arguments):
            return arguments[index + 1]
    return None


class DbtManifestHooks:
    def __init__(
        self,
        bindings: LocalDbtProjectBindingStore,
        service_factory: Callable[[], DbtManifestApplicationService],
        clock: Callable[[], datetime],
        git_observer: DbtGitStateObserver | None = None,
    ) -> None:
        self._bindings = bindings
        self._service_factory = service_factory
        self._clock = clock
        self._git_observer = git_observer or DbtGitStateObserver()

    def before_dbt(self, context: CommandContext) -> DbtBeforeState:
        root = _option_path(context.arguments, "--project-dir", context.working_directory)
        try:
            project_root = find_dbt_project_root(root or context.working_directory)
        except DbtProjectBindingError:
            return DbtBeforeState(None, None, None, skip_code="MNEMO_DBT_PROJECT_UNCONFIGURED")
        binding = self._bindings.get(project_root)
        if binding is None:
            return DbtBeforeState(
                None, project_root, None, skip_code="MNEMO_DBT_PROJECT_UNCONFIGURED"
            )
        target = _option_path(context.arguments, "--target-path", context.working_directory)
        manifest_path = (target or project_root / "target") / "manifest.json"
        try:
            previous = (
                sha256(manifest_path.read_bytes()).hexdigest() if manifest_path.is_file() else None
            )
        except OSError:
            # Do not turn an unreadable prior target artifact into a dbt failure.  The wrapper
            # will emit this bounded status, skip post-ingestion, and leave existing memory alone.
            return DbtBeforeState(
                None, project_root, None, skip_code="MNEMO_DBT_MANIFEST_UNAVAILABLE"
            )
        try:
            active = (
                self._service_factory()
                .get_active_status(GetActiveManifestStatus(binding.scope))
                .snapshot
            )
        except DbtApplicationStorageFailure:
            # Preserve dbt's normal behavior when Mnemo storage is unavailable.
            return DbtBeforeState(
                None, project_root, None, skip_code="MNEMO_DBT_STORAGE_UNAVAILABLE"
            )
        return DbtBeforeState(
            binding.scope,
            project_root,
            manifest_path,
            previous,
            active.snapshot_id if active else None,
            active.metadata.content_digest if active else None,
        )

    def after_dbt(
        self, context: CommandContext, state: object, result: CommandResult
    ) -> HookOutcome:
        if not isinstance(state, DbtBeforeState):
            return HookOutcome(HookStatus.FAILED, "MNEMO_DBT_HOOK_STATE_INVALID")
        if state.scope is None:
            return HookOutcome(
                HookStatus.SKIPPED, state.skip_code or "MNEMO_DBT_HOOK_STATE_INVALID"
            )
        if not result.started or result.interrupted or result.exit_code != 0:
            return HookOutcome(HookStatus.SKIPPED, "MNEMO_DBT_COMMAND_NOT_SUCCESSFUL")
        if state.manifest_path is None or not state.manifest_path.is_file():
            return HookOutcome(HookStatus.UNAVAILABLE, "MNEMO_DBT_MANIFEST_UNAVAILABLE")
        try:
            raw = state.manifest_path.read_bytes()
            digest = sha256(raw).hexdigest()
            service = self._service_factory()
            source_state = (
                self._git_observer.observe(
                    state.project_root,
                    target_name=_option_value(context.arguments, "--target"),
                )
                if state.project_root is not None
                else None
            )
            if (
                digest == state.previous_digest
                and digest == state.expected_active_content_digest
                and state.expected_active_snapshot_id is not None
            ):
                snapshot_id = state.expected_active_snapshot_id
                manifest_changed = False
            else:
                stored = service.ingest(
                    IngestManifest(
                        state.scope,
                        raw,
                        "manifest.json",
                        self._clock(),
                        expected_active_snapshot_id=state.expected_active_snapshot_id,
                        source_state=source_state,
                    )
                )
                snapshot_id = stored.snapshot.snapshot_id
                manifest_changed = not stored.idempotent
            supplemental, supplemental_changed = self._ingest_supplemental(
                service, state, snapshot_id
            )
        except DbtApplicationConflict:
            return HookOutcome(HookStatus.FAILED, "MNEMO_DBT_ACTIVE_SNAPSHOT_CONFLICT")
        except (DbtApplicationInvalidManifest, DbtApplicationStorageFailure, OSError, ValueError):
            return HookOutcome(HookStatus.FAILED, "MNEMO_DBT_MANIFEST_ACTIVATION_FAILED")
        changed = manifest_changed or supplemental_changed
        return HookOutcome(
            HookStatus.ACTIVATED if changed else HookStatus.UNCHANGED,
            (
                "MNEMO_DBT_MANIFEST_ACTIVATED"
                if manifest_changed
                else (
                    "MNEMO_DBT_SUPPLEMENTAL_ACTIVATED"
                    if supplemental_changed
                    else "MNEMO_DBT_MANIFEST_UNCHANGED"
                )
            ),
            metadata=(("snapshot", str(snapshot_id)), *supplemental),
        )

    def _ingest_supplemental(
        self,
        service: DbtManifestApplicationService,
        state: DbtBeforeState,
        snapshot_id: DbtSnapshotId,
    ) -> tuple[tuple[tuple[str, str], ...], bool]:
        assert state.scope is not None and state.manifest_path is not None
        metadata: list[tuple[str, str]] = []
        changed = False
        for kind, filename in (
            ("catalog", "catalog.json"),
            ("run_results", "run_results.json"),
            ("source_freshness", "sources.json"),
        ):
            path = state.manifest_path.with_name(filename)
            if not path.is_file():
                metadata.append((kind, "unavailable"))
                continue
            try:
                raw = path.read_bytes()
                observed_at = self._clock()
                if kind == "catalog":
                    stored = service.ingest_catalog(
                        IngestCatalog(state.scope, snapshot_id, raw, filename, observed_at)
                    )
                elif kind == "run_results":
                    stored = service.ingest_run_results(
                        IngestRunResults(state.scope, snapshot_id, raw, filename, observed_at)
                    )
                else:
                    stored = service.ingest_source_freshness(
                        IngestSourceFreshness(state.scope, snapshot_id, raw, filename, observed_at)
                    )
            except (
                DbtApplicationConflict,
                DbtApplicationInvalidManifest,
                DbtApplicationNotFound,
                DbtApplicationStorageFailure,
                OSError,
                ValueError,
            ):
                metadata.append((kind, "invalid_or_unavailable"))
                continue
            metadata.append((kind, "unchanged" if stored.idempotent else "activated"))
            changed = changed or not stored.idempotent
        return tuple(metadata), changed
