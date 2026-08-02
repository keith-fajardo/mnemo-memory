"""Replaceable application port for Issue 7's two MCP operations."""

from __future__ import annotations

from typing import Protocol


class McpContextPort(Protocol):
    def get_context(self, request: dict[str, object]) -> dict[str, object]: ...

    def save_checkpoint(self, request: dict[str, object]) -> dict[str, object]: ...
