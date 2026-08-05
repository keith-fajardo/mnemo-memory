"""Immutable contracts for version-controlled project skills and agents.

The Markdown payload remains untrusted evidence. These values describe discovery metadata only;
they are not executable instructions and cannot authorize a memory or project mutation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from .knowledge import KnowledgeDocumentRevision, KnowledgeDocumentSourceKind
from .procedures import normalize_procedure_tags

_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_SUPPORTED_CLIENTS = frozenset({"codex", "claude-code"})


class ProjectSkillTrust(StrEnum):
    """Authority class accepted by the first local project registry."""

    CHECKED_IN = "checked_in"


def normalize_registry_name(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("registry name must be a string")
    normalized = value.strip().casefold()
    if not _NAME.fullmatch(normalized):
        raise ValueError("registry name is invalid")
    return normalized


def normalize_registry_version(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("registry version must be a string")
    normalized = value.strip()
    if not _VERSION.fullmatch(normalized):
        raise ValueError("registry version must be major.minor.patch")
    return normalized


def normalize_skill_clients(clients: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(clients, tuple) or not 1 <= len(clients) <= len(_SUPPORTED_CLIENTS):
        raise ValueError("skill clients must contain between 1 and 2 values")
    normalized: list[str] = []
    for client in clients:
        if not isinstance(client, str):
            raise TypeError("skill client must be a string")
        value = client.strip().casefold()
        if value not in _SUPPORTED_CLIENTS:
            raise ValueError("skill client is invalid")
        normalized.append(value)
    if len(set(normalized)) != len(normalized):
        raise ValueError("skill clients must be unique")
    return tuple(sorted(normalized))


def normalize_agent_client(client: str) -> str:
    if not isinstance(client, str):
        raise TypeError("agent client must be a string")
    normalized = client.strip().casefold()
    if normalized not in {*_SUPPORTED_CLIENTS, "any"}:
        raise ValueError("agent client is invalid")
    return normalized


@dataclass(frozen=True, slots=True)
class ProjectSkill:
    """One current checked-in skill revision and its exact discovery metadata."""

    revision: KnowledgeDocumentRevision
    name: str
    version: str
    applicability_tags: tuple[str, ...]
    compatible_clients: tuple[str, ...]
    trust: ProjectSkillTrust

    def __post_init__(self) -> None:
        if self.revision.document.source_kind is not KnowledgeDocumentSourceKind.MARKDOWN:
            raise ValueError("skills require checked-in Markdown")
        object.__setattr__(self, "name", normalize_registry_name(self.name))
        object.__setattr__(self, "version", normalize_registry_version(self.version))
        object.__setattr__(
            self, "applicability_tags", normalize_procedure_tags(self.applicability_tags)
        )
        object.__setattr__(
            self, "compatible_clients", normalize_skill_clients(self.compatible_clients)
        )
        if self.trust is not ProjectSkillTrust.CHECKED_IN:
            raise ValueError("project skill trust is invalid")

    @property
    def source_digest(self) -> str:
        return self.revision.document.content_digest


@dataclass(frozen=True, slots=True)
class ProjectAgent:
    """One current checked-in agent profile that requests explicit skill tags."""

    revision: KnowledgeDocumentRevision
    name: str
    version: str
    client: str
    skill_tags: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.revision.document.source_kind is not KnowledgeDocumentSourceKind.MARKDOWN:
            raise ValueError("agents require checked-in Markdown")
        object.__setattr__(self, "name", normalize_registry_name(self.name))
        object.__setattr__(self, "version", normalize_registry_version(self.version))
        object.__setattr__(self, "client", normalize_agent_client(self.client))
        object.__setattr__(self, "skill_tags", normalize_procedure_tags(self.skill_tags))

    @property
    def source_digest(self) -> str:
        return self.revision.document.content_digest
