# Python packaging

Mnemo Memory is built as a Python 3.12 package with the `uv_build` backend. The import namespace
is `mnemo_memory`; the installed command is `mnemo-memory`. The latter intentionally avoids the
existing, unrelated `mnemo` executable.

The permanent distribution name is `mnemo-unified-context`. Its import namespace remains
`mnemo_memory` and its installed executable remains `mnemo-memory`.

Build source-independent artifacts with:

```bash
uv build --no-sources
```

Runtime migrations and schemas live inside `mnemo_memory.resources` and are loaded through
`importlib.resources`; an installed wheel does not depend on a repository root or its working
directory. The normal data-directory precedence and SQLite migration behavior are unchanged.

For development use `uv sync --locked`. For an isolated local artifact test use
`uv pip install --python <venv>/bin/python dist/<artifact>.whl` from a temporary directory, then
run `mnemo-memory --help` and initialize with an explicit temporary `--data-dir`.

Issue 13B verification builds both the wheel and sdist with `uv build --no-sources`, installs each
outside the checkout, and exercises the installed stdio MCP server with the official MCP Python
client. It also uses `uv tool install` with an isolated tool directory and temporary Codex/Claude
homes. These checks use only synthetic fixtures and isolated Mnemo data; they neither inspect nor
modify real client configuration or `/opt/homebrew/bin/mnemo`.

Publishing is intentionally a separate gate. TestPyPI requires a user-approved permanent name
and valid Trusted Publishing configuration (or an explicitly configured secure alternative).
Production PyPI requires a separate explicit user approval after TestPyPI verification.

The public source repository is [keith-fajardo/mnemo-memory](https://github.com/keith-fajardo/mnemo-memory).
Its repository name is not a PyPI-name decision: the placeholder distribution remains local only.

## TestPyPI Trusted Publishing setup

Before manually invoking `.github/workflows/publish-testpypi.yml`, create the GitHub environment
`testpypi` and configure a TestPyPI Trusted Publisher with these exact values:

- Project: `mnemo-unified-context`
- Owner: `keith-fajardo`
- Repository: `mnemo-memory`
- Workflow: `publish-testpypi.yml`
- Environment: `testpypi`

The workflow is manual-only and needs no API token. It builds and tests one artifact set, requires
OIDC explicitly, then uploads only those exact files to `https://test.pypi.org/legacy/`. A partial
or duplicate upload must be investigated on TestPyPI; do not rerun it with the same version until
the TestPyPI project files and hashes have been checked. Production PyPI is not part of this
workflow.
