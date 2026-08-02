from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[2]
WORKFLOW = REPOSITORY_ROOT / ".github/workflows/publish-pypi.yml"


def test_pypi_workflow_transfers_the_exact_testpypi_verified_bundle() -> None:
    value = WORKFLOW.read_text()

    assert "workflow_dispatch:" in value
    assert "if: github.ref == 'refs/heads/main'" in value
    assert "source-run-id:" in value
    assert 'default: "30761127604"' in value
    assert "repository: keith-fajardo/mnemo-memory" in value
    assert "run-id: ${{ inputs.source-run-id }}" in value
    assert "actions: read" in value
    assert "sha256sum --check SHA256SUMS" in value
    assert "uv build" not in value


def test_pypi_workflow_isolated_oidc_publication_uses_explicit_artifacts() -> None:
    value = WORKFLOW.read_text()

    assert value.count("id-token: write") == 1
    assert value.count("environment: pypi") == 1
    assert "--trusted-publishing always" in value
    assert "--publish-url https://upload.pypi.org/legacy/" in value
    assert "UV_PUBLISH_" not in value
    assert "--username" not in value
    assert "--password" not in value
    assert "--token" not in value
    assert "uv publish dist/*" not in value
    assert "release/mnemo_unified_context-0.1.0a1-py3-none-any.whl" in value
    assert "release/mnemo_unified_context-0.1.0a1.tar.gz" in value
