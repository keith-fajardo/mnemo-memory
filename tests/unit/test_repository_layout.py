from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[2]

EXPECTED_DIRECTORIES = {
    "apps/api",
    "apps/cli",
    "apps/mcp",
    "apps/web",
    "apps/worker",
    "connectors/claude_code",
    "connectors/codex",
    "connectors/dbt",
    "connectors/filesystem",
    "connectors/git",
    "connectors/obsidian",
    "deploy/local",
    "deploy/team",
    "docs",
    "docs/adr",
    "migrations",
    "packages/context_engine",
    "packages/domain",
    "packages/episodic",
    "packages/knowledge",
    "packages/model_gateway",
    "packages/policy",
    "packages/project_index",
    "packages/skills_registry",
    "packages/storage",
    "packages/telemetry",
    "schemas",
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
