from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[2]
WORKFLOW = REPOSITORY_ROOT / ".github/workflows/publish-testpypi.yml"
RELEASE_FILES = {
    "mnemo_unified_context-0.1.0a1-py3-none-any.whl",
    "mnemo_unified_context-0.1.0a1.tar.gz",
    "SHA256SUMS",
}


def workflow() -> str:
    return WORKFLOW.read_text()


def test_testpypi_workflow_transfers_only_a_flat_three_file_release_bundle() -> None:
    value = workflow()

    assert "uv build --no-sources --out-dir build-output" in value
    assert 'cp "build-output/$wheel" "build-output/$sdist" release/' in value
    assert "path: release/" in value
    assert "find release -type f | wc -l | tr -d ' ')\" = 3" in value
    assert "find release -mindepth 1 -maxdepth 1 -type f | wc -l | tr -d ' ')\" = 3" in value
    assert "find release -mindepth 1 -type d | wc -l | tr -d ' ')\" = 0" in value
    assert "release/.gitignore" in value
    assert "Final staged release files:" in value
    for resource in (
        "mnemo_memory/py\\.typed$",
        "mnemo_memory/resources/migrations/0003_dbt_manifest_snapshots\\.sql$",
        "mnemo_memory/resources/schemas/context-packet-v1\\.json$",
    ):
        assert f"require_wheel_member \"build-output/$wheel\" '{resource}'" in value
    for resource in (
        "src/mnemo_memory/resources/migrations/0003_dbt_manifest_snapshots\\.sql$",
        "src/mnemo_memory/resources/schemas/context-packet-v1\\.json$",
    ):
        assert f"require_sdist_member \"build-output/$sdist\" '{resource}'" in value
    for filename in RELEASE_FILES:
        assert f"release/{filename}" in value


def test_testpypi_workflow_uses_verified_explicit_artifacts_and_pypi_dependencies() -> None:
    value = workflow()

    assert "uv publish dist/*" not in value
    assert "uv publish release/*.whl" not in value
    assert "uv publish release/*.tar.gz" not in value
    assert "uv publish release/mnemo_unified_context-0.1.0a1-py3-none-any.whl" in value
    assert "release/mnemo_unified_context-0.1.0a1.tar.gz" in value
    assert "uv publish build-output" not in value
    assert "sha256sum --check SHA256SUMS" in value
    assert "test.pypi.org/simple" not in value
    assert "--index-url https://pypi.org/simple/" in value
    assert 'python -m pip install "uv==' not in value
    assert "astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b" in value


def test_testpypi_workflow_isolates_oidc_to_the_publish_job() -> None:
    value = workflow()

    assert "workflow_dispatch:" in value
    assert "if: github.ref == 'refs/heads/main'" in value
    assert value.count("id-token: write") == 1
    assert value.count("environment: testpypi") == 1
    assert "UV_PUBLISH_URL: https://test.pypi.org/legacy/" in value
    assert "UV_PUBLISH_TRUSTED_PUBLISHING: always" in value
