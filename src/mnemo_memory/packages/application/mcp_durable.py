"""MCP-facing translation of strict tool payloads into checkpoint application use cases."""

from __future__ import annotations

from collections.abc import Mapping

from mnemo_memory.packages.application.checkpoints import (
    AbandonCheckpoint,
    CheckpointApplicationBudgetExceeded,
    CheckpointApplicationDuplicate,
    CheckpointApplicationError,
    CheckpointApplicationInvalidContent,
    CheckpointApplicationInvalidLifecycle,
    CheckpointApplicationInvalidScope,
    CheckpointApplicationMissingProvenance,
    CheckpointApplicationNotFound,
    CheckpointApplicationRevisionConflict,
    CheckpointApplicationService,
    CheckpointApplicationStorageFailure,
    CompleteCheckpoint,
    CreateCheckpoint,
    GetCheckpointContext,
    ReviseCheckpoint,
)
from mnemo_memory.packages.application.dbt import LineageDirection
from mnemo_memory.packages.application.unified_context import (
    ContextLineageQuery,
    GetUnifiedContext,
    UnifiedContextService,
)
from mnemo_memory.packages.domain import (
    CheckpointContent,
    CheckpointId,
    CheckpointRevisionId,
    ContextBudget,
    DbtNodeId,
    DbtSnapshotId,
    EvidenceReference,
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    SessionId,
    TaskId,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.domain.identifiers import Identifier


class DurableMcpContextPort:
    """Thin MCP translation layer; lifecycle and persistence remain in application services."""

    def __init__(
        self,
        service: CheckpointApplicationService,
        context_service: UnifiedContextService | None = None,
    ) -> None:
        self._service = service
        self._context_service = context_service

    def get_context(self, request: dict[str, object]) -> dict[str, object]:
        try:
            scope = _scope(request)
            checkpoint = _optional_id(request, "checkpoint_id", CheckpointId)
            budget = ContextBudget(
                active_task_checkpoint=_integer(request.get("active_task_checkpoint_tokens", 600)),
                total_limit=_integer(request.get("total_tokens", 5700)),
            )
            lineage = request.get("dbt_lineage")
            if lineage is not None:
                if self._context_service is None:
                    raise CheckpointApplicationStorageFailure("dbt project index is unavailable")
                if not isinstance(lineage, Mapping):
                    raise ValueError("dbt_lineage must be an object")
                direction = LineageDirection(_string(lineage, "direction"))
                dbt_query = ContextLineageQuery(
                    DbtNodeId(_string(lineage, "unique_id")),
                    direction,
                    bool(lineage.get("transitive", True)),
                    lineage.get("maximum_depth"),
                    int(lineage.get("maximum_nodes", 500)),
                    int(lineage.get("maximum_edges", 1000)),
                    _optional_id(lineage, "snapshot_id", DbtSnapshotId),
                    lineage.get("current_content_digest")
                    if isinstance(lineage.get("current_content_digest"), str)
                    else None,
                    None,
                    bool(lineage.get("require_current", False)),
                )
                return self._context_service.get_context(
                    GetUnifiedContext(scope, checkpoint, dbt_query, budget)
                ).to_dict()
            return self._service.get_context(
                GetCheckpointContext(scope, checkpoint, budget)
            ).to_dict()
        except Exception as error:
            raise _mcp_error(error) from error

    def save_checkpoint(self, request: dict[str, object]) -> dict[str, object]:
        try:
            operation = _string(request, "operation")
            scope = _scope(request)
            content = _content(request)
            evidence = _evidence(request)
            if operation == "create":
                view = self._service.create(
                    CreateCheckpoint(
                        scope,
                        content,
                        evidence,
                        _optional_id(request, "checkpoint_id", CheckpointId),
                    )
                )
            else:
                checkpoint_id = _required_id(request, "checkpoint_id", CheckpointId)
                expected_revision_id = _required_id(
                    request, "expected_revision_id", CheckpointRevisionId
                )
                if operation == "revise":
                    view = self._service.revise(
                        ReviseCheckpoint(
                            scope, checkpoint_id, expected_revision_id, content, evidence
                        )
                    )
                elif operation == "complete":
                    view = self._service.complete(
                        CompleteCheckpoint(
                            scope, checkpoint_id, expected_revision_id, content, evidence
                        )
                    )
                elif operation == "abandon":
                    view = self._service.abandon(
                        AbandonCheckpoint(
                            scope,
                            checkpoint_id,
                            expected_revision_id,
                            _string(request, "reason"),
                            content,
                            evidence,
                        )
                    )
                else:
                    raise ValueError("operation must be create, revise, complete, or abandon")
            return {
                "checkpoint_id": str(view.aggregate.checkpoint_id),
                "checkpoint_revision_id": str(view.revision.revision_id),
                "revision_number": view.revision.revision_number,
                "lifecycle_status": view.aggregate.lifecycle_status.value,
                "scope": view.aggregate.scope.to_dict(),
                "persistence": "durable",
            }
        except Exception as error:
            raise _mcp_error(error) from error


def _scope(request: Mapping[str, object]) -> MemoryScope:
    return MemoryScope(
        owner_id=_required_id(request, "owner_id", OwnerId),
        level=ScopeLevel.TASK,
        visibility=Visibility(_string(request, "visibility", default="project")),
        workspace_id=_required_id(request, "workspace_id", WorkspaceId),
        project_id=_required_id(request, "project_id", ProjectId),
        session_id=_required_id(request, "session_id", SessionId),
        task_id=_required_id(request, "task_id", TaskId),
    )


def _content(request: Mapping[str, object]) -> CheckpointContent:
    return CheckpointContent(
        task_objective=_string(request, "task_objective"),
        completed_work=_strings(request, "completed_work"),
        current_state=_string(request, "current_state"),
        remaining_work=_strings(request, "remaining_work"),
        decisions=_strings(request, "decisions"),
        failures=_strings(request, "failures"),
        blockers=_strings(request, "blockers"),
        relevant_files=_strings(request, "relevant_files"),
        relevant_artifacts=_strings(request, "relevant_artifacts"),
        verification_performed=_strings(request, "verification_performed"),
        token_estimate=_integer(request.get("token_estimate")),
    )


def _evidence(request: Mapping[str, object]) -> tuple[EvidenceReference, ...]:
    value = request.get("evidence_references")
    if not isinstance(value, list) or not value:
        raise CheckpointApplicationMissingProvenance("checkpoint evidence is required")
    if not all(isinstance(item, Mapping) for item in value):
        raise ValueError("evidence_references must contain objects")
    return tuple(EvidenceReference.from_dict(item) for item in value)


def _strings(request: Mapping[str, object], name: str) -> tuple[str, ...]:
    value = request.get(name, [])
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{name} must be an array of strings")
    return tuple(value)


def _string(request: Mapping[str, object], name: str, *, default: str | None = None) -> str:
    value = request.get(name, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("token values must be non-negative integers")
    return value


def _required_id[IdentifierType: Identifier](
    request: Mapping[str, object], name: str, cls: type[IdentifierType]
) -> IdentifierType:
    value = request.get(name)
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a UUID")
    return cls.from_string(value)


def _optional_id[IdentifierType: Identifier](
    request: Mapping[str, object], name: str, cls: type[IdentifierType]
) -> IdentifierType | None:
    if request.get(name) is None:
        return None
    return _required_id(request, name, cls)


def _mcp_error(error: Exception) -> ValueError:
    codes: tuple[tuple[type[Exception], str], ...] = (
        (CheckpointApplicationNotFound, "MNEMO_CHECKPOINT_NOT_FOUND"),
        (CheckpointApplicationDuplicate, "MNEMO_DUPLICATE_CHECKPOINT"),
        (CheckpointApplicationRevisionConflict, "MNEMO_REVISION_CONFLICT"),
        (CheckpointApplicationInvalidLifecycle, "MNEMO_INVALID_LIFECYCLE"),
        (CheckpointApplicationInvalidScope, "MNEMO_INVALID_SCOPE"),
        (CheckpointApplicationMissingProvenance, "MNEMO_EVIDENCE_REQUIRED"),
        (CheckpointApplicationBudgetExceeded, "MNEMO_TOKEN_BUDGET"),
        (CheckpointApplicationStorageFailure, "MNEMO_STORAGE_UNAVAILABLE"),
        (CheckpointApplicationInvalidContent, "MNEMO_INVALID_INPUT"),
        (CheckpointApplicationError, "MNEMO_APPLICATION_ERROR"),
    )
    for error_type, code in codes:
        if isinstance(error, error_type):
            return ValueError(f"{code}: request could not be completed")
    return ValueError("MNEMO_INVALID_INPUT: request could not be completed")
