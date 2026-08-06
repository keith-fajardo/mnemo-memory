from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[2]
WORKFLOW = REPOSITORY_ROOT / ".github/workflows/publish-pypi.yml"


def test_pypi_workflow_builds_once_then_transfers_an_exact_release_bundle() -> None:
    value = WORKFLOW.read_text()

    assert "workflow_dispatch:" in value
    assert "if: github.ref == 'refs/heads/main'" in value
    assert "uv build --no-sources --out-dir build-output" in value
    assert 'cp "build-output/$wheel" "build-output/$sdist" release/' in value
    assert "path: release/" in value
    assert "name: mnemo-unified-context-0.1.0a5" in value
    assert "sha256sum --check SHA256SUMS" in value
    assert "source-run-id:" not in value
    assert "scripts/verify_installed_dbt_wrapper.py" in value
    assert '--work-directory "$work/dbt wrapper smoke"' in value
    assert "tests/fixtures/dbt/manifest-v12.json" in value


def test_pypi_workflow_isolates_oidc_publication_and_uses_explicit_artifacts() -> None:
    value = WORKFLOW.read_text()

    assert value.count("id-token: write") == 1
    assert value.count("environment: pypi") == 1
    assert "pypa/gh-action-pypi-publish@cef221092ed1bacb1cc03d23a2d87d1d172e277b" in value
    assert "packages-dir: publish-release/" in value
    assert "attestations: true" in value
    assert "find publish-release -type f | wc -l | tr -d ' ')\" = 2" in value
    assert "UV_PUBLISH_" not in value
    assert "--username" not in value
    assert "--password" not in value
    assert "--token" not in value
    assert "uv publish dist/*" not in value
    assert "uv publish" not in value
    assert "cp release/mnemo_unified_context-0.1.0a5-py3-none-any.whl publish-release/" in value
    assert "cp release/mnemo_unified_context-0.1.0a5.tar.gz publish-release/" in value
    assert "https://test.pypi.org/legacy/" not in value


def test_pypi_post_upload_verification_is_unprivileged_and_hash_bound() -> None:
    value = WORKFLOW.read_text()

    assert "verify-pypi:" in value
    assert "--registry-name PyPI" in value
    assert "https://pypi.org/pypi/mnemo-unified-context/0.1.0a5/json" in value
    assert "downloaded-release/mnemo_unified_context-0.1.0a5-py3-none-any.whl" in value
    assert "--provenance-base-url https://pypi.org/integrity" in value
    assert "--expected-repository keith-fajardo/mnemo-memory" in value
    assert "--expected-workflow publish-pypi.yml" in value
