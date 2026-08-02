"""Local stdio-only MCP adapter for synthetic Issue 7 tools."""

from __future__ import annotations

import logging
import sys
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from packages.application.mcp_fixture import FixtureMcpContextPort
from packages.application.mcp_port import McpContextPort

SERVER_NAME = "mnemo-local"
SERVER_VERSION = "0.1.0"


def create_server(port: McpContextPort | None = None) -> FastMCP:
    service = port or FixtureMcpContextPort()
    server = FastMCP(SERVER_NAME, instructions="Synthetic local Mnemo fixture tools only.")
    # FastMCP 1.x exposes the protocol implementation through this adapter field.
    # Setting it here publishes Mnemo's version rather than the SDK version.
    server._mcp_server.version = SERVER_VERSION

    @server.tool(
        name="get_context",
        description="Return an empty, versioned context packet for an explicit owner and query.",
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False),
    )
    def get_context(
        owner_id: Annotated[str, Field(min_length=36, max_length=36)],
        query: Annotated[str, Field(min_length=1, max_length=4_000)],
    ) -> dict[str, object]:
        return service.get_context({"owner_id": owner_id, "query": query})

    @server.tool(
        name="save_checkpoint",
        description="Validate and save a synthetic explicit checkpoint fixture.",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False),
    )
    def save_checkpoint(
        owner_id: Annotated[str, Field(min_length=36, max_length=36)],
        evidence_references: Annotated[list[str], Field(min_length=1, max_length=64)],
        sensitivity: Annotated[str, Field(max_length=16)] = "normal",
    ) -> dict[str, object]:
        return service.save_checkpoint(
            {
                "owner_id": owner_id,
                "evidence_references": evidence_references,
                "sensitivity": sensitivity,
            }
        )

    for name in ("get_context", "save_checkpoint"):
        tool = server._tool_manager._tools[name]
        tool.parameters["additionalProperties"] = False
        tool.fn_metadata.arg_model.model_config["extra"] = "forbid"
        tool.fn_metadata.arg_model.model_rebuild(force=True)

    return server


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(message)s")
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
