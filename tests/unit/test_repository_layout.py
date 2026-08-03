from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[2]

EXPECTED_DIRECTORIES = {
    "src/mnemo_memory/apps/api",
    "src/mnemo_memory/apps/cli",
    "src/mnemo_memory/apps/mcp",
    "src/mnemo_memory/apps/web",
    "src/mnemo_memory/apps/worker",
    "src/mnemo_memory/connectors/claude_code",
    "src/mnemo_memory/connectors/codex",
    "src/mnemo_memory/connectors/automatic_memory",
    "src/mnemo_memory/connectors/dbt",
    "src/mnemo_memory/connectors/filesystem",
    "src/mnemo_memory/connectors/git",
    "src/mnemo_memory/connectors/obsidian",
    "deploy/local",
    "deploy/team",
    "docs",
    "docs/adr",
    "src/mnemo_memory/resources/migrations",
    "src/mnemo_memory/packages/context_engine",
    "src/mnemo_memory/packages/domain",
    "src/mnemo_memory/packages/episodic",
    "src/mnemo_memory/packages/knowledge",
    "src/mnemo_memory/packages/model_gateway",
    "src/mnemo_memory/packages/policy",
    "src/mnemo_memory/packages/project_index",
    "src/mnemo_memory/packages/skills_registry",
    "src/mnemo_memory/packages/storage",
    "src/mnemo_memory/packages/telemetry",
    "src/mnemo_memory/resources/schemas",
    "scripts",
    "tests/contract",
    "tests/architecture",
    "tests/evals",
    "tests/fixtures",
    "tests/integration",
    "tests/security",
    "tests/unit",
}

EXPECTED_FOUNDATION_FILES = {
    ".github/workflows/ci.yml",
    "AGENTS.md",
    "docs/adr/0000-template.md",
    "docs/adr/README.md",
    "docs/dependency-register.toml",
    "docs/evaluation-baseline.md",
    "docs/implementation-plan.md",
    "docs/implementation-status.md",
    "docs/product-memory-contract.md",
    "docs/product-ownership-policy.md",
    "docs/threat-model.md",
    "package-lock.json",
    "package.json",
    "pyproject.toml",
    "uv.lock",
}


def test_planned_monorepo_directories_exist() -> None:
    missing = [path for path in EXPECTED_DIRECTORIES if not (REPOSITORY_ROOT / path).is_dir()]

    assert missing == []


def test_issue_1_foundation_files_exist() -> None:
    missing = [path for path in EXPECTED_FOUNDATION_FILES if not (REPOSITORY_ROOT / path).is_file()]

    assert missing == []


def test_revision_2_plan_is_the_durable_build_record() -> None:
    plan = (REPOSITORY_ROOT / "docs/implementation-plan.md").read_bytes()

    assert any(
        line.startswith(b"**Status:** Build-ready plan, revision 2")
        for line in plan.splitlines()[:5]
    )
