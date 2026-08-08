from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[2]
WORKFLOW = REPOSITORY_ROOT / ".github/workflows/publish-testpypi.yml"
RELEASE_FILES = {
    "mnemo_unified_context-0.1.0a12-py3-none-any.whl",
    "mnemo_unified_context-0.1.0a12.tar.gz",
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
    assert "scripts/verify_release_artifacts.py" in value
    assert '--wheel "build-output/$wheel"' in value
    assert '--sdist "build-output/$sdist"' in value
    for filename in RELEASE_FILES:
        assert f"release/{filename}" in value
    assert "scripts/verify_installed_dbt_wrapper.py" in value
    assert '--work-directory "$work/dbt wrapper smoke"' in value
    assert "tests/fixtures/dbt/manifest-v12.json" in value


def test_testpypi_workflow_uses_verified_explicit_artifacts_and_pypi_dependencies() -> None:
    value = workflow()

    assert "uv publish dist/*" not in value
    assert "uv publish release/*.whl" not in value
    assert "uv publish release/*.tar.gz" not in value
    assert "uv publish" not in value
    assert "pypa/gh-action-pypi-publish@cef221092ed1bacb1cc03d23a2d87d1d172e277b" in value
    assert "packages-dir: publish-release/" in value
    assert "attestations: true" in value
    assert "cp release/mnemo_unified_context-0.1.0a12-py3-none-any.whl publish-release/" in value
    assert "cp release/mnemo_unified_context-0.1.0a12.tar.gz publish-release/" in value
    assert "uv publish build-output" not in value
    assert "sha256sum --check SHA256SUMS" in value
    assert "test.pypi.org/simple" not in value
    assert "--index-url https://pypi.org/simple/" in value
    assert "downloaded-release/mnemo_unified_context-0.1.0a12-py3-none-any.whl" in value
    assert 'python -m pip install "uv==' not in value
    assert "astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b" in value
    assert "\n          ! rg " not in value
    assert "\n          rg " not in value


def test_testpypi_workflow_isolates_oidc_to_the_publish_job() -> None:
    value = workflow()

    assert "workflow_dispatch:" in value
    assert "if: github.ref == 'refs/heads/main'" in value
    assert value.count("id-token: write") == 1
    assert value.count("environment: testpypi") == 1
    assert "repository-url: https://test.pypi.org/legacy/" in value
    assert "UV_PUBLISH_" not in value
    assert "--username" not in value
    assert "--password" not in value
    assert "--token" not in value
    assert "PYPI_TOKEN" not in value
    assert "TWINE_USERNAME" not in value
    assert "TWINE_PASSWORD" not in value
    assert "https://upload.pypi.org/legacy/" not in value
    assert "ACTIONS_ID_TOKEN_REQUEST_URL" in value
    assert "ACTIONS_ID_TOKEN_REQUEST_TOKEN" in value
    assert "--forbidden-text mnemo-agent-context-placeholder" in value
    assert "--text-path pyproject.toml" in value
    assert "--text-path README.md" in value
    assert "--text-path docs" in value
    assert "--provenance-base-url https://test.pypi.org/integrity" in value
    assert "--expected-repository keith-fajardo/mnemo-memory" in value
    assert "--expected-workflow publish-testpypi.yml" in value


def test_testpypi_workflow_uses_bounded_standard_library_post_upload_verification() -> None:
    value = workflow()

    assert "verify-testpypi:" in value
    assert "uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683" in value
    assert "scripts/verify_testpypi_release.py" in value
    assert "--request-timeout-seconds 10" in value
    assert "--deadline-seconds 120" in value
    assert "--retry-interval-seconds 3" in value
    assert "curl --fail --retry" not in value
