"""MCP-facing translation of strict tool payloads into checkpoint application use cases."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import cast

from mnemo_memory.packages.application.checkpoints import (
    AbandonCheckpoint,
    CheckpointApplicationBudgetExceeded,
    CheckpointApplicationDuplicate,
    CheckpointApplicationEpisodicEventConflict,
    CheckpointApplicationError,
    CheckpointApplicationInvalidContent,
    CheckpointApplicationInvalidLifecycle,
    CheckpointApplicationInvalidScope,
    CheckpointApplicationMissingProvenance,
    CheckpointApplicationNotFound,
    CheckpointApplicationRevisionConflict,
    CheckpointApplicationService,
    CheckpointApplicationStorageFailure,
    CheckpointView,
    CompleteCheckpoint,
    CreateCheckpoint,
    GetCheckpointContext,
    RecordApprovedEpisodicEvent,
    RecordCheckpointLesson,
    ReviseCheckpoint,
)
from mnemo_memory.packages.application.dbt import LineageDirection
from mnemo_memory.packages.application.unified_context import (
    ContextLineageQuery,
    ContextSourceChangeQuery,
    ContextSourceImpactQuery,
    ContextSourceOverviewQuery,
    GetUnifiedContext,
    UnifiedContextService,
)
from mnemo_memory.packages.domain import (
    ApprovedEventKind,
    CheckpointContent,
    CheckpointId,
    CheckpointLesson,
    CheckpointRevisionId,
    CodeSnapshotId,
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
        after_checkpoint_save: Callable[[CheckpointView], object] | None = None,
    ) -> None:
        self._service = service
        self._context_service = context_service
        self._after_checkpoint_save = after_checkpoint_save

    def get_context(self, request: dict[str, object]) -> dict[str, object]:
        try:
            scope = _scope(request)
            checkpoint = _optional_id(request, "checkpoint_id", CheckpointId)
            budget = ContextBudget(
                active_task_checkpoint=_integer(request.get("active_task_checkpoint_tokens", 600)),
                total_limit=_integer(request.get("total_tokens", 5700)),
            )
            lineage = request.get("dbt_lineage")
            source_query = request.get("source_query")
            source_impact = request.get("source_impact")
            source_changes = request.get("source_changes")
            source_overview = request.get("source_overview")
            knowledge_query = request.get("knowledge_query")
            semantic_knowledge_query = request.get("semantic_knowledge_query")
            include_lifecycle_events = request.get("include_lifecycle_events", False)
            include_approved_events = request.get("include_approved_events", False)
            if not isinstance(include_lifecycle_events, bool):
                raise ValueError("include_lifecycle_events must be a boolean")
            if not isinstance(include_approved_events, bool):
                raise ValueError("include_approved_events must be a boolean")
            if source_query is not None and not isinstance(source_query, str):
                raise ValueError("source_query must be a string")
            if knowledge_query is not None and not isinstance(knowledge_query, str):
                raise ValueError("knowledge_query must be a string")
            if semantic_knowledge_query is not None and not isinstance(
                semantic_knowledge_query, str
            ):
                raise ValueError("semantic_knowledge_query must be a string")
            if source_impact is not None and not isinstance(source_impact, Mapping):
                raise ValueError("source_impact must be an object")
            if source_impact is not None:
                has_symbol = "symbol" in source_impact
                has_relative_path = "relative_path" in source_impact
                if has_symbol == has_relative_path:
                    raise ValueError("source_impact requires exactly one symbol or relative_path")
                if has_relative_path and not isinstance(source_impact["relative_path"], str):
                    raise ValueError("source_impact.relative_path must be a string")
            if source_changes is not None and not isinstance(source_changes, Mapping):
                raise ValueError("source_changes must be an object")
            if source_changes is not None and (
                "relative_path" in source_changes
                and not isinstance(source_changes["relative_path"], str)
            ):
                raise ValueError("source_changes.relative_path must be a string")
            if source_overview is not None and not isinstance(source_overview, Mapping):
                raise ValueError("source_overview must be an object")
            impact = (
                None
                if source_impact is None
                else ContextSourceImpactQuery(
                    _string(source_impact, "symbol") if "symbol" in source_impact else None,
                    str(source_impact.get("direction", "dependents")),
                    bool(source_impact.get("transitive", True)),
                    source_impact.get("maximum_depth"),
                    int(source_impact.get("maximum_symbols", 100)),
                    int(source_impact.get("maximum_edges", 200)),
                    _optional_id(source_impact, "snapshot_id", CodeSnapshotId),
                    source_impact.get("current_source_digest")
                    if isinstance(source_impact.get("current_source_digest"), str)
                    else None,
                    bool(source_impact.get("require_current", False)),
                    cast(str, source_impact["relative_path"])
                    if "relative_path" in source_impact
                    else None,
                )
            )
            changes = (
                None
                if source_changes is None
                else ContextSourceChangeQuery(
                    maximum_declarations=int(source_changes.get("maximum_declarations", 24)),
                    maximum_relationships=int(source_changes.get("maximum_relationships", 24)),
                    maximum_files=int(source_changes.get("maximum_files", 24)),
                    maximum_transitions=int(source_changes.get("maximum_transitions", 1)),
                    relative_path=(
                        cast(str, source_changes["relative_path"])
                        if "relative_path" in source_changes
                        else None
                    ),
                    current_source_digest=source_changes.get("current_source_digest")
                    if isinstance(source_changes.get("current_source_digest"), str)
                    else None,
                    require_current=bool(source_changes.get("require_current", False)),
                    before_snapshot_id=_optional_id(
                        source_changes, "before_snapshot_id", CodeSnapshotId
                    ),
                    after_snapshot_id=_optional_id(
                        source_changes, "after_snapshot_id", CodeSnapshotId
                    ),
                )
            )
            overview = (
                None
                if source_overview is None
                else ContextSourceOverviewQuery(
                    maximum_files=int(source_overview.get("maximum_files", 12)),
                    maximum_modules=int(source_overview.get("maximum_modules", 12)),
                    maximum_declarations=int(source_overview.get("maximum_declarations", 24)),
                    snapshot_id=_optional_id(source_overview, "snapshot_id", CodeSnapshotId),
                    current_source_digest=source_overview.get("current_source_digest")
                    if isinstance(source_overview.get("current_source_digest"), str)
                    else None,
                    require_current=bool(source_overview.get("require_current", False)),
                )
            )
            if (
                lineage is not None
                or source_query is not None
                or impact is not None
                or changes is not None
                or overview is not None
                or knowledge_query is not None
                or semantic_knowledge_query is not None
            ):
                if self._context_service is None:
                    raise CheckpointApplicationStorageFailure("dbt project index is unavailable")
                if lineage is not None and not isinstance(lineage, Mapping):
                    raise ValueError("dbt_lineage must be an object")
                if lineage is None:
                    return self._context_service.get_context(
                        GetUnifiedContext(
                            scope=scope,
                            checkpoint_id=checkpoint,
                            source_query=source_query,
                            budget=budget,
                            source_impact=impact,
                            source_changes=changes,
                            source_overview=overview,
                            knowledge_query=knowledge_query,
                            semantic_knowledge_query=semantic_knowledge_query,
                            include_lifecycle_events=include_lifecycle_events,
                            include_approved_events=include_approved_events,
                        )
                    ).to_dict()
                direction = LineageDirection(_string(lineage, "direction"))
                has_unique_id = "unique_id" in lineage
                has_relative_path = "relative_path" in lineage
                if has_unique_id == has_relative_path:
                    raise ValueError("dbt_lineage requires exactly one unique_id or relative_path")
                if has_relative_path and not isinstance(lineage["relative_path"], str):
                    raise ValueError("dbt_lineage.relative_path must be a string")
                dbt_query = ContextLineageQuery(
                    DbtNodeId(_string(lineage, "unique_id")) if has_unique_id else None,
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
                    cast(str, lineage["relative_path"]) if has_relative_path else None,
                )
                return self._context_service.get_context(
                    GetUnifiedContext(
                        scope=scope,
                        checkpoint_id=checkpoint,
                        lineage=dbt_query,
                        source_query=source_query,
                        budget=budget,
                        source_impact=impact,
                        source_changes=changes,
                        source_overview=overview,
                        knowledge_query=knowledge_query,
                        semantic_knowledge_query=semantic_knowledge_query,
                        include_lifecycle_events=include_lifecycle_events,
                        include_approved_events=include_approved_events,
                    )
                ).to_dict()
            return self._service.get_context(
                GetCheckpointContext(
                    scope,
                    checkpoint,
                    budget,
                    include_lifecycle_events,
                    8,
                    include_approved_events,
                )
            ).to_dict()
        except Exception as error:
            raise _mcp_error(error) from error

    def save_checkpoint(self, request: dict[str, object]) -> dict[str, object]:
        try:
            operation = _string(request, "operation")
            scope = _scope(request)
            evidence = _evidence(request)
            if operation == "record_event":
                event = self._service.record_approved_event(
                    RecordApprovedEpisodicEvent(
                        scope,
                        ApprovedEventKind(_string(request, "event_kind")),
                        _string(request, "event_summary"),
                        _string(request, "source_event_key"),
                        evidence,
                    )
                )
                return {
                    "event_id": str(event.event.event_id),
                    "event_kind": event.event.kind.value,
                    "scope": event.event.scope.to_dict(),
                    "persistence": "durable",
                    "idempotent": event.idempotent,
                }
            if operation == "create":
                content = _content(request)
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
                    content = _content(request)
                    view = self._service.revise(
                        ReviseCheckpoint(
                            scope, checkpoint_id, expected_revision_id, content, evidence
                        )
                    )
                elif operation == "complete":
                    content = _content(request)
                    view = self._service.complete(
                        CompleteCheckpoint(
                            scope, checkpoint_id, expected_revision_id, content, evidence
                        )
                    )
                elif operation == "abandon":
                    content = _content(request)
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
                elif operation == "record_lesson":
                    lessons = _lessons(request)
                    if len(lessons) != 1:
                        raise ValueError("record_lesson requires exactly one lesson")
                    view = self._service.record_lesson(
                        RecordCheckpointLesson(
                            scope, checkpoint_id, expected_revision_id, lessons[0], evidence
                        )
                    )
                else:
                    raise ValueError(
                        "operation must be create, revise, complete, abandon, record_lesson, "
                        "or record_event"
                    )
            self._observe_checkpoint_save(view)
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

    def _observe_checkpoint_save(self, view: CheckpointView) -> None:
        """Keep optional local structure observation fail-open and outside MCP errors."""
        if self._after_checkpoint_save is None:
            return
        try:
            self._after_checkpoint_save(view)
        except Exception:
            # A durable checkpoint has already succeeded. Do not disclose parser/storage details
            # or turn an optional local index refresh into a failed save response.
            return


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
        lessons=_lessons(request),
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


def _lessons(request: Mapping[str, object]) -> tuple[CheckpointLesson, ...]:
    value = request.get("lessons", [])
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise ValueError("lessons must be an array of objects")
    return tuple(CheckpointLesson.from_dict(item) for item in value)


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
        (CheckpointApplicationEpisodicEventConflict, "MNEMO_EPISODIC_EVENT_CONFLICT"),
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
