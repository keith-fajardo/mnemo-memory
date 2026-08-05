"""MCP-facing translation of strict tool payloads into checkpoint application use cases."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol, cast

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
    ContextDbtChangesQuery,
    ContextDbtCodeExcerptQuery,
    ContextDbtFreshnessQuery,
    ContextDbtSelectorQuery,
    ContextDbtTestCoverageQuery,
    ContextLineageQuery,
    ContextSourceChangeQuery,
    ContextSourceImpactQuery,
    ContextSourceOverviewQuery,
    GetUnifiedContext,
)
from mnemo_memory.packages.domain import (
    ApprovedEventKind,
    CheckpointContent,
    CheckpointId,
    CheckpointLesson,
    CheckpointRevisionId,
    CodeSnapshotId,
    ContextBudget,
    ContextPacket,
    DbtNodeId,
    DbtSnapshotId,
    EvidenceReference,
    MemoryScope,
    OwnerId,
    ProjectId,
    ProjectSkill,
    ScopeLevel,
    SessionId,
    SourceStateFingerprint,
    TaskId,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.domain.identifiers import Identifier
from mnemo_memory.packages.storage import ProjectSkillRegistry


class UnifiedContextPort(Protocol):
    def get_context(self, request: GetUnifiedContext) -> ContextPacket: ...


class DurableMcpContextPort:
    """Thin MCP translation layer; lifecycle and persistence remain in application services."""

    def __init__(
        self,
        service: CheckpointApplicationService,
        context_service: UnifiedContextPort | None = None,
        after_checkpoint_save: Callable[[CheckpointView], object] | None = None,
        default_scope: MemoryScope | None = None,
        current_dbt_source_state: (
            Callable[[MemoryScope], SourceStateFingerprint | None] | None
        ) = None,
        skills: ProjectSkillRegistry | None = None,
        default_budget: ContextBudget | None = None,
        approved_event_capture_enabled: bool = True,
    ) -> None:
        self._service = service
        self._context_service = context_service
        self._after_checkpoint_save = after_checkpoint_save
        self._default_scope = default_scope
        self._current_dbt_source_state = current_dbt_source_state
        self._skills = skills
        self._default_budget = default_budget or ContextBudget()
        if not isinstance(approved_event_capture_enabled, bool):
            raise TypeError("approved event capture setting must be a boolean")
        self._approved_event_capture_enabled = approved_event_capture_enabled

    def _resolve_current_dbt_source_state(
        self, scope: MemoryScope
    ) -> SourceStateFingerprint | None:
        if self._current_dbt_source_state is None:
            return None
        try:
            return self._current_dbt_source_state(scope)
        except Exception:
            return None

    def get_context(self, request: dict[str, object]) -> dict[str, object]:
        try:
            scope = _scope(request, self._default_scope)
            checkpoint = _optional_id(request, "checkpoint_id", CheckpointId)
            budget = ContextBudget(
                active_task_checkpoint=_integer(
                    request.get(
                        "active_task_checkpoint_tokens",
                        self._default_budget.active_task_checkpoint,
                    )
                ),
                episodic_memories=self._default_budget.episodic_memories,
                knowledge=self._default_budget.knowledge,
                structural=self._default_budget.structural,
                skills_and_procedures=self._default_budget.skills_and_procedures,
                provenance_and_conflicts=self._default_budget.provenance_and_conflicts,
                total_limit=_integer(request.get("total_tokens", self._default_budget.total_limit)),
            )
            lineage = request.get("dbt_lineage")
            test_coverage = request.get("dbt_test_coverage")
            dbt_selector = request.get("dbt_selector")
            dbt_freshness = request.get("dbt_freshness")
            dbt_changes = request.get("dbt_changes")
            source_query = request.get("source_query")
            source_impact = request.get("source_impact")
            source_changes = request.get("source_changes")
            source_overview = request.get("source_overview")
            query = request.get("query")
            knowledge_query = request.get("knowledge_query")
            semantic_knowledge_query = request.get("semantic_knowledge_query")
            procedure_tags = request.get("procedure_tags", [])
            skill_tags = request.get("skill_tags", [])
            skill_client = request.get("skill_client")
            skill_agent_name = request.get("skill_agent_name")
            include_lifecycle_events = request.get("include_lifecycle_events", False)
            include_approved_events = request.get("include_approved_events", False)
            if not isinstance(include_lifecycle_events, bool):
                raise ValueError("include_lifecycle_events must be a boolean")
            if not isinstance(include_approved_events, bool):
                raise ValueError("include_approved_events must be a boolean")
            if source_query is not None and not isinstance(source_query, str):
                raise ValueError("source_query must be a string")
            if query is not None and not isinstance(query, str):
                raise ValueError("query must be a string")
            if knowledge_query is not None and not isinstance(knowledge_query, str):
                raise ValueError("knowledge_query must be a string")
            if semantic_knowledge_query is not None and not isinstance(
                semantic_knowledge_query, str
            ):
                raise ValueError("semantic_knowledge_query must be a string")
            if (
                not isinstance(procedure_tags, list)
                or len(procedure_tags) > 8
                or any(not isinstance(tag, str) for tag in procedure_tags)
            ):
                raise ValueError("procedure_tags must be an array of at most 8 strings")
            if (
                not isinstance(skill_tags, list)
                or len(skill_tags) > 8
                or any(not isinstance(tag, str) for tag in skill_tags)
            ):
                raise ValueError("skill_tags must be an array of at most 8 strings")
            if skill_client is not None and not isinstance(skill_client, str):
                raise ValueError("skill_client must be a string")
            if skill_agent_name is not None and not isinstance(skill_agent_name, str):
                raise ValueError("skill_agent_name must be a string")
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
            if test_coverage is not None and not isinstance(test_coverage, Mapping):
                raise ValueError("dbt_test_coverage must be an object")
            if dbt_selector is not None and not isinstance(dbt_selector, Mapping):
                raise ValueError("dbt_selector must be an object")
            if dbt_freshness is not None and not isinstance(dbt_freshness, Mapping):
                raise ValueError("dbt_freshness must be an object")
            if dbt_changes is not None and not isinstance(dbt_changes, Mapping):
                raise ValueError("dbt_changes must be an object")
            if (
                sum(
                    query is not None
                    for query in (
                        lineage,
                        test_coverage,
                        dbt_selector,
                        dbt_freshness,
                        dbt_changes,
                    )
                )
                > 1
            ):
                raise ValueError("request only one dbt structural query at a time")
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
                or test_coverage is not None
                or dbt_selector is not None
                or dbt_freshness is not None
                or dbt_changes is not None
                or source_query is not None
                or query is not None
                or impact is not None
                or changes is not None
                or overview is not None
                or knowledge_query is not None
                or semantic_knowledge_query is not None
                or procedure_tags
                or skill_tags
                or skill_agent_name is not None
            ):
                if self._context_service is None:
                    raise CheckpointApplicationStorageFailure("dbt project index is unavailable")
                if lineage is not None and not isinstance(lineage, Mapping):
                    raise ValueError("dbt_lineage must be an object")
                if (
                    lineage is None
                    and test_coverage is None
                    and dbt_selector is None
                    and dbt_freshness is None
                    and dbt_changes is None
                ):
                    return self._context_service.get_context(
                        GetUnifiedContext(
                            scope=scope,
                            checkpoint_id=checkpoint,
                            query=query,
                            source_query=source_query,
                            budget=budget,
                            source_impact=impact,
                            source_changes=changes,
                            source_overview=overview,
                            knowledge_query=knowledge_query,
                            semantic_knowledge_query=semantic_knowledge_query,
                            procedure_tags=tuple(cast(list[str], procedure_tags)),
                            skill_tags=tuple(cast(list[str], skill_tags)),
                            skill_client=skill_client,
                            skill_agent_name=skill_agent_name,
                            include_lifecycle_events=include_lifecycle_events,
                            include_approved_events=include_approved_events,
                        )
                    ).to_dict()
                if dbt_changes is not None:
                    has_before = "before_snapshot_id" in dbt_changes
                    has_after = "after_snapshot_id" in dbt_changes
                    if has_before != has_after:
                        raise ValueError(
                            "dbt_changes requires both before_snapshot_id and after_snapshot_id"
                        )
                    changes_query = ContextDbtChangesQuery(
                        int(dbt_changes.get("maximum_changes", 32)),
                        int(dbt_changes.get("maximum_affected_nodes", 64)),
                        _optional_id(dbt_changes, "before_snapshot_id", DbtSnapshotId),
                        _optional_id(dbt_changes, "after_snapshot_id", DbtSnapshotId),
                        self._resolve_current_dbt_source_state(scope),
                        bool(dbt_changes.get("require_current", False)),
                    )
                    return self._context_service.get_context(
                        GetUnifiedContext(
                            scope=scope,
                            checkpoint_id=checkpoint,
                            query=query,
                            dbt_changes=changes_query,
                            source_query=source_query,
                            budget=budget,
                            source_impact=impact,
                            source_changes=changes,
                            source_overview=overview,
                            knowledge_query=knowledge_query,
                            semantic_knowledge_query=semantic_knowledge_query,
                            procedure_tags=tuple(cast(list[str], procedure_tags)),
                            skill_tags=tuple(cast(list[str], skill_tags)),
                            skill_client=skill_client,
                            skill_agent_name=skill_agent_name,
                            include_lifecycle_events=include_lifecycle_events,
                            include_approved_events=include_approved_events,
                        )
                    ).to_dict()
                if dbt_freshness is not None:
                    has_unique_id = "unique_id" in dbt_freshness
                    has_relative_path = "relative_path" in dbt_freshness
                    if has_unique_id == has_relative_path:
                        raise ValueError(
                            "dbt_freshness requires exactly one unique_id or relative_path"
                        )
                    if has_relative_path and not isinstance(dbt_freshness["relative_path"], str):
                        raise ValueError("dbt_freshness.relative_path must be a string")
                    freshness_query = ContextDbtFreshnessQuery(
                        DbtNodeId(_string(dbt_freshness, "unique_id")) if has_unique_id else None,
                        _optional_id(dbt_freshness, "snapshot_id", DbtSnapshotId),
                        dbt_freshness.get("current_content_digest")
                        if isinstance(dbt_freshness.get("current_content_digest"), str)
                        else None,
                        self._resolve_current_dbt_source_state(scope),
                        bool(dbt_freshness.get("require_current", False)),
                        cast(str, dbt_freshness["relative_path"]) if has_relative_path else None,
                    )
                    return self._context_service.get_context(
                        GetUnifiedContext(
                            scope=scope,
                            checkpoint_id=checkpoint,
                            query=query,
                            dbt_freshness=freshness_query,
                            source_query=source_query,
                            budget=budget,
                            source_impact=impact,
                            source_changes=changes,
                            source_overview=overview,
                            knowledge_query=knowledge_query,
                            semantic_knowledge_query=semantic_knowledge_query,
                            procedure_tags=tuple(cast(list[str], procedure_tags)),
                            skill_tags=tuple(cast(list[str], skill_tags)),
                            skill_client=skill_client,
                            skill_agent_name=skill_agent_name,
                            include_lifecycle_events=include_lifecycle_events,
                            include_approved_events=include_approved_events,
                        )
                    ).to_dict()
                if dbt_selector is not None:
                    for field_name in ("resource_type", "package_name", "tag"):
                        value = dbt_selector.get(field_name)
                        if value is not None and not isinstance(value, str):
                            raise ValueError(f"dbt_selector.{field_name} must be a string")
                    selector_query = ContextDbtSelectorQuery(
                        cast(str, dbt_selector["resource_type"])
                        if "resource_type" in dbt_selector
                        else None,
                        cast(str, dbt_selector["package_name"])
                        if "package_name" in dbt_selector
                        else None,
                        cast(str, dbt_selector["tag"]) if "tag" in dbt_selector else None,
                        int(dbt_selector.get("maximum_nodes", 32)),
                        _optional_id(dbt_selector, "snapshot_id", DbtSnapshotId),
                        dbt_selector.get("current_content_digest")
                        if isinstance(dbt_selector.get("current_content_digest"), str)
                        else None,
                        self._resolve_current_dbt_source_state(scope),
                        bool(dbt_selector.get("require_current", False)),
                    )
                    return self._context_service.get_context(
                        GetUnifiedContext(
                            scope=scope,
                            checkpoint_id=checkpoint,
                            query=query,
                            dbt_selector=selector_query,
                            source_query=source_query,
                            budget=budget,
                            source_impact=impact,
                            source_changes=changes,
                            source_overview=overview,
                            knowledge_query=knowledge_query,
                            semantic_knowledge_query=semantic_knowledge_query,
                            procedure_tags=tuple(cast(list[str], procedure_tags)),
                            skill_tags=tuple(cast(list[str], skill_tags)),
                            skill_client=skill_client,
                            skill_agent_name=skill_agent_name,
                            include_lifecycle_events=include_lifecycle_events,
                            include_approved_events=include_approved_events,
                        )
                    ).to_dict()
                if test_coverage is not None:
                    has_unique_id = "unique_id" in test_coverage
                    has_relative_path = "relative_path" in test_coverage
                    if has_unique_id == has_relative_path:
                        raise ValueError(
                            "dbt_test_coverage requires exactly one unique_id or relative_path"
                        )
                    if has_relative_path and not isinstance(test_coverage["relative_path"], str):
                        raise ValueError("dbt_test_coverage.relative_path must be a string")
                    coverage_query = ContextDbtTestCoverageQuery(
                        DbtNodeId(_string(test_coverage, "unique_id")) if has_unique_id else None,
                        int(test_coverage.get("maximum_tests", 32)),
                        _optional_id(test_coverage, "snapshot_id", DbtSnapshotId),
                        test_coverage.get("current_content_digest")
                        if isinstance(test_coverage.get("current_content_digest"), str)
                        else None,
                        self._resolve_current_dbt_source_state(scope),
                        bool(test_coverage.get("require_current", False)),
                        cast(str, test_coverage["relative_path"]) if has_relative_path else None,
                    )
                    return self._context_service.get_context(
                        GetUnifiedContext(
                            scope=scope,
                            checkpoint_id=checkpoint,
                            query=query,
                            dbt_test_coverage=coverage_query,
                            source_query=source_query,
                            budget=budget,
                            source_impact=impact,
                            source_changes=changes,
                            source_overview=overview,
                            knowledge_query=knowledge_query,
                            semantic_knowledge_query=semantic_knowledge_query,
                            procedure_tags=tuple(cast(list[str], procedure_tags)),
                            skill_tags=tuple(cast(list[str], skill_tags)),
                            skill_client=skill_client,
                            skill_agent_name=skill_agent_name,
                            include_lifecycle_events=include_lifecycle_events,
                            include_approved_events=include_approved_events,
                        )
                    ).to_dict()
                assert lineage is not None
                direction = LineageDirection(_string(lineage, "direction"))
                has_unique_id = "unique_id" in lineage
                has_relative_path = "relative_path" in lineage
                if has_unique_id == has_relative_path:
                    raise ValueError("dbt_lineage requires exactly one unique_id or relative_path")
                if has_relative_path and not isinstance(lineage["relative_path"], str):
                    raise ValueError("dbt_lineage.relative_path must be a string")
                if "path_to_unique_id" in lineage and not isinstance(
                    lineage["path_to_unique_id"], str
                ):
                    raise ValueError("dbt_lineage.path_to_unique_id must be a string")
                include_excerpt = lineage.get("include_code_excerpt", False)
                if not isinstance(include_excerpt, bool):
                    raise ValueError("dbt_lineage.include_code_excerpt must be a boolean")
                excerpt_options = {"excerpt_start_line", "excerpt_maximum_lines"} & set(lineage)
                if excerpt_options and not include_excerpt:
                    raise ValueError("dbt lineage excerpt options require include_code_excerpt")
                for field_name in excerpt_options:
                    value = lineage[field_name]
                    if isinstance(value, bool) or not isinstance(value, int):
                        raise ValueError(f"dbt_lineage.{field_name} must be an integer")
                excerpt_query = (
                    ContextDbtCodeExcerptQuery(
                        int(lineage.get("excerpt_start_line", 1)),
                        int(lineage.get("excerpt_maximum_lines", 20)),
                    )
                    if include_excerpt
                    else None
                )
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
                    self._resolve_current_dbt_source_state(scope),
                    bool(lineage.get("require_current", False)),
                    cast(str, lineage["relative_path"]) if has_relative_path else None,
                    DbtNodeId(cast(str, lineage["path_to_unique_id"]))
                    if "path_to_unique_id" in lineage
                    else None,
                    excerpt_query,
                )
                return self._context_service.get_context(
                    GetUnifiedContext(
                        scope=scope,
                        checkpoint_id=checkpoint,
                        query=query,
                        lineage=dbt_query,
                        source_query=source_query,
                        budget=budget,
                        source_impact=impact,
                        source_changes=changes,
                        source_overview=overview,
                        knowledge_query=knowledge_query,
                        semantic_knowledge_query=semantic_knowledge_query,
                        procedure_tags=tuple(cast(list[str], procedure_tags)),
                        skill_tags=tuple(cast(list[str], skill_tags)),
                        skill_client=skill_client,
                        skill_agent_name=skill_agent_name,
                        include_lifecycle_events=include_lifecycle_events,
                        include_approved_events=include_approved_events,
                    )
                ).to_dict()
            if self._context_service is not None:
                return self._context_service.get_context(
                    GetUnifiedContext(
                        scope=scope,
                        checkpoint_id=checkpoint,
                        query=query,
                        budget=budget,
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

    def list_skills(self, request: dict[str, object]) -> dict[str, object]:
        """List bounded current skill metadata in one explicitly resolved project scope."""
        try:
            if self._skills is None:
                raise CheckpointApplicationStorageFailure("skill registry is unavailable")
            scope = _project_scope(_scope(request, self._default_scope))
            client = _concrete_client(request)
            maximum_skills = _integer(request.get("maximum_skills", 32))
            if not 1 <= maximum_skills <= 32:
                raise ValueError("maximum_skills must be between 1 and 32")
            skills = self._skills.list_current_skills(scope, client, maximum_skills)
            return {
                "client": client,
                "scope": scope.to_dict(),
                "skills": [_skill_metadata(skill) for skill in skills],
            }
        except Exception as error:
            raise _mcp_error(error) from error

    def get_skill(self, request: dict[str, object]) -> dict[str, object]:
        """Return one exact current checked-in skill without executing its Markdown."""
        try:
            if self._skills is None:
                raise CheckpointApplicationStorageFailure("skill registry is unavailable")
            scope = _project_scope(_scope(request, self._default_scope))
            client = _concrete_client(request)
            name = _string(request, "name")
            skill = self._skills.get_current_skill(scope, name, client)
            return {
                "client": client,
                "scope": scope.to_dict(),
                "skill": None if skill is None else _skill_detail(skill),
            }
        except Exception as error:
            raise _mcp_error(error) from error

    def save_checkpoint(self, request: dict[str, object]) -> dict[str, object]:
        try:
            operation = _string(request, "operation")
            scope = _scope(request, self._default_scope)
            evidence = _evidence(request)
            if operation == "record_event":
                if not self._approved_event_capture_enabled:
                    raise CheckpointApplicationInvalidContent(
                        "approved event capture is disabled by personal settings"
                    )
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


def _skill_metadata(skill: ProjectSkill) -> dict[str, object]:
    revision, document = skill.revision, skill.revision.document
    return {
        "applicability_tags": list(skill.applicability_tags),
        "compatible_clients": list(skill.compatible_clients),
        "document_path": document.relative_path,
        "name": skill.name,
        "revision_id": str(revision.revision_id),
        "source_digest": skill.source_digest,
        "trust": skill.trust.value,
        "version": skill.version,
    }


def _skill_detail(skill: ProjectSkill) -> dict[str, object]:
    return {
        **_skill_metadata(skill),
        "content_representation": "untrusted_evidence",
        "document_id": str(skill.revision.document.document_id),
        "sections": [
            {"content": section.content, "heading": section.heading, "level": section.level}
            for section in skill.revision.document.sections
        ],
        "title": skill.revision.document.title,
    }


def _project_scope(scope: MemoryScope) -> MemoryScope:
    assert scope.workspace_id is not None and scope.project_id is not None
    return MemoryScope(
        scope.owner_id,
        ScopeLevel.PROJECT,
        scope.visibility,
        scope.workspace_id,
        scope.project_id,
    )


def _concrete_client(request: Mapping[str, object]) -> str:
    client = _string(request, "client")
    if client not in {"codex", "claude-code"}:
        raise ValueError("client must be codex or claude-code")
    return client


def _scope(request: Mapping[str, object], default: MemoryScope | None = None) -> MemoryScope:
    names = ("owner_id", "workspace_id", "project_id", "session_id", "task_id")
    supplied = tuple(request.get(name) is not None for name in names)
    if not any(supplied) and default is not None:
        return default
    if any(supplied) and not all(supplied):
        raise ValueError("scope identifiers must be supplied together")
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
