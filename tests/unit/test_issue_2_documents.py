from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[2]


def assert_sections(path: str, sections: set[str]) -> None:
    content = (REPOSITORY_ROOT / path).read_text().casefold()
    missing = sorted(section for section in sections if section.casefold() not in content)

    assert missing == []


def test_repository_instructions_cover_required_rules() -> None:
    assert_sections(
        "AGENTS.md",
        {
            "Product and architecture boundaries",
            "Clean-room originality",
            "Third-party dependencies",
            "Security and privacy requirements",
            "Verification",
            "Scope discipline",
        },
    )


def test_ownership_policy_covers_provenance_and_approval() -> None:
    assert_sections(
        "docs/product-ownership-policy.md",
        {
            "Originality standard",
            "Contributor provenance",
            "Dependency approval process",
            "Dependency register ownership",
            "Review and enforcement",
        },
    )


def test_adr_template_covers_required_decision_dimensions() -> None:
    assert_sections(
        "docs/adr/0000-template.md",
        {
            "Context",
            "Decision",
            "Alternatives considered",
            "Consequences",
            "Security and privacy implications",
            "Token and cost implications",
            "Dependency and licensing implications",
            "Reversal or migration strategy",
        },
    )


def test_memory_contract_covers_user_control_and_memory_semantics() -> None:
    assert_sections(
        "docs/product-memory-contract.md",
        {
            "Memory categories",
            "Source-authority order",
            "Scope model",
            "Evidence requirements",
            "Prohibited data",
            "Consent and capture",
            "Retention defaults",
            "Correction and conflict handling",
            "Export and deletion",
            "Structural projections versus durable memories",
        },
    )


def test_threat_model_covers_initial_required_threats() -> None:
    assert_sections(
        "docs/threat-model.md",
        {
            "Cross-project disclosure",
            "Prompt injection through retrieved content",
            "Poisoned memories",
            "Secret ingestion",
            "Stale structural information",
            "Unauthorized memory mutation",
            "Deletion propagation",
            "Local service exposure",
            "Compromised connectors",
        },
    )


def test_evaluation_spec_defines_three_baselines_and_measurements() -> None:
    assert_sections(
        "docs/evaluation-baseline.md",
        {
            "No-memory baseline",
            "Full-transcript baseline",
            "Mnemo-context baseline",
            "Tokens and cost",
            "Latency and reliability",
            "Quality and task resumption",
            "Provenance and safety",
            "Golden workflow format",
        },
    )
