# Python packaging

Mnemo Memory is built as a Python 3.12 package with the `uv_build` backend. The import namespace
is `mnemo_memory`; the primary installed command is `mnemo`, with `mnemo-memory` retained as a
compatibility alias.

The permanent distribution name is `mnemo-unified-context`. Its import namespace remains
`mnemo_memory` and it installs both command aliases through the same CLI entry point.

Build source-independent artifacts with:

```bash
uv build --no-sources
```

Runtime migrations and schemas live inside `mnemo_memory.resources` and are loaded through
`importlib.resources`; an installed wheel does not depend on a repository root or its working
directory. The normal data-directory precedence and SQLite migration behavior are unchanged.

For development use `uv sync --locked`. For an isolated local artifact test use
`uv pip install --python <venv>/bin/python dist/<artifact>.whl` from a temporary directory, then
run `mnemo --help` and initialize with an explicit temporary `--data-dir`.

Issue 13B verification builds both the wheel and sdist with `uv build --no-sources`, installs each
outside the checkout, and exercises the installed stdio MCP server with the official MCP Python
client. It also uses `uv tool install` with an isolated tool directory and temporary Codex/Claude
homes. These checks use only synthetic fixtures and isolated Mnemo data; they neither inspect nor
modify real client configuration.

Publishing is intentionally a separate gate. TestPyPI requires a user-approved permanent name
and valid Trusted Publishing configuration (or an explicitly configured secure alternative).
Production PyPI requires a separate explicit user approval. The manual
`.github/workflows/publish-pypi.yml` workflow builds, tests, and checksums one release bundle in
an unprivileged job; its environment-approved job publishes those exact files through PyPI OIDC
without rebuilding them.

The public source repository is [keith-fajardo/mnemo-memory](https://github.com/keith-fajardo/mnemo-memory).
The approved distribution name is `mnemo-unified-context`; the import namespace remains
`mnemo_memory`, the primary installed command is `mnemo`, and `mnemo-memory` remains compatible.

## TestPyPI Trusted Publishing setup

Before manually invoking `.github/workflows/publish-testpypi.yml`, create the GitHub environment
`testpypi` and configure a TestPyPI Trusted Publisher with these exact values:

- Project: `mnemo-unified-context`
- Owner: `keith-fajardo`
- Repository: `mnemo-memory`
- Workflow: `publish-testpypi.yml`
- Environment: `testpypi`

The workflow is manual-only and needs no API token. It uses three isolated jobs: an unprivileged
build/test job transfers one flat `release/` directory containing exactly the wheel, sdist, and
`SHA256SUMS` manifest by a short-lived GitHub artifact; only the environment-approved publishing
job obtains OIDC and uploads the two explicit artifact paths. The unprivileged verification job
downloads TestPyPI metadata and the uploaded wheel, then installs that local downloaded wheel with
dependencies resolved only from production PyPI. A partial or duplicate upload must be
investigated on TestPyPI; do not rerun it with the same version until the TestPyPI project files and
hashes have been checked. Production PyPI is not part of this workflow.

## Production PyPI Trusted Publishing setup

Before manually invoking `.github/workflows/publish-pypi.yml`, configure a production PyPI
Trusted Publisher with these exact values:

- Project: `mnemo-unified-context`
- Owner: `keith-fajardo`
- Repository: `mnemo-memory`
- Workflow: `publish-pypi.yml`
- Environment: `pypi`

The production workflow is manual-only. Its unprivileged build job creates a flat release bundle
containing exactly the wheel, sdist, and checksum manifest. The environment-approved publishing
job verifies that bundle and publishes only the two explicit artifact paths through PyPI OIDC; it
does not accept tokens or rebuild release artifacts. A final unprivileged job verifies PyPI
metadata and hashes, downloads the uploaded wheel, and smoke-tests it in a fresh environment.
