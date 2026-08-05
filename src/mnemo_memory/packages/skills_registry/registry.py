"""Scope-first discovery of version-controlled Mnemo skills and agents."""

from __future__ import annotations

from mnemo_memory.packages.domain import (
    KnowledgeDocumentSourceKind,
    MemoryScope,
    ProjectAgent,
    ProjectSkill,
    ProjectSkillTrust,
    ScopeLevel,
    normalize_agent_client,
    normalize_procedure_tags,
    normalize_registry_name,
    normalize_skill_clients,
)
from mnemo_memory.packages.storage import KnowledgeDocumentRepository

_KIND = "mnemo_kind"
_NAME = "mnemo_name"
_VERSION = "mnemo_version"
_TAGS = "mnemo_tags"
_CLIENTS = "mnemo_clients"
_CLIENT = "mnemo_client"
_SKILL_TAGS = "mnemo_skill_tags"
_TRUST = "mnemo_trust"


class KnowledgeDocumentSkillRegistry:
    """Read current skill/agent metadata directly from immutable document revisions.

    There is deliberately no registry cache. A synchronized new document revision is visible on
    the next call, while the existing knowledge repository retains its predecessor revision.
    """

    def __init__(self, documents: KnowledgeDocumentRepository) -> None:
        self._documents = documents

    def list_current_skills(
        self, scope: MemoryScope, client: str, maximum_skills: int = 32
    ) -> tuple[ProjectSkill, ...]:
        project_scope = _require_project_scope(scope)
        compatible_client = _require_supported_client(client)
        _require_limit(maximum_skills)
        skills = tuple(
            skill
            for skill in self._iter_current_skills(project_scope)
            if compatible_client in skill.compatible_clients
        )
        return _unique_skills(skills)[:maximum_skills]

    def get_current_skill(self, scope: MemoryScope, name: str, client: str) -> ProjectSkill | None:
        expected_name = normalize_registry_name(name)
        matches = tuple(
            skill
            for skill in self.list_current_skills(scope, client)
            if skill.name == expected_name
        )
        return matches[0] if len(matches) == 1 else None

    def find_applicable_skills(
        self,
        scope: MemoryScope,
        tags: tuple[str, ...],
        client: str,
        maximum_skills: int = 8,
    ) -> tuple[ProjectSkill, ...]:
        expected_tags = set(normalize_procedure_tags(tags))
        return tuple(
            skill
            for skill in self.list_current_skills(scope, client, 32)
            if expected_tags.intersection(skill.applicability_tags)
        )[: _require_limit(maximum_skills)]

    def get_current_agent(self, scope: MemoryScope, name: str, client: str) -> ProjectAgent | None:
        project_scope = _require_project_scope(scope)
        expected_name = normalize_registry_name(name)
        compatible_client = _require_supported_client(client)
        matches = tuple(
            agent
            for agent in self._iter_current_agents(project_scope)
            if agent.name == expected_name and agent.client in {compatible_client, "any"}
        )
        exact = tuple(agent for agent in matches if agent.client == compatible_client)
        fallback = tuple(agent for agent in matches if agent.client == "any")
        selected = exact or fallback
        return selected[0] if len(selected) == 1 else None

    def _iter_current_skills(self, scope: MemoryScope) -> tuple[ProjectSkill, ...]:
        result: list[ProjectSkill] = []
        for known in self._documents.list_active_documents(scope):
            revision = self._documents.get_current_revision(scope, known.document_id)
            document = revision.document
            if document.source_kind is not KnowledgeDocumentSourceKind.MARKDOWN:
                continue
            values = dict(document.frontmatter)
            if values.get(_KIND) != "skill":
                continue
            try:
                result.append(
                    ProjectSkill(
                        revision,
                        normalize_registry_name(_required(values, _NAME)),
                        _required(values, _VERSION),
                        _parse_tags(_required(values, _TAGS)),
                        normalize_skill_clients(_parse_csv(_required(values, _CLIENTS))),
                        ProjectSkillTrust(_required(values, _TRUST)),
                    )
                )
            except (TypeError, ValueError):
                continue
        return tuple(result)

    def _iter_current_agents(self, scope: MemoryScope) -> tuple[ProjectAgent, ...]:
        result: list[ProjectAgent] = []
        for known in self._documents.list_active_documents(scope):
            revision = self._documents.get_current_revision(scope, known.document_id)
            document = revision.document
            if document.source_kind is not KnowledgeDocumentSourceKind.MARKDOWN:
                continue
            values = dict(document.frontmatter)
            if values.get(_KIND) != "agent":
                continue
            try:
                result.append(
                    ProjectAgent(
                        revision,
                        normalize_registry_name(_required(values, _NAME)),
                        _required(values, _VERSION),
                        normalize_agent_client(_required(values, _CLIENT)),
                        _parse_tags(_required(values, _SKILL_TAGS)),
                    )
                )
            except (TypeError, ValueError):
                continue
        return tuple(result)


def _unique_skills(skills: tuple[ProjectSkill, ...]) -> tuple[ProjectSkill, ...]:
    by_name: dict[str, list[ProjectSkill]] = {}
    for skill in skills:
        by_name.setdefault(skill.name, []).append(skill)
    return tuple(
        sorted(
            (items[0] for items in by_name.values() if len(items) == 1),
            key=lambda skill: (skill.name, skill.version, skill.revision.document.relative_path),
        )
    )


def _require_project_scope(scope: MemoryScope) -> MemoryScope:
    if scope.level is not ScopeLevel.PROJECT:
        raise ValueError("skill registry requires an explicit project scope")
    return scope


def _require_supported_client(client: str) -> str:
    value = normalize_agent_client(client)
    if value == "any":
        raise ValueError("skill registry requires a concrete client")
    return value


def _require_limit(limit: int) -> int:
    if not isinstance(limit, int) or not 1 <= limit <= 32:
        raise ValueError("skill registry limit must be between 1 and 32")
    return limit


def _required(values: dict[str, str], key: str) -> str:
    value = values.get(key)
    if value is None:
        raise ValueError(f"{key} is required")
    return value


def _parse_csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(","))


def _parse_tags(value: str) -> tuple[str, ...]:
    return normalize_procedure_tags(_parse_csv(value))
