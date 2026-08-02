# Mnemo Memory

Mnemo Memory is a local-first context service for coding agents. It stores explicit,
scope-bound checkpoints and verified dbt manifest lineage in a local SQLite database, then
serves a bounded, provenance-bearing context packet over MCP. It does not proxy model traffic,
capture transcripts automatically, execute dbt, contact a warehouse, or call a model.

## Installation

The permanent PyPI distribution name is pending maintainer approval. Once published, install
the approved distribution with:

```bash
uv tool install <approved-distribution-name>
mnemo-memory --help
```

The installed command is deliberately `mnemo-memory`. It is separate from, and never replaces,
an existing `mnemo` executable.

For a locally built wheel:

```bash
uv tool install dist/<distribution>-0.1.0a1-py3-none-any.whl
```

For development:

```bash
uv sync --locked
uv run mnemo-memory --help
```

Upgrade and uninstall after publication use the approved distribution name:

```bash
uv tool upgrade <approved-distribution-name>
uv tool uninstall <approved-distribution-name>
```

## Quickstart

```bash
mnemo-memory init
mnemo-memory dbt ingest target/manifest.json \
  --owner-id 11111111-1111-4111-8111-111111111111 \
  --workspace-id 22222222-2222-4222-8222-222222222222 \
  --project-id 33333333-3333-4333-8333-333333333333
mnemo-memory dbt status \
  --owner-id 11111111-1111-4111-8111-111111111111 \
  --workspace-id 22222222-2222-4222-8222-222222222222 \
  --project-id 33333333-3333-4333-8333-333333333333
mnemo-memory connect codex
mnemo-memory connect claude-code
```

Use `--data-dir` for an explicit isolated local store. Otherwise Mnemo applies its documented
data-directory precedence. The Codex and Claude Code connections register the absolute installed
`mnemo-memory` launcher; they do not alter model, provider, authentication, permissions, or
network settings.

See [the implementation status](docs/implementation-status.md),
[local MCP guide](docs/local-mcp.md), and [dbt manifest guide](docs/dbt-manifest-intelligence.md)
for lifecycle, provenance, staleness, token-budget, and recovery details.

## Development verification

```bash
npm ci
npm run check
uv build --no-sources
```

The package requires Python 3.12.11 in the currently verified local environment. Packaging
verification is performed from a temporary working directory, so runtime resources are loaded
from the installed `mnemo_memory` package rather than from this source checkout.
