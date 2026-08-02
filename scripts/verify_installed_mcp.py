"""Bounded smoke test for an already-installed ``mnemo-memory`` command."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import cast

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SCOPE: dict[str, object] = {
    "owner_id": "11111111-1111-4111-8111-111111111111",
    "workspace_id": "22222222-2222-4222-8222-222222222222",
    "project_id": "33333333-3333-4333-8333-333333333333",
    "session_id": "44444444-4444-4444-8444-444444444444",
    "task_id": "55555555-5555-4555-8555-555555555555",
}


def payload(operation: str = "create", **updates: object) -> dict[str, object]:
    result: dict[str, object] = {
        **SCOPE,
        "operation": operation,
        "task_objective": "Installed-artifact durable MCP smoke",
        "current_state": "active",
        "evidence_references": [
            {
                "evidence_id": "66666666-6666-4666-8666-666666666666",
                "source_id": "77777777-7777-4777-8777-777777777777",
                "source_type": "checkpoint",
                "trust_class": "user_authored",
                "immutable_source_ref": "synthetic://installed-artifact",
                "content_hash": "sha256:" + "a" * 64,
                "location": {
                    "uri": "fixture://installed",
                    "start_line": None,
                    "start_column": None,
                    "end_line": None,
                    "end_column": None,
                },
                "observed_at": "2026-08-03T00:00:00+00:00",
                "verification_status": "verified",
            }
        ],
        "token_estimate": 100,
    }
    result.update(updates)
    return result


async def exercise(command: str, data_dir: Path) -> None:
    parameters = StdioServerParameters(
        command=command,
        args=["mcp", "serve", "--stdio"],
        env={**os.environ, "MNEMO_DATA_DIR": str(data_dir)},
    )
    async with stdio_client(parameters) as (read, write), ClientSession(read, write) as session:
        initialized = await session.initialize()
        assert initialized.serverInfo.name == "mnemo-local"
        assert [tool.name for tool in (await session.list_tools()).tools] == [
            "get_context",
            "save_checkpoint",
        ]
        failed = await session.call_tool("save_checkpoint", payload(token_estimate=601))
        assert failed.isError is True
        created = await session.call_tool("save_checkpoint", payload())
        state = cast(dict[str, object], created.structuredContent)
        revised = await session.call_tool(
            "save_checkpoint",
            payload(
                "revise",
                checkpoint_id=state["checkpoint_id"],
                expected_revision_id=state["checkpoint_revision_id"],
                current_state="revised",
            ),
        )
        revised_state = cast(dict[str, object], revised.structuredContent)
        context = await session.call_tool("get_context", SCOPE)
        assert str(revised_state["checkpoint_revision_id"]) in json.dumps(context.structuredContent)
        completed = await session.call_tool(
            "save_checkpoint",
            payload(
                "complete",
                checkpoint_id=state["checkpoint_id"],
                expected_revision_id=revised_state["checkpoint_revision_id"],
            ),
        )
        assert completed.isError is False
        no_active = await session.call_tool("get_context", SCOPE)
        assert no_active.isError is False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--command", required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    arguments = parser.parse_args()
    asyncio.run(asyncio.wait_for(exercise(arguments.command, arguments.data_dir), timeout=20))
    asyncio.run(asyncio.wait_for(exercise(arguments.command, arguments.data_dir), timeout=20))


if __name__ == "__main__":
    main()
