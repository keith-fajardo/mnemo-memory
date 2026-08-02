import asyncio
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import Tool

from apps.mcp.server import SERVER_NAME, SERVER_VERSION, create_server
from packages.application.mcp_fixture import FixtureMcpContextPort
from packages.domain import ContextPacket

OWNER_ID = "11111111-1111-4111-8111-111111111111"
ROOT = Path(__file__).parents[2]


def test_server_lists_exact_issue_7_tools_with_safety_annotations() -> None:
    async def list_tools() -> list[Tool]:
        return list(await create_server().list_tools())

    tools = asyncio.run(list_tools())
    assert [tool.name for tool in tools] == ["get_context", "save_checkpoint"]
    get_context_annotations = tools[0].annotations
    save_checkpoint_annotations = tools[1].annotations
    assert get_context_annotations is not None
    assert save_checkpoint_annotations is not None
    assert get_context_annotations.readOnlyHint is True
    assert get_context_annotations.destructiveHint is False
    assert get_context_annotations.openWorldHint is False
    assert save_checkpoint_annotations.readOnlyHint is False
    assert save_checkpoint_annotations.destructiveHint is False
    assert save_checkpoint_annotations.openWorldHint is False
    assert tools[0].inputSchema["additionalProperties"] is False


def test_fixture_port_returns_a_valid_empty_packet_and_safe_save_response() -> None:
    port = FixtureMcpContextPort()
    packet = ContextPacket.from_dict(port.get_context({"owner_id": OWNER_ID, "query": "resume"}))

    assert str(packet.owner_scope.owner_id) == OWNER_ID
    assert packet.declared_total_tokens == 0
    assert packet.remaining_budget == 5700
    assert port.save_checkpoint(
        {"owner_id": OWNER_ID, "evidence_references": ["synthetic-evidence"]}
    ) == {"checkpoint_id": "fixture-checkpoint-0001", "revision": 1, "durability": "fixture-only"}


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"owner_id": "not-a-uuid", "query": "resume"}, "UUID"),
        ({"owner_id": OWNER_ID, "query": ""}, "MNEMO_INVALID_INPUT"),
    ],
)
def test_fixture_port_rejects_invalid_context_input(
    payload: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        FixtureMcpContextPort().get_context(payload)


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"owner_id": OWNER_ID, "evidence_references": []}, "MNEMO_EVIDENCE_REQUIRED"),
        (
            {
                "owner_id": OWNER_ID,
                "evidence_references": ["synthetic-evidence"],
                "sensitivity": "prohibited",
            },
            "MNEMO_PROHIBITED_CONTENT",
        ),
    ],
)
def test_fixture_port_rejects_invalid_checkpoint_input(
    payload: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        FixtureMcpContextPort().save_checkpoint(payload)


def test_real_stdio_server_initializes_calls_tools_and_exits_cleanly() -> None:
    async def exercise() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "apps.mcp.server"],
            cwd=ROOT,
        )
        async with stdio_client(parameters) as (read, write), ClientSession(read, write) as session:
            initialized = await session.initialize()
            assert initialized.serverInfo.name == SERVER_NAME
            assert initialized.serverInfo.version == SERVER_VERSION
            assert [tool.name for tool in (await session.list_tools()).tools] == [
                "get_context",
                "save_checkpoint",
            ]
            context = await session.call_tool(
                "get_context", {"owner_id": OWNER_ID, "query": "synthetic resume"}
            )
            assert context.isError is False
            assert ContextPacket.from_dict(context.structuredContent or {}).remaining_budget == 5700
            invalid = await session.call_tool(
                "get_context",
                {"owner_id": OWNER_ID, "query": "synthetic resume", "unexpected": True},
            )
            assert invalid.isError is True
            saved = await session.call_tool(
                "save_checkpoint",
                {"owner_id": OWNER_ID, "evidence_references": ["synthetic-evidence"]},
                read_timeout_seconds=timedelta(seconds=5),
            )
            assert saved.isError is False
            assert saved.structuredContent == {
                "checkpoint_id": "fixture-checkpoint-0001",
                "revision": 1,
                "durability": "fixture-only",
            }

    asyncio.run(asyncio.wait_for(exercise(), timeout=10))
