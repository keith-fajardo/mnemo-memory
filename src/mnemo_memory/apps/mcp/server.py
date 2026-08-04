"""Local stdio-only MCP adapter for durable explicit checkpoints."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from mnemo_memory.connectors.automatic_memory.source_observation import CheckpointSourceObserver
from mnemo_memory.connectors.dbt.manifest import DbtManifestParser
from mnemo_memory.connectors.local_embeddings import FastEmbedLocalProvider
from mnemo_memory.packages.application import (
    LocalConfigurationError,
    LocalRuntimeError,
    build_checkpoint_runtime,
    resolve_local_config,
)
from mnemo_memory.packages.application.automatic_memory import LocalMemoryProjectBindingStore
from mnemo_memory.packages.application.mcp_durable import DurableMcpContextPort
from mnemo_memory.packages.application.mcp_port import McpContextPort
from mnemo_memory.packages.application.unified_context import UnifiedContextService

SERVER_NAME = "mnemo-local"
SERVER_VERSION = "0.1.0"


def create_server(port: McpContextPort) -> FastMCP:
    """Create the two-tool protocol adapter around an explicitly supplied application port."""
    server = FastMCP(SERVER_NAME, instructions="Local Mnemo checkpoint tools.")
    server._mcp_server.version = SERVER_VERSION

    @server.tool(
        name="get_context",
        description="Return a bounded context packet for an explicit task scope.",
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False),
    )
    def get_context(
        owner_id: Annotated[str, Field(min_length=36, max_length=36)],
        workspace_id: Annotated[str, Field(min_length=36, max_length=36)],
        project_id: Annotated[str, Field(min_length=36, max_length=36)],
        session_id: Annotated[str, Field(min_length=36, max_length=36)],
        task_id: Annotated[str, Field(min_length=36, max_length=36)],
        checkpoint_id: Annotated[
            str | None, Field(default=None, min_length=36, max_length=36)
        ] = None,
        dbt_lineage: Annotated[dict[str, object] | None, Field(default=None)] = None,
        source_query: Annotated[
            str | None, Field(default=None, min_length=1, max_length=512)
        ] = None,
        knowledge_query: Annotated[
            str | None,
            Field(
                default=None,
                min_length=1,
                max_length=512,
                description=(
                    "Optional literal query for scoped, cited local knowledge-document sections."
                ),
            ),
        ] = None,
        semantic_knowledge_query: Annotated[
            str | None,
            Field(
                default=None,
                min_length=1,
                max_length=512,
                description=(
                    "Optional local-only semantic query. It uses only an explicitly built local "
                    "semantic index and never sends text to a model provider."
                ),
            ),
        ] = None,
        source_impact: Annotated[dict[str, object] | None, Field(default=None)] = None,
        source_changes: Annotated[dict[str, object] | None, Field(default=None)] = None,
        source_overview: Annotated[dict[str, object] | None, Field(default=None)] = None,
        include_lifecycle_events: Annotated[
            bool,
            Field(
                default=False,
                description="Include a bounded evidence-bearing checkpoint lifecycle timeline.",
            ),
        ] = False,
        include_approved_events: Annotated[
            bool,
            Field(
                default=False,
                description=(
                    "Include bounded explicit evidence-backed decisions, failures, and "
                    "tool outcomes."
                ),
            ),
        ] = False,
        active_task_checkpoint_tokens: Annotated[int, Field(ge=0, le=8_000)] = 600,
        total_tokens: Annotated[int, Field(ge=0, le=8_000)] = 5700,
    ) -> dict[str, object]:
        return port.get_context(
            {
                "owner_id": owner_id,
                "workspace_id": workspace_id,
                "project_id": project_id,
                "session_id": session_id,
                "task_id": task_id,
                "checkpoint_id": checkpoint_id,
                "dbt_lineage": dbt_lineage,
                "source_query": source_query,
                "knowledge_query": knowledge_query,
                "semantic_knowledge_query": semantic_knowledge_query,
                "source_impact": source_impact,
                "source_changes": source_changes,
                "source_overview": source_overview,
                "include_lifecycle_events": include_lifecycle_events,
                "include_approved_events": include_approved_events,
                "active_task_checkpoint_tokens": active_task_checkpoint_tokens,
                "total_tokens": total_tokens,
            }
        )

    @server.tool(
        name="save_checkpoint",
        description=(
            "Create, revise, complete, abandon, record a correction lesson, or record one "
            "explicit evidence-backed decision, failure, or tool outcome for durable task memory."
        ),
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False),
    )
    def save_checkpoint(
        operation: Annotated[
            str,
            Field(
                pattern="^(create|revise|complete|abandon|record_lesson|record_event)$",
                description=(
                    "Lifecycle operation. record_lesson appends exactly one evidence-backed "
                    "correction to the current active revision without resending the complete "
                    "checkpoint; record_event stores one explicit approved episodic fact."
                ),
            ),
        ],
        owner_id: Annotated[str, Field(min_length=36, max_length=36)],
        workspace_id: Annotated[str, Field(min_length=36, max_length=36)],
        project_id: Annotated[str, Field(min_length=36, max_length=36)],
        session_id: Annotated[str, Field(min_length=36, max_length=36)],
        task_id: Annotated[str, Field(min_length=36, max_length=36)],
        task_objective: Annotated[
            str | None,
            Field(
                default=None,
                min_length=1,
                max_length=4_000,
                description=(
                    "Required checkpoint content for create, revise, complete, and abandon."
                ),
            ),
        ] = None,
        current_state: Annotated[
            str | None,
            Field(
                default=None,
                min_length=1,
                max_length=4_000,
                description=(
                    "Required checkpoint content for create, revise, complete, and abandon."
                ),
            ),
        ] = None,
        evidence_references: Annotated[
            list[dict[str, object]] | None,
            Field(
                default=None,
                min_length=1,
                max_length=64,
                description=(
                    "Required evidence for every save; lesson evidence IDs must be retained here."
                ),
            ),
        ] = None,
        token_estimate: Annotated[int | None, Field(default=None, ge=0, le=600)] = None,
        checkpoint_id: Annotated[
            str | None,
            Field(
                default=None,
                min_length=36,
                max_length=36,
                description="Required for every operation except create.",
            ),
        ] = None,
        expected_revision_id: Annotated[
            str | None,
            Field(
                default=None,
                min_length=36,
                max_length=36,
                description="Required for revise, complete, abandon, and record_lesson.",
            ),
        ] = None,
        reason: Annotated[str | None, Field(default=None, max_length=4_000)] = None,
        event_kind: Annotated[
            str | None,
            Field(default=None, pattern="^(decision|failure|tool_outcome)$"),
        ] = None,
        event_summary: Annotated[
            str | None, Field(default=None, min_length=1, max_length=1_200)
        ] = None,
        source_event_key: Annotated[
            str | None, Field(default=None, min_length=1, max_length=256)
        ] = None,
        completed_work: Annotated[list[str] | None, Field(default=None, max_length=128)] = None,
        remaining_work: Annotated[list[str] | None, Field(default=None, max_length=128)] = None,
        decisions: Annotated[list[str] | None, Field(default=None, max_length=128)] = None,
        failures: Annotated[list[str] | None, Field(default=None, max_length=128)] = None,
        lessons: Annotated[
            list[dict[str, object]] | None,
            Field(
                default=None,
                max_length=16,
                description=(
                    "Canonical correction lessons. record_lesson requires exactly one; complete "
                    "content revisions may retain up to 16 applicable lessons."
                ),
            ),
        ] = None,
        blockers: Annotated[list[str] | None, Field(default=None, max_length=128)] = None,
        relevant_files: Annotated[list[str] | None, Field(default=None, max_length=128)] = None,
        relevant_artifacts: Annotated[list[str] | None, Field(default=None, max_length=128)] = None,
        verification_performed: Annotated[
            list[str] | None, Field(default=None, max_length=128)
        ] = None,
    ) -> dict[str, object]:
        return port.save_checkpoint(
            {
                "operation": operation,
                "owner_id": owner_id,
                "workspace_id": workspace_id,
                "project_id": project_id,
                "session_id": session_id,
                "task_id": task_id,
                "task_objective": task_objective,
                "current_state": current_state,
                "evidence_references": evidence_references,
                "token_estimate": token_estimate,
                "checkpoint_id": checkpoint_id,
                "expected_revision_id": expected_revision_id,
                "reason": reason,
                "event_kind": event_kind,
                "event_summary": event_summary,
                "source_event_key": source_event_key,
                "completed_work": completed_work,
                "remaining_work": remaining_work,
                "decisions": decisions,
                "failures": failures,
                "lessons": lessons,
                "blockers": blockers,
                "relevant_files": relevant_files,
                "relevant_artifacts": relevant_artifacts,
                "verification_performed": verification_performed,
            }
        )

    for name in ("get_context", "save_checkpoint"):
        tool = server._tool_manager._tools[name]
        tool.parameters["additionalProperties"] = False
        tool.fn_metadata.arg_model.model_config["extra"] = "forbid"
        tool.fn_metadata.arg_model.model_rebuild(force=True)
    return server


def main(data_directory: Path | None = None) -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(message)s")
    with build_checkpoint_runtime(
        resolve_local_config(data_directory), dbt_parser=DbtManifestParser()
    ) as runtime:
        assert runtime.dbt_manifest_service is not None
        assert runtime.source_structure_repository is not None
        assert runtime.knowledge_document_repository is not None
        observer = CheckpointSourceObserver(
            LocalMemoryProjectBindingStore(runtime.config.data_directory),
            runtime.source_structure_repository,
            runtime.repository,
            lambda: datetime.now(UTC),
        )
        # Construction is inert: FastEmbed is imported and model weights are requested only when
        # a caller explicitly sends semantic_knowledge_query after local semantic indexing.
        from mnemo_memory.packages.knowledge import LocalSemanticKnowledgeRetriever

        semantic_knowledge = LocalSemanticKnowledgeRetriever(
            runtime.knowledge_document_repository,
            FastEmbedLocalProvider(runtime.config.data_directory / "semantic-model-cache"),
        )
        create_server(
            DurableMcpContextPort(
                runtime.checkpoint_service,
                UnifiedContextService(
                    runtime.checkpoint_service,
                    runtime.dbt_manifest_service,
                    runtime.source_structure_repository,
                    runtime.repository,
                    runtime.knowledge_document_repository,
                    semantic_knowledge,
                ),
                observer.observe,
            )
        ).run(transport="stdio")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir")
    args = parser.parse_args()
    try:
        main(None if args.data_dir is None else Path(args.data_dir))
    except (LocalConfigurationError, LocalRuntimeError) as error:
        logging.basicConfig(
            level=logging.ERROR, stream=sys.stderr, format="%(levelname)s %(message)s"
        )
        logging.error("MNEMO_STORAGE_UNAVAILABLE: %s", error)
        raise SystemExit(2) from error
