"""Replaceable application port for context retrieval and checkpoint persistence."""

from __future__ import annotations

from typing import Protocol


class McpContextPort(Protocol):
    def get_context(self, request: dict[str, object]) -> dict[str, object]: ...

    def list_skills(self, request: dict[str, object]) -> dict[str, object]: ...

    def get_skill(self, request: dict[str, object]) -> dict[str, object]: ...

    def save_checkpoint(self, request: dict[str, object]) -> dict[str, object]: ...
