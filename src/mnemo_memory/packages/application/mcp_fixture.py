"""Synthetic Issue 7 fixture implementation; it is not durable storage."""

from __future__ import annotations

from datetime import UTC, datetime

from mnemo_memory.packages.domain import (
    ContextBudget,
    ContextPacket,
    MemoryScope,
    OwnerId,
    PacketSchemaVersion,
    RequestId,
    ScopeLevel,
    Visibility,
)


class FixtureMcpContextPort:
    def get_context(self, request: dict[str, object]) -> dict[str, object]:
        owner = OwnerId.from_string(_id(request, "owner_id"))
        query = request.get("query")
        if not isinstance(query, str) or not query:
            raise ValueError("MNEMO_INVALID_INPUT: query is required")
        packet = ContextPacket(
            PacketSchemaVersion.V1,
            RequestId.new(),
            MemoryScope(owner, ScopeLevel.PERSONAL, Visibility.OWNER),
            query,
            None,
            datetime.now(UTC),
            None,
            0,
            ContextBudget(),
            "mnemo-mcp/0.1.0",
        )
        return packet.to_dict()

    def save_checkpoint(self, request: dict[str, object]) -> dict[str, object]:
        OwnerId.from_string(_id(request, "owner_id"))
        if request.get("sensitivity") == "prohibited":
            raise ValueError("MNEMO_PROHIBITED_CONTENT: prohibited content cannot be saved")
        if not request.get("evidence_references"):
            raise ValueError("MNEMO_EVIDENCE_REQUIRED: checkpoint evidence is required")
        return {
            "checkpoint_id": "fixture-checkpoint-0001",
            "revision": 1,
            "durability": "fixture-only",
        }


def _id(request: dict[str, object], name: str) -> str:
    value = request.get(name)
    if not isinstance(value, str):
        raise ValueError(f"MNEMO_INVALID_INPUT: {name} must be a UUID")
    return value
