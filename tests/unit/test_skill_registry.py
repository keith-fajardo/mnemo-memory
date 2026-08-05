from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from mnemo_memory.packages.domain import (
    KnowledgeDocumentRevision,
    KnowledgeDocumentRevisionId,
    MemoryScope,
    OwnerId,
    ProjectId,
    ProjectSkillTrust,
    ScopeLevel,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.knowledge import KnowledgeDocumentParser, KnowledgeDocumentParseRequest
from mnemo_memory.packages.skills_registry import KnowledgeDocumentSkillRegistry
from mnemo_memory.packages.storage import ReferenceKnowledgeDocumentRepository

NOW = datetime(2026, 8, 5, tzinfo=UTC)
FIXTURES = Path(__file__).parents[1] / "fixtures" / "procedural"


def _scope(seed: int = 1) -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string(f"00000000-0000-4000-8000-{seed:012d}"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string(f"00000000-0000-4000-8001-{seed:012d}"),
        ProjectId.from_string(f"00000000-0000-4000-8002-{seed:012d}"),
    )


def _revision(
    scope: MemoryScope,
    path: str,
    body: str,
    *,
    predecessor: KnowledgeDocumentRevision | None = None,
) -> KnowledgeDocumentRevision:
    document = KnowledgeDocumentParser().parse(KnowledgeDocumentParseRequest(scope, path), body)
    return KnowledgeDocumentRevision(
        KnowledgeDocumentRevisionId.new(),
        document,
        1 if predecessor is None else predecessor.revision_number + 1,
        None if predecessor is None else predecessor.revision_id,
        NOW,
    )


def test_existing_mnemo_skill_and_agent_fixtures_import_without_semantic_loss() -> None:
    repository = ReferenceKnowledgeDocumentRepository()
    skill_source = (FIXTURES / "skill.md").read_text(encoding="utf-8")
    agent_source = (FIXTURES / "agent.md").read_text(encoding="utf-8")
    skill_revision = _revision(_scope(), "skills/reconciliation-review.md", skill_source)
    agent_revision = _revision(_scope(), "agents/reconciliation-agent.md", agent_source)
    repository.apply_sync(_scope(), (skill_revision, agent_revision), ())
    registry = KnowledgeDocumentSkillRegistry(repository)

    skill = registry.get_current_skill(_scope(), "reconciliation-review", "codex")
    agent = registry.get_current_agent(_scope(), "reconciliation-agent", "codex")

    assert skill is not None
    assert skill.name == "reconciliation-review"
    assert skill.version == "1.2.0"
    assert skill.applicability_tags == ("dbt", "reconciliation")
    assert skill.compatible_clients == ("claude-code", "codex")
    assert skill.trust is ProjectSkillTrust.CHECKED_IN
    assert skill.source_digest == skill_revision.document.content_digest
    assert skill.revision.document.frontmatter == (
        ("mnemo_kind", "skill"),
        ("mnemo_name", "reconciliation-review"),
        ("mnemo_version", "1.2.0"),
        ("mnemo_tags", "dbt, reconciliation"),
        ("mnemo_clients", "codex, claude-code"),
        ("mnemo_trust", "checked_in"),
    )
    assert agent is not None
    assert agent.name == "reconciliation-agent"
    assert agent.version == "2.0.1"
    assert agent.client == "any"
    assert agent.skill_tags == ("dbt", "reconciliation")
    assert agent.source_digest == agent_revision.document.content_digest
    assert (
        repository.get_current_revision(
            _scope(), skill_revision.document.document_id
        ).document.sections
        == skill_revision.document.sections
    )


def test_registry_discovers_only_compatible_applicable_checked_in_skills() -> None:
    repository = ReferenceKnowledgeDocumentRepository()
    codex = _revision(
        _scope(),
        "skills/codex.md",
        "---\nmnemo_kind: skill\nmnemo_name: codex-review\nmnemo_version: 1.0.0\n"
        "mnemo_tags: dbt, review\nmnemo_clients: codex\nmnemo_trust: checked_in\n---\n"
        "# Codex review\nReview current dbt state.",
    )
    claude = _revision(
        _scope(),
        "skills/claude.md",
        "---\nmnemo_kind: skill\nmnemo_name: claude-review\nmnemo_version: 1.0.0\n"
        "mnemo_tags: dbt\nmnemo_clients: claude-code\nmnemo_trust: checked_in\n---\n"
        "# Claude review\nReview current dbt state.",
    )
    invalid_trust = _revision(
        _scope(),
        "skills/generated.md",
        "---\nmnemo_kind: skill\nmnemo_name: generated-review\nmnemo_version: 1.0.0\n"
        "mnemo_tags: dbt\nmnemo_clients: codex\nmnemo_trust: generated\n---\n"
        "# Generated\nDo not import.",
    )
    repository.apply_sync(_scope(), (claude, invalid_trust, codex), ())
    registry = KnowledgeDocumentSkillRegistry(repository)

    assert registry.list_current_skills(_scope(), "codex") == (
        registry.get_current_skill(_scope(), "codex-review", "codex"),
    )
    assert registry.find_applicable_skills(_scope(), ("DBT",), "codex") == (
        registry.get_current_skill(_scope(), "codex-review", "codex"),
    )
    assert registry.find_applicable_skills(_scope(), ("python",), "codex") == ()
    assert registry.get_current_skill(_scope(), "claude-review", "codex") is None


def test_registry_reads_current_revision_and_repository_retains_predecessor() -> None:
    repository = ReferenceKnowledgeDocumentRepository()
    first = _revision(
        _scope(),
        "skills/review.md",
        "---\nmnemo_kind: skill\nmnemo_name: review\nmnemo_version: 1.0.0\n"
        "mnemo_tags: review\nmnemo_clients: codex\nmnemo_trust: checked_in\n---\n"
        "# Review\nOld workflow.",
    )
    repository.apply_sync(_scope(), (first,), ())
    registry = KnowledgeDocumentSkillRegistry(repository)
    initial = registry.get_current_skill(_scope(), "review", "codex")
    assert initial is not None and initial.version == "1.0.0"
    second = _revision(
        _scope(),
        "skills/review.md",
        "---\nmnemo_kind: skill\nmnemo_name: review\nmnemo_version: 1.1.0\n"
        "mnemo_tags: review\nmnemo_clients: codex\nmnemo_trust: checked_in\n---\n"
        "# Review\nCurrent workflow.",
        predecessor=first,
    )
    repository.apply_sync(_scope(), (second,), ())

    current = registry.get_current_skill(_scope(), "review", "codex")
    assert current is not None and current.version == "1.1.0"
    assert current.revision.revision_id == second.revision_id
    assert repository.get_revision(_scope(), first.document.document_id, first.revision_id) == first


def test_registry_fails_closed_for_duplicate_names_scope_and_malformed_contracts() -> None:
    repository = ReferenceKnowledgeDocumentRepository()
    duplicate_one = _revision(
        _scope(),
        "skills/one.md",
        "---\nmnemo_kind: skill\nmnemo_name: duplicate\nmnemo_version: 1.0.0\n"
        "mnemo_tags: review\nmnemo_clients: codex\nmnemo_trust: checked_in\n---\n# One\nOne.",
    )
    duplicate_two = _revision(
        _scope(),
        "skills/two.md",
        "---\nmnemo_kind: skill\nmnemo_name: duplicate\nmnemo_version: 2.0.0\n"
        "mnemo_tags: review\nmnemo_clients: codex\nmnemo_trust: checked_in\n---\n# Two\nTwo.",
    )
    other_scope = _revision(
        _scope(2),
        "skills/private.md",
        "---\nmnemo_kind: skill\nmnemo_name: private\nmnemo_version: 1.0.0\n"
        "mnemo_tags: review\nmnemo_clients: codex\nmnemo_trust: checked_in\n---\n"
        "# Private\nPrivate.",
    )
    repository.apply_sync(_scope(), (duplicate_one, duplicate_two), ())
    repository.apply_sync(_scope(2), (other_scope,), ())
    registry = KnowledgeDocumentSkillRegistry(repository)

    assert registry.list_current_skills(_scope(), "codex") == ()
    assert registry.get_current_skill(_scope(), "duplicate", "codex") is None
    assert registry.get_current_skill(_scope(), "private", "codex") is None
    with pytest.raises(ValueError, match="project scope"):
        registry.list_current_skills(
            MemoryScope(
                _scope().owner_id,
                ScopeLevel.WORKSPACE,
                Visibility.WORKSPACE,
                _scope().workspace_id,
            ),
            "codex",
        )
    with pytest.raises(ValueError, match="concrete client"):
        registry.list_current_skills(_scope(), "any")
    with pytest.raises(ValueError, match="registry name"):
        registry.get_current_skill(_scope(), "../private", "codex")


def test_agent_resolution_prefers_exact_client_and_fails_closed_on_ambiguity() -> None:
    repository = ReferenceKnowledgeDocumentRepository()
    fallback = _revision(
        _scope(),
        "agents/default.md",
        "---\nmnemo_kind: agent\nmnemo_name: reviewer\nmnemo_version: 1.0.0\n"
        "mnemo_client: any\nmnemo_skill_tags: review\n---\n# Reviewer\nDefault.",
    )
    exact = _revision(
        _scope(),
        "agents/codex.md",
        "---\nmnemo_kind: agent\nmnemo_name: reviewer\nmnemo_version: 1.1.0\n"
        "mnemo_client: codex\nmnemo_skill_tags: dbt, review\n---\n# Reviewer\nCodex.",
    )
    repository.apply_sync(_scope(), (fallback, exact), ())
    registry = KnowledgeDocumentSkillRegistry(repository)

    assert registry.get_current_agent(_scope(), "reviewer", "codex") == registry.get_current_agent(
        _scope(), "reviewer", "codex"
    )
    codex = registry.get_current_agent(_scope(), "reviewer", "codex")
    claude = registry.get_current_agent(_scope(), "reviewer", "claude-code")
    assert codex is not None and codex.version == "1.1.0"
    assert claude is not None and claude.version == "1.0.0"
    duplicate = _revision(
        _scope(),
        "agents/codex-two.md",
        "---\nmnemo_kind: agent\nmnemo_name: reviewer\nmnemo_version: 1.2.0\n"
        "mnemo_client: codex\nmnemo_skill_tags: review\n---\n# Duplicate\nAmbiguous.",
    )
    repository.apply_sync(_scope(), (duplicate,), ())
    assert registry.get_current_agent(_scope(), "reviewer", "codex") is None
