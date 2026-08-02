# Python packaging

Mnemo Memory is built as a Python 3.12 package with the `uv_build` backend. The import namespace
is `mnemo_memory`; the installed command is `mnemo-memory`. The latter intentionally avoids the
existing, unrelated `mnemo` executable.

Until the maintainer approves a unique permanent PyPI distribution name, local artifacts use the
non-publishable placeholder `mnemo-agent-context-placeholder`. No package is uploaded under that
placeholder.

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

Publishing is intentionally a separate gate. TestPyPI requires a user-approved permanent name
and valid Trusted Publishing configuration (or an explicitly configured secure alternative).
Production PyPI requires a separate explicit user approval after TestPyPI verification.
