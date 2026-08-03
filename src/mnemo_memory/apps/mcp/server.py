"""Local stdio-only MCP adapter for durable explicit checkpoints."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from mnemo_memory.connectors.dbt.manifest import DbtManifestParser
from mnemo_memory.packages.application import (
    LocalConfigurationError,
    LocalRuntimeError,
    build_checkpoint_runtime,
    resolve_local_config,
)
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
        source_impact: Annotated[dict[str, object] | None, Field(default=None)] = None,
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
                "source_impact": source_impact,
                "active_task_checkpoint_tokens": active_task_checkpoint_tokens,
                "total_tokens": total_tokens,
            }
        )

    @server.tool(
        name="save_checkpoint",
        description="Explicitly create, revise, complete, or abandon a durable task checkpoint.",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False),
    )
    def save_checkpoint(
        operation: Annotated[str, Field(pattern="^(create|revise|complete|abandon)$")],
        owner_id: Annotated[str, Field(min_length=36, max_length=36)],
        workspace_id: Annotated[str, Field(min_length=36, max_length=36)],
        project_id: Annotated[str, Field(min_length=36, max_length=36)],
        session_id: Annotated[str, Field(min_length=36, max_length=36)],
        task_id: Annotated[str, Field(min_length=36, max_length=36)],
        task_objective: Annotated[str, Field(min_length=1, max_length=4_000)],
        current_state: Annotated[str, Field(min_length=1, max_length=4_000)],
        evidence_references: Annotated[list[dict[str, object]], Field(min_length=1, max_length=64)],
        token_estimate: Annotated[int, Field(ge=0, le=600)],
        checkpoint_id: Annotated[
            str | None, Field(default=None, min_length=36, max_length=36)
        ] = None,
        expected_revision_id: Annotated[
            str | None, Field(default=None, min_length=36, max_length=36)
        ] = None,
        reason: Annotated[str | None, Field(default=None, max_length=4_000)] = None,
        completed_work: Annotated[list[str] | None, Field(default=None, max_length=128)] = None,
        remaining_work: Annotated[list[str] | None, Field(default=None, max_length=128)] = None,
        decisions: Annotated[list[str] | None, Field(default=None, max_length=128)] = None,
        failures: Annotated[list[str] | None, Field(default=None, max_length=128)] = None,
        lessons: Annotated[
            list[dict[str, object]] | None, Field(default=None, max_length=16)
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
        create_server(
            DurableMcpContextPort(
                runtime.checkpoint_service,
                UnifiedContextService(
                    runtime.checkpoint_service,
                    runtime.dbt_manifest_service,
                    runtime.source_structure_repository,
                ),
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
